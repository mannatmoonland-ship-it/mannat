HI

MANNAT MOON CONSTRUCTION - TRADE BUILDER & GOOGLE PHOTOS TOOL

1. Open PowerShell in this folder.

2. Install dependencies:
   pip install -r requirements.txt

3. Install Chromium for Playwright:
   python -m playwright install chromium

4. Run:
   python app.py

5. Open Trade Builder:
   http://127.0.0.1:5020/

6. Open Google Photos Tool:
   http://127.0.0.1:5020/google-photos

============================================================
DEVELOPMENT AND PRODUCTION SERVER
============================================================

Development:
- Start the existing Flask development server with `python app.py`.
- It continues to bind to `0.0.0.0:5020` so existing LAN testing remains available. Use it only on a trusted development network; it exits rather than starting if `FLASK_ENV=production`.

Production on Windows:
- Install the listed dependencies in the application's virtual environment: `python -m pip install -r requirements.txt`.
- Before production startup, configure `FLASK_ENV=production` and `SECRET_KEY` as environment variables for a unique random secret of at least 32 bytes. Do not put secret values in this README, source control, or command-line arguments.
- If bootstrapping an empty user store with `ensure_initial_super_admin`, provide a policy-compliant `INITIAL_SUPER_ADMIN_PASSWORD` through a private environment secret; the created account must change it at first sign-in. No default bootstrap password is used.
- Configure the existing `CAMERA_AUTH_API_SECRET` and `CAMERA_AUTH_ASSERTION_SECRET` through the production secret environment, matching the Camera application. This WSGI change does not alter the Camera authentication endpoint.
- From the project directory, start the existing Flask application through Waitress:
   `.venv\Scripts\waitress-serve.exe --host=127.0.0.1 --port=5020 wsgi:app`
- The production listener is restricted to loopback; do not bind it to `0.0.0.0` or the LAN address. This keeps port 5020 private and leaves a later local TLS proxy as the only intended path to this API.
- `wsgi.py` imports the existing Flask `app`; it does not initialize a second application or run Flask's development server.
- WSGI startup fails if debug mode is enabled, the Flask secret is missing/weak/default, or the existing app has not enabled Secure session cookies.
- Caddy/TLS configuration is a separate future stage and is not included here.

Render/Railway source startup (deployment preparation only):
- Use `python wsgi.py` as the provider start command. It runs Waitress on `0.0.0.0` and reads the provider-assigned `PORT` (defaulting to 5020 only for local use); it never starts Flask's development server.
- Set `FLASK_ENV=production` and a unique random `SECRET_KEY` of at least 32 bytes in the provider's secret environment.
- Set `PUBLIC_BASE_URL` to the application's canonical public HTTPS origin (for example, `https://app.example.com`, with no path). Production password-reset and account-setup links use this configured origin only; missing or non-HTTPS values fail closed. Development without this setting uses `http://127.0.0.1:5020`; to test email links on an office LAN, explicitly set a development `PUBLIC_BASE_URL`.
- Set `TRUSTED_PROXY_CIDRS` to the exact immediate reverse-proxy networks you operate. Never guess or copy generic provider ranges. In production, the unauthenticated private-LAN Daily Reports viewer/share image fails closed unless the immediate peer is a configured trusted proxy and a valid forwarded client address is present. Ensure the proxy overwrites/appends client forwarding headers safely; untrusted peers' `X-Forwarded-For` is ignored.
- Supply `FIREBASE_PROJECT_ID` and the matching public `FIREBASE_WEB_CONFIG` as provider environment values. Supply Firebase Admin credentials as a provider secret file referenced by `FIREBASE_SERVICE_ACCOUNT`, or as `FIREBASE_SERVICE_ACCOUNT_JSON` / `FIREBASE_SERVICE_ACCOUNT_BASE64`; environment-provided credential JSON is written to a private ignored `instance` file at startup.
- Configure the existing mail, bootstrap-account (only for first initialization), and Camera authentication secrets through the provider secret store. Never upload `.env`, service-account JSON, or private keys as source/deployment artifacts.
- Firebase Authentication must be enabled and configured in the selected Firebase project before browser custom-token sign-in can succeed. This startup command does not enable Firebase services or deploy the application.
- `instance/` and `backups/` are local filesystem storage, not durable cloud persistence. Do not rely on Render/Railway local filesystems for important recovery data; choose persistent storage or an external backup design before relying on those files for recovery.
- Stage a deployment source artifact separately from this development workspace and run `python verify_deployment_artifact.py <staging-directory>` before upload. The verifier rejects `.env*`, Firebase service-account JSON, private-key files, backup/instance/build/dist/virtual-environment/dependency directories, test secret fixtures, and symlinks. Also exclude `.venv*`, `node_modules/`, `.pytest_cache/`, `__pycache__/`, `tests/`, `backups/`, `instance/`, `dist/` (including the Windows executable), `build/`, local Firebase/debug logs, `.env`, and `mannat-moon-construction-firebase-adminsdk-*.json`; include only source/runtime dependencies and required static/templates files. Do not include `.env.example` in production artifacts.
- `.gitignore` contains local-only exclusions, but this workspace is not a Git repository, so Git tracked-status cannot be verified. Ignore rules alone do not prove a provider bundle is clean; inspect the exact staged artifact with the verifier and review its file list without opening or printing secrets.

============================================================
TRADE BUILDER
============================================================

Firebase:
- Existing project: mannat-moon-construction
- Production collection: trades
- Existing daily_records collection is NOT touched by this app.

Initial Trades are not hardcoded into Firestore by this app.
Create Masons and Bar Benders from the UI for the first test.

Trade Builder reads use the authenticated Firebase Web SDK and mutations use
permission-checked Flask APIs. Firestore Rules deny direct browser writes;
validate the Rules with `npm run test:rules` (requires Java 21+) before
deploying them to a Firebase project.

FLASK-TO-FIREBASE AUTHENTICATION BRIDGE
============================================================

The server-side bridge is available through `POST /api/firebase-custom-token`.
It validates the current Flask session and derives the Firebase UID, role,
permissions, session ID, and authorization version from trusted server data.
The browser must not provide identity or permission claims.

`static/firebase_auth.js` exports `firebaseAuthReady`, which resolves after
Firebase Auth is initialized with browser-local persistence and the current
Flask session has signed in using its custom token. Active Firestore page
modules await this promise before initializing their client or reading data.
After a Flask session or authorization change or Firebase authentication error,
call `reauthenticateFirebase()` explicitly and handle rejection by requiring a
valid Flask login when appropriate; the helper does not retry automatically.
`signOutFirebase()` is available for future coordinated Firebase/Flask logout
work. Firestore Rules also verify the token's session ID and authorization
version against the server-owned user record, so Flask logout/revocation blocks
subsequent direct client requests.

Firebase Auth persistence alone does not authorize Firestore access. The
repository's `firestore.rules` file denies access by default, grants permission-
scoped reads for active modules, and denies all direct browser writes. Writes
must continue through the permission-checked Flask APIs. Rules tests use the
Firestore Emulator; they require a Java runtime and can be run with
`npm run test:rules`.

============================================================
GOOGLE PHOTOS TOOL
============================================================

Paste a Google Photos shared URL and click Get Image.

This tool uses Playwright to capture the Google Photos
lh3.googleusercontent.com image URL and requests it at =s0
for maximum available image resolution.

It is a prototype for testing public/shared Google Photos links.
It does not bypass Google login or access controls.
The server validates destination addresses before fetching, but DNS is not pinned to the validated address for the lifetime of a connection; DNS rebinding remains a low residual defense-in-depth risk. Keep outbound network controls enabled and do not treat URL validation as a replacement for provider/network egress restrictions.
