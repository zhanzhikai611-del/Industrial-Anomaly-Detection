# -*- coding: utf-8 -*-
"""
IndustrialWarningSystem/__init__.py
在项目启动时用 pymysql 替换 MySQLdb，使 Django 能够连接 MySQL 数据库

Django 6.x 的 mysql backend 会检测 mysqlclient 版本 >= 2.2.1，
通过覆写 pymysql.version_info 绕过此检查（pymysql 本身功能完全兼容）。
"""
import pymysql

# 伪装版本号，绕过 Django 6 对 mysqlclient 2.2.1+ 的强制要求
pymysql.version_info = (2, 2, 1, "final", 0)

# 让 pymysql 伪装成 MySQLdb
pymysql.install_as_MySQLdb()
