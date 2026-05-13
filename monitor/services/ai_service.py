# -*- coding: utf-8 -*-
"""
ai_service.py — V2 在线推断服务 (终极稳态版)
采用“切削触发更新”策略，彻底锁定非切削期的波动。
"""
import logging
from pathlib import Path
from collections import deque
from datetime import datetime, timedelta
import numpy as np
import joblib
from ..models import ProductionSensorData

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  模型加载
# ═══════════════════════════════════════════════════════════════════

_ML_DIR = Path(__file__).resolve().parent.parent.parent / 'ml_models'

def _load_artifacts():
    try:
        model      = joblib.load(_ML_DIR / 'logistic_model.pkl')
        scaler     = joblib.load(_ML_DIR / 'scaler.pkl')
        threshold  = joblib.load(_ML_DIR / 'optimal_threshold.pkl')
        return model, scaler, threshold
    except:
        return None, None, 0.5

_LR_MODEL, _SCALER, _THRESHOLD = _load_artifacts()

# ═══════════════════════════════════════════════════════════════════
#  状态存储
# ═══════════════════════════════════════════════════════════════════

_SMOOTHED_RAW: dict[str, np.ndarray] = {}  
_SMOOTHED_PROBS: dict[str, float] = {}     
_DEVICE_BUFFERS: dict[str, deque] = {}     

_FEAT_EMA_ALPHA = 0.15 
_PROB_EMA_ALPHA = 0.05  # 略微调高，让反馈更及时
_BUFFER_MAX_SECONDS = 600

# ═══════════════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════════════

def _compute_window_stats(buf: deque, ts: datetime, window_secs: int):
    cutoff = ts - timedelta(seconds=window_secs)
    sc_v, sp_v, fv_v = [], [], []
    for t, sc, sp, fv in buf:
        if t >= cutoff:
            sc_v.append(sc); sp_v.append(sp); fv_v.append(fv)
    if not sc_v: return 0.0, 0.0, 0.0, 0.0, 0.0
    return np.mean(sc_v), np.mean(sp_v), np.mean(fv_v), np.max(sc_v), np.max(fv_v)

def _calibrate(raw_p: float) -> float:
    p = np.clip(raw_p, 1e-9, 1 - 1e-9)
    logit = np.log(p / (1 - p))
    calibrated = 1.0 / (1.0 + np.exp(-(logit + 1.5) / 6.0))
    return float(np.round(calibrated, 4))

# ═══════════════════════════════════════════════════════════════════
#  公开接口 (核心策略：Value Hold)
# ═══════════════════════════════════════════════════════════════════

def predict_proba(record: ProductionSensorData) -> float | None:
    if _LR_MODEL is None: return None
    try:
        name = record.device.device_name
        process = record.machining_process or ""
        
        # 1. 判定是否为“有效切削阶段”
        # 只有在 Layer 1/2/3 进行切削时，我们才认为模型输出具有参考价值
        is_cutting = any(kw in process for kw in ['Layer', 'Cutting', 'Machining'])
        
        # 2. 如果是非切削阶段 (Prep, end, Repositioning, Idle)
        # 我们采取“数值保持”策略，返回上一次的平滑分值，不让它掉回 0
        if not is_cutting:
            return round(_SMOOTHED_PROBS.get(name, 0.01), 4)

        # 3. 正常切削时的推断逻辑
        sc, sp, fv = float(record.spindle_current), float(record.spindle_power), float(record.feed_velocity)
        ts = record.timestamp or datetime.now()

        # 滑动窗口特征构建
        if name not in _DEVICE_BUFFERS: _DEVICE_BUFFERS[name] = deque()
        buf = _DEVICE_BUFFERS[name]
        buf.append((ts, sc, sp, fv))
        while buf and buf[0][0] < ts - timedelta(seconds=_BUFFER_MAX_SECONDS):
            buf.popleft()

        # 构造 13 维特征
        sc_m5, sp_m5, fv_m5, sc_max5, fv_max5 = _compute_window_stats(buf, ts, 300)
        sc_m1, sp_m1, fv_m1, _, _ = _compute_window_stats(buf, ts, 60)
        sc_m10, sp_m10, _, _, _ = _compute_window_stats(buf, ts, 600)
        
        inter = sc_m5 * sp_m5
        eff5 = sp_m5 / (abs(sc_m5) + 0.01)
        eff10 = sp_m10 / (abs(sc_m10) + 0.01)

        x = np.array([[
            sc_m5, sp_m5, fv_m5, sc_max5, fv_max5,
            sc_m1, sp_m1, fv_m1, sc_m10, sp_m10,
            inter, eff5, eff10
        ]])
        
        # 模型推断
        x_s = _SCALER.transform(x)
        raw_p = float(_LR_MODEL.predict_proba(x_s)[0, 1])
        prob = _calibrate(raw_p)

        # EMA 更新 (仅在切削时更新趋势)
        last_p = _SMOOTHED_PROBS.get(name, prob)
        final_p = _PROB_EMA_ALPHA * prob + (1 - _PROB_EMA_ALPHA) * last_p
        _SMOOTHED_PROBS[name] = final_p

        return round(final_p, 4)
    except Exception as e:
        logger.warning(f"Inference Error: {e}")
        return None

def reset_device_buffer(device_name: str):
    """
    [V3.5.2] 强制清除设备的推断缓冲区与 EMA 状态。
    通常在 Copilot 执行修复（Recovery）操作后调用，确保分数能够立即归零。
    """
    if device_name in _SMOOTHED_RAW: del _SMOOTHED_RAW[device_name]
    if device_name in _SMOOTHED_PROBS: del _SMOOTHED_PROBS[device_name]
    if device_name in _DEVICE_BUFFERS: _DEVICE_BUFFERS[device_name].clear()
    logger.info(f"AI Buffer Reset for device: {device_name}")

def predict_is_anomaly(record: ProductionSensorData) -> tuple[bool, float | None]:
    p = predict_proba(record)
    return (p >= _THRESHOLD, p) if p is not None else (False, None)

def is_ai_ready() -> bool: return _LR_MODEL is not None

def get_current_smoothed_score(device_name: str) -> float | None:
    """
    返回指定设备当前的 EMA 平滑风险概率（与在线推断 _SMOOTHED_PROBS 同源）。
    用于设备详情页指标卡，确保与 Dashboard / 设备列表显示值一致。
    返回值为 [0, 1] 的浮点数，若该设备尚无推断记录则返回 None。
    """
    val = _SMOOTHED_PROBS.get(device_name)
    return round(float(val), 4) if val is not None else None
