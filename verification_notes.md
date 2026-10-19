Verification notes (historical)

Earlier dashboard verification covered summary cards, refresh, search/filter, responsive layout, and basic login behavior. The local JSON/password login and embedded Flask admin panel have since been removed in the Firebase migration.

Runtime detection update (2026-10-17): the panel detects Python, Node.js and PHP CLI projects in ZIPs, including wrapper directories; installs common requirements/package/composer dependencies; and launches the selected entry point. A Dockerfile supplies these runtimes for Docker deployments. Earlier verification included Python syntax compilation, JSON validation, nested-project detection fixtures, and Python/Node start-stop smoke tests. PHP CLI was not available in that development sandbox, so PHP process execution was not smoke-tested there.

Firebase/backend separation and host selector (2026-10-19): Firebase Auth and `users/{uid}` profile access run in the Pages browser through Firebase Web SDK; Flask has no Firebase Identity Toolkit or Firestore profile API calls. Flask locally verifies ID-token signatures against Google's Secure Token public keys and then creates its session. Pages resolves the active HTTPS runner origin from `users/panel_config.backendOrigin`; the private admin HTML writes that reserved document and omits it from the profile list. The Pages Function validates HTTPS origins and caches the selected origin briefly. Syntax, mocked proxy routing, local RSA/JWT verification, and login/register client flow checks passed. Firestore access remains governed by the existing Firebase Security Rules.

The statement that an earlier Flask server was listening on port 3522 describes that verification environment only; it is not a claim that a server is currently running.
