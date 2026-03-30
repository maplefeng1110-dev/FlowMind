# Contributing to FlowMind

感谢你愿意为 FlowMind 做贡献。

## 开始之前

- 先阅读 [README.md](README.md) 和 [docs/README.md](docs/README.md)
- 变更前尽量先开一个 Issue，说明问题、目标和边界
- 涉及权限、调度、登录、数据结构变更时，请先说明设计取舍

## 本地开发

1. 创建虚拟环境并安装依赖
2. 复制 `.env.example` 为 `.env`
3. 复制 `agent/.env.example` 为 `agent/.env`
4. 分别启动：
   - `./.venv/bin/python run_stable.py`
   - `./.venv/bin/python run_agent.py`

## 提交建议

- 保持改动聚焦，不把无关重构混在一起
- 新功能尽量补测试
- 修改用户可见行为时，请同步更新 README 或相关文档
- 不要提交任何真实密钥、数据库、上传文件、日志或本地 IDE 配置

## 代码与测试

- Python 代码保持可读性优先
- 新增接口时，请覆盖成功路径和关键失败路径
- 提交前建议至少运行：

```bash
./.venv/bin/python -m pytest -q
```

## 插件贡献

- 新插件请优先参考 [docs/PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md)
- 插件需要提供清晰的 `manifest.yaml`
- 如果插件有副作用、权限风险或外部依赖，请在文档中明确说明

## Pull Request 清单

- 说明改动目的
- 说明用户可见变化
- 说明测试结果
- 说明已知限制或未覆盖项

感谢你的帮助，这个项目会因为这些高质量贡献变得更可靠。
