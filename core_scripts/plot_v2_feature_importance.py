# -*- coding: utf-8 -*-
"""
plot_v2_feature_importance.py
用训练好的 LR V2 模型系数绘制 13 维特征重要性图
运行：venv/bin/python core_scripts/plot_v2_feature_importance.py
"""
import warnings
import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from pathlib import Path

warnings.filterwarnings('ignore')
matplotlib.rcParams['font.family'] = ['Arial Unicode MS', 'PingFang SC', 'Heiti TC',
                                       'STHeiti', 'sans-serif']
matplotlib.rcParams['axes.unicode_minus'] = False

from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression

# ── 1. 还原数据生成（与 reproduce_v1_v2_comparison.py 完全一致）──────────

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
            stage = np.random.choice(
                ['Layer 1 Up','Layer 1 Down','Layer 2 Up',
                 'Layer 2 Down','Layer 3 Up','Repositioning'],
                p=[0.20,0.18,0.18,0.16,0.14,0.14]
            )
            records.append({'device_id': dev_id, 'timestamp': ts,
                            'spindle_current': float(sc), 'spindle_power': float(sp),
                            'feed_velocity': float(fv), 'machining_process': stage,
                            'tool_condition': int(is_worn)})
    df = pd.DataFrame(records)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df[df['machining_process'] != 'Repositioning'].copy()


def build_v2(df):
    parts = []
    for dev_id, grp in df.groupby('device_id'):
        g = grp.set_index('timestamp').sort_index()
        r5  = g[FC].rolling('5min',  min_periods=1)
        r10 = g[FC].rolling('10min', min_periods=1)
        r1  = g[FC].rolling('1min',  min_periods=1)
        agg = pd.concat([
            r5.mean().add_suffix('_mean_5min'),
            r5.max()[['spindle_current','feed_velocity']].add_suffix('_max_5min'),
            r1.mean().add_suffix('_mean_1min'),
            r10.mean()[['spindle_current','spindle_power']].add_suffix('_mean_10min'),
        ], axis=1)
        agg['interaction_current_x_power'] = (
            agg['spindle_current_mean_5min'] * agg['spindle_power_mean_5min'])
        agg['power_efficiency_ratio'] = (
            agg['spindle_power_mean_5min'] / (agg['spindle_current_mean_5min'].abs() + 0.01))
        agg['power_efficiency_10min'] = (
            agg['spindle_power_mean_10min'] / (agg['spindle_current_mean_10min'].abs() + 0.01))
        agg['tool_condition'] = g['tool_condition']
        parts.append(agg)
    res  = pd.concat(parts).reset_index(drop=True)
    feat = [c for c in res.columns if c != 'tool_condition']
    return res[feat].values, res['tool_condition'].values, feat


# ── 2. 训练 LR V2 ───────────────────────────────────────────────────────
print('生成数据集…')
df = generate_dataset()
X, y, feat_names = build_v2(df)

scaler = StandardScaler()
Xs = scaler.fit_transform(X)

model = LogisticRegression(class_weight='balanced', C=0.5, max_iter=2000, random_state=42)
model.fit(Xs, y)
coef_abs = np.abs(model.coef_[0])

# ── 3. 整理特征标签 ─────────────────────────────────────────────────────
LABEL_MAP = {
    'spindle_current_mean_5min':      'sc_mean_5min',
    'spindle_power_mean_5min':        'sp_mean_5min',
    'feed_velocity_mean_5min':        'fv_mean_5min',
    'spindle_current_max_5min':       'sc_max_5min',
    'feed_velocity_max_5min':         'fv_max_5min',
    'spindle_current_mean_1min':      'sc_mean_1min ★',
    'spindle_power_mean_1min':        'sp_mean_1min ★',
    'feed_velocity_mean_1min':        'fv_mean_1min ★',
    'spindle_current_mean_10min':     'sc_mean_10min ★',
    'spindle_power_mean_10min':       'sp_mean_10min ★',
    'interaction_current_x_power':    'current × power ★',
    'power_efficiency_ratio':         'power_eff_5min ★',
    'power_efficiency_10min':         'power_eff_10min ★',
}

# V2 新增特征（橙色），V1 保留特征（蓝色）
V2_NEW = {
    'spindle_current_mean_1min', 'spindle_power_mean_1min', 'feed_velocity_mean_1min',
    'spindle_current_mean_10min', 'spindle_power_mean_10min',
    'interaction_current_x_power', 'power_efficiency_ratio', 'power_efficiency_10min',
}

labels = [LABEL_MAP.get(f, f) for f in feat_names]
colors = ['#E8824A' if f in V2_NEW else '#5B9BD5' for f in feat_names]

# 按系数绝对值从大到小排序
order = np.argsort(coef_abs)  # 从小到大（水平条图从下到上）
sorted_labels  = [labels[i]  for i in order]
sorted_coefs   = coef_abs[order]
sorted_colors  = [colors[i]  for i in order]

# ── 4. 绘图 ─────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(11, 7))
fig.patch.set_facecolor('white')
ax.set_facecolor('#F8F9FA')

bars = ax.barh(sorted_labels, sorted_coefs,
               color=sorted_colors, edgecolor='white',
               linewidth=0.8, height=0.68)

# 数值标注
for bar, val in zip(bars, sorted_coefs):
    ax.text(val + 0.03, bar.get_y() + bar.get_height()/2,
            f'{val:.2f}', va='center', ha='left',
            fontsize=9.5, color='#333333', fontweight='bold')

# 分界线
ax.axvline(x=0, color='#CCCCCC', linewidth=0.8)

# 图例
patch_v1  = mpatches.Patch(facecolor='#5B9BD5', label='V1 保留特征（5min 均值/最大值）')
patch_v2  = mpatches.Patch(facecolor='#E8824A', label='V2 核心新增（多尺度 + 效率 + 交互）★')
ax.legend(handles=[patch_v1, patch_v2], loc='lower right',
          fontsize=10, framealpha=0.9, edgecolor='#CCCCCC')

# 注释最重要特征
top_feat  = sorted_labels[-1]
top_val   = sorted_coefs[-1]
ax.annotate('核心判别特征\n（主轴电流中期趋势）',
            xy=(top_val, len(sorted_labels)-1),
            xytext=(top_val - 2.8, len(sorted_labels)-1 - 2.5),
            fontsize=9, color='#5B9BD5',
            arrowprops=dict(arrowstyle='->', color='#5B9BD5', lw=1.5))

# 注释交互特征
inter_idx = sorted_labels.index('current × power ★')
inter_val = sorted_coefs[inter_idx]
ax.annotate('V2 新增：物理耦合\n交互特征（Attention 映射）',
            xy=(inter_val, inter_idx),
            xytext=(inter_val + 0.8, inter_idx - 2),
            fontsize=9, color='#E8824A',
            arrowprops=dict(arrowstyle='->', color='#E8824A', lw=1.5))

# 网格 & 样式
ax.grid(axis='x', linestyle='--', alpha=0.5, color='#CCCCCC')
ax.set_xlabel('特征重要性（逻辑回归系数绝对值 |coef|）', fontsize=11, labelpad=8)
ax.set_title('V2 逻辑回归模型特征重要性权重分布（13维）', fontsize=14, fontweight='bold', pad=14)
ax.spines[['top','right']].set_visible(False)
ax.tick_params(axis='y', labelsize=10)
ax.tick_params(axis='x', labelsize=9)

# 副标题
fig.text(0.5, 0.01,
         '注：橙色柱子代表 V2 算法新增特征，涵盖三尺度时序窗口、功率效率比与非线性交互特征。',
         ha='center', fontsize=9, color='#666666')

plt.tight_layout(rect=[0, 0.04, 1, 1])

out = Path(__file__).parent.parent / 'Document' / 'charts' / 'v2_feature_importance_13dim.png'
out.parent.mkdir(exist_ok=True)
plt.savefig(out, dpi=180, bbox_inches='tight', facecolor='white')
print(f'✅ 已保存至 {out}')
plt.show()
