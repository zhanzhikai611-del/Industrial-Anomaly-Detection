# -*- coding: utf-8 -*-
import os
import django
from django.urls import resolve

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "IndustrialWarningSystem.settings")
django.setup()

try:
    match = resolve('/accounts/')
    print("Resolved:", match.view_name)
except Exception as e:
    print("Error:", str(e))
