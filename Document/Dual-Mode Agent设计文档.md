# 双模态 AI 交互中心 (Dual-Mode Agent) 设计与实现文档 (V3.5.0)

## 1. 核心概述

在 V3.5.0 版本中，原有的单一自动化 "Copilot 模式" 升级为 **"双模态 AI 交互中心"**。系统不仅保留了原有的全自动运维接管能力，还引入了全新的 **Ask 模式 (智能对话辅助)**。通过双模态架构，不仅能实现“发现异常 -> 自动干预”的全自动闭环，还实现了 **Copilot 与 Ask 双模式对知识库的全面覆盖**，提供基于实时数据与本地文档库的混合调度能力。

在技术架构上，系统基于 **Django Channels** 实现了全双工的 WebSocket 通信，并在后端引入了 **严格的状态机与协程管控**，确保 "Copilot"（自动运行）与 "Ask"（手动问答）两种模式在同一时间互斥，防止数据流重叠和冲突。此外，Ask 模式接入了 **大模型 Function Calling** 与 **FAISS 语义搜索** 双引擎，实现意图识别、业务系统数据库与非结构化知识库的精准互通。

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
    -   `monitor/services/agent_service.py` 作为大模型驱动层，集成了基于 OpenAI SDK 接口标准的通义千问 (Qwen-max) API。
    -   `monitor/services/knowledge_service.py` 专门负责非结构化知识的向量化处理与检索。
    -   Copilot 模式继续保留原有的定时轮询与紧急干预逻辑 (`run_autonomous_loop`)。
    -   Ask 模式首先触发语义检索，随后使用 Function Calling 调度本地工具函数 (`get_device_recent_data`) 查询真实设备状态。

### 2.2 双模态状态互斥与生命周期管理

为了解决 "双模态下后台数据指令冲突" 的核心技术难点，`CopilotConsumer` 实现了一个基于内存状态和协程句柄 (Task Handle) 的状态机。

1.  **连接与初始化:** Client 连接时，初始状态 `self.current_mode = 'idle'`，后台不启动任何循环任务 (`self.agent_task = None`)。
2.  **切换到 Copilot 模式:** 接收到前端的 `mode='copilot'` 和 `action='start'` 指令时，系统判断当前是否有运行的 loop 任务，如果没有，则通过 `asyncio.create_task(self.run_agent_loop())` 启动自动监控流。
3.  **切换到 Ask 模式:** 接收到 `mode='ask'` 时，系统立即更新 `self.current_mode = 'ask'`。关键的一步是：如果发现 `self.agent_task` 仍在运行，**程序立即调用 `self.agent_task.cancel()` 终止自动化无限循环**。随后通过 `asyncio.create_task(self.handle_ask_request(message))` 开启问答并发处理。
4.  **互斥保障:** 这种协程管控机制确保了在同一 WebSocket 连接中，AI 代理要么在其专注区域进行自动扫描与干预，要么专注回答人类问题，杜绝了两种模式争抢系统资源、干扰前端 UI 的隐患。

### 2.3 Copilot 模式的自动化工作流 (The Agent Loop)

当模式为 Copilot 时，Agent 的工作流是一个无限循环（直到 WebSocket 模式切换或断开），其核心状态机闭环如下：

1.  **全局扫描 (Global Scan):**
    -   *行为:* 每隔 3 秒读取一次 `AnomalyAlertLog` 表。**扫描策略**：寻找 `is_handled=False` 且符合以下任一条件的记录：
        -   **物理硬报警**：类型为 `HIGH_CURRENT`, `HIGH_POWER` 等。
        -   **AI 软报警 (Soft Signal)**：类型为 `AI_SOFT_SIGNAL`，且 `anomaly_score > 0.75`。
    -   *意图:* 即使物理指标尚未爆表，只要 AI 持续判定的风险值超过 0.75，Agent 也会将其选定为干预标靶，实现真正的“预见性维护”。
2.  **锁定目标 (Lock Target):**
    -   *行为:* 获取异常设备 (Device) 和对应的警报记录。将设备名称和风险值推送至前端终端。
3.  **安全停机 (Safety Shutdown - Action):**
    -   *行为:* 调用 ORM 将目标设备的 `current_status` 强制修改为 `Stopped`。立即物理级介入，防止刀具损毁或生产事故。
4.  **数据快照采集 (Data Harvesting):**
    -   *行为:* 提取该设备在异常发生前后的最近 10 条真实传感器数据，为 LLM 提供精准数字孪生上下文。
5.  **AI 智能诊断 (AI Diagnosis):**
    -   *行为:* 组装系统 Prompt 和采集到的上下文数据，通过 API 发送给模型。模型扮演专家提炼简短诊断反馈推至终端。
    -   **联动增强**: 诊断结论将同步持久化至数据库 `DeviceInfo.maintenance_advice` 字段。
6.  **安全恢复 (Recovery):**
    -   *行为:* 模拟维修干预（软复位），重置特征组 `current_group_id`，警报归档恢复 `Running` 状态。
7.  **系统重置与重绘 (Reset & Refresh):**
    -   *行为:* 向前端下发 `refresh` 指令重新拉取 API，保证底层图表与状态机同步。

### 2.4 Ask 模式的结构化 Data-Retrieval (Function Calling)

Ask 模式支持基于“三次握手”的多轮会话，以实现对实时数据库的安全穿透：

1.  **第一轮：意图识别 (Identify Intent)**: LLM 检测到用户意图涉及实时数据，返回 `tool_calls`。
2.  **第二轮：数据穿透 (Data Penetration)**: 服务端解析工具调用，通过 Django ORM 提取真实的传感器快照。
3.  **第三轮：归纳总结 (Summarization)**: 将所有背景（含 RAG 文档片段）与工具返回的数据打包发送给 LLM，生成最终回复。

### 2.5 核心代码模块映射 (Implementation Modules)

| 模块 / 文件 | 主要承担的职责 |
|---|---|
| `monitor/consumers.py` | **状态机核心与 WebSocket 总线**：维护 `agent_task` 协程生命周期，主导模式互斥。 |
| `monitor/services/agent_service.py` | **Agent 大脑**：整合 RAG 与 Tool-use，负责多轮对话逻辑与提示词注入。 |
| `monitor/services/knowledge_service.py` | **RAG 引擎**：负责本地 Markdown 文件的扫描、FAISS 向量检索及 Embedding 生成。 |
| `monitor/management/commands/build_rag_index.py` | **索引工具**：手动触发离线索引构建任务的任务。 |
| `monitor/templates/monitor/dashboard.html` | **UI 适配**：Ask 模式悬浮窗交互与 Copilot Terminal UI。 |

---

## 3. 语义化 RAG 架构设计 (V3.5.0 新增)

为了解决 Agent 无法回答非结构化知识（如：技术手册、排查规范）的问题，系统引入了基于向量检索的深度 RAG 架构。

### 3.1 核心技术栈
- **向量数据库 (Vector Store):** 采用 **FAISS (IndexFlatL2)**，实现低延迟的向量匹配。
- **嵌入模型 (Embedding):** 阿里云 **`text-embedding-v2`**，提供 1536 维度的工业语义空间。
- **文档范围:** 递归扫描 `Document/` 目录下的所有 **.md** 文件。

### 3.2 离线处理流水线 (The Ingestion Flow)
1. **清理与预处理**: 去除 Markdown 格式干扰，保留核心文本内容。
2. **切片 (Chunking)**: 块大小 512，重叠 50 字符，确保切片边界语义不丢失。
3. **向量化**: 调用 Embedding API 将切片转化为高维向量，并存储至 `index.faiss`。

### 3.3 实时检索流程 (Dual-Mode Integration)
系统在 `AgentService` 中为两种模式均集成了语义搜索逻辑：
- **Ask 模式 (交互式)**：用户的每次提问均会并发触发一次语义搜索。
- **Copilot 模式 (自动化)**：在 `run_diagnosis_flow` 中，系统自动检索本地故障处理规范与 SOP。
- **处理方式**: 检索 Top-3 的相关片段。如果 Score 匹配度高，则将片段注入 System Prompt 的知识背景区。
- **混合注入示例**: `system_prompt = "已知背景知识：{chunks}\n实时设备数据：{tool_resp}\n请根据以上信息回答..."`

---

## 4. 关键技术点回顾

### 4.1 线程池解决同步阻塞
利用 `loop.run_in_executor(None, ...)` 包裹同步的 OpenAI 和 FAISS 操作，防止 ASGI Worker 阻塞。

### 4.2 预见性干预：AI 软报警机制
仿真引擎每 3 秒生成一次概率。若 $\text{prob} > 0.75$，静默插入 `AI_SOFT_SIGNAL` 报警，引导 Agent 提前锁定风险源。

## 5. 扩展性探讨与未来建议 (V4.0 Outlook)

1.  **深入 ReAct (Reasoning and Acting) 模式:** 将 Copilot 的硬编码干预流声明成 Tools，赋予 LLM 自主决策权。
2.  **长下文对话记忆 (Session Memory):** 引入 Redis 存储会话历史。
3.  **外部知识扩展**: 接入互联网 API 实时搜索行业最新切削标准。
