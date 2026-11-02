"""Claude API client for analysis and repair. Claude explores a read-only copy of the code with three tools
(list, read, search) and must finish by calling the result tool. Nothing Claude writes is executed by the worker."""
import fnmatch
import os
import re

TEXT_EXT = (".py", ".html", ".js", ".css", ".json", ".md", ".txt", ".toml", ".yml", ".yaml", ".sql", ".cfg", ".ini")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv"}


class AIError(Exception):
    pass


class RepoView:
    """Read-only, size-limited view of a downloaded repository snapshot."""

    def __init__(self, root):
        self.root = os.path.realpath(root)

    def _safe(self, path):
        p = os.path.realpath(os.path.join(self.root, str(path).lstrip("/")))
        if not p.startswith(self.root + os.sep) and p != self.root:
            raise AIError("path outside the repository")
        return p

    def list_files(self, pattern="*"):
        out = []
        for d, dirs, files in os.walk(self.root):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for f in files:
                rel = os.path.relpath(os.path.join(d, f), self.root)
                if rel.endswith(TEXT_EXT) and fnmatch.fnmatch(rel, pattern or "*"):
                    out.append(f"{rel} ({os.path.getsize(os.path.join(d, f))} bytes)")
        return "\n".join(sorted(out)[:300]) or "no files"

    def read(self, path, start=1, end=None):
        p = self._safe(path)
        if not os.path.isfile(p):
            return f"{path}: not found"
        with open(p, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().split("\n")
        start = max(1, int(start or 1)); end = min(len(lines), int(end or start + 299), start + 399)
        body = "\n".join(f"{i:>6}  {lines[i - 1][:600]}" for i in range(start, end + 1))
        return f"{path} lines {start}-{end} of {len(lines)}\n{body}"

    def search(self, pattern, glob="*"):
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"bad regular expression: {e}"
        hits = []
        for d, dirs, files in os.walk(self.root):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for f in files:
                rel = os.path.relpath(os.path.join(d, f), self.root)
                if not rel.endswith(TEXT_EXT) or not fnmatch.fnmatch(rel, glob or "*"):
                    continue
                with open(os.path.join(d, f), encoding="utf-8", errors="replace") as fh:
                    for n, line in enumerate(fh, 1):
                        if rx.search(line):
                            hits.append(f"{rel}:{n}: {line.strip()[:240]}")
                            if len(hits) >= 60:
                                return "\n".join(hits) + "\n(first 60 matches)"
        return "\n".join(hits) or "no matches"


EXPLORE_TOOLS = [
    {"name": "list_files", "description": "List text files in the repository matching a glob pattern (e.g. '*.py', 'templates/*').",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": []}},
    {"name": "read_file", "description": "Read lines of a file (with line numbers). At most 400 lines per call.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
                      "required": ["path"]}},
    {"name": "search_code", "description": "Search the repository with a Python regular expression. Returns file:line: text.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}, "glob": {"type": "string"}}, "required": ["pattern"]}},
]


class Claude:
    def __init__(self, api_key, model, base_url="", max_steps=14):
        if not api_key:
            raise AIError("ANTHROPIC_API_KEY is not set")
        import anthropic
        kw = {"api_key": api_key, "max_retries": 3, "timeout": 180}
        if base_url:
            kw["base_url"] = base_url
        self.client, self.model, self.max_steps, self.anthropic = anthropic.Anthropic(**kw), model, max_steps, anthropic

    def run(self, system, prompt, repo, final_tool, max_tokens=8000, log=None):
        """Tool loop: Claude may explore, then must call `final_tool`. Returns (final_input, usage)."""
        tools = EXPLORE_TOOLS + [final_tool]
        messages = [{"role": "user", "content": prompt}]
        usage = {"in": 0, "out": 0}
        for step in range(self.max_steps):
            last = step == self.max_steps - 1
            try:
                resp = self.client.messages.create(model=self.model, max_tokens=max_tokens, system=system, messages=messages, tools=tools,
                                                   tool_choice={"type": "tool", "name": final_tool["name"]} if last else {"type": "any"})
            except self.anthropic.APIStatusError as e:
                raise AIError(f"Claude API error {e.status_code}: {str(getattr(e, 'message', e))[:300]}") from None
            except self.anthropic.APIConnectionError:
                raise AIError("Claude API unreachable") from None
            usage["in"] += getattr(resp.usage, "input_tokens", 0) or 0
            usage["out"] += getattr(resp.usage, "output_tokens", 0) or 0
            calls = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
            for b in calls:
                if b.name == final_tool["name"]:
                    return dict(b.input), usage
            if not calls:
                messages += [{"role": "assistant", "content": [c.model_dump() if hasattr(c, "model_dump") else c for c in resp.content]},
                             {"role": "user", "content": f"Use the tools. When you are done, call {final_tool['name']}."}]
                continue
            results = []
            for b in calls:
                args = dict(b.input or {})
                try:
                    if b.name == "list_files":
                        out = repo.list_files(args.get("pattern", "*"))
                    elif b.name == "read_file":
                        out = repo.read(args.get("path", ""), args.get("start_line", 1), args.get("end_line"))
                    elif b.name == "search_code":
                        out = repo.search(args.get("pattern", ""), args.get("glob", "*"))
                    else:
                        out = f"unknown tool {b.name}"
                except AIError as e:
                    out = f"error: {e}"
                if log:
                    log(f"AI {b.name}({', '.join(f'{k}={str(v)[:60]}' for k, v in args.items())})")
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": out[:30000]})
            messages += [{"role": "assistant", "content": [c.model_dump() if hasattr(c, "model_dump") else c for c in resp.content]},
                         {"role": "user", "content": results}]
        raise AIError(f"Claude did not finish within {self.max_steps} steps")


def untrusted(label, text):
    """Error reports come from the public site: they are data, wrapped so instructions inside them are not followed."""
    t = re.sub(r"(?i)untrusted_", "untrusted-", str(text or ""))      # the data can't open or close our tags
    return f"<untrusted_{label}>\n{t}\n</untrusted_{label}>"
