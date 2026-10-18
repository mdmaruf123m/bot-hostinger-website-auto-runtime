"""Download portable Node.js/PHP runtimes into writable persistent storage.

Node archives are checked against Node.js' signed-release checksum manifest.
PHP is supplied as a standalone, statically linked CLI by StaticPHP. No root or
package-manager access is required; the host must permit outbound HTTPS and
provide writable persistent storage plus tar/xz support.
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

NODE_VERSION = "24.22.0"
PHP_VERSION = "8.4.26"
RUNTIME_DIR = Path(os.environ.get("RUNTIME_BIN_DIR", Path(__file__).resolve().parent / "runtime_bins"))


def _download(url: str, destination: Path, timeout: int = 180) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "BotHostPanel-runtime-bootstrap/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response, destination.open("wb") as out:
        shutil.copyfileobj(response, out)


def _safe_extract(archive: Path, destination: Path, mode: str) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if mode == "xz":
        with tarfile.open(archive, "r:xz") as tf:
            base = destination.resolve()
            for member in tf.getmembers():
                target = (destination / member.name).resolve()
                if target != base and base not in target.parents:
                    raise RuntimeError("Unsafe path in runtime archive")
            tf.extractall(destination, filter="data")
    else:
        with tarfile.open(archive, "r:gz") as tf:
            members = tf.getmembers()
            for member in members:
                if member.name != "php" or not member.isfile():
                    raise RuntimeError("Unexpected contents in standalone PHP archive")
            tf.extractall(destination, members=members, filter="data")


def _node_target() -> tuple[str, str]:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        raise RuntimeError(f"Portable Node.js runtime does not support this CPU architecture: {machine}")
    if arch == "x64" and Path("/etc/alpine-release").exists():
        return "linux-x64-musl", "linux-x64-musl"
    return f"linux-{arch}", f"linux-{arch}"


def ensure_node_runtime(log=print) -> tuple[str, str]:
    target, archive_tag = _node_target()
    bin_dir = RUNTIME_DIR / f"node-v{NODE_VERSION}-{target}" / "bin"
    node = bin_dir / "node"
    npm = bin_dir / "npm"
    if node.is_file() and npm.is_file():
        return str(node), str(npm)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    archive_name = f"node-v{NODE_VERSION}-{archive_tag}.tar.xz"
    base = f"https://nodejs.org/dist/v{NODE_VERSION}"
    log(f"Node.js is not installed on this host. Downloading official Node.js {NODE_VERSION} runtime...")
    with tempfile.TemporaryDirectory(prefix="node-runtime-", dir=RUNTIME_DIR) as tmp:
        tmpdir = Path(tmp)
        checksums = tmpdir / "SHASUMS256.txt"
        archive = tmpdir / archive_name
        _download(f"{base}/SHASUMS256.txt", checksums, timeout=30)
        expected = None
        for line in checksums.read_text(encoding="ascii").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == archive_name:
                expected = parts[0].lower()
                break
        if not expected:
            raise RuntimeError(f"Node.js checksum manifest has no entry for {archive_name}")
        _download(f"{base}/{archive_name}", archive)
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError("Node.js archive checksum verification failed; the archive was discarded")
        stage = tmpdir / "extract"
        _safe_extract(archive, stage, "xz")
        source = stage / f"node-v{NODE_VERSION}-{target}"
        final = RUNTIME_DIR / f"node-v{NODE_VERSION}-{target}"
        if final.exists():
            shutil.rmtree(final)
        shutil.move(str(source), str(final))
    if not node.is_file() or not npm.is_file():
        raise RuntimeError("Node.js runtime download completed but node/npm executables are missing")
    subprocess.run([str(node), "--version"], check=True, capture_output=True, timeout=15)
    subprocess.run([str(node), str(npm), "--version"], check=True, capture_output=True, timeout=30)
    log(f"Installed portable Node.js {NODE_VERSION} and npm under writable runtime storage.")
    return str(node), str(npm)


def ensure_php_runtime(log=print) -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "x86_64"
    elif machine in ("aarch64", "arm64"):
        arch = "aarch64"
    else:
        raise RuntimeError(f"Portable PHP runtime does not support this CPU architecture: {machine}")
    php = RUNTIME_DIR / f"php-{PHP_VERSION}-{arch}" / "php"
    if php.is_file():
        return str(php)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    archive_name = f"php-{PHP_VERSION}-cli-linux-{arch}.tar.gz"
    url = f"https://dl.static-php.dev/v3/php-bin/common/{archive_name}"
    log(f"PHP CLI is not installed on this host. Downloading portable PHP {PHP_VERSION} runtime...")
    with tempfile.TemporaryDirectory(prefix="php-runtime-", dir=RUNTIME_DIR) as tmp:
        tmpdir = Path(tmp)
        archive = tmpdir / archive_name
        _download(url, archive)
        stage = tmpdir / "extract"
        _safe_extract(archive, stage, "gz")
        final = php.parent
        final.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stage / "php", php)
        php.chmod(0o755)
    subprocess.run([str(php), "-v"], check=True, capture_output=True, timeout=20)
    subprocess.run([str(php), "-r", "if (!extension_loaded('openssl')) exit(1);"], check=True, timeout=20)
    log(f"Installed portable PHP {PHP_VERSION} CLI under writable runtime storage.")
    return str(php)


def ensure_composer(php: str, log=print) -> str:
    phar = RUNTIME_DIR / "composer" / "composer.phar"
    wrapper = RUNTIME_DIR / "composer" / "composer"
    if phar.is_file() and wrapper.is_file():
        return str(wrapper)
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    log("Composer is not installed on this host. Downloading Composer and verifying its published SHA-256...")
    with tempfile.TemporaryDirectory(prefix="composer-runtime-", dir=wrapper.parent) as tmp:
        tmpdir = Path(tmp)
        phar_tmp = tmpdir / "composer.phar"
        digest_tmp = tmpdir / "composer.phar.sha256sum"
        _download("https://getcomposer.org/download/latest-stable/composer.phar", phar_tmp)
        _download("https://getcomposer.org/download/latest-stable/composer.phar.sha256sum", digest_tmp, timeout=30)
        expected = digest_tmp.read_text(encoding="ascii").split()[0].lower()
        actual = hashlib.sha256(phar_tmp.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError("Composer PHAR SHA-256 verification failed; the archive was discarded")
        shutil.move(str(phar_tmp), str(phar))
    wrapper.write_text(f"#!/bin/sh\nexec {php!r} {str(phar)!r} \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)
    subprocess.run([php, str(phar), "--version"], check=True, capture_output=True, timeout=30)
    log("Composer is ready for PHP dependency installation.")
    return str(wrapper)
