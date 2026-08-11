import os
import json
import shutil
import socket
from datetime import timedelta

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

    agent_id = data.get('agent_id')
    if not agent_id:
        return JsonResponse({'error': 'Missing agent_id'}, status=400)

    # Check if this agent has been deleted by admin
    if DeletedAgent.objects.filter(agent_id=agent_id).exists():
        if data.get('manual_run'):
            # User manually launched/installed the agent again -> re-enroll cleanly
            DeletedAgent.objects.filter(agent_id=agent_id).delete()
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


@require_http_methods(["GET"])
def api_agents(request):
    now = timezone.now()
    # Auto-prune stale agents older than 3 days
    stale_cutoff = now - timedelta(days=3)
    AgentReport.objects.filter(last_seen__lt=stale_cutoff).delete()

    agents = AgentReport.objects.all()
    online_threshold = now - timedelta(seconds=5)

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

    # Calculate genuine vault storage across all drives of enrolled endpoint agents
    total_bytes = 0
    used_bytes = 0
    free_bytes = 0

    for agent in agents:
        if isinstance(agent.drives, list):
            for d in agent.drives:
                if isinstance(d, dict):
                    total_bytes += int(d.get('total', 0) or 0)
                    used_bytes += int(d.get('used', 0) or 0)
                    free_bytes += int(d.get('free', 0) or 0)

    # Fallback to server disk usage if no endpoint agents are enrolled
    if total_bytes == 0:
        try:
            import psutil
            for part in psutil.disk_partitions(all=False):
                if part.mountpoint and part.fstype and ('cdrom' not in part.opts):
                    try:
                        usage = psutil.disk_usage(part.mountpoint)
                        total_bytes += usage.total
                        used_bytes += usage.used
                        free_bytes += usage.free
                    except (PermissionError, OSError):
                        continue
        except Exception:
            try:
                disk = shutil.disk_usage('/')
                total_bytes = disk.total
                used_bytes = disk.used
                free_bytes = disk.free
            except Exception:
                pass

    server_storage = {
        'total': total_bytes,
        'used': used_bytes,
        'free': free_bytes,
    }

    host_name = socket.gethostname()

    # 48-Hour Retention Window: Delete activities older than 48 hours automatically
    cutoff_time = now - timedelta(hours=48)
    BackupActivity.objects.filter(timestamp__lt=cutoff_time).delete()
    BackupActivity.objects.filter(event__icontains="Backup Complete").delete()
    BackupActivity.objects.filter(event__icontains="test").delete()

    # Query DB activities from the last 48 hours (newest first)
    allowed_prefixes = ("Agent Enrolled:", "File Added:", "File Deleted:", "File Renamed:", "File Modified:", "App Installed:", "App Uninstalled:", "Software Event:")
    all_activities = BackupActivity.objects.filter(timestamp__gte=cutoff_time).order_by('-id')
    
    db_activities = []
    seen_recent_targets = {}
    for act in all_activities:
        if any(act.event.startswith(p) for p in allowed_prefixes):
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
        DeletedAgent.objects.get_or_create(agent_id=agent_id)
        AgentReport.objects.filter(agent_id=agent_id).delete()
        return JsonResponse({'status': 'ok', 'message': 'Agent deleted successfully.'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

