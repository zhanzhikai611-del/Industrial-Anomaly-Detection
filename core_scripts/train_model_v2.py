# -*- coding: utf-8 -*-
"""
train_model_v2.py  (V3.5.0 13维 Full 训练脚本)
基于 CNC 铣床仿真传感数据的逻辑回归刀具磨损预测模型训练脚本。
对齐 13 维全功能特征工程：
  - 多尺度窗口 (1min, 5min, 10min)
  - 物理特征 (功率效率比 5min/10min)
  - 交互特征 (current * power)
  - 动态阈值优化 (PR 曲线)
"""

import os
import sys
import warnings
import numpy as np
import pandas as pd
import joblib
from pathlib import Path

warnings.filterwarnings('ignore')

# ── Django 环境初始化 ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'IndustrialWarningSystem.settings')
import django
django.setup()
# ──────────────────────────────────────────────────────────────────

from monitor.models import ProductionSensorData
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, confusion_matrix,
    classification_report, roc_auc_score,
    precision_recall_curve
)

SEP  = '═' * 64
ML_DIR = BASE_DIR / 'ml_models'
ML_DIR.mkdir(exist_ok=True)

FEATURE_COLS = ['spindle_current', 'spindle_power', 'feed_velocity']
NON_CUTTING_STAGES = {'Prep', 'Starting', 'End', 'end', 'Repositioning', 'Idle', 'Down'}

def load_data() -> pd.DataFrame:
    print(f'\n{SEP}')
    print('  📦  Step 1: 从数据库提取数据 …')
    qs = ProductionSensorData.objects.values(
        'device_id', 'timestamp',
        'spindle_current', 'spindle_power', 'feed_velocity',
        'machining_process', 'tool_condition',
    )
    df = pd.DataFrame.from_records(qs)
    if df.empty:
        print('  ❌ 数据库为空！请先运行 venv/bin/python manage.py run_realtime_stream 产生一些数据。')
        sys.exit(1)
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    df = df.sort_values(['device_id', 'timestamp']).reset_index(drop=True)
    df['tool_condition'] = df['tool_condition'].astype(int)
    return df

def filter_cutting_state(df: pd.DataFrame) -> pd.DataFrame:
    print('  🔪  Step 2: 状态过滤 …')
    df = df[~df['machining_process'].isin(NON_CUTTING_STAGES)].copy()
    return df

def rolling_aggregate_v2_full(df: pd.DataFrame) -> pd.DataFrame:
    """
    13 维全功能特征工程。
    """
    print('  📊  Step 3: V2 Full 13维特征工程 …')
    parts = []
    for device_id, grp in df.groupby('device_id'):
        grp = grp.set_index('timestamp').sort_index()
        
        r5 = grp[FEATURE_COLS].rolling('5min', min_periods=1)
        r1 = grp[FEATURE_COLS].rolling('1min', min_periods=1)
        r10 = grp[FEATURE_COLS].rolling('10min', min_periods=1)

        # 1-5: 5min mean/max
        agg = pd.concat([
            r5.mean().add_suffix('_mean_5min'),
            r5.max()[['spindle_current', 'feed_velocity']].add_suffix('_max_5min'),
        ], axis=1)

        # 6-8: 1min mean
        agg = pd.concat([agg, r1.mean().add_suffix('_mean_1min')], axis=1)

        # 9-10: 10min mean
        agg = pd.concat([agg, r10.mean()[['spindle_current', 'spindle_power']].add_suffix('_mean_10min')], axis=1)

        # 11: 交互项
        agg['interaction_current_x_power'] = (
            agg['spindle_current_mean_5min'] * agg['spindle_power_mean_5min']
        )
        
        # 12-13: 效率比
        agg['power_efficiency_ratio'] = (
            agg['spindle_power_mean_5min'] / (agg['spindle_current_mean_5min'].abs() + 0.01)
        )
        agg['power_efficiency_10min'] = (
            agg['spindle_power_mean_10min'] / (agg['spindle_current_mean_10min'].abs() + 0.01)
        )

        agg['device_id']      = device_id
        agg['tool_condition'] = grp['tool_condition']
        parts.append(agg)

    result = pd.concat(parts).reset_index(drop=True)
    feat_cols = [c for c in result.columns if c not in ('device_id', 'tool_condition')]
    return result, feat_cols

def train_and_save():
    df = load_data()
    df = filter_cutting_state(df)
    agg_df, feat_names = rolling_aggregate_v2_full(df)
    
    X = agg_df[feat_names].values
    y = agg_df['tool_condition'].values

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)

    print('  🤖  Step 4: 训练逻辑回归 …')
    model = LogisticRegression(class_weight='balanced', max_iter=2000, C=0.5, random_state=42)
    model.fit(X_train_s, y_train)

    print('  🎯  Step 5: 动态阈值搜索 …')
    y_prob = model.predict_proba(X_test_s)[:, 1]
    precisions, recalls, thresholds = precision_recall_curve(y_test, y_prob)
    best_f1, best_t = 0, 0.5
    for i in range(len(thresholds)):
        if recalls[i] >= 0.60:
            f1 = 2 * precisions[i] * recalls[i] / (precisions[i] + recalls[i] + 1e-8)
            if f1 > best_f1:
                best_f1, best_t = f1, thresholds[i]
    
    print(f'  最优阈值: τ = {best_t:.4f}')
    
    # 保存
    print('  💾  Step 6: 保存模型至 ml_models/ …')
    joblib.dump(model, ML_DIR / 'logistic_model.pkl')
    joblib.dump(scaler, ML_DIR / 'scaler.pkl')
    joblib.dump(feat_names, ML_DIR / 'feature_names.pkl')
    joblib.dump(best_t, ML_DIR / 'optimal_threshold.pkl')
    print('  ✅  保存成功！维度:', len(feat_names))

if __name__ == '__main__':
    train_and_save()
