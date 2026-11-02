"""Safety policy for AI patches. Decides whether a patch may be committed at all, whether it needs the owner's
approval before production, and how risky it is. The AI's own risk label can only make things stricter."""
import re

# never changed by the bug fixer (self-modification, CI, secrets, deployment config)
FORBIDDEN = [r"^bugfixer/", r"^\.github/", r"(^|/)\.env", r"^vercel\.json$", r"^Dockerfile", r"^requirements-worker\.txt$",
             r"\.(pem|key|p12|pfx)$", r"(^|/)\.git/", r"^wr_bugs\.py$"]
# allowed, but production needs the owner's approval
SENSITIVE = [(r"^wr_admin\.py$", "admin sign-in and security code"), (r"^templates/admin\.", "admin dashboard"),
             (r"^requirements\.txt$", "dependencies"), (r"^public/sw\.js$", "service worker (affects every visit)"),
             (r"^public/manifest\.json$", "installed-app manifest"), (r"^wr_store\.py$", "database access"),
             (r"\.sql$", "database schema")]
TEXT_TYPES = (".py", ".html", ".js", ".css", ".json", ".md", ".txt", ".toml", ".cfg", ".ini", ".yml", ".yaml")
DANGEROUS = [
    (r"\beval\s*\(", "uses eval()"), (r"\bexec\s*\(", "uses exec()"), (r"\bos\.system\s*\(", "runs shell commands"),
    (r"shell\s*=\s*True", "runs a shell"), (r"\bpickle\.loads?\s*\(", "unpickles data"), (r"\b__import__\s*\(", "dynamic import"),
    (r"new\s+Function\s*\(", "builds code from text"), (r"document\.write\s*\(", "document.write"),
    (r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{12,}['\"]", "hard-coded credential"),
    (r"sk-ant-|ghp_|github_pat_|sntrys_|AKIA[0-9A-Z]{16}", "credential-like text"),
    (r"(?i)\bdrop\s+table\b|\btruncate\s+table\b|\bdelete\s+from\b", "destructive SQL"),
    (r"\bshutil\.rmtree\s*\(|\bos\.remove\s*\(.*\*", "deletes files"),
]
URL = re.compile(r"https?://([A-Za-z0-9.\-]+)")


def _added_lines(diff):
    return [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]


def _removed_lines(diff):
    return [l[1:] for l in diff.splitlines() if l.startswith("-") and not l.startswith("---")]


def assess(stats, diff, before, after, cfg, ai_risk="medium", ai_flags=None):
    """Returns {"allowed", "needs_approval", "destructive", "risk", "reasons", "blocked"}."""
    reasons, blocked, approval = [], [], []
    files = stats["files"]
    for p in files:
        if any(re.search(rx, p) for rx in FORBIDDEN):
            blocked.append(f"{p} may not be changed automatically")
        if not p.endswith(TEXT_TYPES):
            blocked.append(f"{p} is not a text source file")
        for rx, why in SENSITIVE:
            if re.search(rx, p):
                approval.append(f"touches {why} ({p})")
    if len(files) > cfg.max_patch_files:
        blocked.append(f"changes {len(files)} files (limit {cfg.max_patch_files})")
    if stats["lines"] > cfg.max_patch_lines:
        blocked.append(f"changes {stats['lines']} lines (limit {cfg.max_patch_lines}); not a minimal fix")
    added = "\n".join(_added_lines(diff))
    for rx, why in DANGEROUS:
        if re.search(rx, added):
            blocked.append(f"adds code that {why}")
    old_hosts = {h for t in before.values() if t for h in URL.findall(t)}
    new_hosts = {h for t in after.values() for h in URL.findall(t)} - old_hosts
    if new_hosts:
        approval.append("adds new web addresses: " + ", ".join(sorted(new_hosts))[:200])
    removed = len(_removed_lines(diff))
    destructive = False
    for p in files:
        if before.get(p) and not after.get(p, "").strip():
            destructive = True; approval.append(f"empties {p}")
    if removed >= 40 or (removed > 3 * max(1, stats["added"]) and removed >= 15):
        destructive = True; approval.append(f"removes {removed} lines")
    flags = ai_flags or {}
    if flags.get("adds_dependencies"):
        approval.append("adds a dependency")
    if flags.get("touches_security"):
        approval.append("the AI marked it as security-related")
    if flags.get("destructive"):
        destructive = True; approval.append("the AI marked it as destructive")
    # risk: the stricter of the AI's label and ours
    risk = ai_risk if ai_risk in ("low", "medium", "high") else "medium"
    if approval or destructive:
        risk = "high"
    elif stats["lines"] > 40 or len(files) > 2:
        risk = "medium" if risk == "low" else risk
    reasons = blocked + approval
    return {"allowed": not blocked, "needs_approval": bool(approval) or destructive or risk == "high", "destructive": destructive,
            "risk": risk, "reasons": reasons, "blocked": blocked}


def deploy_needs_approval(setting, policy_result, priority):
    """Owner approval for production: always for high risk / destructive; otherwise by the 'auto_deploy' setting."""
    if policy_result.get("needs_approval") or policy_result.get("destructive") or policy_result.get("risk") == "high":
        return True, "high-risk or destructive change"
    if setting == "auto":
        return False, "automatic deploys are on"
    if setting == "low_risk":
        return (policy_result.get("risk") != "low"), ("low-risk change" if policy_result.get("risk") == "low" else "only low-risk changes deploy automatically")
    return True, "every production deploy needs the owner's approval (setting)"
