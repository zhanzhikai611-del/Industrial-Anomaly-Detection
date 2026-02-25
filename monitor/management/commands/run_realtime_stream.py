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

REAL_STATS = {
    'S1_CurrentFeedback': {
        'unworn': {'mean': 15.93, 'std': 10.03},
        'worn':   {'mean': 15.95, 'std':  9.93},
        'worn_bias': 8.0,          # 磨损时电流均值上移 +8A
    },
    'S1_OutputPower': {
        'unworn': {'mean': 0.1337, 'std': 0.0783},
        'worn':   {'mean': 0.1332, 'std': 0.0780},
        'worn_bias': 0.07,         # 磨损时功率损耗 +0.07W（提升～50%）
    },
    'X1_ActualVelocity': {
        'unworn': {'mean': -0.19, 'std': 4.82},
        'worn':   {'mean': -0.37, 'std': 8.5},  # 震动严重，std 4.82→8.5
        'worn_bias': 0.0,
    },
}

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

def _gen_single_record(device, ts, is_worn: bool) -> ProductionSensorData:
    """
    为单台设备、单个时刻生成一条传感流水记录（不保存）。
    正态分布参数来自真实 CNC 数据集统计（切削阶段 17,520 条有效样本）。
    worn(磨损)：电流均值 +2A、功率均值 +0.02W、进给速度 std 更大。
    """
    rng = REAL_STATS
    if is_worn:
        sc_mean = rng['S1_CurrentFeedback']['worn']['mean'] + rng['S1_CurrentFeedback']['worn_bias']
        sc_std  = rng['S1_CurrentFeedback']['worn']['std']
        sp_mean = rng['S1_OutputPower']['worn']['mean'] + rng['S1_OutputPower']['worn_bias']
        sp_std  = rng['S1_OutputPower']['worn']['std']
        fv_mean = rng['X1_ActualVelocity']['worn']['mean']
        fv_std  = rng['X1_ActualVelocity']['worn']['std']
    else:
        sc_mean = rng['S1_CurrentFeedback']['unworn']['mean']
        sc_std  = rng['S1_CurrentFeedback']['unworn']['std']
        sp_mean = rng['S1_OutputPower']['unworn']['mean']
        sp_std  = rng['S1_OutputPower']['unworn']['std']
        fv_mean = rng['X1_ActualVelocity']['unworn']['mean']
        fv_std  = rng['X1_ActualVelocity']['unworn']['std']

    spindle_current = float(np.random.normal(sc_mean, sc_std))
    spindle_power   = float(max(0.0, np.random.normal(sp_mean, sp_std)))
    feed_velocity   = float(np.random.normal(fv_mean, fv_std))
    machining_proc  = np.random.choice(STAGE_LABELS, p=STAGE_PROBS)

    # OEE 管理字段
    input_qty     = device.standard_capacity
    downtime      = round(random.uniform(5, 20), 1) if is_worn else 0.0
    actual_output = (
        max(0, input_qty - int(downtime * device.standard_capacity / 60))
        if is_worn else input_qty
    )

    return ProductionSensorData(
        device            = device,
        timestamp         = ts,
        spindle_current   = round(spindle_current, 4),
        spindle_power     = round(spindle_power,   4),
        feed_velocity     = round(feed_velocity,   4),
        machining_process = machining_proc,
        tool_condition    = is_worn,
        loading_time      = LOADING_TIME,
        downtime          = downtime,
        input_qty         = input_qty,
        actual_output     = actual_output,
    )


def _check_and_create_alert(record: ProductionSensorData) -> bool:
    """
    检查刚保存的 record 是否触发物理报警阈值。
    若触发则写入 AnomalyAlertLog，返回 True。
    """
    sc  = abs(record.spindle_current)
    sp  = record.spindle_power
    fv  = abs(record.feed_velocity)

    if sc > ALERT_THRESHOLDS['spindle_current']:
        atype = 'HIGH_CURRENT'
    elif sp > ALERT_THRESHOLDS['spindle_power']:
        atype = 'HIGH_POWER'
    elif fv > ALERT_THRESHOLDS['feed_velocity']:
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

                for dev in devices:
                    is_worn = random.random() < ANOMALY_PROB
                    rec_obj = _gen_single_record(dev, now, is_worn)
                    rec_obj.save()          # 逐条 save 获取 pk，便于立即关联 Alert
                    records_created += 1

                    if _check_and_create_alert(rec_obj):
                        alerts_created += 1

                worn_flag = '⚠' if any(
                    random.random() < ANOMALY_PROB for _ in devices
                ) else '✓'
                self.stdout.write(
                    f'[{tick:>6}] ▶  {now:%H:%M:%S}  '
                    f'写入 {records_created} 条  报警 {alerts_created} 条'
                )

                time.sleep(STREAM_INTERVAL)

        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING(
                '\n\n  ⏹  守护进程已安全退出（Ctrl+C）\n'
            ))
