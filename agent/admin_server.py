import secrets
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from agent.env import get_agent_env, load_agent_env
from agent.plugin_scaffold import scaffold_plugin
from utils.logger import setup_logger


load_agent_env()

logger = setup_logger("AgentAdmin", "agent-admin.log")
app = FastAPI(title="FlowMind Agent Admin API")
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


class ScaffoldPluginRequest(BaseModel):
    plugin_id: str
    description: str | None = None
    source_file: str | None = None
    timeout_sec: int = Field(default=60, ge=1)
    tags: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    params: list[str] = Field(default_factory=list)
    required_params: list[str] = Field(default_factory=list)
    force: bool = False


def _is_enabled(raw_value: str | None) -> bool:
    return str(raw_value or "").strip().lower() not in {"0", "false", "no", "off"}


def agent_admin_enabled() -> bool:
    return _is_enabled(get_agent_env("AGENT_ADMIN_ENABLED", "true"))


def get_agent_admin_host() -> str:
    return str(get_agent_env("AGENT_ADMIN_HOST", "127.0.0.1") or "127.0.0.1")


def get_agent_admin_port() -> int:
    return int(get_agent_env("AGENT_ADMIN_PORT", "8765") or "8765")


def get_agent_admin_token() -> str:
    return str(get_agent_env("AGENT_ADMIN_TOKEN", "") or "").strip()


def get_agent_admin_public_url() -> str:
    return str(get_agent_env("AGENT_ADMIN_PUBLIC_URL", "") or "").strip().rstrip("/")


def _client_host(request: Request) -> str:
    return (request.client.host if request.client else "") or ""


def _authorize_admin_request(request: Request) -> None:
    if not agent_admin_enabled():
        raise HTTPException(status_code=503, detail="Agent 管理 API 当前已禁用。")

    expected_token = get_agent_admin_token()
    if expected_token:
        provided_token = (request.headers.get("X-FlowMind-Agent-Admin-Token") or "").strip()
        if not provided_token or not secrets.compare_digest(provided_token, expected_token):
            raise HTTPException(status_code=401, detail="Agent 管理 API 凭据无效。")
        return

    if _client_host(request) not in LOOPBACK_HOSTS:
        raise HTTPException(
            status_code=403,
            detail="当前 Agent 管理 API 未配置 AGENT_ADMIN_TOKEN，仅允许本机回环访问。",
        )


def _build_scaffold_argv(data: ScaffoldPluginRequest) -> list[str]:
    argv = [data.plugin_id]
    if data.description:
        argv.extend(["--description", data.description])
    if data.source_file:
        argv.extend(["--source-file", data.source_file])
    for item in data.tags:
        argv.extend(["--tag", item])
    for item in data.capabilities:
        argv.extend(["--capability", item])
    for item in data.keywords:
        argv.extend(["--keyword", item])
    if not data.source_file:
        for item in data.params:
            argv.extend(["--param", item])
        for item in data.required_params:
            argv.extend(["--required", item])
    argv.extend(["--timeout-sec", str(data.timeout_sec)])
    if data.force:
        argv.append("--force")
    return argv


def _relative_or_absolute_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "admin_enabled": agent_admin_enabled(),
        "public_url": get_agent_admin_public_url() or None,
        "plugins_dir": str(Path(__file__).resolve().parent / "plugins"),
    }


@app.post("/api/plugins/scaffold")
async def scaffold_plugin_via_admin_api(data: ScaffoldPluginRequest, request: Request):
    _authorize_admin_request(request)

    try:
        plugin_dir = scaffold_plugin(_build_scaffold_argv(data))
        manifest = yaml.safe_load((plugin_dir / "manifest.yaml").read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, FileExistsError, ValueError, SyntaxError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Agent 本地插件脚手架写入失败：{exc}") from exc

    files = ["manifest.yaml", "__init__.py"]
    if (plugin_dir / "impl.py").exists():
        files.append("impl.py")

    logger.info("Scaffolded plugin %s at %s", manifest.get("id") or data.plugin_id, plugin_dir)
    return {
        "plugin": manifest,
        "plugin_dir": _relative_or_absolute_path(plugin_dir),
        "files": files,
        "message": "Agent 本地插件脚手架已生成，请重启对应 Agent 使其重新加载插件。",
        "restart_required": True,
    }


if __name__ == "__main__":
    import uvicorn

    logger.info("=" * 60)
    logger.info("FlowMind Agent Admin API")
    logger.info("=" * 60)
    logger.info("Admin enabled: %s", agent_admin_enabled())
    logger.info("Listening on: http://%s:%s", get_agent_admin_host(), get_agent_admin_port())
    logger.info("=" * 60)
    uvicorn.run("agent.admin_server:app", host=get_agent_admin_host(), port=get_agent_admin_port(), reload=False)
