"""Apply the AI's edits as exact text replacements. Every 'old' text must appear exactly once in its file, so a patch
can never land somewhere unexpected. Produces the new file contents, a unified diff and line counts."""
import difflib


class PatchError(Exception):
    pass


def apply_edits(files, edits, allow_new_under=("tests/",)):
    """files: {path: text} for every file an edit touches (missing = file doesn't exist). Returns (new_files, diff, stats)."""
    new = dict(files)
    for i, e in enumerate(edits, 1):
        path, old, rep = str(e.get("path", "")).strip(), e.get("old", ""), e.get("new", "")
        if not path or path.startswith(("/", "\\")) or ".." in path.split("/") or "\\" in path:
            raise PatchError(f"edit {i}: invalid path {path!r}")
        if not isinstance(old, str) or not isinstance(rep, str):
            raise PatchError(f"edit {i}: old/new must be text")
        if path not in new or new[path] is None:
            if old != "":
                raise PatchError(f"edit {i}: {path} does not exist")
            if not path.startswith(tuple(allow_new_under)):
                raise PatchError(f"edit {i}: new files may only be created under {', '.join(allow_new_under)}")
            new[path] = rep
            continue
        if old == "":
            raise PatchError(f"edit {i}: 'old' is empty for an existing file {path}")
        n = new[path].count(old)
        if n == 0:
            raise PatchError(f"edit {i}: the text to replace was not found in {path}")
        if n > 1:
            raise PatchError(f"edit {i}: the text to replace appears {n} times in {path}; include more surrounding lines")
        if old == rep:
            raise PatchError(f"edit {i}: 'old' and 'new' are the same")
        new[path] = new[path].replace(old, rep, 1)
    diff_parts, added, removed, changed = [], 0, 0, []
    for path in sorted(new):
        before, after = files.get(path) or "", new[path]
        if before == after:
            continue
        changed.append(path)
        d = list(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                      fromfile=f"a/{path}" if path in files and files[path] is not None else "/dev/null", tofile=f"b/{path}", n=3))
        for line in d:
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        diff_parts.append("".join(d if d and d[-1].endswith("\n") else d + ["\n"]))
    if not changed:
        raise PatchError("the patch changes nothing")
    return {p: new[p] for p in changed}, "".join(diff_parts), {"files": changed, "added": added, "removed": removed, "lines": added + removed}
