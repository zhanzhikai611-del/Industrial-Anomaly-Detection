# -*- coding: utf-8 -*-
import logging
from pathlib import Path
import numpy as np
import joblib
from ..models import ProductionSensorData

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  AI 模型全局单例加载
# ═══════════════════════════════════════════════════════════════════

_ML_DIR = Path(__file__).resolve().parent.parent.parent / 'ml_models'

def _load_artifacts():
    """安全加载 LR 模型和 StandardScaler，文件不存在时返回 None。"""
    try:
        model      = joblib.load(_ML_DIR / 'logistic_model.pkl')
        scaler     = joblib.load(_ML_DIR / 'scaler.pkl')
        feat_names = joblib.load(_ML_DIR / 'feature_names.pkl')
        logger.info('[AI Service] 逻辑回归模型加载成功，特征维度=%d', len(feat_names))
        return model, scaler, feat_names
    except FileNotFoundError:
        logger.warning('[AI Service] ml_models/ 文件不存在，AI 推断功能已禁用')
        return None, None, None
    except Exception as exc:
        logger.error('[AI Service] 模型加载异常：%s', exc)
        return None, None, None

# 全局单例
_LR_MODEL, _SCALER, _FEAT_NAMES = _load_artifacts()

def predict_proba(record: ProductionSensorData) -> float | None:
    """
    单条记录在线推断。
    9 维聚合特征：[sc_mean, sp_mean, fv_mean, sc_max, sp_max, fv_max, sc_std, sp_std, fv_std]
    """
    if _LR_MODEL is None:
        return None
    try:
        sc = record.spindle_current
        sp = record.spindle_power
        fv = record.feed_velocity
        # 简单模拟 9 维特征（使用当前值填充 mean/max）
        x = np.array([[sc, sp, fv, sc, sp, fv, 0.0, 0.0, 0.0]])
        x_scaled = _SCALER.transform(x)
        prob = float(_LR_MODEL.predict_proba(x_scaled)[0, 1])
        return round(prob, 4)
    except Exception as exc:
        logger.warning('[AI Service] 推断异常：%s', exc)
        return None

def is_ai_ready() -> bool:
    """检查模型是否加载就绪"""
    return _LR_MODEL is not None
