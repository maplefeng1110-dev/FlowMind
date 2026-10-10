# FlowMind 部署指南

## 一、后端（Docker）

后端 = **Registry**(`:8000`) + **Web**(`:5173`) + **Redis**，全部在 `docker-compose.yml` 里。

### 步骤
```bash
cp .env.example .env
# 至少填：SECRET_KEY、FLOWMIND_ADMIN_USERNAME/PASSWORD、
#         FLOWMIND_AGENT_WS_TOKEN、以及 AI key（DEEPSEEK_API_KEY 等）
docker compose up -d --build
```

访问：
- Web UI：`http://<服务器IP>:5173`
- Agent 连接：`ws://<服务器IP>:8000/ws`
- 管理员密码：若未配 `FLOWMIND_ADMIN_PASSWORD`，看日志或数据卷里的 `/data/bootstrap_admin_password.txt`

### 说明
- 数据持久化在命名卷 `flowmind-data`（`/data`：`registry.db`、上传、日志、bootstrap 密码）。所有路径由 `FLOWMIND_DATA_DIR` 等环境变量驱动，已在镜像里指向 `/data`。
- Registry 与 Web 共用同一镜像，靠 `command` 区分；Web 通过 `REGISTRY_API_URL=http://registry:8000` 连 Registry。
- `__main__` 的监听地址改为 env 可配（`REGISTRY_HOST` / `WEB_HOST`，默认仍 `127.0.0.1`），compose 里设为 `0.0.0.0`。
- Redis 仅做多实例协调，纯内存、无持久化（`REDIS_URL=redis://redis:6379/0` 已接好）。
- 对外 HTTPS：在前面加 Caddy/Nginx 反代到 `5173`/`8000`（本编排未含，按需自加）。

### 常用命令
```bash
docker compose logs -f registry web
docker compose down       # 停止（保留数据卷）
docker compose down -v    # 停止并删除数据卷
```

## 二、Agent（独立可执行，Windows / macOS）

Agent 用 PyInstaller 打成**单个可执行 + 同目录配置文件**。

### 开发者：打包
> 不能交叉编译——Windows 包在 Windows 上打、macOS 包在 macOS 上打。需目标平台 Python 3.9+。

```bash
# macOS
bash packaging/build_mac.sh
#   产物：dist/flowmind-agent-macos/（flowmind-agent + extension/ + USAGE.txt）

# Windows
packaging\build_windows.bat
REM 产物：dist\flowmind-agent-windows\（flowmind-agent.exe + extension\ + USAGE.txt）
```

打包要点（已在 `packaging/flowmind-agent.spec` 处理）：
- `agent/`、`utils/`、`browser_bridge/` 作为源码随包，运行时由 entry 加到 `sys.path`，executor 从解包目录**动态加载插件**；
- 插件依赖（openpyxl / python-docx / bs4 / httpx / websockets…）通过 `collect_all` / `hiddenimports` 一并打入；
- 若运行时报 `ModuleNotFoundError`，把缺的模块名加进 spec 的 `hiddenimports` 再打一次；
- macOS 首次运行被 Gatekeeper 拦：右键「打开」，或 `xattr -dr com.apple.quarantine flowmind-agent`。

### 最终用户：安装与运行
1. 解压发布包，运行 `flowmind-agent`（Mac：`./flowmind-agent`；Windows：双击 `.exe`）。
2. 首次运行交互式填：Registry WS 地址 / 机器 ID / Agent WS Token（与后端一致）→ 生成同目录 `flowmind-agent.env`（以后可直接编辑）。
3. 连接成功后本机出现在后端 Web 的「机器」列表。

## 三、浏览器 RPA（可选 · 进阶）

`rpa_flow` 需要浏览器扩展 + Native Messaging Host。

1. **加载扩展**：Chrome → `chrome://extensions` → 开发者模式 → 加载已解压 → 选发布包里的 `extension/`，复制扩展 ID。
2. **注册 Native Host**：把 `com.flowmind.host.json` 写到浏览器的 `NativeMessagingHosts` 目录，`path` 指向一个 wrapper（因为 native messaging 的 path 不能带参数）：
   - macOS/Linux：一个 shell 脚本，内容 `exec /路径/flowmind-agent --native-host`
   - Windows：一个 `.bat`，内容 `"C:\路径\flowmind-agent.exe" --native-host`
   - `allowed_origins` 填 `chrome-extension://<扩展ID>/`
   （清单模板与协议见 `browser_bridge/README.md`。）
3. Worker 桥接默认 `ws://127.0.0.1:8777`，可用 `FLOWMIND_BROWSER_BRIDGE_PORT` 调整。
