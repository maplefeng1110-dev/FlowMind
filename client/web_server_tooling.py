"""工具路由模块：负责插件发现、企业元数据、约束检查、路由决策与工具执行。"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import httpx
from dotenv import load_dotenv
from fastapi import HTTPException
from mcp import ClientSession
from mcp.client.sse import sse_client

from utils.internal_api import build_internal_api_headers
from utils.logger import setup_logger

load_dotenv()

logger = setup_logger("WebServer", "web_server.log")

REGISTRY_API_URL = os.getenv("REGISTRY_API_URL", "http://127.0.0.1:8000")
MCP_SSE_URL = f"{REGISTRY_API_URL}/mcp/sse"
MAX_REQUIRED_TOOL_RETRIES = 2
MAX_TOOL_CALL_STEPS = max(1, int(os.getenv("FLOWMIND_MAX_TOOL_CALL_STEPS", "6")))
DEFAULT_TOOL_RETRY_BUDGET = max(0, int(os.getenv("FLOWMIND_TOOL_RETRY_BUDGET", "1")))
CONFIRMATION_TERMS = (
    "确认",
    "同意",
    "批准",
    "请执行",
    "立即执行",
    "发送",
    "发出",
    "提交",
    "写入",
    "覆盖",
    "删除",
    "send",
)
PATH_ARG_SUFFIXES = ("path", "paths", "file", "files")
PATH_ARG_NAMES = {
    "invoice_path",
    "document_path",
    "file_path",
    "output_path",
    "source_file",
}


# MCP 与 Registry 数据获取
async def _fetch_available_tools(session: ClientSession) -> List[Dict[str, Any]]:
    """从 MCP 会话中读取当前暴露的工具列表。"""

    tools_response = await session.list_tools()
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema,
            },
        }
        for tool in tools_response.tools
    ]


async def _fetch_registered_rpas_from_session(
    session: ClientSession,
    available_only: bool = True,
) -> List[Dict[str, Any]]:
    """通过 MCP 的 list_rpas 工具获取插件清单，可选择只看当前可执行工具。"""

    result = await session.call_tool("list_rpas", {"available_only": available_only})
    result_text = "\n".join(entry.text for entry in result.content if hasattr(entry, "text"))
    rpas = json.loads(result_text) if result_text else []
    return rpas if isinstance(rpas, list) else []


async def _fetch_registered_rpas(available_only: bool = True) -> List[Dict[str, Any]]:
    """独立建立 MCP 会话并返回插件列表，可选择只看当前可执行工具。"""

    async with sse_client(MCP_SSE_URL, headers=build_internal_api_headers()) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            return await _fetch_registered_rpas_from_session(session, available_only=available_only)


def _filter_registered_rpas_for_allowed_ids(
    registered_rpas: List[Dict[str, Any]],
    allowed_rpa_ids: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """按账号授权过滤插件清单；管理员传 None 表示不过滤。"""

    valid_items = [rpa for rpa in registered_rpas or [] if isinstance(rpa, dict) and rpa.get("id")]
    if allowed_rpa_ids is None:
        return valid_items
    return [rpa for rpa in valid_items if rpa["id"] in allowed_rpa_ids]


def _filter_model_visible_tools(
    available_tools: List[Dict[str, Any]],
    registered_rpas: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """聊天模型只看到当前账号可用的真实业务插件，避免绕过权限调用底层分发工具。"""

    allowed_tool_ids = {
        rpa["id"]
        for rpa in registered_rpas or []
        if isinstance(rpa, dict) and rpa.get("id")
    }
    filtered_tools: List[Dict[str, Any]] = []
    for tool in available_tools or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        tool_name = function.get("name") if isinstance(function, dict) else None
        if tool_name in allowed_tool_ids:
            filtered_tools.append(tool)
    return filtered_tools


# 工具描述增强与匹配辅助
def _normalize_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _get_required_param_names(rpa: Dict[str, Any]) -> List[str]:
    params_schema = rpa.get("params_schema") if isinstance(rpa.get("params_schema"), dict) else {}
    required = params_schema.get("required")
    return [str(item).strip() for item in required if str(item or "").strip()] if isinstance(required, list) else []


def _schema_supports_batch(params_schema: Dict[str, Any]) -> bool:
    properties = params_schema.get("properties") if isinstance(params_schema, dict) else {}
    if not isinstance(properties, dict):
        return False
    for field_name, field_schema in properties.items():
        if not isinstance(field_schema, dict):
            continue
        if field_schema.get("type") == "array":
            return True
        if str(field_name).lower() in {"rows", "urls", "files", "items"}:
            return True
    return False


def _default_tool_outputs(rpa: Dict[str, Any]) -> List[str]:
    returns_schema = rpa.get("returns_schema") if isinstance(rpa.get("returns_schema"), dict) else {}
    properties = returns_schema.get("properties") if isinstance(returns_schema.get("properties"), dict) else {}
    outputs: List[str] = []
    for field_name, field_schema in properties.items():
        if not isinstance(field_schema, dict):
            continue
        description = str(field_schema.get("description") or "").strip()
        outputs.append(description or f"{field_name} 字段")
    return outputs or ["结构化执行结果"]


def _default_tool_failures(rpa: Dict[str, Any]) -> List[str]:
    failures = ["缺少关键参数", "输入文件不存在或格式不受支持"]
    if _schema_supports_batch(rpa.get("params_schema") if isinstance(rpa.get("params_schema"), dict) else {}):
        failures.append("批量输入数据格式不符合 schema")
    if str(rpa.get("id")) == "send_email":
        failures.append("SMTP 配置缺失或收件地址非法")
    return failures


def _normalize_tool_profile(rpa: Dict[str, Any]) -> Dict[str, Any]:
    raw_profile = rpa.get("tool_profile") if isinstance(rpa.get("tool_profile"), dict) else {}
    params_schema = rpa.get("params_schema") if isinstance(rpa.get("params_schema"), dict) else {}
    description = str(rpa.get("description") or "").strip()
    required_params = _get_required_param_names(rpa)
    suitable_inputs = _normalize_string_list(raw_profile.get("suitable_inputs"))
    if not suitable_inputs and required_params:
        suitable_inputs = [f"需要参数：{', '.join(required_params)}"]

    solves = _normalize_string_list(raw_profile.get("solves")) or ([description] if description else [str(rpa.get("id"))])
    outputs = _normalize_string_list(raw_profile.get("outputs")) or _default_tool_outputs(rpa)
    prerequisites = _normalize_string_list(raw_profile.get("prerequisites"))
    common_failures = _normalize_string_list(raw_profile.get("common_failure_reasons")) or _default_tool_failures(rpa)
    allowed_roots = _normalize_string_list(raw_profile.get("allowed_path_roots"))

    raw_retries = raw_profile.get("max_retries")
    try:
        max_retries = max(0, int(raw_retries))
    except (TypeError, ValueError):
        max_retries = DEFAULT_TOOL_RETRY_BUDGET

    return {
        "solves": solves,
        "suitable_inputs": suitable_inputs,
        "outputs": outputs,
        "prerequisites": prerequisites,
        "common_failure_reasons": common_failures,
        "supports_batch": bool(raw_profile.get("supports_batch", _schema_supports_batch(params_schema))),
        "has_side_effects": bool(raw_profile.get("has_side_effects", False)),
        "requires_confirmation": bool(raw_profile.get("requires_confirmation", False)),
        "allowed_path_roots": allowed_roots,
        "max_retries": max_retries,
        "confirmation_hint": str(raw_profile.get("confirmation_hint") or "").strip(),
    }


def _build_tool_capability_map(rpa: Dict[str, Any]) -> Dict[str, Any]:
    profile = _normalize_tool_profile(rpa)
    return {
        "solves": profile["solves"],
        "suitable_inputs": profile["suitable_inputs"],
        "outputs": profile["outputs"],
        "prerequisites": profile["prerequisites"],
        "common_failure_reasons": profile["common_failure_reasons"],
        "supports_batch": profile["supports_batch"],
        "has_side_effects": profile["has_side_effects"],
        "requires_confirmation": profile["requires_confirmation"],
        "allowed_path_roots": profile["allowed_path_roots"],
        "max_retries": profile["max_retries"],
    }


def _build_augmented_tool_description(tool: Dict[str, Any], rpa: Dict[str, Any]) -> str:
    """把 manifest 中的触发场景和强约束补进工具描述，帮助模型更稳地选工具。"""

    function = tool.get("function") if isinstance(tool, dict) else None
    base_description = ""
    if isinstance(function, dict):
        base_description = str(function.get("description") or "").strip()
    if not base_description:
        base_description = str(rpa.get("description") or "").strip()

    enforcement = rpa.get("enforcement") if isinstance(rpa.get("enforcement"), dict) else {}
    trigger_terms = enforcement.get("intent_keywords")
    if not isinstance(trigger_terms, list) or not trigger_terms:
        trigger_terms = rpa.get("capabilities") if isinstance(rpa.get("capabilities"), list) else []

    profile = _normalize_tool_profile(rpa)

    description_parts = [base_description] if base_description else []
    if trigger_terms:
        joined_terms = "、".join(str(item) for item in trigger_terms[:8] if item)
        if joined_terms:
            description_parts.append(f"触发场景：{joined_terms}")
    if profile["solves"]:
        description_parts.append(f"解决问题：{'；'.join(profile['solves'][:2])}")
    if profile["suitable_inputs"]:
        description_parts.append(f"适合输入：{'；'.join(profile['suitable_inputs'][:2])}")
    if profile["outputs"]:
        description_parts.append(f"输出结果：{'；'.join(profile['outputs'][:2])}")
    if profile["prerequisites"]:
        description_parts.append(f"前置条件：{'；'.join(profile['prerequisites'][:2])}")
    if profile["common_failure_reasons"]:
        description_parts.append(f"常见失败：{'；'.join(profile['common_failure_reasons'][:2])}")
    description_parts.append(f"批量处理：{'支持' if profile['supports_batch'] else '未声明或单次执行'}")
    if profile["has_side_effects"]:
        description_parts.append("副作用：会写入文件、发送外部请求或修改业务系统。")
    if profile["requires_confirmation"]:
        hint = profile["confirmation_hint"] or "执行前需要用户明确确认。"
        description_parts.append(f"确认要求：{hint}")
    if enforcement.get("must_call_when_matched"):
        description_parts.append("重要：命中上述场景时，必须先调用此工具，不能直接声称任务已完成。")
    return "\n".join(part for part in description_parts if part)


def _merge_registered_rpa_metadata(
    available_tools: List[Dict[str, Any]],
    registered_rpas: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """把 Registry 里的插件元数据合并进工具列表，统一模型可见描述。"""

    rpa_by_id = {
        rpa["id"]: rpa
        for rpa in registered_rpas or []
        if isinstance(rpa, dict) and rpa.get("id")
    }
    merged_tools: List[Dict[str, Any]] = []
    for tool in available_tools or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        tool_name = function.get("name") if isinstance(function, dict) else None
        rpa = rpa_by_id.get(tool_name)
        if not function or not rpa:
            merged_tools.append(tool)
            continue

        merged_tools.append(
            {
                **tool,
                "function": {
                    **function,
                    "description": _build_augmented_tool_description(tool, rpa),
                },
            }
        )
    return merged_tools


def _extract_latest_user_text(messages: List[Dict[str, Any]]) -> str:
    for message in reversed(messages or []):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _extract_user_path_hints(messages: List[Dict[str, Any]]) -> Set[str]:
    content = "\n".join(str(message.get("content") or "") for message in messages or [])
    hints: Set[str] = set()
    for match in re.finditer(r"Path:\s*([^\]\n]+)", content):
        candidate = str(match.group(1) or "").strip()
        if candidate:
            hints.add(candidate)
    for match in re.finditer(r"(?<!https:)(?<!http:)(?:[A-Za-z]:\\[^\s\"']+|(?:\.{0,2}/|/)[^\s\"']+)", content):
        candidate = str(match.group(0) or "").strip()
        if candidate:
            hints.add(candidate)
    return hints


def _resolve_runtime_path(path_text: str) -> Path:
    candidate = Path(str(path_text or "").strip())
    if candidate.is_absolute():
        return candidate.resolve(strict=False)
    return (Path.cwd() / candidate).resolve(strict=False)


def _is_path_within_allowed_roots(path_text: str, allowed_roots: List[str]) -> bool:
    resolved_path = _resolve_runtime_path(path_text)
    for root in allowed_roots:
        normalized_root = str(root or "").strip()
        if not normalized_root:
            continue
        resolved_root = _resolve_runtime_path(normalized_root)
        try:
            resolved_path.relative_to(resolved_root)
            return True
        except ValueError:
            continue
    return False


def _collect_path_like_values(payload: Any, field_name: str = "") -> List[str]:
    results: List[str] = []
    normalized_field_name = str(field_name or "").lower()
    if isinstance(payload, dict):
        for key, value in payload.items():
            results.extend(_collect_path_like_values(value, str(key)))
        return results
    if isinstance(payload, list):
        for item in payload:
            results.extend(_collect_path_like_values(item, field_name))
        return results
    if not isinstance(payload, str):
        return results
    candidate = payload.strip()
    if not candidate or candidate.startswith(("http://", "https://")):
        return results
    looks_like_filesystem_path = (
        candidate.startswith(("/", "./", "../"))
        or re.match(r"^[A-Za-z]:\\", candidate) is not None
        or candidate.startswith("data/")
        or "\\" in candidate
    )
    if (
        normalized_field_name in PATH_ARG_NAMES
        or normalized_field_name.endswith(PATH_ARG_SUFFIXES)
        or looks_like_filesystem_path
    ):
        results.append(candidate)
    return results


def _has_explicit_confirmation(latest_user_text: str, rpa: Optional[Dict[str, Any]] = None) -> bool:
    normalized = _normalize_match_text(latest_user_text)
    if not normalized:
        return False
    if any(term in normalized for term in CONFIRMATION_TERMS):
        return True
    if isinstance(rpa, dict):
        for term in _build_required_tool_terms(rpa):
            if term and term in normalized:
                return True
    return False


def _evaluate_tool_constraints(
    tool_name: str,
    tool_args: Dict[str, Any],
    rpa: Optional[Dict[str, Any]],
    messages: List[Dict[str, Any]],
    required_tool_ids: Set[str],
    executed_tool_steps: int,
) -> Dict[str, Any]:
    if executed_tool_steps >= MAX_TOOL_CALL_STEPS:
        return {
            "allowed": False,
            "constraints": [
                {
                    "type": "step_limit",
                    "tool_name": tool_name,
                    "status": "blocked",
                    "message": f"本轮最多只允许执行 {MAX_TOOL_CALL_STEPS} 个工具步骤，请拆分请求后重试。",
                    "code": "step_limit_exceeded",
                }
            ],
            "blocked_message": f"已达到本轮工具步骤上限（{MAX_TOOL_CALL_STEPS}）。请拆分需求后再执行。",
        }

    if not isinstance(rpa, dict):
        return {"allowed": True, "constraints": [], "blocked_message": ""}

    constraints: List[Dict[str, Any]] = []
    profile = _normalize_tool_profile(rpa)
    latest_user_text = _extract_latest_user_text(messages)

    missing_required = [
        param_name
        for param_name in _get_required_param_names(rpa)
        if tool_args.get(param_name) in (None, "", [], {})
    ]
    if missing_required:
        constraints.append(
            {
                "type": "parameter",
                "tool_name": tool_name,
                "status": "blocked",
                "message": f"缺少关键参数：{', '.join(missing_required)}",
                "code": "missing_required_params",
                "details": {"missing": missing_required},
            }
        )

    if profile["has_side_effects"]:
        constraints.append(
            {
                "type": "risk",
                "tool_name": tool_name,
                "status": "warning",
                "message": "该工具会写入文件、发出通知或修改外部系统，请确认范围后执行。",
                "code": "side_effect_warning",
            }
        )

    confirmation_satisfied = tool_name in required_tool_ids or _has_explicit_confirmation(latest_user_text, rpa)
    if profile["requires_confirmation"] and not confirmation_satisfied:
        constraints.append(
            {
                "type": "confirmation",
                "tool_name": tool_name,
                "status": "blocked",
                "message": profile["confirmation_hint"] or "该工具执行前需要用户明确确认。",
                "code": "confirmation_required",
            }
        )

    path_values = _collect_path_like_values(tool_args)
    allowed_roots = profile["allowed_path_roots"]
    referenced_paths = _extract_user_path_hints(messages)
    out_of_scope_paths = []
    if allowed_roots:
        for path_value in path_values:
            if path_value in referenced_paths:
                continue
            if not _is_path_within_allowed_roots(path_value, allowed_roots):
                out_of_scope_paths.append(path_value)
    if out_of_scope_paths:
        constraints.append(
            {
                "type": "range",
                "tool_name": tool_name,
                "status": "blocked",
                "message": "发现超出授权范围的文件路径，请使用上传文件或授权目录中的路径。",
                "code": "path_out_of_scope",
                "details": {
                    "paths": out_of_scope_paths,
                    "allowed_roots": allowed_roots,
                },
            }
        )

    blocked = next((item for item in constraints if item["status"] == "blocked"), None)
    return {
        "allowed": blocked is None,
        "constraints": constraints,
        "blocked_message": blocked["message"] if blocked else "",
    }


def _collect_async_tool_ids(rpas: List[Dict[str, Any]]) -> Set[str]:
    """收集所有走后台任务链路的工具 ID。"""

    return {
        rpa["id"]
        for rpa in rpas
        if isinstance(rpa, dict) and rpa.get("id")
    }


def _normalize_match_text(value: Any) -> str:
    """把文本压平后再做关键词匹配，减少大小写和空格差异带来的噪音。"""

    text = str(value or "").lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_available_tool_ids(available_tools: List[Dict[str, Any]]) -> Set[str]:
    """从工具列表里提取工具名集合，便于快速判断必需工具是否在线。"""

    tool_ids: Set[str] = set()
    for tool in available_tools or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(function, dict) and function.get("name"):
            tool_ids.add(function["name"])
    return tool_ids


def _build_required_tool_terms(rpa: Dict[str, Any]) -> List[str]:
    """从 manifest 的 enforcement/capabilities/tags 中提取匹配词。"""

    enforcement = rpa.get("enforcement") if isinstance(rpa.get("enforcement"), dict) else {}
    terms: List[str] = []
    for key in ("intent_keywords", "capabilities"):
        values = enforcement.get(key)
        if isinstance(values, list):
            terms.extend(str(item) for item in values if item)
    if not terms:
        fallback_terms = rpa.get("capabilities")
        if isinstance(fallback_terms, list):
            terms.extend(str(item) for item in fallback_terms if item)
    if not terms and enforcement.get("match_tags", False):
        tags = rpa.get("tags")
        if isinstance(tags, list):
            terms.extend(str(item) for item in tags if item)
    return [_normalize_match_text(term) for term in terms if _normalize_match_text(term)]


# 半强制路由决策
def _get_required_rpas_for_messages(messages: List[Dict[str, Any]], registered_rpas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """根据最新一条用户消息，动态找出当前这轮必须先调用的插件。"""

    latest_user_text = ""
    for message in reversed(messages or []):
        if message.get("role") == "user":
            latest_user_text = _normalize_match_text(message.get("content", ""))
            break

    if not latest_user_text:
        return []

    required_rpas: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    for rpa in registered_rpas or []:
        if not isinstance(rpa, dict) or not rpa.get("id"):
            continue
        enforcement = rpa.get("enforcement") if isinstance(rpa.get("enforcement"), dict) else {}
        if not enforcement.get("must_call_when_matched"):
            continue

        terms = _build_required_tool_terms(rpa)
        if not terms:
            continue

        if any(term in latest_user_text for term in terms):
            if rpa["id"] not in seen_ids:
                required_rpas.append(rpa)
                seen_ids.add(rpa["id"])

    return required_rpas


def _build_required_tool_prompt(base_prompt: str, required_rpas: List[Dict[str, Any]]) -> str:
    """把“这轮必须真实调用工具”的高优先级规则注入 system prompt。"""

    if not required_rpas:
        return base_prompt

    lines = [
        "",
        "For this specific request, real tool execution is mandatory before you give a final answer.",
        "Handle required tools in stages. When one required tool is selected for the current turn, call it before replying:",
    ]
    for rpa in required_rpas:
        lines.append(f"- {rpa['id']}: {rpa.get('description', 'No description')}")
    lines.append("If a required tool is unavailable, explicitly say the tool is unavailable. Do not pretend the task already succeeded.")
    return base_prompt + "\n".join(lines)


def _order_required_rpas(required_rpas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """给必需工具排序，优先执行业务工具，最后再执行 send_email。"""

    ordered = [rpa for rpa in required_rpas if isinstance(rpa, dict) and rpa.get("id")]
    return sorted(ordered, key=lambda rpa: (1 if rpa["id"] == "send_email" else 0))


def _filter_tools_by_name(available_tools: List[Dict[str, Any]], allowed_tool_ids: Set[str]) -> List[Dict[str, Any]]:
    """只保留当前阶段允许模型看到的工具。"""

    filtered_tools: List[Dict[str, Any]] = []
    for tool in available_tools or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        tool_name = function.get("name") if isinstance(function, dict) else None
        if tool_name in allowed_tool_ids:
            filtered_tools.append(tool)
    return filtered_tools


def _build_stage_tool_prompt(base_prompt: str, stage_rpa: Optional[Dict[str, Any]]) -> str:
    """告诉模型当前阶段只能先完成哪个工具调用。"""

    if not stage_rpa:
        return base_prompt
    return (
        f"{base_prompt}\n\n"
        f"For this turn, call `{stage_rpa['id']}` before you respond to the user.\n"
        "Use the tool schema to infer arguments from the conversation context.\n"
        "After the tool returns, either explain the result or continue to the next required tool."
    )


def _build_required_tool_retry_instruction(
    missing_tool_ids: Set[str],
    stage_tool_id: Optional[str] = None,
) -> Dict[str, str]:
    """当模型漏调必需工具时，追加一条系统级重试提醒。"""

    missing = "、".join(sorted(missing_tool_ids))
    if stage_tool_id:
        content = (
            f"你还没有真正调用当前这一步必须先执行的工具：{stage_tool_id}。"
            "请先调用它，再继续回答。"
            "参数要根据当前对话和工具 schema 自行补全，不能跳过执行步骤。"
        )
    else:
        content = (
            f"你还没有调用这次请求所必需的工具：{missing}。"
            "请先调用这些工具，再给最终回答。"
            "如果工具当前不可用，请直接说明不可用，不能假装已经成功完成。"
        )
    return {"role": "system", "content": content}


def _build_required_tool_block_message(
    missing_tool_ids: Set[str],
    unavailable_tool_ids: Optional[Set[str]] = None,
) -> str:
    """在必需工具最终仍未被调用时，返回 fail-closed 的阻断文案。"""

    missing = "、".join(sorted(missing_tool_ids))
    if unavailable_tool_ids:
        unavailable = "、".join(sorted(unavailable_tool_ids))
        return (
            f"当前请求需要先调用这些自动化工具：{missing}。\n\n"
            f"但当前可用工具列表里缺少：{unavailable}。\n"
            "为避免给出不准确的结果，这次不会直接生成“已完成”的结论。"
        )

    return (
        f"当前请求需要先调用这些自动化工具：{missing}。\n\n"
        "模型本轮没有实际调用所需工具，所以这次不会直接输出“已完成”的结果。"
        "请重试，或检查工具注册与连接状态。"
    )


def _should_pause_required_tool_chain(required_tool_statuses: Dict[str, str]) -> bool:
    """只要当前链路里出现 pending/error，就暂停继续强制后续工具。"""

    return any(status in {"pending", "error"} for status in required_tool_statuses.values())


def _get_next_required_tool_id(required_rpas: List[Dict[str, Any]], required_tool_statuses: Dict[str, str]) -> Optional[str]:
    """找出下一步应该强制调用的必需工具。"""

    if _should_pause_required_tool_chain(required_tool_statuses):
        return None
    for rpa in _order_required_rpas(required_rpas):
        if rpa["id"] not in required_tool_statuses:
            return rpa["id"]
    return None


def _get_missing_required_tool_ids(required_rpas: List[Dict[str, Any]], required_tool_statuses: Dict[str, str]) -> Set[str]:
    """计算当前还没完成的必需工具集合。"""

    if _should_pause_required_tool_chain(required_tool_statuses):
        return set()
    return {
        rpa["id"]
        for rpa in _order_required_rpas(required_rpas)
        if rpa["id"] not in required_tool_statuses
    }


def _build_tool_choice(tool_name: Optional[str]) -> Optional[Dict[str, Any]]:
    """构造 OpenAI-compatible 的强制工具选择参数。"""

    if not tool_name:
        return None
    return {"type": "function", "function": {"name": tool_name}}


def _prepare_tool_routing(
    available_tools: List[Dict[str, Any]],
    required_rpas: List[Dict[str, Any]],
    required_tool_statuses: Dict[str, str],
    base_system_prompt: str,
) -> tuple[str, Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]], Optional[str]]:
    """按当前执行进度决定这轮开放哪些工具，以及是否强制 tool_choice。"""

    if _should_pause_required_tool_chain(required_tool_statuses):
        return base_system_prompt, None, None, None

    stage_tool_id = _get_next_required_tool_id(required_rpas, required_tool_statuses)
    if not stage_tool_id:
        return base_system_prompt, available_tools if available_tools else None, None, None

    stage_rpa = next((rpa for rpa in required_rpas if rpa.get("id") == stage_tool_id), None)
    stage_tools = _filter_tools_by_name(available_tools, {stage_tool_id})
    return (
        _build_stage_tool_prompt(base_system_prompt, stage_rpa),
        stage_tools if stage_tools else None,
        _build_tool_choice(stage_tool_id),
        stage_tool_id,
    )


def _record_required_tool_status(required_tool_statuses: Dict[str, str], tool_name: str, tool_status: str) -> None:
    """记录必需工具当前状态，供下一轮路由使用。"""

    if tool_status == "cached" and tool_name in required_tool_statuses:
        return
    required_tool_statuses[tool_name] = tool_status


def _summarize_tool_output(result_text: str) -> str:
    """把工具结果压成较短的预览文本，便于前端轨迹展示。"""

    if not result_text:
        return ""

    preview = result_text
    try:
        parsed = json.loads(result_text)
        preview = json.dumps(parsed, ensure_ascii=False)
    except Exception:
        preview = result_text

    if len(preview) > 280:
        return f"{preview[:280]}..."
    return preview


def _tool_call_signature(tool_name: str, tool_args: Dict[str, Any]) -> str:
    """把工具名和参数序列化成稳定签名，用来做重复调用去重。"""

    try:
        serialized_args = json.dumps(tool_args, ensure_ascii=False, sort_keys=True)
    except TypeError:
        serialized_args = json.dumps(tool_args, ensure_ascii=False, sort_keys=True, default=str)
    return f"{tool_name}:{serialized_args}"


# Registry 代理与工具执行
async def _proxy_json(
    method: str,
    path: str,
    json_body: Dict[str, Any] | None = None,
    *,
    params: Dict[str, Any] | None = None,
    session_token: Optional[str] = None,
) -> Any:
    """把 Web 层请求转发到 Registry 的 HTTP API。"""

    async with httpx.AsyncClient(headers=build_internal_api_headers(session_token=session_token)) as client:
        try:
            response = await client.request(method, f"{REGISTRY_API_URL}{path}", json=json_body, params=params)
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Failed to reach registry: {exc}") from exc

        if response.is_error:
            try:
                payload = response.json()
                detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
            except ValueError:
                detail = response.text or response.reason_phrase
            raise HTTPException(status_code=response.status_code, detail=detail or "Registry request failed")

        try:
            return response.json()
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="Registry returned a non-JSON response") from exc


async def _dispatch_rpa_async(
    rpa_id: str,
    params: Dict[str, Any],
    machine_id: Optional[str] = None,
    conversation_context: Optional[Dict[str, str]] = None,
    session_token: Optional[str] = None,
) -> Dict[str, Any]:
    """把工具调用转成后台任务派发到 Registry。"""

    request_body: Dict[str, Any] = {"rpa_id": rpa_id, "params": params}
    if machine_id:
        request_body["machine_id"] = machine_id
    if conversation_context:
        request_body.update(conversation_context)
    return await _proxy_json("POST", "/dispatch/async", json_body=request_body, session_token=session_token)


async def _execute_tool_with_dedupe(
    session: ClientSession,
    tool_name: str,
    tool_args: Dict[str, Any],
    tool_cache: Dict[str, Dict[str, Any]],
) -> tuple[str, str, Optional[str]]:
    """执行同步工具，并在同轮会话里复用重复调用结果。"""

    signature = _tool_call_signature(tool_name, tool_args)
    cached = tool_cache.get(signature)
    if cached:
        logger.info("Skipping duplicate tool call and reusing cached result: %s", signature)
        return cached["result_text"], "cached", cached.get("task_id")

    try:
        result = await session.call_tool(tool_name, tool_args)
        result_text = "\n".join(entry.text for entry in result.content if hasattr(entry, "text"))
        status = "success"
    except Exception as exc:
        result_text = f"Error: {exc}"
        status = "error"

    tool_cache[signature] = {"result_text": result_text, "status": status, "task_id": None}
    return result_text, status, None


async def _execute_tool_call(
    session: ClientSession,
    tool_name: str,
    tool_args: Dict[str, Any],
    tool_cache: Dict[str, Dict[str, Any]],
    async_tool_ids: Set[str],
    conversation_context: Optional[Dict[str, str]] = None,
    session_token: Optional[str] = None,
) -> tuple[str, str, Optional[str]]:
    """统一执行工具调用：后台任务走 async，其他工具走同步执行。"""

    signature = _tool_call_signature(tool_name, tool_args)
    cached = tool_cache.get(signature)
    if cached:
        logger.info("Skipping duplicate tool call and reusing cached result: %s", signature)
        return cached["result_text"], "cached", cached.get("task_id")

    if tool_name in async_tool_ids:
        try:
            accepted = await _dispatch_rpa_async(
                tool_name,
                tool_args,
                conversation_context=conversation_context,
                session_token=session_token,
            )
            task_id = accepted.get("task_id")
            result_payload = {
                "status": "pending",
                "task_id": task_id,
                "message": "Task submitted for background execution",
            }
            result_text = json.dumps(result_payload, ensure_ascii=False)
            tool_cache[signature] = {"result_text": result_text, "status": "pending", "task_id": task_id}
            return result_text, "pending", task_id
        except Exception as exc:
            result_text = f"Error: {exc}"
            tool_cache[signature] = {"result_text": result_text, "status": "error", "task_id": None}
            return result_text, "error", None

    return await _execute_tool_with_dedupe(session, tool_name, tool_args, tool_cache)
