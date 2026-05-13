import os, sys, warnings
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'IndustrialWarningSystem.settings')
import django
django.setup()
from monitor.models import ProductionSensorData
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score

qs = ProductionSensorData.objects.values('device_id', 'timestamp', 'spindle_current', 'spindle_power', 'feed_velocity', 'machining_process', 'tool_condition')
df = pd.DataFrame.from_records(qs)
df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
df = df.sort_values(['device_id', 'timestamp']).reset_index(drop=True)
df = df[~df['machining_process'].isin({'Prep', 'Starting', 'End', 'end', 'Repositioning'})].copy()
parts = []
for device_id, grp in df.groupby('device_id'):
    grp = grp.set_index('timestamp').sort_index()
    rolled = grp[['spindle_current', 'spindle_power', 'feed_velocity']].rolling('5min', min_periods=1)
    agg_df = pd.concat([rolled.mean().add_suffix('_mean'), rolled.max().add_suffix('_max'), rolled.std().fillna(0).add_suffix('_std')], axis=1)
    agg_df['device_id'] = device_id
    agg_df['tool_condition'] = grp['tool_condition']
    parts.append(agg_df)
agg_df = pd.concat(parts).reset_index(drop=True)
cols = [c for c in agg_df.columns if c not in ('device_id', 'tool_condition')]
X = agg_df[cols].values
y = agg_df['tool_condition'].astype(int).values
X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
scaler = StandardScaler()
X_tr_s = scaler.fit_transform(X_tr)
X_te_s = scaler.transform(X_te)
for c in [1.0, 0.5]:
    model = LogisticRegression(class_weight='balanced', max_iter=1000, C=c, solver='lbfgs', random_state=42)
    model.fit(X_tr_s, y_tr)
    pred = model.predict(X_te_s)
    prob = model.predict_proba(X_te_s)[:, 1]
    print(f"C={c}: Acc={accuracy_score(y_te, pred):.4f}, AUC={roc_auc_score(y_te, prob):.4f}")
