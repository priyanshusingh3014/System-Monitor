import os
import sys
from django.apps import AppConfig


class MonitorConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'monitor'

    def ready(self):
        # Server only receives telemetry and activities via REST API from client agents
        pass
