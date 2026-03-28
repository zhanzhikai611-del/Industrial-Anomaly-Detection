# -*- coding: utf-8 -*-
"""
train_model_v2_academic.py
V3.4.0 学术对标验证 —— 基于真实密歇根大学 CNC 数据集 (全量传感器)

实验设计：
  数据源: CNCData/experiment_*.csv (18 组实验)
  传感器: 全量 44 个传感器通道 + feedrate/clamp_pressure 元数据
  样本粒度: 实验级聚合 (每组实验 → 1 个样本向量)
  验证方法: Leave-One-Out Cross Validation (18-fold)
  对比: V1 (3路传感器, 9维) vs V2 (全量传感器+多尺度+交互+元数据)
"""

import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import LeaveOneOut
from sklearn.metrics import (
    accuracy_score, confusion_matrix,
    classification_report, roc_auc_score,
    precision_recall_curve
)

SEP = '═' * 64
DATA_DIR = Path(__file__).resolve().parent.parent / 'CNCData'

# 仅保留切削阶段
NON_CUTTING = {'Prep', 'Starting', 'End', 'end', 'Repositioning', 'Starting '}

# 传感器通道分组
SENSOR_GROUPS = {
    'X1': ['X1_ActualPosition', 'X1_ActualVelocity', 'X1_ActualAcceleration',
           'X1_CommandPosition', 'X1_CommandVelocity', 'X1_CommandAcceleration',
           'X1_CurrentFeedback', 'X1_DCBusVoltage', 'X1_OutputCurrent',
           'X1_OutputVoltage', 'X1_OutputPower'],
    'Y1': ['Y1_ActualPosition', 'Y1_ActualVelocity', 'Y1_ActualAcceleration',
           'Y1_CommandPosition', 'Y1_CommandVelocity', 'Y1_CommandAcceleration',
           'Y1_CurrentFeedback', 'Y1_DCBusVoltage', 'Y1_OutputCurrent',
           'Y1_OutputVoltage', 'Y1_OutputPower'],
    'Z1': ['Z1_ActualPosition', 'Z1_ActualVelocity', 'Z1_ActualAcceleration',
           'Z1_CommandPosition', 'Z1_CommandVelocity', 'Z1_CommandAcceleration',
           'Z1_CurrentFeedback', 'Z1_DCBusVoltage', 'Z1_OutputCurrent',
           'Z1_OutputVoltage'],
    'S1': ['S1_ActualPosition', 'S1_ActualVelocity', 'S1_ActualAcceleration',
           'S1_CommandPosition', 'S1_CommandVelocity', 'S1_CommandAcceleration',
           'S1_CurrentFeedback', 'S1_DCBusVoltage', 'S1_OutputCurrent',
           'S1_OutputVoltage', 'S1_OutputPower', 'S1_SystemInertia'],
    'M1': ['M1_CURRENT_FEEDRATE'],
}

# 展平所有传感器列名
ALL_SENSORS = []
for group_cols in SENSOR_GROUPS.values():
    ALL_SENSORS.extend(group_cols)

# 系统部署使用的 3 路核心传感器
CORE_3_SENSORS = ['S1_CurrentFeedback', 'S1_OutputPower', 'X1_ActualVelocity']


def load_kaggle_meta():
    print(f'\n{SEP}')
    print('  📦  加载密歇根大学 CNC 数据集元数据 …')
    print(SEP)
    meta = pd.read_csv(DATA_DIR / 'train.csv')
    print(f'  实验总数: {len(meta)}  |  Unworn: {(meta["tool_condition"]=="unworn").sum()}'
          f'  |  Worn: {(meta["tool_condition"]=="worn").sum()}')
    return meta


def extract_experiment_features(exp_df, sensors, include_multiscale=False):
    """
    对单个实验提取特征：
    - 基础: mean / max / std / min / median (每个传感器 5 个统计量)
    - 多尺度 (可选): 前半段 vs 后半段的 delta
    - 高阶 (可选): kurtosis, skew
    """
    cutting = exp_df[~exp_df['Machining_Process'].isin(NON_CUTTING)].copy()
    if len(cutting) < 10:
        cutting = exp_df  # fallback

    # 过滤实际存在的传感器列
    valid_sensors = [s for s in sensors if s in cutting.columns]

    features = {}

    # ── 基础统计量 ──
    for col in valid_sensors:
        series = cutting[col].dropna()
        if len(series) == 0:
            features[f'{col}_mean'] = 0
            features[f'{col}_std'] = 0
            features[f'{col}_max'] = 0
            continue
        features[f'{col}_mean'] = series.mean()
        features[f'{col}_std']  = series.std()
        features[f'{col}_max']  = series.max()
        features[f'{col}_min']  = series.min()
        features[f'{col}_median'] = series.median()

    if include_multiscale and len(cutting) > 20:
        mid = len(cutting) // 2
        first  = cutting.iloc[:mid]
        second = cutting.iloc[mid:]

        for col in valid_sensors:
            s1 = first[col].dropna()
            s2 = second[col].dropna()
            if len(s1) > 0 and len(s2) > 0:
                # Delta 特征 (后半段 - 前半段)
                features[f'{col}_delta_mean'] = s2.mean() - s1.mean()
                features[f'{col}_delta_std']  = s2.std() - s1.std()
                # 高阶统计量
                features[f'{col}_kurtosis'] = series.kurtosis() if len(series) > 3 else 0
                features[f'{col}_skew']     = series.skew() if len(series) > 2 else 0

    return features


def build_dataset(meta, sensors, version_name, include_multiscale=False, include_meta=False):
    """构建特征矩阵"""
    print(f'\n  🔧  构建 {version_name} 特征 …')

    all_feats = []
    labels = []
    for _, row in meta.iterrows():
        exp_df = pd.read_csv(DATA_DIR / f'experiment_{row["No"]:02d}.csv')
        feats = extract_experiment_features(exp_df, sensors, include_multiscale)

        # 元数据注入
        if include_meta:
            feats['feedrate'] = row['feedrate']
            feats['clamp_pressure'] = row['clamp_pressure']

        all_feats.append(feats)
        labels.append(1 if row['tool_condition'] == 'worn' else 0)

    X_df = pd.DataFrame(all_feats).fillna(0)
    y = np.array(labels)
    print(f'    样本数: {len(X_df)}  |  特征维度: {X_df.shape[1]}')
    return X_df.values, y, list(X_df.columns)


def loo_evaluate(X, y, feature_names, version_name, C=1.0):
    """Leave-One-Out 交叉验证"""
    print(f'\n{SEP}')
    print(f'  📈  {version_name}  (LOO-CV, C={C})')
    print(SEP)

    loo = LeaveOneOut()
    y_pred = np.zeros(len(y), dtype=int)
    y_prob = np.zeros(len(y))

    for train_idx, test_idx in loo.split(X):
        scaler = StandardScaler()
        X_tr = scaler.fit_transform(X[train_idx])
        X_te = scaler.transform(X[test_idx])

        model = LogisticRegression(
            class_weight='balanced', max_iter=5000,
            C=C, solver='lbfgs', random_state=42,
        )
        model.fit(X_tr, y[train_idx])
        y_pred[test_idx] = model.predict(X_te)
        y_prob[test_idx] = model.predict_proba(X_te)[0, 1]

    acc = accuracy_score(y, y_pred)
    cm  = confusion_matrix(y, y_pred)
    try:
        roc = roc_auc_score(y, y_prob)
    except:
        roc = None

    print(f'  Accuracy: {acc:.4f} ({int(acc*len(y))}/{len(y)})')
    if roc: print(f'  ROC-AUC:  {roc:.4f}')
    print(f'  Confusion Matrix:  TN={cm[0,0]}  FP={cm[0,1]}  |  FN={cm[1,0]}  TP={cm[1,1]}')
    print(classification_report(y, y_pred,
                                target_names=['Unworn(0)', 'Worn(1)'], digits=4))

    # 逐实验
    print(f'  逐实验明细:')
    for i in range(len(y)):
        tl = 'Worn' if y[i] else 'Unworn'
        pl = 'Worn' if y_pred[i] else 'Unworn'
        st = '✅' if y[i] == y_pred[i] else '❌'
        print(f'    Exp {i+1:>2}: 真实={tl:<7} 预测={pl:<7} P(worn)={y_prob[i]:.4f} {st}')

    return acc, roc, cm, y_pred, y_prob


if __name__ == '__main__':
    print(f'\n{SEP}')
    print('  🔬  V3.4.0 学术对标验证 —— 真实密歇根 CNC 数据集')
    print('  📊  Leave-One-Out Cross Validation (18-fold)')
    print(SEP)

    meta = load_kaggle_meta()

    results = {}

    # ── 实验 A: V1 基线 (3路传感器, 基础统计量) ──
    X_a, y_a, f_a = build_dataset(
        meta, CORE_3_SENSORS, 'V1 基线 (3路×3统计=9维)',
        include_multiscale=False, include_meta=False
    )
    results['V1_3ch_basic'] = loo_evaluate(X_a, y_a, f_a, 'V1: 3路传感器 × 基础统计量', C=1.0)

    # ── 实验 B: V2-A (3路传感器, 多尺度+元数据) ──
    X_b, y_b, f_b = build_dataset(
        meta, CORE_3_SENSORS, 'V2-A (3路+多尺度+元数据)',
        include_multiscale=True, include_meta=True
    )
    results['V2A_3ch_enhanced'] = loo_evaluate(X_b, y_b, f_b, 'V2-A: 3路 + 多尺度 + 元数据', C=0.5)

    # ── 实验 C: V2-B (全量传感器, 基础统计量) ──
    X_c, y_c, f_c = build_dataset(
        meta, ALL_SENSORS, 'V2-B (全量传感器×基础统计)',
        include_multiscale=False, include_meta=False
    )
    results['V2B_all_basic'] = loo_evaluate(X_c, y_c, f_c, 'V2-B: 全量传感器 × 基础统计量', C=0.1)

    # ── 实验 D: V2-C (全量传感器 + 多尺度 + 元数据) 完整版 ──
    X_d, y_d, f_d = build_dataset(
        meta, ALL_SENSORS, 'V2-C (全量+多尺度+元数据)',
        include_multiscale=True, include_meta=True
    )
    results['V2C_all_full'] = loo_evaluate(X_d, y_d, f_d, 'V2-C: 全量 + 多尺度 + 元数据 (完整版)', C=0.1)

    # ── 综合对比 ──
    print(f'\n{SEP}')
    print('  📊  全量对比汇总 (真实密歇根 CNC 数据集, LOO-CV)')
    print(SEP)

    labels = {
        'V1_3ch_basic':      'V1: 3路×基础 (9维)',
        'V2A_3ch_enhanced':  'V2-A: 3路+多尺度+元数据',
        'V2B_all_basic':     'V2-B: 全量×基础',
        'V2C_all_full':      'V2-C: 全量+完整版',
    }

    print(f'  {"方案":<30} {"特征维度":>8} {"Accuracy":>10} {"ROC-AUC":>10}')
    print(f'  {"─"*30} {"─"*8} {"─"*10} {"─"*10}')
    for key, label in labels.items():
        acc, roc, cm, _, _ = results[key]
        feat_dim = {'V1_3ch_basic': len(f_a), 'V2A_3ch_enhanced': len(f_b),
                    'V2B_all_basic': len(f_c), 'V2C_all_full': len(f_d)}[key]
        roc_str = f'{roc:.4f}' if roc else 'N/A'
        print(f'  {label:<30} {feat_dim:>8} {acc:>9.2%} {roc_str:>10}')

    print(f'{SEP}\n')
