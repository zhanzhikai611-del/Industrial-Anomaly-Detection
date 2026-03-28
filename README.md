# 基于机器学习的 CNC 工业生产数据异常预警系统

> **当前版本**：V3.4.0 · AI Score Persistence & Real-time Hardening Edition (2026-03-28)

## 1. 项目简介

本项目是一套面向工业 4.0 的智能化预警系统，旨在解决 CNC 铣床加工过程中的刀具磨损检测问题。系统基于**密歇根大学真实 CNC 数据集**，利用逻辑回归（Logistic Regression）模型实现 25 台设备的实时健康监测，并提供完整的用户鉴权与角色权限控制（RBAC）体系。

## 2. 核心功能

- **单设备深度监控详情 (V3.2.0) [✨最新亮点✨]**：
    - **全景 3D 数字孪生**：针对单个机台的高保真 3D 模型渲染，支持 **OrbitControls** 自由视角旋转、缩放与对齐。
    - **机型自适应渲染**：智能识别 CNC01/LAT02/TML03 型号并执行专用几何校准（如 LAT 模型 18x 缩放）。
    - **动态配色指标卡**：实时同步“AI 风险”与“实时效率”，依据分级阈值自动变换 UI 告警色（红/黄/绿）。
    - **操作溯源流水**：整合人工操作、Copilot 指令与 AI 诊断报告于统一时间线。
- **双模态 AI 交互中心 (V2.1.0)**：
    - **Ask 模式 (交互态)**：基于 Function Calling 与 RAG。支持语义化询问，大模型（qwen-max）直接调取数据库快照进行专业回复。
    - **Copilot 模式 (自动态)**：全时段扫描异常，自动执行安全停机与诊断。
- **3D 数字孪生工厂 (V2.2.1) [⚠️已弃用]**：
    - **等轴测空间驾驶舱**：基于 Three.js 实现 5x5 设备阵列，该功能已收拢至“单机详情页”。
- **SaaS 化极简看板**：提供全厂 OEE、生产产出及设备状态分布的宏观监控。
- **AI 故障预警**：实时计算每台设备的磨损概率，并根据风险值（Anomaly Score）按梯度分级展示。
- **深度钻取诊断**：支持点击单台设备查看"主轴电流 vs 故障概率"的双轴时序波形图。
- **传感黑匣子**：全量记录 25 台设备的高频传感流水（电流、功率、速度等），实现故障溯源。
- **身份认证 & RBAC**：完整的登录/登出流程，三级角色权限（Admin / Engineer / Operator）。
- **数据滚动清理**：实时流守护进程自动保留最近 1 天数据，防止数据库无限膨胀。
- **V5 脉冲产量引擎**：采用 `yield_buffer` 累加器技术，完美解决高频采样下的产量截断丢失问题。

## 3. 技术架构

| 层级 | 技术栈 |
|------|--------|
| 后端 | Python 3.12 / Django 4.x / Django Channels (ASGI) / Daphne |
| 数据库 | MySQL 8.x / **Redis 7.x (性能缓存层)** |
| 前端 | HTMX + Alpine.js / Three.js v145 / ECharts 5.5 / WebSocket |
| AI 算法 | Logistic Regression 二分类检测 / Qwen-3.5 (诊断与咨询) |
| 仿真引擎 | Django Management Command / **Redis Atomic Counters** |

## 4. 首次运行完整指南

> ⚠️ **全新环境必须按顺序执行以下所有步骤**，跳过任何一步将导致系统无法正常启动。

### Step 1 — 环境准备

```bash
# 克隆项目后，创建并激活虚拟环境
python3 -m venv venv
source venv/bin/activate          # macOS / Linux
# venv\Scripts\activate           # Windows

# 安装依赖
pip install -r requirements.txt
```

### Step 2 — 数据库初始化

确保 MySQL 已启动，并已创建数据库：

```sql
CREATE DATABASE industrial_warning_db CHARACTER SET utf8mb4;
```

执行 Django 数据库迁移：

```bash
venv/bin/python manage.py migrate
```

### Step 3 — 创建初始管理员账号

```bash
venv/bin/python manage.py createsuperuser --username admin
# 密码请输入：baby0611
```

> 首次登录时，系统会自动为 `admin` 账号创建 **系统管理员（Admin）** 角色的 UserProfile，无需额外操作。

### Step 4 — 初始化设备与历史仿真数据（仅首次）

```bash
venv/bin/python core_scripts/simulate_real_cnc_data.py
```

此脚本将：
1. 检测库中已有设备，若低于 25 台则会清空数据重置环境。
2. 在 `device_info` 表中创建 **25 台 CNC 设备**，并按生命周期分组（Group 1-5）分配 `current_group_id`。
3. 生成**过去 7 天**的历史传感流水（约 252,000 条）以铺陈图表历史序列。
4. 历史数据初始化完成后，随后会顺便切入实时生成循环（但不含滚动清理机制）。推荐初始化完成后 `Ctrl+C` 退出，以专门的守护进程替代。

### Step 5 — 启动 Django ASGI 服务器

新开一个终端窗口（由于 V2.0 引入了 WebSockets 与 Copilot 长连接，请勿使用原本的 `runserver`，并请确保已正确配置 `DASHSCOPE_API_KEY` 环境变量指向千问大模型 API 密钥）：

```bash
venv/bin/daphne -p 8000 IndustrialWarningSystem.asgi:application
```

### Step 6 — 启动实时数据流守护进程（⚠️ 必须单独运行此命令）

再开一个终端窗口：

```bash
venv/bin/python manage.py run_realtime_stream
```

> **重要**：强烈建议使用 `manage.py run_realtime_stream` 作为日常 7x24 进行实时压测流生成的守护进程。
> 尽管最新的 `core_scripts/simulate_real_cnc_data.py` 的仿真机制与它表现一致，但后者缺乏**实时滚动定时清理**的手段安全机制。`run_realtime_stream` 持有 `RETENTION_DAYS = 1`，将有效避免数据库由于持续堆积 3 秒一条的流水而不受控制地爆满。

### Step 7 — 访问系统

打开浏览器访问：**http://127.0.0.1:8000**

系统将自动跳转到登录页，使用以下凭据登录：

| 字段 | 值 |
|------|----|
| 用户名 | `admin` |
| 密码 | `baby0611` |

---

## 5. 角色权限说明（RBAC）

| 页面 / 功能 | 系统管理员 (Admin) | 工程师 (Engineer) | 操作员 (Operator) |
|------------|:-----------------:|:----------------:|:----------------:|
| 仪表盘 | ✅ | ✅ | ✅ |
| 设备监控（读） | ✅ | ✅ | ✅ |
| 设备监控（处理/重置） | ✅ | ✅ | ❌ 只读 |
| 事件中心 | ✅ | ✅ | ✅ |
| 账号管理 | ✅ | ❌ 隐藏 | ❌ 隐藏 |
| 系统设置 | ✅ | ❌ 隐藏 | ❌ 隐藏 |

新账号通过"账号管理"页面（Admin 专属）创建，支持角色分配、禁用/启用、密码重置。

---

## 6. 日常重启流程

系统已配置启动自检（`apps.py`），重启 Django 服务器后将自动验证并修复设备分组梯度，无需手动干预。

```bash
# 终端 1：Web 服务器 (ASGI)
venv/bin/daphne -p 8000 IndustrialWarningSystem.asgi:application

# 终端 2：实时数据流守护进程（⚠️ 必须使用此命令，勿用 core_scripts/simulate_real_cnc_data.py）
venv/bin/python manage.py run_realtime_stream
```

> | 数据量防爆与滚动清理 (15分钟频次) | ❌ 缺失 (持续运行会导致表过大) | ✅ 支持 (最近 1 天保留清理上限) |

---

## 7. 数据维护与重置指南

当需要清除所有历史数据进行算法重新验证，或系统数据出现逻辑污染时，请按顺序执行：

### Step 1 — 停止所有运行进程
按下 `Ctrl+C` 停止 `daphne` 和 `run_realtime_stream`。

### Step 2 — 执行重置脚本
运行以下命令，该脚本将一键执行：**清空数据库记录**、**重置 Redis 计数器**、**重新初始化 25 台设备**及 **7 天历史流数据**。

```bash
venv/bin/python core_scripts/reset_system.py
```

### Step 3 — 重新验证
重置完成后，重启 Web 服务与实时流进程，此时单机指标将完全基于最新且干净的物理环境进行展示。

---

## 8. 版本更新日志

  ### V3.4.0 — Algorithm Excellence & Real-time Hardening (2026-03-28) [✨最新✨]
- **[算法中心] 工业级预测精度重大跨越**：参考 IJSEM 2025 Attention-based 论文，通过深度特征工程压榨 LR 模型性能：
    - **F1-Score 飙升**：通过引入 Feedrate 与 Clamp_Pressure 元数据，实现 **Precision（精确率）从 23% 到 35%+** 的飞跃。
    - **100% 特征物理对齐**：根治了在线推断 `std` 缺失的顽疾，召回率 (Recall) 稳定突破 **72%**。
    - **多尺度时序建模**：引入 1min/5min 双窗口滑动统计特征，低功耗模拟 LSTM 对切削震颤信号的感知能力。
- **[仿真补丁] 数据链路 1:1 闭环**：
    - **持久化修复**：解决了实时流守护进程在落库前丢失 AI 分数的 Bug，详情页与 Dashboard 实现物理级数据镜像同步。
    - **稳定性增强**：引入对象构造强制占位与 `.save()` 物理双写机制，支撑 24/7 连续、不失真传感流生成。

  ### V3.3.1 — Performance & Physics Synchronization Edition (2026-03-28)
- **[重构] 核心指标 P (Performance) 算法**：采用「流水潜力对齐逻辑」。直接对齐 600 条记录的实产与标准能力，彻底消除采样边界偏差带来的数值飘移（告别虚高 100%）。
- **[优化] 核心指标 A (Availability) 算法**：由瞬时快照升级为「时间加权移动平均」。通过对最近 1 分钟内的停机记录进行物理闭环求和，使看板具备真正的“故障记忆”能力。
- **[系统] 接入 Redis 高性能内核**：
    - **实时计数器**：实现产量指标的内存级原子累加，大幅降低 MySQL 读取压力。
    - **看板级缓存**：对统计密集型接口执行 60s 缓存，保障高并发下的响应速度。
- **[修复] 仿真单位冲突**：修正了仿真引擎中 downtime 与 loading_time 的单位（分钟级统一），根治了可用性指标显示为负数的 Bug。

### V3.2.0 — Elite Diagnostic Edition (2026-03-27)
- **[新增] 环境感知 3D 详情页**：正式上线单台设备的 3D 数字孪生视图，替代旧有的 5x5 全景展示，提升诊断精度。

  ### V3.1.0 — Modern Fundamentalism Edition (2026-03-17)
 - **[重构] 前端架构升级**：引入 **HTMX + Alpine.js** 组合，实现原生的现代交互体验。
 - **[优化] 系统自检中心**：设置页面全面重构，支持实时统计真实数据库传感器流水。
 - **[修复] 时区统计偏差**：修正了今日传感流水在特定数据库环境下显示 0 条的 Bug。
 - **[优化] 视觉标识**：全局统一版本号标识为 V3.1.0。

 ### V2.2.1 — Digital Twin Elite Edition (2026-03-12)
 - **[新增] 3D 数字孪生视图**：正式上线基于 Three.js 的数字工厂页面。
 - **[新增] 智慧脉冲系统**：实现了根据设备实时 OEE 动态调整呼吸频率的视觉特效。
 - **[新增] 端到端监控联动**：实现监控中心录入的维修建议在 3D 检查器中实时投影展示。
 - **[优化] 全局设计规范**：采用“旗舰工业灰” (Elite Industrial Grey) 调色盘，定义了标准的 5x5 正交相机坐标系。

  ### V2.1.0 — Dual-Mode Agent Vision (2026-03-07)
 - **[新增] 双模态切换引擎**：在 Dashboard 实现 Copilot (自动闭环) 与 Ask (即时问答) 模式的秒级无缝切换与协程隔离。
 - **[新增] Ask 手动咨询模式**：接入 Qwen-max，支持通过对话形式实时查询设备状态及异常原因。
 - **[优化] 视觉系统升级**：AI Agent 入口按钮更换为 Split Button 样式。更新 Copilot/Ask 专属 SVG 图标。主圆角调整为更为利落的 15px。
 - **[优化] 系统设置增强**：增加外部 AI 模型接入显示；精简数据库统计信息，去除非核心指标。
 - **[修复] 逻辑鲁棒性**：
     - 实现了 AI 交互入口的**条件渲染逻辑**（仅在 Dashboard 页面显示），根治非看板页面 JS 报错。
     - 解决了 Copilot 终端中 `$` 符号重定义的显示冲突问题。
 
 ---
 
 ## 8. 数据管理

| 项目 | 说明 |
|------|------|
| 数据保留策略 | 实时流守护进程每 15 分钟自动清理超过 **1 天** 的旧传感记录与报警 |
| 系统一键重置 | `venv/bin/python core_scripts/reset_system.py`（清空 DB+Redis 并重建环境）|
| 修复特定分组 | `venv/bin/python core_scripts/fix_db_groups.py`（仅恢复 1-5 梯度，不清除数据）|
