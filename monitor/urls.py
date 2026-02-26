# -*- coding: utf-8 -*-
from django.urls import path
from django.views.generic import RedirectView
from . import views

urlpatterns = [
    # ── 根路径重定向 → Dashboard ────────────────────────────────
    path('', RedirectView.as_view(url='/dashboard/', permanent=False), name='root'),

    # ── 实时流开关 ──────────────────────────────────────────────
    path('api/start-stream/',  views.start_stream,  name='start_stream'),
    path('api/stop-stream/',   views.stop_stream,   name='stop_stream'),
    path('api/stream-status/', views.stream_status, name='stream_status'),
    path('api/system/toggle_stream/', views.api_toggle_stream, name='api_toggle_stream'),

    # ── SaaS 多页面路由 ──────────────────────────────────────────
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('devices/',   views.device_view,   name='device'),
    path('events/',    views.event_view,    name='event'),
    path('accounts/',  views.account_view,  name='account'),
    path('setting/',   views.setting_view,  name='setting'),

    # ── ECharts 数据接口 ─────────────────────────────────────────
    path('api/stats/',           views.api_dashboard_stats, name='api_stats'),
    path('api/stream/',          views.api_realtime_stream, name='api_stream'),
    path('api/alerts/',          views.api_latest_alerts,   name='api_alerts'),
    path('api/device-matrix/',             views.api_device_matrix,  name='api_device_matrix'),
    path('api/sensor-logs/',               views.api_sensor_logs,    name='api_sensor_logs'),
    path('api/stream/<int:device_id>/',    views.api_device_stream,  name='api_device_stream'),
    path('api/alerts/<int:alert_id>/handle/', views.api_handle_alert, name='api_handle_alert'),
    path('api/device/<int:device_id>/status/', views.api_update_device_status, name='api_update_device_status'),
    path('api/device/<int:device_id>/reset/', views.api_device_reset, name='api_device_reset'),
    path('api/accounts/create/', views.api_create_account, name='api_create_account'),
]
