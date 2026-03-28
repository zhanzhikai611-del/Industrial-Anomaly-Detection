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
        [V3.4.3 修复补丁]：联动清除 AI 缓冲区
        """
        try:
            # 1. 重置数据库物理组别
            DeviceInfo.initial_repair_all()
            
            # 2. 联动重置 AI 特征计算缓冲区 (解决重置后分数不降的问题)
            from .ai_service import reset_device_buffer
            devices = DeviceInfo.objects.all()
            for dev in devices:
                reset_device_buffer(dev.id)
            
            logger.info("Successfully reset all device groups and cleared AI buffers.")
            return True
        except Exception as e:
            logger.error(f"Reset groups error: {e}")
            return False
