# -*- coding: utf-8 -*-
import logging
from ..models import SystemConfig, DeviceInfo

logger = logging.getLogger(__name__)

class DeviceService:
    """
    设备控制业务服务 (V3.1.0)
    """

    @staticmethod
    def toggle_realtime_stream(active: bool) -> bool:
        """
        切换系统全局实时数据模拟流开关
        """
        try:
            cfg = SystemConfig.get()
            cfg.is_realtime_active = active
            cfg.save()
            return True
        except Exception as e:
            logger.error(f"Toggle stream error: {e}")
            return False

    @staticmethod
    def reset_all_device_groups():
        """
        全量重置所有设备的特征组梯度 (模拟换刀/新工艺开始)
        """
        try:
            # 批量操作性能更高
            DeviceInfo.objects.all().update(current_group_id=1)
            return True
        except Exception as e:
            logger.error(f"Reset groups error: {e}")
            return False
