import os
import django
import joblib
import numpy as np
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'IndustrialWarningSystem.settings')
django.setup()

base_dir = Path('/Users/chanchihkai/Documents/基于机器学习的工业生产数据异常预警系统')
model = joblib.load(base_dir / 'ml_models/logistic_model.pkl')
scaler = joblib.load(base_dir / 'ml_models/scaler.pkl')

def get_prob(sc, sp, fv):
    x = np.array([[sc, sp, fv, sc, sp, fv, 0.0, 0.0, 0.0]])
    return model.predict_proba(scaler.transform(x))[0, 1]

params = [
    (15, 0.13),
    (25, 0.18),
    (28, 0.25),
    (32, 0.32),
    (42, 0.40),
]

print("sc, sp => prob")
for sc, sp in params:
    print(f"{sc}, {sp} => {get_prob(sc, sp, 0):.3f}")
