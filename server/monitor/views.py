import os
import json
import shutil
import socket
from datetime import timedelta
import psutil

from django.conf import settings
from django.http import JsonResponse, HttpResponse, FileResponse, Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import AgentReport, BackupActivity, DeletedAgent



def format_time_ago(dt):
    """Helper to return a human-readable relative time string."""
    now = timezone.now()
    diff = now - dt
    seconds = int(diff.total_seconds())

    if seconds < 60:
        return 'Just now'
    elif seconds < 3600:
        mins = seconds // 60
        return f"{mins} min{'s' if mins > 1 else ''} ago"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    else:
        days = seconds // 86400
        return f"{days} day{'s' if days > 1 else ''} ago"


@csrf_exempt
@require_http_methods(["POST"])
def api_report(request):
    """Receive a system report from an agent client."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    agent_id = str(data.get('agent_id', '')).strip()
    if not agent_id:
        return JsonResponse({'error': 'Missing agent_id'}, status=400)

    mac = str(data.get('mac_address', '')).strip()

    # Check if this agent (by agent_id or physical mac_address) has been deleted by admin
    is_blacklisted = DeletedAgent.objects.filter(agent_id=agent_id).exists()
    if not is_blacklisted and mac and mac != '—':
        is_blacklisted = DeletedAgent.objects.filter(mac_address=mac).exists()

    if is_blacklisted:
        if data.get('is_fresh_installer_run'):
            # User manually ran DriveAgentSetup.exe installer again -> un-blacklist cleanly
            DeletedAgent.objects.filter(agent_id=agent_id).delete()
            if mac and mac != '—':
                DeletedAgent.objects.filter(mac_address=mac).delete()
        else:
            return JsonResponse({'status': 'stopped', 'message': 'Agent deleted by admin.'}, status=403)

    # Update or create the agent record
    agent, created = AgentReport.objects.update_or_create(
        agent_id=agent_id,
        defaults={
            'hostname': data.get('hostname', ''),
            'username': data.get('username', ''),
            'mac_address': data.get('mac_address', ''),
            'os_info': data.get('os_info', ''),
            'public_ip': data.get('public_ip'),
            'local_ip': data.get('local_ip'),
            'cpu_info': data.get('cpu_info', ''),
            'cpu_cores': data.get('cpu_cores'),
            'cpu_usage': data.get('cpu_usage'),
            'ram_total': data.get('ram_total'),
            'ram_used': data.get('ram_used'),
            'ram_percent': data.get('ram_percent'),
            'drives': data.get('drives', []),
        }
    )

    if created:
        BackupActivity.objects.create(
            event=f"Agent Enrolled: {agent.hostname} ({agent.username or 'User'})",
            data_size="System Agent",
            status="Success",
            status_type="success"
        )

    status_code = 201 if created else 200
    return JsonResponse({
        'status': 'ok',
        'agent_id': str(agent.agent_id),
        'created': created,
    }, status=status_code)


ALLOWED_USER_EXTENSIONS = {
    # Documents & Text
    '.txt', '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.csv', '.rtf', '.odt', '.ods', '.odp', '.md',
    # Images & Graphics
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.tiff',
    '.psd', '.ai', '.raw', '.heic',
    # Media: Video & Audio
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm',
    '.mp3', '.wav', '.aac', '.flac', '.m4a', '.ogg',
    # Archives
    '.zip', '.rar', '.7z', '.tar', '.gz', '.iso',
    # User Code & Scripts
    '.py', '.js', '.html', '.css', '.ts', '.cpp', '.c', '.h', '.cs',
    '.java', '.php', '.rb', '.go', '.rs', '.sql', '.sh', '.bat', '.ps1'
}


def get_target_key(event_str):
    """Extract clean file/app target key without drive suffix e.g. 'File Added: notes.txt (D:)' -> 'notes.txt'"""
    if ":" in event_str:
        part = event_str.split(":", 1)[1].strip()
        if "➔" in part:
            part = part.split("➔")[1].strip()
        if " (" in part and part.endswith(")"):
            part = part.rsplit(" (", 1)[0].strip()
        return part.lower()
    return event_str.lower()


def is_allowed_file_event(event_str):
    """Check if file event is for an allowed user extension."""
    allowed_prefixes = ("File Added:", "File Deleted:", "File Renamed:", "File Modified:")
    if not any(event_str.startswith(p) for p in allowed_prefixes):
        return True  # Non-file system/agent events allowed
    target_key = get_target_key(event_str)
    _, ext = os.path.splitext(target_key)
    return ext.lower() in ALLOWED_USER_EXTENSIONS


@csrf_exempt
@require_http_methods(["POST"])
def api_trigger_activity(request):
    """API endpoint to trigger a new backup job or filesystem activity."""
    try:
        data = json.loads(request.body) if request.body else {}
    except Exception:
        data = {}

    job_name = data.get('job_name', '').strip()
    if not job_name:
        return JsonResponse({'status': 'ignored', 'reason': 'empty event'}, status=400)

    if not is_allowed_file_event(job_name):
        return JsonResponse({'status': 'ignored', 'reason': 'extension_not_allowed'})

    data_size = data.get('data_size', '0 KB')
    status = data.get('status', 'Success')
    status_type = data.get('status_type', 'success')

    event_text = job_name
    now = timezone.now()
    target_key = get_target_key(event_text)

    # Rapid-fire debouncer: Ignore events for the SAME target key triggered within 0.5 seconds
    all_recent = BackupActivity.objects.filter(timestamp__gte=now - timedelta(milliseconds=500))
    for recent in all_recent:
        if get_target_key(recent.event) == target_key:
            return JsonResponse({
                'status': 'duplicate_ignored',
                'activity': {
                    'id': recent.id,
                    'event': recent.event,
                    'time': 'Just now',
                    'data_size': recent.data_size,
                    'status': recent.status,
                    'status_type': recent.status_type
                }
            })

    # Auto-prune activities older than 48 hours
    cutoff = now - timedelta(hours=48)
    BackupActivity.objects.filter(timestamp__lt=cutoff).delete()

    activity = BackupActivity.objects.create(
        event=event_text,
        data_size=data_size,
        status=status,
        status_type=status_type
    )

    # If event indicates uninstallation of the agent, delete matching AgentReport record from DB
    if "App Uninstalled: System Drive Agent" in event_text or "Agent Uninstalled" in event_text:
        if data.get('hostname'):
            AgentReport.objects.filter(hostname=data.get('hostname')).delete()
        if data.get('agent_id'):
            AgentReport.objects.filter(agent_id=data.get('agent_id')).delete()

    return JsonResponse({
        'status': 'ok',
        'activity': {
            'id': activity.id,
            'event': activity.event,
            'time': 'Just now',
            'data_size': activity.data_size,
            'status': activity.status,
            'status_type': activity.status_type
        }
    })


def get_client_ip(request):
    """Retrieve remote IP address of client from request headers."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


@csrf_exempt
@require_http_methods(["POST"])
def api_uninstall_agent(request):
    """API endpoint called when agent executable is uninstalled from a device."""
    try:
        data = json.loads(request.body) if request.body else {}
    except Exception:
        data = {}

    agent_id = str(data.get('agent_id', '')).strip()
    hostname = str(data.get('hostname', '')).strip()
    mac = str(data.get('mac_address', '')).strip()
    username = str(data.get('username', 'User')).strip()
    client_ip = get_client_ip(request)

    # 1. Delete by agent_id (if valid UUID)
    if agent_id:
        try:
            import uuid
            uuid_obj = uuid.UUID(agent_id)
            AgentReport.objects.filter(agent_id=uuid_obj).delete()
        except Exception:
            pass

    # 2. Delete by hostname
    if hostname:
        try:
            AgentReport.objects.filter(hostname__iexact=hostname).delete()
        except Exception:
            pass

    # 3. Delete by MAC address
    if mac and mac != '—':
        try:
            AgentReport.objects.filter(mac_address__iexact=mac).delete()
        except Exception:
            pass

    # 4. Delete by Client IP
    if client_ip and client_ip not in ('127.0.0.1', '::1'):
        try:
            AgentReport.objects.filter(public_ip=client_ip).delete()
            AgentReport.objects.filter(local_ip=client_ip).delete()
        except Exception:
            pass

    BackupActivity.objects.create(
        event=f"Agent Uninstalled: {hostname or 'PC'} ({username})",
        data_size="System Agent",
        status="Success",
        status_type="success"
    )

    return JsonResponse({'status': 'ok', 'message': 'Agent uninstalled and removed from registered devices'})


@require_http_methods(["GET"])
def api_agents(request):
    now = timezone.now()
    agents = AgentReport.objects.all()
    # Device is Online if 1-second heartbeat received within last 4 seconds; Offline if PC shutdown/off
    online_threshold = now - timedelta(seconds=4)

    agents_data = []
    for agent in agents:
        agents_data.append({
            'agent_id': str(agent.agent_id),
            'hostname': agent.hostname,
            'username': agent.username,
            'mac_address': agent.mac_address or '—',
            'os_info': agent.os_info,
            'public_ip': agent.public_ip,
            'local_ip': agent.local_ip,
            'cpu_info': agent.cpu_info,
            'cpu_cores': agent.cpu_cores,
            'cpu_usage': agent.cpu_usage,
            'ram_total': agent.ram_total,
            'ram_used': agent.ram_used,
            'ram_percent': agent.ram_percent,
            'drives': agent.drives,
            'first_seen': agent.first_seen.isoformat() if agent.first_seen else None,
            'last_seen': agent.last_seen.isoformat() if agent.last_seen else None,
            'is_online': agent.last_seen >= online_threshold if agent.last_seen else False,
        })

    # Dynamic live storage of the host PC/Server running Django/Render link
    host_total = 0
    host_used = 0
    host_free = 0

    try:
        for part in psutil.disk_partitions(all=False):
            if 'cdrom' in part.opts or not part.device:
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                host_total += usage.total
                host_used += usage.used
                host_free += usage.free
            except Exception:
                pass
    except Exception:
        try:
            usage = psutil.disk_usage(os.path.abspath('/'))
            host_total = usage.total
            host_used = usage.used
            host_free = usage.free
        except Exception:
            pass

    # Sum drives of active enrolled agents
    agent_total = 0
    agent_used = 0
    agent_free = 0

    for agent in agents:
        if isinstance(agent.drives, list):
            for d in agent.drives:
                if isinstance(d, dict):
                    agent_total += int(d.get('total', 0) or 0)
                    agent_used += int(d.get('used', 0) or 0)
                    agent_free += int(d.get('free', 0) or 0)

    # Combine host server machine storage with agent storage dynamically
    total_bytes = max(host_total, agent_total) if agent_total == 0 else (host_total + agent_total)
    used_bytes = max(host_used, agent_used) if agent_used == 0 else (host_used + agent_used)
    free_bytes = max(host_free, agent_free) if agent_free == 0 else (host_free + agent_free)

    server_storage = {
        'total': total_bytes,
        'used': used_bytes,
        'free': free_bytes,
        'percent': round((used_bytes / total_bytes * 100), 1) if total_bytes > 0 else 0
    }

    host_name = socket.gethostname()

    # 48-Hour Retention Window: Delete activities older than 48 hours automatically
    cutoff_time = now - timedelta(hours=48)
    BackupActivity.objects.filter(timestamp__lt=cutoff_time).delete()
    BackupActivity.objects.filter(event__icontains="Backup Complete").delete()
    BackupActivity.objects.filter(event__icontains="test").delete()
    BackupActivity.objects.filter(event__icontains="Screenshot").delete()
    BackupActivity.objects.filter(event__icontains="xref-").delete()
    BackupActivity.objects.filter(event__icontains="warn-").delete()
    BackupActivity.objects.filter(event__icontains="base_library").delete()
    BackupActivity.objects.filter(event__icontains="agent_client.py").delete()
    BackupActivity.objects.filter(event__icontains="dashboard.js").delete()
    BackupActivity.objects.filter(event__icontains="WhatsApp").delete()
    BackupActivity.objects.filter(event__icontains="{").delete()

    # Query DB activities from the last 48 hours (newest first)
    allowed_prefixes = ("File Added:", "File Deleted:", "File Renamed:", "File Modified:")
    all_activities = BackupActivity.objects.filter(timestamp__gte=cutoff_time).order_by('-id')
    
    db_activities = []
    seen_recent_targets = {}
    for act in all_activities:
        if any(act.event.startswith(p) for p in allowed_prefixes):
            if not is_allowed_file_event(act.event):
                continue
            t_key = get_target_key(act.event)
            # Deduplicate items for the same target key occurring within 5 seconds of each other
            if t_key not in seen_recent_targets or abs((seen_recent_targets[t_key] - act.timestamp).total_seconds()) > 5:
                seen_recent_targets[t_key] = act.timestamp
                db_activities.append(act)
            if len(db_activities) >= 50:
                break

    activities_data = []
    for act in db_activities:
        activities_data.append({
            'id': act.id,
            'event': act.event,
            'time': format_time_ago(act.timestamp),
            'data_size': act.data_size if (act.data_size and act.data_size != '-') else '0 KB',
            'status': act.status,
            'status_type': act.status_type
        })

    return JsonResponse({
        'agents': agents_data,
        'total': len(agents_data),
        'online': sum(1 for a in agents_data if a['is_online']),
        'server_time': now.isoformat(),
        'server_storage': server_storage,
        'server_hostname': host_name,
        'recent_activities': activities_data,
    })


def dashboard(request):
    """Serve the main dashboard HTML page."""
    response = render(request, 'monitor/dashboard.html')
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


@csrf_exempt
@require_http_methods(["POST"])
def api_clear_activities(request):
    """Clear all recent backup activities from database."""
    BackupActivity.objects.all().delete()
    return JsonResponse({'status': 'ok', 'message': 'All activities cleared successfully'})


@csrf_exempt
@require_http_methods(["POST", "DELETE"])
def api_delete_agent(request, agent_id):
    """Delete an enrolled agent device by agent_id and stop its background telemetry."""
    try:
        aid_str = str(agent_id).strip()
        agent = AgentReport.objects.filter(agent_id=aid_str).first()
        mac = agent.mac_address if agent else ''
        host = agent.hostname if agent else ''

        DeletedAgent.objects.get_or_create(
            agent_id=aid_str,
            defaults={'mac_address': mac, 'hostname': host}
        )
        if mac and mac != '—':
            DeletedAgent.objects.get_or_create(
                mac_address=mac,
                defaults={'agent_id': f"mac_{mac}", 'hostname': host}
            )

        AgentReport.objects.filter(agent_id=aid_str).delete()
        return JsonResponse({'status': 'ok', 'message': 'Agent deleted successfully.'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

