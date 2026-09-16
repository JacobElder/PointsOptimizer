from datetime import date, timedelta

import check_alerts
import deal_email
import deal_log
import flight_search
import price_pending_deals

FUTURE = (date.today() + timedelta(days=60)).isoformat()


class _FakeGit:
    """Records git invocations; per-subcommand return codes are configurable."""

    def __init__(self, branch="main", rc=None, dirty=""):
        self.calls = []
        self.branch = branch
        self.rc = rc or {}
        self.dirty = dirty

    def __call__(self, *args):
        self.calls.append(list(args))

        class R:
            stdout, stderr, returncode = "", "", 0

        r = R()
        if args[0] == "rev-parse":
            r.stdout = self.branch + "\n"
        elif args[0] == "status":
            r.stdout = self.dirty
        r.returncode = self.rc.get(args[0], 0)
        return r


def _valid(**over):
    d = dict(origin="JFK", dest="MAD", program="Flying Blue", cabin="BUSINESS",
             date=FUTURE, points=40000, taxes=30.0, currency="USD")
    d.update(over)
    return d


def _priced(p, cpp=3.0, status="priced"):
    return {**p, "cpp": cpp if status == "priced" else None, "price_status": status,
            "verdict": deal_log.verdict_for(cpp if status == "priced" else None, p["cabin"]),
            "cash_price": 1230.0 if status == "priced" else None, "taxes_usd": 30.0,
            "priced_ok": status in ("priced", "no_fare")}


def _wire(monkeypatch, data, evaluate, git=None):
    saved, emails, notifies = {}, [], []
    git = git or _FakeGit()
    monkeypatch.setattr(flight_search, "is_configured", lambda: True)
    monkeypatch.setattr(flight_search, "serpapi_configured", lambda: False)
    monkeypatch.setattr(price_pending_deals, "_git", git)
    monkeypatch.setattr(deal_log, "load", lambda: data)
    monkeypatch.setattr(deal_log, "save", lambda d: saved.update({"data": d}))
    monkeypatch.setattr(check_alerts, "evaluate_alerts", evaluate)
    monkeypatch.setattr(price_pending_deals, "_notify_mac", lambda msg: notifies.append(msg))
    monkeypatch.setattr(deal_email, "is_configured", lambda: True)
    monkeypatch.setattr(deal_email, "send_deal_alert_email", lambda great: emails.append(great))
    return saved, git, emails, notifies


def _data(pending, deals=()):
    return {"processed_message_ids": [], "deals": list(deals), "pending": list(pending)}


def test_never_hard_resets_even_when_push_fails(monkeypatch):
    data = _data([_valid()])
    git = _FakeGit(rc={"push": 1, "pull": 0})
    saved, git, emails, _ = _wire(monkeypatch, data, lambda tp: [_priced(p) for p in tp], git)

    rc = price_pending_deals.main([])

    assert rc == 1
    assert not any(c[:2] == ["reset", "--hard"] for c in git.calls)
    commit = next(c for c in git.calls if c[0] == "commit")
    assert commit[-2:] == price_pending_deals.DATA_FILES  # only data files, never other staged work


def test_refuses_to_run_off_main(monkeypatch):
    evaluated = []
    _wire(monkeypatch, _data([_valid()]), lambda tp: evaluated.append(tp) or [], _FakeGit(branch="feature"))

    assert price_pending_deals.main([]) == 1
    assert evaluated == []


def test_pull_failure_aborts_without_pricing(monkeypatch):
    evaluated = []
    git = _FakeGit(rc={"pull": 1})
    _wire(monkeypatch, _data([_valid()]), lambda tp: evaluated.append(tp) or [], git)

    assert price_pending_deals.main([]) == 1
    assert evaluated == []
    assert ["rebase", "--abort"] in git.calls


def test_email_sent_and_marked_notified(monkeypatch):
    data = _data([_valid()])
    saved, git, emails, notifies = _wire(monkeypatch, data, lambda tp: [_priced(p) for p in tp])

    assert price_pending_deals.main([]) == 0
    assert len(emails) == 1 and len(notifies) == 1
    assert saved["data"]["deals"][0]["notified"] is True
    assert saved["data"]["pending"] == []


def test_transient_failure_stays_pending_with_attempt_counter(monkeypatch):
    data = _data([_valid()])
    saved, *_ = _wire(monkeypatch, data, lambda tp: [_priced(p, status="failed") for p in tp])

    price_pending_deals.main(["--no-git"])

    out = saved["data"]
    assert out["deals"] == []
    assert out["pending"][0]["price_attempts"] == 1
    assert "cpp" not in out["pending"][0]


def test_failures_never_filed_as_deals_even_after_max_attempts(monkeypatch):
    stuck = _valid(price_attempts=price_pending_deals.MAX_PRICE_ATTEMPTS)
    evaluated = []
    _wire(monkeypatch, _data([stuck]), lambda tp: evaluated.append(tp) or [])

    price_pending_deals.main(["--no-git"])

    assert evaluated == []  # capped deals are skipped, and stay queued


def test_quota_exhaustion_leaves_attempts_untouched(monkeypatch):
    data = _data([_valid()])
    saved, *_ = _wire(monkeypatch, data, lambda tp: [_priced(p, status="quota") for p in tp])

    price_pending_deals.main(["--no-git"])

    entry = saved["data"]["pending"][0]
    assert "price_attempts" not in entry
    assert entry["last_price_status"] == "quota"


def test_past_dated_pending_is_expired_not_priced(monkeypatch):
    past = _valid(date=(date.today() - timedelta(days=3)).isoformat())
    evaluated = []
    saved, *_ = _wire(monkeypatch, _data([past]), lambda tp: evaluated.append(tp) or [])

    price_pending_deals.main(["--no-git"])

    assert evaluated == []
    assert saved["data"]["pending"] == [] and saved["data"]["expired"] == [past]


def test_too_far_out_is_left_queued_without_lookup(monkeypatch):
    far = _valid(date=(date.today() + timedelta(days=345)).isoformat())
    evaluated = []
    _wire(monkeypatch, _data([far]), lambda tp: evaluated.append(tp) or [])

    price_pending_deals.main(["--no-git"])

    assert evaluated == []
    assert deal_log.load()["pending"] == [far]


def test_old_unpriced_deals_are_requeued(monkeypatch):
    old = {**_valid(), "cpp": None, "verdict": "NO CASH PRICE", "priced_ok": False, "error": "HTTP 429"}
    good = {**_valid(dest="LIS"), "cpp": 2.2, "verdict": "BOOK"}
    data = _data([], deals=[old, good])
    saved, *_ = _wire(monkeypatch, data, lambda tp: [_priced(p, status="failed") for p in tp])

    price_pending_deals.main(["--no-git", "--no-email"])

    out = saved["data"]
    assert out["deals"] == [good]
    assert out["pending"][0]["dest"] == "MAD" and "error" not in out["pending"][0]


def test_prioritizes_premium_cabin_under_limit(monkeypatch):
    seen = {}
    econ = _valid(cabin="ECONOMY", dest="LIR", points=20000)
    biz = _valid(cabin="BUSINESS", dest="CAI", points=80000)
    _wire(monkeypatch, _data([econ, biz]), lambda tp: seen.setdefault("tp", tp) and [])

    price_pending_deals.main(["--no-git", "--limit", "1", "--no-email"])

    assert [d["cabin"] for d in seen["tp"]] == ["BUSINESS"]


def test_queue_order_preserved(monkeypatch):
    a, b, c = _valid(dest="AAA"), _valid(dest="BBB", cabin="ECONOMY"), _valid(dest="CCC")
    saved, *_ = _wire(monkeypatch, _data([a, b, c]),
                      lambda tp: [_priced(p, status="no_fare") for p in tp])

    price_pending_deals.main(["--no-git"])

    assert [p["dest"] for p in saved["data"]["pending"]] == ["AAA", "BBB", "CCC"]


def test_no_changes_means_no_commit(monkeypatch):
    git = _FakeGit()
    far = _valid(date=(date.today() + timedelta(days=345)).isoformat())
    _wire(monkeypatch, _data([far]), lambda tp: [], git)

    assert price_pending_deals.main([]) == 0
    assert not any(c[0] == "commit" for c in git.calls)


def test_notify_mac_noop_when_osascript_missing(monkeypatch):
    monkeypatch.setattr(price_pending_deals.shutil, "which", lambda name: None)
    calls = []
    monkeypatch.setattr(price_pending_deals.subprocess, "run", lambda *a, **k: calls.append(a))

    price_pending_deals._notify_mac("test message")

    assert calls == []


def test_notify_mac_invokes_osascript_when_present(monkeypatch):
    monkeypatch.setattr(price_pending_deals.shutil, "which", lambda name: "/usr/bin/osascript")
    calls = []
    monkeypatch.setattr(price_pending_deals.subprocess, "run", lambda *a, **k: calls.append(a))

    price_pending_deals._notify_mac('great deal "quoted"')

    assert len(calls) == 1
    assert calls[0][0][0] == "osascript"
