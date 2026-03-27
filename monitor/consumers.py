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
                elif action == 'stop':
                    self.keep_running = False
                    if self.agent_task and not self.agent_task.done():
                        self.agent_task.cancel()
                    await self.push_log("[System] Copilot 模式已由用户手动停止运行。")
            
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




