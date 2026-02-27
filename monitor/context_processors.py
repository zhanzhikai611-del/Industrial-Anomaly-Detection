# -*- coding: utf-8 -*-
"""
monitor/context_processors.py
全局 RBAC 上下文处理器：向所有模板注入角色信息，
使侧边栏导航和功能按钮能够按角色动态显隐。
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
