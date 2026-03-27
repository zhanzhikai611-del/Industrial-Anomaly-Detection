# -*- coding: utf-8 -*-
from datetime import timedelta
from django.utils import timezone
from django.db.models import Sum, Count, Q, Max
from django.db.models.functions import TruncHour
from ..models import DeviceInfo, ProductionSensorData, AnomalyAlertLog, SystemConfig
from . import ai_service

def calculate_rolling_oee(dev, local_now):

    """
    统一 OEE 计算逻辑 (V3.0.9): 30分钟滑动窗口聚合
    """
    window_start = local_now - timedelta(minutes=30)
    window_recs = ProductionSensorData.objects.filter(device=dev, timestamp__gte=window_start)
    first_rec = window_recs.order_by('timestamp').first()
    
    if first_rec:
        agg = window_recs.aggregate(s=Sum('actual_output'), i=Sum('input_qty'))
        actual_out = agg['s'] or 0
        input_qty = agg['i'] or 0
        delta_t_hours = max((local_now - first_rec.timestamp).total_seconds() / 3600.0, 5/60.0)
        theo_max = dev.standard_capacity * delta_t_hours
        
        if theo_max > 0:
            p_val = min(actual_out / theo_max, 1.0)
            q_val = min(actual_out / input_qty, 1.0) if input_qty > 0 else 1.0
            return p_val * q_val
    return 0.0

def get_device_matrix_data():
    """提取自 api_device_matrix 的核心数据计算逻辑，增加批处理优化 (V5.2.4)"""
    local_now = timezone.localtime(timezone.now())
    window_start = local_now - timedelta(minutes=30)
    
    # 1. 批处理优化：一次性获取所有设备最新记录
    latest_ids = ProductionSensorData.objects.values('device').annotate(max_id=Max('id')).values_list('max_id', flat=True)
    latest_map = {r.device_id: r for r in ProductionSensorData.objects.filter(id__in=latest_ids).select_related('device')}
    
    # 2. 批处理优化：一次性获取所有设备 30 分钟聚合数据 (解决 N+1)
    window_aggs = ProductionSensorData.objects.filter(timestamp__gte=window_start).values('device').annotate(
        s=Sum('actual_output'),
        i=Sum('input_qty'),
        first_ts=Max('timestamp') # 近似处理
    )
    agg_map = {a['device']: a for a in window_aggs}

    devices = DeviceInfo.objects.all()
    rows = []
    
    for dev in devices:
        latest = latest_map.get(dev.id)
        
        if latest is None:
            score = 0.0
            process = '--'
            oee_val = 0.0
        else:
            score = ai_service.predict_proba(latest) or 0.0
            process = latest.machining_process
            
            # 使用聚合快照快速计算 OEE
            agg = agg_map.get(dev.id)
            if agg:
                actual_out = agg['s'] or 0
                input_qty = agg['i'] or 0
                # 统一取 30 分钟窗口比例
                delta_t_hours = 0.5 
                theo_max = dev.standard_capacity * delta_t_hours
                p_val = min(actual_out / theo_max, 1.0) if theo_max > 0 else 0.0
                q_val = min(actual_out / input_qty, 1.0) if input_qty > 0 else 1.0
                oee_val = p_val * q_val
            else:
                oee_val = 0.0

        is_running = dev.current_status == 'Running'
        risk_pct = f"{int(score * 100)}%" if is_running else '--'
        risk_cls = (score > 0.75 and 'color-high' or score > 0.45 and 'color-med' or 'color-low') if is_running else 'color-normal'
        oee_pct = f"{int(oee_val * 100)}%" if oee_val > 0 else '--'
        oee_cls = (oee_val < 0.45 and 'color-high' or oee_val < 0.75 and 'color-med' or 'color-low') if oee_val > 0 else 'color-normal'
        status_cls = dev.current_status.lower() if dev.current_status != 'Idle' else 'idle'
        status_label = dev.current_status if dev.current_status != 'Idle' else 'Standby'
        if dev.current_status == 'Down': status_label = 'Stopped'

        rows.append({
            'device_id': dev.id,
            'device_name': dev.device_name,
            'device_type': dev.device_type,
            'machining_process': process,
            'anomaly_score': score,
            'risk_pct': risk_pct,
            'risk_cls': risk_cls,
            'oee_val': oee_val,
            'oee_pct': oee_pct,
            'oee_cls': oee_cls,
            'current_status': dev.current_status,
            'status_cls': status_cls,
            'status_label': status_label,
        })
    
    rows.sort(key=lambda x: x['anomaly_score'], reverse=True)
    return rows

def get_dashboard_stats():
    """
    提取自 views._get_dashboard_stats_context 的核心统计逻辑 (V3.1.0)
    实现看板概览数据的单一事实来源。
    """
    from django.utils.timezone import localtime
    
    local_now = localtime(timezone.now())
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)

    # 1. 设备状态分布 (优化：一次查询完成)
    counts = DeviceInfo.objects.aggregate(
        run=Count('id', filter=Q(current_status='Running')),
        idle=Count('id', filter=Q(current_status='Idle')),
        down=Count('id', filter=Q(current_status='Down')),
        total=Count('id')
    )
    running_count = counts['run']
    idle_count    = counts['idle']
    down_count    = counts['down']
    total_devices = counts['total'] or 25

    # 2. 拉取今日流水进行聚合计算 (取最近 500 条估算性能 P 值)
    today_qs = list(
        ProductionSensorData.objects
        .filter(timestamp__gte=today_start)
        .select_related('device')
        .order_by('-timestamp')[:600]
    )

    # ── A（可用性）────────────────
    avg_a = round(running_count / total_devices, 4) if total_devices > 0 else 0.0

    # ── P（表现度 Performance）────────────────
    # 逻辑说明：反映“此时此刻”的实时作战能力。基于最近 3000 条跨设备混抽样本（约覆盖全线 5-10 分钟数据）。
    _sample_actual = sum(r.actual_output or 0 for r in today_qs)
    _sample_records = len(today_qs)
    
    # 运行总产能：当前 Running 设备的时产之和
    _theoretical_rate = DeviceInfo.objects.filter(current_status='Running').aggregate(total_cap=Sum('standard_capacity'))['total_cap'] or 0
    
    if _sample_records > 0 and _theoretical_rate > 0:
        # V3.1.5 高灵敏逻辑: 
        # 样本量 600 条（约 1 分钟快照），实现近乎实时的效率响应
        _earliest_ts = today_qs[-1].timestamp
        _time_span_hours = (local_now - _earliest_ts).total_seconds() / 3600.0
        
        # 全局保护锁降低至 1 分钟 (0.01666h)，允许修复后快速回升
        _time_span_hours = max(_time_span_hours, 1/60.0)
        
        _rate_per_hour = _sample_actual / _time_span_hours
        avg_p = round(min(_rate_per_hour / _theoretical_rate, 1.0), 4)
    else:
        avg_p = 0.0

    # ── Q（良率 Quality）────────────────
    # 直接引用全厂今日累计产出的良品数与投入总数的比例 (物理实测逻辑)
    daily_target = (DeviceInfo.objects.aggregate(total_cap=Sum('standard_capacity'))['total_cap'] or 0) * 8
    totals = ProductionSensorData.objects.filter(timestamp__gte=today_start).aggregate(
        actual_total=Sum('actual_output'),
        input_total=Sum('input_qty')
    )
    total_output = totals['actual_total'] or 0
    total_input = totals['input_total'] or 0

    avg_q = round(total_output / total_input, 4) if total_input > 0 else 1.0

    # ── OEE 综合率 ─────────────────
    avg_oee = round(avg_a * avg_p * avg_q, 4)

    # ── 报警统计数据 ────────────────
    unhandled_count = AnomalyAlertLog.objects.filter(is_handled=False).count()
    alerts = (AnomalyAlertLog.objects
             .select_related('record', 'record__device')
             .order_by('-alert_time')[:7])
    
    # 预处理报警以便前端渲染
    for alert in alerts:
        score = alert.anomaly_score or 0.75
        alert.risk_pct = int(score * 100)
        if score > 0.8: alert.risk_color = "#F5222D"
        elif score > 0.6: alert.risk_color = "#FAAD14"
        else: alert.risk_color = "#52C41A"

    alert_dist = list(
        AnomalyAlertLog.objects
        .filter(alert_time__gte=today_start)
        .values('alert_type')
        .annotate(cnt=Count('id'))
        .order_by('-cnt')
    )

    # ── 小时产量统计 (最近 8 小时) (V3.2.1 性能优化：混合聚合模式) ──
    # [V3.2.2] 兼容性修复：由于部分 MySQL 环境未挂载时区表，改用“先查后算”模式，避开 TruncHour 报错
    eight_hours_ago = (local_now - timedelta(hours=7)).replace(minute=0, second=0, microsecond=0)
    
    # 获取近 8 小时所有流水（通过索引过滤，数据量可控，避免全表扫描）
    hourly_recs = (
        ProductionSensorData.objects
        .filter(timestamp__gte=eight_hours_ago)
        .values_list('timestamp', 'actual_output')
    )
    
    db_map = {}
    for ts, out in hourly_recs:
        # [V3.2.3] TZ 对齐：数据库 fetch 回的是 UTC，必须转为本地时区后再进行截断匹配
        local_ts = timezone.localtime(ts) 
        h_key = local_ts.replace(minute=0, second=0, microsecond=0)
        db_map[h_key] = db_map.get(h_key, 0) + (out or 0)

    hourly_labels = []
    hourly_output = []
    for i in range(8):
        target_h = eight_hours_ago + timedelta(hours=i)
        hourly_labels.append(target_h.strftime('%-H'))
        hourly_output.append(db_map.get(target_h, 0))

    return {
        'avg_oee': avg_oee,
        'oee_pct': f"{round(avg_oee * 100, 1)}%",
        'oee_q_val': f"{round(avg_q * 100, 1)}%",
        'oee_p_val': f"{round(avg_p * 100, 1)}%",
        'oee_a_val': f"{round(avg_a * 100, 1)}%",
        'oee_q_bar': int(avg_q * 100),
        'oee_p_bar': int(avg_p * 100),
        'oee_a_bar': int(avg_a * 100),
        'oee_bar_width': int(avg_oee * 100),
        'hourly_labels': hourly_labels,
        'hourly_output': hourly_output,
        'total_output': total_output,
        'total_input': total_input,
        'daily_target': daily_target,
        'prod_rate_pct': f"{round((total_output / daily_target * 100), 1) if daily_target > 0 else 0.0}%",
        'prod_bar_width': min(100, int((total_output / daily_target * 100) if daily_target > 0 else 0)),
        'running_devices': running_count,
        'idle_devices': idle_count,
        'down_devices': down_count,
        'total_devices': total_devices,
        'unhandled_alerts': unhandled_count,
        'alert_distribution': alert_dist,
        'total_defects': total_input - total_output,
        'alerts': alerts, # 追加供页面渲染使用
    }

def get_factory_snapshot():
    """
    提取自 consumers.FactoryConsumer._build_snapshot 的核心逻辑 (V3.1.0)
    实现数字孪生工厂全量快照的单一事实来源。
    """
    from django.utils.timezone import localtime
    
    now = timezone.now()
    local_now = localtime(now)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    devices = list(DeviceInfo.objects.all().order_by('id'))
    cfg = SystemConfig.get()

    rows = []
    for dev in devices:
        latest = (
            ProductionSensorData.objects
            .filter(device=dev)
            .order_by('-timestamp')
            .first()
        )
        # 统一调用 ai_service，消除消费者内的 joblib.load (V3.1.2)
        prob = ai_service.predict_proba(latest) if latest else None

        # 统一调用 calculate_rolling_oee (V3.1.3)
        oee_val = calculate_rolling_oee(dev, local_now)

        # Session yield：今日产生的累计良品
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
