# -*- coding: utf-8 -*-
"""
monitor/models.py
工业异常预警系统 —— 核心数据模型（v3，基于 CNC 铣床数据集重构）

数据来源参考：
  University of Michigan CNC 铣床刀具磨损数据集
  (Kaggle: tool-wear-detection-in-cnc-mill)

三张核心表：
  1. DeviceInfo            —— 设备基础信息维（OEE 标准产能基准）
  2. ProductionSensorData  —— CNC 生产传感综合流水维（ML 特征 X + 刀具磨损标签 Y）
  3. AnomalyAlertLog       —— 异常预警记录维（物理阈值 / ML 模型双触发）
"""

from django.db import models
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.contrib.auth.models import User


# ═══════════════════════════════════════════════════════════════════
#  表 1：设备基础信息表
# ═══════════════════════════════════════════════════════════════════

class DeviceInfo(models.Model):
    """
    设备基础信息表 (device_info)
    管理车间内 CNC 机床等物理资产的静态属性，是计算 OEE 性能稼动率的基准来源。
    """

    STATUS_CHOICES = [
        ('Running', '运行'),
        ('Idle',    '待机'),
        ('Down',    '停机'),
    ]

    device_name = models.CharField(
        max_length=50,
        verbose_name='设备名称',
        help_text='如 "CNC-高光机-001"，用于前端列表展示',
    )
    device_type = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        verbose_name='设备型号',
        help_text='区分不同加工精度的机床，可为空',
    )
    standard_capacity = models.IntegerField(
        verbose_name='标准产能',
        help_text='单位时间内理论最大产出数，用于计算 OEE 性能稼动率',
    )
    current_status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='Idle',
        verbose_name='运行状态',
        help_text='Running=运行 | Idle=待机 | Down=停机，用于大屏状态指示灯',
    )
    current_group_id = models.IntegerField(
        default=1, 
        verbose_name='当前特征组 ID', 
        help_text='取值 1~5，由重置逻辑变更为 2(准新) 实现回春'
    )

    class Meta:
        db_table = 'device_info'
        verbose_name = '设备信息'
        verbose_name_plural = '设备信息'
        ordering = ['id']

    def __str__(self):
        return f'[{self.id}] {self.device_name} ({self.get_current_status_display()})'

class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('Admin', 'Admin'),
        ('Engineer', 'Engineer'),
        ('Operator', 'Operator'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile', verbose_name='关联系统用户')
    real_name = models.CharField(max_length=50, verbose_name='真实姓名')
    job_number = models.CharField(max_length=50, unique=True, verbose_name='工号')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='Operator', verbose_name='系统角色')

    class Meta:
        db_table = 'user_profile'


# ═══════════════════════════════════════════════════════════════════
#  表 2：生产与传感综合流水表（CNC 版）
# ═══════════════════════════════════════════════════════════════════

class ProductionSensorData(models.Model):
    """
    生产与传感综合流水表 (production_sensor_data)
    系统核心业务表，融合 CNC 铣床真实电控传感参数与宏观生产管理指标。

    ML 映射关系（基于 CNC 刀具磨损数据集）：
      特征 X1：spindle_current（主轴反馈电流，对应 S1_CurrentFeedback）
      特征 X2：spindle_power（主轴输出功率，对应 S1_OutputPower）
      特征 X3：feed_velocity（X轴实际进给速度，对应 X1_ActualVelocity）
      数据筛选：machining_process 为切削状态（如 "Layer 1 Up"）时的数据才参与训练
      标签 Y：tool_condition（0=未磨损 Unworn，1=已磨损 Worn）

    OEE 计算字段（标准 SEMI E10 公式）：
      时间稼动率 = (loading_time - downtime) / loading_time
      性能稼动率 = actual_output / (实际运转时间(h) × standard_capacity)
                  其中：实际运转时间 = (loading_time - downtime) / 60
      质量合格率 = actual_output / input_qty
      OEE = 时间稼动率 × 性能稼动率 × 质量合格率
    """

    MACHINING_PROCESS_CHOICES = [
        ('Prep',        '准备阶段'),
        ('Layer 1 Up',  '第1层切削-上行'),
        ('Layer 1 Down','第1层切削-下行'),
        ('Layer 2 Up',  '第2层切削-上行'),
        ('Layer 2 Down','第2层切削-下行'),
        ('Layer 3 Up',  '第3层切削-上行'),
        ('Layer 3 Down','第3层切削-下行'),
        ('end',         '结束'),
    ]

    device = models.ForeignKey(
        DeviceInfo,
        on_delete=models.CASCADE,
        verbose_name='关联设备',
        related_name='sensor_records',
        help_text='数据来源的 CNC 设备',
    )
    timestamp = models.DateTimeField(
        default=timezone.now,
        verbose_name='采集时间',
        help_text='数据采集时间，用于 ECharts 时序波形图渲染',
        db_index=True,
    )

    # ── CNC 传感器特征（ML 模型输入 X，来自 Kaggle 数据集）─────────
    spindle_current = models.FloatField(
        verbose_name='主轴反馈电流 (A)',
        help_text='对应 Kaggle: S1_CurrentFeedback | ML 特征 X1 | 刀具磨损时切削阻力增大，电流升高',
    )
    spindle_power = models.FloatField(
        verbose_name='主轴输出功率 (W)',
        help_text='对应 Kaggle: S1_OutputPower | ML 特征 X2',
    )
    feed_velocity = models.FloatField(
        verbose_name='X轴实际进给速度 (mm/min)',
        help_text='对应 Kaggle: X1_ActualVelocity | ML 特征 X3 | 反映切削过程动态稳定性',
    )
    machining_process = models.CharField(
        max_length=50,
        choices=MACHINING_PROCESS_CHOICES,
        default='Layer 1 Up',
        verbose_name='加工阶段',
        help_text='对应 Kaggle: Machining_Process | 用于 ML 数据清洗，剔除 Prep/end 等空转期干扰',
        db_index=True,
    )

    # ── ML 监督学习标签 Y（刀具磨损状态）───────────────────────────
    tool_condition = models.BooleanField(
        default=False,
        verbose_name='刀具磨损状态 (Y)',
        help_text='对应 Kaggle: tool_condition | False=未磨损(Unworn) / True=已磨损(Worn) | 逻辑回归预测目标',
        db_index=True,
    )

    # ── OEE 管理字段（业务自定义扩展）──────────────────────────────
    loading_time = models.FloatField(
        verbose_name='负荷时间 (分钟)',
        help_text='设备计划运行总时间，用于计算 OEE 时间稼动率',
    )
    downtime = models.FloatField(
        default=0.0,
        verbose_name='停机时间 (分钟)',
        help_text='负荷时间内因异常停机的时间，用于计算 OEE 时间稼动率',
    )
    input_qty = models.IntegerField(
        verbose_name='投入数量',
        help_text='该周期投入生产的物料总数，合格率计算分母',
    )
    actual_output = models.IntegerField(
        default=0,
        verbose_name='实际产量',
        help_text='实际产出数量，用于大屏产量看板及 OEE 性能稼动率计算',
    )

    class Meta:
        db_table = 'production_sensor_data'
        verbose_name = '生产传感流水'
        verbose_name_plural = '生产传感流水'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['device', 'timestamp']),
            models.Index(fields=['machining_process', 'tool_condition']),
        ]

    # ── OEE 计算属性（只读，不落库）────────────────────────────────
    @property
    def availability(self):
        """时间稼动率 = (loading_time - downtime) / loading_time"""
        if self.loading_time and self.loading_time > 0:
            return round((self.loading_time - self.downtime) / self.loading_time, 4)
        return None

    @property
    def performance(self):
        """
        性能稼动率 = 实际产量 / 理论最大产量
        理论最大产量 = 实际运转时间(h) × standard_capacity
        实际运转时间 = (loading_time - downtime) / 60

        ⚠ 分母用「实际运转时间」而非「计划时间」，
          避免停机损失被 Availability 和 Performance 双重扣减。
        """
        try:
            cap = self.device.standard_capacity
            actual_run = self.loading_time - self.downtime   # 分钟
            if cap and actual_run and actual_run > 0:
                expected = (actual_run / 60.0) * cap         # 件
                return round(self.actual_output / expected, 4)
        except Exception:
            pass
        return None

    @property
    def quality(self):
        """
        良品率 = actual_output / input_qty
        标准公式：(实际产量 - 不良品数) / 实际产量。
        本模型将 input_qty 视为「总投料数」，actual_output 为「良品产出数」，
        差额 (input_qty - actual_output) 为因停机/磨损损耗的不良/报废数。
        - 正常工况：actual_output = input_qty → 100%
        - 磨损停机：actual_output < input_qty → 合理下降（止损比例≈downtime/loading_time）
        """
        if self.input_qty and self.input_qty > 0:
            return round(min(self.actual_output, self.input_qty) / self.input_qty, 4)
        return None

    @property
    def oee(self):
        """OEE = 时间稼动率 × 性能稼动率 × 质量合格率"""
        a, p, q = self.availability, self.performance, self.quality
        if a is not None and p is not None and q is not None:
            return round(a * p * q, 4)
        return None

    def __str__(self):
        worn = '⚠️磨损' if self.tool_condition else '✓正常'
        return (f'[{self.device.device_name}] {self.timestamp:%Y-%m-%d %H:%M} '
                f'电流={self.spindle_current:.2f}A  功率={self.spindle_power:.1f}W  '
                f'刀具={worn}')


# ═══════════════════════════════════════════════════════════════════
#  表 3：异常预警记录表
# ═══════════════════════════════════════════════════════════════════

class AnomalyAlertLog(models.Model):
    """
    异常预警记录表 (anomaly_alert_log)
    记录由物理阈值或 LR 刀具磨损模型触发的报警，支撑闭环管理与大屏展示。
    """

    ALERT_TYPE_CHOICES = [
        ('LR_TOOL_WEAR',   'LR 模型刀具磨损预警'),
        ('HIGH_CURRENT',   '主轴过电流物理报警'),
        ('HIGH_POWER',     '主轴过载物理报警'),
        ('LOW_VELOCITY',   '进给速度骤降报警'),
        ('DOWNTIME',       '停机超时报警'),
        ('OTHER',          '其他'),
    ]

    record = models.ForeignKey(
        ProductionSensorData,
        on_delete=models.CASCADE,
        verbose_name='关联流水记录',
        related_name='alerts',
        help_text='触发报警时的具体电流、功率等现场数据来源',
    )
    alert_time = models.DateTimeField(
        default=timezone.now,
        verbose_name='预警触发时间',
        db_index=True,
    )
    alert_type = models.CharField(
        max_length=50,
        choices=ALERT_TYPE_CHOICES,
        default='OTHER',
        verbose_name='报警类型',
        help_text='区分报警来源，用于前端分布图表（饼图/雷达图）分类',
        db_index=True,
    )
    anomaly_score = models.FloatField(
        null=True,
        blank=True,
        verbose_name='异常置信度',
        help_text='LR 模型输出的"刀具磨损概率"（0~1），超过 0.75 触发预警',
    )
    is_handled = models.BooleanField(
        default=False,
        verbose_name='是否已处理',
        help_text='False=未处理（大屏红色闪烁）| True=已处理（闭环完成）',
        db_index=True,
    )

    class Meta:
        db_table = 'anomaly_alert_log'
        verbose_name = '异常预警记录'
        verbose_name_plural = '异常预警记录'
        ordering = ['-alert_time']
        indexes = [
            models.Index(fields=['alert_type', 'is_handled', 'alert_time']),
        ]

    def __str__(self):
        score_str = f'  置信度={self.anomaly_score:.1%}' if self.anomaly_score else ''
        status = '✅ 已处理' if self.is_handled else '🔔 未处理'
        return (f'[{self.get_alert_type_display()}] '
                f'{self.alert_time:%Y-%m-%d %H:%M}{score_str}  {status}')


# ═══════════════════════════════════════════════════════════════════
#  表 4：系统配置开关表（单例）
# ═══════════════════════════════════════════════════════════════════

class SystemConfig(models.Model):
    """
    系统全局配置开关（单例表，始终只有一条记录 pk=1）。
    is_realtime_active: 控制实时数据流守护进程的启/停。
      True  → run_realtime_stream 每 3 秒为每台设备追加一条数据
      False → 守护进程挂起，不写入任何数据
    """
    is_realtime_active = models.BooleanField(
        default=False,
        verbose_name='实时数据流开关',
        help_text='True=开启实时模拟写入 | False=暂停（不删除历史数据）',
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='最近更新时间',
    )
    ai_alert_threshold = models.FloatField(default=0.75, verbose_name='AI 置信度高报阈值')
    spindle_current_high = models.FloatField(default=50.0, verbose_name='主轴电流高报阈值(A)')
    spindle_power_high = models.FloatField(default=120.0, verbose_name='主轴功率高报阈值(W)')
    feed_velocity_low = models.FloatField(default=0.5, verbose_name='进给速度低报阈值(mm/min)')

    class Meta:
        db_table  = 'system_config'
        verbose_name = '系统配置'
        verbose_name_plural = '系统配置'

    def save(self, *args, **kwargs):
        """单例模式：强制 pk=1，确保全系统只有一条配置记录"""
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        """获取（或自动创建）唯一配置实例"""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        status = '▶ 运行中' if self.is_realtime_active else '⏸ 已暂停'
        return f'实时数据流：{status}'

