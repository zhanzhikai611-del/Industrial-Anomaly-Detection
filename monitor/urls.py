from django.urls import path
from . import views

urlpatterns = [
    # ── 实时流开关 ──────────────────────────────────────────────
    path('api/start-stream/',  views.start_stream,  name='start_stream'),
    path('api/stop-stream/',   views.stop_stream,   name='stop_stream'),
    path('api/stream-status/', views.stream_status, name='stream_status'),

    # ── SaaS 多页面路由 ──────────────────────────────────────────
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('devices/',   views.device_view,   name='device'),
    path('events/',    views.event_view,    name='event'),
    path('setting/',   views.setting_view,  name='setting'),

    # ── ECharts 数据接口 ─────────────────────────────────────────
    path('api/stats/',           views.api_dashboard_stats, name='api_stats'),
    path('api/stream/',          views.api_realtime_stream, name='api_stream'),
    path('api/alerts/',          views.api_latest_alerts,   name='api_alerts'),
    path('api/device-matrix/',             views.api_device_matrix,  name='api_device_matrix'),
    path('api/sensor-logs/',               views.api_sensor_logs,    name='api_sensor_logs'),
    path('api/stream/<int:device_id>/',    views.api_device_stream,  name='api_device_stream'),
    path('api/alerts/<int:alert_id>/handle/', views.api_handle_alert, name='api_handle_alert'),
]
