"""
monitor/admin.py
Django Admin 后台注册（v3，对应 CNC 版三张模型）
"""

from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from .models import DeviceInfo, ProductionSensorData, AnomalyAlertLog, SystemConfig


# ── SystemConfig（单例开关）─────────────────────────────────────────

@admin.register(SystemConfig)
class SystemConfigAdmin(admin.ModelAdmin):
    list_display  = ('__str__', 'is_realtime_active', 'updated_at')
    readonly_fields = ('updated_at',)

    def has_add_permission(self, request):
        """单例：已存在记录则禁止新增"""
        return not SystemConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False  # 禁止删除唯一配置记录




# ── DeviceInfo ─────────────────────────────────────────────────────

@admin.register(DeviceInfo)
class DeviceInfoAdmin(admin.ModelAdmin):
    list_display  = ('id', 'device_name', 'device_type', 'standard_capacity', 'status_badge')
    list_filter   = ('current_status',)
    search_fields = ('device_name', 'device_type')
    list_per_page = 20

    STATUS_COLORS = {
        'Running': '#28a745',
        'Idle':    '#ffc107',
        'Down':    '#dc3545',
    }

    @admin.display(description='运行状态', ordering='current_status')
    def status_badge(self, obj):
        color = self.STATUS_COLORS.get(obj.current_status, '#6c757d')
        return format_html(
            '<span style="background:{};color:#fff;padding:3px 10px;'
            'border-radius:12px;font-size:12px;font-weight:bold">{}</span>',
            color, obj.get_current_status_display()
        )


# ── ProductionSensorData ───────────────────────────────────────────

@admin.register(ProductionSensorData)
class ProductionSensorDataAdmin(admin.ModelAdmin):
    list_display  = ('id', 'device', 'timestamp', 'spindle_current', 'spindle_power',
                     'feed_velocity', 'machining_process', 'tool_badge',
                     'oee_display', 'input_qty', 'actual_output', 'loading_time', 'downtime')
    list_filter   = ('device', 'machining_process', 'tool_condition')
    search_fields = ('device__device_name', 'machining_process')
    readonly_fields = ('availability_display', 'performance_display',
                       'quality_display', 'oee_display')
    list_per_page = 30
    ordering      = ('-timestamp',)

    fieldsets = (
        ('设备与采集时间', {
            'fields': ('device', 'timestamp'),
        }),
        ('CNC 传感器特征（ML 输入 X）', {
            'fields': ('spindle_current', 'spindle_power', 'feed_velocity', 'machining_process'),
        }),
        ('刀具状态（ML 标签 Y）', {
            'fields': ('tool_condition',),
        }),
        ('OEE 生产管理字段', {
            'fields': ('loading_time', 'downtime', 'input_qty', 'actual_output'),
        }),
        ('OEE 计算结果（只读）', {
            'classes': ('collapse',),
            'fields': ('availability_display', 'performance_display',
                       'quality_display', 'oee_display'),
        }),
    )

    @admin.display(description='刀具状态 Y', ordering='tool_condition')
    def tool_badge(self, obj):
        if obj.tool_condition:
            return format_html(
                '<span style="color:#dc3545;font-weight:bold">⚠ 已磨损</span>'
                '<span style="display:none">{}</span>', ''
            )
        return mark_safe('<span style="color:#28a745">✓ 正常</span>')

    @admin.display(description='OEE')
    def oee_display(self, obj):
        v = obj.oee
        if v is None:
            return '—'
        color = '#28a745' if v >= 0.85 else ('#ffc107' if v >= 0.6 else '#dc3545')
        pct = f'{v:.1%}'
        return format_html('<b style="color:{}">{}</b>', color, pct)

    @admin.display(description='时间稼动率')
    def availability_display(self, obj):
        v = obj.availability
        return f'{v:.1%}' if v is not None else '—'

    @admin.display(description='性能稼动率')
    def performance_display(self, obj):
        v = obj.performance
        return f'{v:.1%}' if v is not None else '—'

    @admin.display(description='质量合格率')
    def quality_display(self, obj):
        v = obj.quality
        return f'{v:.1%}' if v is not None else '—'


# ── AnomalyAlertLog ────────────────────────────────────────────────

@admin.register(AnomalyAlertLog)
class AnomalyAlertLogAdmin(admin.ModelAdmin):
    list_display  = ('id', 'alert_type_badge', 'record', 'alert_time',
                     'score_display', 'is_handled')
    list_filter   = ('alert_type', 'is_handled')
    search_fields = ('record__device__device_name', 'alert_type')
    readonly_fields = ('alert_time',)
    list_per_page = 30
    ordering      = ('-alert_time',)

    TYPE_COLORS = {
        'LR_TOOL_WEAR':  '#7b2d8b',
        'HIGH_CURRENT':  '#dc3545',
        'HIGH_POWER':    '#fd7e14',
        'LOW_VELOCITY':  '#ffc107',
        'DOWNTIME':      '#17a2b8',
        'OTHER':         '#6c757d',
    }

    @admin.display(description='报警类型', ordering='alert_type')
    def alert_type_badge(self, obj):
        color = self.TYPE_COLORS.get(obj.alert_type, '#6c757d')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:4px;font-size:12px">{}</span>',
            color, obj.get_alert_type_display()
        )

    @admin.display(description='异常置信度', ordering='anomaly_score')
    def score_display(self, obj):
        if obj.anomaly_score is None:
            return '—'
        color = '#dc3545' if obj.anomaly_score >= 0.7 else '#ffc107'
        pct = f'{obj.anomaly_score:.1%}'
        return format_html('<b style="color:{}">{}</b>', color, pct)

    actions = ['mark_handled']

    @admin.action(description='✅ 标记选中报警为已处理')
    def mark_handled(self, request, queryset):
        updated = queryset.filter(is_handled=False).update(is_handled=True)
        self.message_user(request, f'已将 {updated} 条报警标记为处理完成。')
