"""
train_model.py
基于 CNC 铣床传感数据的逻辑回归刀具磨损预测模型训练脚本

参考:
  Kaggle Best Practice: CNC Milling Machine Tool Wear Detection
  (https://www.kaggle.com/code/koheimuramatsu/cnc-milling-machine-tool-wear-detection)

特征工程流水线：
  1. 状态过滤 (State Filtering)       —— 仅保留切削阶段数据
  2. 特征降维 (Feature Selection)     —— 三路传感器特征
  3. 滚动聚合 (Rolling Aggregation)   —— 5分钟窗口 mean/max/std
  4. 标准化   (StandardScaler)        —— 消除量纲差异

运行方式：
  venv/bin/python train_model.py
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
    classification_report, roc_auc_score
)

SEP  = '═' * 64
SEP2 = '─' * 64

# ═══════════════════════════════════════════════════════════════════
#  Step 0：创建模型持久化目录
# ═══════════════════════════════════════════════════════════════════
ML_DIR = Path('ml_models')
ML_DIR.mkdir(exist_ok=True)


# ═══════════════════════════════════════════════════════════════════
#  Step 1：从 Django ORM 提取数据到 DataFrame
# ═══════════════════════════════════════════════════════════════════

def load_data() -> pd.DataFrame:
    print(f'\n{SEP}')
    print('  📦  Step 1: 从数据库提取 ProductionSensorData …')
    print(SEP)

    qs = ProductionSensorData.objects.values(
        'id', 'device_id', 'timestamp',
        'spindle_current', 'spindle_power', 'feed_velocity',
        'machining_process', 'tool_condition',
        'loading_time', 'downtime', 'input_qty', 'actual_output',
    )
    df = pd.DataFrame.from_records(qs)

    if df.empty:
        print('  ❌ 数据库为空！请先运行 simulate_real_cnc_data.py')
        sys.exit(1)

    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    df = df.sort_values(['device_id', 'timestamp']).reset_index(drop=True)
    df['tool_condition'] = df['tool_condition'].astype(int)

    print(f'  总行数：{len(df):,}  |  磨损(worn=1)：{df["tool_condition"].sum():,}'
          f'  ({df["tool_condition"].mean():.1%})')
    return df


# ═══════════════════════════════════════════════════════════════════
#  Step 2：状态过滤 —— 仅保留切削阶段 (Kaggle 关键预处理)
# ═══════════════════════════════════════════════════════════════════

# 非切削阶段（Prep / Repositioning / End 等空转阶段）
NON_CUTTING_STAGES = {'Prep', 'Starting', 'End', 'end', 'Repositioning'}

def filter_cutting_state(df: pd.DataFrame) -> pd.DataFrame:
    print(f'\n{SEP}')
    print('  🔪  Step 2: 状态过滤 —— 仅保留切削阶段 (Layer*) …')
    print(SEP)

    before = len(df)
    df = df[~df['machining_process'].isin(NON_CUTTING_STAGES)].copy()
    after = len(df)

    print(f'  过滤前: {before:,} 行  →  过滤后: {after:,} 行  (剔除 {before-after:,} 条空转数据)')
    print(f'  保留阶段: {sorted(df["machining_process"].unique())}')
    return df


# ═══════════════════════════════════════════════════════════════════
#  Step 3：时序滚动聚合特征工程 (5分钟窗口)
# ═══════════════════════════════════════════════════════════════════

FEATURE_COLS   = ['spindle_current', 'spindle_power', 'feed_velocity']
WINDOW         = '5min'
AGG_FUNCS      = ['mean', 'max', 'std']

def rolling_aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """
    按 device_id 分组，对三路传感器特征以 5 分钟滚动窗口计算 mean/max/std。
    std 特别适合捕捉刀具震颤：磨损时切削力波动剧烈，std 显著增大。
    """
    print(f'\n{SEP}')
    print(f'  📊  Step 3: 时序滚动聚合 (窗口={WINDOW}, 聚合={AGG_FUNCS}) …')
    print(SEP)

    parts = []
    for device_id, grp in df.groupby('device_id'):
        grp = grp.set_index('timestamp').sort_index()
        rolled = grp[FEATURE_COLS].rolling(WINDOW, min_periods=1)

        agg_df = pd.concat([
            rolled.mean().add_suffix('_mean'),
            rolled.max().add_suffix('_max'),
            rolled.std().fillna(0).add_suffix('_std'),
        ], axis=1)

        agg_df['device_id']      = device_id
        agg_df['tool_condition'] = grp['tool_condition']
        parts.append(agg_df)

    result = pd.concat(parts).reset_index(drop=True)
    feat_cols = [c for c in result.columns if c not in ('device_id', 'tool_condition')]
    print(f'  生成特征维度: {len(feat_cols)} 个特征')
    print(f'  特征列表: {feat_cols}')
    return result


# ═══════════════════════════════════════════════════════════════════
#  Step 4：构建特征矩阵 X 和标签 Y
# ═══════════════════════════════════════════════════════════════════

def build_xy(agg_df: pd.DataFrame):
    feature_names = [c for c in agg_df.columns
                     if c not in ('device_id', 'tool_condition')]
    X = agg_df[feature_names].values
    y = agg_df['tool_condition'].values
    return X, y, feature_names


# ═══════════════════════════════════════════════════════════════════
#  Step 5：数据集划分 + 标准化
# ═══════════════════════════════════════════════════════════════════

def prepare_dataset(X, y):
    print(f'\n{SEP}')
    print('  ⚖️  Step 4: 数据集划分（80/20）+ StandardScaler 标准化 …')
    print(SEP)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    print(f'  训练集: {len(X_train):,} 条  |  测试集: {len(X_test):,} 条')
    print(f'  训练集磨损率: {y_train.mean():.1%}  |  测试集磨损率: {y_test.mean():.1%}')

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s  = scaler.transform(X_test)

    print('  StandardScaler 均值（前3维）:', scaler.mean_[:3].round(4))
    print('  StandardScaler 标准差（前3维）:', scaler.scale_[:3].round(4))

    return X_train_s, X_test_s, y_train, y_test, scaler


# ═══════════════════════════════════════════════════════════════════
#  Step 6：训练逻辑回归模型（class_weight='balanced' 应对类别不平衡）
# ═══════════════════════════════════════════════════════════════════

def train_logistic(X_train, y_train) -> LogisticRegression:
    print(f'\n{SEP}')
    print('  🤖  Step 5: 训练 LogisticRegression (class_weight=balanced) …')
    print(SEP)

    model = LogisticRegression(
        class_weight='balanced',   # 自动加权：惩罚稀少的磨损样本误分类
        max_iter=1000,
        C=1.0,                     # 正则化强度（默认 L2）
        solver='lbfgs',
        random_state=42,
    )
    model.fit(X_train, y_train)
    print('  模型参数:')
    print(f'    solver={model.solver}  C={model.C}  max_iter={model.max_iter}')
    print(f'    n_iter_={model.n_iter_}  classes_={model.classes_}')
    return model


# ═══════════════════════════════════════════════════════════════════
#  Step 7：模型评估
# ═══════════════════════════════════════════════════════════════════

def evaluate(model, X_test, y_test, feature_names):
    print(f'\n{SEP}')
    print('  📈  Step 6: 测试集评估结果')
    print(SEP)

    y_pred  = model.predict(X_test)
    y_prob  = model.predict_proba(X_test)[:, 1]

    acc = accuracy_score(y_test, y_pred)
    cm  = confusion_matrix(y_test, y_pred)
    roc = roc_auc_score(y_test, y_prob)

    print(f'\n  Accuracy : {acc:.4f}')
    print(f'  ROC-AUC  : {roc:.4f}')
    print(f'\n  Confusion Matrix:')
    print(f'    TN={cm[0,0]:>6}  FP={cm[0,1]:>6}')
    print(f'    FN={cm[1,0]:>6}  TP={cm[1,1]:>6}')
    print(f'\n  Classification Report (正类=磨损 worn=1):')
    print(classification_report(y_test, y_pred,
                                target_names=['正常(0)', '磨损(1)'],
                                digits=4))

    # 特征重要性（逻辑回归系数绝对值）
    coef_abs  = np.abs(model.coef_[0])
    rank_idx  = np.argsort(coef_abs)[::-1]
    print(f'  特征重要性排行（逻辑回归系数 |coef|）:')
    for i, idx in enumerate(rank_idx):
        bar = '█' * int(coef_abs[idx] / coef_abs[rank_idx[0]] * 20)
        print(f'    {i+1:>2}. {feature_names[idx]:<30} |coef|={coef_abs[idx]:.4f}  {bar}')

    return y_pred, y_prob


# ═══════════════════════════════════════════════════════════════════
#  Step 8：模型持久化
# ═══════════════════════════════════════════════════════════════════

def save_artifacts(model, scaler, feature_names):
    print(f'\n{SEP}')
    print('  💾  Step 7: 持久化模型与标准化器 …')
    print(SEP)

    model_path  = ML_DIR / 'logistic_model.pkl'
    scaler_path = ML_DIR / 'scaler.pkl'
    meta_path   = ML_DIR / 'feature_names.pkl'

    joblib.dump(model,         model_path)
    joblib.dump(scaler,        scaler_path)
    joblib.dump(feature_names, meta_path)

    print(f'  ✅  {model_path}   ({model_path.stat().st_size/1024:.1f} KB)')
    print(f'  ✅  {scaler_path}  ({scaler_path.stat().st_size/1024:.1f} KB)')
    print(f'  ✅  {meta_path}    ({meta_path.stat().st_size/1024:.1f} KB)')


# ═══════════════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print(f'\n{SEP}')
    print('  🏭  CNC 刀具磨损逻辑回归预警模型  ·  训练流水线启动')
    print(SEP)

    # 流水线
    df_raw  = load_data()
    df_cut  = filter_cutting_state(df_raw)
    agg_df  = rolling_aggregate(df_cut)
    X, y, feature_names = build_xy(agg_df)

    X_train, X_test, y_train, y_test, scaler = prepare_dataset(X, y)
    model = train_logistic(X_train, y_train)
    evaluate(model, X_test, y_test, feature_names)
    save_artifacts(model, scaler, feature_names)

    print(f'\n{SEP}')
    print('  🎉  训练完成！模型文件保存在 ml_models/ 目录')
    print(SEP + '\n')
