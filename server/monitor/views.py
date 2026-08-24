import os
import json
import shutil
from datetime import timedelta

from django.conf import settings
from django.http import JsonResponse, HttpResponse, FileResponse, Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import AgentReport, BackupActivity, DeletedAgent, UploadedFile
import base64
import mimetypes


ONLINE_TIMEOUT_SECONDS = 15


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

    # Check if this agent has been deleted/uninstalled by admin or user
    is_blacklisted = DeletedAgent.objects.filter(agent_id=agent_id).exists()
    if not is_blacklisted and mac and mac != '—':
        is_blacklisted = DeletedAgent.objects.filter(mac_address=mac).exists()

    if is_blacklisted:
        if data.get('is_fresh_installer_run'):
            # User manually ran DriveAgentSetup.exe again -> un-blacklist and allow registration
            DeletedAgent.objects.filter(agent_id=agent_id).delete()
            if mac and mac != '—':
                DeletedAgent.objects.filter(mac_address=mac).delete()
                DeletedAgent.objects.filter(agent_id=f"mac_{mac}").delete()
        else:
            # Background zombie heartbeat from a killed process -> reject it
            return JsonResponse({'status': 'stopped', 'message': 'Agent deleted.'}, status=403)

    # Prevent duplicates: if a record with the same MAC already exists under a different agent_id, remove it
    if mac and mac != '—':
        AgentReport.objects.filter(mac_address=mac).exclude(agent_id=agent_id).delete()

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
        # Clear any leftover activities so dashboard starts clean
        BackupActivity.objects.all().delete()
        BackupActivity.objects.create(
            event=f"App Installed: System Drive Agent",
            data_size="0 KB",
            status="Success",
            status_type="success",
            hostname=agent.hostname
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


def is_file_activity(event_str):
    file_prefixes = ("File Added:", "File Deleted:", "File Renamed:", "File Modified:")
    return any(event_str.startswith(prefix) for prefix in file_prefixes)


def is_dashboard_activity(event_str):
    """Return True when an activity should be visible in the dashboard feed."""
    if not event_str:
        return False
    if is_file_activity(event_str):
        return is_allowed_file_event(event_str)
    allowed_non_file_prefixes = (
        "App Installed:",
        "App Uninstalled:",
        "Agent Enrolled:",
        "Agent Uninstalled:",
    )
    return any(event_str.startswith(prefix) for prefix in allowed_non_file_prefixes)


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

    # Auto-prune activities older than 24 hours
    cutoff = now - timedelta(hours=24)
    BackupActivity.objects.filter(timestamp__lt=cutoff).delete()

    hostname = data.get('hostname', '').strip()
    agent_id = data.get('agent_id', '').strip()
    if not hostname:
        if agent_id:
            try:
                matched = AgentReport.objects.filter(agent_id=agent_id).first()
                if matched:
                    hostname = matched.hostname
            except Exception:
                pass
        if not hostname:
            client_ip = get_client_ip(request)
            if client_ip and client_ip not in ('127.0.0.1', '::1'):
                matched = AgentReport.objects.filter(public_ip=client_ip).first() or AgentReport.objects.filter(local_ip=client_ip).first()
                if matched:
                    hostname = matched.hostname

    activity = BackupActivity.objects.create(
        event=event_text,
        data_size=data_size,
        status=status,
        status_type=status_type,
        hostname=hostname
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


def safe_int(value):
    """Convert numeric telemetry values to int without breaking API responses."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def summarize_drive_storage(agent):
    """Return total/used/free storage for one reported PC agent."""
    total = 0
    used = 0
    free = 0

    drives = agent.get('drives') or []
    if not isinstance(drives, list):
        drives = []

    for drive in drives:
        if not isinstance(drive, dict):
            continue

        drive_total = safe_int(drive.get('total'))
        drive_used = safe_int(drive.get('used'))
        drive_free = safe_int(drive.get('free'))

        if drive_free == 0 and drive_total >= drive_used:
            drive_free = drive_total - drive_used

        total += drive_total
        used += drive_used
        free += drive_free

    return {
        'total': total,
        'used': used,
        'free': free,
        'percent': round((used / total * 100), 1) if total > 0 else 0,
        'hostname': agent.get('hostname', ''),
        'agent_id': agent.get('agent_id', ''),
        'source': 'agent_pc',
    }


def get_dashboard_vault_storage(request=None, agents_data=None):
    """Retrieve total vault drive storage across all enrolled endpoint agents.
    Sums storage deterministically across all enrolled devices so the overview metric
    remains completely stable and never alternates between devices.
    """
    total = 0
    used = 0
    free = 0

    agents_list = agents_data if (agents_data is not None) else []
    if not agents_list:
        try:
            agents_list = [{'drives': a.drives} for a in AgentReport.objects.all()]
        except Exception:
            agents_list = []

    for agent in agents_list:
        drives = agent.get('drives') or []
        if isinstance(drives, list):
            for drive in drives:
                if isinstance(drive, dict):
                    d_total = safe_int(drive.get('total'))
                    d_used = safe_int(drive.get('used'))
                    d_free = safe_int(drive.get('free'))
                    if d_free == 0 and d_total >= d_used:
                        d_free = d_total - d_used
                    total += d_total
                    used += d_used
                    free += d_free

    if total > 0:
        return {
            'total': total,
            'used': used,
            'free': free,
            'percent': round((used / total * 100), 1),
            'source': 'enrolled_vault_storage',
        }

    return {
        'total': 0,
        'used': 0,
        'free': 0,
        'percent': 0,
        'source': 'none',
    }


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

    # Delete records by any identifier that matches
    if agent_id:
        try:
            AgentReport.objects.filter(agent_id=agent_id).delete()
        except Exception:
            pass  # agent_id might not be a valid UUID
    if hostname:
        AgentReport.objects.filter(hostname__iexact=hostname).delete()
    if mac and mac != '—':
        AgentReport.objects.filter(mac_address__iexact=mac).delete()
    if client_ip and client_ip not in ('127.0.0.1', '::1'):
        AgentReport.objects.filter(public_ip=client_ip).delete()
        AgentReport.objects.filter(local_ip=client_ip).delete()

    # Blacklist in DeletedAgent so if any background client is still alive, it gets a 403 STOP
    if agent_id:
        DeletedAgent.objects.update_or_create(
            agent_id=agent_id,
            defaults={'hostname': hostname, 'mac_address': mac if mac != '—' else ''}
        )
    if mac and mac != '—':
        DeletedAgent.objects.update_or_create(
            agent_id=f"mac_{mac}",
            defaults={'hostname': hostname, 'mac_address': mac}
        )

    # Clear all old activities so dashboard starts fresh after reinstall
    BackupActivity.objects.all().delete()

    BackupActivity.objects.create(
        event=f"App Uninstalled: System Drive Agent",
        data_size="0 KB",
        status="Success",
        status_type="success",
        hostname=hostname
    )

    return JsonResponse({'status': 'ok', 'message': 'Agent uninstalled and removed from registered devices'})


@require_http_methods(["GET"])
def api_agents(request):
    now = timezone.now()
    agents = AgentReport.objects.all().order_by('-first_seen', '-id')
    # A PC is online while fresh agent heartbeats are arriving. If the PC shuts down,
    # heartbeats stop and it moves offline after this grace window.
    online_threshold = now - timedelta(seconds=ONLINE_TIMEOUT_SECONDS)

    agents_data = []
    for agent in agents:
        seconds_since_seen = None
        is_online = False
        if agent.last_seen:
            seconds_since_seen = max(0, int((now - agent.last_seen).total_seconds()))
            is_online = agent.last_seen >= online_threshold

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
            'seconds_since_seen': seconds_since_seen,
            'is_online': True,
            'status': 'Active',
        })

    vault_storage = get_dashboard_vault_storage(request, agents_data)

    # 24-Hour Retention Window: Delete stale and noisy activities in ONE query
    cutoff_time = now - timedelta(hours=24)
    from django.db.models import Q
    noise_filter = (
        Q(timestamp__lt=cutoff_time) |
        Q(event__icontains="Backup Complete") |
        Q(event__icontains="test") |
        Q(event__icontains="Screenshot") |
        Q(event__icontains="xref-") |
        Q(event__icontains="warn-") |
        Q(event__icontains="base_library") |
        Q(event__icontains="agent_client.py") |
        Q(event__icontains="dashboard.js") |
        Q(event__icontains="WhatsApp") |
        Q(event__icontains="{") |
        Q(hostname='')
    )
    BackupActivity.objects.filter(noise_filter).delete()

    # Query DB activities from the last 24 hours (newest first)
    all_activities = BackupActivity.objects.filter(timestamp__gte=cutoff_time).exclude(hostname='').order_by('-id')[:50]
    
    db_activities = []
    seen_events = set()
    for act in all_activities:
        if not is_dashboard_activity(act.event) or not act.hostname:
            continue
        # Avoid immediate duplicate rows with identical event + timestamp
        event_key = f"{act.hostname}:{act.event}:{act.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
        if event_key in seen_events:
            continue
        seen_events.add(event_key)
        db_activities.append(act)

    activities_data = []
    for act in db_activities:
        activities_data.append({
            'id': act.id,
            'event': act.event,
            'hostname': act.hostname or '',
            'time': format_time_ago(act.timestamp),
            'data_size': act.data_size if (act.data_size and act.data_size != '-') else '0 KB',
            'status': act.status,
            'status_type': act.status_type
        })

    return JsonResponse({
        'agents': agents_data,
        'total': len(agents_data),
        'online': sum(1 for a in agents_data if a['is_online']),
        'offline': sum(1 for a in agents_data if not a['is_online']),
        'online_timeout_seconds': ONLINE_TIMEOUT_SECONDS,
        'server_time': now.isoformat(),
        'vault_storage': vault_storage,
        'server_storage': vault_storage,
        'server_hostname': vault_storage.get('hostname', ''),
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


@csrf_exempt
@require_http_methods(["POST"])
def api_file_upload(request):
    """Receive a file upload from an agent client."""
    try:
        hostname = request.POST.get('hostname', '').strip()
        agent_id = request.POST.get('agent_id', '').strip()
        file_path = request.POST.get('file_path', '').strip()
        drive_letter = request.POST.get('drive_letter', '').strip()

        if not hostname or not file_path:
            return JsonResponse({'error': 'Missing hostname or file_path'}, status=400)

        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return JsonResponse({'error': 'No file attached'}, status=400)

        # Extract clean file name regardless of OS slashes
        clean_path = file_path.replace('\\', '/')
        file_name = clean_path.split('/')[-1]
        file_extension = os.path.splitext(file_name)[1].lower()
        file_content = uploaded_file.read()
        file_size = len(file_content)

        # Find the agent record if possible
        agent = None
        if agent_id:
            agent = AgentReport.objects.filter(agent_id=agent_id).first()

        # Create or update the file record
        obj, created = UploadedFile.objects.update_or_create(
            hostname=hostname,
            file_path=file_path,
            defaults={
                'agent': agent,
                'drive_letter': drive_letter,
                'file_name': file_name,
                'file_extension': file_extension,
                'file_size': file_size,
                'file_content': file_content,
                'is_deleted_on_client': False,
            }
        )

        return JsonResponse({
            'status': 'ok',
            'file_id': obj.id,
            'created': created,
            'message': f"File '{file_name}' uploaded successfully."
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_file_delete_notify(request):
    """Notify that a file was deleted on the client machine; keep file in DB and mark is_deleted_on_client=True."""
    try:
        data = json.loads(request.body)
        hostname = data.get('hostname', '').strip()
        file_path = data.get('file_path', '').strip()

        if not hostname or not file_path:
            return JsonResponse({'error': 'Missing hostname or file_path'}, status=400)

        file_obj = UploadedFile.objects.filter(hostname=hostname, file_path=file_path).first()
        if file_obj:
            file_obj.is_deleted_on_client = True
            file_obj.save(update_fields=['is_deleted_on_client', 'last_updated'])

        return JsonResponse({'status': 'ok', 'message': 'File marked as deleted on client.'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


def api_file_download(request, file_id):
    """Download or view a file by its database ID."""
    try:
        uploaded = UploadedFile.objects.get(id=file_id)
    except UploadedFile.DoesNotExist:
        raise Http404("File not found")

    content_type, _ = mimetypes.guess_type(uploaded.file_name)
    if not content_type:
        content_type = 'application/octet-stream'

    response = HttpResponse(bytes(uploaded.file_content), content_type=content_type)
    mode = request.GET.get('mode', 'download').lower()
    if mode == 'view':
        response['Content-Disposition'] = f'inline; filename="{uploaded.file_name}"'
    else:
        response['Content-Disposition'] = f'attachment; filename="{uploaded.file_name}"'
    return response


def api_file_list(request):
    """Return a JSON list of all uploaded files (without heavy binary content)."""
    hostname_filter = request.GET.get('hostname', '').strip()
    drive_filter = request.GET.get('drive', '').strip()
    search = request.GET.get('search', '').strip()

    qs = UploadedFile.objects.all()
    if hostname_filter and hostname_filter != 'all':
        qs = qs.filter(hostname__iexact=hostname_filter)
    if drive_filter and drive_filter != 'all':
        # Match 'D:', 'D:\', or 'D'
        clean_drive = drive_filter.replace('\\', '').rstrip(':').upper()
        qs = qs.filter(drive_letter__istartswith=clean_drive)
    if search:
        qs = qs.filter(file_name__icontains=search)

    # Limit to 5000 most recent files
    qs = qs.order_by('-uploaded_at')[:5000]

    files_data = []
    for f in qs:
        files_data.append({
            'id': f.id,
            'hostname': f.hostname,
            'drive_letter': f.drive_letter,
            'file_path': f.file_path,
            'file_name': f.file_name,
            'file_extension': f.file_extension,
            'file_size': f.file_size,
            'is_deleted_on_client': f.is_deleted_on_client,
            'uploaded_at': f.uploaded_at.isoformat() if f.uploaded_at else None,
        })

    return JsonResponse({'files': files_data, 'total': len(files_data)})
