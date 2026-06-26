"""Tests for the central log handler (no db / no network)."""
import logging

from utils.logger import _CentralLogHandler


def test_central_log_handler_record_to_dict():
    handler = _CentralLogHandler("http://127.0.0.1:1", "machine-a")
    handler.setFormatter(logging.Formatter("%(message)s"))
    record = logging.LogRecord("reg", logging.ERROR, "f.py", 10, "boom %s", ("x",), None)
    payload = handler._record_to_dict(record)
    assert payload["level"] == "ERROR"
    assert payload["source"] == "machine-a"
    assert payload["logger"] == "reg"
    assert payload["message"] == "boom x"
    assert payload["ts"]


def test_emit_never_raises_when_sink_unreachable():
    handler = _CentralLogHandler("http://127.0.0.1:1", "m")
    handler.setFormatter(logging.Formatter("%(message)s"))
    # Must not raise even though the sink is unreachable (best-effort logging).
    handler.emit(logging.LogRecord("x", logging.WARNING, "f", 1, "hi", None, None))
