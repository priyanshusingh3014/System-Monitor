from django.db import models
import uuid


class AgentReport(models.Model):
    """Stores system information reported by each agent client."""

    agent_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    hostname = models.CharField(max_length=255, blank=True, default='')
    username = models.CharField(max_length=255, blank=True, default='')
    os_info = models.CharField(max_length=512, blank=True, default='')

    # Network
    mac_address = models.CharField(max_length=64, blank=True, default='')
    public_ip = models.GenericIPAddressField(null=True, blank=True)
    local_ip = models.GenericIPAddressField(null=True, blank=True)

    # Hardware
    cpu_info = models.CharField(max_length=512, blank=True, default='')
    cpu_cores = models.IntegerField(null=True, blank=True)
    cpu_usage = models.FloatField(null=True, blank=True)
    ram_total = models.BigIntegerField(null=True, blank=True)  # bytes
    ram_used = models.BigIntegerField(null=True, blank=True)   # bytes
    ram_percent = models.FloatField(null=True, blank=True)

    # Drives — stored as JSON list of dicts
    drives = models.JSONField(default=list, blank=True)

    # Timestamps
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-last_seen']

    def __str__(self):
        return f"{self.hostname} ({self.agent_id})"


class BackupActivity(models.Model):
    """Stores logs of backup jobs, pings, and sync activities."""

    event = models.CharField(max_length=255)
    data_size = models.CharField(max_length=50, default='-')
    status = models.CharField(max_length=50, default='Success')
    status_type = models.CharField(max_length=20, default='success')  # 'success' or 'failed'
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.event} - {self.status} ({self.timestamp})"


class DeletedAgent(models.Model):
    """Tracks agent IDs deleted by admin so running background processes stop sending telemetry."""
    agent_id = models.UUIDField(unique=True, primary_key=True)
    deleted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"DeletedAgent ({self.agent_id})"
