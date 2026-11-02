"""Turn an error into a stable fingerprint, so the same bug from many visitors (or from Sentry and our own capture)
becomes one bug report. Numbers, ids, links and quoted values are replaced; line numbers are ignored."""
import hashlib
import re

_NORM = [
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<hex>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<hex>"),
    (re.compile(r"'[^']{0,80}'"), "'<s>'"),
    (re.compile(r'"[^"]{0,80}"'), '"<s>"'),
    (re.compile(r"/tmp/[\w\-./]+"), "<tmp>"),
    (re.compile(r"\d+(\.\d+)?"), "<n>"),
    (re.compile(r"\s+"), " "),
]
IN_APP_PY = re.compile(r"(^|/)(app\.py|wr_[a-z_]+\.py|bugfixer/[\w/]+\.py)$")
JS_FRAME = [re.compile(r"at (?:(?P<fn>[^\s(]+) )?\(?(?P<file>[^\s()]+?):(?P<line>\d+):(?P<col>\d+)\)?$"),   # Chrome
            re.compile(r"^(?P<fn>[^@]*)@(?P<file>.+?):(?P<line>\d+):(?P<col>\d+)$")]                     # Firefox/Safari


def normalize_message(msg):
    s = str(msg or "")[:500]
    for rx, rep in _NORM:
        s = rx.sub(rep, s)
    return s.strip()[:240]


def parse_js_stack(stack, page_origin=""):
    frames = []
    for line in str(stack or "").splitlines()[:40]:
        line = line.strip()
        for rx in JS_FRAME:
            m = rx.search(line)
            if m:
                f = m.group("file")
                frames.append({"file": f.split("?")[0][:200], "function": (m.group("fn") or "?")[:80], "line": int(m.group("line")),
                               "col": int(m.group("col")), "in_app": (not page_origin or f.startswith(page_origin) or f.startswith("/")) and "extension://" not in f})
                break
    return frames


def parse_py_traceback(tb):
    frames = []
    for m in re.finditer(r'File "([^"]+)", line (\d+), in ([^\n]+)', str(tb or "")):
        path = m.group(1)
        short = re.sub(r"^.*?/((?:bugfixer/)?[\w.]+\.py)$", r"\1", path)
        frames.append({"file": short[:200], "function": m.group(3).strip()[:80], "line": int(m.group(2)),
                       "in_app": bool(IN_APP_PY.search(short)) and "site-packages" not in path})
    return frames


def top_in_app(frames):
    for f in reversed(frames or []):          # innermost frame last in Python tracebacks
        if f.get("in_app"):
            return f
    return None


def top_in_app_js(frames):
    for f in frames or []:                    # innermost frame first in JS stacks
        if f.get("in_app"):
            return f
    return None


def compute(kind, error_type, message, frames=None, route="", js=False):
    top = (top_in_app_js(frames) if js else top_in_app(frames)) or {}
    where = f"{top.get('file', '')}:{top.get('function', '')}" if top else (route or "")
    if kind in ("api", "download", "upload", "probe") and route:
        where = f"{route}|{where}"
    basis = "|".join([kind or "", (error_type or "")[:80], normalize_message(message), where])
    return hashlib.sha1(basis.encode(), usedforsecurity=False).hexdigest()   # a grouping key, not security
