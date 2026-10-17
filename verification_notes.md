Verification notes (2026-08-27)

The public login page at /login renders successfully with a white card, pale green background, green MY HOST branding, green shield icon, green primary action button, and focused green input styling. The Flask server is listening on port 3522, app.py passes py_compile, and the root route redirects to /login as expected.

Implemented dashboard features include summary cards for total/running/offline apps, a refresh button with status text, app search, status filtering, an empty filtered-state message, responsive mobile layout improvements, hover/focus states, and Ctrl/Cmd+K focusing the app search box. The user dashboard, user login, admin login, and admin dashboard palettes were changed from dark blue to white-and-green.

Runtime detection update (2026-10-17)

The panel now detects Python, Node.js, and PHP CLI projects in uploaded ZIPs, including wrapper directories; installs common requirements/package/composer dependencies; and launches the selected entry point. A Dockerfile supplies these runtimes for container deployments. Verification for this update included Python syntax compilation, JSON validation, nested-project detection fixtures, and Python/Node start-stop smoke tests. PHP CLI was not available in the development sandbox, so PHP process execution was not smoke-tested there.

The statement above that the Flask server "is listening on port 3522" describes the earlier verification environment and is not a claim that a server is currently running.
