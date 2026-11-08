"""Health checks (OPS-001) and alerts (OPS-002). Synthetic state files only; nothing is sent anywhere except
to a local test receiver on 127.0.0.1."""

import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from quantlab.oms.alerts import Alerter, post_json
from quantlab.oms.health import Check, HealthReport, run_checks
from quantlab.oms.pausing import pause

from .test_oms_risk import stack  # noqa: F401  (fixture)

NOW = datetime(2026, 11, 9, 15, tzinfo=UTC)


def checks(stack, home, **kw):  # noqa: F811
    args = {
        "home": home,
        "kill_switch": stack["kill"],
        "limits_file": stack["limits"],
        "supervisor_file": home / "run" / "supervisor-paper.json",
        "native_dir": home / "engine" / "lean-native",
        "launcher": "docker",
        "now": NOW,
    } | kw
    report = run_checks(stack["sessions"], **args)
    return report, {c.name: c for c in report.checks}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_a_fresh_setup_warns_about_the_supervisor_only(stack, tmp_path):  # noqa: F811
    report, by = checks(stack, tmp_path)
    assert by["database"].status == by["risk limits"].status == by["kill switch"].status == "ok"
    assert by["supervisor"].status == "warn" and "never started" in by["supervisor"].detail
    assert by["deployments"].detail == "none running" and by["engine"].status == "ok"
    assert report.status == "warn"


def test_failures_and_warnings_are_reported(stack, tmp_path):  # noqa: F811
    stack["limits"].unlink()
    with stack["sessions"]() as s:
        stack["kill"].engage(s, actor="owner", reason="test", channel="test")
        pause(s, "d2", actor="owner", reason="review", channel="test")
        s.commit()
    write(tmp_path / "run" / "supervisor-paper.json",
          {"heartbeat_at": (NOW - timedelta(minutes=10)).isoformat(), "interval_s": 5})  # fmt: skip
    beat = NOW.isoformat()
    write(tmp_path / "deployments" / "d1" / "20261109T140000Z" / "status.json",
          {"deployment": "d1", "state": "running",
           "heartbeat_at": (NOW - timedelta(hours=1)).isoformat()})  # fmt: skip
    write(tmp_path / "deployments" / "d2" / "20261109T140000Z" / "status.json",
          {"deployment": "d2", "state": "running", "heartbeat_at": beat, "isolated": False})  # fmt: skip
    report, by = checks(stack, tmp_path, launcher="native")
    assert by["risk limits"].status == "fail" and "every order is rejected" in by["risk limits"].detail
    assert by["kill switch"].status == "warn" and "ENGAGED by owner" in by["kill switch"].detail
    assert by["supervisor"].status == "fail" and "not running" in by["supervisor"].detail
    assert by["deployments"].status == "fail" and "d1" in by["deployments"].detail  # lost process
    assert by["engine"].status == "fail"
    assert report.status == "fail"
    (tmp_path / "deployments" / "d1").rename(tmp_path / "gone")
    _, by = checks(stack, tmp_path)
    assert by["deployments"].status == "warn"
    assert "not isolated: d2" in by["deployments"].detail and "paused: d2" in by["deployments"].detail


def test_an_unhealthy_supervisor_is_a_failure_with_its_last_error(stack, tmp_path):  # noqa: F811
    write(tmp_path / "run" / "supervisor-paper.json",
          {"heartbeat_at": NOW.isoformat(), "interval_s": 5, "healthy": False,
           "errors": ["reconcile: broker unreachable"]})  # fmt: skip
    _, by = checks(stack, tmp_path)
    assert by["supervisor"].status == "fail" and "broker unreachable" in by["supervisor"].detail


def report(at, **statuses):
    return HealthReport(
        max(statuses.values(), key=["ok", "warn", "fail"].index),
        at.isoformat(),
        [Check(name, st, f"{name} is {st}") for name, st in statuses.items()],
    )


def test_alerts_on_changes_recoveries_and_reminders_only(tmp_path):
    sent = []
    a = Alerter(tmp_path, webhook_url="https://hooks.example/x", post=lambda u, b: sent.append(json.loads(b)),
                remind_after_s=3600)  # fmt: skip
    assert a.update(report(NOW, db="ok", supervisor="ok")) == []  # healthy first run: quiet
    (alert,) = a.update(report(NOW + timedelta(minutes=1), db="ok", supervisor="fail"))
    assert alert.text == "[QuantLab paper] FAIL supervisor: supervisor is fail" and alert.previous == "ok"
    assert a.update(report(NOW + timedelta(minutes=30), db="ok", supervisor="fail")) == []  # no repeats
    (again,) = a.update(report(NOW + timedelta(minutes=62), db="ok", supervisor="fail"))
    assert again.reminder and "(still)" in again.text
    (ok,) = a.update(report(NOW + timedelta(minutes=63), db="ok", supervisor="ok"))
    assert ok.text.startswith("[QuantLab paper] RECOVERED supervisor")
    assert [m["text"] for m in sent] == [alert.text, again.text, ok.text]
    lines = (tmp_path / "logs" / "alerts.jsonl").read_text().splitlines()
    assert len(lines) == 3 and json.loads(lines[0])["status"] == "fail"
    (first,) = Alerter(tmp_path / "other").update(report(NOW, supervisor="warn"))  # first run, not ok
    assert first.previous is None


def test_a_failing_webhook_never_breaks_alerting_and_its_url_is_not_logged(tmp_path, caplog):
    def broken(url, body):
        raise ConnectionError(f"cannot reach {url}")

    a = Alerter(tmp_path, webhook_url="https://hooks.example/SECRET-TOKEN", post=broken)
    assert len(a.update(report(NOW, db="fail"))) == 1
    assert (tmp_path / "logs" / "alerts.jsonl").is_file()
    assert "SECRET-TOKEN" not in caplog.text


def test_webhooks_need_https_and_reach_a_local_receiver(tmp_path):
    with pytest.raises(ValueError, match="https"):
        post_json("http://example.com/hook", b"{}")
    with pytest.raises(ValueError, match="https"):
        post_json("file:///etc/passwd", b"{}")
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=server.handle_request, daemon=True).start()
    a = Alerter(tmp_path, webhook_url=f"http://127.0.0.1:{server.server_address[1]}/hook")
    a.update(report(NOW, supervisor="fail"))
    server.server_close()
    assert received[0]["text"] == "[QuantLab paper] FAIL supervisor: supervisor is fail"


def test_supervisor_after_tick_errors_never_stop_supervision(stack, tmp_path):  # noqa: F811
    from quantlab.oms.supervisor import Supervisor

    sup = Supervisor(stack["oms"], stack["sessions"], stack["kill"], tmp_path / "sup.json",
                     interval_s=1, calendar="none", clock=stack["clock"])  # fmt: skip
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("alerting broke")

    sup.run(max_ticks=3, sleep=lambda s: None, after_tick=boom)
    assert sup.status.ticks == 3 and len(calls) == 3
