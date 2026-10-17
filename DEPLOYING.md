# Runtime detection and deployment notes

This panel detects and launches **Python, Node.js, and PHP CLI bots** from uploaded ZIP archives. It supports a project inside one or more wrapper folders; detection walks the archive while skipping common dependency/cache directories.

## Detection rules

- **Python:** `main.py`, `bot.py`, `app.py`, `run.py`, or `start.py`; installs from `requirements.txt`, or installs the project from `pyproject.toml` / `setup.py` when there is no requirements file.
- **Node.js:** `index.js`, `server.js`, `bot.js`, `app.js`, or `main.js`; installs dependencies from `package.json`. If `package.json` has a `main` file it is preferred. Otherwise an `npm start` script is used when present.
- **PHP:** `main.php`, `bot.php`, `app.php`, `run.php`, `start.php`, `worker.php`, or `index.php`; installs Composer dependencies from `composer.json` when present, then starts PHP CLI.

Where several runtimes are present in one folder, the matching manifest (`composer.json`, `package.json`, or a Python manifest) is used to choose the runtime. Keep one app/runtime per ZIP for predictable detection. ZIP contents may be at the archive root or under a wrapper folder.

## Automatic dependencies and shared cache

- Python source imports are inspected when the ZIP has no (or incomplete) `requirements.txt`; common import/distribution-name differences such as `telegram` → `python-telegram-bot` and `yaml` → `PyYAML` are mapped. Other imports are tried using their import name as a PyPI name.
- Node.js `require()` and `import` statements are scanned and missing packages are inferred by their npm package name. Existing `package.json` dependency sections and lockfiles are respected.
- PHP Composer packages are taken from `composer.json`; only a small list of well-known namespaces can be mapped automatically when Composer metadata is missing. PHP namespaces do not uniquely identify Packagist packages, so unknown packages need `composer.json`.
- Each user/app pair gets its own dependency directory and package-manager cache. A bot can reuse its own compatible bundle on a later start, but another bot—even with identical package names—gets a separate install. Deleting an app removes that app's dependency cache. This prevents package environments from being mixed between bots; it costs more disk/network and avoids cross-app version changes.
- Static scanning cannot discover dynamically constructed imports, private packages, system libraries, PHP extensions, or every ambiguous import/package name. A manifest with exact package names and versions remains the reliable option. If inferred installation fails, add/fix the language manifest and check the bot log.
- Reusing one bot's own installed environment mainly saves that bot's startup time and repeat downloads. Because every bot is a separate process, installed packages do not guarantee lower runtime RAM use.

The dependency manager and executable must exist in the hosting image. The included `Dockerfile` installs Python, Node.js/npm, PHP CLI, and Composer for Railway or Render Docker deployments. The outer panel itself is still a Flask/Gunicorn service.

For an admin-only Render test service, set `ALLOW_SIGNUP=false`, and set strong, unique `ADMIN_PASS` and `SECRET_KEY` values in Render's environment settings. This blocks the app's default first-login account creation; use `/admin` for administrator access. The service still has a public URL, so keep the admin password private.

## Storage and hosting

The panel writes `uploads/`, per-app dependency environments/caches, logs, and `database.json` under its application directory by default. Set `DATA_DIR=/data` and mount a persistent volume at `/data` in the provider dashboard to preserve them across redeploys/restarts. Without persistent storage, uploads, accounts, logs and runtime/package caches can be lost.

## Important security limitation

Uploaded programs execute as child processes in the **same container and operating-system user as the panel**. They are not isolated from panel credentials or other files in that container. Do not expose this service to untrusted users as a public multi-tenant host without adding per-app container/VM isolation, resource/network limits, and a secure process supervisor. Runtime detection is not a security sandbox.

This runner is intended for long-running CLI bots/workers. A PHP website is not automatically published as its own public website: serving user web apps requires a separate port/proxy/domain routing design.
