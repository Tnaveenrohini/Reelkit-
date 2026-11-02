"""Fail if the change adds anything that looks like a credential."""
import re, subprocess, sys

base = sys.argv[1] if len(sys.argv) > 1 else "HEAD~1"
PAT = [r"sk-ant-[A-Za-z0-9_\-]{20,}", r"\bgh[pousr]_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{30,}", r"\bsntrys_[A-Za-z0-9_=\-]{30,}",
       r"AKIA[0-9A-Z]{16}", r"AIza[0-9A-Za-z_\-]{35}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"postgres(?:ql)?://[^:\s]+:[^@\s]{6,}@(?!localhost)[\w.\-]+",
       r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][A-Za-z0-9_\-/+]{20,}['\"]"]
try:
    diff = subprocess.run(["git", "diff", "--unified=0", base, "--", ".", ":(exclude)tests/tools/secret_scan.py", ":(exclude)bugfixer/redact.py",
                           ":(exclude)bugfixer/policy.py", ":(exclude)tests/unit/*"], capture_output=True, text=True, check=True).stdout
except subprocess.CalledProcessError as e:
    print("could not diff:", e.stderr[-300:]); sys.exit(1)
hits = [l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++") and any(re.search(p, l) for p in PAT)]
for h in hits:
    print("possible secret added:", h[:120] + "…")
print(f"secret scan: {len(hits)} finding(s)")
sys.exit(1 if hits else 0)
