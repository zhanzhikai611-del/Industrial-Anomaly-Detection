# 基于机器学习的 CNC 工业生产数据异常预警系统

> **当前版本**：V2.0.0 · CNC Cloud Industrial Warning System

## 1. 项目简介

本项目是一套面向工业 4.0 的智能化预警系统，旨在解决 CNC 铣床加工过程中的刀具磨损检测问题。系统基于**密歇根大学真实 CNC 数据集**，利用逻辑回归（Logistic Regression）模型实现 25 台设备的实时健康监测，并提供完整的用户鉴权与角色权限控制（RBAC）体系。

## 2. 核心功能

- **SaaS 化极简看板**：提供全厂 OEE、生产产出及设备状态分布的宏观监控。
- **Copilot 智能体模式 (V2.0)**：引入 AI Agent，实时监控设备状态，在发现异常时自动中断生产、抓取传感数据快照并调用大语言模型（Qwen）进行智能诊断和软复位，实现零人工干预的自动化异常闭环处理。
- **AI 故障预警**：实时计算每台设备的磨损概率，并根据风险值（Anomaly Score）按梯度分级展示。
- **深度钻取诊断**：支持点击单台设备查看"主轴电流 vs 故障概率"的双轴时序波形图。
- **传感黑匣子**：全量记录 25 台设备的高频传感流水（电流、功率、速度等），实现故障溯源。
- **身份认证 & RBAC**：完整的登录/登出流程，三级角色权限（Admin / Engineer / Operator）。
- **数据滚动清理**：实时流守护进程自动保留最近 1 天数据，防止数据库无限膨胀。

## 3. 技术架构

| 层级 | 技术栈 |
|------|--------|
| 后端 | Python 3.12 / Django 4.x / Django Channels (ASGI) / Daphne |
| 数据库 | MySQL 8.x（`industrial_warning_db`）|
| 前端 | Vanilla JS / ECharts 5.5.0 / Bootstrap 5 / WebSocket 实时终端交互 |
| AI 算法 | Logistic Regression (scikit-learn) 二分类检测 / Qwen 大模型 (Copilot诊断) |
| 仿真引擎 | Django Management Command（`run_realtime_stream`），每 3 秒生成 25 台设备数据 |

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
venv/bin/python simulate_real_cnc_data.py
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
> 尽管最新的 `simulate_real_cnc_data.py` 的仿真机制与它表现一致，但后者缺乏**实时滚动定时清理**的手段安全机制。`run_realtime_stream` 持有 `RETENTION_DAYS = 1`，将有效避免数据库由于持续堆积 3 秒一条的流水而不受控制地爆满。

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

# 终端 2：实时数据流守护进程（⚠️ 必须使用此命令，勿用 simulate_real_cnc_data.py）
venv/bin/python manage.py run_realtime_stream
```

> **两个测试数据脚本的核心差异对比：**
>
> | 对比项 | `simulate_real_cnc_data.py` | `manage.py run_realtime_stream` |
> |---|---|---|
> | 一次性初始化 7 天历史流 | ✅ 支持，适合冷启动 | ❌ 不支持 |
> | V5 实时脉冲计算引擎与级联预警 | ✅ 支持 | ✅ 支持 |
> | 支持维修回春与前端操作反向控制 | ✅ 支持 | ✅ 支持 |
> | 数据量防爆与滚动清理 (15分钟频次) | ❌ 缺失 (持续运行会导致表过大) | ✅ 支持 (最近 1 天保留清理上限) |

---

## 7. 数据管理

| 项目 | 说明 |
|------|------|
| 数据保留策略 | 实时流守护进程每 15 分钟自动清理超过 **1 天** 的旧传感记录与报警 |
| 手动修复分组 | `venv/bin/python fix_db_groups.py`（恢复 Group 1-5 梯度分布）|
| 清除并重建历史 | 重新运行 `simulate_real_cnc_data.py` 并在提示时选择清除 |
