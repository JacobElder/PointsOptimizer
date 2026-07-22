import price_pending_deals


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
