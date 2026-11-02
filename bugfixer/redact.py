"""Scrub secrets and personal data before anything is stored, logged or sent to the AI."""
import hashlib
import os
import re
import time

_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"), "[anthropic-key]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[github-token]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[github-token]"),
    (re.compile(r"\bsntrys_[A-Za-z0-9_=\-]{20,}"), "[sentry-token]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[aws-key]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"), "[google-key]"),
    (re.compile(r"(?i)\b(bearer|token|api[_-]?key|secret|password|passwd|authorization)(\s*[:=]\s*|\s+)([^\s'\"&,;]{6,})"), r"\1\2[redacted]"),
    (re.compile(r"postgres(?:ql)?://[^\s'\"]+"), "postgresql://[redacted]"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[email]"),
    (re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"), "[ip]"),
    (re.compile(r"\b\d{10}\b"), "[phone]"),
]


def scrub(text, secrets=()):
    if text is None:
        return None
    s = str(text)
    for sec in secrets:
        if sec and len(sec) >= 8:
            s = s.replace(sec, "[secret]")
    for rx, rep in _PATTERNS:
        s = rx.sub(rep, s)
    return s


def scrub_obj(obj, secrets=(), depth=0):
    if depth > 6:
        return "[deep]"
    if isinstance(obj, dict):
        return {str(k)[:60]: ("[redacted]" if re.search(r"(?i)token|secret|password|authorization|cookie|api[_-]?key", str(k))
                              else scrub_obj(v, secrets, depth + 1)) for k, v in list(obj.items())[:60]}
    if isinstance(obj, (list, tuple)):
        return [scrub_obj(v, secrets, depth + 1) for v in list(obj)[:60]]
    if isinstance(obj, str):
        return scrub(obj, secrets)[:4000]
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return scrub(repr(obj), secrets)[:500]


def strip_query(url):
    """Keep the path of a page/API address, drop the query string (it can hold a user's link)."""
    if not url:
        return ""
    return str(url).split("#")[0].split("?")[0][:300]


def anon(value, salt=None):
    """One-way hash used to count distinct visitors without storing who they are (salt rotates daily)."""
    if not value:
        return ""
    salt = salt or (os.getenv("SESSION_SECRET", "") + time.strftime("%Y-%m-%d"))
    return hashlib.sha256(f"{salt}|{value}".encode()).hexdigest()[:24]
