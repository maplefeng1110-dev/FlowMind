import json as _json
import logging
import os
import queue
import socket
import sys
import threading
import time
import urllib.request
from logging.handlers import RotatingFileHandler

from utils.paths import LOG_DIR, ensure_runtime_dirs

_LOG_SINK_URL = os.getenv("FLOWMIND_LOG_SINK_URL")
_LOG_SOURCE = os.getenv("MACHINE_ID") or os.getenv("FLOWMIND_LOG_SOURCE") or socket.gethostname()
_REMOTE_LEVEL = getattr(logging, str(os.getenv("FLOWMIND_LOG_REMOTE_LEVEL", "WARNING")).upper(), logging.WARNING)


class _CentralLogHandler(logging.Handler):
    """Ships log records to the Orchestrator (POST /logs) on a background thread so
    application logs from every process/machine are centralized. Best-effort: any
    failure is dropped and never breaks the app."""

    def __init__(self, sink_url: str, source: str):
        super().__init__()
        self._url = sink_url.rstrip("/") + "/logs"
        self._source = source
        self._queue = queue.Queue(maxsize=10000)
        threading.Thread(target=self._worker, daemon=True).start()

    def _record_to_dict(self, record: logging.LogRecord) -> dict:
        return {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "source": self._source,
            "logger": record.name,
            "message": self.format(record),
        }

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait(self._record_to_dict(record))
        except Exception:
            pass

    def _worker(self) -> None:
        while True:
            batch = []
            try:
                batch.append(self._queue.get(timeout=2.0))
            except queue.Empty:
                continue
            for _ in range(199):
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            self._post(batch)

    def _post(self, records) -> None:
        try:
            data = _json.dumps({"records": records}).encode("utf-8")
            req = urllib.request.Request(
                self._url, data=data, headers={"Content-Type": "application/json"}, method="POST"
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass


def setup_logger(name: str, log_file: str = None, level=logging.INFO):
    """Function to setup as many loggers as you want"""

    ensure_runtime_dirs()

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Prevent duplicate handlers if the logger is already initialized
    if logger.handlers:
        return logger

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler
    if log_file:
        file_path = LOG_DIR / log_file
        file_handler = RotatingFileHandler(
            file_path, maxBytes=10 * 1024 * 1024, backupCount=5
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Central log handler — ship WARN+ to the Orchestrator when configured.
    if _LOG_SINK_URL:
        try:
            central = _CentralLogHandler(_LOG_SINK_URL, _LOG_SOURCE)
            central.setLevel(_REMOTE_LEVEL)
            central.setFormatter(logging.Formatter('%(message)s'))
            logger.addHandler(central)
        except Exception:
            pass

    return logger


# Default logger for the project
logger = setup_logger("FlowMind", "flowmind.log")
