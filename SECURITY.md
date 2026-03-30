# Security Policy

感谢你帮助 FlowMind 保持安全。

## 报告安全问题

- 请不要在公开 Issue 中直接披露漏洞细节
- 如果仓库启用了 GitHub Private Vulnerability Reporting，请优先使用该通道
- 如果尚未启用，请先创建一个不包含利用细节的简短 Issue，请维护者补开私密沟通渠道

## 报告内容建议

- 影响范围
- 复现步骤
- 可能风险
- 临时缓解方法

## 响应原则

- 我们会尽量先确认问题是否成立
- 高风险问题会优先修复，并在必要时先发布缓解方案
- 在修复发布前，请避免公开披露可直接利用的细节

## 安全边界提醒

在公开部署时，尤其要检查：

- `.env` 和 `agent/.env` 未被提交
- 数据库、上传文件和日志目录未被提交
- 管理员默认凭据已替换
- `FLOWMIND_INTERNAL_API_TOKEN`、`FLOWMIND_AGENT_WS_TOKEN`、`AGENT_ADMIN_TOKEN` 已配置为随机值
- 生产环境启用了 HTTPS、Cookie 安全策略和最小权限配置
