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

    # File Sync & Upload Progress Tracking
    sync_status = models.CharField(max_length=128, blank=True, default='Idle')
    sync_total_files = models.IntegerField(default=0)
    sync_uploaded_files = models.IntegerField(default=0)
    sync_percent = models.FloatField(default=0.0)

    # Timestamps
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-first_seen', '-id']

    def __str__(self):
        return f"{self.hostname} ({self.agent_id})"


class BackupActivity(models.Model):
    """Stores logs of backup jobs, pings, and sync activities."""

    event = models.CharField(max_length=255)
    data_size = models.CharField(max_length=50, default='-')
    status = models.CharField(max_length=50, default='Success')
    status_type = models.CharField(max_length=20, default='success')  # 'success' or 'failed'
    hostname = models.CharField(max_length=255, blank=True, default='')
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.event} - {self.status} ({self.timestamp})"


class DeletedAgent(models.Model):
    """Tracks agent IDs and MAC addresses deleted by admin so running background processes stop sending telemetry."""
    agent_id = models.CharField(max_length=64, unique=True, primary_key=True)
    mac_address = models.CharField(max_length=64, blank=True, default='', db_index=True)
    hostname = models.CharField(max_length=255, blank=True, default='')
    deleted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"DeletedAgent ({self.hostname} - {self.mac_address} - {self.agent_id})"


class UploadedFile(models.Model):
    """Stores actual file contents uploaded by agents from non-C: drives."""
    agent = models.ForeignKey(AgentReport, on_delete=models.SET_NULL, related_name='files', null=True, blank=True)
    hostname = models.CharField(max_length=128, db_index=True)
    drive_letter = models.CharField(max_length=10)          # e.g. "D:", "E:"
    file_path = models.CharField(max_length=500)             # original full path on user's PC
    file_name = models.CharField(max_length=255)             # just the filename
    file_extension = models.CharField(max_length=50, blank=True, default='')
    file_size = models.BigIntegerField()                     # size in bytes
    file_content = models.BinaryField()                      # actual file bytes (LONGBLOB in MySQL)
    is_deleted_on_client = models.BooleanField(default=False) # True if deleted from PC, but kept in DB
    uploaded_at = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-uploaded_at']
        unique_together = ('hostname', 'file_path')          # prevent duplicate uploads

    def __str__(self):
        return f"{self.file_name} ({self.hostname} - {self.drive_letter})"
