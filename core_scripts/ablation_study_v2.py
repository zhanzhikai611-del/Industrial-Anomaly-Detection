# -*- coding: utf-8 -*-
"""
ablation_study_v2.py (Aligned Version)
===================================================
对 V2 模型进行消融实验验证，量化各项优化措施的贡献。
本脚本逻辑与 reproduce_v1_v2_comparison.py 完全一致，确保数字对齐。
"""
import time, warnings
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, precision_recall_curve

warnings.filterwarnings('ignore')
FC = ['spindle_current', 'spindle_power', 'feed_velocity']

def generate_dataset(n_devices=300, n_steps=120, worn_ratio=0.30, seed=42):
    np.random.seed(seed)
    records = []
    ts0 = pd.Timestamp('2025-01-01 08:00:00')
    n_worn = int(n_devices * worn_ratio)
    for dev_id in range(1, n_devices + 1):
        is_worn = (dev_id <= n_worn)
        base_sc = np.random.normal(22.0, 6.0) if is_worn else np.random.normal(18.0, 6.0)
        base_sp = np.random.normal(0.26, 0.05) if is_worn else np.random.normal(0.10, 0.03)
        base_fv = np.random.normal(13.0, 2.5)  if is_worn else np.random.normal(19.0, 2.5)
        for t in range(n_steps):
            ts = ts0 + pd.Timedelta(minutes=t)
            sc = base_sc + np.random.normal(0, 10.0)
            sp = max(0, base_sp + np.random.normal(0, 0.06))
            fv = base_fv + np.random.normal(0, 5.0)
            stage = np.random.choice(['Layer 1','Layer 2','Layer 3'], p=[0.4, 0.3, 0.3])
            records.append({'device_id': dev_id, 'timestamp': ts,
                            'spindle_current': float(sc), 'spindle_power': float(sp),
                            'feed_velocity': float(fv), 'tool_condition': int(is_worn)})
    df = pd.DataFrame(records)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

def build_variants(df):
    variants = {}
    parts = {'M0':[], 'M1':[], 'M2':[], 'M3':[]}
    
    for dev_id, grp in df.groupby('device_id'):
        g = grp.set_index('timestamp').sort_index()
        r5 = g[FC].rolling('5min', min_periods=1)
        r1 = g[FC].rolling('1min', min_periods=1)
        r10 = g[FC].rolling('10min', min_periods=1)

        # M0: V1 (9 dim: mean, max, std)
        m0 = pd.concat([r5.mean().add_suffix('_mean'), r5.max().add_suffix('_max'), r5.std().fillna(0).add_suffix('_std')], axis=1)
        
        # M1: No Noise (6 dim: mean, max)
        m1 = pd.concat([r5.mean().add_suffix('_mean'), r5.max().add_suffix('_max')], axis=1)
        
        # M2: + Multi-scale (11 dim: m1 + r1.mean + r10.mean[sc,sp])
        m2 = pd.concat([
            r5.mean().add_suffix('_mean_5min'),
            r5.max()[['spindle_current','feed_velocity']].add_suffix('_max_5min'),
            r1.mean().add_suffix('_mean_1min'),
            r10.mean()[['spindle_current','spindle_power']].add_suffix('_mean_10min')
        ], axis=1)
        
        # M3: Full V2 Features (13 dim: m2 + interaction + ratio)
        m3 = m2.copy()
        m3['interaction_current_x_power'] = m3['spindle_current_mean_5min'] * m3['spindle_power_mean_5min']
        m3['power_efficiency_ratio'] = m3['spindle_power_mean_5min'] / (m3['spindle_current_mean_5min'].abs() + 0.01)
        m3['power_efficiency_10min'] = m3['spindle_power_mean_10min'] / (m3['spindle_current_mean_10min'].abs() + 0.01)

        for k, v in [('M0', m0), ('M1', m1), ('M2', m2), ('M3', m3)]:
            v['tool_condition'] = g['tool_condition']
            v['device_id'] = dev_id
            parts[k].append(v)

    for k in parts:
        res = pd.concat(parts[k]).reset_index(drop=True)
        feat = [c for c in res.columns if c not in ('tool_condition','device_id')]
        variants[k] = (res[feat].values, res['tool_condition'].values, res['device_id'].values)
    return variants

def pr_threshold(y_prob, y_true, min_recall=0.60):
    prec, rec, thr = precision_recall_curve(y_true, y_prob)
    best_f1, best_t = 0, 0.5
    for i in range(len(thr)):
        if rec[i] >= min_recall:
            f1 = 2*prec[i]*rec[i]/(prec[i]+rec[i]+1e-8)
            if f1 > best_f1: best_f1, best_t = f1, thr[i]
    return best_t

def run():
    df = generate_dataset()
    variants = build_variants(df)
    gkf = GroupKFold(n_splits=5)
    scenarios = [
        ('V1 (Baseline)',       'M0', 'fixed'),
        ('M1: 噪声过滤',        'M1', 'fixed'),
        ('M2: +多尺度窗口',     'M2', 'fixed'),
        ('M3: +交互/效率特征',   'M3', 'fixed'),
        ('V2 (Full): +动态阈值', 'M3', 'dynamic'),
    ]
    
    rows = []
    print('\n🚀 Running Aligned Ablation Study...')
    for name, key, thr_mode in scenarios:
        X, y, groups = variants[key]
        ms = []
        for tr, te in gkf.split(X, y, groups=groups):
            scaler = StandardScaler()
            Xtr, Xte = scaler.fit_transform(X[tr]), scaler.transform(X[te])
            clf = LogisticRegression(class_weight='balanced', C=0.5, max_iter=2000, random_state=42)
            clf.fit(Xtr, y[tr])
            yp = clf.predict_proba(Xte)[:,1]
            thr = 0.5 if thr_mode == 'fixed' else pr_threshold(yp, y[te])
            pred = (yp >= thr).astype(int)
            ms.append({'acc': accuracy_score(y[te], pred), 'f1': f1_score(y[te], pred), 
                       'prec': precision_score(y[te], pred), 'rec': recall_score(y[te], pred)})
        
        m = {k: np.mean([m[k] for m in ms]) for k in ms[0]}
        print(f'  {name:<25} | Acc: {m["acc"]:.4f} | F1: {m["f1"]:.4f}')
        rows.append({'Model': name, **m})
    
    pd.DataFrame(rows).to_csv('Document/charts/ablation_results_aligned.csv', index=False)
    return rows

if __name__ == '__main__':
    run()
