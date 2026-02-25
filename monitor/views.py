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

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt

from .models import (
    SystemConfig, DeviceInfo,
    ProductionSensorData, AnomalyAlertLog,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  页面路由
# ═══════════════════════════════════════════════════════════════════

def dashboard_view(request):
    """首页 Dashboard"""
    from django.shortcuts import render
    return render(request, 'monitor/dashboard.html')

def device_view(request):
    """Device 设备页"""
    from django.shortcuts import render
    return render(request, 'monitor/device.html')

def event_view(request):
    """Event 报警事件页"""
    from django.shortcuts import render
    return render(request, 'monitor/event.html')

def setting_view(request):
    """Setting 系统设置页"""
    from django.shortcuts import render
    return render(request, 'monitor/setting.html')


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
        'updated_at': cfg.updated_at.isoformat() if cfg.updated_at else None,
    })


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
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # ── 设备状态分布 ──────────────────────────────────────────────
    running_count = DeviceInfo.objects.filter(current_status='Running').count()
    idle_count    = DeviceInfo.objects.filter(current_status='Idle').count()
    down_count    = DeviceInfo.objects.filter(current_status='Down').count()
    total_devices = DeviceInfo.objects.count() or 25

    # ── 拉取今日最近流水用于聚合 ──────────────────────────────────
    today_qs = list(
        ProductionSensorData.objects
        .filter(timestamp__gte=today_start)
        .select_related('device')
        .order_by('-timestamp')[:500]
    )

    total_output, total_input = 0, 0
    sum_actual_output = 0
    sum_theoretical_output = 0

    for rec in today_qs:
        total_output += rec.actual_output or 0
        total_input  += rec.input_qty or 0
        sum_actual_output += rec.actual_output or 0
        # 理论产出 = (loading_time - downtime) / 60 × standard_capacity
        if rec.loading_time and rec.loading_time > 0:
            try:
                cap = rec.device.standard_capacity
                actual_run = max(0, rec.loading_time - rec.downtime)
                theo = (actual_run / 60.0) * cap
                sum_theoretical_output += theo
            except Exception:
                pass

    # ═══════════════════════════════════════════════════════════════
    #  OEE 级联计算模型
    # ═══════════════════════════════════════════════════════════════

    # ── A（可用性）= Running设备比率 × 排班修正系数 ────────────────
    #    排班修正：假设标准班为 25 台全部在线，实际可能有排班空置
    SHIFT_FACTOR = 0.998  # 日班排班修正系数（约 0.2% 换班损耗）
    avg_a = round((running_count / total_devices) * SHIFT_FACTOR, 4)

    # ── P（性能）= Σ实际产出 / Σ理论最大产出 ──────────────────────
    #    理论最大产出 = 该设备实际运转时间下的标准满载产量
    if sum_theoretical_output > 0:
        avg_p = round(sum_actual_output / sum_theoretical_output, 4)
    else:
        avg_p = None

    # ── Q（良率）= 1 - (高风险设备数 × 次品系数) / 总设备数 ───────
    #    高风险设备 = anomaly_score > 0.75 的设备（来自 AI 推断）
    #    次品系数 = 0.08（工业经验值：高风险设备平均产出 8% 次品）
    DEFECT_COEFF = 0.08
    # 统计高风险设备数：取每台设备最新一条记录推断
    high_risk_count = 0
    if _LR_MODEL is not None:
        seen_devices = set()
        for rec in today_qs:
            dev_id = rec.device_id
            if dev_id in seen_devices:
                continue
            seen_devices.add(dev_id)
            prob = _predict_proba(rec)
            if prob is not None and prob > 0.75:
                high_risk_count += 1
    avg_q = round(1.0 - (high_risk_count * DEFECT_COEFF) / total_devices, 4)

    # ── OEE = A × P × Q（严格连乘）────────────────────────────────
    if avg_a is not None and avg_p is not None and avg_q is not None:
        avg_oee = round(avg_a * avg_p * avg_q, 4)
    else:
        avg_oee = None

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
        # 产量
        'total_output':    total_output,
        'total_input':     total_input,
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
    GET /api/stream/ — 模拟实时数据流
    从 Running 设备近期数据中随机采样 1 条，将 timestamp 替换为当前时间，
    使前端折线图每次轮询都能获得新时间戳，从而持续更新。
    """
    import random as _random

    # 从 Running 设备最近 5000 条记录中随机抽 1 条
    qs = (
        ProductionSensorData.objects
        .select_related('device')
        .filter(device__current_status='Running')
        .order_by('-timestamp')[:5000]
    )
    records = list(qs)
    if not records:
        return JsonResponse({'status': 'ok', 'count': 0, 'data': [], 'ai_ready': _LR_MODEL is not None})

    rec  = _random.choice(records)
    prob = _predict_proba(rec)
    now  = timezone.now()

    data = [{
        'id':                  rec.id,
        'timestamp':           now.isoformat(),          # ← 当前时间，保证每次不同
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
    return JsonResponse({'status': 'ok', 'count': len(data), 'data': data})


# ═══════════════════════════════════════════════════════════════════
#  API 4（新增）：设备风险矩阵 /api/device-matrix/
# ═══════════════════════════════════════════════════════════════════

@require_http_methods(['GET'])
def api_device_matrix(request):
    """
    GET /api/device-matrix/
    返回全部设备最新一条传感记录 + AI anomaly_score，
    按 anomaly_score 降序排列（最危险的设备排第一）。
    """
    import random as _random

    devices = DeviceInfo.objects.all()
    rows = []
    for dev in devices:
        if dev.current_status == 'Running':
            # 从 Running 设备最近 200 条记录中随机抽 1 条，模拟实时波动
            qs = list(ProductionSensorData.objects.filter(device=dev).order_by('-timestamp')[:200])
            latest = _random.choice(qs) if qs else None
        else:
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
        else:
            prob    = _predict_proba(latest)
            process = latest.machining_process
            sc      = latest.spindle_current
            sp      = latest.spindle_power
            fv      = latest.feed_velocity

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
            'anomaly_score':    prob,          # AI 输出置信度
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
#  API 7（新增）：标记报警已处理 /api/alerts/<id>/handle/
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
