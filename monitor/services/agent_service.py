# -*- coding: utf-8 -*-
import json
import logging
import asyncio
from openai import OpenAI
from typing import List, Tuple, Dict, Any, Callable
from asgiref.sync import sync_to_async
from datetime import timedelta
from django.utils import timezone
from ..models import DeviceInfo, AnomalyAlertLog, ProductionSensorData, SystemConfig
from .knowledge_service import knowledge_service

logger = logging.getLogger(__name__)

# 配置参数 (V3.5.0)
API_KEY = "sk-6244491a10cd439b9d9013b557450741"
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL_NAME = "qwen3.5-flash"

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

class AgentService:
    """
    Agent 服务类 (V3.5.0): 
    整合非结构化知识 RAG 与 结构化数据 Tool-use。
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
                ],
                "recent_sensor_data": [
                    {
                        "time": s.timestamp.strftime('%H:%M:%S'),
                        "current": s.spindle_current,
                        "power": s.spindle_power,
                        "feed_velocity": s.feed_velocity
                    }
                    for s in ProductionSensorData.objects.filter(device=device).order_by('-timestamp')[:20]
                ]
            }
            results.append(dev_info)

        return json.dumps(results, ensure_ascii=False)

    @classmethod
    async def ask_copilot(cls, user_input: str) -> Tuple[str, List[Dict[str, Any]]]:
        """处理 Ask 模式：RAG + Tool-use 混合问答"""
        
        # 1. 语义检索 (非结构化)
        knowledge_context = await sync_to_async(knowledge_service.search)(user_input)
        
        tools = [{
            "type": "function",
            "function": {
                "name": "get_device_recent_data",
                "description": "获取指定设备的当前运行状态、异常分数、报警日志以及最新20条传感器时序数据以进行深度诊断。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "device_names": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["device_names"]
                }
            }
        }]

        # 2. 增强 Prompt
        system_prompt = "你是工厂 CNC 智能助理。请优先结合【背景知识】和【实时工具数据】回答。"
        if knowledge_context:
            system_prompt += f"\n\n【背景知识检索自本地文档库】：\n{knowledge_context}"

        messages = [
            {"role": "system", "content": system_prompt},
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
        time_limit = timezone.now() - timedelta(minutes=5)
        # [V3.5.3] 扫描逻辑升级：仅寻找状态为 Running 的高风险设备。
        # 理由：如果设备已经是 Stopped/Idle，说明 Copilot 或人工已经在处理，无需重复锁定。
        latest_alert = AnomalyAlertLog.objects.filter(
            is_handled=False, 
            alert_time__gte=time_limit,
            record__device__current_status='Running'
        ).order_by('-anomaly_score').first()
        
        cfg = SystemConfig.objects.first()
        thresh = cfg.ai_alert_threshold if cfg else 0.75

        if latest_alert and latest_alert.anomaly_score and latest_alert.anomaly_score >= thresh:
            dev = latest_alert.record.device
            # [V3.5.3] 原子级清理：一旦锁定该设备，立即将该设备所有积压的未处理报警归档。
            # 这能有效解决 AI 诊断期间产生“幽灵报警”导致的重复触发问题。
            AnomalyAlertLog.objects.filter(record__device_id=dev.id, is_handled=False).update(is_handled=True)
            return dev, latest_alert
        return None, None

    @staticmethod
    @sync_to_async
    def set_device_status(device_id, status):
        DeviceInfo.objects.filter(id=device_id).update(current_status=status)

    @staticmethod
    @sync_to_async
    def get_harvest_data(device_id):
        recs = ProductionSensorData.objects.filter(device_id=device_id).order_by('-timestamp')[:10]
        return "\n".join([f"Time: {r.timestamp.strftime('%H:%M:%S')} | Spindle:{r.spindle_current:.2f}A" for r in recs])

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
        """AI 诊断工作流 (V3.5.0: 全面接入 RAG 知识检索)"""
        await log_cb("[AI] 正在检索本地故障处理标准与 SOP 说明...")
        
        # 1. 语义检索：基于设备名和当前异常情况获取文档知识
        knowledge_context = await sync_to_async(knowledge_service.search)(f"{device_name} 故障诊断与处理规范")
        
        await log_cb("[AI] 正在进行预警数据的交叉验证分析...")
        await asyncio.sleep(1)
        
        # 2. 构造融合知识的诊断提示词
        prompt = f"设备 '{device_name}' 触发高危预警。\n"
        if knowledge_context:
            prompt += f"参考知识库规范：\n{knowledge_context}\n\n"
        prompt += f"待诊断实时数据：\n{harvest_data}\n\n请结合知识库规范和实时数据，给出简短诊断建议（3句以内）。"

        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": "You are a CNC industrial expert. Respond in Chinese."},
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
        await log_cb("[System] Copilot 智能体已接管系统，正在初始化扫描路径...")
        
        while should_continue():
            await asyncio.sleep(3)
            await log_cb("[System] 正在扫描全线设备风险状态...")
            
            device, alert = await cls.get_highest_risk_device()
            if not device:
                await log_cb("[Scan] 当前无高风险设备，维持监控。")
                await asyncio.sleep(5)
                continue
            
            await log_cb(f"[Target] 锁定风险源：{device.device_name} (分数: {int(alert.anomaly_score*100)}%)")
            await cls.set_device_status(device.id, 'Stopped')
            await log_cb("[Action] 触发紧急避险：设备已远程停机。")
            await asyncio.sleep(2)
            
            h_data = await cls.get_harvest_data(device.id)
            await cls.run_diagnosis_flow(device.id, device.device_name, h_data, log_cb)
            await asyncio.sleep(3)
            
            await log_cb("[Recovery] 正在尝试自动化系统复位与报警清理...")
            await cls.perform_recovery(device.id)
            await log_cb("[Status] 设备已恢复运行至安全组，报警记录已归档。")
