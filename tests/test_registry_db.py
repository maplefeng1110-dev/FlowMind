import pytest
import aiosqlite

from registry import db as registry_db


@pytest.mark.asyncio
async def test_register_machine_persists_dynamic_manifests(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    manifest = {
        "id": "weather_api",
        "version": "1.0.0",
        "description": "天气查询",
        "tags": ["weather"],
        "params_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
        "returns_schema": {"type": "object"},
        "timeout_sec": 10,
        "owner": "system",
    }

    await registry_db.register_machine(
        "machine-a",
        {},
        ["weather_api"],
        [manifest],
    )

    rpa = await registry_db.get_rpa("weather_api")
    assert rpa is not None
    assert rpa["description"] == "天气查询"


@pytest.mark.asyncio
async def test_authenticate_user_and_session_round_trip(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    user = await registry_db.create_user("biz_demo", "strongpass123", display_name="业务演示账号")

    authed = await registry_db.authenticate_user("biz_demo", "strongpass123")
    assert authed is not None
    assert authed["id"] == user["id"]

    session_token = await registry_db.create_session(user["id"])
    session_user = await registry_db.get_user_by_session_token(session_token)
    assert session_user is not None
    assert session_user["username"] == "biz_demo"


@pytest.mark.asyncio
async def test_init_db_generates_bootstrap_admin_password_file_when_env_missing(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))
    monkeypatch.delenv("FLOWMIND_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(registry_db, "DEFAULT_ADMIN_USERNAME", "admin")

    await registry_db.init_db()

    bootstrap_file = test_db.parent / registry_db.BOOTSTRAP_ADMIN_PASSWORD_FILENAME
    assert bootstrap_file.exists()

    password_line = next(
        line for line in bootstrap_file.read_text(encoding="utf-8").splitlines() if line.startswith("password=")
    )
    bootstrap_password = password_line.split("=", 1)[1].strip()

    admin_user = await registry_db.authenticate_user("admin", bootstrap_password)
    assert admin_user is not None
    assert admin_user["role"] == "admin"


@pytest.mark.asyncio
async def test_set_user_allowed_rpas_persists_assignments(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    await registry_db.upsert_rpa_manifests(
        [
            {"id": "invoice_ocr", "description": "OCR", "params_schema": {"type": "object", "properties": {}}},
            {"id": "send_email", "description": "邮件", "params_schema": {"type": "object", "properties": {}}},
        ]
    )
    user = await registry_db.create_user("ops_team", "strongpass123")

    updated = await registry_db.set_user_allowed_rpas(user["id"], ["send_email", "invoice_ocr"])

    assert updated["assigned_rpa_ids"] == ["invoice_ocr", "send_email"]


@pytest.mark.asyncio
async def test_get_all_rpas_includes_dynamic_registered_plugins(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    await registry_db.upsert_rpa_manifests(
        [
            {
                "id": "browser_automation",
                "version": "1.0.0",
                "description": "浏览器自动化",
                "tags": ["browser", "automation"],
                "params_schema": {"type": "object", "properties": {}},
                "returns_schema": {"type": "object"},
                "timeout_sec": 20,
                "owner": "system",
            }
        ]
    )

    rpas = await registry_db.get_all_rpas()

    assert any(rpa["id"] == "browser_automation" for rpa in rpas)


@pytest.mark.asyncio
async def test_get_available_rpas_only_returns_online_plugins(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    await registry_db.upsert_rpa_manifests(
        [
            {
                "id": "browser_automation",
                "description": "浏览器自动化",
                "tags": ["browser"],
                "params_schema": {"type": "object", "properties": {}},
            },
            {
                "id": "send_email",
                "description": "邮件发送",
                "tags": ["mail"],
                "params_schema": {"type": "object", "properties": {}},
            },
        ]
    )
    await registry_db.register_machine("machine-a", {}, ["send_email"], [])

    rpas = await registry_db.get_available_rpas()

    assert [rpa["id"] for rpa in rpas] == ["send_email"]
    assert rpas[0]["online_machine_ids"] == ["machine-a"]
    assert rpas[0]["online_machine_count"] == 1


@pytest.mark.asyncio
async def test_get_stale_online_machine_ids_respects_heartbeat_timeout(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    await registry_db.register_machine("machine-a", {}, ["send_email"], [])
    await registry_db.register_machine("machine-b", {}, ["web_query"], [])

    async with aiosqlite.connect(str(test_db)) as db:
        await db.execute(
            "UPDATE machines SET last_heartbeat = datetime('now', '-120 seconds') WHERE id = ?",
            ("machine-a",),
        )
        await db.commit()

    stale_ids = await registry_db.get_stale_online_machine_ids(30)

    assert stale_ids == ["machine-a"]


@pytest.mark.asyncio
async def test_save_message_persists_meta(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    await registry_db.save_conversation("conv-1", "测试会话")
    await registry_db.save_message(
        "msg-1",
        "conv-1",
        "assistant",
        "done",
        "12:00",
        {"tool_trace": [{"id": "tool-1", "name": "web_query", "status": "success"}]},
    )

    messages = await registry_db.get_conversation_messages("conv-1")

    assert messages[0]["meta"]["tool_trace"][0]["name"] == "web_query"
    assert messages[0]["meta"] is not None


@pytest.mark.asyncio
async def test_common_task_round_trip_is_owner_scoped(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    owner_a = await registry_db.create_user("team_a", "strongpass123")
    owner_b = await registry_db.create_user("team_b", "strongpass123")

    created = await registry_db.create_common_task(
        title="发票处理",
        prompt="把这些发票识别后汇总到 Excel，再发邮件给财务。",
        summary="invoice_ocr -> excel_processor -> send_email",
        plan={"status": "completed"},
        owner_user_id=owner_a["id"],
    )

    owner_a_tasks = await registry_db.list_common_tasks(owner_user_id=owner_a["id"])
    owner_b_tasks = await registry_db.list_common_tasks(owner_user_id=owner_b["id"])

    assert [task["id"] for task in owner_a_tasks] == [created["id"]]
    assert owner_b_tasks == []

    used = await registry_db.touch_common_task(created["id"], owner_user_id=owner_a["id"])
    assert used is not None
    assert used["id"] == created["id"]

    deleted = await registry_db.delete_common_task(created["id"], owner_user_id=owner_a["id"])
    assert deleted is True


@pytest.mark.asyncio
async def test_task_schedule_round_trip_and_run_claim(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    owner = await registry_db.create_user("schedule_owner", "strongpass123")

    schedule = await registry_db.create_task_schedule(
        title="财务日报",
        prompt="整理今日财务摘要并发送日报邮件。",
        summary="日报任务",
        provider="deepseek",
        schedule_type="interval",
        schedule_config={"interval_minutes": 15},
        timezone_name="Asia/Shanghai",
        owner_user_id=owner["id"],
    )

    listed = await registry_db.list_task_schedules(owner_user_id=owner["id"])
    assert [item["id"] for item in listed] == [schedule["id"]]
    assert listed[0]["schedule_config"]["interval_minutes"] == 15

    paused = await registry_db.set_task_schedule_active(schedule["id"], False, owner_user_id=owner["id"])
    assert paused is not None
    assert paused["is_active"] is False

    resumed = await registry_db.set_task_schedule_active(schedule["id"], True, owner_user_id=owner["id"])
    assert resumed is not None
    assert resumed["is_active"] is True

    async with aiosqlite.connect(str(test_db)) as db:
        await db.execute(
            "UPDATE task_schedules SET next_run_at = datetime('now', '-1 minute') WHERE id = ?",
            (schedule["id"],),
        )
        await db.commit()

    claimed = await registry_db.claim_due_task_schedules(limit=2)
    assert len(claimed) == 1
    claimed_item = claimed[0]
    assert claimed_item["schedule"]["id"] == schedule["id"]
    assert claimed_item["run"]["status"] == "queued"

    started = await registry_db.mark_task_schedule_run_started(
        schedule["id"],
        claimed_item["run"]["id"],
        claimed_item["claim_token"],
    )
    assert started is not None
    assert started["status"] == "running"

    completed = await registry_db.complete_task_schedule_run(
        schedule["id"],
        claimed_item["run"]["id"],
        claimed_item["claim_token"],
        status="success",
        response_text="日报已完成",
        runtime_plan={"status": "completed"},
        tool_trace=[{"name": "send_email", "status": "success"}],
        conversation_id="conv-1",
        conversation_title="定时任务｜财务日报",
        provider="deepseek",
    )
    assert completed is not None
    assert completed["status"] == "success"
    assert completed["tool_trace"][0]["name"] == "send_email"

    refreshed_schedule = await registry_db.get_task_schedule(schedule["id"], owner_user_id=owner["id"])
    assert refreshed_schedule is not None
    assert refreshed_schedule["last_status"] == "success"
    assert refreshed_schedule["last_run_at"]
    assert refreshed_schedule["next_run_at"]

    runs = await registry_db.list_task_schedule_runs(owner_user_id=owner["id"])
    assert [item["id"] for item in runs] == [claimed_item["run"]["id"]]


@pytest.mark.asyncio
async def test_owner_scoped_conversations_and_tasks_are_filtered(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    await registry_db.register_machine("machine-a", {}, ["invoice_ocr"], [])

    owner_a = await registry_db.create_user("team_a", "strongpass123")
    owner_b = await registry_db.create_user("team_b", "strongpass123")

    await registry_db.save_conversation("conv-a", "A 对话", owner_user_id=owner_a["id"])
    await registry_db.save_conversation("conv-b", "B 对话", owner_user_id=owner_b["id"])
    await registry_db.create_task(
        "task-a",
        "machine-a",
        "invoice_ocr",
        {"invoice_path": "a.pdf"},
        conversation_id="conv-a",
        conversation_title="A 对话",
        owner_user_id=owner_a["id"],
    )
    await registry_db.create_task(
        "task-b",
        "machine-a",
        "invoice_ocr",
        {"invoice_path": "b.pdf"},
        conversation_id="conv-b",
        conversation_title="B 对话",
        owner_user_id=owner_b["id"],
    )

    conversations = await registry_db.get_conversations(owner_user_id=owner_a["id"])
    tasks = await registry_db.list_tasks(owner_user_id=owner_a["id"])

    assert [item["id"] for item in conversations] == ["conv-a"]
    assert [item["id"] for item in tasks] == ["task-a"]


@pytest.mark.asyncio
async def test_get_conversation_messages_preserves_insert_order_with_same_second_timestamps(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    await registry_db.save_conversation("conv-1", "测试会话")
    await registry_db.save_message("msg-1", "conv-1", "assistant", "first", "12:00")
    await registry_db.save_message("msg-2", "conv-1", "assistant", "second", "12:00")

    messages = await registry_db.get_conversation_messages("conv-1")

    assert [message["id"] for message in messages] == ["msg-1", "msg-2"]


@pytest.mark.asyncio
async def test_get_task_decodes_params_and_result_json(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    await registry_db.register_machine("machine-a", {}, ["invoice_ocr"], [])
    await registry_db.create_task(
        "task-1",
        "machine-a",
        "invoice_ocr",
        {"invoice_path": "a.pdf"},
        status="running",
        conversation_id="conv-1",
        conversation_title="测试对话",
    )
    await registry_db.update_task_result("task-1", "success", {"status": "success", "data": {"text": "ok"}})

    task = await registry_db.get_task("task-1")

    assert task["params"]["invoice_path"] == "a.pdf"
    assert task["result"]["data"]["text"] == "ok"
    assert task["conversation_id"] == "conv-1"
    assert task["conversation_title"] == "测试对话"
    assert task["machine_status"] == "online"


@pytest.mark.asyncio
async def test_list_tasks_returns_latest_first_with_decoded_payloads(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    await registry_db.register_machine("machine-a", {}, ["invoice_ocr"], [])
    await registry_db.register_machine("machine-b", {}, ["web_query"], [])
    await registry_db.create_task("task-1", "machine-a", "invoice_ocr", {"invoice_path": "a.pdf"}, status="running")
    await registry_db.create_task("task-2", "machine-b", "web_query", {"query": "天气"}, status="running")
    await registry_db.update_task_result("task-2", "success", {"status": "success", "data": {"summary": "ok"}})

    tasks = await registry_db.list_tasks(limit=10)

    assert tasks[0]["id"] == "task-2"
    assert tasks[0]["result"]["data"]["summary"] == "ok"
    assert tasks[0]["machine_status"] == "online"
    assert tasks[1]["params"]["invoice_path"] == "a.pdf"


@pytest.mark.asyncio
async def test_list_machines_returns_status_rpas_and_running_task_count(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    await registry_db.register_machine(
        "machine-a",
        {"os": "macOS"},
        ["invoice_ocr", "send_email"],
        [],
    )
    await registry_db.register_machine(
        "machine-b",
        {"os": "Windows"},
        ["web_query"],
        [],
    )
    await registry_db.update_machine_status("machine-b", "offline")
    await registry_db.create_task("task-1", "machine-a", "invoice_ocr", {"invoice_path": "a.pdf"}, status="running")

    machines = await registry_db.list_machines()

    assert machines[0]["id"] == "machine-a"
    assert machines[0]["status"] == "online"
    assert machines[0]["system_info"]["os"] == "macOS"
    assert machines[0]["rpas"] == ["invoice_ocr", "send_email"]
    assert machines[0]["running_task_count"] == 1
    assert machines[1]["id"] == "machine-b"
    assert machines[1]["status"] == "offline"


@pytest.mark.asyncio
async def test_list_machines_preserves_agent_admin_metadata(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    await registry_db.register_machine(
        "machine-a",
        {
            "os": "macOS",
            "agent_admin": {
                "enabled": True,
                "api_url": "https://agent-a.example.com",
            },
        },
        ["invoice_ocr"],
        [],
    )

    machines = await registry_db.list_machines()

    assert machines[0]["system_info"]["agent_admin"]["enabled"] is True
    assert machines[0]["system_info"]["agent_admin"]["api_url"] == "https://agent-a.example.com"


@pytest.mark.asyncio
async def test_delete_task_record_removes_terminal_task(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    await registry_db.register_machine("machine-a", {}, ["invoice_ocr"], [])
    await registry_db.create_task("task-1", "machine-a", "invoice_ocr", {"invoice_path": "a.pdf"}, status="success")

    deleted = await registry_db.delete_task_record("task-1")
    task = await registry_db.get_task("task-1")

    assert deleted is True
    assert task is None


@pytest.mark.asyncio
async def test_get_machine_and_delete_machine_record_round_trip(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    await registry_db.register_machine("machine-a", {"os": "macOS"}, ["invoice_ocr"], [])
    await registry_db.update_machine_status("machine-a", "offline")

    machine = await registry_db.get_machine("machine-a")
    deleted = await registry_db.delete_machine_record("machine-a")
    missing_machine = await registry_db.get_machine("machine-a")

    assert machine is not None
    assert machine["status"] == "offline"
    assert machine["system_info"]["os"] == "macOS"
    assert deleted is True
    assert missing_machine is None
