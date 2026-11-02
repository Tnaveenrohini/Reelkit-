"""Module 6 – Bug priority. Critical, High, Medium or Low from what failed, where, how often and for how many visitors.
Critical bugs are processed first (rank 0), then High, Medium, Low; inside a level the higher score goes first."""

LEVELS = ["critical", "high", "medium", "low"]
RANK = {p: i for i, p in enumerate(LEVELS)}
CORE_ROUTES = ("/api/info", "/api/download", "/api/convert", "/api/mp3", "/", "/health")


def _bump(p, n=1):
    return LEVELS[max(0, RANK[p] - n)]


def _drop(p, n=1):
    return LEVELS[min(len(LEVELS) - 1, RANK[p] + n)]


def assess(kind, status=None, route="", count_1h=0, count_24h=0, sessions_24h=0, probe_failures=0, error_type="",
           message="", hours_since_last=0.0):
    """Returns (priority, score 0–100, reasons)."""
    reasons = []
    route = route or ""
    core = any(route == r or (r != "/" and route.startswith(r)) for r in CORE_ROUTES)
    if kind == "probe":
        p = "critical" if probe_failures >= 2 else "high"
        reasons.append(f"site check failed {probe_failures} time(s) in a row")
    elif kind == "database":
        p = "high"; reasons.append("database error")
    elif kind in ("backend", "api"):
        if status and int(status) >= 500 and core:
            p = "high"; reasons.append(f"server error {status} on a core route ({route})")
        elif status and int(status) >= 500:
            p = "medium"; reasons.append(f"server error {status} on {route or 'an API route'}")
        else:
            p = "high" if kind == "backend" and core else "medium"; reasons.append("unhandled backend exception" if kind == "backend" else "API error")
    elif kind in ("download", "upload"):
        if (status or 0) >= 500:
            p = "high"; reasons.append(f"{kind} failed with server error {status}")
        elif status == 0:
            p = "low"; reasons.append(f"{kind} failed (network: often the visitor's connection)")
        else:
            p = "medium"; reasons.append(f"{kind} failed" + (f" ({status})" if status else ""))
    elif kind == "frontend":
        p = "medium"; reasons.append("JavaScript error in the page")
        if "is not defined" in message or "is not a function" in message or "Cannot read properties" in message:
            reasons.append("code error (not a network problem)")
    else:
        p = "low"; reasons.append(f"{kind} error")
    # how many people it hits
    if sessions_24h >= 50 or count_1h >= 100:
        p = _bump(p, 2 if kind != "frontend" else 1); reasons.append(f"many visitors affected ({sessions_24h} in 24 h, {count_1h} errors in 1 h)")
    elif sessions_24h >= 10 or count_1h >= 20:
        p = _bump(p); reasons.append(f"several visitors affected ({sessions_24h} in 24 h)")
    elif count_24h <= 1 and kind not in ("probe", "database"):
        p = _drop(p); reasons.append("seen only once")
    if hours_since_last > 72 and kind != "probe":
        p = _drop(p); reasons.append("not seen for 3 days")
    if p == "critical" and kind == "frontend" and sessions_24h < 50:
        p = "high"
    score = min(100, int(40 * (3 - RANK[p]) / 3 + 60 * min(1.0, (sessions_24h + count_1h / 5) / 60)))
    return p, score, reasons


def adjust_with_ai(rule_priority, ai_priority):
    """The AI may raise or lower the rule-based level by one step at most (it can't make a rare bug critical)."""
    if ai_priority not in RANK:
        return rule_priority
    r, a = RANK[rule_priority], RANK[ai_priority]
    return LEVELS[max(r - 1, min(r + 1, a))]
