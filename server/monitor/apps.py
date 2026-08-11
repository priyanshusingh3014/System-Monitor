import os
import sys
from django.apps import AppConfig


class MonitorConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'monitor'

    def ready(self):
        # Prevent double execution during Django autoreload main process check
        if 'runserver' in sys.argv and os.environ.get('RUN_MAIN') != 'true':
            return

        try:
            from .models import BackupActivity
            from .fs_monitor import start_fs_monitor

            def create_db_activity(event, data_size, status, status_type):
                BackupActivity.objects.create(
                    event=event,
                    data_size=data_size,
                    status=status,
                    status_type=status_type
                )

            start_fs_monitor(create_db_activity)
            print("[FS MONITOR] Real-time filesystem observer started successfully!")
        except Exception as e:
            print(f"[FS MONITOR INIT ERROR] {e}")
