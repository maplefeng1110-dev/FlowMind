import pytest

from agent.plugins import invoice_ocr, send_email


@pytest.mark.asyncio
async def test_invoice_ocr_requires_configuration(monkeypatch, tmp_path):
    file_path = tmp_path / "invoice.jpg"
    file_path.write_bytes(b"demo")
    monkeypatch.delenv("OCR_API_URL", raising=False)

    result = await invoice_ocr.run(str(file_path))

    assert result["status"] == "error"
    assert "OCR_API_URL" in result["message"]


@pytest.mark.asyncio
async def test_invoice_ocr_handles_timeout(monkeypatch, tmp_path):
    file_path = tmp_path / "invoice.jpg"
    file_path.write_bytes(b"demo")
    monkeypatch.setenv("OCR_API_URL", "http://localhost:8080/ocr")

    def raise_timeout(*_args, **_kwargs):
        raise invoice_ocr.requests.Timeout("timeout")

    monkeypatch.setattr("agent.plugins.invoice_ocr._call_ocr_api", raise_timeout)

    result = await invoice_ocr.run(str(file_path))

    assert result["status"] == "error"
    assert result["message"] == "OCR request timed out"


@pytest.mark.asyncio
async def test_invoice_ocr_returns_empty_text_error(monkeypatch, tmp_path):
    file_path = tmp_path / "invoice.jpg"
    file_path.write_bytes(b"demo")
    monkeypatch.setenv("OCR_API_URL", "http://localhost:8080/ocr")
    monkeypatch.setattr("agent.plugins.invoice_ocr._call_ocr_api", lambda *_args: {"data": {"raw_out": []}})

    result = await invoice_ocr.run(str(file_path))

    assert result["status"] == "error"
    assert result["data"]["text"] == ""


@pytest.mark.asyncio
async def test_send_email_requires_credentials(monkeypatch):
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)

    result = await send_email.run("user@example.com", "subject", "<p>body</p>")

    assert result["status"] == "error"
    assert "credentials" in result["message"].lower()


@pytest.mark.asyncio
async def test_send_email_success(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setattr("agent.plugins.send_email._send_email_sync", lambda *_args: None)

    result = await send_email.run("user@example.com", "subject", "<p>body</p>")

    assert result["status"] == "success"
    assert result["data"]["sent"] is True


@pytest.mark.asyncio
async def test_send_email_failure(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")

    def raise_error(*_args):
        raise RuntimeError("smtp failed")

    monkeypatch.setattr("agent.plugins.send_email._send_email_sync", raise_error)

    result = await send_email.run("user@example.com", "subject", "<p>body</p>")

    assert result["status"] == "error"
    assert result["error"] == "smtp failed"
