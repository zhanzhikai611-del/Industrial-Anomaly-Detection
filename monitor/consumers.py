import json
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from django.utils import timezone
from .models import DeviceInfo, AnomalyAlertLog, ProductionSensorData, SystemConfig
import os
from openai import OpenAI

class CopilotConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.keep_running = True
        self.agent_task = None
        self.current_mode = 'idle'
        await self.accept()

    async def disconnect(self, close_code):
        self.keep_running = False
        if self.agent_task:
            self.agent_task.cancel()

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            mode = data.get('mode')
            action = data.get('action')
            message = data.get('message', '')

            if mode == 'copilot':
                self.current_mode = 'copilot'
                if action == 'start':
                    # If navigating to copilot, ensure any previous ask task is cancelled
                    # (Though currently ask is a fast one-off response, but good practice)
                    if not self.agent_task or self.agent_task.done():
                        self.keep_running = True
                        self.agent_task = asyncio.create_task(self.run_agent_loop())
            
            elif mode == 'ask':
                self.current_mode = 'ask'
                # Cancel the autonomous loop if it's running
                if self.agent_task and not self.agent_task.done():
                    self.keep_running = False
                    self.agent_task.cancel()
                
                # Process the ask logic concurrently
                asyncio.create_task(self.handle_ask_request(message))
        except Exception as e:
            print(f"Error in receive: {e}")

    async def handle_ask_request(self, user_input):
        # We will dispatch this to the ai_engine later
        from .ai_engine import ask_copilot_with_tools
        
        try:
            # run the synchronous LLM call in a thread pool
            loop = asyncio.get_event_loop()
            final_reply, data_context = await loop.run_in_executor(None, ask_copilot_with_tools, user_input)
            
            await self.send(text_data=json.dumps({
                'type': 'ask_response',
                'message': final_reply,
                'data_context': data_context
            }))
        except Exception as e:
            print(f"Error handling ask request: {e}")
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': str(e)
            }))

    async def push_log(self, message):
        """Helper to send logs to the frontend terminal."""
        await self.send(text_data=json.dumps({
            'type': 'log',
            'message': message,
            'timestamp': timezone.localtime(timezone.now()).strftime('%H:%M:%S')
        }))

    @sync_to_async
    def get_highest_risk_device(self):
        """Find the device with the highest anomaly score that is not handled."""
        latest_alert = AnomalyAlertLog.objects.filter(is_handled=False).order_by('-anomaly_score').first()
        if latest_alert and latest_alert.anomaly_score is not None and latest_alert.anomaly_score > 0.70:
            return latest_alert.record.device, latest_alert
        return None, None

    @sync_to_async
    def set_device_status(self, device_id, status):
        device = DeviceInfo.objects.get(id=device_id)
        device.current_status = status
        device.save()
        return device.device_name

    @sync_to_async
    def get_harvest_data(self, device_id):
        records = ProductionSensorData.objects.filter(device_id=device_id).order_by('-timestamp')[:10]
        data_str = "Recent Sensor Data:\\n"
        for r in records:
            data_str += f"Time: {r.timestamp.strftime('%H:%M:%S')} | Current: {r.spindle_current:.2f}A | Power: {r.spindle_power:.1f}W | Velocity: {r.feed_velocity:.2f}\\n"
        return data_str

    async def run_ai_diagnosis(self, device_name, harvest_data):
        await self.push_log(f"[AI] 正在通过 LLM 进行多维关联分析...")
        
        # Simulate typing/thinking delay
        await asyncio.sleep(2)
        
        prompt = f"""
        你是一个工业生产预警系统中的智能助理 (Copilot)。
        设备 '{device_name}' 触发了高危预警。以下是该设备触发预警前的 10 条传感器数据日志：
        
        {harvest_data}
        
        请分析这些数据并给出简短的诊断建议（最多3句）。通常电流和功率的激增意味着刀具严重磨损，建议更换。
        """
        
        try:
            client = OpenAI(
                api_key='sk-6244491a10cd439b9d9013b557450741',
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
            
            # Since OpenAI client is synchronous, we run it in a thread pool to avoid blocking ASGI loop
            def _call_api():
                completion = client.chat.completions.create(
                    model="qwen3.5-flash",
                    messages=[
                        {'role': 'system', 'content': 'You are a professional industrial Copilot assistant. You diagnose CNC machine issues concisely.'},
                        {'role': 'user', 'content': prompt}
                    ],
                    extra_body={"enable_thinking": False}
                )
                return completion.choices[0].message.content
            
            loop = asyncio.get_event_loop()
            ai_response = await loop.run_in_executor(None, _call_api)
            await self.push_log(f"[AI] 诊断意见：{ai_response}")
            
        except Exception as e:
            await self.push_log(f"[AI/Error] LLM 调用失败: {str(e)}")

    @sync_to_async
    def perform_recovery(self, device_id, alert_id):
        device = DeviceInfo.objects.get(id=device_id)
        # Reset current_group_id to 1 (new tool)
        device.current_group_id = 1
        device.current_status = 'Running'
        device.save()
        
        # 将该设备积压的所有未处理报警一并标记为已处理，防止处理过后仍被残留的旧高分流水重新捕捉
        alerts = AnomalyAlertLog.objects.filter(record__device_id=device_id, is_handled=False)
        alerts.update(is_handled=True)

    async def run_agent_loop(self):
        """The main Copilot autonomous loop."""
        await self.push_log(f"[System] Copilot 智能体已接管系统...")
        
        while self.keep_running:
            await asyncio.sleep(3)  # Wait briefly between loop iterations
            
            await self.push_log(f"[System] 正在扫描全线设备风险状态...")
            await asyncio.sleep(2)
            
            device, alert = await self.get_highest_risk_device()
            
            if not device:
                await self.push_log(f"[Scan] 当前未检测到超过阈值的设备，继续监控。")
                await asyncio.sleep(5)
                continue
                
            # Found a high risk device
            score_pct = round(alert.anomaly_score * 100, 1) if alert.anomaly_score else 0
            await self.push_log(f"[Target] 锁定高风险设备：{device.device_name}，风险值：{score_pct}%")
            await asyncio.sleep(2)
            
            # Action: Shutdown
            await self.set_device_status(device.id, 'Stopped')
            await self.push_log(f"[Action] 已触发紧急停机指令，设备状态 -> Stopped")
            await asyncio.sleep(2)
            
            # Harvest Data
            await self.push_log(f"[Data] 开始提取设备传感上下文快照...")
            harvest_data = await self.get_harvest_data(device.id)
            await self.push_log(f"[Info] 成功提取 10 条异常历史记录。")
            await asyncio.sleep(1)
            
            # AI Diagnosis
            await self.run_ai_diagnosis(device.device_name, harvest_data)
            await asyncio.sleep(3)
            
            # Recovery
            await self.push_log(f"[Action] 执行换刀作业(软复位)，重置特征组。")
            await self.perform_recovery(device.id, alert.id)
            await self.push_log(f"[System] 诊断建议已下发至维修单，警报已解除，设备恢复运行。")
            await asyncio.sleep(3)
            
            # Send a special message to let frontend know it can refresh standard views if needed
            await self.send(text_data=json.dumps({'type': 'refresh'}))
            
            # 多停留一段时间让上一轮修复的设备回升到正常风险值，避免连续操作同一台设备
            await asyncio.sleep(5)
