# -*- coding: utf-8 -*-
import logging
from ..models import AnomalyAlertLog

logger = logging.getLogger(__name__)

class AlertService:
    """
    报警业务服务 (V3.1.0)
    """

    @staticmethod
    def handle_alert(alert_id: int) -> bool:
        """
        将指定报警标记为已处理
        """
        try:
            alert = AnomalyAlertLog.objects.get(id=alert_id)
            alert.is_handled = True
            alert.save()
            return True
        except AnomalyAlertLog.DoesNotExist:
            return False
        except Exception as e:
            logger.error(f"Handle alert error: {e}")
            return False

    @staticmethod
    def get_formatted_alerts(limit=7):
        """
        获取最近报警并预处理 UI 字段 (risk_pct, risk_color)
        """
        alerts = (AnomalyAlertLog.objects
                 .select_related('record', 'record__device')
                 .order_by('-alert_time')[:limit])
        
        for alert in alerts:
            score = alert.anomaly_score or 0.75
            alert.risk_pct = int(score * 100)
            if score > 0.8:
                alert.risk_color = "#F5222D" # 危险
            elif score > 0.6:
                alert.risk_color = "#FAAD14" # 警告
            else:
                alert.risk_color = "#52C41A" # 正常
        
        return alerts

    @staticmethod
    def get_unhandled_alerts_count() -> int:
        """
        获取未处理报警的总数
        """
        return AnomalyAlertLog.objects.filter(is_handled=False).count()
