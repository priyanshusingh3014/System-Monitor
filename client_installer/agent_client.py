"""
System Monitor Agent Client
============================
Collects system information (CPU, RAM, drives, IP, OS) and sends it
to the Django dashboard server via HTTP POST every 30 seconds.
Also monitors local filesystem events (file added, deleted, renamed)
and reports them in real-time to the dashboard.

Usage:
    python agent_client.py

To compile to .exe:
    pyinstaller --onefile --noconsole agent_client.py
"""

import json
import os
import platform
import socket
import threading
import sys
import time
import uuid

import psutil
import requests

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False


def get_config_file_path():
    """Locate config.json relative to PyInstaller bundle, running executable, or script dir."""
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            bundle_c = os.path.join(sys._MEIPASS, "config.json")
            if os.path.exists(bundle_c):
                return bundle_c
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        c_path = os.path.join(exe_dir, "config.json")
        if os.path.exists(c_path):
            return c_path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, "config.json")


DEFAULT_SERVER_URL = "https://system-monitor-s3q7.onrender.com"

def get_base_url():
    env_url = os.environ.get("SERVER_BASE_URL", "").strip()
    if env_url:
        return env_url.rstrip("/")

    c_path = get_config_file_path()
    if os.path.exists(c_path):
        try:
            with open(c_path, "r") as f:
                c = json.load(f)
                url = str(c.get("server_url", "")).strip()
                if url:
                    return url.rstrip("/")
        except Exception:
            pass

    return DEFAULT_SERVER_URL

BASE_URL = get_base_url()
SERVER_URL = f"{BASE_URL}/api/report/"
ACTIVITY_URL = f"{BASE_URL}/api/activities/create/"
REPORT_INTERVAL = 1.0  # seconds (1 second heartbeat)
PUBLIC_IP_REFRESH_SECONDS = 300
PUBLIC_IP_RETRY_SECONDS = 60
AGENT_ID_FILE = os.path.join(os.path.expanduser("~"), ".system_monitor_agent_id")

public_ip_cache = None
public_ip_last_checked_at = 0

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

IGNORED_PATTERNS = [
    'AppData', 'Application Data', 'LocalSettings', 'ProgramData',
    'Windows', 'Program Files', 'Program Files (x86)', 'System Volume Information',
    '$Recycle.Bin', '$RECYCLE.BIN', '__pycache__', '.venv', 'venv', 'node_modules',
    '.git', '.gemini', '.antigravity', 'CacheStorage', 'GPUCache', 'IndexedDB',
    'prefetch', 'temp', 'tmp', 'crashdumps', 'logs', 'telemetry', 'diagnostics',
    'screenshot', 'screenshots', 'build', 'dist', 'scratch',
    'site-packages', 'pyinstaller', 'whatsapp', 'my agent', 'client_installer'
]


def is_ignored(path):
    filename = os.path.basename(path)
    fn_lower = filename.lower()

    # Ignore system/temp/build prefixes & GUID temp files
    if (fn_lower.startswith('.') or fn_lower.startswith('~$') or fn_lower.startswith('{') or 
        fn_lower.startswith('f_') or fn_lower.startswith('todelete_') or fn_lower.startswith('screenshot') or 
        fn_lower.startswith('xref-') or fn_lower.startswith('warn-') or 'base_library' in fn_lower or 'whatsapp' in fn_lower):
        return True

    # Check ignored system/build paths
    path_lower = path.lower()
    for pattern in IGNORED_PATTERNS:
        if pattern.lower() in path_lower:
            return True

    # Strict ALLOWLIST: Only accept genuine user files
    _, ext = os.path.splitext(fn_lower)
    if ext not in ALLOWED_USER_EXTENSIONS:
        return True

    return False


def format_size(num_bytes):
    if num_bytes is None or num_bytes < 0:
        return '0 KB'
    if num_bytes == 0:
        return '0 KB'
    if num_bytes < 1024:
        return f"{num_bytes} B"
    elif num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    elif num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"


def get_or_create_agent_id():
    """Get a persistent agent ID, or create one if first run."""
    if os.path.exists(AGENT_ID_FILE):
        with open(AGENT_ID_FILE, "r") as f:
            agent_id = f.read().strip()
            if agent_id:
                return agent_id

    agent_id = str(uuid.uuid4())
    with open(AGENT_ID_FILE, "w") as f:
        f.write(agent_id)
    return agent_id


def send_activity_event(event_name, data_size="0 KB", status="Success", status_type="success"):
    """Post real-time filesystem activity to the server."""
    try:
        requests.post(
            ACTIVITY_URL,
            json={
                "job_name": event_name,
                "data_size": data_size,
                "status": status,
                "status_type": status_type,
                "hostname": get_genuine_pc_name(),
                "agent_id": get_or_create_agent_id(),
            },
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
    except Exception as e:
        print(f"[WARN] Failed to post activity event: {e}")


def get_display_name(path):
    """Return filename with drive letter for context e.g. myfile.txt (D:)"""
    drive, _ = os.path.splitdrive(path)
    filename = os.path.basename(path)
    if drive:
        return f"{filename} ({drive})"
    return filename


if HAS_WATCHDOG:
    class AgentFSHandler(FileSystemEventHandler):
        def __init__(self):
            super().__init__()
            self.last_events = {}
            self.file_sizes = {}

        def _debounce(self, path, event_type):
            key = f"{path}:{event_type}"
            now = time.time()
            if key in self.last_events and (now - self.last_events[key]) < 0.5:
                return True
            self.last_events[key] = now
            return False

        def _get_size_and_store(self, path):
            time.sleep(0.01)
            try:
                size = os.path.getsize(path)
                self.file_sizes[path] = size
                return format_size(size)
            except Exception:
                last = self.file_sizes.get(path)
                return format_size(last if last is not None else 0)

        def on_created(self, event):
            if event.is_directory or is_ignored(event.src_path):
                return
            if self._debounce(event.src_path, 'created'):
                return
            name = get_display_name(event.src_path)
            size_str = self._get_size_and_store(event.src_path)
            send_activity_event(f"File Added: {name}", size_str)

        def on_modified(self, event):
            if event.is_directory or is_ignored(event.src_path):
                return
            _, ext = os.path.splitext(event.src_path.lower())
            # Images, media, and archives are modified automatically by OS background indexing; ignore on_modified
            if ext in {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg', '.webp', '.tiff', '.psd', '.ai', '.raw', '.heic', '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mp3', '.wav', '.aac', '.flac', '.m4a', '.ogg', '.zip', '.rar', '.7z', '.tar', '.gz', '.iso'}:
                return
            try:
                new_size = os.path.getsize(event.src_path)
                old_size = self.file_sizes.get(event.src_path)
                if old_size != new_size:
                    self.file_sizes[event.src_path] = new_size
                    if self._debounce(event.src_path, 'modified'):
                        return
                    name = get_display_name(event.src_path)
                    send_activity_event(f"File Modified: {name}", format_size(new_size))
            except Exception:
                pass

        def on_deleted(self, event):
            if event.is_directory or is_ignored(event.src_path):
                return
            filename = os.path.basename(event.src_path)
            if any(filename.startswith(p) for p in ["New Text Document", "New Microsoft Word", "New Rich Text", "New Bitmap", "New Folder"]):
                return
            if self._debounce(event.src_path, 'deleted'):
                return
            name = get_display_name(event.src_path)
            last_size = self.file_sizes.pop(event.src_path, 0)
            size_str = format_size(last_size)
            send_activity_event(f"File Deleted: {name}", size_str)

        def on_moved(self, event):
            if event.is_directory or is_ignored(event.src_path) or is_ignored(event.dest_path):
                return
            if self._debounce(event.dest_path, 'moved'):
                return
            src_name = get_display_name(event.src_path)
            dest_name = get_display_name(event.dest_path)
            old_size = self.file_sizes.pop(event.src_path, None)
            size_str = self._get_size_and_store(event.dest_path)
            if size_str == '0 KB' and old_size is not None:
                size_str = format_size(old_size)
            send_activity_event(f"File Renamed: {src_name} ➔ {dest_name}", size_str)


def detect_all_client_drives():
    dirs = []
    user_home = os.path.expanduser("~")
    for folder in ["Desktop", "Documents", "Downloads", "Pictures", "Videos"]:
        p = os.path.join(user_home, folder)
        if os.path.exists(p):
            dirs.append(p)
    try:
        for part in psutil.disk_partitions(all=False):
            if part.mountpoint and os.path.exists(part.mountpoint):
                dirs.append(part.mountpoint)
    except Exception:
        pass
    return list(dict.fromkeys(dirs))


def start_client_software_monitor():
    if os.name != 'nt':
        return

    def run_software_loop():
        try:
            import winreg
        except ImportError:
            return

        def get_installed_apps():
            apps = set()
            keys = [
                (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'),
                (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'),
                (winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Uninstall')
            ]
            for hkey, path in keys:
                try:
                    key = winreg.OpenKey(hkey, path)
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            sub = winreg.EnumKey(key, i)
                            sub_key = winreg.OpenKey(key, sub)
                            val, _ = winreg.QueryValueEx(sub_key, 'DisplayName')
                            if val and str(val).strip():
                                apps.add(str(val).strip())
                        except Exception:
                            pass
                except Exception:
                    pass
            return apps

        known_apps = get_installed_apps()
        time.sleep(2)
        while True:
            try:
                time.sleep(8)
                current_apps = get_installed_apps()
                if not known_apps:
                    known_apps = current_apps
                    continue

                new_apps = current_apps - known_apps
                removed_apps = known_apps - current_apps

                for app in new_apps:
                    send_activity_event(f"App Installed: {app}", "0 KB")

                for app in removed_apps:
                    send_activity_event(f"App Uninstalled: {app}", "0 KB")

                known_apps = current_apps
            except Exception as e:
                time.sleep(10)

    t = threading.Thread(target=run_software_loop, daemon=True)
    t.start()


def start_client_fs_monitor():
    start_client_software_monitor()

    if not HAS_WATCHDOG:
        print("[AGENT] watchdog not installed; filesystem event tracking disabled.")
        return

    watch_dirs = detect_all_client_drives()
    handler = AgentFSHandler()
    observer = Observer()
    for d in watch_dirs:
        try:
            observer.schedule(handler, d, recursive=True)
            print(f"[AGENT FS] Watching drive/directory: {d}")
        except Exception as e:
            print(f"[AGENT FS] Error watching {d}: {e}")

    t = threading.Thread(target=observer.start, daemon=True)
    t.start()


def get_public_ip():
    global public_ip_cache, public_ip_last_checked_at

    now = time.time()
    retry_after = PUBLIC_IP_REFRESH_SECONDS if public_ip_cache else PUBLIC_IP_RETRY_SECONDS
    if now - public_ip_last_checked_at < retry_after:
        return public_ip_cache

    public_ip_last_checked_at = now
    try:
        resp = requests.get("https://api.ipify.org?format=json", timeout=2)
        ip = resp.json().get("ip")
        if ip:
            public_ip_cache = ip
    except Exception:
        pass

    return public_ip_cache


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_cpu_info():
    try:
        if platform.system() == "Windows":
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            )
            cpu_name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            winreg.CloseKey(key)
            return cpu_name.strip()
        else:
            return platform.processor() or "Unknown CPU"
    except Exception:
        return platform.processor() or "Unknown CPU"


def get_genuine_user_name():
    """Dynamically retrieve the genuine user full name of ANY PC at runtime via Windows Security API, net user, and OS Environment."""
    if platform.system() == "Windows":
        # 1. Query Windows Security API for User Full Display Name (NameDisplay = 3)
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(256)
            size = ctypes.c_ulong(256)
            if ctypes.windll.secur32.GetUserNameExW(3, buf, ctypes.byref(size)):
                val = buf.value.strip()
                if val:
                    return val
        except Exception:
            pass

        # 2. Query 'net user <USERNAME>' for Full Name
        try:
            import subprocess
            username = os.environ.get("USERNAME", "")
            if username:
                out = subprocess.check_output(f'net user "{username}"', shell=True, text=True, timeout=3, stderr=subprocess.DEVNULL)
                for line in out.splitlines():
                    if "Full Name" in line or "Full name" in line:
                        parts = line.split("Full Name") if "Full Name" in line else line.split("Full name")
                        full_name = parts[-1].strip()
                        if full_name:
                            return full_name
        except Exception:
            pass

        # 3. Fallback to OS Environment variables
        u = os.environ.get("USERNAME")
        if u:
            return u

    import getpass
    return getpass.getuser()


def get_genuine_pc_name():
    """Retrieve genuine Windows Computer / PC Name dynamically from Windows Kernel API."""
    if platform.system() == "Windows":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(256)
            size = ctypes.c_ulong(256)
            # ComputerNamePhysicalDnsHostname = 5 (Genuine DNS PC Name)
            if ctypes.windll.kernel32.GetComputerNameExW(5, buf, ctypes.byref(size)):
                val = buf.value.strip()
                if val:
                    return val
        except Exception:
            pass
        if os.environ.get("COMPUTERNAME"):
            return os.environ.get("COMPUTERNAME")
    return socket.gethostname() or platform.node()


def get_mac_address():
    """Retrieve primary physical MAC address of the device formatted as XX:XX:XX:XX:XX:XX."""
    try:
        mac_num = uuid.getnode()
        mac = ':'.join(['{:02x}'.format((mac_num >> i) & 0xff) for i in range(0, 48, 8)][::-1])
        if mac and mac != '00:00:00:00:00:00':
            return mac.upper()
    except Exception:
        pass
    return '—'


def collect_system_info(agent_id):
    cpu_usage = psutil.cpu_percent(interval=None)
    cpu_cores = psutil.cpu_count(logical=True)
    cpu_info = get_cpu_info()
    ram = psutil.virtual_memory()

    drives = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            drives.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "percent": usage.percent,
            })
        except (PermissionError, OSError):
            continue

    os_info = f"{platform.system()} {platform.release()} ({platform.version()})"

    return {
        "agent_id": agent_id,
        "hostname": get_genuine_pc_name(),
        "username": get_genuine_user_name(),
        "mac_address": get_mac_address(),
        "os_info": os_info,
        "public_ip": get_public_ip(),
        "local_ip": get_local_ip(),
        "cpu_info": cpu_info,
        "cpu_cores": cpu_cores,
        "cpu_usage": cpu_usage,
        "ram_total": ram.total,
        "ram_used": ram.used,
        "ram_percent": ram.percent,
        "drives": drives
    }


def send_report(data):
    try:
        resp = requests.post(
            SERVER_URL,
            json=data,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if resp.status_code in (200, 201):
            print(f"[OK] Report sent — {data.get('hostname', 'Host')} ({data.get('public_ip', '—')})")
            return "OK"
        elif resp.status_code == 403:
            print("[STOP] Server returned 403: Agent deleted by admin.")
            return "STOP"
        else:
            print(f"[WARN] Server returned {resp.status_code}: {resp.text}")
    except requests.ConnectionError:
        print(f"[ERROR] Cannot reach server at {SERVER_URL}")
    except Exception as e:
        print(f"[ERROR] {e}")
    return "CONTINUE"


def show_message_box(title, text, style=0):
    """Native Windows MessageBox helper. Returns button clicked (e.g. 6 for Yes)."""
    if os.name == 'nt':
        try:
            import ctypes
            return ctypes.windll.user32.MessageBoxW(0, text, title, style | 0x00040000)
        except Exception:
            pass
    return 0


def kill_running_agent():
    """Kill any running system_monitor_agent or agent_client process so cleanup succeeds."""
    if os.name != 'nt':
        return
    try:
        import psutil, os, time
        current_proc = psutil.Process(os.getpid())
        parent_pid = current_proc.ppid()
        current_pid = current_proc.pid
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                name = proc.info.get('name')
                pid = proc.info['pid']
                if name and name.lower() in ['system_monitor_agent.exe', 'agent_client.exe', 'driveagentsetup.exe']:
                    if pid != current_pid and pid != parent_pid:
                        proc.kill()
            except Exception:
                pass
        time.sleep(0.5)
    except Exception:
        pass


def register_uninstaller(target_exe, app_dir):
    r"""Register System Drive Agent under Programs and Features across HKCU & HKLM (standard + WOW6432Node)."""
    if os.name != 'nt':
        return

    import winreg
    keys_to_register = [
        (winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Uninstall\SystemDriveAgent'),
        (winreg.HKEY_CURRENT_USER, r'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\SystemDriveAgent'),
        (winreg.HKEY_LOCAL_MACHINE, r'Software\Microsoft\Windows\CurrentVersion\Uninstall\SystemDriveAgent'),
        (winreg.HKEY_LOCAL_MACHINE, r'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\SystemDriveAgent')
    ]

    install_date = time.strftime('%Y%m%d')
    uninst_cmd = f'"{target_exe}" --uninstall'

    for hkey, path in keys_to_register:
        try:
            key = winreg.CreateKey(hkey, path)
            winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_SZ, 'System Drive Agent')
            winreg.SetValueEx(key, 'Publisher', 0, winreg.REG_SZ, 'Drive Monitor Software')
            winreg.SetValueEx(key, 'DisplayVersion', 0, winreg.REG_SZ, '1.0.0')
            winreg.SetValueEx(key, 'InstallLocation', 0, winreg.REG_SZ, app_dir)
            winreg.SetValueEx(key, 'UninstallString', 0, winreg.REG_SZ, uninst_cmd)
            winreg.SetValueEx(key, 'QuietUninstallString', 0, winreg.REG_SZ, uninst_cmd)
            winreg.SetValueEx(key, 'ModifyPath', 0, winreg.REG_SZ, uninst_cmd)
            winreg.SetValueEx(key, 'DisplayIcon', 0, winreg.REG_SZ, target_exe)
            winreg.SetValueEx(key, 'EstimatedSize', 0, winreg.REG_DWORD, 12000)
            winreg.SetValueEx(key, 'InstallDate', 0, winreg.REG_SZ, install_date)
            winreg.SetValueEx(key, 'NoModify', 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, 'NoRepair', 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, 'WindowsInstaller', 0, winreg.REG_DWORD, 0)
            winreg.SetValueEx(key, 'SystemComponent', 0, winreg.REG_DWORD, 0)
            winreg.CloseKey(key)
        except Exception:
            pass


def perform_uninstallation():
    """Unregisters registry keys, notifies server to remove device, kills background process, deletes agent directory, and shows confirmation."""
    if os.name != 'nt':
        return

    try:
        # Collect identifiers BEFORE deleting anything
        agent_id = None
        try:
            if os.path.exists(AGENT_ID_FILE):
                with open(AGENT_ID_FILE, "r") as f:
                    agent_id = f.read().strip()
        except Exception:
            pass
        if not agent_id:
            agent_id = str(uuid.uuid4())

        pc_name = get_genuine_pc_name()
        user_name = get_genuine_user_name()
        mac_addr = get_mac_address()

        # Notify server to remove registered endpoint device — retry up to 3 times
        uninst_url = f"{BASE_URL}/api/agents/uninstall/"
        for attempt in range(3):
            try:
                resp = requests.post(
                    uninst_url,
                    json={
                        "agent_id": agent_id,
                        "hostname": pc_name,
                        "username": user_name,
                        "mac_address": mac_addr
                    },
                    headers={"Content-Type": "application/json"},
                    timeout=5
                )
                if resp.status_code in (200, 201):
                    break
            except Exception as e:
                time.sleep(0.5)

        import winreg, subprocess, shutil
        kill_running_agent()

        # Remove Startup Key
        try:
            run_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run', 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(run_key, 'SystemMonitorAgent')
            winreg.CloseKey(run_key)
        except Exception:
            pass

        # Remove Uninstall Keys
        uninst_paths = [
            (winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Uninstall\SystemDriveAgent'),
            (winreg.HKEY_CURRENT_USER, r'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\SystemDriveAgent'),
            (winreg.HKEY_LOCAL_MACHINE, r'Software\Microsoft\Windows\CurrentVersion\Uninstall\SystemDriveAgent'),
            (winreg.HKEY_LOCAL_MACHINE, r'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\SystemDriveAgent')
        ]
        for hkey, path in uninst_paths:
            try:
                winreg.DeleteKey(hkey, path)
            except Exception:
                pass

        # Delete agent ID file
        if os.path.exists(AGENT_ID_FILE):
            try:
                os.remove(AGENT_ID_FILE)
            except Exception:
                pass

        # Delete installed files and folder
        app_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'SystemMonitorAgent')
        target_exe = os.path.join(app_dir, 'system_monitor_agent.exe')
        target_cfg = os.path.join(app_dir, 'config.json')

        try:
            if os.path.exists(target_exe):
                os.remove(target_exe)
            if os.path.exists(target_cfg):
                os.remove(target_cfg)
            if os.path.exists(app_dir):
                shutil.rmtree(app_dir, ignore_errors=True)
        except Exception:
            pass

        show_message_box(
            "System Drive Agent Uninstalled",
            "System Drive Agent has been uninstalled successfully from this PC.",
            0x00000040
        )

        # Background cleanup in case of locked handles
        cmd = f'timeout /t 1 & rmdir /s /q "{app_dir}"'
        subprocess.Popen(f'cmd /c "{cmd}"', shell=True, creationflags=0x08000000)
    except Exception as e:
        print(f"[UNINSTALL] Notice: {e}")


def handle_uninstall():
    """Perform uninstallation when triggered via Windows Programs and Features (--uninstall flag)."""
    if os.name != 'nt' or '--uninstall' not in sys.argv:
        return False

    res = show_message_box(
        "Uninstall System Drive Agent",
        "Are you sure you want to uninstall System Drive Agent from this PC?",
        0x00000004 | 0x00000020
    )

    if res == 6:  # IDYES = 6
        perform_uninstallation()

    sys.exit(0)
    return True


def install_to_startup():
    """Ask user for confirmation before installing/uninstalling Drive Agent on the PC."""
    if os.name != 'nt':
        return

    try:
        import winreg, shutil, sys, subprocess
        current_exe = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
        if not current_exe.endswith('.exe'):
            return

        app_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'SystemMonitorAgent')
        target_exe = os.path.join(app_dir, 'system_monitor_agent.exe')
        target_cfg = os.path.join(app_dir, 'config.json')

        is_installed_path = (os.path.normpath(current_exe).lower() == os.path.normpath(target_exe).lower())

        # If running installer from outside AppData directory
        if not is_installed_path:
            is_silent = bool('--silent' in sys.argv or '--reconnect' in sys.argv or '--quiet' in sys.argv or '--yes' in sys.argv)
            if not is_silent:
                if os.path.exists(target_exe):
                    # Already installed! Ask user if they want to UNINSTALL
                    res = show_message_box(
                        "System Drive Agent Setup",
                        "System Drive Agent is already installed on this PC.\n\nWould you like to UNINSTALL System Drive Agent from this PC?",
                        0x00000004 | 0x00000020  # Yes / No
                    )
                    if res == 6:  # IDYES -> User wants to UNINSTALL
                        perform_uninstallation()
                    sys.exit(0)
                else:
                    # Not installed yet! Ask user if they want to INSTALL
                    res = show_message_box(
                        "System Drive Agent Setup",
                        "Do you want to install System Drive Agent on this PC?",
                        0x00000004 | 0x00000020  # Yes / No
                    )
                    if res != 6:  # User clicked NO or closed window
                        sys.exit(0)

            # Kill any existing background agent process so file overwrite succeeds cleanly
            kill_running_agent()

            # Copy executable & config.json to AppData
            if not os.path.exists(app_dir):
                os.makedirs(app_dir, exist_ok=True)

            try:
                shutil.copy2(current_exe, target_exe)
                src_cfg = get_config_file_path()
                if os.path.exists(src_cfg):
                    shutil.copy2(src_cfg, target_cfg)
                else:
                    with open(target_cfg, "w") as f:
                        json.dump({"server_url": BASE_URL}, f, indent=2)
            except Exception as e:
                print(f"[INSTALL] File copy warning: {e}")

            # Add to HKCU Windows Startup Registry Key
            run_key_path = r'Software\Microsoft\Windows\CurrentVersion\Run'
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key_path, 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, 'SystemMonitorAgent', 0, winreg.REG_SZ, f'"{target_exe}"')
            winreg.CloseKey(key)

            # Register under Windows Control Panel Programs and Features
            register_uninstaller(target_exe, app_dir)

            # Send immediate initial report to un-blacklist & register device instantly on server
            try:
                aid = get_or_create_agent_id()
                d_init = collect_system_info(aid)
                d_init["is_fresh_installer_run"] = True
                send_report(d_init)
            except Exception as e:
                print(f"[INSTALL] Initial report notice: {e}")

            # Show Success notification
            show_message_box(
                "Drive Agent Installed",
                "Drive Agent has been installed successfully and is now monitoring your PC in the background.",
                0x00000040
            )

            # Launch installed target executable detached in background and exit installer
            try:
                CREATE_NO_WINDOW = 0x08000000
                subprocess.Popen([target_exe, "--reconnect"], cwd=app_dir, creationflags=CREATE_NO_WINDOW)
            except Exception as e:
                print(f"[INSTALL] Launch warning: {e}")
            sys.exit(0)

    except SystemExit:
        sys.exit(0)
    except Exception as e:
        print(f"[INSTALL] Notice: {e}")


def main():
    handle_uninstall()
    install_to_startup()

    agent_id = get_or_create_agent_id()
    print(f"System Monitor Agent")
    print(f"====================")
    print(f"Agent ID : {agent_id}")
    print(f"Server   : {SERVER_URL}")
    print(f"Interval : {REPORT_INTERVAL}s")
    print(f"")

    start_client_fs_monitor()

    while True:
        try:
            data = collect_system_info(agent_id)
            res = send_report(data)
            if res == "STOP":
                print("[TERMINATING] Stop signal received from server. Exiting agent.")
                sys.exit(0)
        except SystemExit:
            sys.exit(0)
        except Exception as e:
            print(f"[ERROR] Failed to collect/send report: {e}")

        time.sleep(REPORT_INTERVAL)


if __name__ == "__main__":
    main()
