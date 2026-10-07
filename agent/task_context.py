"""The FlowMind task a plugin is currently running for.

Set by LocalExecutor around each plugin call so plugins can tag artifacts with the
task id (e.g. failure snapshots) without changing their ``run(**params)`` signature.
"""
from contextvars import ContextVar
from typing import Optional

_current_task_id: ContextVar[Optional[str]] = ContextVar("flowmind_current_task_id", default=None)


def current_task_id() -> Optional[str]:
    return _current_task_id.get()
