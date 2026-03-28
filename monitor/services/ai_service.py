# -*- coding: utf-8 -*-
"""
ai_service.py — V2 在线推断服务
从 Redis 滑动窗口中提取多尺度特征，对齐 train_model_v2.py 训练时的特征工程。

V2 特征 (10 维):
  5min 窗口: sc_mean, sp_mean, fv_mean, sc_max, fv_max
  1min 窗口: sc_mean, sp_mean, fv_mean
  交互特征: current_x_power, delta_velocity
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
#  模型全局单例加载
# ═══════════════════════════════════════════════════════════════════

_ML_DIR = Path(__file__).resolve().parent.parent.parent / 'ml_models'


def _load_artifacts():
    """加载 V2 模型、标准化器、特征名和阈值"""
    try:
        model      = joblib.load(_ML_DIR / 'logistic_model.pkl')
        scaler     = joblib.load(_ML_DIR / 'scaler.pkl')
        feat_names = joblib.load(_ML_DIR / 'feature_names.pkl')
        # 尝试加载最优阈值（V2 新增）
        try:
            threshold = joblib.load(_ML_DIR / 'optimal_threshold.pkl')
        except FileNotFoundError:
            threshold = 0.5
        logger.info('[AI Service] V2 模型加载成功，特征维度=%d，阈值=%.4f',
                     len(feat_names), threshold)
        return model, scaler, feat_names, threshold
    except FileNotFoundError:
        logger.warning('[AI Service] ml_models/ 文件不存在，AI 推断功能已禁用')
        return None, None, None, 0.5
    except Exception as exc:
        logger.error('[AI Service] 模型加载异常：%s', exc)
        return None, None, None, 0.5


_LR_MODEL, _SCALER, _FEAT_NAMES, _THRESHOLD = _load_artifacts()


# ═══════════════════════════════════════════════════════════════════
#  设备级滑动窗口缓冲区（内存中维护最近 5 分钟的传感器数据）
# ═══════════════════════════════════════════════════════════════════

# 结构: {device_id: deque([(timestamp, sc, sp, fv), ...])}
_DEVICE_BUFFERS: dict[int, deque] = {}
_BUFFER_MAX_SECONDS = 300  # 5 分钟


def _get_buffer(device_id: int) -> deque:
    if device_id not in _DEVICE_BUFFERS:
        _DEVICE_BUFFERS[device_id] = deque()
    return _DEVICE_BUFFERS[device_id]


def _push_and_trim(device_id: int, ts: datetime, sc: float, sp: float, fv: float):
    """将新数据点推入缓冲区，并清除超过 5 分钟的旧数据"""
    buf = _get_buffer(device_id)
    buf.append((ts, sc, sp, fv))
    # 清除 > 5min 的旧数据
    cutoff = ts - timedelta(seconds=_BUFFER_MAX_SECONDS)
    while buf and buf[0][0] < cutoff:
        buf.popleft()


def _compute_window_stats(buf: deque, ts: datetime, window_secs: int):
    """
    从缓冲区中提取指定时间窗口内的统计量。
    返回: (sc_mean, sp_mean, fv_mean, sc_max, fv_max)
    """
    cutoff = ts - timedelta(seconds=window_secs)
    sc_vals, sp_vals, fv_vals = [], [], []
    for t, sc, sp, fv in buf:
        if t >= cutoff:
            sc_vals.append(sc)
            sp_vals.append(sp)
            fv_vals.append(fv)

    if not sc_vals:
        return 0.0, 0.0, 0.0, 0.0, 0.0

    return (
        np.mean(sc_vals),   # sc_mean
        np.mean(sp_vals),   # sp_mean
        np.mean(fv_vals),   # fv_mean
        np.max(sc_vals),    # sc_max
        np.max(fv_vals),    # fv_max
    )


def _build_v2_features(device_id: int, ts: datetime, sc: float, sp: float, fv: float) -> np.ndarray:
    """
    构造 V2 的 10 维特征向量，对齐 train_model_v2.py 中的 rolling_aggregate_v2()。

    特征顺序:
      0: spindle_current_mean_5min
      1: spindle_power_mean_5min
      2: feed_velocity_mean_5min
      3: spindle_current_max_5min
      4: feed_velocity_max_5min
      5: spindle_current_mean_1min
      6: spindle_power_mean_1min
      7: feed_velocity_mean_1min
      8: interaction_current_x_power   (sc_mean_5min × sp_mean_5min)
      9: delta_velocity_5m_1m          (fv_mean_5min - fv_mean_1min)
    """
    # 推入新数据
    _push_and_trim(device_id, ts, sc, sp, fv)
    buf = _get_buffer(device_id)

    # 5 分钟窗口统计
    sc_mean_5, sp_mean_5, fv_mean_5, sc_max_5, fv_max_5 = \
        _compute_window_stats(buf, ts, 300)

    # 1 分钟窗口统计
    sc_mean_1, sp_mean_1, fv_mean_1, _, _ = \
        _compute_window_stats(buf, ts, 60)

    # 交互特征
    interaction = sc_mean_5 * sp_mean_5
    delta_vel   = fv_mean_5 - fv_mean_1

    return np.array([[
        sc_mean_5, sp_mean_5, fv_mean_5,
        sc_max_5, fv_max_5,
        sc_mean_1, sp_mean_1, fv_mean_1,
        interaction, delta_vel,
    ]])


_LOGIT_SCALE = 6.0   # 减小缩放因子，让 sigmoid 敏感区更宽
_LOGIT_BIAS = 1.5    # 引入偏置，平移概率中心点


def _calibrate_probability(raw_prob: float) -> float:
    """
    高级概率校准：实现 0% - 100% 全量程覆盖。
    
    逻辑：
      1. 将饱和的 raw_prob 转回 logit 空间。
      2. 对 logit 进行线性重缩放和偏置。
      3. 重新映射到 sigmoid 空间，实现平滑且具区分度的连续谱。
    """
    p = np.clip(raw_prob, 1e-9, 1 - 1e-9)
    logit = np.log(p / (1 - p))
    # 动态映射：健康机器 logit 更负，加上偏置后依然在 0% 附近产生差异
    # 故障机器 logit 正值极大，缩放后能稳步达到 95% 以上
    calibrated = 1.0 / (1.0 + np.exp(-(logit + _LOGIT_BIAS) / _LOGIT_SCALE))
    return float(np.round(calibrated, 4))


# ═══════════════════════════════════════════════════════════════════
#  公开 API
# ═══════════════════════════════════════════════════════════════════

def reset_device_buffer(device_id: int):
    """
    重置指定设备的特征缓冲区。
    通常在设备维修完成、更换刀具或系统冷启动时调用，
    以消除旧数据的“物理指标惯性”，让 AI 分数立即恢复正常。
    """
    if device_id in _DEVICE_BUFFERS:
        _DEVICE_BUFFERS[device_id].clear()
        print(f"♻️  AI Buffer Reset: Device {device_id}")


def predict_proba(record: ProductionSensorData) -> float | None:
    """
    V2 单条记录在线推断。
    使用设备级滑动窗口构造 10 维多尺度特征，对齐训练时的特征工程。
    输出经 Temperature Scaling 校准的平滑概率。
    """
    if _LR_MODEL is None:
        return None
    try:
        sc = float(record.spindle_current)
        sp = float(record.spindle_power)
        fv = float(record.feed_velocity)
        ts = record.timestamp or datetime.now()
        device_id = record.device_id

        x = _build_v2_features(device_id, ts, sc, sp, fv)
        x_scaled = _SCALER.transform(x)
        raw_prob = float(_LR_MODEL.predict_proba(x_scaled)[0, 1])
        prob = _calibrate_probability(raw_prob)
        return round(prob, 4)
    except Exception as exc:
        logger.warning('[AI Service] V2 推断异常：%s', exc)
        return None


def predict_is_anomaly(record: ProductionSensorData) -> tuple[bool, float | None]:
    """
    V2 异常判定（使用动态阈值）。
    返回: (is_anomaly, probability)
    """
    prob = predict_proba(record)
    if prob is None:
        return False, None
    return prob >= _THRESHOLD, prob


def is_ai_ready() -> bool:
    """检查模型是否加载就绪"""
    return _LR_MODEL is not None
