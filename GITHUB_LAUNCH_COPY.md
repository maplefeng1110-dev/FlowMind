# Launch Copy Templates

这份文档整理了 FlowMind 首个公开版本可直接复用的 GitHub、X、知乎首发文案。

## 首个 commit message

推荐版本：

```text
chore: publish FlowMind as an open-source AI-native RPA orchestration project
```

如果你更想强调功能基础，也可以用：

```text
feat: open-source FlowMind with chat orchestration, scheduling, and export center
```

## GitHub 仓库简介

### 中文版

```text
AI Native RPA 编排系统，支持自由对话编排、插件化工具调用、定时调度、执行轨迹与统一导出。
```

### 英文版

```text
AI-native RPA orchestration with chat-driven tool execution, plugin scaffolding, scheduling, runtime traces, and export center.
```

## GitHub Topics

建议从下面这些里挑 8 到 12 个：

- `ai`
- `rpa`
- `automation`
- `mcp`
- `fastapi`
- `python`
- `agent`
- `workflow`
- `orchestration`
- `chatbot`
- `plugin-system`
- `scheduler`
- `ocr`
- `document-processing`

## Social Preview / Open Graph 文案

```text
FlowMind
AI-native RPA orchestration for chat-driven automation
Plugins · Runtime plans · Scheduling · Exports
```

## 仓库首页一句话介绍

### 中文版

```text
FlowMind 把 AI 对话、RPA 插件和可控执行轨迹放进同一个工作台，让业务用户用自然语言完成自动化任务。
```

### 英文版

```text
FlowMind brings AI chat, RPA plugins, and observable execution into one workspace for business-friendly automation.
```

## GitHub Release / 首发帖文案

### 中文版

```text
FlowMind 开源了。

这是一个 AI Native RPA 编排系统，核心方向不是传统重流程设计器，而是让业务用户直接通过自然语言驱动插件执行，并在过程中看到运行时计划、工具轨迹、调度记录和统一导出结果。

当前版本已经包含：
- 对话驱动的工具编排
- 插件化 RPA 扩展机制
- 常用任务沉淀
- 定时调度中心
- 统一导出中心
- 多用户权限边界

欢迎试用、提 Issue、提 PR。
```

### 英文版

```text
FlowMind is now open source.

It is an AI-native RPA orchestration project focused on chat-driven automation instead of heavyweight flow designers. Users can trigger plugins with natural language and inspect runtime plans, tool traces, scheduling records, and exportable results in one place.

Current foundation includes:
- chat-driven tool orchestration
- plugin-based RPA extensions
- reusable common tasks
- scheduling center
- export center
- multi-user permission boundaries

Issues and PRs are welcome.
```

## X / Twitter Launch Copy

### 中文短帖版

```text
FlowMind 开源了。

这是一个 AI Native RPA 编排系统，不做重型流程设计器，而是让业务用户直接通过自然语言驱动插件执行，并在同一个界面里看到运行时计划、工具轨迹、调度记录和导出结果。

当前版本已经有：
- chat-driven tool orchestration
- plugin scaffolding
- scheduling center
- export center
- multi-user permission boundaries

#opensource #AI #RPA #automation
```

### 英文短帖版

```text
FlowMind is now open source.

It is an AI-native RPA orchestration project focused on chat-driven automation instead of heavyweight flow designers. Users can trigger plugins with natural language and inspect runtime plans, tool traces, scheduling records, and exportable results in one place.

#opensource #AI #RPA #automation #python
```

### X Thread 模板

```text
1/ We just open-sourced FlowMind, an AI-native RPA orchestration project.

2/ The core idea is simple: let users trigger automation with natural language, while still keeping execution observable and controllable.

3/ Instead of pushing users into heavyweight flow designers first, FlowMind starts from:
- chat-driven tool orchestration
- plugin-based RPA extensions
- runtime plans and tool traces

4/ Current foundation also includes:
- reusable common tasks
- scheduling center
- export center
- multi-user permission boundaries

5/ If you're exploring AI + automation + MCP + enterprise workflow tooling, we'd love your feedback.
```

## 知乎首发文案

### 标题备选

- `我把一个 AI Native RPA 编排项目开源了：FlowMind`
- `为什么我觉得企业自动化不该先做重型流程设计器？`
- `FlowMind 开源：把 AI 对话、RPA 插件、调度和执行轨迹放进一个工作台`

### 摘要版

```text
最近把一个自己在做的 AI Native RPA 编排项目整理后开源了，名字叫 FlowMind。

它的核心方向不是传统那种“先画流程图、先搭重型 BPM”的路线，而是先让业务用户直接通过自然语言触发自动化，再逐步补运行时计划、工具轨迹、调度、导出和可复用任务沉淀。

当前版本已经有对话驱动工具编排、插件化扩展、定时调度中心、统一导出中心和多用户权限边界。后面还会继续补更强的失败恢复、事件触发和工程化能力。
```

### 长文版

```text
最近把一个自己在做的 AI Native RPA 编排项目整理后开源了，名字叫 FlowMind。

先说它的定位：它不是想一开始就做一个重型流程设计器，也不是单纯做一个“聊天机器人接几个工具”。我更想解决的是企业自动化里一个很常见的问题：业务用户希望直接说需求，但系统又不能因为“自由对话”就失控。

所以 FlowMind 走的是另外一条路：

1. 先让用户通过自然语言触发自动化
2. 在后台生成运行时计划，而不是强迫用户先画流程图
3. 把执行轨迹、步骤状态、恢复建议和结果导出做出来
4. 再把跑通的对话沉淀成常用任务、调度计划和可复用能力

当前开源出来的这版，已经有几块基础能力：

- 对话驱动的工具编排
- 插件化 RPA 扩展机制
- 工具能力地图
- 运行时执行计划与轨迹
- 常用任务沉淀
- 定时调度中心
- 统一导出中心
- 多用户权限边界

这条路线我比较看重的一点是：它比“先让业务人员学会配流程节点”更自然，也更接近真实使用场景。用户第一次可以自由说，系统跑通之后，再慢慢沉淀成半结构化模板，而不是上来就要求他们理解 BPMN。

当然，这个项目现在也还不完美。比如更强的失败恢复、事件触发、Excel/PDF 导出、重型流程引擎、生产级部署手册，都还在后续路线里。

如果你也在关注 AI + RPA、MCP、企业自动化、对话式编排这几个方向，欢迎来看看，也欢迎提问题和建议。
```

## README 顶部宣传短句备选

- `让 AI 对话真正驱动企业自动化。`
- `从自然语言到可观测执行的 AI Native RPA 工作台。`
- `把插件能力、运行时计划、调度和导出统一到同一个自动化入口。`

## 使用建议

- 首个公开版本建议优先用英文仓库简介，中文介绍放在 README
- Topics 不要一次加太多，先选最核心的 8 到 12 个
- 如果还没有演示图，先上一个简洁的 Social Preview 标题图，再补完整 GIF
- X 文案建议保持“短帖 + thread”两套
- 知乎建议优先发长文版，再从里面拆短摘要同步到其它平台
