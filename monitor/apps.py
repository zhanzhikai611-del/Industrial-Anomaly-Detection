# -*- coding: utf-8 -*-
"""
monitor/apps.py

AppConfig：Django 应用就绪回调。

启动时自检 current_group_id 梯度分布：
  - 若最高 group_id ≤ 2（说明从未正确初始化，或所有设备都被回春操作覆写），
    则按 id 升序位置强制恢复 1-5 分组，每组 5 台。
  - 符合 V1.2.0 RRD 第 67 条设计意图：
    "项目重启后初始化脚本将重置所有设备的组别配置"

分组对照：
  idx  0- 4 → Group 1 (新设备    · sc≈15A · 低风险   0-40%)
  idx  5- 9 → Group 2 (准新设备  · sc≈24A · 低中风险 30-50%)
  idx 10-14 → Group 3 (正常磨损  · sc≈27A · 中风险   41-75%)
  idx 15-19 → Group 4 (老旧设备  · sc≈31A · 中高风险 60-85%)
  idx 20-24 → Group 5 (故障边缘  · sc≈40A · 高风险   76-100%)
"""

import logging
from django.apps import AppConfig

logger = logging.getLogger(__name__)


class MonitorConfig(AppConfig):
    name = 'monitor'

    def ready(self):
        # 使用 post_migrate 信号避免 "Accessing the database during app
        # initialization is discouraged" RuntimeWarning，同时在普通启动时
        # 通过 connection_created 信号完成首次连接后的修复。
        from django.db.models.signals import post_migrate
        from django.db.backends.signals import connection_created

        post_migrate.connect(_repair_device_groups_signal, sender=self)
        connection_created.connect(_repair_on_first_connection, sender=None)


# ─── 信号处理器 ──────────────────────────────────────────────────────────────

def _repair_device_groups_signal(sender, **kwargs):
    """post_migrate 信号触发：每次 manage.py migrate 后自动修复分组。"""
    _repair_device_groups()


def _repair_on_first_connection(sender, connection, **kwargs):
    """
    首次数据库连接建立时触发（适用于 runserver / gunicorn 等正常启动）。
    通过断开自身注册实现「只执行一次」语义，避免每条 SQL 都触发修复。
    """
    from django.db.backends.signals import connection_created
    connection_created.disconnect(_repair_on_first_connection)
    try:
        _repair_device_groups()
    except Exception as exc:
        logger.debug('[MonitorConfig] startup group repair skipped: %s', exc)


# ─── 核心修复逻辑 ─────────────────────────────────────────────────────────────

def _repair_device_groups():
    """
    每次项目启动时无条件将所有设备的 current_group_id 重置为初始梯度分布。

    映射规则（按 id 升序位置）：
      idx  0- 4 → Group 1 (新设备    · sc≈15A · 低风险   0-40%)
      idx  5-10 → Group 2 (准新设备  · sc≈24A · 低中风险 30-50%)
      idx 11-17 → Group 3 (正常磨损  · sc≈27A · 中风险   41-75%)
      idx 18-22 → Group 4 (老旧设备  · sc≈31A · 中高风险 60-85%)
      idx 23-24 → Group 5 (故障边缘  · sc≈40A · 高风险   76-100%)

    设计意图（V1.2.0 RRD §2.5 第 67 条）：
      "项目重启后初始化脚本将重置所有设备的组别配置"
      ——回春效果仅在当次运行期间有效，重启即恢复初始梯度，实现每次启动都是一次新的演示。
    """
    from .models import DeviceInfo

    devices = list(DeviceInfo.objects.order_by('id'))
    if len(devices) < 5:
        return

    to_update = []
    for idx, dev in enumerate(devices):
        if idx < 5:    expected = 1
        elif idx < 11: expected = 2
        elif idx < 18: expected = 3
        elif idx < 23: expected = 4
        else:          expected = 5
        
        # 联动重置 (V2.2.0): 重启时清空工单文本
        dev.maintenance_advice = '设备运行平稳，暂无维修建议。'
        dev.current_group_id = expected
        to_update.append(dev)

    if to_update:
        DeviceInfo.objects.bulk_update(to_update, ['current_group_id', 'maintenance_advice'])
        logger.info(
            '[MonitorConfig] startup group reset: %d devices restored to gradient 1-5 and advice cleared.',
            len(to_update),
        )
