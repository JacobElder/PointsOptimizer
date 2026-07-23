import check_alerts
import deal_email
import deal_log
import flight_search
import price_pending_deals


class _FakeRun:
    """Simulates git subprocess calls; push returncode is configurable."""

    def __init__(self, push_rc=0):
        self.push_rc = push_rc
        self.calls = []

    def __call__(self, cmd, *args, **kwargs):
        self.calls.append(cmd)

        class R:
            pass

        r = R()
        r.stdout = ""
        r.stderr = ""
        r.returncode = 0
        if cmd[:2] == ["git", "push"]:
            r.returncode = self.push_rc
        return r


def _valid(**over):
    d = dict(origin="JFK", dest="MAD", program="Flying Blue", cabin="BUSINESS",
             date="2026-11-04", points=40000, taxes=30.0, currency="USD")
    d.update(over)
    return d


def _wire(monkeypatch, pending, priced, push_rc=0):
    """Set up main() with in-memory state and stubbed git/pricing/email."""
    data = {"processed_message_ids": [], "deals": [], "pending": list(pending)}
    saved = {}
    fake_run = _FakeRun(push_rc=push_rc)
    emails, notifies = [], []

    monkeypatch.setattr(flight_search, "is_configured", lambda: True)
    monkeypatch.setattr(price_pending_deals.subprocess, "run", fake_run)
    monkeypatch.setattr(deal_log, "load", lambda: data)
    monkeypatch.setattr(deal_log, "save", lambda d: saved.update({"data": d}))
    monkeypatch.setattr(check_alerts, "evaluate_alerts", lambda to_price: priced)
    monkeypatch.setattr(price_pending_deals, "_notify_mac", lambda msg: notifies.append(msg))
    monkeypatch.setattr(deal_email, "is_configured", lambda: True)
    monkeypatch.setattr(deal_email, "send_deal_alert_email", lambda great: emails.append(great))
    return data, saved, fake_run, emails, notifies


def test_email_not_sent_when_push_fails(monkeypatch):
    great_deal = _valid(cpp=3.0, verdict="BOOK", priced_ok=True, taxes_usd=30.0, cash_price=1230.0)
    data, saved, fake_run, emails, notifies = _wire(
        monkeypatch, pending=[_valid()], priced=[great_deal], push_rc=1
    )

    price_pending_deals.main()

    assert emails == []      # FIX 2: no email when the push didn't land
    assert notifies == []
    assert ["git", "reset", "--hard", "origin/main"] in fake_run.calls  # self-heal


def test_email_sent_when_push_succeeds(monkeypatch):
    great_deal = _valid(cpp=3.0, verdict="BOOK", priced_ok=True, taxes_usd=30.0, cash_price=1230.0)
    data, saved, fake_run, emails, notifies = _wire(
        monkeypatch, pending=[_valid()], priced=[great_deal], push_rc=0
    )

    price_pending_deals.main()

    assert len(emails) == 1 and len(emails[0]) == 1
    assert len(notifies) == 1


def test_transient_failure_keeps_deal_pending_for_retry(monkeypatch):
    failed = _valid(cpp=None, verdict="NO CASH PRICE", priced_ok=False, price_error="boom", taxes_usd=30.0)
    data, saved, fake_run, emails, notifies = _wire(
        monkeypatch, pending=[_valid()], priced=[failed], push_rc=0
    )

    price_pending_deals.main()

    out = saved["data"]
    assert out["deals"] == []                       # FIX 3: not recorded as a final deal
    assert len(out["pending"]) == 1                  # stays queued
    assert out["pending"][0]["price_attempts"] == 1  # with a bounded retry counter
    assert "cpp" not in out["pending"][0]            # pricing fields stripped back off


def test_transient_failure_gives_up_after_max_attempts(monkeypatch):
    failed = _valid(cpp=None, verdict="NO CASH PRICE", priced_ok=False,
                    price_error="boom", price_attempts=price_pending_deals.MAX_PRICE_ATTEMPTS - 1)
    data, saved, fake_run, emails, notifies = _wire(
        monkeypatch, pending=[_valid()], priced=[failed], push_rc=0
    )

    price_pending_deals.main()

    out = saved["data"]
    assert len(out["deals"]) == 1      # recorded as definitive after the cap
    assert out["pending"] == []


def test_malformed_pending_entry_is_skipped_not_crashed(monkeypatch):
    seen = {}

    def _capture(to_price):
        seen["to_price"] = to_price
        return []

    good = _valid()
    bad = dict(origin="JFK", dest="CTG", program="Aeroplan")  # missing required fields
    data = {"processed_message_ids": [], "deals": [], "pending": [good, bad]}
    saved = {}
    monkeypatch.setattr(flight_search, "is_configured", lambda: True)
    monkeypatch.setattr(price_pending_deals.subprocess, "run", _FakeRun(push_rc=0))
    monkeypatch.setattr(deal_log, "load", lambda: data)
    monkeypatch.setattr(deal_log, "save", lambda d: saved.update({"data": d}))
    monkeypatch.setattr(check_alerts, "evaluate_alerts", _capture)
    monkeypatch.setattr(price_pending_deals, "_notify_mac", lambda msg: None)
    monkeypatch.setattr(deal_email, "is_configured", lambda: False)

    price_pending_deals.main()  # must not raise

    assert seen["to_price"] == [good]           # only the valid entry was priced
    assert bad in saved["data"]["pending"]      # malformed entry retained, not lost


def test_notify_mac_noop_when_osascript_missing(monkeypatch):
    monkeypatch.setattr(price_pending_deals.shutil, "which", lambda name: None)
    calls = []
    monkeypatch.setattr(price_pending_deals.subprocess, "run", lambda *a, **k: calls.append(a))

    price_pending_deals._notify_mac("test message")

    assert calls == []  # never even attempted to invoke osascript


def test_notify_mac_invokes_osascript_when_present(monkeypatch):
    monkeypatch.setattr(price_pending_deals.shutil, "which", lambda name: "/usr/bin/osascript")
    calls = []
    monkeypatch.setattr(price_pending_deals.subprocess, "run", lambda *a, **k: calls.append(a))

    price_pending_deals._notify_mac('great deal "quoted"')

    assert len(calls) == 1
    assert calls[0][0][0] == "osascript"
