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
from pathlib import Path

import numpy as np
import joblib

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
    """首页 Dashboard"""
    return render(request, 'monitor/dashboard.html')


@login_required
def device_view(request):
    """Device 设备页"""
    return render(request, 'monitor/device.html')

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
    
    # 按照类型统计
    type_counts = AnomalyAlertLog.objects.filter(alert_time__gte=today_start).values('alert_type').annotate(count=Count('id'))
    stats_data = {'HIGH_CURRENT': 0, 'HIGH_POWER': 0, 'LOW_VELOCITY': 0}
    for tc in type_counts:
        stats_data[tc['alert_type']] = tc['count']
        
    from django.utils.timezone import localtime
    now_local = localtime(now)
    trend_labels = []
    trend_values = []
    for i in range(23, -1, -1):
        start_t = now - timedelta(hours=i+1)
        end_t   = now - timedelta(hours=i)
        
        end_t_local = now_local - timedelta(hours=i)
        trend_labels.append(end_t_local.strftime('%H:00'))
        
        cnt = AnomalyAlertLog.objects.filter(alert_time__gte=start_t, alert_time__lt=end_t).count()
        trend_values.append(cnt)

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
    return render(request, 'monitor/setting.html')

@login_required
def factory_view(request):
    """Digital Factory 数字孪生工厂大屏（V2.2.0）"""
    return render(request, 'monitor/factory.html')

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
#  AI 模型全局单例加载（进程启动时执行一次）
# ═══════════════════════════════════════════════════════════════════

_ML_DIR = Path(__file__).resolve().parent.parent / 'ml_models'

def _load_artifacts():
    """安全加载 LR 模型和 StandardScaler，文件不存在时返回 None。"""
    try:
        model      = joblib.load(_ML_DIR / 'logistic_model.pkl')
        scaler     = joblib.load(_ML_DIR / 'scaler.pkl')
        feat_names = joblib.load(_ML_DIR / 'feature_names.pkl')
        logger.info('[AI] 逻辑回归模型加载成功，特征维度=%d', len(feat_names))
        return model, scaler, feat_names
    except FileNotFoundError:
        logger.warning('[AI] ml_models/ 文件不存在，AI 推断功能已禁用')
        return None, None, None
    except Exception as exc:
        logger.error('[AI] 模型加载异常：%s', exc)
        return None, None, None

# 全局单例
_LR_MODEL, _SCALER, _FEAT_NAMES = _load_artifacts()


def _predict_proba(record: ProductionSensorData) -> float | None:
    """
    单条记录在线推断。
    9 维聚合特征：[sc_mean, sp_mean, fv_mean, sc_max, sp_max, fv_max, sc_std, sp_std, fv_std]
    """
    if _LR_MODEL is None:
        return None
    try:
        sc = record.spindle_current
        sp = record.spindle_power
        fv = record.feed_velocity
        x = np.array([[sc, sp, fv, sc, sp, fv, 0.0, 0.0, 0.0]])
        x_scaled = _SCALER.transform(x)
        prob = float(_LR_MODEL.predict_proba(x_scaled)[0, 1])
        return round(prob, 4)
    except Exception as exc:
        logger.warning('[AI] 推断异常：%s', exc)
        return None


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
    import json
    data = json.loads(request.body)
    action = data.get('action')
    cfg = SystemConfig.get()
    
    if action == 'start':
        cfg.is_realtime_active = True
    elif action == 'stop':
        cfg.is_realtime_active = False
        
    cfg.save()
    return JsonResponse({'status': 'ok', 'is_active': cfg.is_realtime_active})
    
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
    """POST /api/system/reset_groups/ — 全量重置设备生命周期分组梯度"""
    from django.db import transaction
    
    def _get_initial_group(idx):
        """逻辑回归梯度分布规则 (0-indexed)"""
        if idx < 5:    return 1  # 1-5
        elif idx < 11: return 2  # 6-11
        elif idx < 18: return 3  # 12-18
        elif idx < 23: return 4  # 19-23
        else:          return 5  # 24-25

    try:
        with transaction.atomic():
            devices = DeviceInfo.objects.all().order_by('id')
            for i, dev in enumerate(devices):
                dev.current_group_id = _get_initial_group(i)
                dev.save(update_fields=['current_group_id'])
        
        return JsonResponse({'status': 'ok', 'message': '风险梯度已重置为初始状态'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


# ═══════════════════════════════════════════════════════════════════
#  API 1：看板概览统计 /api/stats/
# ═══════════════════════════════════════════════════════════════════

@require_http_methods(['GET'])
def api_dashboard_stats(request):
    """
    GET /api/stats/
    返回第一层 Dashboard 所需全部聚合数据：
      - OEE 三分项（可用性 A、表现性 P、质量率 Q）及综合得分
      - 当日总产出 / 总投入
      - 设备状态分布（Running / Idle / Down）
      - 今日报警分类频次

    V1.1 重构：废除 per-record 平均逻辑，改用设备级联计算模型。
      A = (Running设备数 / 总设备数) × 排班修正系数
      P = Σ(actual_output) / Σ(theoretical_output)   全厂级
      Q = 1 - (高风险设备数 × 次品系数) / 总设备数
      OEE = A × P × Q
    """
    from django.utils.timezone import localtime
    local_now = localtime(timezone.now())
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)

    # ── 设备状态分布 ──────────────────────────────────────────────
    running_count = DeviceInfo.objects.filter(current_status='Running').count()
    idle_count    = DeviceInfo.objects.filter(current_status='Idle').count()
    down_count    = DeviceInfo.objects.filter(current_status='Down').count()
    total_devices = DeviceInfo.objects.count() or 25

    # ── 拉取今日最近流水用于聚合（OEE AI推断用，上限500条） ──────────
    today_qs = list(
        ProductionSensorData.objects
        .filter(timestamp__gte=today_start)
        .select_related('device')
        .order_by('-timestamp')[:500]
    )

    # ═══════════════════════════════════════════════════════════════
    #  OEE 级联计算模型（V1.4.2 修正，适配 STEP_MINS=0.05 新格式）
    # ═══════════════════════════════════════════════════════════════

    # ── A（可用性）= Running设备比率 ────────────────
    avg_a = round(running_count / total_devices, 4)

    # ── P（性能）= 近期实际生产速率 / 理论满载速率 ──────────────────────────
    # 使用近 500 条流水日志（today_qs），估算当前的全局生产速率。
    # 废弃原本的 8 点早班时间锁，使 24 小时任何时段都能进行准确的瞬时性能核算

    _sample_actual = sum(r.actual_output or 0 for r in today_qs)
    _sample_records = len(today_qs)

    # 用最近 500 条数据估算全局生产速率 (V2.1.1 墙钟法优化)
    if _sample_records > 0 and running_count > 0:
        # 墙钟法：从样本中最老的一条至今的真实物理耗时
        _earliest_ts = today_qs[-1].timestamp
        _sample_hours = (local_now - _earliest_ts).total_seconds() / 3600.0
        
        # 兜底：如果样本极其新鲜（小于理论步长），使用理论步长，防止 P 值因分母过小而突波
        _theo_step_hours = _sample_records / (running_count * (3600 / 3.0))
        _sample_hours = max(_sample_hours, _theo_step_hours)
        
        _rate_per_hour = _sample_actual / _sample_hours if _sample_hours > 0 else 0  # 产量/小时
        
        # 理论满载速率（当前 Running 设备的标准产能之和）
        _devices_with_caps = DeviceInfo.objects.filter(current_status='Running').values_list('standard_capacity', flat=True)
        _theoretical_rate = sum(_devices_with_caps)  # 件/小时
        
        if _theoretical_rate > 0:
            avg_p = round(min(_rate_per_hour / _theoretical_rate, 1.0), 4)
        else:
            avg_p = None
    else:
        avg_p = None

    # ── 生产计划基础统计 (用于看板展示及 Q 指标计算) ────────────────
    from django.db.models import Sum
    from django.db.models.functions import TruncHour
    from datetime import timedelta

    # 1. 动态产能目标计算: 全厂标准产能之和 * 8小时标准班
    daily_target_dict = DeviceInfo.objects.aggregate(total_cap=Sum('standard_capacity'))
    daily_target = (daily_target_dict['total_cap'] or 0) * 8

    # 2. 全量当日良品与投入统计（用于计算真实良率 Q）
    start_of_local_day = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_all_qs = ProductionSensorData.objects.filter(timestamp__gte=start_of_local_day)

    total_output = 0
    total_input = 0
    if today_all_qs.exists():
        totals = today_all_qs.aggregate(
            actual_total=Sum('actual_output'),
            input_total=Sum('input_qty')
        )
        total_output = totals['actual_total'] or 0
        total_input = totals['input_total'] or 0

    # ── Q（良率）= 实际良品产出 / 实际投入总量 (V2.1.2 物理实测逻辑) ──
    if total_input > 0:
        avg_q = round(total_output / total_input, 4)
    else:
        avg_q = 1.0  # 无产出时默认为 100% 良率

    # ── OEE = A × P × Q（严格连乘）────────────────────────────────
    if avg_a is not None and avg_p is not None and avg_q is not None:
        avg_oee = round(avg_a * avg_p * avg_q, 4)
    else:
        avg_oee = None

    # ── 生产计划达成分析重构 (V1.4.2) ──────────────────────────────────
    from django.db.models import Sum
    from django.db.models.functions import TruncHour
    from datetime import timedelta

    # (注：total_output 和 total_input 已在上方 Q 计算部分完成聚合)

    # 3. 按小时(最近8小时)分布的真实良品产量
    hourly_output_array = []
    hourly_labels = []
    
    for i in range(7, -1, -1):
        start_t = local_now - timedelta(hours=i)
        start_h = start_t.replace(minute=0, second=0, microsecond=0)
        end_h = start_h + timedelta(hours=1)
        
        hourly_labels.append(start_h.strftime('%Hh'))
        
        h_qs = ProductionSensorData.objects.filter(timestamp__gte=start_h, timestamp__lt=end_h)
        val = h_qs.aggregate(s=Sum('actual_output'))['s'] or 0
        hourly_output_array.append(val)

    # ── 今日报警分类频次 ──────────────────────────────────────────
    from django.db.models import Count
    alert_dist = list(
        AnomalyAlertLog.objects
        .filter(alert_time__gte=today_start)
        .values('alert_type')
        .annotate(cnt=Count('id'))
        .order_by('-cnt')
    )

    unhandled_count = AnomalyAlertLog.objects.filter(is_handled=False).count()

    return JsonResponse({
        'status':          'ok',
        # OEE
        'avg_oee':         avg_oee,
        'avg_oee_pct':     f'{avg_oee:.1%}' if avg_oee is not None else '--',
        'availability':    avg_a,
        'performance':     avg_p,
        'quality':         avg_q,
        # 产量与计划
        'total_output':    total_output,
        'total_input':     total_input,
        'daily_target':    daily_target,
        'hourly_labels':   hourly_labels,
        'hourly_output':   hourly_output_array,
        # 设备状态
        'running_devices': running_count,
        'idle_devices':    idle_count,
        'down_devices':    down_count,
        'total_devices':   total_devices,
        # 报警
        'unhandled_alerts': unhandled_count,
        'alert_distribution': alert_dist,
        'ai_model_loaded':  _LR_MODEL is not None,
        'computed_at':      timezone.now().isoformat(),
    })


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
        return JsonResponse({'status': 'ok', 'count': 0, 'data': [], 'ai_ready': _LR_MODEL is not None})

    dev = _random.choice(running_devices)
    rec = (
        ProductionSensorData.objects
        .select_related('device')
        .filter(device=dev)
        .order_by('-timestamp')
        .first()
    )
    if not rec:
        return JsonResponse({'status': 'ok', 'count': 0, 'data': [], 'ai_ready': _LR_MODEL is not None})

    prob = _predict_proba(rec)
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
    return JsonResponse({'status': 'ok', 'count': 1, 'data': data, 'ai_ready': _LR_MODEL is not None})


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
def api_device_matrix(request):
    """
    GET /api/device-matrix/
    返回全部设备最新一条传感记录 + AI anomaly_score，
    按 anomaly_score 降序排列（最危险的设备排第一）。

    V1.2.1 修正：统一使用最新单条记录进行 AI 推断。
    原"随机抽 200 选 1"逻辑会在历史数据与实时流数据混合期导致风险值随机闪烁，
    改为始终取 timestamp 最新的记录，确保显示值稳定且与实时流保持一致。
    """
        # ── V1.4.3 动态时间窗口 OEE 计算 ──
    from django.utils import timezone
    from django.utils.timezone import localtime
    from django.db.models import Sum
    
    local_now = localtime(timezone.now())
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)

    devices = DeviceInfo.objects.all()
    rows = []
    
    for dev in devices:
        # 1. 获取最新记录用于展示基础信息与 AI 推断
        latest = (
            ProductionSensorData.objects
            .filter(device=dev)
            .order_by('-timestamp')
            .first()
        )
        
        if latest is None:
            prob = None
            process = '--'
            sc, sp, fv = 0, 0, 0
            oee_val = 0.0  # 缺省为 0
        else:
            prob    = _predict_proba(latest)
            process = latest.machining_process
            sc      = latest.spindle_current
            sp      = latest.spindle_power
            fv      = latest.feed_velocity
            
            # == V2.1.2 滑动窗口 OEE 计算 (最近 30 分钟瞬时快照) ==
            from datetime import timedelta
            window_start = local_now - timedelta(minutes=30)
            window_recs = ProductionSensorData.objects.filter(device=dev, timestamp__gte=window_start)
            first_rec = window_recs.order_by('timestamp').first()
            
            if first_rec:
                agg = window_recs.aggregate(s=Sum('actual_output'), i=Sum('input_qty'))
                actual_out = agg['s'] or 0
                input_qty = agg['i'] or 0
                
                # ΔT = Now - Window First Record Timestamp (滑动窗口墙钟法)
                delta_t_hours = (local_now - first_rec.timestamp).total_seconds() / 3600.0
                
                # 启动阶段/样本过少保护：不足 5 分钟则按 5 分钟基准计算，防止数值爆表
                delta_t_hours = max(delta_t_hours, 5 / 60.0)
                
                # 理论最大产量 = 标准产能 * 物理历经时长 (小时)
                theo_max = dev.standard_capacity * delta_t_hours
                
                if theo_max > 0:
                    # 计算 P 和 Q
                    p_val = min(actual_out / theo_max, 1.0)
                    q_val = min(actual_out / input_qty, 1.0) if input_qty > 0 else 1.0
                    oee_val = round(p_val * q_val, 4)
                else:
                    oee_val = 0.0
            else:
                oee_val = 0.0

        # 最新未处理报警
        latest_alert = (
            AnomalyAlertLog.objects
            .filter(record__device=dev, is_handled=False)
            .order_by('-alert_time')
            .first()
        )

        rows.append({
            'device_id':        dev.id,
            'device_name':      dev.device_name,
            'device_type':      dev.device_type or '',
            'current_status':   dev.current_status,
            'machining_process': process,
            'spindle_current':  sc,
            'spindle_power':    sp,
            'feed_velocity':    fv,
            'anomaly_score':    prob,
            'oee':              oee_val,   # 新的动态 OEE
            'ai_alert':         prob is not None and prob > 0.8,
            'unhandled_alert_id': latest_alert.id if latest_alert else None,
            'standard_capacity': dev.standard_capacity,
        })

    # 按 anomaly_score 降序（None 排末位）
    rows.sort(key=lambda r: r['anomaly_score'] if r['anomaly_score'] is not None else -1, reverse=True)

    return JsonResponse({
        'status': 'ok',
        'count':  len(rows),
        'data':   rows,
    })


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
    anomaly_scores   = []

    for rec in records:
        timestamps.append(rec.timestamp.isoformat())
        spindle_currents.append(rec.spindle_current)
        spindle_powers.append(rec.spindle_power)
        prob = _predict_proba(rec)
        anomaly_scores.append(round((prob or 0) * 100, 2))  # 转换为百分比

    device = DeviceInfo.objects.filter(pk=device_id).first()

    return JsonResponse({
        'status':          'ok',
        'device_id':       device_id,
        'device_name':     device.device_name if device else str(device_id),
        'device_type':     device.device_type if device else '',
        'timestamps':      timestamps,
        'spindle_current': spindle_currents,
        'spindle_power':   spindle_powers,
        'anomaly_score':   anomaly_scores,
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
@require_http_methods(['GET', 'POST'])
def api_handle_alert(request, alert_id):
    """
    POST /api/alerts/<alert_id>/handle/
    闭环逻辑：
      1. 将目标报警 is_handled = True
      2. 同设备的其他所有未处理报警一并关闭（避免残留锁定）
      3. 将 device_info.current_status 恢复为 'Running'
    """
    try:
        alert = AnomalyAlertLog.objects.select_related('record__device').get(pk=alert_id)
        device = alert.record.device

        # 1. 关闭本条报警
        alert.is_handled = True
        alert.save(update_fields=['is_handled'])

        # 2. 关闭该设备其他所有未处理报警（批量，避免残留）
        AnomalyAlertLog.objects.filter(
            record__device=device,
            is_handled=False,
        ).update(is_handled=True)

        # 3. 恢复设备状态为 Running
        device.current_status = 'Running'
        device.save(update_fields=['current_status'])

        return JsonResponse({
            'status': 'ok',
            'message': f'报警 {alert_id} 已处理，设备 {device.device_name} 已恢复运行',
            'device_id': device.id,
            'device_status': 'Running',
        })
    except AnomalyAlertLog.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': '报警记录不存在'}, status=404)

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
            AnomalyAlertLog.objects.filter(
                record__device=device,
                is_handled=False
            ).update(is_handled=True)

            device.current_group_id = 1  # V1.4.0: 全新设备组，sc_mean→15.0A，AI风险→0-40%
            device.current_status = 'Idle'  # 下发完成后切到待机，不自动恢复运行
            device.save(update_fields=['current_group_id', 'current_status'])

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
