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


def test_central_log_handler_sends_agent_token(monkeypatch):
    import utils.logger as logger_mod
    from utils.internal_api import AGENT_TOKEN_HEADER

    sent = []
    monkeypatch.setenv("FLOWMIND_AGENT_WS_TOKEN", "agent-token-for-tests")
    monkeypatch.setattr(logger_mod.urllib.request, "urlopen", lambda req, timeout=None: sent.append(req))
    handler = _CentralLogHandler("http://127.0.0.1:1", "m")
    handler._post([{"level": "WARNING", "message": "hi"}])
    assert sent and sent[0].get_header(AGENT_TOKEN_HEADER.capitalize()) == "agent-token-for-tests"
