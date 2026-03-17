import json
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from django.utils import timezone
from .models import DeviceInfo, AnomalyAlertLog, ProductionSensorData, SystemConfig, SystemLog
from .services import stats_service
import os
from openai import OpenAI

from .services import stats_service, agent_service

class CopilotConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.keep_running = True
        self.agent_task = None
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
                if action == 'start':
                    if not self.agent_task or self.agent_task.done():
                        self.keep_running = True
                        self.agent_task = asyncio.create_task(
                            agent_service.AgentService.run_autonomous_loop(
                                log_cb=self.push_log,
                                should_continue=lambda: self.keep_running
                            )
                        )
            
            elif mode == 'ask':
                if self.agent_task and not self.agent_task.done():
                    self.keep_running = False
                    self.agent_task.cancel()
                
                asyncio.create_task(self.handle_ask_request(message))
        except Exception as e:
            await self.push_log(f"[Error] 接收异常: {e}")

    async def handle_ask_request(self, user_input):
        """代理 Ask 模式交予 AgentService"""
        try:
            reply, context = await agent_service.AgentService.ask_copilot(user_input)
            await self.send(text_data=json.dumps({
                'type': 'ask_response',
                'message': reply,
                'data_context': context
            }))
        except Exception as e:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': f"Ask 异常: {e}"
            }))

    async def push_log(self, message):
        """Log 回调：由 AgentService 调用推送至前端"""
        await self.send(text_data=json.dumps({
            'type': 'log',
            'message': message,
            'timestamp': timezone.localtime(timezone.now()).strftime('%H:%M:%S')
        }))


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
        """调用 SSOT Service 获取工厂全量快照 (V3.1.0)"""
        return stats_service.get_factory_snapshot()

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

