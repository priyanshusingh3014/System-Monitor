import os
import time
import threading
import string

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False

# Directories and patterns to ignore across all drives
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
    'screenshot', 'screenshots', 'onedrive', 'build', 'dist', 'scratch',
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


def get_display_name(path):
    """Return filename with drive letter for context e.g. myfile.txt (D:\)"""
    drive, _ = os.path.splitdrive(path)
    filename = os.path.basename(path)
    if drive:
        return f"{filename} ({drive})"
    return filename


class LocalFileSystemHandler(FileSystemEventHandler):
    """Event handler that logs local drive & folder file additions, deletions, renames, and modifications with file sizes."""

    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.last_events = {}
        self.file_sizes = {}

    def _debounce(self, path, event_type):
        key = f"{path}:{event_type}"
        now = time.time()
        if key in self.last_events and (now - self.last_events[key]) < 5.0:
            return True
        self.last_events[key] = now
        return False

    def _get_size_and_store(self, path):
        time.sleep(0.15)
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

        self.callback(
            event=f"File Added: {name}",
            data_size=size_str,
            status="Success",
            status_type="success"
        )

    def on_modified(self, event):
        if event.is_directory or is_ignored(event.src_path):
            return
        try:
            new_size = os.path.getsize(event.src_path)
            old_size = self.file_sizes.get(event.src_path)
            if old_size != new_size:
                self.file_sizes[event.src_path] = new_size
                if self._debounce(event.src_path, 'modified'):
                    return
                name = get_display_name(event.src_path)
                self.callback(
                    event=f"File Modified: {name}",
                    data_size=format_size(new_size),
                    status="Success",
                    status_type="success"
                )
        except Exception:
            pass

    def on_deleted(self, event):
        if event.is_directory or is_ignored(event.src_path):
            return
        filename = os.path.basename(event.src_path)
        # Suppress transient Windows Explorer placeholder deletions during inline rename
        if any(filename.startswith(p) for p in ["New Text Document", "New Microsoft Word", "New Rich Text", "New Bitmap", "New Folder"]):
            return
        if self._debounce(event.src_path, 'deleted'):
            return

        name = get_display_name(event.src_path)
        last_size = self.file_sizes.pop(event.src_path, 0)
        size_str = format_size(last_size)

        self.callback(
            event=f"File Deleted: {name}",
            data_size=size_str,
            status="Success",
            status_type="success"
        )

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

        self.callback(
            event=f"File Renamed: {src_name} ➔ {dest_name}",
            data_size=size_str,
            status="Success",
            status_type="success"
        )


def detect_all_drives():
    """Detect all fixed disk drive roots (C:\, D:\, etc.) and user folders."""
    dirs = []

    # 1. User folders
    user_home = os.path.expanduser("~")
    for folder in ["Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music"]:
        p = os.path.join(user_home, folder)
        if os.path.exists(p):
            dirs.append(p)

    # 2. All mounted drive partitions (e.g. C:\, D:\)
    try:
        import psutil
        for part in psutil.disk_partitions(all=False):
            if part.mountpoint and os.path.exists(part.mountpoint):
                dirs.append(part.mountpoint)
    except Exception:
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                dirs.append(drive)

    # Remove duplicates while preserving order
    return list(dict.fromkeys(dirs))


def start_software_monitor(log_callback):
    """Monitor app installs and uninstalls in real time."""
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
                    print(f"[SOFTWARE MONITOR] Detected App Installed: {app}")
                    log_callback(
                        event=f"App Installed: {app}",
                        data_size="0 KB",
                        status="Success",
                        status_type="success"
                    )

                for app in removed_apps:
                    print(f"[SOFTWARE MONITOR] Detected App Uninstalled: {app}")
                    log_callback(
                        event=f"App Uninstalled: {app}",
                        data_size="0 KB",
                        status="Success",
                        status_type="success"
                    )

                known_apps = current_apps
            except Exception as e:
                print(f"[SOFTWARE MONITOR] Error: {e}")
                time.sleep(10)

    t = threading.Thread(target=run_software_loop, daemon=True)
    t.start()


def start_fs_monitor(log_callback):
    """Start watching all PC drives and user folders in background thread."""
    start_software_monitor(log_callback)

    if not HAS_WATCHDOG:
        print("[FS MONITOR] watchdog module not installed.")
        return None

    watch_dirs = detect_all_drives()
    event_handler = LocalFileSystemHandler(log_callback)
    observer = Observer()

    for d in watch_dirs:
        try:
            observer.schedule(event_handler, d, recursive=True)
            print(f"[FS MONITOR] Watching drive/directory: {d}")
        except Exception as e:
            print(f"[FS MONITOR] Failed to watch {d}: {e}")

    def run():
        observer.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.stop()
        observer.join()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return observer
