import json
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from django.utils import timezone
from .models import DeviceInfo, AnomalyAlertLog, ProductionSensorData, SystemConfig, SystemLog
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

    @sync_to_async
    def _save_maintenance_advice(self, device_id, advice):
        """将 AI 诊断结论持久化到数据库 (V2.2.0 新增)"""
        DeviceInfo.objects.filter(id=device_id).update(maintenance_advice=advice)

    async def run_ai_diagnosis(self, device_id, device_name, harvest_data):
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
            
            # V2.2.0: 将诊断意见写回数据库，供数字孪生大屏展示
            await self._save_maintenance_advice(device_id, ai_response)
            
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
            await self.run_ai_diagnosis(device.id, device.device_name, harvest_data)
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


# ═══════════════════════════════════════════════════════════════════
#  FactoryConsumer — 数字孪生工厂实时推送 (V2.2.0)
#  路由: ws/factory/
#  行为: 每 3s 推送 25 台设备全量快照
# ═══════════════════════════════════════════════════════════════════

class FactoryConsumer(AsyncWebsocketConsumer):
    INTERVAL = 3  # 推送间隔（秒）

    async def connect(self):
        await self.accept()
        self._running = True
        self._last_log_id = 0
        self._push_task = asyncio.create_task(self._push_loop())

    async def disconnect(self, close_code):
        self._running = False
        if self._push_task:
            self._push_task.cancel()

    async def receive(self, text_data):
        """处理前端控制指令：start_stream / stop_stream / reset_groups"""
        try:
            data = json.loads(text_data)
            action = data.get('action')
            if action == 'start_stream':
                await self._set_stream_active(True)
            elif action == 'stop_stream':
                await self._set_stream_active(False)
            elif action == 'reset_groups':
                await self._reset_device_groups()
            elif action == 'fetch_device_history':
                device_id = data.get('device_id')
                history = await self._get_device_history(device_id)
                advice = await self._get_maintenance_advice(device_id)
                await self.send(text_data=json.dumps({
                    'type': 'device_mode_data',
                    'device_id': device_id,
                    'history': history,
                    'advice': advice
                }))
        except Exception as e:
            print(f'[FactoryConsumer] receive error: {e}')

    # ── 核心推送循环 ────────────────────────────────────────────────
    async def _push_loop(self):
        while self._running:
            try:
                # 1. 发送 3D 状态快照
                snapshot = await self._build_snapshot()
                await self.send(text_data=json.dumps(snapshot))
                
                # 2. 发送增量引擎日志 (降级方案：无 Redis 时轮询数据库)
                await self._poll_and_send_logs()
            except Exception as e:
                print(f'[FactoryConsumer] push error: {e}')
            await asyncio.sleep(self.INTERVAL)

    async def _poll_and_send_logs(self):
        """轮询并发送新产生的 SystemLog 日志。"""
        new_logs, last_id = await self._get_new_logs(self._last_log_id)
        if last_id:
            self._last_log_id = last_id
        for log in new_logs:
            await self.send(text_data=json.dumps(log))

    @sync_to_async
    def _get_new_logs(self, last_id):
        """从数据库获取比 last_id 更大的新日志。"""
        qs = SystemLog.objects.filter(id__gt=last_id).order_by('id')
        logs = []
        new_last_id = last_id
        for row in qs:
            logs.append({
                'type': 'engine_log',
                'message': row.message,
                'timestamp': row.timestamp.strftime('%H:%M:%S')
            })
            new_last_id = row.id
        return logs, new_last_id

    # ── 构建全量快照 Payload ─────────────────────────────────────────
    @sync_to_async
    def _build_snapshot(self):
        """一次性拉取所有设备最新传感记录，打包为 factory_snapshot。"""
        import joblib
        from pathlib import Path
        import numpy as np
        from django.utils.timezone import localtime

        now = timezone.now()
        local_now = localtime(now)

        # 加载 LR 模型（轻量缓存：模块级已由 views.py 加载，这里按需重试）
        try:
            _ml_dir = Path(__file__).resolve().parent.parent / 'ml_models'
            _model  = joblib.load(_ml_dir / 'logistic_model.pkl')
            _scaler = joblib.load(_ml_dir / 'scaler.pkl')
        except Exception:
            _model = _scaler = None

        def _predict(rec):
            if _model is None or rec is None:
                return None
            try:
                sc, sp, fv = rec.spindle_current, rec.spindle_power, rec.feed_velocity
                x = np.array([[sc, sp, fv, sc, sp, fv, 0.0, 0.0, 0.0]])
                return round(float(_model.predict_proba(_scaler.transform(x))[0, 1]), 4)
            except Exception:
                return None

        devices = list(DeviceInfo.objects.all().order_by('id'))
        cfg     = SystemConfig.get()

        rows = []
        for dev in devices:
            latest = (
                ProductionSensorData.objects
                .filter(device=dev)
                .order_by('-timestamp')
                .first()
            )
            prob = _predict(latest)

            # 30-min 滑动窗口 OEE（与 views.py 逻辑一致）
            oee_val = 0.0
            if latest:
                from datetime import timedelta
                from django.db.models import Sum
                window_start = local_now - timedelta(minutes=30)
                w_qs = ProductionSensorData.objects.filter(device=dev, timestamp__gte=window_start)
                first_rec = w_qs.order_by('timestamp').first()
                if first_rec:
                    agg = w_qs.aggregate(s=Sum('actual_output'), i=Sum('input_qty'))
                    actual_out = agg['s'] or 0
                    input_qty  = agg['i'] or 0
                    dt_h = (local_now - first_rec.timestamp).total_seconds() / 3600.0
                    dt_h = max(dt_h, 5 / 60.0)
                    theo = dev.standard_capacity * dt_h
                    if theo > 0:
                        p_val = min(actual_out / theo, 1.0)
                        q_val = min(actual_out / input_qty, 1.0) if input_qty > 0 else 1.0
                        oee_val = round(p_val * q_val, 4)

            # Session yield：今日 00:00 起的累计良品（简化实现，无 session_uuid）
            from django.utils.timezone import localtime as lt2
            from datetime import timedelta as td
            today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            session_yield = (
                ProductionSensorData.objects
                .filter(device=dev, timestamp__gte=today_start)
                .aggregate(s=Sum('actual_output'))['s'] or 0
            )

            rows.append({
                'device_id':        dev.id,
                'device_name':      dev.device_name,
                'device_type':      dev.device_type or '',
                'current_status':   dev.current_status,
                'spindle_current':  round(latest.spindle_current, 2) if latest else 0,
                'spindle_power':    round(latest.spindle_power, 4)   if latest else 0,
                'feed_velocity':    round(latest.feed_velocity, 2)   if latest else 0,
                'machining_process': latest.machining_process        if latest else '--',
                'anomaly_score':    prob,
                'oee':              oee_val,
                'session_yield':    session_yield,
            })

        return {
            'type':      'factory_snapshot',
            'timestamp': local_now.strftime('%H:%M:%S'),
            'is_stream_active': cfg.is_realtime_active,
            'devices':   rows,
        }

    # ── 控制指令辅助 ─────────────────────────────────────────────────
    @sync_to_async
    def _set_stream_active(self, value: bool):
        cfg = SystemConfig.get()
        cfg.is_realtime_active = value
        cfg.save()

    @sync_to_async
    def _reset_device_groups(self):
        # V2.2.0: 逻辑下沉，调用模型层统一接口
        try:
            DeviceInfo.initial_repair_all()
        except Exception as e:
            print(f"WS Reset Error: {e}")

    @sync_to_async
    def _get_device_history(self, device_id):
        """获取该设备最近 20 条传感记录。"""
        records = (
            ProductionSensorData.objects
            .filter(device_id=device_id)
            .order_by('-timestamp')[:20]
        )
        return [
            {
                'time': r.timestamp.strftime('%H:%M:%S'),
                'cur':  round(r.spindle_current, 2),
                'pow':  round(r.spindle_power, 2)
            } for r in records
        ]

    @sync_to_async
    def _get_maintenance_advice(self, device_id):
        """从设备基础信息表中获取人工录入的维修建议 (V2.2.0)。"""
        device = DeviceInfo.objects.filter(pk=device_id).first()
        if device:
            return device.maintenance_advice
        return "设备运行平稳，暂无维修建议。"

