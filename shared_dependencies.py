"""Best-effort dependency discovery and app-scoped environment caches for uploaded bots.

Explicit language manifests remain authoritative. When absent (or incomplete), common
static imports are inferred. The caller supplies a cache root unique to one user/app;
compatible starts of that same app reuse its environment, never another app's bundle.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()

PYTHON_IMPORT_MAP = {
    "telegram": "python-telegram-bot", "telebot": "pyTelegramBotAPI",
    "discord": "discord.py", "cv2": "opencv-python-headless", "PIL": "Pillow",
    "yaml": "PyYAML", "bs4": "beautifulsoup4", "dotenv": "python-dotenv",
    "Crypto": "pycryptodome", "Cryptodome": "pycryptodomex", "sklearn": "scikit-learn",
    "dateutil": "python-dateutil", "gi": "PyGObject", "psycopg2": "psycopg2-binary",
    "MySQLdb": "mysqlclient", "jwt": "PyJWT", "googleapiclient": "google-api-python-client",
    "PILLOW_VERSION": "Pillow", "jose": "python-jose", "google.cloud.storage": "google-cloud-storage",
    "google.cloud.firestore": "google-cloud-firestore",
}

PHP_NAMESPACE_MAP = {
    "GuzzleHttp": "guzzlehttp/guzzle", "Monolog": "monolog/monolog",
    "Carbon": "nesbot/carbon", "Ramsey\\Uuid": "ramsey/uuid",
    "Symfony\\Component\\Yaml": "symfony/yaml", "Symfony\\Component\\HttpClient": "symfony/http-client",
    "Firebase\\JWT": "firebase/php-jwt", "Telegram\\Bot": "irazasyed/telegram-bot-sdk",
    "TelegramBot\\Api": "telegram-bot-sdk/telegram-bot-sdk",
    "Predis": "predis/predis", "Dotenv": "vlucas/phpdotenv",
}

NODE_CORE = set("assert async_hooks buffer child_process cluster console constants crypto dgram diagnostics_channel dns domain events fs http http2 https module net os path perf_hooks process punycode querystring readline repl stream string_decoder sys timers tls trace_events tty url util v8 vm wasi worker_threads zlib test inspector".split())


def _say(log, text: str) -> None:
    try:
        log(text if text.endswith("\n") else text + "\n")
    except Exception:
        pass


def _digest(value) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _version(command) -> str:
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL, timeout=10).strip()
    except Exception:
        return "unknown"


def _lock_for(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _bundle_dir(cache_root: str, language: str, key: str) -> str:
    return os.path.join(cache_root, language, key)


def _complete(path: str) -> bool:
    return os.path.isfile(os.path.join(path, ".install-complete"))


def _begin_bundle(path: str) -> None:
    if os.path.isdir(path) and not _complete(path):
        shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)


def _mark_bundle(path: str) -> None:
    Path(path, ".install-complete").write_text("ok\n", encoding="utf-8")


def _run(command, cwd, timeout, log, label, env=None):
    try:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        _say(log, f"{label} timed out after {timeout} seconds.")
        return False, f"{label} timed out"
    except Exception as exc:
        _say(log, f"{label} failed: {type(exc).__name__}: {exc}")
        return False, str(exc)
    if result.stdout:
        _say(log, result.stdout[-5000:])
    if result.returncode:
        if result.stderr:
            _say(log, f"{label} error:\n{result.stderr[-4000:]}")
        return False, (result.stderr or result.stdout or f"{label} failed")[-1000:]
    return True, ""


def _walk_sources(root: str, extensions: set[str]):
    excluded = {".git", ".venv", "venv", "node_modules", "vendor", "__pycache__", ".idea", ".vscode"}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in excluded]
        for filename in files:
            if Path(filename).suffix.lower() in extensions:
                yield os.path.join(base, filename)


def _local_python_module(root: str, module: str) -> bool:
    name = module.split(".", 1)[0]
    if os.path.isfile(os.path.join(root, name + ".py")) or os.path.isfile(os.path.join(root, name, "__init__.py")):
        return True
    # Python 3 namespace packages may intentionally omit __init__.py. A
    # directory containing local .py sources is still project code, not a
    # package that should be installed from PyPI.
    local_dir = os.path.join(root, name)
    if os.path.isdir(local_dir) and any(
        filename.endswith(".py") for filename in os.listdir(local_dir)
    ):
        return True
    excluded = {".git", ".venv", "venv", "node_modules", "vendor", "__pycache__"}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in excluded]
        if name + ".py" in files or (name in dirs and os.path.isfile(os.path.join(base, name, "__init__.py"))):
            return True
    return False


def infer_python_packages(project_root: str, log=None) -> list[str]:
    imports: set[str] = set()
    for path in _walk_sources(project_root, {".py"}):
        try:
            tree = ast.parse(Path(path).read_text(encoding="utf-8", errors="ignore"), filename=path)
        except (SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imports.add(node.module)
                if node.module == "google.cloud":
                    imports.update(f"google.cloud.{alias.name}" for alias in node.names)
    packages = set()
    stdlib = getattr(sys, "stdlib_module_names", set())
    for imported in imports:
        top = imported.split(".", 1)[0]
        if top in stdlib or _local_python_module(project_root, imported):
            continue
        distribution = next((pkg for prefix, pkg in sorted(PYTHON_IMPORT_MAP.items(), key=lambda item: len(item[0]), reverse=True)
                             if imported == prefix or imported.startswith(prefix + ".")), None)
        if not distribution:
            if top == "google":
                _say(log, f"Python import '{imported}' is in the shared Google namespace and cannot be mapped safely; add its exact distribution to requirements.txt.")
                continue
            distribution = top.replace("_", "-")
            _say(log, f"Auto-detected Python import '{top}' → trying PyPI package '{distribution}'. If names differ, add the correct package to requirements.txt.")
        packages.add(distribution)
    return sorted(packages, key=str.lower)


def _python_requirement_name(line: str) -> str | None:
    try:
        from pip._vendor.packaging.requirements import Requirement
        from pip._vendor.packaging.utils import canonicalize_name
        return canonicalize_name(Requirement(line).name)
    except Exception:
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if not match or line.lstrip().startswith(("-", "http:", "https:", "git+")):
            return None
        return re.sub(r"[-_.]+", "-", match.group(1)).lower()


def _project_manifest(project_root: str, names: tuple[str, ...]):
    for name in names:
        path = os.path.join(project_root, name)
        if os.path.isfile(path):
            data = Path(path).read_bytes()
            return path, hashlib.sha256(data).hexdigest()
    return None, None


def prepare_python(project_root: str, cache_root: str, log) -> tuple[str, dict]:
    req_path = os.path.join(project_root, "requirements.txt")
    project_manifest, project_hash = _project_manifest(project_root, ("pyproject.toml", "setup.py"))
    inferred = infer_python_packages(project_root, log)
    existing_lines = []
    if os.path.isfile(req_path):
        existing_lines = [line.strip() for line in Path(req_path).read_text(encoding="utf-8", errors="ignore").splitlines()
                          if line.strip() and not line.lstrip().startswith("#")]
    declared = {n for n in (_python_requirement_name(line) for line in existing_lines) if n}
    extra = [pkg for pkg in inferred if re.sub(r"[-_.]+", "-", pkg).lower() not in declared]
    if extra:
        _say(log, "Auto-detected Python packages from source imports: " + ", ".join(extra))
    if not existing_lines and project_manifest is None and not inferred:
        _say(log, "No third-party Python imports detected; using the system Python runtime.")
        return shutil.which("python3") or sys.executable, {"ok": True, "cached": True, "skipped": True, "packages": []}

    identity = {
        "requirements": sorted(existing_lines, key=str.lower),
        "extra_imports": sorted(extra, key=str.lower),
        "project_manifest_sha256": project_hash,
        "runtime": f"python-{sys.version_info.major}.{sys.version_info.minor}",
    }
    key = _digest(identity)
    path = _bundle_dir(cache_root, "python", key)
    python_bin = os.path.join(path, "venv", "bin", "python")
    lock = _lock_for("python:" + path)
    with lock:
        if _complete(path) and os.path.isfile(python_bin):
            _say(log, f"Reusing this bot's cached Python dependency environment ({len(existing_lines)+len(extra)} declared/inferred package entries).")
            return python_bin, {"ok": True, "cached": True, "packages": len(existing_lines)+len(extra)}
        _begin_bundle(path)
        venv_dir = os.path.join(path, "venv")
        pip_env = os.environ.copy()
        pip_env["PIP_CACHE_DIR"] = os.path.join(cache_root, "pip-downloads")
        ok, err = _run([shutil.which("python3") or sys.executable, "-m", "venv", venv_dir], project_root, 120, log, "Python venv creation")
        if not ok:
            return "", {"ok": False, "error": err}
        python_bin = os.path.join(venv_dir, "bin", "python")
        if existing_lines:
            ok, err = _run([python_bin, "-m", "pip", "install", "--disable-pip-version-check", "-r", req_path], project_root, 600, log, "Python dependency installation", pip_env)
            if not ok:
                return "", {"ok": False, "error": err}
        if project_manifest:
            ok, err = _run([python_bin, "-m", "pip", "install", "--disable-pip-version-check", "."], project_root, 600, log, "Python project installation", pip_env)
            if not ok:
                return "", {"ok": False, "error": err}
        if extra:
            ok, err = _run([python_bin, "-m", "pip", "install", "--disable-pip-version-check", *extra], project_root, 600, log, "Auto-detected Python dependency installation", pip_env)
            if not ok:
                return "", {"ok": False, "error": err}
        _mark_bundle(path)
        return python_bin, {"ok": True, "cached": False, "packages": len(existing_lines)+len(extra)}


def _node_package_name(spec: str) -> str | None:
    if not isinstance(spec, str) or not spec:
        return None
    if spec.startswith(("node:", "./", "../", "/")):
        return None
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") and len(parts) >= 2 else parts[0]


def infer_node_packages(project_root: str, log=None) -> list[str]:
    imports: set[str] = set()
    patterns = [
        re.compile(r"\b(?:require|import)\s*\(\s*['\"]([^'\"]+)['\"]\s*\)"),
        re.compile(r"\b(?:from\s*|import\s*)['\"]([^'\"]+)['\"]"),
    ]
    for path in _walk_sources(project_root, {".js", ".cjs", ".mjs"}):
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in patterns:
            imports.update(pattern.findall(text))
    packages = set()
    for spec in imports:
        name = _node_package_name(spec)
        if not name:
            continue
        if name.split("/", 1)[0] in NODE_CORE or name.startswith("node:"):
            continue
        packages.add(name)
    if packages:
        _say(log, "Auto-detected Node.js packages from source imports: " + ", ".join(sorted(packages)))
    return sorted(packages)


def _node_deps(project_root: str, inferred: list[str]):
    package_path = os.path.join(project_root, "package.json")
    package = {}
    if os.path.isfile(package_path):
        try:
            package = json.loads(Path(package_path).read_text(encoding="utf-8"))
        except Exception as exc:
            return None, None, [], f"Invalid package.json: {exc}"
        if not isinstance(package, dict):
            return None, None, [], "Invalid package.json: the root value must be a JSON object"
    deps = {}
    declared_names = set()
    for section in ("dependencies", "optionalDependencies", "devDependencies"):
        values = package.get(section, {})
        if isinstance(values, dict):
            deps.update({str(k): str(v) for k, v in values.items()})
            declared_names.update(str(k) for k in values)
    for name in inferred:
        deps.setdefault(name, "*")
    extra = [name for name in inferred if name not in declared_names]
    return package, dict(sorted(deps.items())), extra, None


def _link_cache(cache_dir: str, project_root: str, name: str, log) -> None:
    source = os.path.join(cache_dir, name)
    dest = os.path.join(project_root, name)
    if not os.path.isdir(source):
        return
    if os.path.islink(dest):
        try:
            if os.path.realpath(dest) == os.path.realpath(source):
                return
        except OSError:
            pass
        os.unlink(dest)
    if os.path.exists(dest):
        if os.path.isdir(dest):
            _say(log, f"Using uploaded project-local {name}/ instead of the shared cache; project already contains this directory.")
        return
    try:
        os.symlink(source, dest, target_is_directory=True)
    except OSError as exc:
        _say(log, f"Could not link shared {name} directory: {exc}")


def prepare_node(project_root: str, cache_root: str, log) -> tuple[dict, dict]:
    from runtime_bootstrap import ensure_node_runtime

    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        try:
            node, npm = ensure_node_runtime(log)
            os.environ["PATH"] = os.path.dirname(node) + os.pathsep + os.environ.get("PATH", "")
        except Exception as exc:
            return {}, {"ok": False, "error": f"Could not prepare portable Node.js/npm: {exc}"}
    inferred = infer_node_packages(project_root, log)
    package, deps, extra, error = _node_deps(project_root, inferred)
    if error:
        return {}, {"ok": False, "error": error}
    if not deps:
        _say(log, "No external Node.js packages detected; no npm installation needed.")
        return {}, {"ok": True, "cached": True, "skipped": True, "packages": 0}
    lock_path = _project_manifest(project_root, ("package-lock.json", "npm-shrinkwrap.json"))
    lock_hash = lock_path[1] if lock_path and lock_path[0] and not extra else None
    local_specs = [v for v in deps.values() if v.startswith(("file:", "link:", "workspace:"))]
    if local_specs:
        _say(log, "Local/workspace npm dependency detected; installing into this app with the global npm tarball cache to preserve relative paths.")
        try:
            npm_env = os.environ.copy()
            npm_env["npm_config_cache"] = os.path.join(cache_root, "npm-downloads")
            result = subprocess.run([shutil.which("npm") or "npm", "install", "--no-audit", "--no-fund"], cwd=project_root, env=npm_env, capture_output=True, text=True, timeout=600)
            _say(log, result.stdout[-4000:])
            if result.returncode:
                _say(log, result.stderr[-3000:])
                return {}, {"ok": False, "error": "npm install failed"}
            return {}, {"ok": True, "cached": False, "packages": len(deps)}
        except Exception as exc:
            return {}, {"ok": False, "error": str(exc)}
    identity = {"dependencies": deps, "lock_sha256": lock_hash,
                "node_version": _version([node, "--version"])}
    path = _bundle_dir(cache_root, "node", _digest(identity))
    lock = _lock_for("node:" + path)
    with lock:
        was_cached = _complete(path) and os.path.isdir(os.path.join(path, "node_modules"))
        if not was_cached:
            _begin_bundle(path)
            package_data = {k: v for k, v in package.items() if k not in ("scripts", "workspaces")}
            package_data.setdefault("name", "shared-bot-dependencies")
            package_data.setdefault("private", True)
            package_data.setdefault("version", "1.0.0")
            if not package_data:
                package_data["dependencies"] = deps
            elif extra:
                package_data.setdefault("dependencies", {}).update({name: deps[name] for name in extra})
            Path(path, "package.json").write_text(json.dumps(package_data, indent=2), encoding="utf-8")
            if lock_path and lock_path[0] and not extra:
                shutil.copy2(lock_path[0], os.path.join(path, os.path.basename(lock_path[0])))
            command = [npm, "install", "--prefix", path, "--no-audit", "--no-fund"]
            npm_env = os.environ.copy()
            npm_env["npm_config_cache"] = os.path.join(cache_root, "npm-downloads")
            ok, err = _run(command, project_root, 600, log, "npm install", npm_env)
            if not ok:
                return {}, {"ok": False, "error": err}
            _mark_bundle(path)
        else:
            _say(log, f"Reusing this bot's cached Node.js dependency environment ({len(deps)} packages).")
    _link_cache(path, project_root, "node_modules", log)
    env = {"NODE_PATH": os.path.join(path, "node_modules") + (os.pathsep + os.environ["NODE_PATH"] if os.environ.get("NODE_PATH") else "")}
    return env, {"ok": True, "cached": was_cached, "packages": len(deps)}


def _php_namespaces(project_root: str) -> set[str]:
    found = set()
    pattern = re.compile(r"^\s*use\s+(?!function\b|const\b)([A-Za-z_\\][A-Za-z0-9_\\]*)", re.M)
    fqcn_pattern = re.compile(r"\\([A-Z][A-Za-z0-9_]*(?:\\[A-Za-z_][A-Za-z0-9_]*)+)")
    for path in _walk_sources(project_root, {".php"}):
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for value in pattern.findall(text):
            found.add(value.strip("\\"))
        found.update(fqcn_pattern.findall(text))
    return found


def infer_php_packages(project_root: str, log=None) -> list[str]:
    packages = set()
    for namespace in _php_namespaces(project_root):
        package = next((pkg for prefix, pkg in sorted(PHP_NAMESPACE_MAP.items(), key=lambda item: len(item[0]), reverse=True)
                        if namespace == prefix or namespace.startswith(prefix + "\\")), None)
        if package:
            packages.add(package)
        else:
            _say(log, f"PHP namespace '{namespace}' has no reliable Packagist mapping. Add its package to composer.json; namespace names do not uniquely identify Composer packages.")
    if packages:
        _say(log, "Auto-detected PHP Composer packages from known namespaces: " + ", ".join(sorted(packages)))
    return sorted(packages)


def prepare_php(project_root: str, cache_root: str, log) -> tuple[dict, dict]:
    from runtime_bootstrap import ensure_composer, ensure_php_runtime

    composer = shutil.which("composer")
    php = shutil.which("php")
    if not php:
        try:
            php = ensure_php_runtime(log)
        except Exception as exc:
            return {}, {"ok": False, "error": f"Could not prepare portable PHP CLI: {exc}"}
    composer_path = os.path.join(project_root, "composer.json")
    lock_path = os.path.join(project_root, "composer.lock")
    data = {}
    if os.path.isfile(composer_path):
        try:
            data = json.loads(Path(composer_path).read_text(encoding="utf-8"))
        except Exception as exc:
            return {}, {"ok": False, "error": f"Invalid composer.json: {exc}"}
    inferred = infer_php_packages(project_root, log)
    declared = data.get("require", {}) if isinstance(data.get("require", {}), dict) else {}
    deps = {str(k): str(v) for k, v in declared.items() if k.lower() != "php" and not k.lower().startswith("ext-")}
    extra = [pkg for pkg in inferred if pkg.lower() not in {n.lower() for n in deps}]
    for pkg in extra:
        deps[pkg] = "*"
    if not deps:
        if not os.path.isfile(composer_path):
            _say(log, "No external PHP namespace/package detected; no Composer install needed.")
        return {}, {"ok": True, "cached": True, "skipped": True, "packages": 0}
    if not composer:
        try:
            composer = ensure_composer(php, log)
        except Exception as exc:
            return {}, {"ok": False, "error": f"Could not prepare Composer: {exc}"}

    # Shared vendor trees are safe only for ordinary dependency manifests. Preserve
    # project-specific autoload/repository/scripts behavior via a local install.
    special = any(k in data for k in ("autoload", "autoload-dev", "repositories", "scripts"))
    if special:
        _say(log, "Project-specific Composer autoload/repository/scripts detected; installing this app locally (Composer's global download cache is still reused).")
        env = os.environ.copy()
        env["COMPOSER_CACHE_DIR"] = os.path.join(cache_root, "composer-downloads")
        if extra:
            data["require"] = dict(declared)
            data["require"].update({pkg: "*" for pkg in extra})
            Path(composer_path).write_text(json.dumps(data, indent=2), encoding="utf-8")
            result = subprocess.run([composer, "install", "--no-interaction", "--no-dev", "--prefer-dist"], cwd=project_root, env=env, capture_output=True, text=True, timeout=600)
        _say(log, result.stdout[-4000:])
        if result.returncode:
            _say(log, result.stderr[-3000:])
            return {}, {"ok": False, "error": "Composer install failed"}
        return env, {"ok": True, "cached": False, "packages": len(deps)}

    identity = {"require": deps, "manifest_require": declared,
                "php_version": _version([php, "-r", "echo PHP_VERSION;"]),
                "lock_sha256": hashlib.sha256(Path(lock_path).read_bytes()).hexdigest() if os.path.isfile(lock_path) else None}
    path = _bundle_dir(cache_root, "php", _digest(identity))
    lock = _lock_for("php:" + path)
    with lock:
        was_cached = _complete(path) and os.path.isdir(os.path.join(path, "vendor"))
        if not was_cached:
            _begin_bundle(path)
            manifest = dict(data) if data else {"name": "shared/bot-dependencies", "description": "Shared bot dependency bundle", "type": "project"}
            manifest["require"] = dict(declared)
            for pkg in extra:
                manifest["require"][pkg] = "*"
            manifest.setdefault("minimum-stability", "stable")
            manifest.setdefault("prefer-stable", True)
            Path(path, "composer.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            if os.path.isfile(lock_path) and not extra:
                shutil.copy2(lock_path, os.path.join(path, "composer.lock"))
            env = os.environ.copy()
            env["COMPOSER_CACHE_DIR"] = os.path.join(cache_root, "composer-downloads")
            command = [composer, "install" if os.path.isfile(os.path.join(path, "composer.lock")) else "update", "--no-interaction", "--no-dev", "--prefer-dist", "--no-scripts"]
            ok, err = _run(command, path, 600, log, "Composer install", env)
            if not ok:
                return {}, {"ok": False, "error": err}
            _mark_bundle(path)
        else:
            _say(log, f"Reusing this bot's cached PHP Composer dependency environment ({len(deps)} packages).")
    _link_cache(path, project_root, "vendor", log)
    env = {"COMPOSER_VENDOR_DIR": os.path.join(path, "vendor"), "COMPOSER_CACHE_DIR": os.path.join(cache_root, "composer-downloads")}
    return env, {"ok": True, "cached": was_cached, "packages": len(deps)}
