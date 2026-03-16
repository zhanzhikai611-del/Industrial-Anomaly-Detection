# -*- coding: utf-8 -*-
"""
monitor/context_processors.py
全局上下文处理器：向所有模板注入角色信息及 HTMX 渲染基座。
"""

def rbac_context(request):
    """
    向所有模板注入以下变量：
      user_role          — 当前用户角色字符串（'Admin' / 'Engineer' / 'Operator'）
      user_display_name  — 显示姓名（优先真实姓名，否则用户名）
      user_initial       — 头像首字母（用于 Avatar 占位）
      show_admin_nav     — 是否显示"账号管理"和"系统设置"导航项
      is_operator        — 是否为操作员（用于隐藏设备页处理按钮）
    """
    if not request.user.is_authenticated:
        return {
            'user_role': '',
            'user_display_name': '',
            'user_initial': '?',
            'show_admin_nav': False,
            'is_operator': False,
        }

    profile = getattr(request.user, 'profile', None)
    role = profile.role if profile else 'Operator'

    real_name = profile.real_name if (profile and profile.real_name) else ''
    display_name = real_name or request.user.username
    initial = display_name[0].upper() if display_name else '?'

    return {
        'user_role': role,
        'user_display_name': display_name,
        'user_initial': initial,
        'show_admin_nav': role == 'Admin',
        'is_operator': role == 'Operator',
    }

def htmx_context(request):
    """
    提供全局 context 变量，用于实现 HTMX 局部/全局渲染切换。
    """
    if getattr(request, 'is_htmx', False):
        return {'base_template': 'monitor/partial.html'}
    return {'base_template': 'monitor/base.html'}
