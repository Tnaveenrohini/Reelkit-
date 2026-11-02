"""Lint the page: every inline script must parse (node --check, never executed) and every JSON data block must be valid."""
import json, os, re, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
bad = 0
for name in ("templates/index.html", "templates/admin.html"):
    html = open(os.path.join(ROOT, name), encoding="utf-8").read()
    scripts = re.findall(r"<script(?![^>]*(?:src=|application/))[^>]*>(.*?)</script>", html, re.S)
    node = shutil.which("node")
    with tempfile.TemporaryDirectory() as d:
        for i, code in enumerate(scripts):
            if not node:
                break
            p = os.path.join(d, f"s{i}.js"); open(p, "w", encoding="utf-8").write(code)
            r = subprocess.run([node, "--check", p], capture_output=True, text=True)
            if r.returncode:
                bad += 1; print(f"{name}: inline script {i + 1} does not parse:\n{r.stderr[-800:]}")
    for m in re.finditer(r'<script type="application/(?:ld\+)?json" id="([\w-]+)">(.*?)</script>', html, re.S):
        try:
            json.loads(m.group(2).replace("<\\/", "</"))
        except ValueError as e:
            bad += 1; print(f"{name}: JSON block {m.group(1)} is invalid: {e}")
    print(f"{name}: {len(scripts)} script(s) checked")
for js in ("templates/admin.js", "templates/admin_bugs.js", "public/sw.js"):
    if shutil.which("node"):
        r = subprocess.run(["node", "--check", os.path.join(ROOT, js)], capture_output=True, text=True)
        if r.returncode:
            bad += 1; print(f"{js} does not parse:\n{r.stderr[-800:]}")
sys.exit(1 if bad else 0)
