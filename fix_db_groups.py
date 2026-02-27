# -*- coding: utf-8 -*-
"""
fix_db_groups.py
强制修复 device_info 表的 current_group_id 梯度分布。

映射规则（0-based 位置序号 → group_id 1-5）：
  idx  0- 4  →  Group 1  (新设备,    5台,  设备 1-5,  sc≈15A, 低风险   0-40%)
  idx  5-10  →  Group 2  (准新设备,  6台,  设备 6-11, sc≈24A, 低中风险 30-50%)
  idx 11-17  →  Group 3  (正常磨损,  7台,  设备12-18, sc≈27A, 中风险   41-75%)
  idx 18-22  →  Group 4  (老旧设备,  5台,  设备19-23, sc≈31A, 中高风险 60-85%)
  idx 23-24  →  Group 5  (故障边缘,  2台,  设备24-25, sc≈40A, 高风险   76-100%)

注意：
  - 按 id 升序排列确定 idx，与设备 ID 数值无关。
  - 仅覆写 current_group_id，不修改 current_status 及其他字段。
  - 若某台设备已经历"维修回春"（group_id=2），也会被强制恢复为初始组别。
"""

import os
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'IndustrialWarningSystem.settings')

import django
django.setup()

from monitor.models import DeviceInfo

# 新分组：5-6-7-5-2
EXPECTED_COUNTS = {1: 5, 2: 6, 3: 7, 4: 5, 5: 2}

GROUP_PARAMS_LABEL = [
    'Group 1 (新设备   · idx  0-4  · 设备 1-5  · sc≈15A · 低风险   0-40%)',
    'Group 2 (准新设备 · idx  5-10 · 设备 6-11 · sc≈24A · 低中风险 30-50%)',
    'Group 3 (正常磨损 · idx 11-17 · 设备12-18 · sc≈27A · 中风险   41-75%)',
    'Group 4 (老旧设备 · idx 18-22 · 设备19-23 · sc≈31A · 中高风险 60-85%)',
    'Group 5 (故障边缘 · idx 23-24 · 设备24-25 · sc≈40A · 高风险   76-100%)',
]


def _expected_group(idx: int) -> int:
    if idx < 5:    return 1
    elif idx < 11: return 2
    elif idx < 18: return 3
    elif idx < 23: return 4
    else:          return 5


def fix_groups(dry_run: bool = False):
    devices = list(DeviceInfo.objects.order_by('id'))
    if not devices:
        print('❌ 数据库中没有设备，请先运行 simulate_real_cnc_data.py')
        sys.exit(1)

    print(f'\n{"=" * 70}')
    print('  device_info.current_group_id 修复脚本（新分组 5-6-7-5-2）')
    print(f'  设备总数: {len(devices)} 台 | dry_run={dry_run}')
    print(f'{"=" * 70}')
    print(f'\n  {"ID":>4}  {"设备名称":<20} {"旧 gid":>6} → {"新 gid":>6}  {"变化":<6}')
    print(f'  {"-" * 62}')

    to_update = []
    changed = 0

    for idx, dev in enumerate(devices):
        expected_gid = _expected_group(idx)
        old_gid = dev.current_group_id
        flag = '✔' if old_gid == expected_gid else '⚠ 变更'
        if old_gid != expected_gid:
            changed += 1
            dev.current_group_id = expected_gid
            to_update.append(dev)
        print(f'  {dev.id:>4}  {dev.device_name:<20} {old_gid:>6} → {expected_gid:>6}  {flag}')

    print(f'\n  共需修复: {changed} 台')

    if dry_run:
        print('\n  ⚠ dry_run=True，未写入数据库。')
        return

    if to_update:
        DeviceInfo.objects.bulk_update(to_update, ['current_group_id'])
        print('\n  ✅ bulk_update 完成，写入成功。')
    else:
        print('\n  ✅ 所有设备分组已正确，无需修复。')

    # ── 修复后验证 ──────────────────────────────────────────────────
    print(f'\n  {"─" * 35}')
    print('  修复后 group_id 分布验证：')
    from collections import Counter
    final = list(DeviceInfo.objects.order_by('id').values_list('current_group_id', flat=True))
    dist = Counter(final)
    ok = True
    for gid in range(1, 6):
        cnt = dist.get(gid, 0)
        exp = EXPECTED_COUNTS[gid]
        status = '✅' if cnt == exp else '❌'
        label = GROUP_PARAMS_LABEL[gid - 1]
        print(f'  {status} Group {gid}: {cnt:>2} 台（期望 {exp} 台）  {label}')
        if cnt != exp:
            ok = False
    print(f'\n  最终结论: {"✅ 分组梯度正确" if ok else "❌ 分组仍有异常，请检查"}')
    print(f'{"=" * 70}\n')


if __name__ == '__main__':
    dry = '--dry' in sys.argv
    fix_groups(dry_run=dry)
