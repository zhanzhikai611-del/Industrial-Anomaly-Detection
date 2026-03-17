# -*- coding: utf-8 -*-
"""
analyze_cnc_dataset.py
CNC 铣床刀具磨损数据集统计分析脚本

功能：
  1. 读取 CNCData/ 目录下的 18 个实验 CSV 文件
  2. 关联 train.csv 标签（unworn / worn）
  3. 对关键字段按刀具状态分组，计算 mean / std / min / max / P1 / P99
  4. 输出加工阶段分布、特征可分性报告

用法：
  python analyze_cnc_dataset.py
"""

import os
import warnings
import pandas as pd
import numpy as np

warnings.filterwarnings('ignore')

DATA_DIR = '../CNCData'   # 文件已移至 lab/，需向上跳一级指向根目录下的 CNCData

# ── 目标字段（对应 Django 模型字段）─────────────────────────────────
FIELDS = {
    'S1_CurrentFeedback': 'spindle_current（主轴反馈电流 A）',
    'S1_OutputPower':     'spindle_power（主轴输出功率 W）',
    'X1_ActualVelocity':  'feed_velocity（X轴实际进给速度 mm/s）',
    'M1_CURRENT_FEEDRATE':'loading_feedrate（当前进给率）',
}


def load_train_labels():
    """读取 train.csv，返回：worn 实验编号集合、unworn 实验编号集合"""
    train = pd.read_csv(f'{DATA_DIR}/train.csv')
    worn   = set(train[train['tool_condition'] == 'worn'  ]['No'].tolist())
    unworn = set(train[train['tool_condition'] == 'unworn']['No'].tolist())
    return worn, unworn


def load_all_experiments(worn_set, unworn_set):
    """读取全部 18 个 CSV，按 tool_condition 分类并合并"""
    worn_frames   = []
    unworn_frames = []
    all_frames    = []
    loaded = []

    for i in range(1, 19):
        path = f'{DATA_DIR}/experiment_{i:02d}.csv'
        if not os.path.exists(path):
            print(f'  ⚠ 跳过（文件不存在）: {path}')
            continue
        df = pd.read_csv(path)
        df['experiment_no']   = i
        df['tool_condition']  = 'worn' if i in worn_set else ('unworn' if i in unworn_set else 'unknown')
        all_frames.append(df)
        if i in worn_set:
            worn_frames.append(df)
        elif i in unworn_set:
            unworn_frames.append(df)
        loaded.append(i)

    print(f'  已加载实验编号: {loaded}')
    return (pd.concat(all_frames,   ignore_index=True),
            pd.concat(worn_frames,  ignore_index=True),
            pd.concat(unworn_frames,ignore_index=True))


def filter_cutting(df):
    """只保留切削阶段（Machining_Process 含 'Layer'），剔除 Prep/Starting/end 空转噪声"""
    return df[df['Machining_Process'].str.contains('Layer', na=False)]


def compute_stats(df, fields):
    """计算各字段的描述性统计"""
    result = {}
    for f in fields:
        if f not in df.columns:
            continue
        s = df[f].dropna()
        result[f] = {
            'count': len(s),
            'mean':  s.mean(),
            'std':   s.std(),
            'min':   s.min(),
            'p1':    s.quantile(0.01),
            'p25':   s.quantile(0.25),
            'median':s.median(),
            'p75':   s.quantile(0.75),
            'p99':   s.quantile(0.99),
            'max':   s.max(),
        }
    return result


def print_stats_table(unworn_stats, worn_stats, fields):
    """对比打印 unworn / worn 统计表"""
    sep = '=' * 80
    print(f'\n{sep}')
    print('  特征字段统计对比（切削阶段，仅保留 Machining_Process ∋ Layer 的行）')
    print(sep)

    for f, label in fields.items():
        if f not in unworn_stats or f not in worn_stats:
            continue
        u = unworn_stats[f]
        w = worn_stats[f]
        print(f'\n  ── {f}  ({label})')
        print(f"  {'指标':<10} {'unworn(正常)':>14} {'worn(磨损)':>14} {'差值(worn-unworn)':>18}")
        print(f"  {'-'*58}")
        for key in ['count','mean','std','min','p1','p25','median','p75','p99','max']:
            uv = u.get(key, float('nan'))
            wv = w.get(key, float('nan'))
            diff = wv - uv
            print(f"  {key:<10} {uv:>14.4f} {wv:>14.4f} {diff:>+18.4f}")
    print(f'\n{sep}')


def print_machining_distribution(all_df):
    """打印加工阶段分布（用于确定仿真时的权重）"""
    sep = '=' * 50
    print(f'\n{sep}')
    print('  Machining_Process 分布（全部 18 个实验）')
    print(sep)
    dist = all_df['Machining_Process'].value_counts()
    total = dist.sum()
    print(f"  {'阶段':<20} {'次数':>8}  {'占比':>8}")
    print(f"  {'-'*40}")
    for stage, cnt in dist.items():
        print(f"  {stage:<20} {cnt:>8,}  {cnt/total:>7.1%}")
    print(f'  {"-"*40}')
    print(f"  {'合计':<20} {total:>8,}  {'100.0%':>8}")
    print(sep)


def print_simulation_params(unworn_stats, worn_stats):
    """输出可直接用于仿真脚本的正态分布参数建议"""
    sep = '=' * 70
    print(f'\n{sep}')
    print('  仿真脚本推荐参数（可直接复制到 simulate_real_cnc_data.py）')
    print(sep)

    fields_abbr = {
        'S1_CurrentFeedback': ('spindle_current', 'A'),
        'S1_OutputPower':     ('spindle_power',   'W'),
        'X1_ActualVelocity':  ('feed_velocity',   'mm/s'),
    }
    for f, (db_field, unit) in fields_abbr.items():
        if f not in unworn_stats or f not in worn_stats:
            continue
        u = unworn_stats[f]
        w = worn_stats[f]
        print(f"\n  # {db_field} ({unit})")
        print(f"  # unworn: np.random.normal(mean={u['mean']:.4f}, std={u['std']:.4f})")
        print(f"  # worn:   np.random.normal(mean={w['mean']:.4f}, std={w['std']:.4f})")
        print(f"  # 异常报警阈值建议（P99_worn ≈ {w['p99']:.4f}，P99_unworn ≈ {u['p99']:.4f}）")
    print(f'\n{sep}\n')


# ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print('\n' + '=' * 50)
    print('  CNC 刀具磨损数据集 - 统计特征分析')
    print('=' * 50)

    print('\n[1] 加载实验标签 (train.csv)…')
    worn_set, unworn_set = load_train_labels()
    print(f'    worn 实验: {sorted(worn_set)}')
    print(f'    unworn 实验: {sorted(unworn_set)}')

    print('\n[2] 读取 18 个实验 CSV…')
    all_df, worn_df, unworn_df = load_all_experiments(worn_set, unworn_set)
    print(f'    总行数: {len(all_df):,}  |  worn: {len(worn_df):,}  |  unworn: {len(unworn_df):,}')

    print('\n[3] 筛选切削阶段（Layer*）…')
    worn_cut   = filter_cutting(worn_df)
    unworn_cut = filter_cutting(unworn_df)
    print(f'    切削阶段行数: worn={len(worn_cut):,}  unworn={len(unworn_cut):,}')

    print('\n[4] 计算统计特征…')
    field_list = list(FIELDS.keys())
    worn_stats   = compute_stats(worn_cut,   field_list)
    unworn_stats = compute_stats(unworn_cut, field_list)

    print_stats_table(unworn_stats, worn_stats, FIELDS)
    print_machining_distribution(all_df)
    print_simulation_params(unworn_stats, worn_stats)
