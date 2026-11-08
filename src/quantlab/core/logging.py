"""Structured logging with secret redaction (CORE-003).

* JSON lines (default) or human console output, via structlog on top of stdlib logging,
  so third-party library logs go through the same pipeline and the same redaction.
* Redaction runs on every event, for both structured fields (by key name, recursively)
  and free text (``password=...``, ``"token": "..."``, ``Bearer ...``).
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

import structlog

REDACTED = "***REDACTED***"

# Field names whose values are always secret.
_SENSITIVE_KEY = re.compile(
    r"(pass(word|wd|phrase)?|secret|token|api[_-]?key|apikey|auth(orization)?|credential|"
    r"private[_-]?key|cookie|session[_-]?id|account[_-]?(id|number|no))",
    re.IGNORECASE,
)

_KEY_WORDS = (
    r"pass(?:word|wd|phrase)?|secret|token|api[_-]?key|apikey|authorization|credential|"
    r"private[_-]?key|account[_-]?(?:id|number|no)"
)
# key=value / key: value / "key": "value" inside free text.
_TEXT_PAIR = re.compile(
    rf"""(?P<key>["']?\b[\w-]*(?:{_KEY_WORDS})[\w-]*["']?)(?P<sep>\s*[:=]\s*)(?P<q>["']?)(?P<val>[^\s"',;}}&]+)""",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]{6,}", re.IGNORECASE)


def is_sensitive_key(key: object) -> bool:
    return isinstance(key, str) and bool(_SENSITIVE_KEY.search(key))


def redact_text(text: str) -> str:
    text = _BEARER.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    return _TEXT_PAIR.sub(lambda m: f"{m.group('key')}{m.group('sep')}{m.group('q')}{REDACTED}", text)


def redact(value: Any) -> Any:
    """Return a copy of value with secrets masked. Containers are traversed recursively."""
    if isinstance(value, Mapping):
        return {k: (REDACTED if is_sensitive_key(k) else redact(v)) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return type(value)(redact(v) for v in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def redaction_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: mask secrets in every field, including the event message."""
    return {k: (REDACTED if is_sensitive_key(k) else redact(v)) for k, v in event_dict.items()}


_CONFIGURED_HANDLERS: list[logging.Handler] = []


def configure_logging(
    level: str = "INFO",
    json_output: bool = True,
    log_file: Path | None = None,
    stream: Any = None,
) -> None:
    """Configure structlog + stdlib logging. Safe to call repeatedly (replaces previous handlers)."""
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer(colors=False)
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            redaction_processor,  # last step before rendering: nothing escapes unredacted
            renderer,
        ],
    )

    root = logging.getLogger()
    for handler in _CONFIGURED_HANDLERS:
        root.removeHandler(handler)
        handler.close()
    _CONFIGURED_HANDLERS.clear()

    console = logging.StreamHandler(stream if stream is not None else sys.stderr)
    console.setFormatter(formatter)
    _CONFIGURED_HANDLERS.append(console)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=20 * 1024 * 1024, backupCount=10, encoding="utf-8"
        )
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                foreign_pre_chain=shared,
                processors=[
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    redaction_processor,
                    structlog.processors.JSONRenderer(),  # files are always machine-readable
                ],
            )
        )
        _CONFIGURED_HANDLERS.append(file_handler)

    for handler in _CONFIGURED_HANDLERS:
        root.addHandler(handler)
    root.setLevel(level)

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> Any:
    return structlog.stdlib.get_logger(name)


def configure_from_settings(settings: Any, stream: Any = None) -> None:
    """Apply ``settings.logging`` (a quantlab.core.config.Settings). File logs go to QUANTLAB_HOME/logs."""
    cfg = settings.logging
    log_file = settings.layout.logs_dir / "quantlab.log" if cfg.to_file else None
    configure_logging(level=cfg.level, json_output=cfg.json_output, log_file=log_file, stream=stream)
