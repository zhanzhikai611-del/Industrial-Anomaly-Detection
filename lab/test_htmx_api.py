import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'IndustrialWarningSystem.settings')
django.setup()
from django.conf import settings
settings.ALLOWED_HOSTS = ['*']
from django.test import Client
from django.contrib.auth.models import User
c = Client()
try:
    u = User.objects.get(username='admin')
    c.force_login(u)
except:
    pass
response = c.get('/accounts/', HTTP_HX_REQUEST='true', SERVER_NAME='127.0.0.1')
print(response.content.decode('utf-8'))
