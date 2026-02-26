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
  - Ctrl+C 安全退出

注意：只追加（Append），不删除任何历史数据。
"""

import time
import random
import logging
import numpy as np

from django.core.management.base import BaseCommand
from django.utils import timezone

from monitor.models import (
    DeviceInfo, ProductionSensorData, AnomalyAlertLog, SystemConfig
)

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
LOADING_TIME  = 60.0    # 负荷时间（分钟/记录）
STREAM_INTERVAL = 3     # 守护进程轮询间隔（秒）


# ═══════════════════════════════════════════════════════════════════
#  核心生成函数（复用 simulate_real_cnc_data.py 同款逻辑）
# ═══════════════════════════════════════════════════════════════════

def _gen_running_record(device, ts, group_idx) -> ProductionSensorData:
    sc_mean, sp_mean = GROUP_PARAMS[group_idx]
    
    sc = float(np.random.normal(sc_mean, 1.5))
    sp = float(max(0.0, np.random.normal(sp_mean, 0.015)))
    fv = float(np.random.normal(-0.19, 2.0))
    machining_proc = np.random.choice(STAGE_LABELS, p=STAGE_PROBS)

    is_worn = (group_idx >= 3)
    cap = device.standard_capacity

    # ── 可用性 A：分组停机时间 ──────────────────────────────────────
    if group_idx == 0:
        downtime = 0.0
    elif group_idx == 1:
        downtime = round(random.uniform(0, 2), 1)
    elif group_idx == 2:
        downtime = round(random.uniform(1, 5), 1)
    elif group_idx == 3:
        downtime = round(random.uniform(3, 12), 1)
    else:
        downtime = round(random.uniform(8, 20), 1)

    # ── 性能 P：主轴倍率下降 → 实际产出 < 理论产出 ─────────────────
    if group_idx == 0:
        perf_ratio = random.uniform(0.96, 1.00)
    elif group_idx == 1:
        perf_ratio = random.uniform(0.93, 0.98)
    elif group_idx == 2:
        perf_ratio = random.uniform(0.88, 0.95)
    elif group_idx == 3:
        perf_ratio = random.uniform(0.78, 0.90)
    else:
        perf_ratio = random.uniform(0.65, 0.82)

    actual_run = max(0.0, LOADING_TIME - downtime)
    theoretical_output = (actual_run / 60.0) * cap
    out = max(0, int(theoretical_output * perf_ratio))

    # ── 良率 Q：高风险设备产出次品 ─────────────────────────────────
    input_qty_val = max(1, int(theoretical_output))
    if group_idx <= 1:
        defect_rate = random.uniform(0.0, 0.01)
    elif group_idx == 2:
        defect_rate = random.uniform(0.01, 0.04)
    elif group_idx == 3:
        defect_rate = random.uniform(0.03, 0.08)
    else:
        defect_rate = random.uniform(0.06, 0.15)

    defects = int(out * defect_rate)
    good_output = max(0, out - defects)

    return ProductionSensorData(
        device            = device,
        timestamp         = ts,
        spindle_current   = round(sc, 4),
        spindle_power     = round(sp,   4),
        feed_velocity     = round(fv,   4),
        machining_process = machining_proc,
        tool_condition    = is_worn,
        loading_time      = LOADING_TIME,
        downtime          = downtime,
        input_qty         = input_qty_val,
        actual_output     = good_output,
    )

def _gen_idle_record(device, ts) -> ProductionSensorData:
    sc = float(max(0, np.random.normal(0.3, 0.05)))
    sp = float(max(0, np.random.normal(0.002, 0.001)))
    fv = float(np.random.normal(0.0, 0.02))
    return ProductionSensorData(
        device=device, timestamp=ts,
        spindle_current=round(sc, 4), spindle_power=round(sp, 4), feed_velocity=round(fv, 4),
        machining_process='Idle', tool_condition=False,
        loading_time=0.0, downtime=0.0,
        input_qty=device.standard_capacity, actual_output=0,
    )

def _gen_down_record(device, ts) -> ProductionSensorData:
    return ProductionSensorData(
        device=device, timestamp=ts,
        spindle_current=0.0, spindle_power=0.0, feed_velocity=0.0,
        machining_process='Down', tool_condition=False,
        loading_time=0.0, downtime=float(STREAM_INTERVAL),
        input_qty=device.standard_capacity, actual_output=0,
    )


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

    AnomalyAlertLog.objects.create(
        record        = record,
        alert_time    = record.timestamp,
        alert_type    = atype,
        anomaly_score = round(random.uniform(0.60, 0.99), 4),
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
            '  🚀  实时数据流守护进程已启动\n'
            f'  轮询间隔: {STREAM_INTERVAL}s | 异常概率: {ANOMALY_PROB:.0%}\n'
            '  按 Ctrl+C 安全退出\n'
            '══════════════════════════════════════════\n'
        ))

        tick = 0
        try:
            while True:
                tick += 1
                cfg = SystemConfig.get()

                if not cfg.is_realtime_active:
                    self.stdout.write(f'[{tick:>6}] ⏸  Streaming paused...')
                    time.sleep(STREAM_INTERVAL)
                    continue

                # ── 激活状态：为每台设备生成并追加一条数据 ──────────
                now     = timezone.now()
                devices = list(DeviceInfo.objects.all())

                if not devices:
                    self.stdout.write(self.style.WARNING(
                        f'[{tick:>6}] ⚠  数据库中没有设备，请先运行 simulate_real_cnc_data.py'
                    ))
                    time.sleep(STREAM_INTERVAL)
                    continue

                records_created = 0
                alerts_created  = 0

                for idx, dev in enumerate(devices):
                    dev.refresh_from_db(fields=['current_status', 'current_group_id'])
                    status = dev.current_status
                    group_idx = min(max(dev.current_group_id - 1, 0), 4)

                    if status == 'Running':
                        rec_obj = _gen_running_record(dev, now, group_idx)
                        rec_obj.save()          # 逐条 save 获取 pk，便于立即关联 Alert
                        records_created += 1

                        if _check_and_create_alert(rec_obj):
                            alerts_created += 1
                    elif status == 'Idle':
                        rec_obj = _gen_idle_record(dev, now)
                        rec_obj.save()
                        records_created += 1
                    else: # Down
                        rec_obj = _gen_down_record(dev, now)
                        rec_obj.save()
                        records_created += 1

                worn_flag = '⚠' if alerts_created > 0 else '✓'
                self.stdout.write(
                    f'[{tick:>6}] ▶  {now:%H:%M:%S}  '
                    f'写入 {records_created} 条  报警 {alerts_created} 条'
                )

                time.sleep(STREAM_INTERVAL)

        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING(
                '\n\n  ⏹  守护进程已安全退出（Ctrl+C）\n'
            ))
