# FlowMind 插件开发指南

## 1. 概述

FlowMind 采用插件化架构，允许开发者通过简单的 Python 代码和 YAML 配置来扩展系统的 RPA（机器人流程自动化）能力。每个插件都是一个独立的功能模块，可以被 AI 通过 MCP（Model Context Protocol）协议自动发现和调用。

## 2. 插件架构

### 2.1 核心概念

- **插件**: 独立的功能模块，包含业务逻辑和元数据
- **Manifest**: 插件元数据文件（`manifest.yaml`），包含插件的描述、参数、返回值等信息
- **Executor**: 插件执行器（`LocalExecutor`），负责加载和执行插件
- **MCP 暴露**: 通过 MCP 协议将插件暴露给 AI 模型

### 2.2 插件目录结构

每个插件必须位于 `agent/plugins/` 目录下的一个独立文件夹中：

```
agent/plugins/
└── {插件名}/
    ├── __init__.py          # 插件实现（必须包含 async def run()）
    ├── manifest.yaml        # 插件元数据
    ├── [其他资源文件]       # 可选的辅助文件（如图片、配置等）
    └── __pycache__/         # Python 编译文件（自动生成）
```

### 2.3 快速脚手架

除了命令行，你也可以直接走 Web 管理端：

- 管理员登录后打开“账号权限中心”
- 使用“插件接入向导”填写插件 ID、描述、关键词、标签等元数据
- 如果有现成 Python 文件，填入绝对路径后提交
- 如果没有现成实现，可以直接填写参数定义生成空插件骨架
- 页面会自动把 manifest 写入 Registry 元数据，方便你马上分配账号权限
- 最后重启 Agent，让它真正加载并注册这个新插件

边界说明：

- `scaffold_plugin` 的实现归属在 `agent/`，因为它最终是在 Agent 本地生成 `agent/plugins/{plugin_id}/`。
- Web“插件接入向导”更适合同机部署或开发环境。
- 如果 Agent 独立部署，可以登录到 Agent 节点执行下面的 CLI，或者配置 Agent 管理 API 让 Web 管理端远程调用；都不要在 Client 所在机器上直接写远端插件目录。
- 如果希望在 Web 管理端按机器选择安装目标，请为每台 Agent 配置 `AGENT_ADMIN_PUBLIC_URL`，并优先把多机 token 放在 `AGENT_ADMIN_TOKEN_STORE_PATH` 指向的 JSON 文件里。
- 建议在 token store 中补齐 `rotated_at` 元数据，并把运行态文件权限收紧到 `600`。

推荐把插件 ID 和目录名控制在 `weather_api` 这种形式，避免使用空格、连字符和中文目录名。

如果你已经有一个 Python RPA 文件，最省事的接入方式是直接用脚手架命令：

```bash
./.venv/bin/python -m agent.scaffold_plugin weather_api \
  --description "天气查询插件" \
  --source-file /path/to/weather_rpa.py
```

这个命令会自动：

- 在 `agent/plugins/weather_api/` 下创建插件目录
- 复制你的 Python 文件到 `impl.py`
- 生成包装用的 `__init__.py`
- 自动推断 `run()` 函数参数并生成 `manifest.yaml`

如果你还没有现成的 Python 文件，也可以先生成一个空插件骨架：

```bash
./.venv/bin/python -m agent.scaffold_plugin weather_api \
  --description "天气查询插件" \
  --param city:string \
  --required city \
  --keyword 天气
```

常用参数：

- `--source-file`：已有 Python RPA 文件路径
- `--param name:type`：手工定义 manifest 参数
- `--required name`：声明必填参数
- `--tag` / `--capability` / `--keyword`：补充 manifest 元数据
- `--output-dir`：自定义输出目录（默认 `agent/plugins`）
- `--force`：覆盖已有目录

注意：

- 脚手架要求你的现有 Python 文件里有顶层 `run()` 函数
- 如果 `run()` 是同步函数，脚手架会自动生成异步包装层
- 插件 ID 当前要求字母开头，后续只包含字母、数字和下划线
- 自动推断出来的 `manifest.yaml` 适合作为起点，复杂 schema 仍建议手工再精修

## 3. manifest.yaml 详解

这是插件的核心配置文件，包含了插件的所有元数据信息。

### 3.1 基本信息

```yaml
id: "invoice_ocr"              # 唯一标识符（必填）
version: "1.0.0"               # 版本号（必填）
description: "发票 OCR 识别"  # 简要描述（必填）
tags: ["ocr", "发票", "财务"]  # 标签（用于分类和搜索）
capabilities: ["ocr", "invoice"]  # 能力列表（用于 MCP 暴露）
owner: "system"                # 插件所有者（可选）
```

### 3.2 强制调用策略

FlowMind 支持**半强制工具路由**，确保 AI 在处理特定请求时必须调用相应的 RPA 插件。

```yaml
enforcement:
  must_call_when_matched: true  # 匹配到意图时必须调用
  require_success_before_final: true  # 必须成功执行后才能结束对话
  intent_keywords: [           # 意图匹配关键词
    "ocr", "识别发票", "发票识别", "发票ocr", "识别票据", "提取发票"
  ]
```

**意图匹配原理**：
- 系统会将用户消息与 `intent_keywords` 进行匹配
- 如果匹配成功，AI 必须调用该插件
- 可以配置是否要求插件必须成功执行才能结束对话

### 3.3 参数 Schema

定义插件接受的输入参数，使用 JSON Schema 格式。

```yaml
params_schema:
  type: "object"
  properties:
    invoice_path:
      type: "string"
      description: "发票文件完整路径，例如 data/uploads/xxx.jpg"
    output_format:
      type: "string"
      description: "输出格式 (json/dict)"
      default: "json"
  required:
    - "invoice_path"
```

### 3.4 返回值 Schema

定义插件的返回值格式，同样使用 JSON Schema。

```yaml
returns_schema:
  type: "object"
  properties:
    status:
      type: "string"
      description: "执行状态 (success/error)"
    data:
      type: "object"
      description: "OCR 结果数据，原文位于 data.text"
```

### 3.5 其他配置

```yaml
timeout_sec: 60                # 插件执行超时时间（秒）
async: false                   # 是否是异步插件（可选，默认 false）
```

### 3.6 企业级能力地图（tool_profile）

如果希望 AI 在自由对话里更稳定地编排插件，建议在 manifest 里补齐 `tool_profile`：

```yaml
tool_profile:
  solves:
    - "这个插件解决什么业务问题"
  suitable_inputs:
    - "适合什么输入或什么前置上下文"
  outputs:
    - "会产出什么结果"
  prerequisites:
    - "执行前必须满足什么条件"
  common_failure_reasons:
    - "最常见的失败原因"
  supports_batch: false
  has_side_effects: false
  requires_confirmation: false
  allowed_path_roots:
    - "data/uploads"
    - "data/exports"
  max_retries: 1
```

这些字段会直接影响：

- AI 选工具时对插件职责的理解
- 运行时计划里如何展示步骤和恢复建议
- 执行前的约束检查，例如缺参数、风险确认、路径范围限制

建议：

- `has_side_effects`：会写文件、发通知、调用外部系统时设为 `true`
- `requires_confirmation`：高风险动作设为 `true`
- `allowed_path_roots`：尽量限制在上传目录和导出目录
- `common_failure_reasons`：写真实错误，不要只写“执行失败”

## 4. 插件实现 (__init__.py)

插件实现必须包含一个 `async def run(**params)` 函数，该函数接受参数并返回一个包含 `status` 和 `data` 的字典。

### 4.1 基础模板

```python
import asyncio
from utils.logger import setup_logger
from utils.plugin_result import success_result, error_result

# 创建日志器（日志文件会自动保存到 logs/ 目录）
logger = setup_logger("InvoiceOCR", "invoice_ocr.log")

async def run(invoice_path: str, output_format: str = "json") -> dict:
    """
    发票 OCR 识别插件

    参数:
        invoice_path: 发票文件路径
        output_format: 输出格式 (json/dict)

    返回:
        dict: 包含 status 和 data 的结果字典
    """

    # 参数验证
    if not invoice_path:
        return error_result("invoice_path is required")

    try:
        # 业务逻辑实现
        # 可以调用外部 API、执行本地命令、操作文件等
        result = await do_ocr(invoice_path)

        return success_result(data=result)
    except Exception as e:
        logger.error(f"OCR failed: {e}")
        return error_result("OCR processing failed", error=str(e))
```

### 4.2 实用工具

#### 4.2.1 日志

使用 `setup_logger()` 函数创建日志器：

```python
from utils.logger import setup_logger
logger = setup_logger("插件名", "日志文件名.log")

# 日志级别
logger.debug("调试信息")
logger.info("普通信息")
logger.warning("警告")
logger.error("错误")
logger.critical("严重错误")
```

#### 4.2.2 结果包装

使用 `success_result()` 和 `error_result()` 函数包装返回结果：

```python
from utils.plugin_result import success_result, error_result

# 成功结果
return success_result(
    data={"key": "value"},
    message="操作成功"
)

# 错误结果
return error_result(
    "操作失败",
    error="详细错误信息"
)
```

#### 4.2.3 文件操作

项目提供了统一的文件操作接口：

```python
from pathlib import Path
from utils.paths import get_uploads_dir

# 获取上传目录
uploads_dir = get_uploads_dir()
file_path = Path(invoice_path)

# 检查文件是否存在
if not file_path.exists():
    return error_result(f"File not found: {invoice_path}")
```

#### 4.2.4 环境变量配置

如果插件需要读取环境变量（例如 `OCR_API_URL`、`SMTP_SERVER`），建议把这类配置放到 `agent/.env`，而不是根目录 `.env`。这样可以把 Agent / 插件侧配置与 Registry / Web 侧配置分开管理。

### 4.3 同步代码异步化

如果需要调用同步代码（如第三方库），可以使用 `asyncio.to_thread()` 或 `loop.run_in_executor()`：

```python
import requests
import asyncio

async def run(url: str) -> dict:
    # 同步函数
    def fetch_url():
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.text

    try:
        content = await asyncio.to_thread(fetch_url)
        return success_result(data={"content": content})
    except Exception as e:
        return error_result(str(e))
```

## 5. 现有插件示例分析

### 5.1 invoice_ocr（发票识别）

**功能**：从发票图片或 PDF 中提取 OCR 文本。

**特点**：
- 使用外部 OCR API
- 支持图片和 PDF 文件
- 返回结构化文本
- 强制调用策略

**实现要点**：
```python
async def run(invoice_path: str, output_format: str = "json") -> dict:
    file_path = Path(invoice_path)
    if not file_path.exists():
        return error_result("Invoice file does not exist")

    ocr_url = os.getenv("OCR_API_URL")
    if not ocr_url:
        return error_result("OCR_API_URL is not configured")

    img_b64 = await asyncio.to_thread(_encode_file, file_path)
    response_json = await asyncio.to_thread(_call_ocr_api, ocr_url, img_b64, timeout)
    # 处理 OCR 结果
    return success_result(data={"text": processed_text})
```

### 5.2 send_email（邮件发送）

**功能**：发送邮件。

**特点**：
- 支持 HTML 和纯文本邮件
- 支持附件
- SMTP 配置
- 错误处理

### 5.3 web_query（网页查询）

**功能**：查询网页内容。

**特点**：
- 使用浏览器自动化（Selenium 或 Playwright）
- 支持页面截图
- 支持内容提取

## 6. 插件测试

### 6.1 本地测试

可以直接在 Python 中测试插件：

```python
import sys
import asyncio
from pathlib import Path

# 添加入口目录到系统路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.executor import LocalExecutor

async def test_plugin():
    # 初始化执行器
    executor = LocalExecutor(Path("agent/plugins"))

    # 测试发票识别插件
    result = await executor.run("invoice_ocr", {
        "invoice_path": "test_invoice.jpg"
    })

    print("Result:", result)

if __name__ == "__main__":
    asyncio.run(test_plugin())
```

### 6.2 集成测试

使用项目的测试框架进行集成测试：

```python
# tests/test_plugins.py
import pytest
import asyncio
from agent.executor import LocalExecutor

@pytest.fixture
def executor():
    return LocalExecutor()

@pytest.mark.asyncio
async def test_invoice_ocr_plugin(executor):
    result = await executor.run("invoice_ocr", {
        "invoice_path": "data/uploads/test_invoice.jpg"
    })
    assert result["status"] == "success"
    assert "text" in result["data"]
```

## 7. 常见问题与解决方案

### 7.1 插件未加载

**问题**：插件在 `agent/plugins/` 目录下，但执行器没有加载。

**解决方案**：
1. 检查插件目录是否符合规范
2. 检查 `__init__.py` 是否包含 `async def run()` 函数
3. 检查 `manifest.yaml` 是否存在且格式正确
4. 查看 `agent.log` 中的错误信息

### 7.2 参数验证失败

**问题**：AI 调用插件时参数验证失败。

**解决方案**：
1. 检查 `manifest.yaml` 中的 `params_schema` 定义
2. 确保插件的 `run()` 函数参数与 Schema 匹配
3. 检查参数类型是否正确

### 7.3 超时

**问题**：插件执行超时。

**解决方案**：
1. 优化插件代码，减少执行时间
2. 增加 `manifest.yaml` 中的 `timeout_sec` 配置
3. 使用异步操作提高响应速度

### 7.4 调试插件

**解决方案**：
1. 使用 `logger.debug()` 输出调试信息
2. 在 `run()` 函数中添加 `print()` 语句
3. 使用 `pdb` 调试器
4. 查看 `logs/` 目录下的插件日志文件

## 8. 插件开发最佳实践

### 8.1 设计原则

1. **单一职责**：每个插件应该只负责一个具体的功能
2. **参数精简**：只暴露必要的参数，减少使用复杂度
3. **错误处理**：全面的错误处理和日志记录
4. **异步优先**：优先使用异步代码，提高并发能力
5. **可测试性**：确保插件可以独立测试

### 8.2 命名规范

- 插件 ID：使用小写字母、数字和下划线的组合（如 `invoice_ocr`）
- 文件名：使用有意义的名称（如 `invoice_ocr.py`）
- 变量名：遵循 Python 命名规范（蛇形命名法）

### 8.3 版本管理

- 使用语义化版本号（如 `1.0.0`）
- 更新插件时更新版本号
- 保持向后兼容性

### 8.4 文档

- 在 `__init__.py` 中添加详细的文档字符串
- 在 `manifest.yaml` 中添加清晰的描述
- 为复杂插件创建 README 文件

## 9. 待开发插件建议

### 9.1 excel_processor

**功能**：Excel 文件处理。

**核心功能**：
- 读取 Excel 文件
- 写入 Excel 文件
- 数据筛选和统计
- 图表生成

**使用场景**：
- 数据导入/导出
- 报表生成
- 数据清洗

### 9.2 word_processor

**功能**：Word 文档处理。

**核心功能**：
- 读取 Word 文档
- 写入 Word 文档
- 文档格式化
- 邮件合并

**使用场景**：
- 文档生成
- 报告自动化
- 合同处理

### 9.3 web_query

**功能**：网页内容查询。

**核心功能**：
- 网页截图
- 内容提取
- 表单填写
- 页面导航

**使用场景**：
- 数据爬取
- 信息收集
- 网页自动化

## 10. 总结

FlowMind 的插件化架构使得扩展系统功能变得非常简单。开发者只需要遵循一定的规范，就可以开发出强大的 RPA 插件，这些插件可以被 AI 模型自动发现和调用，从而实现自然语言驱动的流程自动化。

通过合理的设计和实现，开发者可以创建出高效、稳定、可维护的插件，满足企业的各种自动化需求。
