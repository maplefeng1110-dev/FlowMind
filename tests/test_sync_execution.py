"""Tools declared `execution: sync` return their real result within the chat turn."""
import client.web_server_tooling as tooling

EXCEL = {"id": "excel_processor", "execution": "sync", "params_schema": {"type": "object"}}
MAIL = {"id": "send_email", "execution": "sync", "params_schema": {"type": "object"}}
FLOW = {"id": "rpa_flow", "params_schema": {"type": "object"}}


def test_tools_split_by_declared_execution_mode():
    rpas = [EXCEL, MAIL, FLOW, {"description": "no id"}]
    assert tooling._collect_sync_tool_ids(rpas) == {"excel_processor", "send_email"}
    assert tooling._collect_async_tool_ids(rpas) == {"rpa_flow"}
