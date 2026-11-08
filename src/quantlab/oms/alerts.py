"""Alerts on health changes (OPS-002).

``Alerter.update`` compares a health report with the previous one (``run/alerts-state.json``) and raises an
alert for every check that changes status (worse or recovered). Alerts are appended to ``logs/alerts.jsonl``
and logged; with ``[alerts] webhook_secret`` set they are also POSTed as JSON (``{"text": ...}``, accepted by
Slack / Teams / Discord-style incoming webhooks). The webhook URL is a secret (it usually contains a token)
and is never logged. A failing webhook is logged, never fatal. A check that stays ``warn`` or ``fail`` is
reminded at most every ``remind_after_s``.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from quantlab.core.logging import get_logger
from quantlab.oms.health import HealthReport

log = get_logger(__name__)

Poster = Callable[[str, bytes], None]


def post_json(url: str, body: bytes) -> None:
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
        raise ValueError("alert webhooks must use https (or a local http://127.0.0.1 receiver)")
    request = urllib.request.Request(  # noqa: S310 (scheme checked above)
        url, body, {"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 (scheme checked above)
        response.read()


@dataclass(frozen=True)
class Alert:
    at: str
    check: str
    previous: str | None
    status: str
    detail: str
    reminder: bool = False

    @property
    def text(self) -> str:
        if self.status == "ok":
            return f"[QuantLab paper] RECOVERED {self.check}: {self.detail}"
        again = " (still)" if self.reminder else ""
        return f"[QuantLab paper] {self.status.upper()}{again} {self.check}: {self.detail}"


class Alerter:
    def __init__(
        self,
        home: Path,
        *,
        webhook_url: str | None = None,
        post: Poster = post_json,
        remind_after_s: float = 3600.0,
    ) -> None:
        self._state_file = home / "run" / "alerts-state.json"
        self._log_file = home / "logs" / "alerts.jsonl"
        self._webhook = webhook_url
        self._post = post
        self._remind = remind_after_s

    def _previous(self) -> dict[str, Any]:
        try:
            data: dict[str, Any] = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data

    def update(self, report: HealthReport) -> list[Alert]:
        now = datetime.fromisoformat(report.checked_at)
        prev = self._previous()
        state: dict[str, Any] = {}
        alerts: list[Alert] = []
        for check in report.checks:
            before = prev.get(check.name, {})
            last_alert = before.get("alerted_at")
            entry = {"status": check.status, "alerted_at": last_alert}
            changed = before.get("status") != check.status
            due = (
                check.status != "ok"
                and last_alert is not None
                and (now - datetime.fromisoformat(last_alert)).total_seconds() >= self._remind
            )
            first_ok = not before and check.status == "ok"  # nothing to report on a healthy first run
            if (changed and not first_ok) or due:
                alert = Alert(report.checked_at, check.name, before.get("status"), check.status, check.detail,
                              reminder=due and not changed)  # fmt: skip
                alerts.append(alert)
                entry["alerted_at"] = report.checked_at
            state[check.name] = entry
        for alert in alerts:
            self._deliver(alert)
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_file.with_name(self._state_file.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self._state_file)
        return alerts

    def _deliver(self, alert: Alert) -> None:
        record = {**alert.__dict__, "text": alert.text}
        self._log_file.parent.mkdir(parents=True, exist_ok=True)
        with self._log_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        log.warning("alert", check=alert.check, status=alert.status, detail=alert.detail)
        if self._webhook is None:
            return
        try:
            self._post(self._webhook, json.dumps({"text": alert.text, **record}).encode("utf-8"))
        except Exception as exc:  # never fatal; the URL (a secret) is not logged
            log.warning("alert.webhook_failed", check=alert.check, error=type(exc).__name__)
