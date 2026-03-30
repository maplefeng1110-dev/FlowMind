# First Public Commit Fileset

这份文档给出 FlowMind 首个公开提交建议包含的文件范围。

## 建议加入首个公开提交的根目录文件

- `.gitignore`
- `.env.example`
- `README.md`
- `LICENSE`
- `PUBLISHING.md`
- `PUBLIC_COMMIT_FILESET.md`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `CODE_OF_CONDUCT.md`
- `requirements.txt`
- `pytest.ini`
- `run_stable.py`
- `run_agent.py`

## 建议加入首个公开提交的目录

- `agent/`
  - 但不包含 `agent/.env`
  - 只保留源码、示例配置和内置插件
- `client/`
- `registry/`
- `utils/`
- `docs/`

## 建议加入的示例数据

- `data/agent_admin_tokens.example.json`

## 明确不要加入首个公开提交的内容

- `.env`
- `agent/.env`
- `.venv/`
- `.claude/`
- `.codex-enterprise/`
- `logs/`
- `data/*.db`
- `data/uploads/`
- `data/bootstrap_admin_password.txt`
- `data/agent_admin_tokens.json`
- `__pycache__/`
- `.pytest_cache/`
- 任意包含真实密钥、真实邮箱、内网地址、运行轨迹或测试样本的临时文件

## 建议的首个 `git add` 范围

如果你是从干净副本初始化公开仓库，首个提交建议至少包含：

```bash
git add \
  .gitignore \
  .env.example \
  README.md \
  LICENSE \
  PUBLISHING.md \
  PUBLIC_COMMIT_FILESET.md \
  CONTRIBUTING.md \
  SECURITY.md \
  CODE_OF_CONDUCT.md \
  requirements.txt \
  pytest.ini \
  run_stable.py \
  run_agent.py \
  agent \
  client \
  registry \
  utils \
  docs \
  data/agent_admin_tokens.example.json
```

## 首个提交前再检查一次

在真正提交前，建议跑这两步：

```bash
git status --short --ignored
rg -n "sk-|SECRET_KEY=|SMTP_PASSWORD=|FLOWMIND_AGENT_WS_TOKEN=|AGENT_ADMIN_TOKEN=|192\\.168\\.|/Users/|qq\\.com" .
```

如果第二条命令仍扫出真实值，请先清理再提交。

## 推荐首发原则

- 首发优先保证“能跑 + 能看懂 + 不泄密”
- 不必一开始就把所有运维工程化文件补齐
- 先让外部用户能理解项目定位、能本地启动、能看插件机制

如果这份清单和 `PUBLISHING.md` 都检查通过，FlowMind 的首个公开提交范围就基本清楚了。
