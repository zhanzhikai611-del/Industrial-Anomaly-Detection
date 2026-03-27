# -*- coding: utf-8 -*-
"""
monitor/services/cache_service.py
Redis 缓存数据接入层 (V3.3.0)
提供以下核心能力：
1. 设备状态实时快照 (Hash)
2. 看板统计数据高频缓存 (60s)
3. 关键业务计数器 (Counter)
"""

import json
import logging
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

class CacheService:
    # Redis Key 定义
    KEY_DEVICE_SNAPSHOT = "device:status:all"
    KEY_DASHBOARD_STATS = "stats:dashboard:overview"
    KEY_DAILY_OUTPUT = "stats:output:today"
    
    TTL_DASHBOARD = 60  # 60s 缓存 (PRD 2.2)

    # ═══════════════════════════════════════════════════════════════════
    #  1. 设备状态快照 (Live State Snapshot)
    # ═══════════════════════════════════════════════════════════════════

    @classmethod
    def update_device_snapshot(cls, device_id, data):
        """
        更新 Redis 中对应设备的快照。
        使用 Hash 结构存储以免全量读写，device_id 作为 field。
        """
        try:
            # 这里的 data 通常是一个 dict，包含最新传感、状态、AI分数等
            # 序列化后存入 Redis
            cache.set_hash(cls.KEY_DEVICE_SNAPSHOT, str(device_id), json.dumps(data))
        except Exception as e:
            logger.error(f"[CacheService] Update snapshot failed: {str(e)}")

    @classmethod
    def get_all_device_snapshots(cls):
        """
        一次性获取所有设备的快照。
        """
        try:
            raw_data = cache.get_hash(cls.KEY_DEVICE_SNAPSHOT) or {}
            result = []
            for k, v in raw_data.items():
                try:
                    result.append(json.loads(v))
                except:
                    continue
            return result
        except Exception as e:
            logger.error(f"[CacheService] Get all snapshots failed: {str(e)}")
            return None # 触发回退数据库逻辑

    # ═══════════════════════════════════════════════════════════════════
    #  2. 看板聚合缓存 (Hourly Production Cache)
    # ═══════════════════════════════════════════════════════════════════

    @classmethod
    def get_dashboard_cache(cls):
        """尝试获取看板缓存数据"""
        return cache.get(cls.KEY_DASHBOARD_STATS)

    @classmethod
    def set_dashboard_cache(cls, data):
        """写入看板缓存数据，过期时间 60s"""
        try:
            cache.set(cls.KEY_DASHBOARD_STATS, data, timeout=cls.TTL_DASHBOARD)
        except Exception as e:
            logger.error(f"[CacheService] Set dashboard cache failed: {str(e)}")

    # ═══════════════════════════════════════════════════════════════════
    #  3. 实时产量计数器 (Real-time Calculation Decoupling)
    # ═══════════════════════════════════════════════════════════════════

    @classmethod
    @classmethod
    def incr_daily_output(cls, amount=1):
        """增加今日总产量计数 (V3.3.1 优化)"""
        try:
            # 改进：如果 key 不存在，先通过 get_daily_output 进行初始化同步
            if cache.get(cls.KEY_DAILY_OUTPUT) is None:
                cls.get_daily_output()
            cache.incr(cls.KEY_DAILY_OUTPUT, amount)
        except Exception as e:
            logger.error(f"[CacheService] Incr daily output failed: {str(e)}")

    @classmethod
    def set_daily_output(cls, value, timeout=None):
        """显式设置今日产量计数器值 (V3.3.1)"""
        try:
            cache.set(cls.KEY_DAILY_OUTPUT, int(value), timeout=timeout)
        except Exception as e:
            logger.error(f"[CacheService] Set daily output failed: {str(e)}")

    @classmethod
    def get_daily_output(cls, force_sync=False):
        """读取内存中的产量指标，支持 SQL 兜底初始化 (V3.3.1)"""
        try:
            val = cache.get(cls.KEY_DAILY_OUTPUT)
            
            # 如果强制同步，或者 Redis 值为 None，则查 DB
            if val is None or force_sync:
                from monitor.models import ProductionSensorData
                from django.db.models import Sum
                today = timezone.localtime(timezone.now()).replace(hour=0, minute=0, second=0, microsecond=0)
                db_sum = ProductionSensorData.objects.filter(timestamp__gte=today).aggregate(s=Sum('actual_output'))['s'] or 0
                
                # 记录同步日志以便调试 (V3.3.1)
                logger.info(f"[CacheService] Syncing Output Counter: Redis={val} -> DB={db_sum}")
                cache.set(cls.KEY_DAILY_OUTPUT, int(db_sum), timeout=None)
                return int(db_sum)
                
            return int(val)
        except Exception as e:
            logger.error(f"[CacheService] Get daily output failed: {str(e)}")
            return None

    @classmethod
    def reset_daily_output_task(cls):
        """(备用) 每日凌晨重置计数器"""
        cache.delete(cls.KEY_DAILY_OUTPUT)

# ── 扩展 django-redis 的 set_hash/get_hash (若 backend 支持) ────────────
# 注：django-redis 的 cache 对象可以通过 .client 访问原生的 Redis 客户端。
# 这里封装简单的工具方法，避免直接散落到业务代码中。

if not hasattr(cache, 'set_hash'):
    def set_hash(key, field, value):
        from django_redis import get_redis_connection
        conn = get_redis_connection("default")
        # [V3.3.0] django-redis 默认会添加 :1: 前缀，这里为了统一也加上
        # 如果 settings 中配置了 KEY_PREFIX，这里需要更复杂的逻辑，
        # 但在本项目缺省配置下，:1: 是常态。
        conn.hset(f":1:{key}", field, value)
    cache.set_hash = set_hash

if not hasattr(cache, 'get_hash'):
    def get_hash(key):
        from django_redis import get_redis_connection
        conn = get_redis_connection("default")
        raw = conn.hgetall(f":1:{key}")
        return {k.decode('utf-8'): v.decode('utf-8') for k, v in raw.items()}
    cache.get_hash = get_hash
