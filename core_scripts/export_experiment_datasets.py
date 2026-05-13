# -*- coding: utf-8 -*-
"""
export_experiment_datasets.py
===================================================
将 reproduce_v1_v2_comparison.py 生成的实验数据集（Raw, V1, V2）导出为 CSV 文档。
"""
import pandas as pd
import numpy as np
from pathlib import Path
import sys

# 尝试从 core_scripts 导入
sys.path.append(str(Path(__file__).parent))
try:
    from reproduce_v1_v2_comparison import generate_dataset, build_features
except ImportError:
    print("无法导入 reproduce_v1_v2_comparison，请确保脚本在 core_scripts 目录下运行。")
    sys.exit(1)

def export():
    # 1. 生成原始数据集
    df_raw = generate_dataset(n_devices=300, n_steps=120, worn_ratio=0.30, seed=42)
    raw_path = Path('experiment_raw_data.csv')
    df_raw.to_csv(raw_path, index=False)
    print(f"✅ 原始仿真数据已保存至: {raw_path}")

    # 2. 构建 V1 特征集 (实验一基线)
    X_v1, y_v1, g_v1, f_v1 = build_features(df_raw, 'v1')
    df_v1 = pd.DataFrame(X_v1, columns=f_v1)
    df_v1['tool_condition'] = y_v1
    df_v1['device_id'] = g_v1
    v1_path = Path('experiment_v1_features.csv')
    df_v1.to_csv(v1_path, index=False)
    print(f"✅ 实验一 (V1) 特征数据集已保存至: {v1_path}")

    # 3. 构建 V2 特征集 (实验一 V2 & 实验二)
    X_v2, y_v2, g_v2, f_v2 = build_features(df_raw, 'v2')
    df_v2 = pd.DataFrame(X_v2, columns=f_v2)
    df_v2['tool_condition'] = y_v2
    df_v2['device_id'] = g_v2
    v2_path = Path('experiment_v2_features.csv')
    df_v2.to_csv(v2_path, index=False)
    print(f"✅ 实验一/二 (V2) 特征数据集已保存至: {v2_path}")

if __name__ == '__main__':
    export()
