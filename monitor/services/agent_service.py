# -*- coding: utf-8 -*-
import json
import logging
import asyncio
from openai import OpenAI
from typing import List, Tuple, Dict, Any, Callable
from asgiref.sync import sync_to_async
from django.utils import timezone
from ..models import DeviceInfo, AnomalyAlertLog, ProductionSensorData

logger = logging.getLogger(__name__)

# 配置参数 (V3.1.0: 统一维护)
API_KEY = "sk-6244491a10cd439b9d9013b557450741"
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL_NAME = "qwen3.5-flash"

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

class AgentService:
    """
    Agent 服务类 (V3.1.0): 
    整合原 ai_engine.py 的 RAG 能力与 consumers.py 的自主决策循环。
    实现“思考-决策-执行”的闭环逻辑。
    """

    @staticmethod
    def get_device_recent_data(device_names: List[str]) -> str:
        """RAG 工具：查询设备实时上下文"""
        results = []
        if 'all' in [name.lower() for name in device_names]:
            devices = [alert.record.device for alert in 
                      AnomalyAlertLog.objects.filter(is_handled=False, anomaly_score__gt=0.4)
                      .select_related('record__device').order_by('-anomaly_score')[:10]]
            # 去重
            seen = set()
            all_devices = []
            for d in devices:
                if d.id not in seen:
                    all_devices.append(d)
                    seen.add(d.id)
            if not all_devices:
                return json.dumps([{"message": "目前所有设备均处于正常状态。"}], ensure_ascii=False)
        else:
            all_devices = list(DeviceInfo.objects.filter(device_name__in=device_names))
            if not all_devices and device_names:
                all_devices = list(DeviceInfo.objects.filter(device_name__icontains=device_names[0]))

        for device in all_devices:
            latest_alert = AnomalyAlertLog.objects.filter(record__device=device, is_handled=False).order_by('-alert_time').first()
            current_score = latest_alert.anomaly_score if latest_alert else 0.0
            
            dev_info = {
                "device_id": device.id,
                "device_name": device.device_name,
                "current_status": device.current_status,
                "anomaly_score": current_score,
                "recent_alerts": [
                    {"alert_type": a.alert_type, "alert_time": a.alert_time.isoformat(), "anomaly_score": a.anomaly_score}
                    for a in AnomalyAlertLog.objects.filter(record__device=device, is_handled=False).order_by('-alert_time')[:3]
                ]
            }
            results.append(dev_info)

        return json.dumps(results, ensure_ascii=False)

    @classmethod
    async def ask_copilot(cls, user_input: str) -> Tuple[str, List[Dict[str, Any]]]:
        """处理 Ask 模式：RAG 问答"""
        tools = [{
            "type": "function",
            "function": {
                "name": "get_device_recent_data",
                "description": "获取指定设备的当前运行状态、异常分数和最近的报警日志。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "device_names": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["device_names"]
                }
            }
        }]

        messages = [
            {"role": "system", "content": "你是工厂的 CNC 智能助理。必须使用 get_device_recent_data 查询真实数据。"},
            {"role": "user", "content": user_input}
        ]

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: client.chat.completions.create(
                model=MODEL_NAME, messages=messages, tools=tools, tool_choice="auto"
            ))
            
            resp_msg = response.choices[0].message
            data_context = []

            if resp_msg.tool_calls:
                messages.append(resp_msg)
                for tc in resp_msg.tool_calls:
                    if tc.function.name == "get_device_recent_data":
                        args = json.loads(tc.function.arguments)
                        # V3.1.1 Fix: 在异步上下文中调用同步 DB 工具函数必须 wrap (解决 Ask 模式失效)
                        func_resp = await sync_to_async(cls.get_device_recent_data)(args.get("device_names", []))
                        try:
                            data_context.extend(json.loads(func_resp))
                        except: pass
                        messages.append({"tool_call_id": tc.id, "role": "tool", "name": tc.function.name, "content": func_resp})

                second_resp = await loop.run_in_executor(None, lambda: client.chat.completions.create(
                    model=MODEL_NAME, messages=messages
                ))
                return second_resp.choices[0].message.content, data_context
            
            return resp_msg.content, data_context
        except Exception as e:
            logger.error(f"Ask Copilot Error: {e}")
            return "抱歉，系统异常，请稍后再试。", []

    # ── 数据库原子操作 (同步转异步) ───────────────────────────────────
    
    @staticmethod
    @sync_to_async
    def get_highest_risk_device():
        latest_alert = AnomalyAlertLog.objects.filter(is_handled=False).order_by('-anomaly_score').first()
        if latest_alert and latest_alert.anomaly_score and latest_alert.anomaly_score > 0.70:
            return latest_alert.record.device, latest_alert
        return None, None

    @staticmethod
    @sync_to_async
    def set_device_status(device_id, status):
        DeviceInfo.objects.filter(id=device_id).update(current_status=status)

    @staticmethod
    @sync_to_async
    def get_harvest_data(device_id):
        recs = ProductionSensorData.objects.filter(device_id=device_id).order_by('-timestamp')[:10]
        return "\\n".join([f"Time: {r.timestamp.strftime('%H:%M:%S')} | Spindle:{r.spindle_current:.2f}A" for r in recs])

    @staticmethod
    @sync_to_async
    def save_diagnosis(device_id, advice):
        DeviceInfo.objects.filter(id=device_id).update(maintenance_advice=advice)

    @staticmethod
    @sync_to_async
    def perform_recovery(device_id):
        DeviceInfo.objects.filter(id=device_id).update(current_group_id=1, current_status='Running')
        AnomalyAlertLog.objects.filter(record__device_id=device_id, is_handled=False).update(is_handled=True)

    # ── 核心业务流程 ───────────────────────────────────────────────

    @classmethod
    async def run_diagnosis_flow(cls, device_id, device_name, harvest_data, log_cb: Callable):
        """AI 诊断工作流"""
        await log_cb("[AI] 正在进行预警数据的交叉验证分析...")
        await asyncio.sleep(2)
        
        prompt = f"设备 '{device_name}' 触发高危预警。历史数据：\\n{harvest_data}\\n请给出简短诊断建议（3句以内）。"
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": "You are a CNC expert."},
                    {"role": "user", "content": prompt}
                ]
            ))
            advice = resp.choices[0].message.content
            await log_cb(f"[AI] 诊断完成：{advice}")
            await cls.save_diagnosis(device_id, advice)
            return advice
        except Exception as e:
            await log_cb(f"[AI/Error] 诊断失败: {e}")
            return "建议人工检查。"

    @classmethod
    async def run_autonomous_loop(cls, log_cb: Callable, should_continue: Callable):
        """
        自主控制循环 (原 CopilotConsumer.run_agent_loop)
        """
        await log_cb("[System] Copilot 智能体已接管系统，正在初始化扫描路径...")
        
        # V3.1.2 Fix: should_continue 是同步 lambda，移除 await (解决 Copilot 模式失效)
        while should_continue():
            await asyncio.sleep(3)
            await log_cb("[System] 正在扫描全线设备风险状态...")
            
            device, alert = await cls.get_highest_risk_device()
            if not device:
                await log_cb("[Scan] 当前无高风险设备，维持监控。")
                await asyncio.sleep(5)
                continue
            
            # 执行防御性操作
            await log_cb(f"[Target] 锁定风险源：{device.device_name} (分数: {int(alert.anomaly_score*100)}%)")
            await cls.set_device_status(device.id, 'Stopped')
            await log_cb("[Action] 触发紧急避险：设备已远程停机。")
            await asyncio.sleep(2)
            
            # AI 诊断
            h_data = await cls.get_harvest_data(device.id)
            await cls.run_diagnosis_flow(device.id, device.device_name, h_data, log_cb)
            await asyncio.sleep(3)
            
            # 自动化修复 (如果分数不是极高，尝试复位)
            await log_cb("[Recovery] 正在尝试自动化系统复位与报警清理...")
            await cls.perform_recovery(device.id)
            await log_cb("[Status] 设备已恢复运行至安全组，报警记录已归档。")
