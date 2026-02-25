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
    ('CNC-高光-{:02d}',  10, '高光机组', 1000),   # 01-10
    ('CNC-精雕-{:02d}',  10, '精雕机组',  800),   # 01-10
    ('Punch-冲压-{:02d}',  5, '冲压机组', 2000),  # 01-05
]

RUNNING_COUNT = 22   # 序号 1-22 → Running
IDLE_COUNT    =  2   # 序号 23-24 → Idle
DOWN_COUNT    =  1   # 序号 25 → Down
TOTAL_DEVICES = RUNNING_COUNT + IDLE_COUNT + DOWN_COUNT  # 25

DAYS          = 7
INTERVAL_MINS = 1
ANOMALY_PROB  = 0.15
LOADING_TIME  = 60.0
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
    if seq <= RUNNING_COUNT:
        return 'Running'
    elif seq <= RUNNING_COUNT + IDLE_COUNT:
        return 'Idle'
    return 'Down'


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
            ))
    DeviceInfo.objects.bulk_create(objs)
    devices = list(DeviceInfo.objects.order_by('id'))
    print(f'完成，共 {len(devices)} 台')
    return devices


# ── 记录生成函数 ────────────────────────────────────────────────────
def _gen_running_record(device, ts, is_worn):
    r = REAL_STATS
    if is_worn:
        sc = float(np.random.normal(r['S1_CurrentFeedback']['worn']['mean'] + r['S1_CurrentFeedback']['worn_bias'],
                                    r['S1_CurrentFeedback']['worn']['std']))
        sp = float(max(0, np.random.normal(r['S1_OutputPower']['worn']['mean'] + r['S1_OutputPower']['worn_bias'],
                                           r['S1_OutputPower']['worn']['std'])))
        fv = float(np.random.normal(r['X1_ActualVelocity']['worn']['mean'],
                                    r['X1_ActualVelocity']['worn']['std']))
    else:
        sc = float(np.random.normal(r['S1_CurrentFeedback']['unworn']['mean'],
                                    r['S1_CurrentFeedback']['unworn']['std']))
        sp = float(max(0, np.random.normal(r['S1_OutputPower']['unworn']['mean'],
                                           r['S1_OutputPower']['unworn']['std'])))
        fv = float(np.random.normal(r['X1_ActualVelocity']['unworn']['mean'],
                                    r['X1_ActualVelocity']['unworn']['std']))

    cap = device.standard_capacity
    dt  = round(random.uniform(5, 20), 1) if is_worn else 0.0
    out = max(0, cap - int(dt * cap / 60)) if is_worn else cap

    return ProductionSensorData(
        device=device, timestamp=ts,
        spindle_current=round(sc, 4), spindle_power=round(sp, 4), feed_velocity=round(fv, 4),
        machining_process=np.random.choice(STAGE_LABELS, p=STAGE_PROBS),
        tool_condition=is_worn, loading_time=LOADING_TIME,
        downtime=dt, input_qty=cap, actual_output=out,
    )


def _gen_idle_record(device, ts):
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


def _gen_down_record(device, ts):
    return ProductionSensorData(
        device=device, timestamp=ts,
        spindle_current=0.0, spindle_power=0.0, feed_velocity=0.0,
        machining_process='Down', tool_condition=False,
        loading_time=0.0, downtime=float(INTERVAL_MINS),
        input_qty=device.standard_capacity, actual_output=0,
    )


# ── Step 3：生成流水 + 报警日志 ─────────────────────────────────────
def simulate_sensor_data(devices):
    now        = timezone.now()
    start_time = now - timedelta(days=DAYS)
    total_mins = DAYS * 24 * 60 // INTERVAL_MINS
    expected   = len(devices) * total_mins
    print(f'📊  生成 {DAYS} 天 × {len(devices)} 台 × {total_mins:,} 条/台 = {expected:,} 条流水…')

    all_records, alert_infos = [], []

    for dev in devices:
        status = dev.current_status

        for m in range(total_mins):
            ts = start_time + timedelta(minutes=m * INTERVAL_MINS)

            if status == 'Running':
                is_worn = random.random() < ANOMALY_PROB
                rec = _gen_running_record(dev, ts, is_worn)
                all_records.append(rec)

                # 物理阈值报警
                if (abs(rec.spindle_current) > ALERT_THRESHOLDS['spindle_current'] or
                        rec.spindle_power    > ALERT_THRESHOLDS['spindle_power']      or
                        abs(rec.feed_velocity) > ALERT_THRESHOLDS['feed_velocity']):
                    atype = ('HIGH_CURRENT' if abs(rec.spindle_current) > ALERT_THRESHOLDS['spindle_current']
                             else 'HIGH_POWER' if rec.spindle_power > ALERT_THRESHOLDS['spindle_power']
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

def _generate_single_device_realtime(dev, ts):
    status = dev.current_status
    alert_info = None
    if status == 'Running':
        is_worn = random.random() < ANOMALY_PROB
        rec = _gen_running_record(dev, ts, is_worn)
        if (abs(rec.spindle_current) > ALERT_THRESHOLDS['spindle_current'] or
                rec.spindle_power    > ALERT_THRESHOLDS['spindle_power']      or
                abs(rec.feed_velocity) > ALERT_THRESHOLDS['feed_velocity']):
            atype = ('HIGH_CURRENT' if abs(rec.spindle_current) > ALERT_THRESHOLDS['spindle_current']
                     else 'HIGH_POWER' if rec.spindle_power > ALERT_THRESHOLDS['spindle_power']
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
                futures = [executor.submit(_generate_single_device_realtime, dev, ts) for dev in devices]
                
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
