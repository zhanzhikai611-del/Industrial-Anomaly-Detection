# CNC 工业生产异常实时预警系统 (AI-Driven I-IoT)

[![Version](https://img.shields.io/badge/version-V3.5.0-blue.svg)](https://github.com/your-repo)
[![Tech Stack](https://img.shields.io/badge/tech-Django%20|%20Redis%20|%20HTMX-orange.svg)]()
[![AI Model](https://img.shields.io/badge/AI-Logistic%20Regression%20|%20RAG-green.svg)]()

本系统是一套面向工业 4.0 的智能化预警平台，专注于 CNC 铣床加工过程中的**刀具磨损预测性维护 (PdM)**。系统深度融合了实时流计算、逻辑回归推断算法与 RAG (检索增强生成) 诊断技术。

---

## 📂 项目目录结构

```text
.
├── IndustrialWarningSystem/    # Django 项目配置 (Settings, ASGI/WSGI, URLs)
├── monitor/                    # 核心业务应用 (App)
│   ├── management/             # 自定义管理命令 (实时流、RAG索引构建)
│   ├── services/               # 服务层 (AI推断、缓存管理、RAG诊断、统计逻辑)
│   ├── models.py               # 数据库模型 (设备资产、传感流水、告警日志)
│   ├── templates/              # 前端模板 (HTMX 碎片与主页面)
│   └── static/                 # 静态资源 (CSS变量、Alpine.js 逻辑、Three.js 模型)
├── core_scripts/               # 核心任务脚本 (数据仿真引擎、模型训练、实验脚本)
├── ml_models/                  # 模型固化文件 (LR Model, Scaler, Threshold)
├── Document/                   # 完善的技术文档 (架构、算法、评估报告)
├── CNCData/                    # 原始数据集 (Michigan CNC Dataset)
├── ml_models/                  # 训练好的机器学习模型文件
├── manage.py                   # Django 入口脚本
└── requirements.txt            # 项目依赖清单
```

---

## 🏗️ 系统架构设计

系统采用了 **Service-Oriented MTV** 架构，实现了 Web 业务、高性能缓存与后台计算任务的解耦：

1.  **数据层 (Simulation Layer)**：由 `core_scripts` 驱动，模拟 25 台设备 7x24 小时的高频传感脉冲。
2.  **内核层 (AI Kernel)**：`ai_service` 实现基于 13 维特征工程的实时在线推断。
3.  **缓存层 (Redis Hybrid)**：采用 Redis Hash 存储设备快照，利用原子计数器维护全量 OEE 指标，响应延迟 < 2ms。
4.  **诊断层 (RAG Agent)**：基于 FAISS 向量库与大语言模型，将实时异常信号与本地技术手册结合。
5.  **展示层 (HDA Frontend)**：采用 HTMX + Alpine.js + Three.js 驱动，实现无刷新的 SPA 级工业大屏体验。

---

## 🚀 快速开始

### 1. 环境部署
```bash
# 初始化环境
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 数据库迁移
python manage.py migrate

# 创建管理员
python manage.py createsuperuser
```

### 2. 数据与知识库初始化 (必选)
```bash
# 1. 生成 25 台设备及 7 天仿真历史数据
python core_scripts/simulate_real_cnc_data.py

# 2. 构建 RAG 知识库向量索引 (赋能 Agent 检索 Document/ 目录)
python manage.py build_rag_index
```

### 3. 启动核心服务
1.  **Web 引擎 (ASGI)**: `daphne -p 8000 IndustrialWarningSystem.asgi:application`
2.  **实时数据流守护进程**: `python manage.py run_realtime_stream`

---

## 🛠️ 运维与系统排查

### 5.1 僵尸进程管理
工业级常驻进程若非正常关闭，可能导致 Redis 数据污染。
```bash
# 一键清理项目相关所有残留进程
pkill -f manage.py
pkill -f core_scripts
pkill -f daphne
```

### 5.2 数据重置与缓存清理
若发现看板数据与详情页不一致，请重置 Redis 快照缓存：
```bash
python manage.py shell -c "from django_redis import get_redis_connection; get_redis_connection('default').delete(':1:device:status:all')"
```

---

## 📜 版本更新摘要

- **V3.5.0 (2026-05-05)**：落地 RAG 架构，引入 FAISS 向量库，支持 AI 回答的引用溯源。
- **V3.4.0 (2026-03-28)**：引入多尺度特征工程，Recall 突破 72%，解决实时流同步 Bug。
- **V3.3.0 (2026-03-28)**：全量看板数据源切换至 Redis，修正 OEE 物理对齐算法。

---
© 2026 CNC Industrial Warning System. All Rights Reserved.
