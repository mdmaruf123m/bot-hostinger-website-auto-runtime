import os
import re
import json
import time
import shutil
import zipfile
import hashlib
import random
import subprocess
import signal
import threading
import py_compile
import io
from datetime import datetime, timedelta
from collections import defaultdict, deque
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from cryptography.fernet import Fernet, InvalidToken

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from flask import Flask, render_template, render_template_string, request, redirect, url_for, session, jsonify, send_file
from shared_dependencies import prepare_python, prepare_node, prepare_php
from runtime_bootstrap import ensure_node_runtime, ensure_php_runtime

# Firebase Web configuration is public client configuration, not a service-account key.
FIREBASE_WEB_CONFIG = {
    "apiKey": "AIzaSyCaMi91Dp6s7ydrKMO4DwfWcJXMaViG_d4",
    "authDomain": "bot-36914.firebaseapp.com",
    "projectId": "bot-36914",
    "storageBucket": "bot-36914.firebasestorage.app",
    "messagingSenderId": "365558992881",
    "appId": "1:365558992881:web:cb801e341311d54685be50",
}

# ============================================================
# APP SETUP
# ============================================================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(32).hex())
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = True
app.permanent_session_lifetime = timedelta(days=7)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200MB hard ceiling (safety net, not a target)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.environ.get("DATA_DIR", BASE_DIR))
UPLOAD_FOLDER = os.path.join(DATA_DIR, "uploads")
VENV_FOLDER = os.path.join(DATA_DIR, "venvs")
LOG_FOLDER = os.path.join(DATA_DIR, "logs")
DB_FILE = os.path.join(DATA_DIR, "database.json")
DB_LOCK = threading.Lock()

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(VENV_FOLDER, exist_ok=True)
os.makedirs(LOG_FOLDER, exist_ok=True)
APP_ENV_KEY_FILE = os.path.join(DATA_DIR, ".app_env_encryption.key")
APP_ENV_KEY_LOCK = threading.Lock()


ENTRY_FILES_PY = ["main.py", "bot.py", "app.py", "run.py", "start.py"]
ENTRY_FILES_JS = ["index.js", "server.js", "bot.js", "app.js", "main.js"]
ENTRY_FILES_PHP = ["main.php", "bot.php", "app.php", "run.php", "start.php", "worker.php", "index.php"]
SKIP_PROJECT_DIRS = {"venv", ".venv", "node_modules", "vendor", "__pycache__", ".git", ".idea", ".vscode"}

# ============================================================
# IN-MEMORY RUNTIME STATE (per-process, rebuilt from DB when needed)
# ============================================================
processes = {}          # (user, app_name) -> subprocess.Popen
proc_meta = {}          # (user, app_name) -> {"device": str, "started": ts, "restarts": int}
request_windows = defaultdict(lambda: deque())   # (user, app_name) -> deque of timestamps (outbound activity log, self-reported)

STATE_LOCK = threading.Lock()

# ============================================================
# DATABASE HELPERS
# ============================================================
def default_db():
    return {
        "start_times": {},        # "user_app" -> epoch ms
        "app_settings": {},       # "user_app" -> {auto_on, auto_restart, sleep_until, req_hash, device, public_token}
        "app_env": {},            # app settings key -> {variable name: Fernet ciphertext}
        "public_tokens": {},      # token -> "user_app"  (reverse lookup for public share links)
        "config": {
            "max_concurrent_per_user": 3,
            "max_concurrent_global": 25,
            "sleep_after_hours": 4,
            "auto_wake_after_minutes": 20,
            "request_baseline_per_min": 30
        }
    }

def load_db():
    with DB_LOCK:
        if not os.path.exists(DB_FILE):
            data = default_db()
            _write_db(data)
            return data
        try:
            with open(DB_FILE, "r") as f:
                data = json.load(f)
        except Exception:
            data = default_db()
            _write_db(data)
            return data

        # backfill any missing keys so old DB files never break new code
        defaults = default_db()
        for k, v in defaults.items():
            if k not in data:
                data[k] = v
        for k, v in defaults["config"].items():
            if k not in data.get("config", {}):
                data["config"][k] = v

        # Authentication now lives in Firebase. Remove deprecated local password hashes
        # and trusted-admin tokens from the JSON state on its next read.
        removed_legacy_auth = False
        for obsolete_key in ("users", "admin_device_tokens"):
            if obsolete_key in data:
                data.pop(obsolete_key, None)
                removed_legacy_auth = True
        if removed_legacy_auth:
            _write_db(data)
        return data

def _write_db(data):
    temp_db = DB_FILE + ".tmp"
    with open(temp_db, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(temp_db, DB_FILE)

def save_db(data):
    with DB_LOCK:
        _write_db(data)

def get_config(db, key):
    return db.get("config", {}).get(key, default_db()["config"].get(key))

def get_user_concurrency_limit(db, email):
    return get_config(db, "max_concurrent_per_user")

# ============================================================
# SECURITY HELPERS
# ============================================================
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\.]+$")

def is_safe_component(name):
    """Rejects path traversal, absolute paths, empty names."""
    if not name or name in (".", ".."):
        return False
    if "/" in name or "\\" in name:
        return False
    if not SAFE_NAME_RE.match(name):
        return False
    return True

def safe_join(base, *parts):
    """Joins paths and guarantees the result stays inside base. Raises ValueError otherwise."""
    base = os.path.abspath(base)
    target = os.path.abspath(os.path.join(base, *parts))
    if not (target == base or target.startswith(base + os.sep)):
        raise ValueError("Path traversal blocked")
    return target

EMAIL_RE = re.compile(r"^[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+\.[A-Za-z0-9\-.]+$")

def is_valid_email(email):
    return bool(email) and len(email) <= 120 and bool(EMAIL_RE.match(email))

def user_folder_id(email):
    """Emails contain @ and . which aren't safe as raw folder names, so we
    derive a stable, filesystem-safe id from the email for storage purposes."""
    return hashlib.sha256(email.lower().encode()).hexdigest()[:24]

def generate_secret_token(nbytes=24):
    return os.urandom(nbytes).hex()

def _app_env_fernet():
    """Load or create the stable, private key used to encrypt app env values."""
    with APP_ENV_KEY_LOCK:
        try:
            fd = os.open(APP_ENV_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            fd = None
        if fd is not None:
            with os.fdopen(fd, "wb") as key_file:
                key_file.write(Fernet.generate_key())
        os.chmod(APP_ENV_KEY_FILE, 0o600)
        with open(APP_ENV_KEY_FILE, "rb") as key_file:
            return Fernet(key_file.read().strip())

def get_app_env_values(user, name):
    db = load_db()
    encrypted = db.get("app_env", {}).get(app_settings_key(user, name), {})
    if not encrypted:
        return {}
    fernet = _app_env_fernet()
    values = {}
    for variable, ciphertext in encrypted.items():
        try:
            values[variable] = fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise RuntimeError("Saved environment secrets cannot be decrypted. Keep the panel data directory and .app_env_encryption.key together.") from exc
    return values

APP_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
APP_ENV_RESERVED = {"PATH", "HOME", "PWD", "OLDPWD", "PORT", "DATA_DIR", "SECRET_KEY", "PYTHONUNBUFFERED", "NODE_OPTIONS", "NODE_PATH", "COMPOSER_VENDOR_DIR", "COMPOSER_CACHE_DIR"}

def validate_app_env_name(name):
    return bool(APP_ENV_NAME_RE.fullmatch(name or "")) and name.upper() not in APP_ENV_RESERVED and not name.upper().startswith(("LD_", "PYTHON", "PHP_", "COMPOSER_"))

# ============================================================
# DEVICE FINGERPRINT POOL
# One is picked per app per run/restart, stays consistent for that
# run's lifetime (a real device doesn't change mid-session).
# ============================================================
DEVICE_POOL = [
    {"ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "platform": "Windows"},
    {"ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15", "platform": "Windows"},
    {"ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15", "platform": "Mac"},
    {"ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "platform": "Mac"},
    {"ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1", "platform": "iOS"},
    {"ua": "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1", "platform": "iOS"},
    {"ua": "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36", "platform": "Android"},
    {"ua": "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36", "platform": "Android"},
    {"ua": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36", "platform": "Android"},
    {"ua": "Mozilla/5.0 (Linux; Android 12; CPH2449) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36", "platform": "Android"},
    {"ua": "Dalvik/2.1.0 (Linux; U; Android 14; SM-S918B Build/UP1A.231005.007)", "platform": "Android-App"},
    {"ua": "Dalvik/2.1.0 (Linux; U; Android 13; Pixel 7 Build/TQ3A.230901.001)", "platform": "Android-App"},
    {"ua": "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0", "platform": "Windows"},
    {"ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "platform": "Linux"},
    {"ua": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0", "platform": "Linux"},
    {"ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0", "platform": "Windows"},
    {"ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1", "platform": "iOS"},
    {"ua": "Mozilla/5.0 (Linux; Android 11; SM-G975F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36", "platform": "Android"},
    {"ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 OPR/109.0.0.0", "platform": "Mac"},
    {"ua": "Mozilla/5.0 (Linux; Android 14; SM-A546E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36", "platform": "Android"},
]

def assign_device(seed_key):
    """Deterministic-but-random pick, stable per app-run via seed_key (user_appname_starttime)."""
    rnd = random.Random(seed_key)
    device = rnd.choice(DEVICE_POOL)
    return device

def device_env_vars(device):
    """Environment variables injected into a child process so libraries that
    respect them (requests default headers via env, custom bots reading os.environ)
    can pick a consistent identity. The app's own code decides whether to use these."""
    return {
        "BOT_USER_AGENT": device["ua"],
        "BOT_DEVICE_PLATFORM": device["platform"],
    }

# ============================================================
# ADAPTIVE PROTECTION (no hard caps — baseline + spike detection)
# Goal: don't throttle normal traffic. Only step in when an app's
# self-reported activity deviates sharply from its own recent baseline.
# ============================================================
class AppActivityTracker:
    """Tracks a rolling window of activity per app and exposes a soft
    'should_slow_down' signal plus a circuit-breaker for repeated crashes."""
    def __init__(self):
        self.windows = defaultdict(lambda: deque())   # key -> timestamps
        self.crash_log = defaultdict(lambda: deque())  # key -> crash timestamps
        self.paused_until = {}                         # key -> epoch seconds
        self.lock = threading.Lock()

    def ping(self, key):
        now = time.time()
        with self.lock:
            dq = self.windows[key]
            dq.append(now)
            cutoff = now - 60
            while dq and dq[0] < cutoff:
                dq.popleft()

    def current_rate(self, key):
        with self.lock:
            return len(self.windows[key])

    def suggested_delay(self, key, baseline_per_min):
        """Returns a small random delay (seconds) to space out requests.
        Grows only if recent rate is far above the app's own historical baseline."""
        rate = self.current_rate(key)
        base_delay = random.uniform(0.4, 1.8)
        if baseline_per_min and rate > baseline_per_min * 3:
            # sharp spike vs configured baseline -> add gentle extra spacing
            return base_delay + random.uniform(1.5, 4.0)
        return base_delay

    def is_paused(self, key):
        return time.time() < self.paused_until.get(key, 0)

    def register_crash(self, key, cooldown_seconds=300):
        now = time.time()
        with self.lock:
            dq = self.crash_log[key]
            dq.append(now)
            cutoff = now - 600
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= 4:
                self.paused_until[key] = now + cooldown_seconds
                return True
        return False

    def clear_pause(self, key):
        self.paused_until.pop(key, None)
        self.crash_log[key].clear()

activity = AppActivityTracker()

# ============================================================
# VALIDATION (pre-deploy syntax check)
# ============================================================
def validate_python_file(path):
    try:
        py_compile.compile(path, doraise=True)
        return {"ok": True}
    except py_compile.PyCompileError as e:
        msg = str(e.exc_value) if hasattr(e, "exc_value") else str(e)
        line = None
        m = re.search(r"line (\d+)", msg)
        if m:
            line = int(m.group(1))
        return {"ok": False, "line": line, "error": msg.strip()}
    except SyntaxError as e:
        return {"ok": False, "line": e.lineno, "error": f"{e.msg}"}
    except Exception as e:
        return {"ok": False, "line": None, "error": str(e)}

def validate_js_file(path):
    node_bin = shutil.which("node")
    if not node_bin:
        try:
            node_bin, _ = ensure_node_runtime(lambda _message: None)
        except Exception as exc:
            return {"ok": True, "skipped": True, "note": f"Node.js unavailable for validation: {exc}"}
    try:
        result = subprocess.run([node_bin, "--check", path], capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return {"ok": True}
        err = result.stderr.strip()
        line = None
        # Node's --check prints "path/to/file.js:LINE" as the very first line of stderr
        first_line = err.splitlines()[0] if err else ""
        m = re.search(re.escape(path) + r":(\d+)", first_line)
        if m:
            line = int(m.group(1))
        return {"ok": False, "line": line, "error": err}
    except Exception as e:
        return {"ok": False, "line": None, "error": str(e)}

def find_project(extract_dir):
    """Find the most likely project root and executable entry in an extracted ZIP."""
    candidates = []
    manifests = [
        ("composer.json", "php"), ("package.json", "node"),
        ("requirements.txt", "python"), ("pyproject.toml", "python"),
        ("setup.py", "python"),
    ]
    entries = [(n, "python") for n in ENTRY_FILES_PY]
    entries += [(n, "node") for n in ENTRY_FILES_JS]
    entries += [(n, "php") for n in ENTRY_FILES_PHP]
    for root, dirs, files in os.walk(extract_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_PROJECT_DIRS)
        file_set = set(files)
        manifest_kind = next((kind for manifest, kind in manifests if manifest in file_set), None)
        entry = None
        # Respect a package.json's explicit start command/main before generic names.
        if "package.json" in file_set:
            try:
                with open(os.path.join(root, "package.json"), encoding="utf-8") as f:
                    pkg = json.load(f)
                main = pkg.get("main")
                if isinstance(main, str) and os.path.isfile(os.path.join(root, main)):
                    entry = (main, "node")
                elif isinstance(pkg.get("scripts", {}).get("start"), str):
                    entry = ("__npm_start__", "node")
            except Exception:
                pass
        if not entry:
            for name, kind in entries:
                if name in file_set and (manifest_kind is None or kind == manifest_kind):
                    entry = (name, kind)
                    break
        if entry:
            depth = os.path.relpath(root, extract_dir).count(os.sep)
            # An explicit manifest is stronger evidence than a filename alone.
            entry_rank = entries.index((entry[0], entry[1])) if (entry[0], entry[1]) in entries else -1
            score = (depth, 0 if manifest_kind == entry[1] else 1, entry_rank)
            candidates.append((score, root, entry[0], entry[1]))
    if not candidates:
        return None, None, None
    _, project_root, entry, kind = min(candidates, key=lambda item: item[0])
    return os.path.normpath(entry), kind, project_root

def find_entry_file(extract_dir):
    entry, kind, _ = find_project(extract_dir)
    return entry, kind

def validate_php_file(path):
    php_bin = shutil.which("php")
    if not php_bin:
        try:
            php_bin = ensure_php_runtime(lambda _message: None)
        except Exception as exc:
            return {"ok": True, "skipped": True, "note": f"PHP CLI unavailable for validation: {exc}"}
    try:
        result = subprocess.run([php_bin, "-l", path], capture_output=True, text=True, timeout=15)
        return {"ok": result.returncode == 0, "error": result.stderr.strip() or result.stdout.strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def validate_project(extract_dir):
    """Validate supported source files under the detected project root."""
    entry, kind, project_root = find_project(extract_dir)
    if not entry:
        return {"ok": False, "error": f"Entry file not found. Supported entry files: {', '.join(ENTRY_FILES_PY + ENTRY_FILES_JS + ENTRY_FILES_PHP)}"}

    problems = []
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in SKIP_PROJECT_DIRS]
        for fname in files:
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, project_root)
            if fname.endswith(".py"):
                res = validate_python_file(fpath)
                if not res["ok"]:
                    problems.append({"file": rel, "line": res.get("line"), "error": res.get("error")})
            elif fname.endswith(".js") and kind == "node":
                res = validate_js_file(fpath)
                if not res.get("ok") and not res.get("skipped"):
                    problems.append({"file": rel, "line": res.get("line"), "error": res.get("error")})
            elif fname.endswith(".php") and kind == "php":
                res = validate_php_file(fpath)
                if not res.get("ok") and not res.get("skipped"):
                    problems.append({"file": rel, "line": None, "error": res.get("error")})

    if problems:
        return {"ok": False, "entry": entry, "kind": kind, "project_root": os.path.relpath(project_root, extract_dir), "problems": problems}
    return {"ok": True, "entry": entry, "kind": kind, "project_root": os.path.relpath(project_root, extract_dir)}

# ============================================================
# SMART REQUIREMENTS INSTALL
# - one virtualenv per app
# - only installs what's missing / changed (hash of requirements.txt)
# - skips entirely if nothing changed since last successful install
# ============================================================
def venv_path_for(user, app_name):
    return safe_join(VENV_FOLDER, f"{user}__{app_name}")

def dependency_cache_for(user, app_name):
    """Return a stable but app-isolated runtime dependency cache directory."""
    cache_id = hashlib.sha256(f"{user}\0{app_name}".encode("utf-8")).hexdigest()
    return os.path.join(DATA_DIR, "runtime_dependencies", cache_id)

def file_hash(path):
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def get_installed_packages(python_bin):
    try:
        result = subprocess.run([python_bin, "-m", "pip", "list", "--format=freeze"],
                                 capture_output=True, text=True, timeout=30)
        installed = {}
        for line in result.stdout.splitlines():
            if "==" in line:
                name, ver = line.split("==", 1)
                installed[name.lower()] = ver
        return installed
    except Exception:
        return {}

def parse_requirements(req_path):
    reqs = []
    with open(req_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            reqs.append(line)
    return reqs

def ensure_venv(venv_dir):
    if not os.path.exists(os.path.join(venv_dir, "bin", "python")):
        subprocess.run(["python3", "-m", "venv", venv_dir], check=True, timeout=120)
    return os.path.join(venv_dir, "bin", "python")

def smart_install_python(extract_dir, venv_dir, log_callback):
    req_path = os.path.join(extract_dir, "requirements.txt")
    source_manifest = next((os.path.join(extract_dir, name) for name in ("pyproject.toml", "setup.py")
                            if os.path.isfile(os.path.join(extract_dir, name))), None)
    state_path = os.path.join(venv_dir, ".install_state.json")
    if not os.path.exists(req_path) and not source_manifest:
        log_callback("No Python dependency manifest found; skipping installation.\n")
        return {"ok": True, "skipped": True}
    manifest_path = req_path if os.path.exists(req_path) else source_manifest
    current_hash = file_hash(manifest_path)
    prev_state = {}
    if os.path.exists(state_path):
        try:
            with open(state_path) as f:
                prev_state = json.load(f)
        except Exception:
            prev_state = {}

    if prev_state.get("hash") == current_hash and prev_state.get("last_ok"):
        log_callback(f"No changes in {os.path.basename(manifest_path)}; skipping Python dependency install.\n")
        return {"ok": True, "skipped": True}
    try:
        python_bin = ensure_venv(venv_dir)
    except Exception as e:
        log_callback(f"Error creating virtual environment: {e}\n")
        return {"ok": False, "error": str(e)}
    command = [python_bin, "-m", "pip", "install", "--disable-pip-version-check"]
    command += ["-r", "requirements.txt"] if os.path.exists(req_path) else ["."]
    log_callback(f"Installing Python dependencies from {os.path.basename(manifest_path)}...\n")
    try:
        result = subprocess.run(command, cwd=extract_dir, capture_output=True, text=True, timeout=600)
        log_callback(result.stdout[-3000:] + "\n")
        ok = result.returncode == 0
        if not ok:
            log_callback("PIP INSTALL ERROR:\n" + result.stderr[-2000:] + "\n")
        with open(state_path, "w") as f:
            json.dump({"hash": current_hash, "last_ok": ok}, f)
        return {"ok": ok, **({} if ok else {"error": result.stderr[-500:]})}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Python dependency installation timed out"}
    except Exception as e:
        log_callback(f"Install error: {e}\n")
        return {"ok": False, "error": str(e)}

def smart_install_node(extract_dir, log_callback):
    pkg_path = os.path.join(extract_dir, "package.json")
    if not os.path.exists(pkg_path):
        log_callback("No package.json found; skipping npm install.\n")
        return {"ok": True, "skipped": True}
    npm_bin = shutil.which("npm")
    if not npm_bin:
        log_callback("npm is not available on this server; skipping Node dependency installation.\n")
        return {"ok": False, "error": "npm not found"}

    state_path = os.path.join(extract_dir, ".npm_install_state.json")
    lock_paths = [os.path.join(extract_dir, n) for n in ("package-lock.json", "npm-shrinkwrap.json")]
    current_hash = hashlib.sha256("".join(file_hash(p) or "" for p in [pkg_path] + lock_paths).encode()).hexdigest()
    prev_state = {}
    if os.path.exists(state_path):
        try:
            with open(state_path) as f:
                prev_state = json.load(f)
        except Exception:
            prev_state = {}

    if prev_state.get("hash") == current_hash and prev_state.get("last_ok"):
        log_callback("No changes in package.json; skipping npm install.\n")
        return {"ok": True, "skipped": True}

    log_callback("Running npm install...\n")
    try:
        result = subprocess.run([npm_bin, "install", "--no-audit", "--no-fund"],
                                 cwd=extract_dir, capture_output=True, text=True, timeout=600)
        log_callback(result.stdout[-3000:] + "\n")
        ok = result.returncode == 0
        if not ok:
            log_callback("NPM INSTALL WARNING:\n" + result.stderr[-2000:] + "\n")
        with open(state_path, "w") as f:
            json.dump({"hash": current_hash, "last_ok": ok}, f)
        return {"ok": ok}
    except subprocess.TimeoutExpired:
        log_callback("npm install timed out.\n")
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        log_callback(f"npm install error: {e}\n")
        return {"ok": False, "error": str(e)}

def smart_install_php(extract_dir, log_callback):
    composer_json = os.path.join(extract_dir, "composer.json")
    if not os.path.isfile(composer_json):
        log_callback("No composer.json found; skipping Composer installation.\n")
        return {"ok": True, "skipped": True}
    php_bin, composer_bin = shutil.which("php"), shutil.which("composer")
    if not php_bin:
        return {"ok": False, "error": "PHP CLI is not installed in the hosting runtime"}
    if not composer_bin:
        return {"ok": False, "error": "Composer is not installed in the hosting runtime"}
    state_path = os.path.join(extract_dir, ".composer_install_state.json")
    lock_path = os.path.join(extract_dir, "composer.lock")
    current_hash = hashlib.sha256(((file_hash(composer_json) or "") + (file_hash(lock_path) or "")).encode()).hexdigest()
    try:
        with open(state_path) as f:
            prev_state = json.load(f)
    except Exception:
        prev_state = {}
    if prev_state.get("hash") == current_hash and prev_state.get("last_ok"):
        log_callback("No changes in composer.json; skipping Composer install.\n")
        return {"ok": True, "skipped": True}
    try:
        log_callback("Running composer install...\n")
        result = subprocess.run([composer_bin, "install", "--no-interaction", "--no-dev", "--prefer-dist"],
                                cwd=extract_dir, capture_output=True, text=True, timeout=600)
        log_callback(result.stdout[-3000:] + "\n")
        ok = result.returncode == 0
        if not ok:
            log_callback("COMPOSER INSTALL ERROR:\n" + result.stderr[-2000:] + "\n")
        with open(state_path, "w") as f:
            json.dump({"hash": current_hash, "last_ok": ok}, f)
        return {"ok": ok, **({} if ok else {"error": result.stderr[-500:]})}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Composer installation timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ============================================================
# APP SETTINGS (per user_app: auto_on, auto_restart, sleep state, device)
# ============================================================
def app_settings_key(user, name):
    return f"{user}_{name}"

def get_app_settings(db, user, name):
    key = app_settings_key(user, name)
    settings = db.setdefault("app_settings", {}).setdefault(key, {})
    settings.setdefault("auto_on", False)
    settings.setdefault("auto_restart", False)
    settings.setdefault("sleep_until", 0)
    settings.setdefault("offline_at", 0)
    settings.setdefault("device_ua", None)
    settings.setdefault("device_platform", None)
    settings.setdefault("manual_stop", False)
    settings.setdefault("public_token", None) # secret token for the no-login share link
    return settings

def ensure_public_token(db, user, name):
    settings = get_app_settings(db, user, name)
    if not settings.get("public_token"):
        token = generate_secret_token()
        settings["public_token"] = token
        db.setdefault("public_tokens", {})[token] = app_settings_key(user, name)
        save_db(db)
    return settings["public_token"]

def revoke_public_token(db, user, name):
    settings = get_app_settings(db, user, name)
    old = settings.get("public_token")
    if old:
        db.get("public_tokens", {}).pop(old, None)
    settings["public_token"] = None
    save_db(db)

def app_dirs(user, name):
    app_dir = safe_join(UPLOAD_FOLDER, user, name)
    extract_dir = os.path.join(app_dir, "extracted")
    log_path = os.path.join(app_dir, "logs.txt")
    return app_dir, extract_dir, log_path

def append_log(log_path, text):
    try:
        with open(log_path, "a", encoding="utf-8", errors="ignore") as f:
            f.write(text)
    except Exception:
        pass

def trim_log_if_needed(log_path, max_bytes=500_000):
    try:
        if os.path.exists(log_path) and os.path.getsize(log_path) > max_bytes:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(-max_bytes // 2, os.SEEK_END)
                tail = f.read()
            with open(log_path, "w", encoding="utf-8", errors="ignore") as f:
                f.write("...[log truncated]...\n" + tail)
    except Exception:
        pass

def count_running_for_user(user):
    return sum(1 for (u, _), p in processes.items() if u == user and p.poll() is None)

def count_running_global():
    return sum(1 for p in processes.values() if p.poll() is None)

def start_app_process(user, name, db):
    """Core launcher: validates, installs deps if needed, assigns a device
    fingerprint, and starts the subprocess. Returns dict with ok/error info."""
    app_dir, extract_dir, log_path = app_dirs(user, name)
    key = (user, name)

    if key in processes and processes[key].poll() is None:
        return {"ok": True, "already_running": True}

    if activity.is_paused(f"{user}_{name}"):
        return {"ok": False, "error": "This app is currently in cooldown because of repeated crashes. Try again later."}

    entry, kind, project_root = find_project(extract_dir)
    if not entry:
        msg = f"Entry file not found. Supported entry files: {', '.join(ENTRY_FILES_PY + ENTRY_FILES_JS + ENTRY_FILES_PHP)}"
        append_log(log_path, f"[{datetime.now()}] START FAILED: {msg}\n")
        return {"ok": False, "error": msg}

    settings = get_app_settings(db, user, name)

    # concurrency guards (soft, configurable — not meant to block normal usage)
    max_global = get_config(db, "max_concurrent_global")
    max_user = get_user_concurrency_limit(db, user)
    if count_running_for_user(user) >= max_user:
        return {"ok": False, "error": f"You can run a maximum of {max_user} apps at once. Stop one app and try again."}
    if count_running_global() >= max_global:
        return {"ok": False, "error": "The server is currently at maximum capacity. Try again later."}

    append_log(log_path, f"\n[{datetime.now()}] ===== Deploy start =====\n")

    def log_cb(text):
        append_log(log_path, text)

    app_dependency_cache = dependency_cache_for(user, name)
    runtime_env = {}
    if kind == "python":
        if not shutil.which("python3"):
            return {"ok": False, "error": "Python 3 is not installed in the hosting runtime"}
        python_bin, install_res = prepare_python(project_root, app_dependency_cache, log_cb)
        if not install_res.get("ok"):
            return {"ok": False, "error": install_res.get("error", "Python dependency installation failed")}
        cmd = [python_bin, entry]
    elif kind == "node":
        node_bin = shutil.which("node")
        npm_bin = shutil.which("npm")
        if not node_bin or not npm_bin:
            try:
                node_bin, npm_bin = ensure_node_runtime(log_cb)
                os.environ["PATH"] = os.path.dirname(node_bin) + os.pathsep + os.environ.get("PATH", "")
            except Exception as e:
                message = f"Could not install portable Node.js on this host: {e}. The host must allow outbound HTTPS, provide writable persistent storage, and support Linux {__import__('platform').machine()}."
                append_log(log_path, message + "\n")
                return {"ok": False, "error": message}
        runtime_env, install_res = prepare_node(project_root, app_dependency_cache, log_cb)
        if not install_res.get("ok"):
            return {"ok": False, "error": install_res.get("error", "Node.js dependency installation failed")}
        if entry == "__npm_start__":
            npm_bin = shutil.which("npm") or locals().get("npm_bin")
            if not npm_bin:
                return {"ok": False, "error": "npm is not installed in the hosting runtime"}
            cmd = [npm_bin, "start"]
        else:
            cmd = [node_bin, entry]
    else:
        php_bin = shutil.which("php")
        if not php_bin:
            try:
                php_bin = ensure_php_runtime(log_cb)
            except Exception as e:
                message = f"Could not install portable PHP CLI on this host: {e}. The host must allow outbound HTTPS, provide writable persistent storage, and support Linux {__import__('platform').machine()}."
                append_log(log_path, message + "\n")
                return {"ok": False, "error": message}
        runtime_env, install_res = prepare_php(project_root, app_dependency_cache, log_cb)
        if not install_res.get("ok"):
            return {"ok": False, "error": install_res.get("error", "PHP dependency installation failed")}
        cmd = [php_bin, entry]

    # device fingerprint: stable for this run
    seed_key = f"{user}_{name}_{int(time.time())}"
    device = assign_device(seed_key)
    settings["device_ua"] = device["ua"]
    settings["device_platform"] = device["platform"]
    settings["manual_stop"] = False

    env = os.environ.copy()
    env.update(device_env_vars(device))
    env.update(runtime_env)
    env["PYTHONUNBUFFERED"] = "1"
    try:
        env.update(get_app_env_values(user, name))
    except RuntimeError as e:
        append_log(log_path, f"Environment secret error: {e}\n")
        return {"ok": False, "error": str(e)}

    trim_log_if_needed(log_path)
    log_file = open(log_path, "a", encoding="utf-8", errors="ignore")
    try:
        proc = subprocess.Popen(cmd, cwd=project_root, stdout=log_file, stderr=log_file,
                                text=True, env=env, start_new_session=(os.name == "posix"))
    except Exception as e:
        append_log(log_path, f"Process start error: {e}\n")
        return {"ok": False, "error": str(e)}

    processes[key] = proc
    proc_meta[key] = {"device": device, "started": time.time(), "restarts": proc_meta.get(key, {}).get("restarts", 0)}

    # Catch syntax/runtime failures that exit immediately so the dashboard does
    # not claim a crashed bot was successfully started.
    time.sleep(0.35)
    exit_code = proc.poll()
    if exit_code is not None:
        processes.pop(key, None)
        proc_meta.pop(key, None)
        db.get("start_times", {}).pop(app_settings_key(user, name), None)
        save_db(db)
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as log_file:
                tail = log_file.read()[-1200:].strip()
        except OSError:
            tail = ""
        append_log(log_path, f"[{datetime.now()}] Process exited immediately with code {exit_code}.\n")
        message = f"Bot stopped immediately (exit code {exit_code}). Check Logs for the error."
        if tail:
            message += "\n" + tail[-700:]
        return {"ok": False, "error": message}

    now_ms = int(time.time() * 1000)
    db["start_times"][app_settings_key(user, name)] = now_ms
    settings["offline_at"] = 0
    settings["sleep_until"] = 0
    save_db(db)

    append_log(log_path, f"[{datetime.now()}] Started with entry={entry}, device={device['platform']}\n")
    return {"ok": True, "install": install_res, "device": device["platform"]}

def stop_app_process(user, name, db, manual=True):
    key = (user, name)
    p = processes.get(key)
    if p and p.poll() is None:
        try:
            if os.name == "posix":
                os.killpg(p.pid, signal.SIGTERM)
            else:
                p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(p.pid, signal.SIGKILL)
                else:
                    p.kill()
        except Exception:
            pass
    if key in processes:
        del processes[key]

    settings = get_app_settings(db, user, name)
    settings["manual_stop"] = manual
    tkey = app_settings_key(user, name)
    if tkey in db["start_times"]:
        del db["start_times"][tkey]
    save_db(db)

# ============================================================
# BACKGROUND SCHEDULER: sleep-after-4h, conditional auto-wake,
# crash detection -> circuit breaker, auto-restart if enabled
# ============================================================
def scheduler_loop():
    while True:
        try:
            db = load_db()
            sleep_hours = get_config(db, "sleep_after_hours")
            wake_minutes = get_config(db, "auto_wake_after_minutes")
            now = time.time()
            changed = False

            for tkey, started_ms in list(db.get("start_times", {}).items()):
                if "_" not in tkey:
                    continue
                user, name = tkey.split("_", 1)
                key = (user, name)
                settings = get_app_settings(db, user, name)
                started_s = started_ms / 1000.0

                p = processes.get(key)
                is_running = bool(p and p.poll() is None)

                if is_running and (now - started_s) >= sleep_hours * 3600:
                    _, _, log_path = app_dirs(user, name)
                    append_log(log_path, f"[{datetime.now()}] Auto-sleep: {sleep_hours}h reached, stopping.\n")
                    stop_app_process(user, name, db, manual=False)
                    settings = get_app_settings(db, user, name)
                    settings["offline_at"] = now
                    changed = True

                if p and not is_running and key in processes:
                    # process died on its own (crash)
                    tracker_key = f"{user}_{name}"
                    tripped = activity.register_crash(tracker_key)
                    del processes[key]
                    if tkey in db["start_times"]:
                        del db["start_times"][tkey]
                    settings["offline_at"] = now
                    changed = True
                    _, _, log_path = app_dirs(user, name)
                    if tripped:
                        append_log(log_path, f"[{datetime.now()}] Repeated crashes detected, cooling down for 5 min.\n")
                    elif settings.get("auto_restart") and not settings.get("manual_stop"):
                        append_log(log_path, f"[{datetime.now()}] Crash detected, auto-restart is ON, restarting.\n")
                        start_app_process(user, name, db)

                if not is_running and settings.get("offline_at") and settings.get("auto_on") and not settings.get("manual_stop"):
                    if (now - settings["offline_at"]) >= wake_minutes * 60 and not activity.is_paused(f"{user}_{name}"):
                        _, _, log_path = app_dirs(user, name)
                        append_log(log_path, f"[{datetime.now()}] Auto-On: {wake_minutes}-minute cooldown finished; restarting.\n")
                        start_app_process(user, name, db)
                        changed = True

            if changed:
                save_db(db)
        except Exception:
            pass
        time.sleep(30)

def resume_apps_after_restart():
    """On server process restart, previously-running apps are NOT force-resumed
    automatically unless their auto_restart/auto_on flags say so — avoids
    surprising behavior, but we do clear stale start_times so the dashboard
    doesn't show a 'running' app that no longer has a process."""
    db = load_db()
    changed = False
    for tkey in list(db.get("start_times", {}).keys()):
        if "_" not in tkey:
            continue
        user, name = tkey.split("_", 1)
        key = (user, name)
        if key not in processes or processes[key].poll() is not None:
            del db["start_times"][tkey]
            settings = get_app_settings(db, user, name)
            settings["offline_at"] = time.time()
            changed = True
    if changed:
        save_db(db)

_scheduler_started = False
def ensure_scheduler():
    global _scheduler_started
    if not _scheduler_started:
        _scheduler_started = True
        resume_apps_after_restart()
        t = threading.Thread(target=scheduler_loop, daemon=True)
        t.start()

# ============================================================
# LOGIN PAGE
# ============================================================
PUBLIC_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ name or "Not found" }} | MY HOST Public</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script>
    <style>
        :root { --bg: #050217; --primary: #0D0782; --card: rgba(16,13,43,.92); --text: #f4f1ff; --success:#a49eff; }
        * { box-sizing: border-box; }
        body { background: radial-gradient(circle at 12% 0%, rgba(13,7,130,.78) 0, transparent 42%), linear-gradient(145deg,#0D0782 0%,#050217 68%); background-attachment:fixed; color: var(--text); font-family: 'Segoe UI', sans-serif; margin: 0; padding: 15px; }
        .card { background: var(--card); border-radius: 18px; padding: 20px; border: 1px solid rgba(255,255,255,0.08); max-width: 500px; margin: 20px auto; }
        h2 { color: var(--primary); font-size: 18px; margin-top: 0; }
        .status-pill { font-size: 11px; padding: 5px 12px; border-radius: 20px; border: 1px solid; }
        .status-run { color: var(--success); border-color: #625bd2; background: rgba(87,78,210,.18); }
        .status-off { color: #ef4444; border-color: #ef4444; background: rgba(239,68,68,0.1); }
        .btn { padding: 12px; border-radius: 10px; border: none; font-weight: bold; cursor: pointer; font-size: 14px; width: 100%; margin-top: 10px; color: #fff; }
        .btn-primary { background: linear-gradient(135deg, #0D0782, #463be0); color: #fff; }
        .btn-danger { background: #ef4444; }
        #logBox { background: #08061b; color: #c6c1ff; font-family: monospace; font-size: 11px; padding: 10px; border-radius: 8px; max-height: 300px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; margin-top: 15px; }
        .small-text { font-size: 11px; opacity: 0.6; margin-top: 10px; }
    </style>
</head>
<body>
    {% if valid %}
    <div class="card">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <h2><i class="fa-solid fa-server"></i> {{ name }}</h2>
            <span class="status-pill {% if running %}status-run{% else %}status-off{% endif %}" id="statusPill">{% if running %}RUNNING{% else %}OFFLINE{% endif %}</span>
        </div>
        <p class="small-text">Public control link — start, stop, and view logs. Log in to edit files.</p>
        <button class="btn btn-primary" id="startBtn" onclick="doAction('start')" {% if running %}style="display:none;"{% endif %}><i class="fa-solid fa-play"></i> Start</button>
        <button class="btn btn-danger" id="stopBtn" onclick="doAction('stop')" {% if not running %}style="display:none;"{% endif %}><i class="fa-solid fa-stop"></i> Stop</button>
        <div id="logBox">Loading logs...</div>
    </div>
    <script>
        const token = "{{ token }}";
        function refresh() {
            fetch('/public/' + token + '/status').then(r => r.json()).then(res => {
                if (!res.ok) return;
                document.getElementById('logBox').textContent = res.log || '(No logs available)';
                const pill = document.getElementById('statusPill');
                pill.textContent = res.running ? 'RUNNING' : 'OFFLINE';
                pill.className = 'status-pill ' + (res.running ? 'status-run' : 'status-off');
                document.getElementById('startBtn').style.display = res.running ? 'none' : 'block';
                document.getElementById('stopBtn').style.display = res.running ? 'block' : 'none';
            });
        }
        function doAction(action) {
            fetch('/public/' + token + '/' + action, { method: 'POST' }).then(r => r.json()).then(res => {
                if (!res.ok) Swal.fire('Error', res.error || 'Action failed', 'error');
                setTimeout(refresh, 500);
            });
        }
        refresh();
        setInterval(refresh, 4000);
    </script>
    {% else %}
    <div class="card">
        <h2><i class="fa-solid fa-triangle-exclamation"></i> Link Invalid</h2>
        <p class="small-text">This public link has expired or was never valid.</p>
    </div>
    {% endif %}
</body>
</html>
'''

# ============================================================
# ROUTES — AUTH
# ============================================================
@app.before_request
def _boot():
    ensure_scheduler()

class FirebaseAuthError(Exception):
    pass


def firebase_identity_request(method, payload):
    api_key = FIREBASE_WEB_CONFIG["apiKey"]
    url = f"https://identitytoolkit.googleapis.com/v1/{method}?key={quote(api_key)}"
    req = Request(url, data=json.dumps(payload).encode("utf-8"),
                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
            message = detail.get("error", {}).get("message", "Firebase request failed")
        except Exception:
            message = "Firebase request failed"
        raise FirebaseAuthError(message) from exc
    except (URLError, TimeoutError) as exc:
        raise FirebaseAuthError("Could not connect to Firebase. Please try again.") from exc


def _firestore_document_url(uid):
    project = quote(FIREBASE_WEB_CONFIG["projectId"], safe="")
    return (f"https://firestore.googleapis.com/v1/projects/{project}/databases/"
            f"(default)/documents/users/{quote(uid, safe='')}")


def firebase_get_profile(uid, id_token):
    req = Request(_firestore_document_url(uid), headers={"Authorization": f"Bearer {id_token}"})
    try:
        with urlopen(req, timeout=12) as response:
            doc = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise FirebaseAuthError("Could not read the user's Firestore profile.") from exc
    except (URLError, TimeoutError) as exc:
        raise FirebaseAuthError("Could not connect to Firestore to read the user's profile.") from exc
    fields = doc.get("fields", {})
    return {key: value.get("stringValue", "") for key, value in fields.items()}


def firebase_create_profile(uid, email, name, id_token):
    payload = {"fields": {
        "uid": {"stringValue": uid},
        "email": {"stringValue": email},
        "name": {"stringValue": name or email.split("@", 1)[0]},
        "status": {"stringValue": "Active"},
    }}
    req = Request(_firestore_document_url(uid), data=json.dumps(payload).encode("utf-8"),
                  headers={"Authorization": f"Bearer {id_token}", "Content-Type": "application/json"},
                  method="PATCH")
    try:
        with urlopen(req, timeout=12):
            return
    except HTTPError as exc:
        raise FirebaseAuthError("Firebase Auth succeeded, but Firestore could not save the user profile. Check Firestore rules.") from exc
    except (URLError, TimeoutError) as exc:
        raise FirebaseAuthError("Firebase Auth succeeded, but Firestore profile sync failed.") from exc


@app.route("/login", methods=["GET"])
def login():
    if require_login():
        return redirect(url_for("index"))
    return render_template("login.html", firebase_config=FIREBASE_WEB_CONFIG)


@app.route("/register", methods=["GET"])
def register():
    if require_login():
        return redirect(url_for("index"))
    return render_template("register.html", firebase_config=FIREBASE_WEB_CONFIG)


@app.route("/auth/session", methods=["POST"])
def firebase_session():
    body = request.get_json(silent=True) or {}
    id_token = body.get("idToken", "")
    if not isinstance(id_token, str) or len(id_token) < 100:
        return jsonify({"ok": False, "error": "Firebase sign-in token is missing or invalid."}), 400
    try:
        lookup = firebase_identity_request("accounts:lookup", {"idToken": id_token})
        users = lookup.get("users", [])
        if not users:
            return jsonify({"ok": False, "error": "Firebase account was not found."}), 401
        user = users[0]
        if user.get("disabled"):
            return jsonify({"ok": False, "error": "This account has been disabled."}), 403
        uid = user.get("localId", "")
        email = user.get("email", "").strip().lower()
        if not uid or not is_valid_email(email):
            return jsonify({"ok": False, "error": "Firebase did not return a valid account."}), 401
        display_name = user.get("displayName") or email.split("@", 1)[0]
        profile = firebase_get_profile(uid, id_token)
        if profile and str(profile.get("status", "Active")).lower() in {"blocked", "disabled"}:
            return jsonify({"ok": False, "error": "This account is blocked. Contact the administrator."}), 403
        if not profile:
            firebase_create_profile(uid, email, display_name, id_token)
        session.clear()
        session.permanent = True
        session["username"] = email
        session["uid"] = uid
        session["user_folder"] = user_folder_id(email)
        return jsonify({"ok": True, "redirect": url_for("index")})
    except FirebaseAuthError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 401
    except Exception:
        app.logger.exception("Unexpected error during Firebase session exchange")
        return jsonify({"ok": False, "error": "The sign-in succeeded, but the panel could not finish syncing your Firebase profile. Check BotKeep logs and Firestore access."}), 500


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login", logged_out="1"))


def require_login():
    return bool(session.get("username") and session.get("user_folder"))


def current_user_key():
    """Returns the stable per-email directory key for the signed-in Firebase user."""
    return session.get("user_folder") or user_folder_id(session.get("username", ""))

# ============================================================
# ROUTES — DASHBOARD
# ============================================================
@app.route("/api/apps")
def api_apps():
    if not require_login():
        return jsonify({"ok": False, "apps": []}), 401
    user_name = current_user_key()
    user_dir = safe_join(UPLOAD_FOLDER, user_name)
    os.makedirs(user_dir, exist_ok=True)
    db = load_db()
    apps_list = []
    for name in sorted(os.listdir(user_dir)):
        if not os.path.isdir(os.path.join(user_dir, name)):
            continue
        process = processes.get((user_name, name))
        running = bool(process and process.poll() is None)
        settings = get_app_settings(db, user_name, name)
        start_ms = db["start_times"].get(app_settings_key(user_name, name), 0)
        elapsed = (time.time() - start_ms / 1000.0) if start_ms else 0
        sleep_hours = get_config(db, "sleep_after_hours")
        apps_list.append({
            "name": name,
            "running": running,
            "auto_on": settings.get("auto_on", False),
            "auto_restart": settings.get("auto_restart", False),
            "device": settings.get("device_platform"),
            "remaining_run_min": round(max(0, sleep_hours * 3600 - elapsed) / 60, 1) if running else 0,
        })
    return jsonify({"ok": True, "apps": apps_list})

@app.route("/")
def index():
    if not require_login():
        return redirect(url_for("login"))
    user_name = current_user_key()
    user_dir = safe_join(UPLOAD_FOLDER, user_name)
    os.makedirs(user_dir, exist_ok=True)
    db = load_db()
    apps_list = []
    for name in sorted(os.listdir(user_dir)):
        if not os.path.isdir(os.path.join(user_dir, name)):
            continue
        p = processes.get((user_name, name))
        running = bool(p and p.poll() is None)
        settings = get_app_settings(db, user_name, name)
        start_ms = db["start_times"].get(app_settings_key(user_name, name), 0)
        elapsed = (time.time() - start_ms / 1000.0) if start_ms else 0
        sleep_hours = get_config(db, "sleep_after_hours")
        wake_minutes = get_config(db, "auto_wake_after_minutes")
        remaining_run = max(0, sleep_hours * 3600 - elapsed) if running else 0
        remaining_wake = 0
        if not running and settings.get("offline_at") and settings.get("auto_on") and not settings.get("manual_stop"):
            remaining_wake = max(0, wake_minutes * 60 - (time.time() - settings["offline_at"]))
        apps_list.append({
            "name": name,
            "running": running,
            "auto_on": settings.get("auto_on", False),
            "auto_restart": settings.get("auto_restart", False),
            "device": settings.get("device_platform"),
            "remaining_run_min": round(remaining_run / 60, 1),
            "remaining_wake_min": round(remaining_wake / 60, 1),
        })
    display_name = session.get("username", user_name)
    return render_template("index.html", apps=apps_list, username=display_name)

# ============================================================
# ROUTES — UPLOAD / VALIDATE / DEPLOY
# ============================================================
def _do_upload(user_name):
    file = request.files.get("file")
    overwrite = request.form.get("overwrite") == "true"

    if not file or not file.filename:
        return jsonify({"ok": False, "error": "No file was provided."}), 400
    if not file.filename.lower().endswith(".zip"):
        return jsonify({"ok": False, "error": "Only .zip files are allowed."}), 400

    app_name = re.sub(r"[^A-Za-z0-9_\-]", "_", file.filename.rsplit('.', 1)[0])[:50]
    if not app_name:
        return jsonify({"ok": False, "error": "The app name is invalid."}), 400

    user_dir = safe_join(UPLOAD_FOLDER, user_name, app_name)
    already_exists = os.path.exists(user_dir)
    if already_exists and not overwrite:
        return jsonify({"ok": False, "needs_confirm": True, "error": f"'{app_name}' already exists. Overwrite it?"}), 200

    tmp_zip = os.path.join(BASE_DIR, f"_tmp_{user_name}_{app_name}_{int(time.time())}.zip")
    file.save(tmp_zip)

    if not zipfile.is_zipfile(tmp_zip):
        os.remove(tmp_zip)
        return jsonify({"ok": False, "error": "This ZIP file is corrupt or invalid. Please try again."}), 400

    db = load_db()
    key = (user_name, app_name)
    if key in processes and processes[key].poll() is None:
        stop_app_process(user_name, app_name, db, manual=True)

    if os.path.exists(user_dir):
        shutil.rmtree(user_dir, ignore_errors=True)
    os.makedirs(user_dir, exist_ok=True)
    extract_dir = os.path.join(user_dir, "extracted")

    try:
        with zipfile.ZipFile(tmp_zip, 'r') as zip_ref:
            bad_file = zip_ref.testzip()
            if bad_file:
                raise zipfile.BadZipFile(f"Corrupt entry: {bad_file}")
            for member in zip_ref.namelist():
                member_path = os.path.abspath(os.path.join(extract_dir, member))
                if not member_path.startswith(os.path.abspath(extract_dir)):
                    raise ValueError("A suspicious path was found in the ZIP (path traversal attempt); upload blocked.")
            zip_ref.extractall(extract_dir)
    except (zipfile.BadZipFile, ValueError) as e:
        shutil.rmtree(user_dir, ignore_errors=True)
        os.remove(tmp_zip)
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        shutil.rmtree(user_dir, ignore_errors=True)
        if os.path.exists(tmp_zip):
            os.remove(tmp_zip)
        return jsonify({"ok": False, "error": f"Error while extracting: {e}"}), 400
    finally:
        if os.path.exists(tmp_zip):
            os.remove(tmp_zip)

    if not os.path.exists(extract_dir) or not os.listdir(extract_dir):
        shutil.rmtree(user_dir, ignore_errors=True)
        return jsonify({"ok": False, "error": "No files were found after extraction; the ZIP may be empty."}), 400

    entry, kind, project_root = find_project(extract_dir)
    if not entry:
        return jsonify({
            "ok": True,
            "warning": f"Upload completed, but no supported entry file was found. Expected one of: {', '.join(ENTRY_FILES_PY + ENTRY_FILES_JS + ENTRY_FILES_PHP)}",
            "app_name": app_name
        })

    return jsonify({"ok": True, "app_name": app_name, "entry": entry, "kind": kind,
                    "project_root": os.path.relpath(project_root, extract_dir)})

@app.route("/upload", methods=["POST"])
def upload():
    if not require_login():
        return jsonify({"ok": False, "error": "Login required"}), 401
    return _do_upload(current_user_key())

@app.route("/validate/<name>")
def validate_app(name):
    if not require_login():
        return jsonify({"ok": False, "error": "Login required"}), 401
    user_name = current_user_key()
    if not is_safe_component(name):
        return jsonify({"ok": False, "error": "Invalid app name"}), 400
    try:
        _, extract_dir, _ = app_dirs(user_name, name)
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid path"}), 400
    if not os.path.exists(extract_dir):
        return jsonify({"ok": False, "error": "App not found."}), 404
    result = validate_project(extract_dir)
    return jsonify(result)

@app.route("/run/<name>")
def run_app_route(name):
    if not require_login():
        return redirect(url_for("login"))
    user_name = current_user_key()
    if not is_safe_component(name):
        return jsonify({"ok": False, "error": "Invalid app name"}), 400
    db = load_db()
    result = start_app_process(user_name, name, db)
    if request.args.get("json") == "1":
        return jsonify(result)
    return redirect(url_for("index"))

@app.route("/stop/<name>")
def stop_app_route(name):
    if not require_login():
        return redirect(url_for("login"))
    user_name = current_user_key()
    if not is_safe_component(name):
        return jsonify({"ok": False, "error": "Invalid app name"}), 400
    db = load_db()
    stop_app_process(user_name, name, db, manual=True)
    activity.clear_pause(f"{user_name}_{name}")
    if request.args.get("json") == "1":
        return jsonify({"ok": True})
    return redirect(url_for("index"))

@app.route("/restart/<name>")
def restart_app_route(name):
    if not require_login():
        return redirect(url_for("login"))
    user_name = current_user_key()
    if not is_safe_component(name):
        return jsonify({"ok": False, "error": "Invalid app name"}), 400
    db = load_db()
    stop_app_process(user_name, name, db, manual=False)
    time.sleep(0.5)
    result = start_app_process(user_name, name, db)
    if request.args.get("json") == "1":
        return jsonify(result)
    return redirect(url_for("index"))

@app.route("/delete/<name>")
def delete_app_route(name):
    if not require_login():
        return redirect(url_for("login"))
    user_name = current_user_key()
    if not is_safe_component(name):
        return jsonify({"ok": False, "error": "Invalid app name"}), 400
    db = load_db()
    stop_app_process(user_name, name, db, manual=True)
    app_dir, _, _ = app_dirs(user_name, name)
    if os.path.exists(app_dir):
        shutil.rmtree(app_dir, ignore_errors=True)
    venv_dir = venv_path_for(user_name, name)
    if os.path.exists(venv_dir):
        shutil.rmtree(venv_dir, ignore_errors=True)
    dependency_cache = dependency_cache_for(user_name, name)
    if os.path.exists(dependency_cache):
        shutil.rmtree(dependency_cache, ignore_errors=True)
    key = app_settings_key(user_name, name)
    db.get("app_settings", {}).pop(key, None)
    db.get("app_env", {}).pop(key, None)
    db.get("start_times", {}).pop(key, None)
    save_db(db)
    return redirect(url_for("index"))

@app.route("/toggle_auto_on/<name>", methods=["POST"])
def toggle_auto_on(name):
    if not require_login():
        return jsonify({"ok": False}), 401
    user_name = current_user_key()
    if not is_safe_component(name):
        return jsonify({"ok": False, "error": "Invalid app name"}), 400
    db = load_db()
    settings = get_app_settings(db, user_name, name)
    settings["auto_on"] = not settings.get("auto_on", False)
    save_db(db)
    return jsonify({"ok": True, "auto_on": settings["auto_on"]})

@app.route("/toggle_auto_restart/<name>", methods=["POST"])
def toggle_auto_restart(name):
    if not require_login():
        return jsonify({"ok": False}), 401
    user_name = current_user_key()
    if not is_safe_component(name):
        return jsonify({"ok": False, "error": "Invalid app name"}), 400
    db = load_db()
    settings = get_app_settings(db, user_name, name)
    settings["auto_restart"] = not settings.get("auto_restart", False)
    save_db(db)
    return jsonify({"ok": True, "auto_restart": settings["auto_restart"]})

@app.route("/api/apps/<name>/env", methods=["GET", "PUT", "DELETE"])
def app_environment(name):
    if not require_login():
        return jsonify({"ok": False, "error": "Login required"}), 401
    if not is_safe_component(name):
        return jsonify({"ok": False, "error": "Invalid app name"}), 400
    user = current_user_key()
    _, extract_dir, _ = app_dirs(user, name)
    if not os.path.isdir(extract_dir):
        return jsonify({"ok": False, "error": "App not found"}), 404
    key = app_settings_key(user, name)
    db = load_db()
    app_env = db.setdefault("app_env", {})
    if request.method == "GET":
        return jsonify({"ok": True, "variables": sorted(app_env.get(key, {}).keys())})
    if request.method == "DELETE":
        variable = request.args.get("name", "")
        if not validate_app_env_name(variable):
            return jsonify({"ok": False, "error": "Invalid or reserved environment variable name"}), 400
        values = app_env.get(key, {})
        if variable not in values:
            return jsonify({"ok": False, "error": "Variable not found"}), 404
        values.pop(variable, None)
        if not values:
            app_env.pop(key, None)
        save_db(db)
        return jsonify({"ok": True, "variables": sorted(values.keys())})
    payload = request.get_json(silent=True) or {}
    variable = str(payload.get("name", "")).strip()
    value = payload.get("value")
    if not validate_app_env_name(variable):
        return jsonify({"ok": False, "error": "Use a valid, non-reserved name, for example TELEGRAM_BOT_TOKEN."}), 400
    if not isinstance(value, str) or len(value) > 8192:
        return jsonify({"ok": False, "error": "Value must be text and no longer than 8,192 characters."}), 400
    values = app_env.setdefault(key, {})
    if variable not in values and len(values) >= 40:
        return jsonify({"ok": False, "error": "An app can have at most 40 environment variables."}), 400
    try:
        values[variable] = _app_env_fernet().encrypt(value.encode("utf-8")).decode("ascii")
    except Exception:
        app.logger.exception("Could not encrypt app environment variable")
        return jsonify({"ok": False, "error": "Could not securely save this variable."}), 500
    save_db(db)
    return jsonify({"ok": True, "variables": sorted(values.keys()), "message": "Saved securely. Restart the app for the new value to take effect."})

@app.route("/get_log/<name>")
def get_log(name):
    if not require_login():
        return jsonify({"log": "", "status": "OFFLINE"}), 401
    user_name = current_user_key()
    if not is_safe_component(name):
        return jsonify({"log": "", "status": "OFFLINE"}), 400
    _, _, log_path = app_dirs(user_name, name)
    log_content = ""
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            log_content = f.read()[-4000:]
    p = processes.get((user_name, name))
    db = load_db()
    is_running = bool(p and p.poll() is None)
    settings = get_app_settings(db, user_name, name)
    return jsonify({
        "log": log_content,
        "status": "RUNNING" if is_running else "OFFLINE",
        "start_time": db["start_times"].get(app_settings_key(user_name, name), 0),
        "device": settings.get("device_platform")
    })

@app.route("/download/<name>")
def download_app(name):
    if not require_login():
        return redirect(url_for("login"))
    user_name = current_user_key()
    if not is_safe_component(name):
        return "Invalid app name", 400
    _, extract_dir, _ = app_dirs(user_name, name)
    if not os.path.exists(extract_dir):
        return "App not found", 404
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(extract_dir):
            dirs[:] = [d for d in dirs if d not in ("venv", "__pycache__", "node_modules")]
            for f in files:
                fpath = os.path.join(root, f)
                zf.write(fpath, os.path.relpath(fpath, extract_dir))
    memory_file.seek(0)
    return send_file(memory_file, download_name=f"{name}.zip", as_attachment=True)

# ============================================================
# ROUTES — PUBLIC SHARE LINK (no login needed: view status, start/stop, logs only)
# ============================================================
@app.route("/get_public_link/<name>", methods=["POST"])
def get_public_link(name):
    if not require_login():
        return jsonify({"ok": False}), 401
    if not is_safe_component(name):
        return jsonify({"ok": False, "error": "Invalid name"}), 400
    user_name = current_user_key()
    _, extract_dir, _ = app_dirs(user_name, name)
    if not os.path.exists(extract_dir):
        return jsonify({"ok": False, "error": "App not found"}), 404
    db = load_db()
    token = ensure_public_token(db, user_name, name)
    return jsonify({"ok": True, "token": token, "url": url_for("public_view", token=token, _external=True)})

@app.route("/revoke_public_link/<name>", methods=["POST"])
def revoke_public_link_route(name):
    if not require_login():
        return jsonify({"ok": False}), 401
    if not is_safe_component(name):
        return jsonify({"ok": False, "error": "Invalid name"}), 400
    db = load_db()
    revoke_public_token(db, current_user_key(), name)
    return jsonify({"ok": True})

def _lookup_public_token(token):
    db = load_db()
    tkey = db.get("public_tokens", {}).get(token)
    if not tkey or "_" not in tkey:
        return None, None, None
    user, name = tkey.split("_", 1)
    return db, user, name

@app.route("/public/<token>")
def public_view(token):
    db, user, name = _lookup_public_token(token)
    if not user:
        return render_template_string(PUBLIC_HTML, valid=False, name=None, running=False, token=token)
    p = processes.get((user, name))
    running = bool(p and p.poll() is None)
    return render_template_string(PUBLIC_HTML, valid=True, name=name, running=running, token=token)

@app.route("/public/<token>/status")
def public_status(token):
    db, user, name = _lookup_public_token(token)
    if not user:
        return jsonify({"ok": False, "error": "Invalid or revoked link"}), 404
    p = processes.get((user, name))
    running = bool(p and p.poll() is None)
    _, _, log_path = app_dirs(user, name)
    log_content = ""
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            log_content = f.read()[-3000:]
    return jsonify({"ok": True, "running": running, "log": log_content, "name": name})

@app.route("/public/<token>/start", methods=["POST"])
def public_start(token):
    db, user, name = _lookup_public_token(token)
    if not user:
        return jsonify({"ok": False, "error": "Invalid or revoked link"}), 404
    result = start_app_process(user, name, db)
    return jsonify(result)

@app.route("/public/<token>/stop", methods=["POST"])
def public_stop(token):
    db, user, name = _lookup_public_token(token)
    if not user:
        return jsonify({"ok": False, "error": "Invalid or revoked link"}), 404
    stop_app_process(user, name, db, manual=True)
    return jsonify({"ok": True})

# ============================================================
# ROUTES — FILE MANAGER
# ============================================================
@app.route("/list_files/<name>")
def list_files(name):
    if not require_login():
        return jsonify({"files": []}), 401
    user_name = current_user_key()
    if not is_safe_component(name):
        return jsonify({"files": []}), 400
    _, extract_dir, _ = app_dirs(user_name, name)
    files = []
    if os.path.exists(extract_dir):
        for root, dirs, filenames in os.walk(extract_dir):
            dirs[:] = [d for d in dirs if d not in ("venv", "__pycache__", "node_modules", ".git")]
            for f in filenames:
                files.append(os.path.relpath(os.path.join(root, f), extract_dir))
    return jsonify({"files": sorted(files)})

def _resolve_project_file(user_name, project, filename):
    if not is_safe_component(project):
        raise ValueError("Invalid project name")
    _, extract_dir, _ = app_dirs(user_name, project)
    # filename may contain subfolders (relative path) but must not escape extract_dir
    target = os.path.abspath(os.path.join(extract_dir, filename))
    if not target.startswith(os.path.abspath(extract_dir) + os.sep) and target != os.path.abspath(extract_dir):
        raise ValueError("Path traversal blocked")
    return target

@app.route("/read_file", methods=["POST"])
def read_content():
    if not require_login():
        return jsonify({"content": ""}), 401
    data = request.json or {}
    try:
        path = _resolve_project_file(current_user_key(), data.get('project', ''), data.get('filename', ''))
    except ValueError as e:
        return jsonify({"content": "", "error": str(e)}), 400
    if os.path.exists(path) and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return jsonify({"content": f.read()})
        except Exception as e:
            return jsonify({"content": "", "error": str(e)}), 500
    return jsonify({"content": "", "error": "File not found"}), 404

@app.route("/save_file", methods=["POST"])
def save_content():
    if not require_login():
        return jsonify({"status": "error"}), 401
    data = request.json or {}
    try:
        path = _resolve_project_file(current_user_key(), data.get('project', ''), data.get('filename', ''))
    except ValueError as e:
        return jsonify({"status": "error", "error": str(e)}), 400
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data.get('content', ''))
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/delete_file", methods=["POST"])
def delete_file_api():
    if not require_login():
        return jsonify({"status": "error"}), 401
    data = request.json or {}
    try:
        path = _resolve_project_file(current_user_key(), data.get('project', ''), data.get('filename', ''))
    except ValueError as e:
        return jsonify({"status": "error", "error": str(e)}), 400
    if os.path.exists(path):
        os.remove(path)
        return jsonify({"status": "deleted"})
    return jsonify({"status": "error", "error": "File not found"}), 404

if __name__ == "__main__":
    ensure_scheduler()
    port = int(os.environ.get("PORT", 3522))
    app.run(host="0.0.0.0", port=port, debug=False)
