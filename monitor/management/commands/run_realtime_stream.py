# -*- coding: utf-8 -*-
"""
monitor/management/commands/run_realtime_stream.py
实时数据流守护进程 —— Django Management Command

用法：
  venv/bin/python manage.py run_realtime_stream

行为：
  - 每 3 秒轮询 SystemConfig.is_realtime_active
  - True  → 为每台 DeviceInfo 追加一条当前时刻的仿真传感数据
  - False → 打印 "Streaming paused..." 并继续等待
  - 每 CLEANUP_EVERY 次心跳执行一次滚动清理，删除超过 RETENTION_DAYS 天的旧数据
  - Ctrl+C 安全退出
"""

import time
import random
import logging
import numpy as np
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from monitor.models import (
    DeviceInfo, ProductionSensorData, AnomalyAlertLog, SystemConfig, SystemLog
)
from monitor.services.cache_service import CacheService
from monitor.services import ai_service

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  从 simulate_real_cnc_data.py 提取的 CNC 统计参数（保持一致）
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
#  分组参数（与 simulate_real_cnc_data.py 一致）
# ═══════════════════════════════════════════════════════════════════

GROUP_PARAMS = [
    (15.0, 0.13), # Group 1: 1-5 (New) -> ~0-40% Probability
    (24.0, 0.18), # Group 2: 6-10 (Semi-new) -> ~30-50%
    (27.0, 0.25), # Group 3: 11-15 (Normal) -> ~41-75%
    (31.0, 0.32), # Group 4: 16-20 (Old) -> ~60-85%
    (40.0, 0.40), # Group 5: 21-25 (Faulty) -> ~76-100%
]

# 报警阈值（与 simulate_real_cnc_data.py 保持一致）
ALERT_THRESHOLDS = {
    'spindle_current': 25.0,   # > 25A → HIGH_CURRENT
    'spindle_power':    0.20,  # > 0.20W → HIGH_POWER
    'feed_velocity':    17.0,  # |v| > 17 mm/s → LOW_VELOCITY
}

# 加工阶段权重（基于真实 CSV 分布）
_STAGES = [
    ('Layer 1 Up',    4085), ('Layer 2 Up',    3104),
    ('Layer 3 Up',    2794), ('Layer 1 Down',  2655),
    ('Layer 2 Down',  2528), ('Layer 3 Down',  2354),
    ('Repositioning', 3377),
]
STAGE_LABELS = [s[0] for s in _STAGES]
STAGE_PROBS  = [s[1] / sum(w for _, w in _STAGES) for s in _STAGES]

ANOMALY_PROB  = 0.15    # 磨损工况概率（与历史脚本保持一致）
STREAM_INTERVAL = 3     # 守护进程轮询间隔（秒）
STEP_SECS       = float(STREAM_INTERVAL)          # 每步真实时长（秒）
STEP_MINS       = STEP_SECS / 60.0               # 每步真实时长（分钟）= 0.05

# ── 滚动数据保留策略 ──────────────────────────────────────────────────
RETENTION_DAYS = 1      # 保留最近 N 天的传感与报警数据
CLEANUP_EVERY  = 300    # 每 N 次心跳执行一次清理（300 × 3s ≈ 15 分钟）


# ═══════════════════════════════════════════════════════════════════
#  核心生成函数（复用 simulate_real_cnc_data.py 同款逻辑）
# ═══════════════════════════════════════════════════════════════════

def _gen_running_record(device, ts, group_idx):
    """
    BUG FIX (V1.4.2): 原实现使用固定 LOADING_TIME=60分钟 计算每条 3秒 记录的产量，
    导致每条 3 秒记录产出约 1000 件（等于按整小时满负荷计算），造成日产量爆炸。

    修复方案：使用 V5 脉冲累加器（yield_buffer），与 simulate_real_cnc_data.py 一致：
      - 每步理论产量 = cap / 3600 * STEP_SECS（精确时间份额，一步约 0.833 件）
      - yield_buffer 积累小数部分，整数溢出时才计入一件产量
      - loading_time 写入实际步长 STEP_MINS（0.05 分钟），而非固定 60 分钟
    """
    sc_mean, sp_mean = GROUP_PARAMS[group_idx]
    sc = float(np.random.normal(sc_mean, 1.5))
    sp = float(max(0, np.random.normal(sp_mean, 0.015)))
    
    # V1.4.2 修复：防止正常进给速度随机值过小而持续触发 LOW_VELOCITY 报警
    fv_mean = 20.0 if group_idx < 3 else 18.0
    fv = float(np.random.normal(fv_mean, 2.0))

    # 人工注入物理极限预警，以确保不同类型异常均匀分布
    cfg = SystemConfig.get()
    if group_idx >= 3 and random.random() < (ANOMALY_PROB * 0.1): 
        spike_type = random.choice(['HIGH_CURRENT', 'HIGH_POWER', 'LOW_VELOCITY'])
        if spike_type == 'HIGH_CURRENT':
            sc = cfg.spindle_current_high + random.uniform(2.0, 10.0)
        elif spike_type == 'HIGH_POWER':
            sp = cfg.spindle_power_high + random.uniform(5.0, 20.0)
        elif spike_type == 'LOW_VELOCITY':
            fv = random.uniform(0.0, max(0.1, cfg.feed_velocity_low - 0.1))

    machining_proc = np.random.choice(STAGE_LABELS, p=STAGE_PROBS)

    is_worn = (group_idx >= 3)
    cap = device.standard_capacity

    # [V3.3.1] 视觉效果优化：显著微抖动 (增加卡顿深度，使图表产生可见抖动)
    if group_idx == 0:
        dt_prob = 0.0001
    elif group_idx == 1:
        dt_prob = 0.001
    elif group_idx == 2:
        dt_prob = 0.005
    elif group_idx == 3:
        dt_prob = random.uniform(0.04, 0.08)
    else:
        dt_prob = random.uniform(0.08, 0.12)

    if random.random() < dt_prob:
        # 触发时停机占比调大 (40%-80%)，形成图表“毛刺”感
        dt = round(random.uniform(STEP_MINS * 0.4, STEP_MINS * 0.8), 2)
    else:
        dt = 0.0

    # 运行时间：扣除本步中的微型停机
    run_step_min = STEP_MINS - dt
    input_fraction = (cap / 60.0) * run_step_min

    # ── 性能 P：主轴倍率下降 ─────────────────────────────────────────
    if group_idx == 0:
        perf_ratio = random.uniform(0.96, 1.00)
    elif group_idx == 1:
        perf_ratio = random.uniform(0.93, 0.98)
    elif group_idx == 2:
        perf_ratio = random.uniform(0.88, 0.94)
    elif group_idx == 3:
        perf_ratio = random.uniform(0.80, 0.88)
    else:
        perf_ratio = random.uniform(0.65, 0.82)

    # ── V5 脉冲累加器：每步精确时间份额，积累到整数才出一件 ─────────
    # 累加本步产出脉冲到 yield_buffer (增量驱动)
    pulse = input_fraction * perf_ratio
    device.yield_buffer = getattr(device, 'yield_buffer', 0.0) + pulse

    if dt > 0:   # 停机：本步产出=0
        good_output   = 0
        input_qty_val = 0
        final_perf = 0.0
    else:
        if device.yield_buffer >= 1.0:
            # 物理限制：3秒内产量无论如何堆积，单次输出强限制为不超过1件，
            # 避免瞬间 OEE 性能 P 除法溢出超 100%
            produced = min(1, int(device.yield_buffer))
            device.yield_buffer -= produced
        else:
            produced = 0

        # ── 良率 Q：次品 buffer 精确积累 ────────────────────────────
        if group_idx <= 1:
            defect_rate = random.uniform(0.0, 0.01)
        elif group_idx == 2:
            defect_rate = random.uniform(0.01, 0.04)
        elif group_idx == 3:
            defect_rate = random.uniform(0.03, 0.08)
        else:
            defect_rate = random.uniform(0.06, 0.15)

        defects = 0
        if produced > 0:
            device.defect_buffer = getattr(device, 'defect_buffer', 0.0) + (produced * defect_rate)
            if device.defect_buffer >= 1.0:
                defects = min(1, int(device.defect_buffer))
                device.defect_buffer -= defects

        good_output   = max(0, produced - defects)
        input_qty_val = produced
        final_perf = perf_ratio

    rec = ProductionSensorData(
        device            = device,
        timestamp         = ts,
        spindle_current   = round(sc, 4),
        spindle_power     = round(sp,   4),
        feed_velocity     = round(fv,   4),
        machining_process = machining_proc,
        tool_condition    = is_worn,
        loading_time      = STEP_MINS,     # 实际步长（0.05分钟），而非固定60分钟
        downtime          = dt,
        input_qty         = input_qty_val,
        actual_output     = good_output,
    )
    return rec, final_perf

def _gen_idle_record(device, ts):
    sc = float(max(0, np.random.normal(0.3, 0.05)))
    sp = float(max(0, np.random.normal(0.002, 0.001)))
    fv = float(np.random.normal(0.0, 0.02))
    rec = ProductionSensorData(
        device=device, timestamp=ts,
        spindle_current=round(sc, 4), spindle_power=round(sp, 4), feed_velocity=round(fv, 4),
        machining_process='Idle', tool_condition=False,
        loading_time=0.0, downtime=0.0,
        input_qty=0, actual_output=0,   # 待机不投料
    )
    return rec, 0.0

def _gen_down_record(device, ts):
    rec = ProductionSensorData(
        device=device, timestamp=ts,
        spindle_current=0.0, spindle_power=0.0, feed_velocity=0.0,
        machining_process='Down', tool_condition=False,
        loading_time=0.0, downtime=STEP_MINS,   # 停机时长=实际步长
        input_qty=0, actual_output=0,            # 停机不投料
    )
    return rec, 0.0


def _check_and_create_alert(record: ProductionSensorData) -> bool:
    """
    检查刚保存的 record 是否触发物理报警阈值。
    若触发则写入 AnomalyAlertLog，返回 True。
    """
    cfg = SystemConfig.get()
    sc  = abs(record.spindle_current)
    sp  = record.spindle_power
    fv  = abs(record.feed_velocity)

    if sc > cfg.spindle_current_high:
        atype = 'HIGH_CURRENT'
    elif sp > cfg.spindle_power_high:
        atype = 'HIGH_POWER'
    elif fv < cfg.feed_velocity_low:
        atype = 'LOW_VELOCITY'
    else:
        return False

    prob = ai_service.predict_proba(record)
    if prob is None:
        prob = random.uniform(0.60, 0.99)
    AnomalyAlertLog.objects.create(
        record        = record,
        alert_time    = record.timestamp,
        alert_type    = atype,
        anomaly_score = round(prob, 4),
        is_handled    = False,
    )
    return True


# ═══════════════════════════════════════════════════════════════════
#  Management Command
# ═══════════════════════════════════════════════════════════════════

class Command(BaseCommand):
    help = '实时数据流守护进程：受 SystemConfig.is_realtime_active 开关控制，每 3 秒追加一轮数据'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(
            '\n══════════════════════════════════════════\n'
            '  🚀  实时数据流守护进程已启动 (Redis-Hybrid 模式)\n'
            f'  轮询间隔: {STREAM_INTERVAL}s | 异常概率: {ANOMALY_PROB:.0%}\n'
            '  按 Ctrl+C 安全退出\n'
            '══════════════════════════════════════════\n'
        ))

        tick = 0
        # ★ 关键：在 while 循环外初始化设备列表 and yield_buffer
        #   每次循环内只调用 refresh_from_db(fields=[...]) 更新状态，
        #   而不重建对象，从而让 yield_buffer/defect_buffer 持续积累。
        devices = list(DeviceInfo.objects.all())
        for dev in devices:
            dev.yield_buffer  = 0.0
            dev.defect_buffer = 0.0
        known_device_ids = {d.id for d in devices}

        try:
            while True:
                tick += 1
                # 强制重新从数据库拉取最新配置，避免单例对象缓存导致无法接收页面开关指令
                cfg = SystemConfig.get()
                cfg.refresh_from_db()

                if not cfg.is_realtime_active:
                    if tick % 5 == 0:
                        self.stdout.write(f'[{tick:>6}] ⏸  Streaming paused (Waiting for signal)...')
                    time.sleep(STREAM_INTERVAL)
                    continue

                # ── 若设备数量变化则重新加载列表（如新增/删除设备） ──
                current_ids = set(DeviceInfo.objects.values_list('id', flat=True))
                if current_ids != known_device_ids:
                    devices = list(DeviceInfo.objects.all())
                    for dev in devices:
                        if not hasattr(dev, 'yield_buffer'):
                            dev.yield_buffer  = 0.0
                        if not hasattr(dev, 'defect_buffer'):
                            dev.defect_buffer = 0.0
                    known_device_ids = current_ids

                # ── 激活状态：为每台设备生成并追加一条数据 ──────────
                now = timezone.now()

                if not devices:
                    self.stdout.write(self.style.WARNING(
                        f'[{tick:>6}] ⚠  数据库中没有设备'
                    ))
                    time.sleep(STREAM_INTERVAL)
                    continue

                records_created = 0
                alerts_created  = 0

                total_step_output = 0
                for idx, dev in enumerate(devices):
                    # [V3.3.4 Fix] 关键修复：强制从数据库同步最新状态（分组信息），
                    # 避免由于内存对象过期导致 Copilot 修复后性能分不回升的问题。
                    dev.refresh_from_db(fields=['current_group_id', 'current_status', 'maintenance_advice'])
                    
                    status = dev.current_status
                    group_idx = min(max(dev.current_group_id - 1, 0), 4)

                    if status == 'Running':
                        rec_obj, perf_ratio = _gen_running_record(dev, now, group_idx)
                        rec_obj.save()          # 逐条 save 获取 pk，便于立即关联 Alert
                        records_created += 1
                        total_step_output += (rec_obj.actual_output or 0)

                        if _check_and_create_alert(rec_obj):
                            alerts_created += 1
                    elif status == 'Idle':
                        rec_obj, perf_ratio = _gen_idle_record(dev, now)
                        rec_obj.save()
                        records_created += 1
                    else: # Down
                        rec_obj, perf_ratio = _gen_down_record(dev, now)
                        rec_obj.save()
                        records_created += 1
                    
                    # [V3.3.0] 关键修复：同步更新 Redis 快照，供 Dashboard 矩阵及监控列表使用
                    prob = ai_service.predict_proba(rec_obj) or 0.0
                    snapshot = {
                        'device_id': rec_obj.device_id,
                        'device_name': dev.device_name,
                        'device_type': dev.device_type,
                        'current_status': dev.current_status,
                        'spindle_current': float(rec_obj.spindle_current),
                        'spindle_power': float(rec_obj.spindle_power),
                        'feed_velocity': float(rec_obj.feed_velocity),
                        'anomaly_score': float(round(prob, 4)),
                        'oee_val': float(perf_ratio) if dev.current_status == 'Running' else 0.0,
                        'last_update': now.isoformat(),
                        'machining_process': rec_obj.machining_process,
                    }
                    CacheService.update_device_snapshot(dev.id, snapshot)

                # [V3.3.0] 同步 Redis 今日产量计数器
                if total_step_output > 0:
                    CacheService.incr_daily_output(total_step_output)

                log_text = f'写入 {records_created} 条  报警 {alerts_created} 条'
                self.stdout.write(f'[{tick:>6}] ▶  {now:%H:%M:%S}  ' + log_text)

                # 无论 Redis 是否可用，都写入 SystemLog 供长效查询或无 Redis 时的降级方案
                engine_msg = f'ENGINE: {records_created} nodes written successfully. (Session: STREAM-{tick})'
                try:
                    SystemLog.objects.create(message=engine_msg, log_type='engine_log', timestamp=now)
                    # 清理旧日志（保持 100 条）
                    if tick % 20 == 0:
                        ids_to_keep = SystemLog.objects.order_by('-timestamp')[:100].values_list('id', flat=True)
                        SystemLog.objects.exclude(id__in=list(ids_to_keep)).delete()
                except Exception as e:
                    logger.error(f"SystemLog Write Error: {e}")

                # ── 滚动清理：每 CLEANUP_EVERY 次心跳执行一次 ────────
                if tick % CLEANUP_EVERY == 0:
                    cutoff = now - timedelta(days=RETENTION_DAYS)
                    try:
                        # 必须先删子表（AnomalyAlertLog），再删父表（ProductionSensorData）
                        deleted_alerts  = AnomalyAlertLog.objects.filter(alert_time__lt=cutoff).delete()[0]
                        deleted_records = ProductionSensorData.objects.filter(timestamp__lt=cutoff).delete()[0]
                        self.stdout.write(self.style.WARNING(
                            f'[{tick:>6}] 🗑  滚动清理完成：'
                            f'删除 {deleted_records:,} 条传感记录、{deleted_alerts:,} 条报警'
                            f'（保留最近 {RETENTION_DAYS} 天）'
                        ))
                    except Exception as exc:
                        self.stdout.write(self.style.ERROR(
                            f'[{tick:>6}] ❌  滚动清理异常：{exc}'
                        ))

                time.sleep(STREAM_INTERVAL)

        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING(
                '\n\n  ⏹  守护进程已安全退出（Ctrl+C）\n'
            ))
