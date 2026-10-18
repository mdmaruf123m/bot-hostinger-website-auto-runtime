Verification notes (historical)

Earlier dashboard verification covered summary cards, refresh, search/filter, responsive layout, and basic login behavior. The local JSON/password login and embedded Flask admin panel have since been removed in the Firebase migration.

Runtime detection update (2026-10-17): the panel detects Python, Node.js and PHP CLI projects in ZIPs, including wrapper directories; installs common requirements/package/composer dependencies; and launches the selected entry point. A Dockerfile supplies these runtimes for Docker deployments. Earlier verification included Python syntax compilation, JSON validation, nested-project detection fixtures, and Python/Node start-stop smoke tests. PHP CLI was not available in that development sandbox, so PHP process execution was not smoke-tested there.

Firebase auth migration (2026-10-18): separate `/login` and `/register` pages use Firebase Email/Password Authentication. The Flask app accepts a Firebase ID token only after checking it with Firebase Identity Toolkit, then creates the existing Flask session. Registration creates a Firestore profile in `users/{uid}`. The private standalone `admin.html` reads and updates those profile documents; it does not manage Firebase Authentication accounts and is kept outside the GitHub repository. Firestore access still depends on the Firebase project's Security Rules.

The statement that an earlier Flask server was listening on port 3522 describes that verification environment only; it is not a claim that a server is currently running.
