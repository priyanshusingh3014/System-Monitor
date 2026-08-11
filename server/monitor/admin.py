from django.contrib import admin
from .models import AgentReport


@admin.register(AgentReport)
class AgentReportAdmin(admin.ModelAdmin):
    list_display = ['hostname', 'agent_id', 'public_ip', 'local_ip', 'cpu_usage', 'ram_percent', 'last_seen']
    list_filter = ['os_info']
    search_fields = ['hostname', 'username', 'public_ip', 'local_ip']
    readonly_fields = ['agent_id', 'first_seen', 'last_seen']
