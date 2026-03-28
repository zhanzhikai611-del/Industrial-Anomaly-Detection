# -*- coding: utf-8 -*-
"""
monitor/views.py
ECharts 大屏 RESTful API + 逻辑回归实时在线推理

API 端点：
  GET  /api/start-stream/          → 启动实时数据流
  GET  /api/stop-stream/           → 暂停实时数据流
  GET  /api/stream-status/         → 查询实时流开关状态
  GET  /api/stats/                 → 看板概览（OEE / 运行设备数 / 未处理报警数）
  GET  /api/stream/                → 最新10条流水 + AI 磨损概率推断
  GET  /api/alerts/                → 最新 N 条报警滚动
  GET  /api/device-matrix/         → 所有设备最新状态 + AI 风险，按 anomaly_score 降序
  GET  /api/sensor-logs/           → 全局最新传感流水（含报警穿透标记）
  GET  /api/stream/<device_id>/    → 单台设备近 50 个时间点的传感序列 + AI 概率
  POST /api/alerts/<id>/handle/    → 将指定报警标记为已处理
"""

import logging
from datetime import timedelta
from .services import ai_service, stats_service, alert_service, device_service

from django.http import JsonResponse, HttpResponseForbidden
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Avg, Sum, Count

from .models import (
    SystemConfig, DeviceInfo, UserProfile,
    ProductionSensorData, AnomalyAlertLog,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  RBAC 角色装饰器（必须在页面路由之前定义）
# ═══════════════════════════════════════════════════════════════════

def role_required(allowed_roles):
    """
    RBAC 角色装饰器。
    - 未登录：重定向到 LOGIN_URL（保留 next 参数）
    - 角色不足：API 请求返回 403 JSON；页面请求重定向到仪表盘
    """
    from functools import wraps
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(f'/login/?next={request.path}')
            user_profile = getattr(request.user, 'profile', None)
            role_name = user_profile.role if user_profile else 'Operator'
            if role_name not in allowed_roles:
                is_api = (request.path.startswith('/api/') or
                          request.headers.get('x-requested-with') == 'XMLHttpRequest')
                if is_api:
                    return JsonResponse({'status': 'error', 'message': 'Permission Denied'}, status=403)
                return redirect('/dashboard/')
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator


# ═══════════════════════════════════════════════════════════════════
#  页面路由
# ═══════════════════════════════════════════════════════════════════

@login_required
def dashboard_view(request):
    """首页 Dashboard (V3.1.0: 逻辑下沉至 Service)"""
    ctx = stats_service.get_dashboard_stats()
    return render(request, 'monitor/dashboard.html', ctx)


@login_required
def device_view(request):
    """
    Device 设备页 - 支持 HTMX 局部刷新 (V3.0.4)
    """
    search_q = request.GET.get('q', '').strip().lower()
    filter_q = request.GET.get('filter', 'all').strip()
    sort_q   = request.GET.get('sort', 'risk_desc').strip()

    # 获取全量计算数据
    devices_data = stats_service.get_device_matrix_data()

    # 1. 先进行搜索过滤
    if search_q:
        devices_data = [d for d in devices_data if search_q in d['device_name'].lower()]
    
    # 2. 计算当前搜索结果下的分类计数 (用于 OOB 更新)
    counts = {
        'all': len(devices_data),
        'high': len([d for d in devices_data if d['current_status'] == 'Running' and d['anomaly_score'] > 0.75]),
        'med': len([d for d in devices_data if d['current_status'] == 'Running' and 0.45 < d['anomaly_score'] <= 0.75]),
        'low': len([d for d in devices_data if d['current_status'] == 'Running' and d['current_status'] == 'Running' and d['anomaly_score'] <= 0.45]),
    }

    # 3. 执行分类过滤
    if filter_q == 'high':
        devices_data = [d for d in devices_data if d['current_status'] == 'Running' and d['anomaly_score'] > 0.75]
    elif filter_q == 'med':
        devices_data = [d for d in devices_data if d['current_status'] == 'Running' and 0.45 < d['anomaly_score'] <= 0.75]
    elif filter_q == 'low':
        devices_data = [d for d in devices_data if d['current_status'] == 'Running' and d['anomaly_score'] <= 0.45]

    # 4. 执行排序
    if sort_q == 'risk_asc':
        devices_data.sort(key=lambda x: x['anomaly_score'])
    elif sort_q == 'name_asc':
        devices_data.sort(key=lambda x: x['device_name'])
    else: # 默认 risk_desc
        devices_data.sort(key=lambda x: x['anomaly_score'], reverse=True)

    context = {
        'devices': devices_data,
        'counts': counts
    }

    # 三级精准驱动逻辑 (V3.0.8)
    target = request.headers.get('HX-Target')
    is_htmx = request.headers.get('HX-Request')
    
    if is_htmx and target == 'dev-tbody':
        # 1. 局部刷新：仅返回表格行
        tpl = 'monitor/includes/device/table_rows.html'
    elif is_htmx and target == 'main-content':
        # 2. SPA跳转：返回设备页主体（含工具栏）
        tpl = 'monitor/device.html'
    else:
        # 3. 初始进入/强制刷新：返回全量页面
        tpl = 'monitor/device.html'

    response = render(request, tpl, context)
    
    # 标头驱动数据推送
    if is_htmx:
        import json
        response['HX-Trigger'] = json.dumps({"updateCounts": counts})
        # 核心：确保 SPA 导航时 URL 同步 (V3.2.10)
        if target and 'main-content' in target:
            response['HX-Push-Url'] = request.get_full_path()
        
    return response

@login_required
def device_detail_view(request, device_id):
    """
    Device Detail 设备详情页 (V3.2.0)
    跳转至独占的单台设备诊断面板，包含实时数据、图表、日志以及AI诊断中枢。
    """
    device = get_object_or_404(DeviceInfo, pk=device_id)
    return render(request, 'monitor/device_detail.html', {'device': device})


@login_required
def event_view(request):
    """Event 报警事件页"""
    from django.core.paginator import Paginator
    from django.db.models import Count
    from django.utils import timezone
    from datetime import timedelta
    import json
    
    events_list = AnomalyAlertLog.objects.select_related('record', 'record__device').order_by('-alert_time')
    paginator = Paginator(events_list, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # 按照类型统计 (已移除 DOWNTIME)
    type_counts = AnomalyAlertLog.objects.filter(alert_time__gte=today_start).values('alert_type').annotate(count=Count('id'))
    stats_data = {'HIGH_CURRENT': 0, 'HIGH_POWER': 0, 'LOW_VELOCITY': 0}
    for tc in type_counts:
        if tc['alert_type'] in stats_data:
            stats_data[tc['alert_type']] = tc['count']
    
    # 补回被误删的趋势图计算逻辑 (V3.3.1)
    last_24h_start = now - timedelta(hours=24)
    recent_alerts = AnomalyAlertLog.objects.filter(alert_time__gte=last_24h_start).values_list('alert_time', flat=True)
    from django.utils.timezone import localtime
    now_local = localtime(now)
    
    # 建立小时索引
    hour_counts = {}
    for at in recent_alerts:
        at_l = localtime(at).replace(minute=0, second=0, microsecond=0)
        hour_counts[at_l] = hour_counts.get(at_l, 0) + 1
    
    trend_labels = []
    trend_values = []
    for i in range(23, -1, -1):
        target_dt = (now_local - timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
        trend_labels.append(target_dt.strftime('%H'))
        trend_values.append(hour_counts.get(target_dt, 0))

    return render(request, 'monitor/event.html', {
        'page_obj': page_obj,
        'stats_data_json': json.dumps([stats_data.get('LOW_VELOCITY', 0), stats_data.get('HIGH_POWER', 0), stats_data.get('HIGH_CURRENT', 0)]),
        'trend_labels_json': json.dumps(trend_labels),
        'trend_values_json': json.dumps(trend_values),
    })

@login_required
@role_required(['Admin'])
def setting_view(request):
    """Setting 系统设置页（仅 Admin 可访问）"""
    config = SystemConfig.get()
    
    # 动态统计真实数据 (V3.2.1)
    from .models import DeviceInfo, ProductionSensorData, AnomalyAlertLog
    from django.utils import timezone
    from datetime import timedelta
    
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    # 统计今日流水条数（实际投入件数）
    today_stream_count = ProductionSensorData.objects.filter(timestamp__gte=today_start).count()
    
    stats = {
        'total_devices': DeviceInfo.objects.count(),
        'today_stream': f"{today_stream_count:,}",
        'total_alerts': AnomalyAlertLog.objects.count()
    }
    
    return render(request, 'monitor/setting.html', {
        'config': config,
        'db_stats': stats
    })

@login_required
@role_required(['Admin'])
def account_view(request):
    """Account 账号管理页（仅 Admin 可访问）"""
    from django.core.paginator import Paginator
    from django.db.models import Q

    search_q = request.GET.get('q', '').strip()
    role_q   = request.GET.get('role', '').strip()

    qs = User.objects.select_related('profile').all().order_by('-date_joined')
    if search_q:
        qs = qs.filter(
            Q(username__icontains=search_q) |
            Q(profile__real_name__icontains=search_q) |
            Q(profile__job_number__icontains=search_q)
        )
    if role_q:
        qs = qs.filter(profile__role=role_q)

    paginator = Paginator(qs, 10)
    page_obj  = paginator.get_page(request.GET.get('page'))

    return render(request, 'monitor/account.html', {
        'page_obj': page_obj,
        'search_q': search_q,
        'role_q':   role_q,
        'total':    paginator.count,
    })

@csrf_exempt
@require_http_methods(['POST'])
@role_required(['Admin'])
def api_create_account(request):
    import json
    try:
        data = json.loads(request.body)
        username = data.get('username')
        password = data.get('password')
        real_name = data.get('real_name')
        job_number = data.get('job_number')
        role = data.get('role', 'Operator')
        
        if User.objects.filter(username=username).exists():
            return JsonResponse({'status': 'error', 'message': 'Username already exists'}, status=400)
            
        with transaction.atomic():
            user = User.objects.create(
                username=username,
                password=make_password(password)
            )
            UserProfile.objects.create(
                user=user,
                real_name=real_name,
                job_number=job_number,
                role=role
            )
        return JsonResponse({'status': 'ok', 'message': '账号创建成功'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)




# ═══════════════════════════════════════════════════════════════════
#  实时流开关 API（原有）
# ═══════════════════════════════════════════════════════════════════

@csrf_exempt
@require_http_methods(['GET', 'POST'])
def start_stream(request):
    cfg = SystemConfig.get()
    cfg.is_realtime_active = True
    cfg.save()
    return JsonResponse({'status': 'ok', 'message': '实时数据流已启动', 'is_realtime_active': True})


@csrf_exempt
@require_http_methods(['POST'])
def api_toggle_stream(request):
    """POST /api/system/toggle_stream/ — 控制数据流开关 (V3.1.0)"""
    import json
    try:
        data = json.loads(request.body)
        action = data.get('action')
        active = (action == 'start')
        
        success = device_service.DeviceService.toggle_realtime_stream(active)
        if success:
            msg = "实时数据流已开启" if active else "已暂停实时流"
            return JsonResponse({'status': 'ok', 'message': msg, 'is_realtime_active': active})
        return JsonResponse({'status': 'error', 'message': '设置失败'}, status=500)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    
@csrf_exempt
@require_http_methods(['GET', 'POST'])
def stop_stream(request):
    cfg = SystemConfig.get()
    cfg.is_realtime_active = False
    cfg.save()
    return JsonResponse({'status': 'ok', 'message': '实时数据流已暂停', 'is_realtime_active': False})


@require_http_methods(['GET'])
def stream_status(request):
    cfg = SystemConfig.get()
    return JsonResponse({
        'status': 'ok',
        'is_realtime_active': cfg.is_realtime_active,
        'is_active': cfg.is_realtime_active,
        'updated_at': cfg.updated_at.isoformat() if cfg.updated_at else None,
    })


@csrf_exempt
@require_http_methods(['POST'])
def api_reset_groups(request):
    """POST /api/system/reset_groups/ — 全量重置设备生命周期分组梯度 (V3.1.0)"""
    success = device_service.DeviceService.reset_all_device_groups()
    if success:
        return JsonResponse({'status': 'ok', 'message': '所有设备刀具损耗梯度已重置'})
    return JsonResponse({'status': 'error', 'message': '重置失败'}, status=500)


# ═══════════════════════════════════════════════════════════════════
#  API 1：看板概览统计 /api/stats/
# ═══════════════════════════════════════════════════════════════════


from django.views.decorators.cache import cache_control

@require_http_methods(['GET'])
@cache_control(no_cache=True, must_revalidate=True, no_store=True)
def api_dashboard_stats(request):
    """GET /api/stats/ — 传统的 JSON 接口 (保持向前兼容)"""
    ctx = stats_service.get_dashboard_stats()
    return JsonResponse({
        'status': 'ok',
        'avg_oee': ctx['avg_oee'],
        'availability': ctx['oee_a_bar'] / 100.0,
        'performance': ctx['oee_p_bar'] / 100.0,
        'quality': ctx['oee_q_bar'] / 100.0,
        'total_output': ctx['total_output'],
        'total_input': ctx['total_input'],
        'daily_target': ctx['daily_target'],
        'running_devices': ctx['running_devices'],
        'idle_devices': ctx['idle_devices'],
        'down_devices': ctx['down_devices'],
        'total_devices': ctx['total_devices'],
        'unhandled_alerts': ctx['unhandled_alerts'],
        'hourly_labels': ctx.get('hourly_labels', []),
        'hourly_output': ctx.get('hourly_output', []),
    })

@login_required
def dashboard_partial(request, fragment):
    """
    HTMX 局部刷新视图 (V3.0.12)
    可根据请求参数返回不同的仪表盘片段
    """
    if fragment == 'oee':
        ctx = stats_service.get_dashboard_stats()
        return render(request, 'monitor/includes/dashboard/panel_oee.html', ctx)
    
    elif fragment == 'production':
        ctx = stats_service.get_dashboard_stats()
        response = render(request, 'monitor/includes/dashboard/panel_production.html', ctx)
        
        # [V3.2.1] 性能补丁：将图表数据随 HTML 一并推送，减少一次 API 请求
        import json
        chart_data = {
            'labels': ctx.get('hourly_labels', []),
            'values': ctx.get('hourly_output', [])
        }
        # [V3.2.5] 兼容性修复：改用 HX-Trigger-After-Swap，确保 DOM 交换完成后再通知 JS 初始化图标
        # 这能解决竞态条件下 ECharts 容器尚未出现在文档流中就触发事件导致的渲染失败
        triggers = {}
        triggers['updateHourlyChart'] = chart_data
        response['HX-Trigger-After-Swap'] = json.dumps(triggers)
        return response
        
    elif fragment == 'alerts':
        limit = min(int(request.GET.get('limit', 7)), 20)
        alerts = alert_service.AlertService.get_formatted_alerts(limit=limit)
        
        total_unhandled = alert_service.AlertService.get_unhandled_alerts_count()
        return render(request, 'monitor/includes/dashboard/panel_alerts.html', {
            'alerts': alerts,
            'unhandled_alerts': total_unhandled
        })
    
    return HttpResponseForbidden()



# ═══════════════════════════════════════════════════════════════════
#  API 2：实时数据流 + AI 推断 /api/stream/
# ═══════════════════════════════════════════════════════════════════

@require_http_methods(['GET'])
def api_realtime_stream(request):
    """
    GET /api/stream/ — 实时数据流（Dashboard 折线图数据源）
    从 Running 设备中随机选 1 台，取其最新一条传感记录，
    将 timestamp 替换为当前时间使前端持续更新。

    V1.2.1 修正：原"随机抽 5000 选 1"会混入大量旧 Group-1 历史数据，
    改为从每台 Running 设备各取最新 1 条后再随机选台，确保显示值来自当前流。
    """
    import random as _random

    running_devices = list(DeviceInfo.objects.filter(current_status='Running'))
    if not running_devices:
        return JsonResponse({'status': 'ok', 'count': 0, 'data': [], 'ai_ready': ai_service.is_ai_ready()})

    dev = _random.choice(running_devices)
    rec = (
        ProductionSensorData.objects
        .select_related('device')
        .filter(device=dev)
        .order_by('-timestamp')
        .first()
    )
    if not rec:
        return JsonResponse({'status': 'ok', 'count': 0, 'data': [], 'ai_ready': ai_service.is_ai_ready()})

    prob = ai_service.predict_proba(rec)
    data = [{
        'id':                  rec.id,
        'timestamp':           rec.timestamp.isoformat(),  # 使用数据库真实时间戳，暂停时不再变化
        'device_id':           rec.device_id,
        'device_name':         rec.device.device_name,
        'spindle_current':     rec.spindle_current,
        'spindle_power':       rec.spindle_power,
        'feed_velocity':       rec.feed_velocity,
        'machining_process':   rec.machining_process,
        'tool_condition':      int(rec.tool_condition),
        'oee':                 rec.oee,
        'anomaly_probability': prob,
        'ai_alert':            (prob is not None and prob > 0.75),
    }]
    return JsonResponse({'status': 'ok', 'count': 1, 'data': data, 'ai_ready': ai_service.is_ai_ready()})


# ═══════════════════════════════════════════════════════════════════
#  API 3：最新报警滚动 /api/alerts/
# ═══════════════════════════════════════════════════════════════════

@require_http_methods(['GET'])
def api_latest_alerts(request):
    """GET /api/alerts/ — 最新 N 条报警（默认 5，最大 20）"""
    limit = min(int(request.GET.get('limit', 5)), 20)
    alerts = (
        AnomalyAlertLog.objects
        .select_related('record', 'record__device')
        .order_by('-alert_time')[:limit]
    )
    data = []
    for a in alerts:
        data.append({
            'id':                 a.id,
            'alert_time':         a.alert_time.isoformat(),
            'alert_type':         a.alert_type,
            'alert_type_display': a.get_alert_type_display(),
            'device_id':          a.record.device_id,
            'device_name':        a.record.device.device_name,
            'anomaly_score':      a.anomaly_score,
            'is_handled':         a.is_handled,
            'spindle_current':    a.record.spindle_current,
            'spindle_power':      a.record.spindle_power,
            'feed_velocity':      a.record.feed_velocity,
        })
    total_unhandled = AnomalyAlertLog.objects.filter(is_handled=False).count()
    return JsonResponse({'status': 'ok', 'count': len(data), 'total': total_unhandled, 'data': data})


# ═══════════════════════════════════════════════════════════════════
#  API 4（新增）：设备风险矩阵 /api/device-matrix/
# ═══════════════════════════════════════════════════════════════════

@require_http_methods(['GET'])
@cache_control(no_cache=True, must_revalidate=True, no_store=True)
def api_device_matrix(request):
    """
    GET /api/device-matrix/
    已重构：内部调用 stats_service.get_device_matrix_data() (V5.2.3)
    """
    rows = stats_service.get_device_matrix_data()
    return JsonResponse({'status': 'ok', 'data': rows})


# ═══════════════════════════════════════════════════════════════════
#  API 5（新增）：全局传感流水 /api/sensor-logs/
# ═══════════════════════════════════════════════════════════════════

@require_http_methods(['GET'])
def api_sensor_logs(request):
    """
    GET /api/sensor-logs/
    返回最新 N 条全局传感记录（含报警穿透标记 has_alert）。
    """
    limit = min(int(request.GET.get('limit', 30)), 100)
    records = (
        ProductionSensorData.objects
        .prefetch_related('alerts')
        .select_related('device')
        .order_by('-timestamp')[:limit]
    )
    data = []
    for rec in records:
        alerts = list(rec.alerts.all())
        data.append({
            'id':               rec.id,
            'timestamp':        rec.timestamp.isoformat(),
            'device_id':        rec.device_id,
            'device_name':      rec.device.device_name,
            'spindle_current':  rec.spindle_current,
            'spindle_power':    rec.spindle_power,
            'feed_velocity':    rec.feed_velocity,
            'machining_process': rec.machining_process,
            'tool_condition':   int(rec.tool_condition),
            'oee':              rec.oee,
            # 若该流水记录在报警表中有外键引用 → 行背景变红
            'has_alert':        len(alerts) > 0,
            'alert_type':       alerts[0].alert_type if alerts else None,
        })
    return JsonResponse({'status': 'ok', 'count': len(data), 'data': data})


# ═══════════════════════════════════════════════════════════════════
#  API 6（新增）：单机历史序列 /api/stream/<device_id>/
# ═══════════════════════════════════════════════════════════════════

@require_http_methods(['GET'])
def api_device_stream(request, device_id):
    """
    GET /api/stream/<device_id>/
    返回指定设备最近 50 个时间点的 spindle_current 序列
    + 对每条同步用 LR 模型计算的 anomaly_score 序列，
    供 AI 诊断详情弹窗中的双轴折线图渲染。
    """
    limit = min(int(request.GET.get('limit', 50)), 200)
    records = list(
        ProductionSensorData.objects
        .filter(device_id=device_id)
        .select_related('device')
        .order_by('-timestamp')[:limit]
    )
    # 时间顺序（旧→新）
    records = list(reversed(records))

    timestamps       = []
    spindle_currents = []
    spindle_powers   = []
    feed_velocities  = []
    machining_processes = []
    anomaly_scores   = []
    oee_list         = []

    device = DeviceInfo.objects.filter(pk=device_id).first()
    if not device:
        return JsonResponse({'status': 'error', 'message': 'Device not found'}, status=404)

    local_now = timezone.localtime(timezone.now())
    # 实时窗口 OEE 计算 (V3.0.9) - 保持与表格 100% 一致
    stable_oee = stats_service.calculate_rolling_oee(device, local_now)

    for rec in records:
        timestamps.append(rec.timestamp.isoformat())
        spindle_currents.append(rec.spindle_current)
        spindle_powers.append(rec.spindle_power)
        feed_velocities.append(rec.feed_velocity)
        machining_processes.append(rec.get_machining_process_display())
        # 固定返回聚合后的稳定值，避免瞬时 0.0% 干扰
        oee_list.append(round(stable_oee * 100, 1))        # [V3.4.6] 统一读取持久化的异常分，禁止在此处重算（解决 Web 进程 Buffer 缺失导致的偏差）
        score = rec.anomaly_score or 0.0
        anomaly_scores.append(round(score * 100, 2))  # 转换为百分比

    device = DeviceInfo.objects.filter(pk=device_id).first()

    logs_data = []
    if device and device.maintenance_advice:
        logs_data.append({
            'id': f"m_{device.id}",
            'time': timezone.localtime(timezone.now()).strftime('%m-%d %H:%M:%S'),
            'source': '系统工单',
            'content': device.maintenance_advice
        })
        
    recent_alerts = AnomalyAlertLog.objects.filter(record__device=device).order_by('-alert_time')[:5]
    for a in recent_alerts:
        score_info = f" 置信度: {a.anomaly_score*100:.1f}%" if a.anomaly_score else ""
        logs_data.append({
            'id': f"a_{a.id}",
            'time': timezone.localtime(a.alert_time).strftime('%m-%d %H:%M:%S'),
            'source': '预警拦截',
            'content': f"[{a.get_alert_type_display()}]{score_info}"
        })

    return JsonResponse({
        'status':          'ok',
        'device_id':       device_id,
        'device_name':     device.device_name if device else str(device_id),
        'device_type':     device.device_type if device else '',
        'timestamps':      timestamps,
        'spindle_current': spindle_currents,
        'spindle_power':   spindle_powers,
        'feed_velocity':   feed_velocities,
        'process':         machining_processes,
        'anomaly_score':   anomaly_scores,
        'oee':             oee_list,
        'logs':            logs_data,
    })


# ═══════════════════════════════════════════════════════════════════
#  API 7（新增）：24h 报警频率趋势 /api/alert-trend/
# ═══════════════════════════════════════════════════════════════════

@require_http_methods(['GET'])
def api_alert_trend(request):
    """
    GET /api/alert-trend/
    统计过去 24 小时（以整点小时为桶，共 24 桶）的报警频次。
    """
    from django.utils.timezone import localtime
    from datetime import timedelta
    now_utc = timezone.now()
    now_local = localtime(now_utc)
    labels, values = [], []
    for i in range(23, -1, -1):
        start_t = now_utc - timedelta(hours=(i + 1))
        end_t   = now_utc - timedelta(hours=i)
        
        # Calculate the local time for the label
        end_t_local = now_local - timedelta(hours=i)
        labels.append(end_t_local.strftime('%H:00'))
        
        cnt = AnomalyAlertLog.objects.filter(
            alert_time__gte=start_t,
            alert_time__lt=end_t
        ).count()
        values.append(cnt)
    return JsonResponse({'status': 'ok', 'labels': labels, 'values': values})


# ═══════════════════════════════════════════════════════════════════
#  API 8（原 7）：标记报警已处理 /api/alerts/<id>/handle/
# ═══════════════════════════════════════════════════════════════════

@csrf_exempt
@require_http_methods(['POST'])
def api_handle_alert(request, alert_id):
    """POST /api/alerts/<id>/handle/ — 处理警报 (V3.1.0)"""
    success = alert_service.AlertService.handle_alert(alert_id)
    if success:
        return JsonResponse({'status': 'ok', 'message': f'报警 {alert_id} 处理成功'})
    return JsonResponse({'status': 'error', 'message': '报警不存在或处理失败'}, status=404)

# ═══════════════════════════════════════════════════════════════════
#  API 8（新增）：更新设备状态 /api/device/<id>/status/
# ═══════════════════════════════════════════════════════════════════

@csrf_exempt
@require_http_methods(['POST'])
def api_update_device_status(request, device_id):
    """
    POST /api/device/<id>/status/
    反向控制：接收前端下发的状态变更指令，并更新数据库中 DeviceInfo 的状态字段。
    """
    import json
    try:
        data = json.loads(request.body)
        new_status = data.get('status')
        if new_status not in ['Running', 'Idle', 'Down']:
            return JsonResponse({'status': 'error', 'message': '无效的状态'}, status=400)
            
        device = DeviceInfo.objects.get(pk=device_id)
        device.current_status = new_status
        device.save(update_fields=['current_status'])
        
        return JsonResponse({'status': 'ok', 'message': f'设备状态已成功更新为 {new_status}'})
    except DeviceInfo.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': '设备不存在'}, status=404)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@csrf_exempt
@require_http_methods(['POST'])
@role_required(['Admin', 'Engineer'])
def api_device_reset(request, device_id):
    try:
        device = get_object_or_404(DeviceInfo, pk=device_id)
        if device.current_status != 'Down':
            return JsonResponse({
                'status': 'error',
                'message': '仅停机(Stopped)状态下可下发处理操作',
            }, status=400)
        with transaction.atomic():
            # 解析工单说明内容
            import json
            try:
                data = json.loads(request.body)
                note = data.get('note', '设备已处理完毕，环境已重置。')
            except:
                note = '设备已处理完毕，环境已重置。'

            AnomalyAlertLog.objects.filter(
                record__device=device,
                is_handled=False
            ).update(is_handled=True)

            device.current_group_id = 1  # V1.4.0: 全新设备组，sc_mean→15.0A，AI风险→0-40%
            device.current_status = 'Idle'  # 下发完成后切到待机，不自动恢复运行
            device.maintenance_advice = note # 保存到设备信息表中
            device.save(update_fields=['current_group_id', 'current_status', 'maintenance_advice'])
            
            # [V3.4.3 增强] 联动重置 AI 特征计算缓冲区，让风险分数瞬间回落
            ai_service.reset_device_buffer(device_id)

        return JsonResponse({'status': 'ok', 'message': f'设备 {device.device_name} 已处理完毕，已切换至待机'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


# ═══════════════════════════════════════════════════════════════════
#  身份认证视图  Login / Logout
# ═══════════════════════════════════════════════════════════════════

def login_view(request):
    """
    GET  /login/  — 渲染登录页
    POST /login/  — 执行 Django authenticate → login → 重定向到 next 或 dashboard
    """
    if request.user.is_authenticated:
        return redirect('/dashboard/')

    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            if user.is_active:
                login(request, user)
                # 确保超级管理员有 Admin 角色的 UserProfile
                _ensure_admin_profile(user)
                next_url = request.GET.get('next', '/dashboard/')
                return redirect(next_url if next_url.startswith('/') else '/dashboard/')
            else:
                error = '该账号已被禁用，请联系系统管理员'
        else:
            error = '用户名或密码错误，请重试'

    return render(request, 'monitor/login.html', {'error': error})


def logout_view(request):
    """GET/POST /logout/  — 清除 Session 并跳转到登录页"""
    logout(request)
    return redirect('/login/')


def _ensure_admin_profile(user):
    """
    超级用户（is_superuser=True）首次登录时自动创建 Admin 角色的 UserProfile。
    普通用户的 Profile 应通过账号管理页面创建，不在此自动生成。
    """
    if not user.is_superuser:
        return
    profile = getattr(user, 'profile', None)
    if profile is None:
        # 生成不冲突的工号
        from monitor.models import UserProfile as _UP
        jn = f'{user.id:06d}'
        if _UP.objects.filter(job_number=jn).exists():
            jn = f'SU{user.id:04d}'
        _UP.objects.get_or_create(
            user=user,
            defaults={
                'role': 'Admin',
                'real_name': user.get_full_name() or user.username,
                'job_number': jn,
            }
        )
    elif profile.role != 'Admin' and user.is_superuser:
        profile.role = 'Admin'
        profile.save(update_fields=['role'])


# ═══════════════════════════════════════════════════════════════════
#  账号管理 API  — Toggle Status / Delete / Update Role
# ═══════════════════════════════════════════════════════════════════

@csrf_exempt
@require_http_methods(['POST'])
@role_required(['Admin'])
def api_toggle_user_status(request, user_id):
    """POST /api/accounts/<id>/toggle/ — 切换账号启用/禁用状态"""
    try:
        target = User.objects.get(pk=user_id)
        if target == request.user:
            return JsonResponse({'status': 'error', 'message': '不能禁用自己的账号'}, status=400)
        target.is_active = not target.is_active
        target.save(update_fields=['is_active'])
        action = '启用' if target.is_active else '禁用'
        return JsonResponse({'status': 'ok', 'is_active': target.is_active,
                             'message': f'账号已{action}'})
    except User.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': '用户不存在'}, status=404)


@csrf_exempt
@require_http_methods(['POST'])
@role_required(['Admin'])
def api_delete_user(request, user_id):
    """POST /api/accounts/<id>/delete/ — 删除账号（不可删除自身）"""
    try:
        target = User.objects.get(pk=user_id)
        if target == request.user:
            return JsonResponse({'status': 'error', 'message': '不能删除自己的账号'}, status=400)
        target.delete()
        return JsonResponse({'status': 'ok', 'message': '账号已删除'})
    except User.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': '用户不存在'}, status=404)


@csrf_exempt
@require_http_methods(['POST'])
@role_required(['Admin'])
def api_update_user(request, user_id):
    """POST /api/accounts/<id>/update/ — 更新角色、真实姓名、启用状态"""
    import json
    try:
        data = json.loads(request.body)
        target = get_object_or_404(User, pk=user_id)
        profile = getattr(target, 'profile', None)
        if not profile:
            return JsonResponse({'status': 'error', 'message': '用户无 Profile 记录'}, status=400)

        with transaction.atomic():
            if 'role' in data:
                valid_roles = ['Admin', 'Engineer', 'Operator']
                if data['role'] not in valid_roles:
                    return JsonResponse({'status': 'error', 'message': '无效角色'}, status=400)
                profile.role = data['role']
            if 'real_name' in data:
                profile.real_name = data['real_name']
            profile.save()

            if 'is_active' in data and target != request.user:
                target.is_active = bool(data['is_active'])
                target.save(update_fields=['is_active'])

            if 'password' in data and data['password']:
                target.password = make_password(data['password'])
                target.save(update_fields=['password'])

        return JsonResponse({'status': 'ok', 'message': '账号信息已更新'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
