# FlowMind 操作手册

本文档详细介绍了如何安装、配置、运行以及扩展 FlowMind —— 一个基于 OpenAI 兼容大模型接口和 MCP 协议的分布式 RPA 编排系统。

---

## 目录
1. [系统要求](#系统要求)
2. [环境安装与启动](#环境安装与启动)
3. [核心服务说明](#核心服务说明)
4. [系统配置说明 (.env)](#系统配置说明--env)
5. [功能使用指南](#功能使用指南)
6. [如何开发新的 RPA 插件](#如何开发新的-rpa-插件)
7. [当前优先改进项](#当前优先改进项)

---

## 1. 系统要求
- **操作系统**: Windows / macOS / Linux
- **Python 环境**: Python 3.11 或更高版本
- **大模型 API 密钥**: 至少准备一个 OpenAI 兼容提供商的 API Key。当前项目文档示例主要覆盖 DeepSeek 和火山引擎（ARK）。

---

## 2. 环境安装与启动

### 2.1 安装依赖
打开终端，进入项目根目录：
```bash
# Windows
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# macOS / Linux
python3.11 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

### 2.2 启动核心服务
FlowMind 目前推荐把核心服务和 Agent 分开启动，这样更方便排查“Web 已启动但没有在线工具”的问题。

**推荐方式：使用启动脚本**

**终端 1 - Registry + Web**
```bash
# Windows
.\.venv\Scripts\python.exe run_stable.py

# macOS / Linux
./.venv/bin/python run_stable.py
```

**终端 2 - Agent**
```bash
# Windows
.\.venv\Scripts\python.exe run_agent.py

# macOS / Linux
./.venv/bin/python run_agent.py
```

仅运行 `run_stable.py` 时，Web 页面可以打开，但不会有在线 Agent，工具调用会保持等待。

**备用方式：分别启动三个服务**

**终端 1 - Registry 中心服务后台**
```bash
# Windows
.\.venv\Scripts\python.exe -m uvicorn registry.main:app --host 127.0.0.1 --port 8000

# macOS / Linux
./.venv/bin/python -m uvicorn registry.main:app --host 127.0.0.1 --port 8000
```

**终端 2 - RPA 执行 Agent 节点**
```bash
# Windows
.\.venv\Scripts\python.exe -m agent.main

# macOS / Linux
./.venv/bin/python -m agent.main
```

**终端 3 - Client Web UI 界面**
```bash
# Windows
.\.venv\Scripts\python.exe -m client.web_server

# macOS / Linux
./.venv/bin/python -m client.web_server
```
Web 界面默认在 `http://127.0.0.1:5173` 启动。

---

## 3. 核心服务说明
- **Registry 中心 (8000)**: 维护一个内部的 MCP 服务，能够根据在线注册的 Agent，动态向 AI 模型提供当前可用能力清单（Tools）。
- **Agent 本地端**: 读取 `agent/plugins` 目录中的各个 `manifest.yaml` 元数据，向外通过 WebSocket 展现它可执行的任务。
- **Web 端 (5173)**: 通过 `/api/chat` 以 HTTP 的方式连接到 `registry` 去索要并执行 `tool_calls`。用户和 AI 在这里交互。

---

## 4. 系统配置说明 (.env)

FlowMind 现在把配置拆成两层：

- 根目录 `.env`：Registry / Web / 共享安全配置
- `agent/.env`：Agent 连接参数和插件侧配置

如果找不到这些文件，可以分别从项目内的 `.env.example` 和 `agent/.env.example` 复制：

```ini
# 根目录 .env
# (选填) DeepSeek API Key
DEEPSEEK_API_KEY=sk-xxxx

# (选填) 火山引擎 (豆包) API Key
ARK_API_KEY=your-ark-key
ARK_MODEL=doubao-pro-32k

# Registry / Web 地址
REGISTRY_API_URL=http://127.0.0.1:8000

# 内部服务鉴权（二选一）
SECRET_KEY=your-secret-key
# 或者单独设置
# FLOWMIND_INTERNAL_API_TOKEN=your-internal-token

# 管理员账号初始化
FLOWMIND_ADMIN_USERNAME=admin
FLOWMIND_ADMIN_PASSWORD=replace-with-a-strong-password

# Session Cookie
FLOWMIND_SESSION_TTL_SECONDS=604800
FLOWMIND_SESSION_COOKIE=flowmind_session
FLOWMIND_SESSION_COOKIE_SECURE=false

# 调度中心（可选）
FLOWMIND_SCHEDULER_ENABLED=true
FLOWMIND_SCHEDULER_POLL_SECONDS=20
FLOWMIND_SCHEDULER_CLAIM_BATCH_SIZE=3
FLOWMIND_SCHEDULER_SESSION_TTL_SECONDS=7200

# Registry 侧在线 Agent 清理参数（可选）
AGENT_HEARTBEAT_TIMEOUT_SEC=45
AGENT_HEARTBEAT_SWEEP_INTERVAL_SEC=15
```

```ini
# agent/.env
REGISTRY_URL=ws://127.0.0.1:8000/ws
MACHINE_ID=local-dev-machine
AGENT_HEARTBEAT_INTERVAL_SEC=10
AGENT_ADMIN_ENABLED=true
AGENT_ADMIN_HOST=127.0.0.1
AGENT_ADMIN_PORT=8765
# AGENT_ADMIN_PUBLIC_URL=https://agent-a.example.com
# AGENT_ADMIN_TOKEN=replace-with-a-random-token

# 可选：invoice_ocr 插件
# OCR_API_URL=http://your-ocr-api-url
# OCR_API_TIMEOUT=15

# 可选：send_email 插件
# SMTP_SERVER=smtp.example.com
# SMTP_PORT=587
# SMTP_USER=your_email@example.com
# SMTP_PASSWORD=your_email_password
```
如果 Web 管理端需要通过远端 Agent 管理 API 创建插件，还可以在根目录 `.env` 里补：

```ini
# .env
# AGENT_ADMIN_API_URL=http://agent-host:8765
# AGENT_ADMIN_TOKEN_STORE_PATH=data/agent_admin_tokens.json
# AGENT_ADMIN_API_TOKEN=replace-with-agent-admin-token
# AGENT_ADMIN_API_TOKENS_JSON='{"machine-a":"token-a","machine-b":"token-b"}'
```

如果要在多台 Agent 中按机器选择插件安装目标：

- 每台 Agent 都需要配置自己的 `AGENT_ADMIN_PUBLIC_URL`
- Web 推荐使用独立 token store 文件 `data/agent_admin_tokens.json`
- 可直接参考 `data/agent_admin_tokens.example.json`
- 运行态文件建议执行 `chmod 600 data/agent_admin_tokens.json`
- 建议在文件中补齐 `updated_at`、`default_rotated_at`、各机器 `rotated_at`
- 如果 token store 中没有对应机器条目，系统仍会回退到 `AGENT_ADMIN_API_TOKENS_JSON` 和共享 `AGENT_ADMIN_API_TOKEN`
- 管理员可以调用 `GET /api/admin/agent-admin-token-store` 查看脱敏后的状态与告警

完成后记得重启相关服务。系统会自动检测可用的 AI 提供商并在界面顶栏显示。

如果没有配置 `FLOWMIND_ADMIN_PASSWORD`，系统会在首次初始化时于 `data/bootstrap_admin_password.txt` 生成一次性 bootstrap 管理员凭据文件。
如果 Agent 启动后一直无法注册，请优先检查 `agent/.env` 中的 `REGISTRY_URL`、`MACHINE_ID` 以及相关插件变量。

---

## 5. 功能使用指南
打开浏览器访问：[http://127.0.0.1:5173](http://127.0.0.1:5173)

### 常用使用指令
1.  **查询能力**: 
    在输入框中向 AI 询问："你支持哪些自动化工具？" 或者 "你能做些什么？" 体验 AI 对系统功能的理解。
2.  **触发测试用例一 (OCR)**:
    输入 "帮我识别一张发票，路径是：/demo/invoice.pdf"，观察 AI 自动将这条要求解析出了所需参数，发送给 Agent，然后在收到成功回调后输出整理好的内容结果。
3.  **触发测试用例二 (Email)**:
    输入 "给 zhangsan@email.com 发送一封邮件，告诉他我请假一天。"

### 当前版本新增入口

- **调度中心**：顶部时钟按钮，可把常用任务沉淀成一次性、固定间隔或每日计划，并查看最近执行记录。
- **导出中心**：顶部下载按钮，可把当前对话、任务中心、常用任务、调度计划和执行记录下载成 JSON / CSV / Markdown。

---

## 6. 如何开发新的 RPA 插件
如果希望给 Agent 增加诸如“浏览器操作”、“爬虫”或“数据库写入”这些新的技能，无需修改后端代码只需开发插件。

### 6.1 最快接入方式：使用脚手架

如果你已经登录 Web 管理端，其实不必再回到命令行。推荐直接：

1. 使用管理员账号登录
2. 打开“账号权限中心”
3. 找到“插件接入向导”
4. 填写插件 ID 与描述
5. 若已有 Python RPA 文件，直接填写绝对路径
6. 若暂时没有实现，填写参数定义生成空白插件骨架
7. 提交后重启 Agent，让新插件上线

页面提交后会自动完成：

- 生成 `manifest.yaml`
- 生成 `__init__.py`
- 若是包装现有实现，还会复制出 `impl.py`
- 同步 manifest 到 Registry 元数据，便于管理员先做权限分配

说明：插件 ID 建议使用 `weather_api` 这类“字母开头 + 下划线命名”的形式。

补充说明：

- 插件脚手架本身属于 Agent 侧能力，真正的落盘位置是 Agent 本地的 `agent/plugins/`。
- 因此“插件接入向导”更适合同机部署或开发环境。
- 如果 Agent 与 Web/Registry 分开部署，可选两种方式：
  - 在 Agent 节点执行下面的脚手架命令
  - 或配置好 Agent 管理 API，让 Web 管理端通过远端 API 调用
- 如果已经配置了多个 Agent 的 `AGENT_ADMIN_PUBLIC_URL`，插件向导里可以直接选择目标机器。

如果你已经有一个 Python RPA 文件，推荐直接运行：

```bash
./.venv/bin/python -m agent.scaffold_plugin weather_api \
  --description "天气查询插件" \
  --source-file /path/to/weather_rpa.py
```

这个命令会自动生成：

- `agent/plugins/weather_api/manifest.yaml`
- `agent/plugins/weather_api/__init__.py`
- `agent/plugins/weather_api/impl.py`

如果你还没有现成实现，也可以先生成一个空插件骨架：

```bash
./.venv/bin/python -m agent.scaffold_plugin weather_api \
  --description "天气查询插件" \
  --param city:string \
  --required city
```

生成后只需要补上业务逻辑，再重启 Agent 即可。

### 6.2 手工方式：自己建插件目录

在 `agent/plugins/` 目录下创建一个新的文件夹（如 `weather_api`）：

**1. 定义功能和字段 (manifest.yaml)**
它描述了这一能力是如何展示被大模型看到并且调用的：
```yaml
id: "weather_api"
version: "1.0.0"
description: "获取某个指定的城市的天气预报"
params_schema:
  type: "object"
  properties:
    city:
      type: "string"
      description: "想查询的城市名"
  required:
    - "city"
timeout_sec: 10
owner: "system"
```

**2. 编写功能的底层 Python 逻辑 (\_\_init\_\_.py)**
定义异步方法 `run` 接收 `manifest` 中的参数。
```python
import asyncio

async def run(city: str, **kwargs):
    print(f"执行爬虫逻辑抓取 {city} 的天气...")
    await asyncio.sleep(2) # 模拟爬虫过程
    return {
        "temperature": 20,
        "wind": "东风"
    }
```
**3. 重启 Agent**
退出当前的 Agent 后台并重新启动。系统将自动让 AI 模型意识到新的 `weather_api` 能力。在 Web UI 重发送新的请求即可！

---

## 7. 当前优先改进项

根据当前文档和实现状态，FlowMind 接下来最值得继续补强的方向有：

1. **可视化流程编排**
   - 当前更适合聊天式触发工具调用
   - 当前已经有调度中心和执行记录
   - 仍缺流程设计器、流程版本管理和更复杂的事件触发

2. **上线运维能力**
   - 当前文档已经能支撑本地开发和试运行
   - 但 Docker、进程守护、反向代理、TLS、备份恢复、监控告警还需要专门补齐

3. **插件实战化**
   - Excel、Word、网页查询等插件能力已经增强
   - 但真实文件样本、异常格式处理、插件沙箱与权限边界仍值得继续加强

4. **前端真实环境验证**
   - 当前界面体验已经做过视觉和滚动优化
   - 但现代浏览器、移动端和 E2E 自动化验证仍不够充分

5. **调度与导出增强**
   - 当前已经有统一入口
   - 但失败重试策略、Excel/PDF 导出和脱敏下载仍值得继续加强
