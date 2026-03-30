from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """聊天请求体，包含消息列表和当前会话上下文。"""

    messages: List[Dict[str, Any]]
    provider: str = None
    conversation_id: Optional[str] = None
    conversation_title: Optional[str] = None


class TaskSummaryRequest(BaseModel):
    """后台任务总结请求体，用于把任务结果转成用户可读摘要。"""

    tool_name: str
    task: Dict[str, Any]
    arguments: Optional[Dict[str, Any]] = None


class LoginRequest(BaseModel):
    """登录请求体。"""

    username: str
    password: str


class CreateUserRequest(BaseModel):
    """管理员创建账号请求体。"""

    username: str
    password: str
    display_name: Optional[str] = None
    role: str = "business"
    allowed_rpa_ids: List[str] = Field(default_factory=list)


class UpdateUserPluginsRequest(BaseModel):
    """管理员更新账号插件授权。"""

    allowed_rpa_ids: List[str] = Field(default_factory=list)


class ScaffoldPluginRequest(BaseModel):
    """管理员创建插件脚手架请求体。"""

    plugin_id: str
    machine_id: Optional[str] = None
    description: Optional[str] = None
    source_file: Optional[str] = None
    timeout_sec: int = Field(default=60, ge=1)
    tags: List[str] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    params: List[str] = Field(default_factory=list)
    required_params: List[str] = Field(default_factory=list)
    force: bool = False


class CommonTaskCreateRequest(BaseModel):
    """保存常用任务模板请求体。"""

    title: str
    prompt: str
    summary: Optional[str] = None
    plan: Optional[Dict[str, Any]] = None


class TaskScheduleRequest(BaseModel):
    """定时调度计划请求体。"""

    title: Optional[str] = None
    prompt: Optional[str] = None
    summary: Optional[str] = None
    common_task_id: Optional[str] = None
    provider: Optional[str] = None
    schedule_type: str
    schedule_config: Dict[str, Any] = Field(default_factory=dict)
    timezone: Optional[str] = None
    is_active: bool = True


class TaskScheduleToggleRequest(BaseModel):
    """调度开关请求体。"""

    is_active: bool


class ExportDownloadRequest(BaseModel):
    """统一导出中心下载请求体。"""

    resource: str
    format: str
    conversation_id: Optional[str] = None
    limit: Optional[int] = Field(default=200, ge=1, le=500)
