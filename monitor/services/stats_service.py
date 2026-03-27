# -*- coding: utf-8 -*-
from datetime import timedelta
from django.utils import timezone
from django.db.models import Sum, Count, Q, Max
from django.db.models.functions import TruncHour
from ..models import DeviceInfo, ProductionSensorData, AnomalyAlertLog, SystemConfig
from . import ai_service
from .cache_service import CacheService

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
            # P 使用总投入（含次品）衡量速度，Q 使用良品/总投入衡量质量
            p_val = min(input_qty / theo_max, 1.0)
            q_val = min(actual_out / input_qty, 1.0) if input_qty > 0 else 1.0
            return p_val * q_val
    return 0.0

def get_device_matrix_data():
    """提取自 api_device_matrix 的核心数据计算逻辑 (V3.3.1 优化版)"""
    # 1. 初始化基础环境
    local_now = timezone.localtime(timezone.now())
    window_start = local_now - timedelta(minutes=30)
    devices = list(DeviceInfo.objects.all())
    
    # 2. 批量聚合 30 分钟滑动窗口数据 (V3.3.1 性能优化：单次查询代替 25 次)
    from django.db.models import Sum, Min
    agg_qs = ProductionSensorData.objects.filter(timestamp__gte=window_start) \
        .values('device_id') \
        .annotate(
            s=Sum('actual_output'), 
            i=Sum('input_qty'),
            t_min=Min('timestamp')
        )
    agg_map = {row['device_id']: row for row in agg_qs}
    
    def calculate_oee_from_agg(dev, agg):
        """基于聚合结果计算 OEE，保持与详情页逻辑对齐"""
        if not agg or not agg['t_min']: return 0.0
        # 实效理论跨度：从窗口第一条记录开始到当前，不少于 5 分钟
        delta_hours = max((local_now - agg['t_min']).total_seconds() / 3600.0, 5/60.0)
        theo_max = dev.standard_capacity * delta_hours
        actual_out = agg['s'] or 0
        input_qty  = agg['i'] or actual_out # 投入兜底
        input_qty  = agg['i'] or actual_out # 投入兜底
        p_val = min(input_qty / theo_max, 1.0) if theo_max > 0 else 0.0
        q_val = min(actual_out / input_qty, 1.0) if input_qty > 0 else 1.0
        return p_val * q_val

    # 3. 尝试从 Redis 快照获取实时状态
    snapshots = CacheService.get_all_device_snapshots()
    rows = []
    
    if snapshots:
        snap_map = {int(s['device_id']): s for s in snapshots}
        for dev in devices:
            s = snap_map.get(dev.id, {})
            score = s.get('anomaly_score', 0.0)
            is_running = dev.current_status == 'Running'
            agg = agg_map.get(dev.id)
            oee_val = calculate_oee_from_agg(dev, agg)
            
            rows.append({
                'device_id': dev.id,
                'device_name': dev.device_name,
                'device_type': dev.device_type or '--', 
                'machining_process': s.get('machining_process', '--'),
                'anomaly_score': score,
                'risk_pct': f"{int(score * 100)}%" if is_running else '--',
                'risk_cls': (score > 0.75 and 'color-high' or score > 0.45 and 'color-med' or 'color-low') if is_running else 'color-normal',
                'oee_val': oee_val,
                'oee_pct': f"{int(oee_val * 100)}%" if is_running else '--',
                'oee_cls': (oee_val < 0.45 and 'color-high' or oee_val < 0.75 and 'color-med' or 'color-low') if is_running else 'color-normal',
                'current_status': dev.current_status,
                'status_cls': dev.current_status.lower() if dev.current_status != 'Idle' else 'idle',
                'status_label': 'Stopped' if dev.current_status == 'Down' else (dev.current_status if dev.current_status != 'Idle' else 'Standby')
            })
    else:
        # Fallback path: 从数据库直接获取最新一条记录用于过程名和 AI 分数
        from . import ai_service
        from django.db.models import Max
        latest_recs = ProductionSensorData.objects.filter(id__in=ProductionSensorData.objects.values('device_id').annotate(max_id=Max('id')).values('max_id'))
        latest_map = {r.device_id: r for r in latest_recs}
        
        for dev in devices:
            latest = latest_map.get(dev.id)
            agg = agg_map.get(dev.id)
            oee_val = calculate_oee_from_agg(dev, agg)
            
            if latest is None:
                score, process = 0.0, '--'
            else:
                score = ai_service.predict_proba(latest) or 0.0
                process = latest.machining_process

            is_running = dev.current_status == 'Running'
            rows.append({
                'device_id': dev.id, 'device_name': dev.device_name, 'device_type': dev.device_type or '--',
                'machining_process': process, 'anomaly_score': score,
                'risk_pct': f"{int(score * 100)}%" if is_running else '--',
                'risk_cls': (score > 0.75 and 'color-high' or score > 0.45 and 'color-med' or 'color-low') if is_running else 'color-normal',
                'oee_val': oee_val, 'oee_pct': f"{int(oee_val * 100)}%" if is_running else '--',
                'oee_cls': (oee_val < 0.45 and 'color-high' or oee_val < 0.75 and 'color-med' or 'color-low') if is_running else 'color-normal',
                'current_status': dev.current_status, 'status_cls': dev.current_status.lower() if dev.current_status != 'Idle' else 'idle',
                'status_label': 'Stopped' if dev.current_status == 'Down' else (dev.current_status if dev.current_status != 'Idle' else 'Standby')
            })

    # 4. 默认排序：Running 靠前，其次按风险降序
    rows.sort(key=lambda x: (x['current_status'] != 'Running', -x['anomaly_score']))
    return rows

def get_dashboard_stats():
    # 1. 核心产出基准校验 (V3.3.1) —— 解决“次品过万”与“良品不动”的根本
    from django.utils.timezone import localtime
    local_now = localtime(timezone.now())
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # 物理数据库汇总 (基准)
    totals = ProductionSensorData.objects.filter(timestamp__gte=today_start).aggregate(
        actual_total=Sum('actual_output'), 
        input_total=Sum('input_qty')
    )
    db_actual = totals['actual_total'] or 0
    db_input  = totals['input_total'] or 0

    # 尝试读取 Redis 缓存
    cached_data = CacheService.get_dashboard_cache()
    realtime_output = CacheService.get_daily_output()
    
    # 如果 Redis 里的良品数落后于数据库基准，强制对齐
    if realtime_output is None or realtime_output < db_actual:
        realtime_output = db_actual
        CacheService.set_daily_output(realtime_output)

    # 如果有全量缓存，则返回带基准修正的缓存数据
    if cached_data:
        cached_data['total_output'] = realtime_output
        # 实时修正：次品数必须基于数据库最新的投入数和最新的良品数
        # 这里的 db_input 是当前物理值，比 cached_data 里的快
        cached_data['total_input'] = db_input
        cached_data['total_defects'] = max(0, db_input - realtime_output)
        
        # 修正达成率进度
        target = cached_data.get('daily_target', 1)
        cached_data['prod_rate_pct'] = f"{round((realtime_output / target * 100), 1) if target > 0 else 0.0}%"
        cached_data['prod_bar_width'] = min(100, int((realtime_output / target * 100) if target > 0 else 0))
        return cached_data

    # 2. 缓存失效，执行全量聚合 (UI 各组件指标重算)

    # 2. 计算实时设备状态计数 (用于看板底部的实时指示灯/数字)
    counts = DeviceInfo.objects.aggregate(
        run=Count('id', filter=Q(current_status='Running')),
        idle=Count('id', filter=Q(current_status='Idle')),
        down=Count('id', filter=Q(current_status='Down')),
        total=Count('id')
    )
    running_count, idle_count, down_count, total_devices = counts['run'], counts['idle'], counts['down'], counts['total'] or 25

    # 3. 计算 A & P (Performance & Availability 移动平均版) —— (V3.3.1-B 专项修复)
    # 取最近 600 条记录来平滑指标，使 A 指标能“记住”短暂的微型停机
    recent_qs = list(ProductionSensorData.objects.all().order_by('-id').select_related('device')[:600])
    
    avg_a, avg_p = 1.0, 0.0
    if recent_qs:
        total_run_time = 0.0
        total_load_time = 0.0
        total_actual_pieces = 0.0
        total_theo_pieces = 0.0
        
        for r in recent_qs:
            # [A] 可用性：计算 600 条流水内的物理稼动率
            load = r.loading_time or 0.05
            dt = r.downtime or 0.0
            total_run_time += (load - dt)
            total_load_time += load
            
            # [P] 表现度：计算运行期间的产出负荷
            # 注意：仅统计记录发生时设备处于 Running 状态的情况
            if r.device.current_status == 'Running':
                total_actual_pieces += (r.input_qty or 0)
                theo_seg = (r.device.standard_capacity or 0) * load / 60.0
                total_theo_pieces += theo_seg
        
        avg_a = round(total_run_time / total_load_time, 4) if total_load_time > 0 else 1.0
        if total_theo_pieces > 0:
            avg_p = round(min(total_actual_pieces / total_theo_pieces, 1.0), 4)

    # 4. 计算 Q (Quality) 与 OEE 聚合值
    daily_target = (DeviceInfo.objects.aggregate(total_cap=Sum('standard_capacity'))['total_cap'] or 0) * 8
    # [V3.3.1] 确保 Q 指标的分子分母口径一致
    # 如果处于数据重载期，统一使用数据库聚合值以防止 Redis 计数器与 DB 记录不同步
    totals = ProductionSensorData.objects.filter(timestamp__gte=today_start).aggregate(actual_total=Sum('actual_output'), input_total=Sum('input_qty'))
    db_actual = totals['actual_total'] or 0
    db_input  = totals['input_total'] or 0
    total_output = CacheService.get_daily_output() or db_actual
    
    # 强制同步：如果 Redis 值远小于 DB 值（可能刚重启），以 DB 为准
    if total_output < db_actual:
        total_output = db_actual
        
    total_input = db_input
    avg_q = min(round(total_output / total_input, 4), 1.0) if total_input > 0 else 1.0
    avg_oee = round(avg_a * avg_p * avg_q, 4)

    unhandled_count = AnomalyAlertLog.objects.filter(is_handled=False).count()
    alerts = list(AnomalyAlertLog.objects.select_related('record', 'record__device').order_by('-alert_time')[:7])
    for a in alerts:
        score = a.anomaly_score or 0.75
        a.risk_pct = int(score * 100)
        a.risk_color = "#F5222D" if score > 0.8 else "#FAAD14" if score > 0.6 else "#52C41A"

    alert_dist = list(AnomalyAlertLog.objects.filter(alert_time__gte=today_start).values('alert_type').annotate(cnt=Count('id')).order_by('-cnt'))

    eight_hours_ago = (local_now - timedelta(hours=7)).replace(minute=0, second=0, microsecond=0)
    hourly_recs = ProductionSensorData.objects.filter(timestamp__gte=eight_hours_ago).values_list('timestamp', 'actual_output')
    db_map = {}
    for ts, out in hourly_recs:
        local_ts = timezone.localtime(ts) 
        h_key = local_ts.replace(minute=0, second=0, microsecond=0)
        db_map[h_key] = db_map.get(h_key, 0) + (out or 0)

    hourly_labels, hourly_output = [], []
    for i in range(8):
        target_h = eight_hours_ago + timedelta(hours=i)
        hourly_labels.append(target_h.strftime('%-H'))
        hourly_output.append(db_map.get(target_h, 0))

    res = {
        'avg_oee': avg_oee, 'oee_pct': f"{round(avg_oee * 100, 1)}%", 'oee_q_val': f"{round(avg_q * 100, 1)}%",
        'oee_p_val': f"{round(avg_p * 100, 1)}%", 'oee_a_val': f"{round(avg_a * 100, 1)}%",
        'oee_q_bar': int(avg_q * 100), 'oee_p_bar': int(avg_p * 100), 'oee_a_bar': int(avg_a * 100),
        'oee_bar_width': int(avg_oee * 100), 'hourly_labels': hourly_labels, 'hourly_output': hourly_output,
        'total_output': total_output, 'total_input': total_input, 'daily_target': daily_target,
        'prod_rate_pct': f"{round((total_output / daily_target * 100), 1) if daily_target > 0 else 0.0}%",
        'prod_bar_width': min(100, int((total_output / daily_target * 100) if daily_target > 0 else 0)),
        'running_devices': running_count, 'idle_devices': idle_count, 'down_devices': down_count,
        'total_devices': total_devices, 'avg_a': avg_a, 'avg_p': avg_p, 'avg_q': avg_q, # 补全基础浮点值
        'unhandled_alerts': unhandled_count, 'alert_distribution': alert_dist,
        'total_defects': total_input - total_output, 'alerts': alerts,
    }
    CacheService.set_dashboard_cache(res)
    return res

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
            'anomaly_score':    float(round(prob, 4)) if prob is not None else None,
            'oee':              oee_val,
            'session_yield':    session_yield,
        })

    return {
        'type':      'factory_snapshot',
        'timestamp': local_now.strftime('%H:%M:%S'),
        'is_stream_active': cfg.is_realtime_active,
        'devices':   rows,
    }
