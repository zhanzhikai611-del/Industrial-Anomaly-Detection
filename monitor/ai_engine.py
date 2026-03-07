import json
import logging
from openai import OpenAI
from typing import List, Tuple, Dict, Any

from .models import DeviceInfo, AnomalyAlertLog, ProductionSensorData

logger = logging.getLogger(__name__)

# Hardcoded for dev as requested (Repository is currently private)
API_KEY = "sk-6244491a10cd439b9d9013b557450741"
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL_NAME = "qwen-max"

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
)

def get_device_recent_data(device_names: List[str]) -> str:
    """
    RAG 工具函数：根据设备名称查询数据库，返回其当前状态、异常分数及最近的未处理报警信息。
    严格只读，不进行任何修改操作。
    """
    results = []
    
    # 模糊匹配或精确匹配设备名
    all_devices = []
    
    if 'all' in [name.lower() for name in device_names]:
        # User wants status of all devices
        # We find devices with recent alerts to represent "high risk" devices
        recent_high_risk_alerts = AnomalyAlertLog.objects.filter(
            is_handled=False,
            anomaly_score__gt=0.4
        ).select_related('record__device').order_by('-anomaly_score')[:10]
        
        device_ids_added = set()
        for alert in recent_high_risk_alerts:
            device = alert.record.device
            if device.id not in device_ids_added:
                all_devices.append(device)
                device_ids_added.add(device.id)
                
        if not all_devices:
            return json.dumps([{"message": "目前所有设备均处于正常状态，暂无高危预警。"}], ensure_ascii=False)
            
    else:
        # Search by specific names
        devices = DeviceInfo.objects.filter(device_name__in=device_names)
        
        if not devices.exists() and len(device_names) > 0:
            # 尝试模糊匹配第一个
            devices = DeviceInfo.objects.filter(device_name__icontains=device_names[0])
            
        if not devices.exists():
            return json.dumps({"error": f"Device(s) not found for: {', '.join(device_names)}"})
        
        all_devices = list(devices)

    for device in all_devices:
        # Get the latest alert score for this device if any
        latest_alert = AnomalyAlertLog.objects.filter(
            record__device=device,
            is_handled=False
        ).order_by('-alert_time').first()
        
        current_score = latest_alert.anomaly_score if latest_alert and latest_alert.anomaly_score else 0.0

        dev_info = {
            "device_id": device.id,
            "device_name": device.device_name,
            "current_status": device.current_status,
            "anomaly_score": current_score,
            "recent_alerts": []
        }

        
        # 查询最近未处理的高风险报警
        recent_alerts = AnomalyAlertLog.objects.filter(
            record__device=device,
            is_handled=False
        ).order_by('-alert_time')[:3]
        
        for alert in recent_alerts:
            dev_info["recent_alerts"].append({
                "alert_type": alert.alert_type,
                "alert_time": alert.alert_time.isoformat() if alert.alert_time else None,
                "anomaly_score": alert.anomaly_score
            })
            
        results.append(dev_info)

    return json.dumps(results, ensure_ascii=False)


# 定义工具 schema，供大模型使用 Function Calling
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_device_recent_data",
            "description": "获取指定设备的当前运行状态、异常分数和最近的报警日志。",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_names": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "要查询的设备名称列表，例如 ['CNC-精雕-01', 'CNC-车铣-05']"
                    }
                },
                "required": ["device_names"]
            }
        }
    }
]

def ask_copilot_with_tools(user_input: str) -> Tuple[str, List[Dict[str, Any]]]:
    """
    处理用户输入，使用 Function Calling 调度大模型与本地工具函数。
    返回 (最终自然语言回复, 数据上下文列表[可选])
    """
    messages = [
        {"role": "system", "content": "你是工厂的 CNC 智能诊断助理。如果用户询问设备状态、报警、风险等信息，请务必使用工具函数 get_device_recent_data 去查询真实数据，然后基于真实数据简洁、准确地回答用户。如果用户问所有设备或没明确指明哪台设备但问了状态，你可以传入 ['all'] 去查询。不要编造数据。"},
        {"role": "user", "content": user_input}
    ]

    try:
        # 第一轮调用：给大模型提供 tools，看其是否决定调用
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )

        response_message = response.choices[0].message
        tool_calls = response_message.tool_calls
        
        data_context = [] # 用于给前端渲染表格呈现的原始数据

        if tool_calls:
            # 大模型决定调用工具
            messages.append(response_message)  # 将大模型的助手回复加入历史

            for tool_call in tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)

                if function_name == "get_device_recent_data":
                    device_names = function_args.get("device_names", [])
                    function_response = get_device_recent_data(device_names)
                    
                    try:
                        parsed_response = json.loads(function_response)
                        if isinstance(parsed_response, list):
                             data_context.extend(parsed_response)
                    except:
                        pass

                    messages.append(
                        {
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": function_name,
                            "content": function_response,
                        }
                    )

            # 第二轮调用：把工具返回的数据喂给大模型，让它生成最终的自然语言总结
            second_response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
            )
            final_reply = second_response.choices[0].message.content
            return final_reply, data_context

        else:
            # 如果大模型觉得不需要调用工具（例如普通闲聊），直接返回其回复
            return response_message.content, data_context

    except Exception as e:
        logger.error(f"Error in ask_copilot_with_tools: {e}", exc_info=True)
        return "抱歉，我在处理您的请求时遇到了一点系统问题，请稍后再试。", []
