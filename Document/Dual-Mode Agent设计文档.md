# 双模态 AI 交互中心 (Dual-Mode Agent) 设计与实现文档 (V2.1.0)

## 1. 核心概述

在 V2.1.0 版本中，原有的单一自动化 "Copilot 模式" 升级为 **"双模态 AI 交互中心"**。系统不仅保留了原有的全自动运维接管能力，还引入了全新的 **Ask 模式 (智能对话辅助)**。通过双模态架构，不仅能实现“发现异常 -> 自动干预”的全自动闭环，还能提供“实时提问 -> 数据检索 -> 智能解答”的交互式 RAG (Retrieval-Augmented Generation) 能力。

在技术架构上，系统基于 **Django Channels** 实现了全双工的 WebSocket 通信，并在后端引入了 **严格的状态机与协程管控**，确保 "Copilot"（自动运行）与 "Ask"（手动问答）两种模式在同一时间互斥，防止数据流重叠和冲突。此外，Ask 模式接入了 **大模型 Function Calling** 能力，实现意图识别与业务系统真实状态数据库的精准互通。

## 2. 架构设计

### 2.1 整体架构

-   **前端 (Frontend):** 
    -   `base.html` 顶部导航栏替换为交互式的 **Split Button (拆分按钮)**，不仅可以一键唤起主模式，还可以展开下拉菜单自由切换 "Copilot" 与 "Ask" 模式。
    -   **Copilot 蒙层:** 维持全屏 Glass Wall 与 Terminal UI，拦截用户操作，实时展现后台守护模式的自动化执行流。
    -   **Ask 悬浮窗:** 新增右下方的毛玻璃悬浮 Chat 窗口 (`#ask-modal`)，提供即时的对话交互界面。
-   **后端总线 (Channels/ASGI):**
    -   路由 `ws/copilot/` 绑定至 `CopilotConsumer` (位于 `monitor/consumers.py`)。
    -   在 Consumer 中增加了 `receive` 方法以扮演消息路由和状态机的角色，根据前端传入的 JSON 指令动态切换工作流。
-   **智能体核心 (AI Engine):**
    -   `monitor/ai_engine.py` 作为全新的大模型驱动层，集成了基于 OpenAI SDK 接口标准的通义千问 (Qwen-max) API。
    -   Copilot 模式继续保留原有的定时轮询与紧急干预逻辑 (`run_agent_loop`)。
    -   Ask 模式使用 Function Calling 调度本地 RAG 工具函数 (`get_device_recent_data`) 查询真实设备状态，交由大模型整理返回给用户。

### 2.2 双模态状态互斥与生命周期管理

为了解决 "双模态下后台数据指令冲突" 的核心技术难点，`CopilotConsumer` 实现了一个基于内存状态和协程句柄 (Task Handle) 的状态机。

1.  **连接与初始化:** Client 连接时，初始状态 `self.current_mode = 'idle'`，后台不启动任何循环任务 (`self.agent_task = None`)。
2.  **切换到 Copilot 模式:** 接收到前端的 `mode='copilot'` 和 `action='start'` 指令时，系统判断当前是否有运行的 loop 任务，如果没有，则通过 `asyncio.create_task(self.run_agent_loop())` 启动自动监控流。
3.  **切换到 Ask 模式:** 接收到 `mode='ask'` 时，系统立即更新 `self.current_mode = 'ask'`。关键的一步是：如果发现 `self.agent_task` 仍在运行，**程序立即调用 `self.agent_task.cancel()` 终止自动化无限循环**。随后通过 `asyncio.create_task(self.handle_ask_request(message))` 开启问答并发处理。
4.  **互斥保障:** 这种协程管控机制确保了在同一 WebSocket 连接中，AI 代理要么在其专注区域进行自动扫描与干预，要么专注回答人类问题，杜绝了两种模式争抢系统资源、干扰前端 UI 的隐患。

### 2.3 Copilot 模式的自动化工作流 (The Agent Loop)

当模式为 Copilot 时，Agent 的工作流是一个无限循环（直到 WebSocket 模式切换或断开），其核心状态机闭环如下：

1.  **全局扫描 (Global Scan):**
    -   *行为:* 每隔 3 秒读取一次 `AnomalyAlertLog` 表。寻找 `is_handled=False` 且 `anomaly_score > 0.70` 的最新记录。若无异常则继续沉睡。
2.  **锁定目标 (Lock Target):**
    -   *行为:* 获取异常设备 (Device) 和对应的警报记录。将设备名称和风险值推送至前端终端。
3.  **安全停机 (Safety Shutdown - Action):**
    -   *行为:* 调用 ORM 将目标设备的 `current_status` 强制修改为 `Stopped`。立即物理级介入，防止刀具损毁或生产事故。
4.  **数据快照采集 (Data Harvesting):**
    -   *行为:* 提取该设备在异常发生前后的最近 10 条真实传感器数据（涉及主轴电流、功率、进给速度等），为 LLM 提供精准数字孪生上下文。
5.  **AI 智能诊断 (AI Diagnosis):**
    -   *行为:* 组装系统 Prompt 和采集到的上下文数据，通过 API 发送给千问大模型。模型扮演专家提炼简短诊断反馈推至终端。
    -   **[V2.2.0 联动增强]**: 诊断结论将同步持久化至数据库 `DeviceInfo.maintenance_advice` 字段。这意味着 AI 生成的专业建议（如“刀具磨损，建议更换”）将直接同步至 3D 数字孪生及所有监控终端，实现“瞬时日志日志 -> 业务工单”的转化。
6.  **安全恢复 (Recovery):**
    -   *行为:* 模拟维修干预（软复位），重置特征组 `current_group_id`，警报归档恢复 `Running` 状态。
7.  **系统重置与重绘 (Reset & Refresh):**
    -   *行为:* 向前端下发 `refresh` 指令重新拉取 API，保证底层图表与状态机同步。

### 2.4 Ask 模式的 RAG 与 Function Calling 技术

Ask 模式的本质是一个结合了本地实时数据库检索能力的智能问答系统。为了实现“语义化问题 -> 结构化查询 -> 自然语言总结”的完整链路，系统采用了 **Function Calling (工具调用)** 机制。

#### 2.4.1 详细交互流程 (三次握手交互模型)

不同于普通的单次闲聊，Ask 模式在后台经历了类似“三次握手”的多轮会话（但在 WebSocket 前端感知中仍为一次异步请求）：

1.  **第一轮：意图识别 (Identify Intent)**
    *   **发起方:** 本地服务器 (`monitor/ai_engine.py`) -> 大模型 (LLM)。
    *   **动作:** 服务端将用户提问（如：“现在哪台设备风险最高？”）连同系统预设的工具集定义 (`TOOLS`) 发送给 LLM。
    *   **产出:** LLM 检测到用户意图涉及实时数据，不直接回答，而是返回一个特殊的 **`tool_calls`** 结构，指明要调用的函数名（如 `get_device_recent_data`） and 参数（如 `device_names: ['all']`）。

2.  **第二轮：数据穿透 (Data Penetration)**
    *   **发起方:** 本地服务器 (`monitor/ai_engine.py`) -> 本地数据库 (MySQL)。
    *   **动作:** 服务端解析 LLM 发回的 `tool_calls`。**这是最关键的解析步骤**，发生在 `ai_engine.py` 的异步执行线程中。它负责根据 LLM 指定的参数执行 Python 业务逻辑，通过 Django ORM 从 `DeviceInfo` 和 `AnomalyAlertLog` 中提取真实的传感器快照。
    *   **产出:** 穿透数据库后，服务端组装一个包含设备状态、风险分数、报警原因的 JSON 数据包，并将其作为“工具角色 (tool role)”的消息回复给 LLM。

3.  **第三轮：归纳总结 (Summarization)**
    *   **发起方:** 本地服务器 (`monitor/ai_engine.py`) -> 大模型 (LLM)。
    *   **动作:** 服务端将最初的问题、LLM 第一轮的 `tool_calls` 以及刚刚获得的本地真实数据 (`tool response`) 全部打包，再次发送给 LLM。
    *   **产出:** LLM 吸收了注入的真实实时数据，生成最终的专业自然语言回复。服务端通过 WebSocket (`monitor/consumers.py`) 将回复推送给前端。

#### 2.4.2 解析逻辑与执行模块

*   **执行模块:** 系统在 **`monitor/ai_engine.py`** 中定义了 `ask_copilot_with_tools` 函数。该模块是整个 AI 调度的“中枢神经”。
*   **解析逻辑:**
    1.  服务端接收到用户消息后，通过 OpenAI 兼容接口向大模型声明可调用的本地 API 清单。
    2.  利用 `json.loads(tool_call.function.arguments)` 解析大模型生成的参数字符串。
    3.  通过映射字典，将大模型的意图映射到本地定义的 RAG 函数 `get_device_recent_data` 上，从而实现数据的安全读取。
    4.  通过 `data_context` 变量保留一份原始 JSON 副本，随 AI 文本一并下发，供前端进行结构化渲染（卡片或表格）。

### 2.5 核心代码模块映射 (Implementation Modules)

| 模块 / 文件 | 主要承担的职责 |
|---|---|
| `monitor/consumers.py` | **状态机核心与 WebSocket 总线**：统一接管 `ws/copilot/` 路由，处理 `receive` 请求，维护 `agent_task` 协程生命周期，执行 Copilot 单独监控流，并主导模式互斥。 |
| `monitor/ai_engine.py` | **AI 大脑与 Function Calling 引擎**：负责衔接 Qwen-max，注册、解析、执行 `get_device_recent_data` 函数并处理大模型两段式 Ask 对话返回或 Copilot 单次诊断推断。 |
| `monitor/templates/monitor/dashboard.html` | **双模态 UI 适配**：囊括了 Ask 模式的毛玻璃风格聊天窗 (`#ask-modal`) 及其交互 JS，并融合传统的全屏 Copilot Terminal UI。 |
| `monitor/templates/monitor/base.html` | **Split Button 入口**：引入全新样式的原生 HTML+CSS “主按钮/下拉菜单”混合热区，提供清晰的模式入口。 |

## 3. 关键技术点回顾

### 3.1 线程池解决同步阻塞
在 `ai_engine.py` 与 `consumers.py` 中处理请求时，底层使用的是同步的 OpenAI Client，为了防止因为大模型网络延迟或生成过长导致 ASGI Worker (Daphne) 出现事件循环阻塞引发客户端断连，我们在处理请求时利用 AsyncIO 提供的线程池特性进行包裹执行：
```python
loop = asyncio.get_event_loop()
final_reply, data_context = await loop.run_in_executor(None, ask_copilot_with_tools, user_input)
```

### 3.2 提示词工程 (Prompt Engineering) 与 `all` 策略
- **Copilot 模式提示工程**: 采用强控 System Prompt，并在 User prompt 中硬性约定回复长度（“最多3句”），并提供业务常识暗示（“电流激增意味着刀具严重磨损”），彻底避免 LLM 终端日志的格式发散。
- **Ask 模式 `all` 策略**: 为应对用户习惯使用的模糊或总结性提问（例如：“现在车间的设备情况咋样？”），我们对 `System Prompt` 进行了补充。明确告知大模型：可以传入 `['all']` 查询。在后台接收 `all` 信号后，ORM 函数会自动聚合高风险权重 (`anomaly_score > 0.4`) 的设备进行针对性回答，实现了智能的“抓大放小”。

### 3.3 Django 异步 ORM 操作
因后台轮询与工具执行环境位于 ASGI 事件循环中，而 Django ORM 默认是同步的，所以大量引入了 `asgiref.sync.sync_to_async` 包装器进行保护性读写（尤其在 Copilot 的无限循环与 `AnomalyAlertLog` 读取分析中）。

### 3.4 前端 Glass Wall 与视觉反馈
-   **Copilot 交互阻断:** `#copilot-overlay` 绝对定位全屏覆盖，设置 `pointer-events: auto`，拦截一切点击事件，保证系统“被接管”的视觉与物理真实感。增加 `body.copilot-active` 实现屏幕边缘浅蓝色“呼吸灯”效果（box-shadow inset）。
-   **Ask 界面与表格嵌入:** Ask 消息分为 `user` 及 `ai` 两派样式。通过在 WS 返回中下发原生的二维数据表，配合 `font-family: 'Roboto Mono'` 生成具备科技感的行内解析表格，完美补齐了纯文本分析的短板。

## 4. 扩展性探讨与未来建议 (V3.0 Outlook)

1.  **深入 ReAct (Reasoning and Acting) 模式:** V2.1.0 已经在 Ask 模式中验证了 Function Calling 读取能力。V3.0 可将 Copilot 的硬编码干预流（关停、复位）也声明成 Tools，赋予 LLM 更高权限的 Action，令模型结合最新反馈自主决定修复时机。
2.  **长下文对话记忆 (Session Memory):** 目前的 Ask 模块以单向的“一问一答”实现。未来可通过持久化聊天 `messages` 数组（使用 Redis 或 PostgreSQL 保存），进而支持包含指代消解的持续追问（如：“刚才那台精雕设备，它的历史报警记录是什么？”）。
3.  **多功能工具集纵向扩散:** 面向生产管理实际，只需进一步暴露数据库 API，开发更多诸如 `get_production_plan()`（产线进度核对）、`trigger_maintenance_ticket()`（生成工单安排任务） 等工具函数，让 AI 助手真正接管复杂繁冗的车间 ERP 管理节点。
