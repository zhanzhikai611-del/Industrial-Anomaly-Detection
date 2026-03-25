# -*- coding: utf-8 -*-
"""
simulate_real_cnc_data.py  v4 — 精益车间版
————————————————————————————————————
25 台设备，状态固定，无自动状态机

  Running × 20 (id  1-20)：正常传感器波动 + 产量
  Idle    ×  3 (id 21-23)：极低底噪，产量=0
  Down    ×  2 (id 24-25)：全零，每台插入 DOWNTIME 停机报警
"""

import os, random
import numpy as np
from datetime import timedelta

# ── Django 环境初始化 ─────────────────────────────────────────────
import sys
from pathlib import Path
# 获取项目根目录并加入搜索路径，确保在 core_scripts 目录下运行也能找到应用
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'IndustrialWarningSystem.settings')
import django; django.setup()

from django.utils import timezone
from monitor.models import DeviceInfo, ProductionSensorData, AnomalyAlertLog

# ── 传感器统计参数（密歇根大学公开数据集）──────────────────────────
REAL_STATS = {
    'S1_CurrentFeedback': {
        'unworn': {'mean': 15.93, 'std': 10.03, 'p1': -1.86,  'p99': 28.96},
        'worn':   {'mean': 15.95, 'std':  9.93, 'p1': -0.98,  'p99': 29.90},
        'worn_bias': 8.0,
    },
    'S1_OutputPower': {
        'unworn': {'mean': 0.1337, 'std': 0.0783, 'p1': 0.0, 'p99': 0.2150},
        'worn':   {'mean': 0.1332, 'std': 0.0780, 'p1': 0.0, 'p99': 0.2220},
        'worn_bias': 0.07,
    },
    'X1_ActualVelocity': {
        'unworn': {'mean': -0.19, 'std': 4.82, 'p1': -18.0, 'p99': 17.8},
        'worn':   {'mean': -0.37, 'std': 8.5,  'p1': -19.8, 'p99': 19.9},
        'worn_bias': 0.0,
    },
}

ALERT_THRESHOLDS = {
    'spindle_current': 25.0,
    'spindle_power':    0.20,
    'feed_velocity':    17.0,
}

MACHINING_STAGES = [
    ('Layer 1 Up',    4085), ('Layer 2 Up',   3104), ('Layer 3 Up',   2794),
    ('Layer 1 Down',  2655), ('Layer 2 Down', 2528), ('Layer 3 Down', 2354),
    ('Repositioning', 3377),
]
STAGE_LABELS  = [s[0] for s in MACHINING_STAGES]
STAGE_WEIGHTS = [s[1] for s in MACHINING_STAGES]
STAGE_PROBS   = [w / sum(STAGE_WEIGHTS) for w in STAGE_WEIGHTS]

# ── 设备规模 ────────────────────────────────────────────────────────
# 共 25 台，按名称序列生成，状态由序号决定
DEVICE_CONFIGS = [
    ('CNC-VMC-{:02d}',  10, '立式机组', 1000),   # 01-10
    ('CNC-LAT-{:02d}',  10, '数控机组',  800),   # 01-10
    ('CNC-TML-{:02d}',  5, '复合机组', 2000),  # 01-05
]

RUNNING_COUNT = 25   # V1.1 默认全天运行
IDLE_COUNT    =  0
DOWN_COUNT    =  0
TOTAL_DEVICES = RUNNING_COUNT + IDLE_COUNT + DOWN_COUNT  # 25

DAYS          = 7
INTERVAL_MINS = 1
ANOMALY_PROB  = 0.15
BATCH_SIZE    = 3000


# ── Step 1：清空 ────────────────────────────────────────────────────
def clear_data():
    print('\n🗑  清空旧数据…', end=' ', flush=True)
    AnomalyAlertLog.objects.all().delete()
    ProductionSensorData.objects.all().delete()
    DeviceInfo.objects.all().delete()
    print('完成')


# ── Step 2：创建 25 台设备 ──────────────────────────────────────────
def _resolve_status(seq):
    return 'Running'

GROUP_PARAMS = [
    (15.0, 0.13), # Group 1: 1-5 (New) -> ~0-40% Probability
    (24.0, 0.18), # Group 2: 6-10 (Semi-new) -> ~30-50%
    (27.0, 0.25), # Group 3: 11-15 (Normal) -> ~41-75%
    (31.0, 0.32), # Group 4: 16-20 (Old) -> ~60-85%
    (40.0, 0.40), # Group 5: 21-25 (Faulty) -> ~76-100%
]

def _get_group_id(seq: int) -> int:
    """
    将设备创建序号 (1-25) 映射到特征组 ID (1-5)。
    分布：Group1=5台(1-5)、Group2=6台(6-11)、Group3=7台(12-18)、
          Group4=5台(19-23)、Group5=2台(24-25)
    """
    idx = seq - 1  # 转为 0-based
    if idx < 5:    return 1
    elif idx < 11: return 2
    elif idx < 18: return 3
    elif idx < 23: return 4
    else:          return 5

def create_devices():
    print(f'🏭  初始化 {TOTAL_DEVICES} 台设备 '
          f'(Running={RUNNING_COUNT} / Idle={IDLE_COUNT} / Down={DOWN_COUNT})…',
          end=' ', flush=True)
    objs, seq = [], 0
    for name_tpl, count, dtype, cap in DEVICE_CONFIGS:
        for i in range(1, count + 1):
            seq += 1
            objs.append(DeviceInfo(
                device_name=name_tpl.format(i),
                device_type=dtype,
                standard_capacity=cap,
                current_status=_resolve_status(seq),
                current_group_id=_get_group_id(seq),
            ))
    DeviceInfo.objects.bulk_create(objs)
    devices = list(DeviceInfo.objects.order_by('id'))
    print(f'完成，共 {len(devices)} 台')
    return devices


# ── 记录生成函数 ────────────────────────────────────────────────────
def _gen_running_record(device, ts, group_idx):
    sc_mean, sp_mean = GROUP_PARAMS[group_idx]
    
    # 按照设定的分组生成正态分布的特征，使由于特征基准的偏移，
    # 逻辑回归推断出的概率准确落在各自区间。
    sc = float(np.random.normal(sc_mean, 1.5))
    sp = float(max(0, np.random.normal(sp_mean, 0.015)))
    fv = float(np.random.normal(-0.19, 2.0))

    is_worn = (group_idx >= 3)
    cap = device.standard_capacity

    # ── 可用性 A：停机时间（仅高危设备产生计划外停机）──────────────
    if group_idx == 0:
        dt = 0.0                                          # 新设备，无停机
    elif group_idx == 1:
        dt = round(random.uniform(0, 2), 1)               # 准新，偶发微停
    elif group_idx == 2:
        dt = round(random.uniform(1, 5), 1)               # 正常磨损
    elif group_idx == 3:
        dt = round(random.uniform(3, 12), 1)              # 老旧，频繁微停
    else:
        dt = round(random.uniform(8, 20), 1)              # 故障边缘

    # ── 性能 P：主轴倍率下降导致实际产出低于标准 ─────────────────
    if group_idx == 0:
        perf_ratio = random.uniform(0.96, 1.00)           # 新设备接近满载
    elif group_idx == 1:
        perf_ratio = random.uniform(0.93, 0.98)           # 准新，轻微下降
    elif group_idx == 2:
        perf_ratio = random.uniform(0.88, 0.95)           # 正常磨损期
    elif group_idx == 3:
        perf_ratio = random.uniform(0.78, 0.90)           # 老旧，主轴倍率明显降低
    else:
        perf_ratio = random.uniform(0.65, 0.82)           # 故障边缘，严重降速

    # ── V5 引擎：脉冲余数累加器 (Yield Buffer / Pulse Accumulator) ────────
    # 本 step 代表的秒数：实时=3s，静态历史=INTERVAL_MINS*60s
    step_secs = getattr(device, 'step_secs', INTERVAL_MINS * 60)

    # 每步理论投入 = cap / 3600 * step_secs（精确的时间份额）
    input_fraction = cap / 3600.0 * step_secs
    input_qty_val  = input_fraction        # 存储为 float 比例（DB 字段是 Int，见下面 round）

    # 停机时该步产出为 0；否则累积产量
    if dt > 0:
        good_output = 0
        input_qty_val = 0
    else:
        # 累加本步产出脉冲到 yield_buffer
        pulse = input_fraction * perf_ratio
        device.yield_buffer = getattr(device, 'yield_buffer', 0.0) + pulse

        if device.yield_buffer >= 1.0:
            produced = int(device.yield_buffer)
            device.yield_buffer -= produced
        else:
            produced = 0

        # ── 良率 Q -次品缓冲器 ──────────────────────────────
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
            device.defect_buffer = getattr(device, 'defect_buffer', 0.0) + produced * defect_rate
            if device.defect_buffer >= 1.0:
                defects = int(device.defect_buffer)
                device.defect_buffer -= defects

        good_output    = max(0, produced - defects)
        input_qty_val  = produced          # 投入 = 本步实际出件数（整数）

    return ProductionSensorData(
        device=device, timestamp=ts,
        spindle_current=round(sc, 4), spindle_power=round(sp, 4), feed_velocity=round(fv, 4),
        machining_process=np.random.choice(STAGE_LABELS, p=STAGE_PROBS),
        tool_condition=is_worn, loading_time=float(step_secs / 60.0),
        downtime=dt, input_qty=int(input_qty_val), actual_output=good_output,
    )


def _gen_idle_record(device, ts):
    sc = float(max(0, np.random.normal(0.3, 0.05)))
    sp = float(max(0, np.random.normal(0.002, 0.001)))
    fv = float(np.random.normal(0.0, 0.02))
    # input_qty 必须是本步长的时间份额，而非整点产能（防止 OEE-P 崩溃）
    step_secs     = getattr(device, 'step_secs', INTERVAL_MINS * 60)
    input_qty_val = 0  # Idle 时不投料，产出=0
    return ProductionSensorData(
        device=device, timestamp=ts,
        spindle_current=round(sc, 4), spindle_power=round(sp, 4), feed_velocity=round(fv, 4),
        machining_process='Idle', tool_condition=False,
        loading_time=float(step_secs / 60.0), downtime=0.0,
        input_qty=input_qty_val, actual_output=0,
    )


def _gen_down_record(device, ts):
    step_secs     = getattr(device, 'step_secs', INTERVAL_MINS * 60)
    input_qty_val = 0  # Down 时不投料，产出=0
    return ProductionSensorData(
        device=device, timestamp=ts,
        spindle_current=0.0, spindle_power=0.0, feed_velocity=0.0,
        machining_process='Down', tool_condition=False,
        loading_time=0.0, downtime=float(step_secs / 60.0),
        input_qty=input_qty_val, actual_output=0,
    )


# ── Step 3：生成流水 + 报警日志 ─────────────────────────────────────
def simulate_sensor_data(devices):
    now        = timezone.now()
    start_time = now - timedelta(days=DAYS)
    total_mins = DAYS * 24 * 60 // INTERVAL_MINS
    expected   = len(devices) * total_mins
    print(f'📊  生成 {DAYS} 天 × {len(devices)} 台 × {total_mins:,} 条/台 = {expected:,} 条流水…')

    all_records, alert_infos = [], []

    for idx, dev in enumerate(devices):
        status = dev.current_status
        # IDX is 0-indexed (0 to 24 corresponding to devices 1 to 25)
        group_idx = min(max(dev.current_group_id - 1, 0), 4)

        for m in range(total_mins):
            ts = start_time + timedelta(minutes=m * INTERVAL_MINS)

            # V5：明确传入步长（秒），让 yield_buffer 使用正确的时间份额
            dev.step_secs = INTERVAL_MINS * 60   # 1分钟→60秒

            if status == 'Running':
                rec = _gen_running_record(dev, ts, group_idx)
                all_records.append(rec)

                from monitor.models import SystemConfig
                cfg = SystemConfig.objects.first()
                sc_thresh = cfg.spindle_current_high if cfg else ALERT_THRESHOLDS['spindle_current']
                sp_thresh = cfg.spindle_power_high if cfg else ALERT_THRESHOLDS['spindle_power']
                fv_thresh = cfg.feed_velocity_low if cfg else 0.5

                if (abs(rec.spindle_current) > sc_thresh or
                        rec.spindle_power    > sp_thresh or
                        abs(rec.feed_velocity) < fv_thresh):
                    atype = ('HIGH_CURRENT' if abs(rec.spindle_current) > sc_thresh
                             else 'HIGH_POWER' if rec.spindle_power > sp_thresh
                             else 'LOW_VELOCITY')
                    alert_infos.append({
                        'list_idx':    len(all_records) - 1,
                        'alert_time':  ts,
                        'alert_type':  atype,
                        'anomaly_score': round(random.uniform(0.60, 0.99), 4),
                        'is_handled':  random.choice([True, False]),
                    })

            elif status == 'Idle':
                all_records.append(_gen_idle_record(dev, ts))

            else:  # Down
                all_records.append(_gen_down_record(dev, ts))

        # 每台停机设备插入 1 条非计划停机整体报警
        if status == 'Down':
            latest_ts = start_time + timedelta(minutes=(total_mins - 1) * INTERVAL_MINS)
            alert_infos.append({
                'list_idx':    len(all_records) - 1,
                'alert_time':  latest_ts,
                'alert_type':  'DOWNTIME',
                'anomaly_score': round(random.uniform(0.80, 0.99), 4),
                'is_handled':  False,
            })

    # bulk_create 流水
    print(f'  ⚡ bulk_create {len(all_records):,} 条流水（批次={BATCH_SIZE}）…',
          end=' ', flush=True)
    for i in range(0, len(all_records), BATCH_SIZE):
        ProductionSensorData.objects.bulk_create(all_records[i: i + BATCH_SIZE])
    print('完成')

    # 查回 pk，写报警日志
    alert_count = 0
    if alert_infos:
        alert_idxs = {info['list_idx'] for info in alert_infos}
        anchor     = [all_records[i] for i in alert_idxs]
        qs = ProductionSensorData.objects.filter(
            device_id__in=[o.device_id for o in anchor],
            timestamp__in=[o.timestamp  for o in anchor],
        ).values('id', 'device_id', 'timestamp')
        pk_map = {(row['device_id'], row['timestamp']): row['id'] for row in qs}

        logs = []
        for info in alert_infos:
            obj = all_records[info['list_idx']]
            pk  = pk_map.get((obj.device_id, obj.timestamp))
            if pk:
                logs.append(AnomalyAlertLog(
                    record_id=pk, alert_time=info['alert_time'],
                    alert_type=info['alert_type'], anomaly_score=info['anomaly_score'],
                    is_handled=info['is_handled'],
                ))
        if logs:
            print(f'  🚨 bulk_create {len(logs):,} 条报警日志…', end=' ', flush=True)
            AnomalyAlertLog.objects.bulk_create(logs, batch_size=1000)
            print('完成')
            alert_count = len(logs)

    return len(all_records), alert_count


# ── 汇总 ────────────────────────────────────────────────────────────
def print_summary(device_count, sensor_count, alert_count):
    sep = '═' * 64
    running = DeviceInfo.objects.filter(current_status='Running').count()
    idle    = DeviceInfo.objects.filter(current_status='Idle').count()
    down    = DeviceInfo.objects.filter(current_status='Down').count()
    print(f'\n{sep}')
    print('  ✅  仿真完成！数据库写入汇总')
    print(sep)
    print(f'  📟  设备总数      : {device_count:>10,} 台')
    print(f'      ├ Running     : {running:>10,} 台')
    print(f'      ├ Idle        : {idle:>10,} 台')
    print(f'      └ Down        : {down:>10,} 台')
    print(f'  📈  流水记录总数  : {sensor_count:>10,} 条')
    print(f'  🚨  报警记录总数  : {alert_count:>10,} 条')
    print(f'  📡  覆盖时段      : 过去 {DAYS} 天，每 {INTERVAL_MINS} 分钟/条')
    print(sep)
    print('\n  📋  数据库核查：')
    print(f'      DeviceInfo           : {DeviceInfo.objects.count():>8,}')
    print(f'      ProductionSensorData : {ProductionSensorData.objects.count():>8,}')
    worn = ProductionSensorData.objects.filter(tool_condition=True).count()
    if sensor_count:
        print(f'        └─ 磨损(worn=True) : {worn:>8,}  ({worn/sensor_count:.1%})')
    print(f'      AnomalyAlertLog      : {AnomalyAlertLog.objects.count():>8,}')
    unhandled = AnomalyAlertLog.objects.filter(is_handled=False).count()
    print(f'        └─ 未处理          : {unhandled:>8,}')
    print(f'        └─ DOWNTIME 停机   : '
          f'{AnomalyAlertLog.objects.filter(alert_type="DOWNTIME").count():>8,}')
    print(f'{sep}\n')


# ── 实时仿真引擎（多线程+自动重连） ──────────────────────────────────
import time
import threading
from django.db import connection

def _generate_single_device_realtime(dev, ts, group_idx):
    status = dev.current_status
    alert_info = None
    if status == 'Running':
        rec = _gen_running_record(dev, ts, group_idx)
        from monitor.models import SystemConfig
        cfg = SystemConfig.objects.first()
        sc_thresh = cfg.spindle_current_high if cfg else ALERT_THRESHOLDS['spindle_current']
        sp_thresh = cfg.spindle_power_high if cfg else ALERT_THRESHOLDS['spindle_power']
        fv_thresh = cfg.feed_velocity_low if cfg else 0.5

        if (abs(rec.spindle_current) > sc_thresh or
                rec.spindle_power    > sp_thresh or
                abs(rec.feed_velocity) < fv_thresh):
            atype = ('HIGH_CURRENT' if abs(rec.spindle_current) > sc_thresh
                     else 'HIGH_POWER' if rec.spindle_power > sp_thresh
                     else 'LOW_VELOCITY')
            alert_info = {
                'alert_time':  ts,
                'alert_type':  atype,
                'anomaly_score': round(random.uniform(0.60, 0.99), 4),
                'is_handled':  False,
            }
    elif status == 'Idle':
        rec = _gen_idle_record(dev, ts)
    else:
        rec = _gen_down_record(dev, ts)
    return rec, alert_info

def run_realtime_simulation():
    from monitor.models import SystemConfig
    from concurrent.futures import ThreadPoolExecutor
    
    print("\n🚀 启动实时仿真引擎 (每 3 秒生成一次数据)...")
    
    config = SystemConfig.objects.first()
    if config and not config.is_realtime_active:
        print("⚠️ 发现 is_realtime_active 为 False，强制修复为 True...")
        config.is_realtime_active = True
        config.save()

    devices = list(DeviceInfo.objects.all())
    # 💡 关键：重置所有设备的 yield_buffer，防止 7 天历史数据累积的余量污染实时轮询
    # 同时重置维修建议 (V2.2.0 同步 PRD 逻辑)
    for dev in devices:
        dev.yield_buffer  = 0.0
        dev.defect_buffer = 0.0
        dev.maintenance_advice = '设备运行平稳，暂无维修建议。'
        dev.save(update_fields=['yield_buffer', 'defect_buffer', 'maintenance_advice'])
    
    with ThreadPoolExecutor(max_workers=25) as executor:
        while True:
            try:
                # 确保数据库连接存活
                connection.ensure_connection()
                
                config = SystemConfig.objects.first()
                if not config.is_realtime_active:
                    print("⏸️ 监控已暂停，休眠 3 秒...")
                    time.sleep(3)
                    continue

                ts = timezone.now()
                # 根据 device index 生成组别
                futures = []
                for idx, dev in enumerate(devices):
                    # 重新从 DB 读取最新状态与分组，支持前台动态更改及回春逻辑
                    dev.refresh_from_db(fields=['current_status', 'current_group_id'])
                    
                    # V5: 3 秒步长，用 step_secs 统一表示
                    dev.step_secs = 3.0
                    dev.current_interval_mins = 3.0 / 60.0  # 保留兼容

                    group_idx = min(max(dev.current_group_id - 1, 0), 4)
                    futures.append(executor.submit(_generate_single_device_realtime, dev, ts, group_idx))
                
                records = []
                alert_infos = []
                for i, future in enumerate(futures):
                    rec, alert = future.result()
                    records.append(rec)
                    if alert:
                        alert['list_idx'] = len(records) - 1
                        alert_infos.append(alert)

                if records:
                    ProductionSensorData.objects.bulk_create(records)
                    
                    if alert_infos:
                        # 获取刚写入的 records id (部分数据库 bulk_create 不能返回 id，用 timestamp 查)
                        qs = ProductionSensorData.objects.filter(
                            timestamp=ts
                        ).values('id', 'device_id')
                        pk_map = {row['device_id']: row['id'] for row in qs}
                        
                        logs = []
                        for info in alert_infos:
                            obj = records[info['list_idx']]
                            pk = pk_map.get(obj.device_id)
                            if pk:
                                logs.append(AnomalyAlertLog(
                                    record_id=pk, alert_time=info['alert_time'],
                                    alert_type=info['alert_type'], anomaly_score=info['anomaly_score'],
                                    is_handled=info['is_handled'],
                                ))
                        if logs:
                            AnomalyAlertLog.objects.bulk_create(logs)
                
                print(f"[{ts.strftime('%H:%M:%S')}] ✅ 成功生成 25 台设备流水 (报警: {len(alert_infos)} 条)")
                time.sleep(3)
                
            except Exception as e:
                print(f"❌ 仿真引擎异常: {e}")
                print("🔄 尝试重建数据库连接并于 5 秒后重试...")
                connection.close()  # 强制关闭无效连接
                time.sleep(5)


if __name__ == '__main__':
    print('\n' + '═' * 64)
    print(f'  🏭  CNC 精益车间仿真器 v4  ·  {DAYS}天 · {TOTAL_DEVICES}台设备 · {INTERVAL_MINS}分钟/条')
    print(f'       Running={RUNNING_COUNT}  |  Idle={IDLE_COUNT}  |  Down={DOWN_COUNT}')
    print('═' * 64)
    
    # 初始化：判断如果数据太少才重置历史，否则直接接续实时仿真
    if DeviceInfo.objects.count() < 25:
        clear_data()
        devices = create_devices()
        sensor_count, alert_count = simulate_sensor_data(devices)
        print_summary(len(devices), sensor_count, alert_count)
    
    # 进入实时产出循环
    run_realtime_simulation()
