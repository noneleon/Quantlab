import io
import json
import logging

import pytest
from hypothesis import given
from hypothesis import strategies as st

from quantlab.core.logging import REDACTED, configure_logging, get_logger, redact, redact_text


@pytest.fixture
def stream():
    buf = io.StringIO()
    configure_logging(level="DEBUG", json_output=True, stream=buf)
    yield buf
    configure_logging(level="WARNING", json_output=True, stream=io.StringIO())


def lines(buf):
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def test_structured_fields_redacted_by_key(stream):
    get_logger("t").info(
        "connect",
        password="hunter2",
        api_key="AKIA123",
        nested={"Token": "abc", "port": 4002},
        account_number="U1234567",
        host="127.0.0.1",
    )
    rec = lines(stream)[-1]
    assert rec["password"] == REDACTED
    assert rec["api_key"] == REDACTED
    assert rec["account_number"] == REDACTED
    assert rec["nested"] == {"Token": REDACTED, "port": 4002}
    assert rec["host"] == "127.0.0.1"
    assert rec["event"] == "connect"
    assert rec["level"] == "info"
    assert "timestamp" in rec
    assert "hunter2" not in stream.getvalue() and "AKIA123" not in stream.getvalue()


def test_free_text_and_stdlib_logs_redacted(stream):
    logging.getLogger("thirdparty").warning("login failed password=hunter2 user=bob")
    get_logger("t").info("header Authorization: Bearer abcdef123456 sent")
    out = stream.getvalue()
    assert "hunter2" not in out and "abcdef123456" not in out
    assert "user=bob" in out


def test_exception_text_redacted(stream):
    try:
        raise RuntimeError("bad token=zzzsecretzzz")
    except RuntimeError:
        get_logger("t").exception("boom")
    assert "zzzsecretzzz" not in stream.getvalue()


def test_file_output_is_json_and_redacted(tmp_path):
    log_file = tmp_path / "logs" / "quantlab.log"
    configure_logging(level="INFO", json_output=False, log_file=log_file, stream=io.StringIO())
    get_logger("t").info("x", secret="s3cr3t")
    for h in logging.getLogger().handlers:
        h.flush()
    rec = json.loads(log_file.read_text().splitlines()[-1])
    assert rec["secret"] == REDACTED
    configure_logging(level="WARNING", stream=io.StringIO())


@pytest.mark.parametrize(
    ("text", "leak"),
    [
        ('{"password": "p@ss"}', "p@ss"),
        ("api_key=XYZ987", "XYZ987"),
        ("IB_PASSWORD: qwerty", "qwerty"),
        ("Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
    ],
)
def test_redact_text_patterns(text, leak):
    assert leak not in redact_text(text)


def test_redact_preserves_container_types():
    out = redact({"a": [1, "token=abc"], "b": ("x",)})
    assert out == {"a": [1, f"token={REDACTED}"], "b": ("x",)}


@given(st.text(alphabet=st.characters(whitelist_categories=("L", "N")), min_size=6, max_size=40))
def test_password_value_never_survives(secret):
    assert secret not in redact_text(f"password={secret}") or secret in "password="


def test_configure_from_settings_writes_to_home_logs(ql_home):
    from quantlab.core.config import load_settings
    from quantlab.core.logging import configure_from_settings

    s = load_settings(overrides={"logging": {"level": "INFO", "json_output": True}})
    configure_from_settings(s, stream=io.StringIO())
    get_logger("t").info("hello", token="tkn-value")
    for h in logging.getLogger().handlers:
        h.flush()
    content = (s.layout.logs_dir / "quantlab.log").read_text()
    assert "hello" in content and "tkn-value" not in content
    configure_logging(level="WARNING", stream=io.StringIO())
