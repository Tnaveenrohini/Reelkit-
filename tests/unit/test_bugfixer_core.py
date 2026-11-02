"""Unit tests: fingerprint, priority, patching, policy, redaction, CI evaluation, Sentry mapping, expected errors."""
import pytest

from bugfixer import detection, fingerprint, patching, policy, priority, testing
from bugfixer.config import Config
from bugfixer.redact import scrub, scrub_obj, strip_query
from bugfixer.services import deployment_id_from_status, sentry_event_to_report, verify_sentry_signature


def test_fingerprint_groups_same_bug_ignoring_numbers_and_lines():
    tb1 = 'Traceback (most recent call last):\n  File "/var/task/app.py", line 10, in convert\n    x\nKeyError: 1'
    tb2 = 'Traceback (most recent call last):\n  File "/var/task/app.py", line 99, in convert\n    x\nKeyError: 2'
    f1 = fingerprint.compute("backend", "KeyError", "key 123 missing in /tmp/a1", fingerprint.parse_py_traceback(tb1), "/api/convert")
    f2 = fingerprint.compute("backend", "KeyError", "key 456 missing in /tmp/b2", fingerprint.parse_py_traceback(tb2), "/api/convert")
    assert f1 == f2
    assert f1 != fingerprint.compute("backend", "ValueError", "key 1 missing", fingerprint.parse_py_traceback(tb1), "/api/convert")


def test_js_stack_parsing_chrome_and_firefox():
    chrome = fingerprint.parse_js_stack("TypeError: x\n    at showDl (https://www.waverise.in/:2205:40)\n    at https://www.waverise.in/:2300:1", "https://www.waverise.in")
    ff = fingerprint.parse_js_stack("showDl@https://www.waverise.in/:2205:40", "https://www.waverise.in")
    assert chrome[0]["function"] == "showDl" and chrome[0]["line"] == 2205 and chrome[0]["in_app"]
    assert ff[0]["function"] == "showDl" and ff[0]["col"] == 40


@pytest.mark.parametrize("kind,status,route,sessions,expect", [
    ("probe", None, "/", 0, "high"), ("backend", 500, "/api/download", 3, "high"), ("api", 500, "/api/other", 3, "medium"),
    ("frontend", None, "/", 3, "medium"), ("download", 0, "/api/download", 3, "low"), ("database", None, "", 2, "high")])
def test_priority_levels(kind, status, route, sessions, expect):
    p, score, why = priority.assess(kind, status, route, count_1h=2, count_24h=3, sessions_24h=sessions)
    assert p == expect and 0 <= score <= 100 and why


def test_priority_many_visitors_and_probe_streak_make_it_critical():
    assert priority.assess("backend", 500, "/api/info", count_1h=150, count_24h=400, sessions_24h=80)[0] == "critical"
    assert priority.assess("probe", 503, "/", probe_failures=2)[0] == "critical"
    assert priority.assess("frontend", None, "/", count_1h=150, count_24h=300, sessions_24h=20)[0] == "high"   # frontend never critical below 50 visitors


def test_ai_can_only_move_priority_one_step():
    assert priority.adjust_with_ai("low", "critical") == "medium"
    assert priority.adjust_with_ai("high", "low") == "medium"
    assert priority.adjust_with_ai("medium", "nonsense") == "medium"


def test_patch_applies_exact_unique_edits():
    files = {"app.py": "a = 1\nb = 2\n"}
    new, diff, stats = patching.apply_edits(files, [{"path": "app.py", "old": "b = 2", "new": "b = 3"}])
    assert new["app.py"] == "a = 1\nb = 3\n" and "-b = 2" in diff and "+b = 3" in diff and stats["lines"] == 2


@pytest.mark.parametrize("edit,err", [
    ({"path": "app.py", "old": "nope", "new": "x"}, "not found"), ({"path": "app.py", "old": "x", "new": "y"}, "appears 2 times"),
    ({"path": "../etc/passwd", "old": "", "new": "x"}, "invalid path"), ({"path": "wr_new.py", "old": "", "new": "x"}, "new files may only"),
    ({"path": "app.py", "old": "", "new": "x"}, "'old' is empty")])
def test_patch_rejects_bad_edits(edit, err):
    with pytest.raises(patching.PatchError, match=err):
        patching.apply_edits({"app.py": "x\nx\n"}, [edit])


def test_patch_can_add_regression_test_file():
    new, diff, stats = patching.apply_edits({"tests/regression_fixes/test_x.py": None}, [{"path": "tests/regression_fixes/test_x.py", "old": "", "new": "def test(): pass\n"}])
    assert "tests/regression_fixes/test_x.py" in new and "/dev/null" in diff


def _assess(path, before, after, **kw):
    new, diff, stats = patching.apply_edits({path: before}, [{"path": path, "old": before.splitlines()[0], "new": after}])
    return policy.assess(stats, diff, {path: before}, new, Config(), kw.get("risk", "low"), kw.get("flags"))


def test_policy_blocks_forbidden_files_and_dangerous_code():
    assert not _assess(".github/workflows/x.yml", "a: 1\n", "a: 2")["allowed"]
    assert not _assess("bugfixer/worker.py", "x = 1\n", "x = 2")["allowed"]
    assert not _assess("app.py", "x = 1\n", "x = eval(input())")["allowed"]
    assert not _assess("app.py", "x = 1\n", "subprocess.run(c, shell=True)")["allowed"]
    assert not _assess("app.py", "x = 1\n", 'API_KEY = "abcdefghijklmnop1234"')["allowed"]


def test_policy_needs_approval_for_sensitive_and_new_domains():
    r = _assess("wr_admin.py", "x = 1\n", "x = 2")
    assert r["allowed"] and r["needs_approval"] and r["risk"] == "high"
    r = _assess("app.py", "x = 1\n", 'u = "https://evil.example.com/a"')
    assert r["needs_approval"] and "evil.example.com" in " ".join(r["reasons"])
    r = _assess("app.py", "x = 1\n", "x = 2")
    assert r["allowed"] and not r["needs_approval"] and r["risk"] == "low"


def test_policy_marks_big_deletions_destructive():
    before = "".join(f"line {i}\n" for i in range(60))
    new, diff, stats = patching.apply_edits({"app.py": before}, [{"path": "app.py", "old": "".join(f"line {i}\n" for i in range(5, 50)), "new": ""}])
    r = policy.assess(stats, diff, {"app.py": before}, new, Config(max_patch_lines=500), "low")
    assert r["destructive"] and r["needs_approval"]


def test_deploy_permissions():
    low = {"risk": "low", "needs_approval": False}
    assert policy.deploy_needs_approval("approval", low, "low")[0]
    assert not policy.deploy_needs_approval("low_risk", low, "low")[0]
    assert policy.deploy_needs_approval("low_risk", {"risk": "medium"}, "low")[0]
    assert not policy.deploy_needs_approval("auto", {"risk": "medium"}, "high")[0]
    assert policy.deploy_needs_approval("auto", {"risk": "high", "needs_approval": True}, "low")[0]


def test_redaction_removes_secrets_and_personal_data():
    s = scrub("token=abcdef123456 sk-ant-api03-ABCDEFGHIJKLMNOP me@site.com 10.1.2.3 postgresql://u:p@db/x", ["mysecretvalue"])
    for bad in ("abcdef123456", "sk-ant-api03", "me@site.com", "10.1.2.3", "u:p@db"):
        assert bad not in s
    assert scrub_obj({"Authorization": "Bearer x", "ok": "fine"})["Authorization"] == "[redacted]"
    assert strip_query("https://www.waverise.in/?url=https://instagram.com/p/x#a") == "https://www.waverise.in/"


def test_expected_errors_are_not_bugs():
    assert detection.is_expected({"source": "client", "kind": "api", "status_code": 422, "message": "This post is private"})
    assert detection.is_expected({"source": "client", "error_type": "visible_error", "message": "Paste an Instagram link first."})
    assert detection.is_expected({"source": "server", "kind": "api", "status_code": 503, "message": "YouTube downloads are coming soon to WaveRise."})
    assert detection.is_expected({"source": "client", "kind": "frontend", "message": "ResizeObserver loop limit exceeded"})
    assert not detection.is_expected({"source": "client", "kind": "frontend", "message": "Cannot read properties of null (reading 'x')"})
    assert not detection.is_expected({"source": "server", "kind": "api", "status_code": 500, "message": "KeyError: 'url'"})


def test_ci_evaluation():
    req = ["lint", "unit"]
    run = lambda n, s, c, t="2026-01-01T00:00:00Z": {"name": n, "status": s, "conclusion": c, "started_at": t, "id": 1}
    assert testing.evaluate([run("lint", "completed", "success")], req)[0] == "pending"
    assert testing.evaluate([run("lint", "completed", "success"), run("unit", "in_progress", None)], req)[0] == "pending"
    assert testing.evaluate([run("lint", "completed", "success"), run("unit", "completed", "failure")], req)[0] == "failed"
    assert testing.evaluate([run("lint", "completed", "success"), run("unit", "completed", "success"), run("audit", "completed", "failure")], req)[0] == "passed"
    rerun = [run("unit", "completed", "failure", "2026-01-01T00:00:00Z"), run("unit", "completed", "success", "2026-01-01T01:00:00Z"), run("lint", "completed", "success")]
    assert testing.evaluate(rerun, req)[0] == "passed"


def test_sentry_mapping_signature_and_vercel_status():
    issue = {"id": "42", "title": "KeyError: 'x'", "metadata": {"type": "KeyError", "value": "'x'"}, "count": "7", "userCount": 3, "platform": "python"}
    event = {"entries": [{"type": "exception", "data": {"values": [{"type": "KeyError", "value": "'x'", "stacktrace": {"frames": [
        {"filename": "app.py", "function": "convert", "lineNo": 12, "inApp": True}]}}]}}], "tags": [{"key": "transaction", "value": "/api/convert"}]}
    r = sentry_event_to_report(issue, event)
    assert r["kind"] == "backend" and r["error_type"] == "KeyError" and r["frames"][0]["function"] == "convert" and r["route"] == "/api/convert"
    import hashlib, hmac
    body = b'{"action":"created"}'
    assert verify_sentry_signature(body, hmac.new(b"s3cret", body, hashlib.sha256).hexdigest(), "s3cret")
    assert not verify_sentry_signature(body, "bad", "s3cret")
    assert deployment_id_from_status("https://vercel.com/team/proj/EwUzU8FhNr6CJv5x5RDQYxH2Kb6k") == "EwUzU8FhNr6CJv5x5RDQYxH2Kb6k"
    assert deployment_id_from_status("https://example.com/") is None
