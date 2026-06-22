"""聊天编排模块：负责半强制路由下的流式/非流式对话循环。"""

import json
import shlex
from typing import Any, AsyncIterator, Dict, List, Optional

from mcp import ClientSession
from mcp.client.sse import sse_client

from client.ai_manager import ai_manager
from client.orchestration_runtime import (
    add_constraints,
    build_initial_runtime_plan,
    build_reusable_task_suggestion,
    finalize_runtime_plan,
    register_tool_result,
    register_tool_start,
)
from client.web_server_tooling import (
    MCP_SSE_URL,
    MAX_REQUIRED_TOOL_RETRIES,
    _build_required_tool_block_message,
    _build_required_tool_prompt,
    _collect_async_tool_ids,
    _execute_tool_call,
    _extract_available_tool_ids,
    _fetch_available_tools,
    _fetch_registered_rpas_from_session,
    _filter_model_visible_tools,
    _filter_registered_rpas_for_allowed_ids,
    _get_missing_required_tool_ids,
    _get_required_rpas_for_messages,
    _merge_registered_rpa_metadata,
    _evaluate_tool_constraints,
    _prepare_tool_routing,
    _record_required_tool_status,
    _summarize_tool_output,
    _build_required_tool_retry_instruction,
    logger,
)
from utils.internal_api import build_internal_api_headers


def _parse_tool_arguments(raw_arguments: str) -> Dict[str, Any]:
    """把模型传回的工具参数安全解析成字典，失败时回退为空对象。"""

    try:
        parsed = json.loads(raw_arguments)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ==================== slash 命令：命令式直达插件 ====================
def _last_user_text(messages: List[Dict[str, Any]]) -> str:
    for message in reversed(messages or []):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        return part["text"].strip()
    return ""


def _freeform_to_params(text: str, rpa_meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """自由文本映射到该插件 params_schema 的首个必填(或首个)参数。"""
    schema = (rpa_meta or {}).get("params_schema") or {}
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    key = required[0] if required else next(iter(properties), None)
    return {key: text} if key else {"input": text}


def _parse_slash_command(text: str, rpa_by_id: Dict[str, Any]) -> Optional[tuple]:
    """解析 `/<rpa_id> 参数`：参数支持 JSON、key=value、或自由文本。"""
    if not text.startswith("/"):
        return None
    body = text[1:].strip()
    if not body:
        return ("", {})
    head, _, rest = body.partition(" ")
    rpa_id = head.strip()
    rest = rest.strip()
    params: Dict[str, Any] = {}
    if rest:
        if rest.startswith("{"):
            try:
                loaded = json.loads(rest)
                params = loaded if isinstance(loaded, dict) else {}
            except Exception:
                params = {}
        elif "=" in rest:
            try:
                tokens = shlex.split(rest)
            except Exception:
                tokens = rest.split()
            for token in tokens:
                if "=" in token:
                    key, value = token.split("=", 1)
                    params[key.strip()] = value.strip()
            if not params:
                params = _freeform_to_params(rest, rpa_by_id.get(rpa_id))
        else:
            params = _freeform_to_params(rest, rpa_by_id.get(rpa_id))
    return (rpa_id, params)


def _slash_reply(text: str, provider: str, emit_tool_events: bool, tool_trace=None):
    if emit_tool_events:
        async def _gen() -> AsyncIterator[str]:
            yield f"data: {json.dumps({'content': text}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return _gen()
    return {"response": text, "provider": provider, "tool_trace": tool_trace or []}


async def _maybe_run_slash_command(
    messages: List[Dict[str, Any]],
    provider: str,
    allowed_rpa_ids: Optional[set],
    session_token: Optional[str],
    emit_tool_events: bool,
):
    """`/<插件id> 参数` 绕过 AI 直接执行指定插件；非 slash 消息返回 None。"""
    text = _last_user_text(messages)
    if not text.startswith("/"):
        return None

    async with sse_client(MCP_SSE_URL, headers=build_internal_api_headers(session_token=session_token)) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            registered = _filter_registered_rpas_for_allowed_ids(
                await _fetch_registered_rpas_from_session(session, available_only=False),
                allowed_rpa_ids,
            )
            rpa_by_id = {
                str(rpa["id"]): rpa
                for rpa in (registered or [])
                if isinstance(rpa, dict) and rpa.get("id")
            }
            available_ids = _extract_available_tool_ids(await _fetch_available_tools(session))
            async_ids = _collect_async_tool_ids(registered)

            rpa_id, params = _parse_slash_command(text, rpa_by_id)
            available_hint = ", ".join(sorted(rpa_by_id)) or "（无）"
            if not rpa_id:
                return _slash_reply(
                    "命令格式：/<插件id> 参数，例如 `/web_query query=FlowMind`。可用：" + available_hint,
                    provider, emit_tool_events,
                )
            if rpa_id not in rpa_by_id:
                return _slash_reply(f"未找到插件「{rpa_id}」。可用：" + available_hint, provider, emit_tool_events)
            if rpa_id not in available_ids:
                return _slash_reply(f"插件「{rpa_id}」当前离线（没有可用执行机）。", provider, emit_tool_events)

            result_text, status, task_id = await _execute_tool_call(
                session, rpa_id, params, {}, async_ids, None, session_token,
            )

    reply = f"⌘ 直接执行 `{rpa_id}` · {status}\n\n{result_text or '(无输出)'}"
    return _slash_reply(reply, provider, emit_tool_events, tool_trace=[{"name": rpa_id, "status": status, "task_id": task_id}])


def _serialize_tool_calls(tool_calls: List[Any]) -> List[Dict[str, Any]]:
    """把模型工具调用统一转换成 OpenAI-compatible 的消息结构。"""

    return [
        {
            "id": tool_call.id,
            "type": "function",
            "function": {
                "name": tool_call.function.name,
                "arguments": tool_call.function.arguments,
            },
        }
        for tool_call in tool_calls
    ]


def _upsert_non_stream_tool_trace(tool_trace: List[Dict[str, Any]], next_tool: Dict[str, Any]) -> List[Dict[str, Any]]:
    index = next(
        (
            item_index
            for item_index, item in enumerate(tool_trace)
            if item.get("id") == next_tool.get("id")
        ),
        -1,
    )
    if index >= 0:
        tool_trace[index] = {**tool_trace[index], **next_tool}
        return tool_trace
    tool_trace.append(next_tool)
    return tool_trace


def _build_system_prompt() -> str:
    """生成 FlowMind 的统一系统提示词。"""

    return (
        "You are FlowMind, an enterprise automation assistant.\n"
        "You cannot directly operate enterprise systems or pretend an automation already happened.\n"
        "When a request requires automation, you must use real tools before claiming success.\n"
        "If the current turn is narrowed to one required tool, call that tool before replying.\n"
        "If the user has uploaded files, their paths will appear in the conversation and should be used as tool arguments when relevant.\n"
        "If a tool returns status='pending' with a task_id, explain that the task is running in the background and the UI will update when it finishes.\n"
        "Do not call the same tool again with the same arguments unless the previous call failed or the arguments changed.\n"
        "Never say a task is completed if no tool was actually called."
    )


async def _run_chat_loop(
    messages: List[Dict[str, Any]],
    provider: str,
    conversation_context: Optional[Dict[str, str]] = None,
    allowed_rpa_ids: Optional[set[str]] = None,
    session_token: Optional[str] = None,
    emit_tool_events: bool = False,
) -> AsyncIterator[str] | Dict[str, Any]:
    """执行统一的 MCP 聊天循环；流式和非流式都复用这套路由逻辑。"""

    async with sse_client(MCP_SSE_URL, headers=build_internal_api_headers(session_token=session_token)) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            # 聊天路由需要看到“全部已注册 manifest”，这样工具离线时也能 fail-closed。
            registered_rpas = _filter_registered_rpas_for_allowed_ids(
                await _fetch_registered_rpas_from_session(session, available_only=False),
                allowed_rpa_ids,
            )
            available_tools = _merge_registered_rpa_metadata(
                await _fetch_available_tools(session),
                registered_rpas,
            )
            available_tools = _filter_model_visible_tools(available_tools, registered_rpas)
            async_tool_ids = _collect_async_tool_ids(registered_rpas)
            available_tool_ids = _extract_available_tool_ids(available_tools)
            required_rpas = _get_required_rpas_for_messages(messages, registered_rpas)
            required_tool_ids = {rpa["id"] for rpa in required_rpas}
            rpa_by_id = {
                str(rpa["id"]): rpa
                for rpa in registered_rpas or []
                if isinstance(rpa, dict) and rpa.get("id")
            }
            unavailable_required_tool_ids = required_tool_ids - available_tool_ids
            base_system_prompt = _build_required_tool_prompt(_build_system_prompt(), required_rpas)
            tool_cache: Dict[str, Dict[str, Any]] = {}
            required_tool_statuses: Dict[str, str] = {}
            required_tool_retry_count = 0
            executed_tool_steps = 0
            runtime_plan = build_initial_runtime_plan(messages, required_rpas)
            reusable_task_suggestion = None
            non_stream_tool_trace: List[Dict[str, Any]] = []

            if unavailable_required_tool_ids:
                block_message = _build_required_tool_block_message(required_tool_ids, unavailable_required_tool_ids)
                if runtime_plan:
                    runtime_plan = add_constraints(
                        runtime_plan,
                        [
                            {
                                "type": "availability",
                                "tool_name": ",".join(sorted(unavailable_required_tool_ids)),
                                "status": "blocked",
                                "message": block_message,
                                "code": "tool_unavailable",
                            }
                        ],
                    )
                    runtime_plan = finalize_runtime_plan(runtime_plan, block_message)

                if emit_tool_events:
                    async def blocked_stream() -> AsyncIterator[str]:
                        if runtime_plan:
                            yield f"data: {json.dumps({'type': 'runtime_plan', 'plan': runtime_plan}, ensure_ascii=False)}\n\n"
                        yield f"data: {json.dumps({'content': block_message}, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"

                    return blocked_stream()
                return {"response": block_message, "provider": provider, "runtime_plan": runtime_plan, "tool_trace": []}

            if emit_tool_events:
                async def stream_generator() -> AsyncIterator[str]:
                    nonlocal required_tool_retry_count
                    nonlocal executed_tool_steps
                    nonlocal runtime_plan
                    nonlocal reusable_task_suggestion
                    if runtime_plan:
                        yield f"data: {json.dumps({'type': 'runtime_plan', 'plan': runtime_plan}, ensure_ascii=False)}\n\n"
                    while True:
                        round_system_prompt, round_tools, tool_choice, stage_tool_id = _prepare_tool_routing(
                            available_tools,
                            required_rpas,
                            required_tool_statuses,
                            base_system_prompt,
                        )
                        content, tool_calls = await ai_manager.chat(
                            provider=provider,
                            messages=messages,
                            system_prompt=round_system_prompt,
                            tools=round_tools,
                            tool_choice=tool_choice,
                        )

                        if not tool_calls:
                            missing_required_tool_ids = _get_missing_required_tool_ids(required_rpas, required_tool_statuses)
                            if missing_required_tool_ids:
                                if required_tool_retry_count < MAX_REQUIRED_TOOL_RETRIES:
                                    required_tool_retry_count += 1
                                    messages.append(
                                        _build_required_tool_retry_instruction(
                                            missing_required_tool_ids,
                                            stage_tool_id,
                                        )
                                    )
                                    continue
                                final_content = _build_required_tool_block_message(missing_required_tool_ids)
                            else:
                                final_content = content or ""
                            if runtime_plan:
                                runtime_plan = finalize_runtime_plan(runtime_plan, final_content)
                                reusable_task_suggestion = build_reusable_task_suggestion(runtime_plan, messages)
                                yield f"data: {json.dumps({'type': 'runtime_plan', 'plan': runtime_plan}, ensure_ascii=False)}\n\n"
                                if reusable_task_suggestion:
                                    yield f"data: {json.dumps({'type': 'reusable_task_suggestion', 'suggestion': reusable_task_suggestion}, ensure_ascii=False)}\n\n"

                            chunk_size = 80
                            for index in range(0, len(final_content), chunk_size):
                                chunk = final_content[index:index + chunk_size]
                                yield f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
                            if not final_content:
                                yield f"data: {json.dumps({'content': ''}, ensure_ascii=False)}\n\n"
                            break

                        required_tool_retry_count = 0
                        assistant_msg = {"role": "assistant", "content": content or ""}
                        assistant_msg["tool_calls"] = _serialize_tool_calls(tool_calls)
                        messages.append(assistant_msg)

                        for tool_call in tool_calls:
                            tool_name = tool_call.function.name
                            tool_args = _parse_tool_arguments(tool_call.function.arguments)
                            registered_rpa = rpa_by_id.get(tool_name)

                            if runtime_plan:
                                runtime_plan = register_tool_start(runtime_plan, tool_name, tool_args, registered_rpa)
                                yield f"data: {json.dumps({'type': 'runtime_plan', 'plan': runtime_plan}, ensure_ascii=False)}\n\n"

                            guardrail_result = _evaluate_tool_constraints(
                                tool_name,
                                tool_args,
                                registered_rpa,
                                messages,
                                required_tool_ids,
                                executed_tool_steps,
                            )
                            if runtime_plan and guardrail_result["constraints"]:
                                runtime_plan = add_constraints(runtime_plan, guardrail_result["constraints"])
                                yield f"data: {json.dumps({'type': 'runtime_plan', 'plan': runtime_plan}, ensure_ascii=False)}\n\n"

                            yield f"data: {json.dumps({'type': 'tool_start', 'tool': {'id': tool_call.id, 'name': tool_name, 'arguments': tool_args}}, ensure_ascii=False)}\n\n"
                            if guardrail_result["allowed"]:
                                executed_tool_steps += 1
                                result_text, tool_status, task_id = await _execute_tool_call(
                                    session,
                                    tool_name,
                                    tool_args,
                                    tool_cache,
                                    async_tool_ids,
                                    conversation_context,
                                    session_token,
                                )
                            else:
                                result_payload = {
                                    "status": "error",
                                    "message": guardrail_result["blocked_message"],
                                    "error": guardrail_result["blocked_message"],
                                    "constraints": guardrail_result["constraints"],
                                }
                                result_text = json.dumps(result_payload, ensure_ascii=False)
                                tool_status = "error"
                                task_id = None

                            if tool_name in required_tool_ids:
                                _record_required_tool_status(required_tool_statuses, tool_name, tool_status)

                            if runtime_plan:
                                runtime_plan = register_tool_result(
                                    runtime_plan,
                                    tool_name,
                                    tool_status,
                                    _summarize_tool_output(result_text),
                                    task_id,
                                )
                                yield f"data: {json.dumps({'type': 'runtime_plan', 'plan': runtime_plan}, ensure_ascii=False)}\n\n"

                            yield f"data: {json.dumps({'type': 'tool_result', 'tool': {'id': tool_call.id, 'name': tool_name, 'arguments': tool_args, 'status': tool_status, 'task_id': task_id, 'result_preview': _summarize_tool_output(result_text)}}, ensure_ascii=False)}\n\n"
                            messages.append({"tool_call_id": tool_call.id, "role": "tool", "content": result_text})

                    yield "data: [DONE]\n\n"

                return stream_generator()

            while True:
                logger.info("Sending %s messages to AI", len(messages))
                round_system_prompt, round_tools, tool_choice, stage_tool_id = _prepare_tool_routing(
                    available_tools,
                    required_rpas,
                    required_tool_statuses,
                    base_system_prompt,
                )
                content, tool_calls = await ai_manager.chat(
                    provider=provider,
                    messages=messages,
                    system_prompt=round_system_prompt,
                    tools=round_tools,
                    tool_choice=tool_choice,
                )

                if not tool_calls:
                    missing_required_tool_ids = _get_missing_required_tool_ids(required_rpas, required_tool_statuses)
                    if missing_required_tool_ids:
                        if required_tool_retry_count < MAX_REQUIRED_TOOL_RETRIES:
                            required_tool_retry_count += 1
                            messages.append(
                                _build_required_tool_retry_instruction(
                                    missing_required_tool_ids,
                                    stage_tool_id,
                                )
                            )
                            continue
                        runtime_plan = finalize_runtime_plan(
                            add_constraints(
                                runtime_plan,
                                [
                                    {
                                        "type": "required_tool",
                                        "tool_name": ",".join(sorted(missing_required_tool_ids)),
                                        "status": "blocked",
                                        "message": _build_required_tool_block_message(missing_required_tool_ids),
                                        "code": "required_tool_missing",
                                    }
                                ],
                            ),
                            _build_required_tool_block_message(missing_required_tool_ids),
                        )
                        return {
                            "response": _build_required_tool_block_message(missing_required_tool_ids),
                            "provider": provider,
                            "runtime_plan": runtime_plan,
                            "tool_trace": non_stream_tool_trace,
                        }
                    runtime_plan = finalize_runtime_plan(runtime_plan, content or "")
                    reusable_task_suggestion = build_reusable_task_suggestion(runtime_plan, messages)
                    return {
                        "response": content,
                        "provider": provider,
                        "runtime_plan": runtime_plan,
                        "reusable_task_suggestion": reusable_task_suggestion,
                        "tool_trace": non_stream_tool_trace,
                    }

                required_tool_retry_count = 0
                assistant_msg = {"role": "assistant", "content": content}
                assistant_msg["tool_calls"] = _serialize_tool_calls(tool_calls)
                messages.append(assistant_msg)

                tool_results = []
                for tool_call in tool_calls:
                    tool_name = tool_call.function.name
                    tool_args = _parse_tool_arguments(tool_call.function.arguments)
                    registered_rpa = rpa_by_id.get(tool_name)

                    runtime_plan = register_tool_start(runtime_plan, tool_name, tool_args, registered_rpa)
                    guardrail_result = _evaluate_tool_constraints(
                        tool_name,
                        tool_args,
                        registered_rpa,
                        messages,
                        required_tool_ids,
                        executed_tool_steps,
                    )
                    runtime_plan = add_constraints(runtime_plan, guardrail_result["constraints"])

                    if guardrail_result["allowed"]:
                        executed_tool_steps += 1
                        result_text, tool_status, _task_id = await _execute_tool_call(
                            session,
                            tool_name,
                            tool_args,
                            tool_cache,
                            async_tool_ids,
                            conversation_context,
                            session_token,
                        )
                    else:
                        result_payload = {
                            "status": "error",
                            "message": guardrail_result["blocked_message"],
                            "error": guardrail_result["blocked_message"],
                            "constraints": guardrail_result["constraints"],
                        }
                        result_text = json.dumps(result_payload, ensure_ascii=False)
                        tool_status = "error"
                        _task_id = None

                    if tool_name in required_tool_ids:
                        _record_required_tool_status(required_tool_statuses, tool_name, tool_status)
                    if tool_status == "error" and not result_text.startswith("Error"):
                        result_text = f"Error calling tool: {result_text}"
                    runtime_plan = register_tool_result(
                        runtime_plan,
                        tool_name,
                        tool_status,
                        _summarize_tool_output(result_text),
                        _task_id,
                    )
                    non_stream_tool_trace = _upsert_non_stream_tool_trace(
                        non_stream_tool_trace,
                        {
                            "id": tool_call.id,
                            "name": tool_name,
                            "arguments": tool_args,
                            "status": tool_status,
                            "task_id": _task_id,
                            "result_preview": _summarize_tool_output(result_text),
                        },
                    )

                    tool_results.append(
                        {
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "content": result_text,
                        }
                    )
                messages.extend(tool_results)


async def chat_with_mcp(
    messages: List[Dict[str, Any]],
    provider: str,
    conversation_context: Optional[Dict[str, str]] = None,
    allowed_rpa_ids: Optional[set[str]] = None,
    session_token: Optional[str] = None,
):
    """非流式聊天入口，返回最终回复文本。"""

    try:
        slash = await _maybe_run_slash_command(
            messages, provider, allowed_rpa_ids, session_token, emit_tool_events=False
        )
        if slash is not None:
            return slash
        return await _run_chat_loop(
            messages,
            provider,
            conversation_context=conversation_context,
            allowed_rpa_ids=allowed_rpa_ids,
            session_token=session_token,
        )
    except Exception as exc:
        logger.error("MCP connection or session error: %s", exc)
        return {
            "response": "工具系统当前不可用。为避免返回不准确的自动化执行结果，这次不会直接给出“已完成”的结论。",
            "provider": provider,
        }


async def stream_chat_with_mcp(
    messages: List[Dict[str, Any]],
    provider: str,
    conversation_context: Optional[Dict[str, str]] = None,
    allowed_rpa_ids: Optional[set[str]] = None,
    session_token: Optional[str] = None,
) -> AsyncIterator[str]:
    """流式聊天入口，按 SSE 事件格式持续输出内容和工具轨迹。"""

    try:
        slash = await _maybe_run_slash_command(
            messages, provider, allowed_rpa_ids, session_token, emit_tool_events=True
        )
        if slash is not None:
            return slash
        return await _run_chat_loop(
            messages,
            provider,
            conversation_context=conversation_context,
            allowed_rpa_ids=allowed_rpa_ids,
            session_token=session_token,
            emit_tool_events=True,
        )
    except Exception as exc:
        logger.error("Stream error: %s", exc)

        async def error_stream() -> AsyncIterator[str]:
            yield f"data: {json.dumps({'content': f'错误: {exc}'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return error_stream()
