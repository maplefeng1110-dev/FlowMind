import asyncio
import base64
import os
from pathlib import Path

import requests

from utils.logger import setup_logger
from utils.plugin_result import error_result, success_result

logger = setup_logger("InvoiceOCR", "invoice_ocr.log")

DEFAULT_TIMEOUT = float(os.getenv("OCR_API_TIMEOUT", "15"))


def _encode_file(file_path: Path) -> str:
    with file_path.open("rb") as file_obj:
        return base64.b64encode(file_obj.read()).decode("utf-8")


def _call_ocr_api(url: str, img_b64: str, timeout: float) -> dict:
    response = requests.post(url=url, data={"img": img_b64, "compress": 0}, timeout=timeout)
    response.raise_for_status()
    return response.json()


async def run(invoice_path: str, output_format: str = "json") -> dict:
    file_path = Path(invoice_path)
    if not invoice_path:
        return error_result("invoice_path is required")
    if not file_path.exists():
        return error_result("Invoice file does not exist", error=f"File not found: {invoice_path}")
    if file_path.is_dir():
        return error_result("invoice_path must point to a file", error=f"Expected file, got directory: {invoice_path}")

    ocr_url = os.getenv("OCR_API_URL")
    if not ocr_url:
        return error_result("OCR_API_URL is not configured")

    timeout = float(os.getenv("OCR_API_TIMEOUT", str(DEFAULT_TIMEOUT)))
    logger.info("Processing invoice via OCR API: %s", file_path)

    try:
        img_b64 = await asyncio.to_thread(_encode_file, file_path)
        response_json = await asyncio.to_thread(_call_ocr_api, ocr_url, img_b64, timeout)
    except requests.Timeout as exc:
        logger.error("OCR API timed out: %s", exc)
        return error_result("OCR request timed out", error=str(exc))
    except requests.RequestException as exc:
        logger.error("OCR API request failed: %s", exc)
        return error_result("OCR request failed", error=str(exc))
    except Exception as exc:
        logger.error("Failed to prepare OCR request: %s", exc)
        return error_result("OCR processing failed", error=str(exc))

    raw_out = response_json.get("data", {}).get("raw_out")
    if not raw_out:
        logger.warning("OCR API returned no text for %s", file_path)
        return error_result("OCR API returned no text", data={"text": ""})

    lines = [entry[1] for entry in raw_out if isinstance(entry, (list, tuple)) and len(entry) > 1]
    text = "\n".join(lines)
    data = {"text": text, "output_format": output_format}
    logger.info("OCR completed successfully with %s lines", len(lines))
    return success_result(data=data)
