"""DSL schema + parsing for the rpa_flow execution engine.

Defines the YAML/JSON flow format (``Flow`` / ``Step`` / ``FlowContext``) with
Pydantic, plus helpers to load a flow from a file or inline dict and to
substitute ``${env.X}`` / ``${vars.X}`` placeholders at execution time.

This module has no browser dependency, so it can be imported and unit-tested
without a browser or the extension bridge.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

# Basic operations run purely against the browser driver.
BASIC_ACTIONS = {"goto", "click", "type", "scroll", "extract_text", "wait", "save_db"}
# AI-native operations route through the AI gateway (visual self-healing / extraction).
AI_ACTIONS = {"ai_click", "ai_extract"}
SUPPORTED_ACTIONS = BASIC_ACTIONS | AI_ACTIONS


class Step(BaseModel):
    """A single action in a flow. Unknown keys are allowed so authors (and LLMs)
    can attach extra metadata without breaking validation."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    action: str
    # locator-based actions (click / type / extract_text)
    selector: Optional[str] = None
    # goto
    url: Optional[str] = None
    # type
    text: Optional[str] = None
    # scroll
    direction: str = "down"
    amount: int = 600
    # extract_text / ai_extract: field name -> CSS selector (extract_text) or description (ai_extract)
    fields: Optional[Dict[str, str]] = None
    schema_ref: Optional[str] = None
    # ai_click: natural-language description of the target
    intent: Optional[str] = None
    # variable plumbing
    save_as: Optional[str] = None
    # save_db
    table: Optional[str] = None
    source: Optional[str] = Field(default=None, alias="from")
    # control
    timeout_ms: int = 15000
    optional: bool = False  # failure does not abort the flow
    heal: bool = True  # allow ai_click fallback when a selector fails

    def describe(self) -> str:
        target = self.selector or self.url or self.intent or self.table or ""
        return f"{self.action}({target})".strip()


class FlowContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    incognito: bool = True
    headless: bool = True
    viewport: Dict[str, int] = Field(default_factory=lambda: {"width": 1280, "height": 800})
    base_url: Optional[str] = None


class Flow(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = "unnamed_flow"
    context: FlowContext = Field(default_factory=FlowContext)
    steps: List[Step] = Field(default_factory=list)


def load_flow(flow_path: Optional[str] = None, flow: Optional[dict] = None) -> Flow:
    """Build a validated ``Flow`` from a YAML/JSON file path or an inline dict."""
    if flow is not None:
        data = flow
    elif flow_path:
        data = yaml.safe_load(Path(flow_path).read_text(encoding="utf-8")) or {}
    else:
        raise ValueError("Either flow_path or flow must be provided")
    if not isinstance(data, dict):
        raise ValueError("Flow definition must be a mapping")
    return Flow.model_validate(data)


_PLACEHOLDER = re.compile(r"\$\{([a-zA-Z0-9_]+)\.([a-zA-Z0-9_.]+)\}")


def substitute(value: Any, env: Dict[str, Any], variables: Dict[str, Any]) -> Any:
    """Recursively replace ``${env.X}`` / ``${vars.X}`` placeholders in strings."""
    if isinstance(value, str):
        def _repl(match: "re.Match[str]") -> str:
            scope, key = match.group(1), match.group(2)
            if scope == "env":
                source: Dict[str, Any] = env
            elif scope in {"vars", "var"}:
                source = variables
            else:
                return match.group(0)
            return str(_dig(source, key))

        return _PLACEHOLDER.sub(_repl, value)
    if isinstance(value, list):
        return [substitute(item, env, variables) for item in value]
    if isinstance(value, dict):
        return {key: substitute(item, env, variables) for key, item in value.items()}
    return value


def _dig(source: Dict[str, Any], dotted: str) -> Any:
    current: Any = source
    for part in dotted.split("."):
        if isinstance(current, dict):
            current = current.get(part, "")
        else:
            return ""
    return current
