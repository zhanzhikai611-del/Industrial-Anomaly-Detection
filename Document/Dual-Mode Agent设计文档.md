# 工业生产数据异常预警系统 —— Dual-Mode AI Agent 深度设计文档 (V3.5.0)

## 1. 概述：双模智能体架构
本项目设计了 **Ask (交互式助手)** 与 **Copilot (自主式巡检)** 两种工作模式。这种双模设计旨在兼顾“人工决策辅助”与“自动化应急避险”的双重需求。

---

## 2. 模式一：Ask 模式 (交互式 AI 助手)
**Ask 模式** 运行在前端对话框中，用于响应用户的主动查询。它通过 **RAG (检索增强生成)** 与 **Tool-use (工具调用)** 的结合，提供具备实时数据支持的专业回答。

### 2.1 业务流程逻辑
1.  **用户输入**: 用户提问，例如：“CNC-01 的健康状况如何？”
2.  **语义检索 (RAG)**: 调用 `knowledge_service.search` 检索本地标准作业程序 (SOP) 和历史故障文档，获取背景知识。
3.  **工具决策**: LLM (Qwen 3.5 Flash) 接收提示词，判断是否需要调用工具。
4.  **数据抓取 (Tool Calling)**: 若需要实时数据，Agent 调用 `get_device_recent_data` 函数，从数据库中提取该设备的最新传感流水和报警记录。
5.  **融合回复**: Agent 将“检索到的知识”与“抓取到的数据”结合，生成最终回复。

### 2.2 关键代码实现
*   **入口函数**: `ask_copilot(user_input)`
*   **工具函数**: `get_device_recent_data(device_names)`
*   **知识库接口**: `knowledge_service.search(query)`

---

## 3. 模式二：Copilot 模式 (自主巡检智能体)
**Copilot 模式** 是系统的“自动驾驶仪”。它作为一个后台异步进程常驻运行，负责 24/7 的设备状态监控与紧急故障干预。

### 3.1 核心业务流程 (Autonomous Loop)
Copilot 的运行逻辑是一个严密的 **“感知-决策-执行”** 闭环：

1.  **定时扫描 (Sense)**: `run_autonomous_loop` 每隔 3-5 秒执行一次扫描。
2.  **风险锁定 (Identify)**: 
    *   调用 `get_highest_risk_device()`。
    *   逻辑：寻找最近 5 分钟内产生的、风险分数 `anomaly_score > 0.75` 且当前状态为 `Running` 的设备。
    *   **原子级锁定**: 一旦锁定设备，立即将该设备所有未处理报警标记为 `is_handled=True`，防止逻辑重复触发。
3.  **紧急避险 (Execute - Step 1)**: 
    *   调用 `set_device_status(id, 'Stopped')`。
    *   逻辑：在 AI 介入分析前，优先下发“停机”指令，模拟工业现场的紧急泄压或停机操作，防止故障扩大。
4.  **深度诊断 (Analyze)**: 
    *   调用 `get_harvest_data(id)` 提取停机前 10 条核心传感器数据切片。
    *   调用 `run_diagnosis_flow()`。
    *   **RAG 融合诊断**: 结合设备名检索本地 SOP，利用 LLM 对传感切片进行根因分析，并将分析建议通过 `save_diagnosis` 写入数据库。
5.  **自动恢复 (Execute - Step 2)**: 
    *   调用 `perform_recovery(id)`。
    *   逻辑：系统将设备重置为 `G1 (新机组)`，状态恢复为 `Running`。这模拟了 AI 完成软件级重置或判定为暂态异常后的全自动复位过程。

### 3.2 关键代码实现
*   **主循环函数**: `run_autonomous_loop(log_cb, should_continue)`
*   **锁定函数**: `get_highest_risk_device()`
*   **诊断函数**: `run_diagnosis_flow(device_id, device_name, harvest_data, log_cb)`
*   **复位函数**: `perform_recovery(device_id)`

---

## 4. 技术优势与总结
1.  **明确解耦**: Ask 模式负责“知”，Copilot 模式负责“行”。
2.  **安全优先**: Copilot 在诊断前强制停机，体现了“工业安全第一”的设计准则。
3.  **知识赋能**: 通过 RAG 架构，Agent 的诊断能力随本地文档库的丰富而自动增强，无需重新训练模型。

---

## 5. AI 实现细节与安全性保障

### 5.1 增强型 RAG 检索技术栈
为了确保诊断的严肃性与准确性，后端采用了以下配置：
*   **向量化方案**：采用 `shibing624/text2vec-base-chinese` 预训练模型，将 SOP 文档转化为 768 维向量，确保语义捕捉的精准度。
*   **分片逻辑**：按照 500 Tokens/Chunk 进行语义分片，并设置 10% 的上下文重叠度，确保检索到的知识片段具备完整语境。
*   **检索过滤**：仅当余弦相似度（Cosine Similarity）超过 0.7 时才将其存入 Prompt 的 Context 区域，彻底规避“模型幻觉”问题。

### 5.2 确定性 Prompt 工程
Agent 的输出遵循严格的结构化协议：
*   **角色锚定**：System Prompt 强制 Agent 扮演“资深 CNC 维保专家”，并要求其在回复中必须对比“实时传感器数据”与“SOP 标准阈值”。
*   **引用溯源**：强制要求在回复末尾附带 `Reference: [Document_Name]`，实现 AI 建议的可审计性，增强工程师的信任度。

### 5.3 工业安全红线机制 (Safety Guardrails)
*   **状态互斥锁**：当 Copilot 锁定设备进行 `Execute - Step 1 (强制停机)` 时，系统会自动对该设备打上 `AI_LOCK` 标记，禁止任何手动复位操作，直至诊断完成。
*   **硬恢复保障**：系统保留了基于物理规则的传统预警逻辑作为 AI 模型的冗余备份，即使 LLM 响应超时，物理报警系统依然能独立下发停机指令。

---
*Updated: 2026-05-13*
