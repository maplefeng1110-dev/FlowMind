# Publishing FlowMind

这份清单用于准备 FlowMind 的首个公开 GitHub 版本。

## 目标

在不泄露本地运行数据、密钥和内部协作痕迹的前提下，整理出一个适合公开展示、可被他人直接克隆试用的开源仓库。

## 建议公开的内容

- `agent/`
- `client/`
- `registry/`
- `utils/`
- `docs/`
- `README.md`
- `LICENSE`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `CODE_OF_CONDUCT.md`
- `.env.example`
- `agent/.env.example`
- `requirements.txt`
- `pytest.ini`
- `run_stable.py`
- `run_agent.py`
- `data/agent_admin_tokens.example.json`

## 不要公开的内容

- `.env`
- `agent/.env`
- `.venv/`
- `logs/`
- `data/*.db`
- `data/uploads/`
- `data/bootstrap_admin_password.txt`
- `data/agent_admin_tokens.json`
- `.claude/`
- `.codex-enterprise/`
- 本地 IDE 配置
- 任何包含真实内网地址、邮箱、密钥、token、测试样本或运行轨迹的临时文件

## 首个公开提交前检查

1. 从干净副本创建公开仓库，不直接打包当前运行目录。
2. 确认 `.gitignore` 已覆盖本地环境文件、日志、数据库、上传文件和内部协作目录。
3. 搜索仓库内是否仍有敏感内容，例如：

```bash
rg -n "sk-|SECRET_KEY=|SMTP_PASSWORD=|FLOWMIND_AGENT_WS_TOKEN=|AGENT_ADMIN_TOKEN=|192\\.168\\.|/Users/|qq\\.com" .
```

4. 确认只有示例配置文件中保留占位符，不包含真实值。
5. 确认 `README.md`、`docs/README.md`、`SECURITY.md` 中没有虚假的仓库地址或联系方式。
6. 手工检查首个 commit 计划包含的文件列表。

## 建议的首个公开提交内容

- 核心源码
- 文档
- 示例配置
- 开源治理文件

更具体的候选文件列表见 [PUBLIC_COMMIT_FILESET.md](PUBLIC_COMMIT_FILESET.md)。

不要把历史运行数据、调试日志、截图样本、个人脚本或内部审计文件混进首个公开提交。

## GitHub 仓库创建后建议补充

- 替换 README 中的仓库地址占位符
- 配置仓库简介、标签和社交预览图
- 社交预览图可直接使用 `docs/assets/social-preview.jpg`
- README 展示图可直接使用 `docs/assets/showcase-chat.png`、`docs/assets/showcase-schedule.png`、`docs/assets/showcase-export.png`
- 开启 Issues 和 Pull Requests
- 开启 Private Vulnerability Reporting
- 配置默认分支保护规则
- 补第一版演示截图或 GIF
- 准备一段简短的项目介绍文案

## 最后人工确认

在真正 `git push` 之前，请再确认一次：

- 没有真实密钥
- 没有个人邮箱密码
- 没有内网地址
- 没有数据库和上传文件
- 没有内部协作资产
- `LICENSE` 已存在且符合你的发布意图

如果这些都满足，FlowMind 就已经具备一个比较体面的首个公开版本基础。
