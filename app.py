import base64
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import socket
import ssl
import threading
import time
import uuid
from html import escape
from collections import deque
from datetime import datetime, timedelta, timezone
from functools import wraps
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests
from google.api_core.exceptions import DeadlineExceeded, GoogleAPICallError
from google.api_core.retry import Retry
from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    render_template_string,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from flask_mail import Mail, Message
from playwright.sync_api import sync_playwright
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash
from owner_scope import (
    LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
    OwnerScopeConflictError,
    create_owner_scope_item,
    delete_owner_scope_item,
    get_owner_scope_data,
    update_owner_scope_item,
)
from permission_policy import compile_effective_permissions

load_dotenv()

IS_PRODUCTION_ENVIRONMENT = (
    os.environ.get("FLASK_ENV", "").strip().lower() == "production"
    or bool(os.environ.get("RENDER"))
    or bool(os.environ.get("RAILWAY_ENVIRONMENT"))
)

try:
    import firebase_admin
    from firebase_admin import auth as firebase_auth, credentials, firestore
    import google.auth
    import google.auth.credentials
    import google.auth.transport.grpc
    import google.auth.transport.urllib3
    from google.cloud.firestore_v1 import FieldFilter
    from google.cloud.firestore_v1.services.firestore import client as firestore_gapic_client_module
    from google.cloud.firestore_v1.services.firestore.transports.grpc import FirestoreGrpcTransport
    import urllib3
except ImportError:  # pragma: no cover - optional for local/dev fallback
    firebase_admin = None
    firebase_auth = None
    credentials = None
    firestore = None
    FieldFilter = None
    google = None
    firestore_gapic_client_module = None
    FirestoreGrpcTransport = None
    urllib3 = None


_FIRESTORE_AUTH_REQUEST = None
_FIRESTORE_AUTH_POOL = None
_FIRESTORE_AUTH_REQUEST_LOCK = threading.Lock()


def _get_firestore_auth_request():
    global _FIRESTORE_AUTH_POOL, _FIRESTORE_AUTH_REQUEST
    if _FIRESTORE_AUTH_REQUEST is None:
        with _FIRESTORE_AUTH_REQUEST_LOCK:
            if _FIRESTORE_AUTH_REQUEST is None:
                tls_context = ssl.create_default_context()
                tls_context.check_hostname = True
                tls_context.verify_mode = ssl.CERT_REQUIRED
                _FIRESTORE_AUTH_POOL = urllib3.PoolManager(
                    ssl_context=tls_context,
                    retries=False,
                    timeout=urllib3.Timeout(connect=5, read=5),
                )
                _FIRESTORE_AUTH_REQUEST = google.auth.transport.urllib3.Request(
                    _FIRESTORE_AUTH_POOL
                )
    return _FIRESTORE_AUTH_REQUEST


if FirestoreGrpcTransport is not None:
    class _Urllib3FirestoreGrpcTransport(FirestoreGrpcTransport):
        @classmethod
        def create_channel(
            cls,
            host="firestore.googleapis.com",
            credentials=None,
            credentials_file=None,
            scopes=None,
            quota_project_id=None,
            **kwargs,
        ):
            if credentials_file is not None:
                if credentials is not None:
                    raise ValueError("'credentials' and 'credentials_file' are mutually exclusive.")
                credentials, _ = google.auth.load_credentials_from_file(
                    credentials_file,
                    scopes=scopes,
                    default_scopes=cls.AUTH_SCOPES,
                )
            elif credentials is None:
                credentials, _ = google.auth.default(
                    scopes=scopes,
                    default_scopes=cls.AUTH_SCOPES,
                )
            else:
                credentials = google.auth.credentials.with_scopes_if_required(
                    credentials,
                    scopes=scopes,
                    default_scopes=cls.AUTH_SCOPES,
                )

            if quota_project_id and isinstance(
                credentials, google.auth.credentials.CredentialsWithQuotaProject
            ):
                credentials = credentials.with_quota_project(quota_project_id)

            ssl_credentials = kwargs.pop("ssl_credentials", None)
            return google.auth.transport.grpc.secure_authorized_channel(
                credentials,
                _get_firestore_auth_request(),
                host,
                ssl_credentials=ssl_credentials,
                **kwargs,
            )


    class _Urllib3FirestoreClient(firestore.Client):
        @property
        def _firestore_api(self):
            return self._firestore_api_helper(
                _Urllib3FirestoreGrpcTransport,
                firestore_gapic_client_module.FirestoreClient,
                firestore_gapic_client_module,
            )
else:  # pragma: no cover - Firebase dependencies are optional
    _Urllib3FirestoreGrpcTransport = None
    _Urllib3FirestoreClient = None


def find_firebase_service_account_path():
    configured_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if configured_path:
        return configured_path

    service_account_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    encoded_service_account = os.environ.get("FIREBASE_SERVICE_ACCOUNT_BASE64")
    if service_account_json is not None or encoded_service_account is not None:
        try:
            raw_json = (
                base64.b64decode(encoded_service_account, validate=True).decode("utf-8")
                if encoded_service_account is not None
                else service_account_json
            )
            service_account = json.loads(raw_json)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("Firebase Admin service-account environment data is invalid.") from error
        if (
            not isinstance(service_account, dict)
            or service_account.get("type") != "service_account"
            or not all(
                isinstance(service_account.get(field), str) and service_account[field]
                for field in ("project_id", "client_email", "private_key")
            )
        ):
            raise RuntimeError("Firebase Admin service-account environment data is invalid.")
        configured_project_id = os.environ.get("FIREBASE_PROJECT_ID")
        if configured_project_id and service_account["project_id"] != configured_project_id:
            raise RuntimeError("Firebase Admin credentials do not match FIREBASE_PROJECT_ID.")

        private_directory = Path(__file__).resolve().parent / "instance"
        private_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        service_account_path = private_directory / "firebase-service-account.runtime.json"
        temporary_path = private_directory / f".firebase-service-account.{uuid.uuid4().hex}.tmp"
        file_descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as service_account_file:
                json.dump(service_account, service_account_file)
            os.replace(temporary_path, service_account_path)
            try:
                os.chmod(service_account_path, 0o600)
            except OSError:
                if os.name != "nt":
                    raise
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
        return str(service_account_path)

    search_roots = (Path.cwd(), Path(__file__).resolve().parent)
    for search_root in dict.fromkeys(search_roots):
        try:
            filenames = sorted(os.listdir(search_root))
        except OSError:
            continue
        for filename in filenames:
            if filename.lower().startswith("mannat-moon-construction-firebase-adminsdk-") and filename.lower().endswith(".json"):
                return str(search_root / filename)
    return None


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "mannat-moon-dev-secret-change-me")
app.config["PUBLIC_BASE_URL"] = os.environ.get("PUBLIC_BASE_URL", "").strip()
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION_ENVIRONMENT


def static_asset_version(filename):
    if not isinstance(filename, str) or Path(filename).suffix.lower() not in {".css", ".js", ".mjs"}:
        return None

    static_root = Path(app.static_folder).resolve()
    asset_path = (static_root / filename).resolve()
    try:
        if os.path.commonpath((str(static_root), str(asset_path))) != str(static_root):
            return None
        asset_stat = asset_path.stat()
    except (OSError, ValueError):
        return None

    if not asset_path.is_file():
        return None
    return f"{asset_stat.st_mtime_ns:x}-{asset_stat.st_size:x}"


@app.url_defaults
def add_static_asset_version(endpoint, values):
    if endpoint != "static":
        return

    filename = values.get("filename")
    version = static_asset_version(filename)
    if version:
        values["v"] = version


@app.before_request
def redirect_unversioned_static_assets():
    if request.endpoint != "static":
        return None

    filename = (request.view_args or {}).get("filename")
    version = static_asset_version(filename)
    if version is None or request.args.get("v") == version:
        return None

    response = redirect(url_for("static", filename=filename), code=302)
    response.headers["Cache-Control"] = "no-store"
    return response


app.config.update(
    MAIL_SERVER=os.environ.get("MAIL_SERVER", "smtp.gmail.com"),
    MAIL_PORT=int(os.environ.get("MAIL_PORT", "587")),
    MAIL_USE_TLS=os.environ.get("MAIL_USE_TLS", "True").lower() in {"1", "true", "yes", "on"},
    MAIL_USERNAME=os.environ.get("MAIL_USERNAME", "mannatmoonland@gmail.com"),
    MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD", ""),
    MAIL_DEFAULT_SENDER=os.environ.get("MAIL_DEFAULT_SENDER", "mannatmoonland@gmail.com"),
)
mail = Mail(app)
PASSWORD_RESET_LOCK = threading.Lock()
PASSWORD_RESET_ATTEMPTS = {}
PASSWORD_RESET_WINDOW_SECONDS = 3600
PASSWORD_RESET_IP_LIMIT = 10
PASSWORD_RESET_USER_LIMIT = 3
PASSWORD_RESET_RESPONSE = "If an eligible account was found, reset instructions will be sent to its saved email address."
LEGACY_SHARED_TEMP_PASSWORD = "password"

if not os.environ.get("FIREBASE_PROJECT_ID"):
    os.environ["FIREBASE_PROJECT_ID"] = "mannat-moon-construction"

if not os.environ.get("FIREBASE_SERVICE_ACCOUNT"):
    detected_path = find_firebase_service_account_path()
    if detected_path:
        os.environ["FIREBASE_SERVICE_ACCOUNT"] = detected_path

DEFAULT_FIREBASE_WEB_CONFIG = {
    "apiKey": "AIzaSyC45yuUwsyI4VF_UNGcZoMbf9lIXmDqbUA",
    "authDomain": "mannat-moon-construction.firebaseapp.com",
    "projectId": "mannat-moon-construction",
    "storageBucket": "mannat-moon-construction.firebasestorage.app",
    "messagingSenderId": "688328149099",
    "appId": "1:688328149099:web:0eb1e0df82c7e51e10092e",
    "measurementId": "G-215RJCKKGR",
}
ACTIVE_FIREBASE_CONFIG_PATH = Path(app.instance_path) / "firebase_active_project.json"
FIREBASE_WEB_CONFIG_REQUIRED_FIELDS = frozenset({"apiKey", "authDomain", "projectId", "appId"})
FIREBASE_WEB_CONFIG_OPTIONAL_FIELDS = frozenset({
    "storageBucket",
    "messagingSenderId",
    "measurementId",
})


def _validate_firebase_web_config(web_config, project_id):
    if not isinstance(web_config, dict):
        raise ValueError("Firebase Web configuration must be a JSON object.")
    unsupported = set(web_config) - FIREBASE_WEB_CONFIG_REQUIRED_FIELDS - FIREBASE_WEB_CONFIG_OPTIONAL_FIELDS
    if unsupported:
        raise ValueError("Firebase Web configuration contains unsupported configuration fields.")
    missing = FIREBASE_WEB_CONFIG_REQUIRED_FIELDS - set(web_config)
    if missing:
        raise ValueError("FIREBASE_WEB_CONFIG is missing required public configuration fields.")
    if any(
        not isinstance(value, str) or not value.strip()
        for value in web_config.values()
    ):
        raise ValueError("Firebase Web configuration values must be non-empty strings.")
    if web_config["projectId"] != project_id:
        raise ValueError("Firebase Admin and Web project IDs differ.")
    return web_config


def get_active_firebase_config():
    if ACTIVE_FIREBASE_CONFIG_PATH.is_file():
        try:
            with ACTIVE_FIREBASE_CONFIG_PATH.open("r", encoding="utf-8") as config_file:
                config = json.load(config_file)
            if not isinstance(config, dict) or not isinstance(config.get("web_config"), dict):
                raise ValueError("Invalid active Firebase project configuration.")
            _validate_firebase_web_config(config["web_config"], config.get("project_id"))
            return config
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise RuntimeError("The active Firebase project configuration could not be read.") from error
    project_id = os.environ.get("FIREBASE_PROJECT_ID") or "mannat-moon-construction"
    web_config_json = os.environ.get("FIREBASE_WEB_CONFIG")
    if web_config_json is not None:
        try:
            web_config = json.loads(web_config_json)
        except json.JSONDecodeError as error:
            raise RuntimeError("FIREBASE_WEB_CONFIG must contain valid JSON.") from error
        if not isinstance(web_config, dict):
            raise RuntimeError("FIREBASE_WEB_CONFIG must contain a JSON object.")
    else:
        web_config = dict(DEFAULT_FIREBASE_WEB_CONFIG)
    try:
        _validate_firebase_web_config(web_config, project_id)
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    return {
        "project_id": project_id,
        "service_account_path": os.environ.get("FIREBASE_SERVICE_ACCOUNT") or find_firebase_service_account_path(),
        "web_config": web_config,
    }


def save_active_firebase_config(config, store):
    _validate_firebase_web_config(config.get("web_config"), config.get("project_id"))
    ACTIVE_FIREBASE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = ACTIVE_FIREBASE_CONFIG_PATH.with_name(ACTIVE_FIREBASE_CONFIG_PATH.name + f".{uuid.uuid4().hex}.tmp")
    with temporary_path.open("w", encoding="utf-8") as config_file:
        json.dump(config, config_file, ensure_ascii=False, indent=2)
    os.replace(temporary_path, ACTIVE_FIREBASE_CONFIG_PATH)
    app._firebase_store = store
    app._firebase_store_key = (config["project_id"], config.get("service_account_path"))
    app.config["FIREBASE_PROJECT_ID"] = config["project_id"]
    app.config["FIREBASE_SERVICE_ACCOUNT"] = config.get("service_account_path")


def get_request_base_url():
    configured_base_url = app.config.get("PUBLIC_BASE_URL", "")
    if not configured_base_url:
        if IS_PRODUCTION_ENVIRONMENT:
            return None
        configured_base_url = "http://127.0.0.1:5020"

    try:
        parsed = urlsplit(configured_base_url)
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    try:
        parsed.port
    except ValueError:
        return None
    if IS_PRODUCTION_ENVIRONMENT and parsed.scheme != "https":
        return None
    return configured_base_url.rstrip("/")


def get_authenticated_form_csrf_token():
    user_id = session.get("user_id")
    session_id = session.get("session_id")
    if not isinstance(user_id, str) or not user_id or not isinstance(session_id, str) or not session_id:
        return ""

    binding = f"{user_id}:{session_id}"
    if session.get("authenticated_form_csrf_binding") != binding:
        session["authenticated_form_csrf"] = secrets.token_urlsafe(32)
        session["authenticated_form_csrf_binding"] = binding
    token = session.get("authenticated_form_csrf", "")
    return token if isinstance(token, str) else ""


@app.context_processor
def inject_authenticated_form_csrf_token():
    return {"authenticated_form_csrf_token": get_authenticated_form_csrf_token}


def require_authenticated_form_csrf(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return view(*args, **kwargs)
        user = get_current_user()
        if user:
            expected = get_authenticated_form_csrf_token()
            supplied = request.form.get("csrf_token", "")
            if (
                not expected
                or not isinstance(supplied, str)
                or not supplied
                or not hmac.compare_digest(expected, supplied)
            ):
                return "Request validation failed.", 400
        return view(*args, **kwargs)

    wrapped._requires_authenticated_form_csrf = True
    return wrapped


def allow_password_reset_request(remote_addr, user_id):
    now = time.monotonic()
    keys = (
        ("ip", hashlib.sha256(str(remote_addr or "unknown").encode("utf-8")).hexdigest(), PASSWORD_RESET_IP_LIMIT),
        ("user", hashlib.sha256(str(user_id or "").strip().encode("utf-8")).hexdigest(), PASSWORD_RESET_USER_LIMIT),
    )
    with PASSWORD_RESET_LOCK:
        for key, attempts in list(PASSWORD_RESET_ATTEMPTS.items()):
            while attempts and now - attempts[0] >= PASSWORD_RESET_WINDOW_SECONDS:
                attempts.popleft()
            if not attempts:
                PASSWORD_RESET_ATTEMPTS.pop(key, None)
        if any(len(PASSWORD_RESET_ATTEMPTS.get((scope, digest), ())) >= limit for scope, digest, limit in keys):
            return False
        for scope, digest, _ in keys:
            PASSWORD_RESET_ATTEMPTS.setdefault((scope, digest), deque()).append(now)
        while len(PASSWORD_RESET_ATTEMPTS) > 5000:
            PASSWORD_RESET_ATTEMPTS.pop(next(iter(PASSWORD_RESET_ATTEMPTS)))
    return True

BUILT_IN_STAFF_TYPES = {"super_admin", "office_staff", "supervisor"}
OPERATIONAL_PERMISSIONS = [
    "dashboard",
    "daily_reports",
    "trade_builder",
    "drawings",
    "labour_payments",
    "ledger_prints",
    "google_photos",
    "extract_google_photos_url",
    "image",
    "materials",
    "labour_muster",
]
ROLE_PERMISSIONS = {
    "super_admin": ["user_management", "all_pages", "password_reset_all", "super_admin_recovery"],
    "office_staff": list(OPERATIONAL_PERMISSIONS),
    "supervisor": list(OPERATIONAL_PERMISSIONS),
}
CAMERA_AUTH_WINDOW_SECONDS = 900
CAMERA_AUTH_MAX_ATTEMPTS = 5
CAMERA_AUTH_GLOBAL_MAX_ATTEMPTS = 200
CAMERA_AUTH_ASSERTION_TTL_SECONDS = 300
CAMERA_AUTH_ASSERTION_SALT = "mannat-moon-camera-auth-v1"
CAMERA_AUTH_DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))

# =========================================================
# BROWSER POOL FOR GOOGLE PHOTOS EXTRACTION
# =========================================================

class BrowserPool:
    def __init__(self, max_browsers=2):
        self.max_browsers = max_browsers
        self.browsers = []
        self.lock = threading.Lock()
        self._initialized = False

    def _initialize(self):
        if not self._initialized:
            playwright = sync_playwright().start()
            self.playwright = playwright
            self._initialized = True

    def get_browser(self):
        with self.lock:
            self._initialize()
            if self.browsers:
                return self.browsers.pop()
            if len(self.browsers) < self.max_browsers:
                browser = self.playwright.chromium.launch(headless=True)
                return browser
            return None

    def return_browser(self, browser):
        with self.lock:
            if browser and len(self.browsers) < self.max_browsers:
                self.browsers.append(browser)
            elif browser:
                browser.close()

    def close_all(self):
        with self.lock:
            for browser in self.browsers:
                browser.close()
            self.browsers = []
            if self._initialized:
                self.playwright.stop()
                self._initialized = False


# Global browser pool
browser_pool = BrowserPool(max_browsers=2)


# =========================================================
# URL CACHE FOR GOOGLE PHOTOS
# =========================================================

_url_cache = {}
_cache_lock = threading.Lock()
MAX_CACHE_SIZE = 1000


def get_cached_url(photos_url):
    with _cache_lock:
        return _url_cache.get(photos_url)


def cache_url(photos_url, direct_url):
    with _cache_lock:
        if len(_url_cache) >= MAX_CACHE_SIZE:
            oldest_key = next(iter(_url_cache))
            del _url_cache[oldest_key]
        _url_cache[photos_url] = direct_url


# =========================================================
# FIRESTORE BACKEND
# =========================================================

def normalize_user_status(value=None, default_active=True):
    if value is None:
        return "active" if default_active else "inactive"
    if isinstance(value, bool):
        return "active" if value else "inactive"
    status_value = str(value).strip().lower()
    if status_value in {"active", "enabled", "true", "1", "yes", "open"}:
        return "active"
    if status_value in {"inactive", "disabled", "false", "0", "no", "closed"}:
        return "inactive"
    return "active" if default_active else "inactive"


PROFILE_AVATAR_IDS = frozenset({
    "cute_cat", "puppy", "bear", "panda", "rabbit", "fox", "lion", "tiger",
    "elephant", "monkey", "owl", "penguin", "parrot", "chicken", "cow",
    "pig", "sheep", "horse", "goat", "duck", "whale", "dolphin", "octopus",
    "turtle", "shark", "dino", "trex", "unicorn", "dragon", "sloth",
    "raccoon", "koala", "hedgehog", "otter", "frog", "seal",
})
DEFAULT_PROFILE_AVATAR_ID = "cute_cat"


def password_reset_token_is_valid(user, token_hash, now=None):
    if not user or normalize_user_status(user.get("status"), default_active=bool(user.get("is_active", True))) != "active":
        return False
    stored_hash = user.get("password_reset_token_hash") or ""
    if not stored_hash or not token_hash or not hmac.compare_digest(stored_hash, token_hash):
        return False
    try:
        expires_at = datetime.fromisoformat(str(user.get("password_reset_expires_at", "")).replace("Z", "+00:00"))
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > (now or datetime.now(timezone.utc))


class InMemoryFirestoreStore:
    def __init__(self):
        self.users = {}
        self.trades = {}
        self.drawings_categories = {}
        self.drawings = {}
        self.custom_staff_types = {}
        self.audit_log = []
        self.camera_auth_attempts = {}
        self._password_reset_lock = threading.Lock()
        self._payment_lock = threading.RLock()
        self._trade_mutation_lock = threading.RLock()
        self._drawings_mutation_lock = threading.RLock()
        self._material_transaction_lock = threading.RLock()
        self.material_categories = {}
        self.materials = {}
        self.material_brands = {}
        self.suppliers = {}
        self.material_purchases = {}
        self.material_consumptions = {}

    def mutate_material_category(self, action, category_id, payload=None):
        with self._material_transaction_lock:
            existing = self.material_categories.get(category_id)
            if action == "delete":
                if existing is None:
                    return None
                _ensure_material_category_deletable(
                    category_id,
                    self.materials,
                    self.suppliers.values(),
                    self.material_purchases.values(),
                    self.material_consumptions.values(),
                )
                del self.material_categories[category_id]
                return {"id": category_id}

            if action == "create":
                if existing is not None:
                    if _material_category_create_matches(existing, payload):
                        return {"id": category_id, "duplicate": True}
                    raise MaterialMasterDataError(
                        "This category request ID has already been used.", 409
                    )
                if any(
                    normalized_material_name(category.get("name"))
                    == normalized_material_name(payload["name"])
                    for category in self.material_categories.values()
                ):
                    raise MaterialMasterDataError(
                        "A category with this name already exists.", 409
                    )
                now = iso_now()
                self.material_categories[category_id] = {
                    **payload,
                    "createdAt": now,
                    "updatedAt": now,
                }
                return {"id": category_id, "duplicate": False}

            if existing is None:
                return None
            if any(
                other_id != category_id
                and normalized_material_name(category.get("name"))
                == normalized_material_name(payload["name"])
                for other_id, category in self.material_categories.items()
            ):
                raise MaterialMasterDataError(
                    "A category with this name already exists.", 409
                )
            has_history = _material_category_has_transaction_history(
                category_id,
                self.materials,
                self.material_purchases.values(),
                self.material_consumptions.values(),
            )
            if has_history and payload["unit"] != str(existing.get("unit") or "").strip():
                raise MaterialMasterDataError(
                    "Category unit cannot be changed after transaction history exists.",
                    409,
                )
            existing.update(payload)
            existing["updatedAt"] = iso_now()
            return {"id": category_id}

    def mutate_material_supplier(self, action, supplier_id, payload=None):
        with self._material_transaction_lock:
            existing = self.suppliers.get(supplier_id)
            if action == "delete":
                if existing is None:
                    return None
                if any(
                    purchase.get("supplierId") == supplier_id
                    for purchase in self.material_purchases.values()
                ):
                    raise MaterialMasterDataError(
                        "This supplier is referenced by saved purchases.", 409
                    )
                del self.suppliers[supplier_id]
                return {"id": supplier_id}

            if action == "create":
                if existing is not None:
                    if _material_supplier_create_matches(existing, payload):
                        return {"id": supplier_id, "duplicate": True}
                    raise MaterialMasterDataError(
                        "This supplier request ID has already been used.", 409
                    )
            elif existing is None:
                return None

            _validate_material_supplier_relationships(
                payload,
                self.material_categories,
                self.materials,
                self.material_brands,
                self.material_purchases,
            )
            if action == "create":
                now = iso_now()
                self.suppliers[supplier_id] = {
                    **payload,
                    "createdAt": now,
                    "updatedAt": now,
                }
                return {"id": supplier_id, "duplicate": False}
            existing.update(payload)
            existing["updatedAt"] = iso_now()
            return {"id": supplier_id}

    def create_drawing_category(self, category_id, name):
        with self._drawings_mutation_lock:
            existing = self.drawings_categories.get(category_id)
            if existing is not None:
                if existing.get("name") == name:
                    return {"id": category_id, "duplicate": True}
                raise DrawingMutationError("This category request ID has already been used.", 409)
            if any(
                str(category.get("name", "")).strip().casefold() == name.casefold()
                for category in self.drawings_categories.values()
            ):
                raise DrawingMutationError("A category with this name already exists.", 409)
            sort_order = max(
                (
                    order
                    for order in (
                        _drawing_sort_order(category)
                        for category in self.drawings_categories.values()
                    )
                    if order is not None
                ),
                default=-1,
            ) + 1
            now = iso_now()
            self.drawings_categories[category_id] = {
                "name": name,
                "sortOrder": sort_order,
                "createdAt": now,
                "updatedAt": now,
            }
            return {"id": category_id, "duplicate": False}

    def update_drawing_category(self, category_id, name):
        with self._drawings_mutation_lock:
            category = self.drawings_categories.get(category_id)
            if category is None:
                return None
            if any(
                other_id != category_id
                and str(other.get("name", "")).strip().casefold() == name.casefold()
                for other_id, other in self.drawings_categories.items()
            ):
                raise DrawingMutationError("A category with this name already exists.", 409)
            now = iso_now()
            category.update({"name": name, "updatedAt": now})
            for drawing in self.drawings.values():
                if drawing.get("categoryId") == category_id:
                    drawing.update({"categoryName": name, "updatedAt": now})
            return {"id": category_id, "name": name}

    def delete_drawing_category(self, category_id):
        with self._drawings_mutation_lock:
            if category_id not in self.drawings_categories:
                return False
            if any(drawing.get("categoryId") == category_id for drawing in self.drawings.values()):
                raise DrawingMutationError(
                    "Categories containing drawings cannot be deleted.", 409
                )
            del self.drawings_categories[category_id]
            return True

    def reorder_drawing_categories(self, category_ids):
        with self._drawings_mutation_lock:
            if set(category_ids) != set(self.drawings_categories) or len(category_ids) != len(self.drawings_categories):
                raise DrawingMutationError("The category list changed. Reload and try again.", 409)
            for sort_order, category_id in enumerate(category_ids):
                self.drawings_categories[category_id]["sortOrder"] = sort_order
            return True

    def create_drawing(self, drawing_id, payload):
        with self._drawings_mutation_lock:
            existing = self.drawings.get(drawing_id)
            if existing is not None:
                if all(existing.get(key) == value for key, value in payload.items()):
                    return {"id": drawing_id, "duplicate": True}
                raise DrawingMutationError("This drawing request ID has already been used.", 409)
            category = self.drawings_categories.get(payload["categoryId"])
            if category is None:
                raise DrawingMutationError("Select an existing drawing category.", 400)
            normalized = _drawing_create_fields(payload, category, self.drawings.values())
            now = iso_now()
            self.drawings[drawing_id] = {
                **normalized,
                "createdAt": now,
                "updatedAt": now,
            }
            return {"id": drawing_id, "duplicate": False}

    def update_drawing(self, drawing_id, payload):
        with self._drawings_mutation_lock:
            drawing = self.drawings.get(drawing_id)
            if drawing is None:
                return None
            category = self.drawings_categories.get(payload["categoryId"])
            if category is None:
                raise DrawingMutationError("Select an existing drawing category.", 400)
            category_drawings = [
                {"id": item_id, **item}
                for item_id, item in self.drawings.items()
                if item.get("categoryId") == payload["categoryId"]
            ]
            normalized = _drawing_update_fields(
                payload, category, drawing, category_drawings, drawing_id
            )
            drawing.update(normalized)
            drawing["updatedAt"] = iso_now()
            return {"id": drawing_id}

    def delete_drawing(self, drawing_id):
        with self._drawings_mutation_lock:
            if drawing_id not in self.drawings:
                return False
            del self.drawings[drawing_id]
            return True

    def reorder_drawings(self, category_id, drawing_ids):
        with self._drawings_mutation_lock:
            category_drawings = {
                drawing_id: drawing
                for drawing_id, drawing in self.drawings.items()
                if drawing.get("categoryId") == category_id
            }
            if (
                set(drawing_ids) != set(category_drawings)
                or len(drawing_ids) != len(category_drawings)
            ):
                raise DrawingMutationError("The drawing list changed. Reload and try again.", 409)
            for sort_order, drawing_id in enumerate(drawing_ids):
                category_drawings[drawing_id]["sortOrder"] = sort_order
            return True

    def _normalize_user(self, user):
        if user is None:
            return None
        username = user.get("username") or ""
        is_active = user.get("is_active") if "is_active" in user else normalize_user_status(user.get("status")) == "active"
        normalized_user = {
            "id": user.get("id"),
            "username": username,
            "username_lower": str(username).strip().lower(),
            "full_name": user.get("full_name", ""),
            "email": user.get("email"),
            "recovery_email": user.get("recovery_email"),
            "role": user.get("role"),
            "custom_staff_type": user.get("custom_staff_type", ""),
            "password_hash": user.get("password_hash", ""),
            "must_change_password": bool(user.get("must_change_password", False)),
            "status": normalize_user_status(user.get("status"), default_active=bool(is_active)),
            "is_active": bool(is_active),
            "active_sessions": list(user.get("active_sessions", [])),
            "authorization_version": get_authorization_version(user),
            "created_at": user.get("created_at"),
            "updated_at": user.get("updated_at"),
            "last_login_at": user.get("last_login_at"),
            "last_password_change": user.get("last_password_change"),
            "recovery_code_hash": user.get("recovery_code_hash"),
            "recovery_code_used": bool(user.get("recovery_code_used", False)),
            "password_reset_token_hash": user.get("password_reset_token_hash"),
            "password_reset_expires_at": user.get("password_reset_expires_at"),
            "permissions": list(user.get("permissions", [])),
            "created_by": user.get("created_by"),
            "avatarId": user.get("avatarId", DEFAULT_PROFILE_AVATAR_ID),
        }
        if user.get("username_lower"):
            normalized_user["username_lower"] = str(user.get("username_lower")).strip().lower()
        return normalized_user

    def get_user_by_username(self, username):
        username = (username or "").strip().lower()
        for user in self.users.values():
            if str(user.get("username", "")).strip().lower() == username:
                return self._normalize_user(user)
        return None

    def get_user(self, user_id):
        user = self.users.get(user_id)
        return self._normalize_user(user)

    def list_users(self):
        return [self._normalize_user(user) for user in self.users.values()]

    def list_trades(self):
        return [
            {**dict(trade), "id": trade_id}
            for trade_id, trade in self.trades.items()
        ]

    def save_user(self, user):
        if not user.get("id"):
            raise ValueError("User id is required")
        self.users[user["id"]] = self._normalize_user(user)
        return self.users[user["id"]]

    def delete_user(self, user_id):
        if user_id not in self.users:
            return False
        del self.users[user_id]
        return True

    def create_trade(self, trade_id, payload):
        with self._trade_mutation_lock:
            existing = self.trades.get(trade_id)
            if existing is not None:
                if _trade_create_matches(existing, payload):
                    return {"id": trade_id, "duplicate": True}
                raise TradeMutationError("This trade request ID has already been used.", 409)
            now = iso_now()
            self.trades[trade_id] = {
                **payload,
                "contractItems": [],
                "paymentTransactions": [],
                "createdAt": now,
                "updatedAt": now,
            }
            return {"id": trade_id, "duplicate": False}

    def update_trade_configuration(self, trade_id, payload):
        with self._trade_mutation_lock:
            trade = self.trades.get(trade_id)
            if trade is None:
                return None
            trade.update(payload)
            trade["updatedAt"] = iso_now()
            return dict(trade)

    def rename_trade(self, trade_id, name):
        with self._trade_mutation_lock:
            trade = self.trades.get(trade_id)
            if trade is None:
                return None
            trade.update({"name": name, "updatedAt": iso_now()})
            return {"id": trade_id, "name": name, "updatedAt": trade["updatedAt"]}

    def delete_trade(self, trade_id):
        with self._trade_mutation_lock:
            if trade_id not in self.trades:
                return False
            del self.trades[trade_id]
            return True

    def mutate_payment_transaction(self, trade_id, action, transaction_id, payment_data):
        with self._payment_lock:
            trade = self.trades.get(trade_id)
            result = _apply_payment_transaction_mutation(
                trade,
                action,
                transaction_id,
                payment_data,
            )
            if result is None:
                return None
            updated_trade, payment_transactions, transaction = result
            updated_trade["updatedAt"] = iso_now()
            self.trades[trade_id] = updated_trade
            return {
                "paymentTransactions": payment_transactions,
                "transaction": transaction,
            }

    def mutate_contract_items(self, trade_id, action, contract_id=None, contract_data=None):
        with self._payment_lock:
            trade = self.trades.get(trade_id)
            result = _apply_contract_items_mutation(
                trade,
                action,
                contract_id,
                contract_data,
            )
            if result is None:
                return None
            updated_trade, contract_items = result
            updated_trade["updatedAt"] = iso_now()
            self.trades[trade_id] = updated_trade
            return contract_items

    def mutate_material_transaction(self, kind, action, transaction_id, payload=None):
        collection_name = _material_transaction_collection(kind)
        collection = getattr(self, collection_name)
        with self._material_transaction_lock:
            existing = collection.get(transaction_id)
            if action == "delete":
                if not existing:
                    return None
                del collection[transaction_id]
                return {"id": transaction_id}

            if action == "edit" and not existing:
                return None

            category_id = (
                payload.get("categoryId")
                if isinstance(payload, dict) and "categoryId" in payload
                else (existing or {}).get("categoryId")
            )
            if not _valid_material_id(category_id):
                raise MaterialTransactionError("Select a valid material category.")
            category_value = self.material_categories.get(category_id)
            category = ({**category_value, "id": category_id} if category_value else None)
            supplier_id = (
                payload.get("supplierId", "")
                if isinstance(payload, dict)
                else (existing or {}).get("supplierId", "")
            )
            if supplier_id and not _valid_material_id(supplier_id):
                raise MaterialTransactionError("Select a valid supplier.")
            supplier_value = self.suppliers.get(supplier_id) if supplier_id else None
            supplier = ({**supplier_value, "id": supplier_id} if supplier_value else None)
            brand_options = _material_brand_options(
                category,
                [
                    {"id": material_id, **material}
                    for material_id, material in self.materials.items()
                ],
                [
                    {"id": brand_id, **brand}
                    for brand_id, brand in self.material_brands.items()
                ],
                [
                    {"id": purchase_id, **purchase}
                    for purchase_id, purchase in self.material_purchases.items()
                ],
            )
            normalized_payload = _validate_material_transaction_payload(
                kind,
                payload,
                category,
                supplier,
                existing,
                brand_options,
            )
            if action == "create":
                if existing:
                    if _material_transaction_matches(existing, normalized_payload):
                        return {"id": transaction_id, "payload": existing, "duplicate": True}
                    raise MaterialTransactionError("This transaction request has already been used.", 409)
                if kind == "consumptions":
                    _validate_material_stock(
                        category,
                        normalized_payload,
                        None,
                        self.material_purchases.values(),
                        self.material_consumptions.values(),
                        self.materials,
                    )
                saved = {**normalized_payload, "createdAt": iso_now(), "updatedAt": iso_now()}
                collection[transaction_id] = saved
                return {"id": transaction_id, "payload": saved, "duplicate": False}

            if kind == "consumptions":
                _validate_material_stock(
                    category,
                    normalized_payload,
                    existing,
                    self.material_purchases.values(),
                    self.material_consumptions.values(),
                    self.materials,
                )
            existing.update(normalized_payload)
            existing["updatedAt"] = iso_now()
            return {"id": transaction_id, "payload": existing, "duplicate": False}

    def update_user_profile(self, user_id, full_name, email, recovery_email):
        user = self.users.get(user_id)
        if not user:
            return None
        user["full_name"] = full_name
        user["email"] = email
        user["recovery_email"] = recovery_email
        return self._normalize_user(user)

    def consume_password_reset(self, user_id, token_hash, password_hash, now):
        with self._password_reset_lock:
            user = self.users.get(user_id)
            if not password_reset_token_is_valid(user, token_hash, now):
                return False
            user.update({
                "password_hash": password_hash,
                "must_change_password": False,
                "password_reset_token_hash": None,
                "password_reset_expires_at": None,
                "active_sessions": [],
                "authorization_version": get_authorization_version(user) + 1,
                "last_password_change": iso_now(),
                "updated_at": iso_now(),
            })
            self.users[user_id] = self._normalize_user(user)
            return True

    def save_custom_staff_type(self, staff_type):
        if not staff_type.get("id"):
            raise ValueError("Staff type id is required")
        self.custom_staff_types[staff_type["id"]] = dict(staff_type)
        return dict(staff_type)

    def delete_custom_staff_type(self, staff_type_id):
        self.custom_staff_types.pop(staff_type_id, None)

    def list_custom_staff_types(self):
        return [dict(item) for item in self.custom_staff_types.values()]

    def append_audit(self, entry):
        self.audit_log.append({
            "id": entry.get("id") or uuid.uuid4().hex,
            "actor_id": entry.get("actor_id"),
            "target_user_id": entry.get("target_user_id"),
            "action": entry.get("action"),
            "details": entry.get("details"),
            "timestamp": entry.get("timestamp") or iso_now(),
        })
        return self.audit_log[-1]

    def consume_camera_auth_attempt(self, subject_fingerprint=None, include_global=True, now=None):
        now = now or time.time()
        keys_and_limits = []
        if subject_fingerprint is not None:
            keys_and_limits.append((f"subject:{subject_fingerprint}", CAMERA_AUTH_MAX_ATTEMPTS))
        if include_global:
            keys_and_limits.append(("global", CAMERA_AUTH_GLOBAL_MAX_ATTEMPTS))
        with self._password_reset_lock:
            current = {}
            for key, limit in keys_and_limits:
                window_start, count = self.camera_auth_attempts.get(key, (now, 0))
                if now - window_start >= CAMERA_AUTH_WINDOW_SECONDS:
                    window_start, count = now, 0
                if count >= limit:
                    return False
                current[key] = (window_start, count + 1)
            self.camera_auth_attempts.update(current)
        return True


class FirebaseFirestoreStore:
    def __init__(self, client=None, project_id=None, service_account_path=None, app_name=None):
        self.project_id = project_id or os.environ.get("FIREBASE_PROJECT_ID") or "mannat-moon-construction"
        self.service_account_path = service_account_path or os.environ.get("FIREBASE_SERVICE_ACCOUNT") or find_firebase_service_account_path()
        self.firebase_app = None
        self.db = client
        if self.db is None:
            self.firebase_app, self.db = self._initialize_client(app_name)

    def _initialize_client(self, app_name=None):
        if firebase_admin is None:
            raise RuntimeError("firebase-admin is not installed")
        service_account_path = self.service_account_path
        if service_account_path and not os.path.isabs(service_account_path):
            service_account_path = os.path.abspath(os.path.join(os.path.dirname(__file__), service_account_path))
        if service_account_path and os.path.exists(service_account_path):
            cred = credentials.Certificate(service_account_path)
        else:
            cred = credentials.ApplicationDefault()

        firebase_app = None
        if app_name is None:
            try:
                firebase_app = firebase_admin.get_app()
                if firebase_app.project_id != self.project_id:
                    firebase_app = None
            except ValueError:
                firebase_app = firebase_admin.initialize_app(cred, {"projectId": self.project_id})
        if firebase_app is None:
            firebase_app = firebase_admin.initialize_app(
                cred,
                {"projectId": self.project_id},
                name=app_name or f"mannat-{self.project_id}-{uuid.uuid4().hex[:10]}",
            )
        auth_transport = os.environ.get("FIRESTORE_GRPC_AUTH_TRANSPORT", "").strip().lower()
        if auth_transport not in {"", "urllib3"}:
            raise RuntimeError("FIRESTORE_GRPC_AUTH_TRANSPORT must be 'urllib3' when set.")
        if auth_transport == "urllib3":
            if _Urllib3FirestoreClient is None:
                raise RuntimeError("The urllib3 Firestore authentication transport is unavailable.")
            db = _Urllib3FirestoreClient(
                credentials=firebase_app.credential.get_credential(),
                project=firebase_app.project_id,
            )
        else:
            db = firestore.client(app=firebase_app)
        return firebase_app, db

    def _collection(self, name):
        return self.db.collection(name)

    def list_trades(self):
        return [
            {**(snapshot.to_dict() or {}), "id": snapshot.id}
            for snapshot in self._collection("trades").stream()
        ]

    def mutate_material_category(self, action, category_id, payload=None):
        categories = self._collection("material_categories")
        materials = self._collection("materials")
        suppliers = self._collection("suppliers")
        purchases = self._collection("material_purchases")
        consumptions = self._collection("material_consumptions")
        reference = categories.document(category_id)
        firestore_transaction = self.db.transaction()

        @firestore.transactional
        def mutate(transaction):
            snapshot = transaction.get(reference)
            if action == "delete":
                if not snapshot.exists:
                    return None
                material_snapshots = list(
                    transaction.get(materials.where("categoryId", "==", category_id))
                )
                supplier_snapshots = list(transaction.get(suppliers))
                purchase_snapshots = list(
                    transaction.get(purchases.where("categoryId", "==", category_id))
                )
                consumption_snapshots = list(
                    transaction.get(consumptions.where("categoryId", "==", category_id))
                )
                _ensure_material_category_deletable(
                    category_id,
                    {
                        item.id: item.to_dict() or {}
                        for item in material_snapshots
                    },
                    [item.to_dict() or {} for item in supplier_snapshots],
                    [item.to_dict() or {} for item in purchase_snapshots],
                    [item.to_dict() or {} for item in consumption_snapshots],
                )
                transaction.delete(reference)
                return {"id": category_id}

            existing = snapshot.to_dict() or {} if snapshot.exists else None
            if action == "create" and existing is not None:
                if _material_category_create_matches(existing, payload):
                    return {"id": category_id, "duplicate": True}
                raise MaterialMasterDataError(
                    "This category request ID has already been used.", 409
                )
            if action == "edit" and existing is None:
                return None

            category_snapshots = list(transaction.get(categories))
            if any(
                item.id != category_id
                and normalized_material_name((item.to_dict() or {}).get("name"))
                == normalized_material_name(payload["name"])
                for item in category_snapshots
            ):
                raise MaterialMasterDataError(
                    "A category with this name already exists.", 409
                )

            if action == "edit":
                material_snapshots = list(
                    transaction.get(materials.where("categoryId", "==", category_id))
                )
                has_history = bool(
                    list(transaction.get(purchases.where("categoryId", "==", category_id)))
                    or list(transaction.get(consumptions.where("categoryId", "==", category_id)))
                )
                for material_snapshot in material_snapshots:
                    has_history = has_history or bool(
                        list(
                            transaction.get(
                                purchases.where("materialId", "==", material_snapshot.id)
                            )
                        )
                        or list(
                            transaction.get(
                                consumptions.where("materialId", "==", material_snapshot.id)
                            )
                        )
                    )
                if has_history and payload["unit"] != str(existing.get("unit") or "").strip():
                    raise MaterialMasterDataError(
                        "Category unit cannot be changed after transaction history exists.",
                        409,
                    )
                transaction.update(reference, {**payload, "updatedAt": firestore.SERVER_TIMESTAMP})
                return {"id": category_id}

            transaction.create(reference, {
                **payload,
                "createdAt": firestore.SERVER_TIMESTAMP,
                "updatedAt": firestore.SERVER_TIMESTAMP,
            })
            return {"id": category_id, "duplicate": False}

        return mutate(firestore_transaction)

    def mutate_material_supplier(self, action, supplier_id, payload=None):
        suppliers = self._collection("suppliers")
        categories = self._collection("material_categories")
        materials = self._collection("materials")
        brands = self._collection("material_brands")
        purchases = self._collection("material_purchases")
        reference = suppliers.document(supplier_id)
        firestore_transaction = self.db.transaction()

        @firestore.transactional
        def mutate(transaction):
            snapshot = transaction.get(reference)
            existing = snapshot.to_dict() or {} if snapshot.exists else None
            if action == "delete":
                if existing is None:
                    return None
                if list(transaction.get(purchases.where("supplierId", "==", supplier_id))):
                    raise MaterialMasterDataError(
                        "This supplier is referenced by saved purchases.", 409
                    )
                transaction.delete(reference)
                return {"id": supplier_id}
            if action == "create" and existing is not None:
                if _material_supplier_create_matches(existing, payload):
                    return {"id": supplier_id, "duplicate": True}
                raise MaterialMasterDataError(
                    "This supplier request ID has already been used.", 409
                )
            if action == "edit" and existing is None:
                return None

            category_values = {}
            for category_id in payload["categoryIds"]:
                category_snapshot = transaction.get(categories.document(category_id))
                if not category_snapshot.exists:
                    raise MaterialMasterDataError("Select a valid material category.")
                category_values[category_id] = {
                    "id": category_id,
                    **(category_snapshot.to_dict() or {}),
                }

            materials_by_category = {}
            legacy_brand_values = []
            purchase_values = []
            for category_id, category in category_values.items():
                if isinstance(category.get("brands"), list):
                    continue
                material_snapshots = list(
                    transaction.get(materials.where("categoryId", "==", category_id))
                )
                materials_by_category[category_id] = [
                    {"id": item.id, **(item.to_dict() or {})}
                    for item in material_snapshots
                ]
                for material_snapshot in material_snapshots:
                    legacy_brand_values.extend(
                        {
                            "id": item.id,
                            **(item.to_dict() or {}),
                        }
                        for item in transaction.get(
                            brands.where("materialId", "==", material_snapshot.id)
                        )
                    )
                    purchase_values.extend(
                        {
                            "id": item.id,
                            **(item.to_dict() or {}),
                        }
                        for item in transaction.get(
                            purchases.where("materialId", "==", material_snapshot.id)
                        )
                    )
                purchase_values.extend(
                    {"id": item.id, **(item.to_dict() or {})}
                    for item in transaction.get(
                        purchases.where("categoryId", "==", category_id)
                    )
                )
            _validate_material_supplier_relationships(
                payload,
                category_values,
                materials_by_category,
                legacy_brand_values,
                purchase_values,
            )
            if action == "create":
                transaction.create(reference, {
                    **payload,
                    "createdAt": firestore.SERVER_TIMESTAMP,
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                })
                return {"id": supplier_id, "duplicate": False}
            transaction.update(
                reference, {**payload, "updatedAt": firestore.SERVER_TIMESTAMP}
            )
            return {"id": supplier_id}

        return mutate(firestore_transaction)

    def create_drawing_category(self, category_id, name):
        reference = self._collection("drawings_categories").document(category_id)
        categories = self._collection("drawings_categories")
        firestore_transaction = self.db.transaction()

        @firestore.transactional
        def create(transaction):
            existing = transaction.get(reference)
            if existing.exists:
                if (existing.to_dict() or {}).get("name") == name:
                    return {"id": category_id, "duplicate": True}
                raise DrawingMutationError("This category request ID has already been used.", 409)
            category_snapshots = list(transaction.get(categories))
            if any(
                str((snapshot.to_dict() or {}).get("name", "")).strip().casefold()
                == name.casefold()
                for snapshot in category_snapshots
            ):
                raise DrawingMutationError("A category with this name already exists.", 409)
            sort_order = max(
                (
                    order
                    for order in (
                        _drawing_sort_order(snapshot)
                        for snapshot in category_snapshots
                    )
                    if order is not None
                ),
                default=-1,
            ) + 1
            now = iso_now()
            transaction.create(reference, {
                "name": name,
                "sortOrder": sort_order,
                "createdAt": now,
                "updatedAt": now,
            })
            return {"id": category_id, "duplicate": False}

        return create(firestore_transaction)

    def update_drawing_category(self, category_id, name):
        category_reference = self._collection("drawings_categories").document(category_id)
        drawing_query = self._collection("drawings").where("categoryId", "==", category_id)
        categories = self._collection("drawings_categories")
        firestore_transaction = self.db.transaction()

        @firestore.transactional
        def update(transaction):
            category_snapshot = transaction.get(category_reference)
            if not category_snapshot.exists:
                return None
            category_snapshots = list(transaction.get(categories))
            if any(
                snapshot.id != category_id
                and str((snapshot.to_dict() or {}).get("name", "")).strip().casefold()
                == name.casefold()
                for snapshot in category_snapshots
            ):
                raise DrawingMutationError("A category with this name already exists.", 409)
            drawing_snapshots = list(transaction.get(drawing_query))
            if len(drawing_snapshots) > 499:
                raise DrawingMutationError(
                    "This category has too many drawings to update safely.", 409
                )
            now = iso_now()
            transaction.update(category_reference, {"name": name, "updatedAt": now})
            for snapshot in drawing_snapshots:
                transaction.update(
                    self._collection("drawings").document(snapshot.id),
                    {"categoryName": name, "updatedAt": now},
                )
            return {"id": category_id, "name": name}

        return update(firestore_transaction)

    def delete_drawing_category(self, category_id):
        category_reference = self._collection("drawings_categories").document(category_id)
        drawing_query = self._collection("drawings").where("categoryId", "==", category_id)
        firestore_transaction = self.db.transaction()

        @firestore.transactional
        def delete(transaction):
            category_snapshot = transaction.get(category_reference)
            if not category_snapshot.exists:
                return False
            if list(transaction.get(drawing_query)):
                raise DrawingMutationError(
                    "Categories containing drawings cannot be deleted.", 409
                )
            transaction.delete(category_reference)
            return True

        return delete(firestore_transaction)

    def reorder_drawing_categories(self, category_ids):
        categories = self._collection("drawings_categories")
        firestore_transaction = self.db.transaction()

        @firestore.transactional
        def reorder(transaction):
            snapshots = list(transaction.get(categories))
            by_id = {snapshot.id: snapshot for snapshot in snapshots}
            if set(category_ids) != set(by_id) or len(category_ids) != len(by_id):
                raise DrawingMutationError(
                    "The category list changed. Reload and try again.", 409
                )
            if len(category_ids) > 500:
                raise DrawingMutationError("Too many categories to reorder safely.", 409)
            for sort_order, category_id in enumerate(category_ids):
                transaction.update(
                    categories.document(category_id), {"sortOrder": sort_order}
                )
            return True

        return reorder(firestore_transaction)

    def create_drawing(self, drawing_id, payload):
        drawing_reference = self._collection("drawings").document(drawing_id)
        category_reference = self._collection("drawings_categories").document(
            payload["categoryId"]
        )
        drawings = self._collection("drawings")
        firestore_transaction = self.db.transaction()

        @firestore.transactional
        def create(transaction):
            existing_snapshot = transaction.get(drawing_reference)
            category_snapshot = transaction.get(category_reference)
            if not category_snapshot.exists:
                raise DrawingMutationError("Select an existing drawing category.", 400)
            category = category_snapshot.to_dict() or {}
            if existing_snapshot.exists:
                existing = existing_snapshot.to_dict() or {}
                if all(existing.get(key) == value for key, value in payload.items()):
                    return {"id": drawing_id, "duplicate": True}
                raise DrawingMutationError("This drawing request ID has already been used.", 409)
            category_drawings = list(
                transaction.get(drawings.where("categoryId", "==", payload["categoryId"]))
            )
            normalized = _drawing_create_fields(payload, category, category_drawings)
            now = iso_now()
            transaction.create(drawing_reference, {
                **normalized,
                "createdAt": now,
                "updatedAt": now,
            })
            return {"id": drawing_id, "duplicate": False}

        return create(firestore_transaction)

    def update_drawing(self, drawing_id, payload):
        drawing_reference = self._collection("drawings").document(drawing_id)
        category_reference = self._collection("drawings_categories").document(
            payload["categoryId"]
        )
        drawings = self._collection("drawings")
        firestore_transaction = self.db.transaction()

        @firestore.transactional
        def update(transaction):
            drawing_snapshot = transaction.get(drawing_reference)
            if not drawing_snapshot.exists:
                return None
            category_snapshot = transaction.get(category_reference)
            if not category_snapshot.exists:
                raise DrawingMutationError("Select an existing drawing category.", 400)
            existing = drawing_snapshot.to_dict() or {}
            category = category_snapshot.to_dict() or {}
            category_drawings = list(
                transaction.get(drawings.where("categoryId", "==", payload["categoryId"]))
            )
            normalized = _drawing_update_fields(
                payload, category, existing, category_drawings, drawing_id
            )
            transaction.update(drawing_reference, {**normalized, "updatedAt": iso_now()})
            return {"id": drawing_id}

        return update(firestore_transaction)

    def delete_drawing(self, drawing_id):
        reference = self._collection("drawings").document(drawing_id)
        firestore_transaction = self.db.transaction()

        @firestore.transactional
        def delete(transaction):
            snapshot = transaction.get(reference)
            if not snapshot.exists:
                return False
            transaction.delete(reference)
            return True

        return delete(firestore_transaction)

    def reorder_drawings(self, category_id, drawing_ids):
        drawings = self._collection("drawings")
        query = drawings.where("categoryId", "==", category_id)
        firestore_transaction = self.db.transaction()

        @firestore.transactional
        def reorder(transaction):
            snapshots = list(transaction.get(query))
            by_id = {snapshot.id: snapshot for snapshot in snapshots}
            if set(drawing_ids) != set(by_id) or len(drawing_ids) != len(by_id):
                raise DrawingMutationError(
                    "The drawing list changed. Reload and try again.", 409
                )
            if len(drawing_ids) > 500:
                raise DrawingMutationError("Too many drawings to reorder safely.", 409)
            for sort_order, drawing_id in enumerate(drawing_ids):
                transaction.update(
                    drawings.document(drawing_id), {"sortOrder": sort_order}
                )
            return True

        return reorder(firestore_transaction)

    def create_trade(self, trade_id, payload):
        reference = self._collection("trades").document(trade_id)
        transaction = self.db.transaction()

        @firestore.transactional
        def create(transaction):
            snapshot = reference.get(transaction=transaction)
            if snapshot.exists:
                if _trade_create_matches(snapshot.to_dict() or {}, payload):
                    return {"id": trade_id, "duplicate": True}
                raise TradeMutationError("This trade request ID has already been used.", 409)
            now = iso_now()
            transaction.create(reference, {
                **payload,
                "contractItems": [],
                "paymentTransactions": [],
                "createdAt": now,
                "updatedAt": now,
            })
            return {"id": trade_id, "duplicate": False}

        return create(transaction)

    def update_trade_configuration(self, trade_id, payload):
        reference = self._collection("trades").document(trade_id)
        transaction = self.db.transaction()

        @firestore.transactional
        def update(transaction):
            snapshot = reference.get(transaction=transaction)
            if not snapshot.exists:
                return None
            now = iso_now()
            transaction.update(reference, {**payload, "updatedAt": now})
            return {"id": trade_id, **payload, "updatedAt": now}

        return update(transaction)

    def rename_trade(self, trade_id, name):
        reference = self._collection("trades").document(trade_id)
        transaction = self.db.transaction()

        @firestore.transactional
        def rename(transaction):
            snapshot = reference.get(transaction=transaction)
            if not snapshot.exists:
                return None
            now = iso_now()
            transaction.update(reference, {"name": name, "updatedAt": now})
            return {"id": trade_id, "name": name, "updatedAt": now}

        return rename(transaction)

    def delete_trade(self, trade_id):
        reference = self._collection("trades").document(trade_id)
        transaction = self.db.transaction()

        @firestore.transactional
        def delete(transaction):
            snapshot = reference.get(transaction=transaction)
            if not snapshot.exists:
                return False
            transaction.delete(reference)
            return True

        return delete(transaction)

    def mutate_payment_transaction(self, trade_id, action, transaction_id, payment_data):
        trade_reference = self._collection("trades").document(trade_id)
        firestore_transaction = self.db.transaction()

        @firestore.transactional
        def mutate(transaction):
            snapshot = trade_reference.get(transaction=transaction)
            if not snapshot.exists:
                return None
            trade = snapshot.to_dict()
            result = _apply_payment_transaction_mutation(
                trade,
                action,
                transaction_id,
                payment_data,
            )
            if result is None:
                return None
            _updated_trade, payment_transactions, changed_transaction = result
            transaction.update(trade_reference, {
                "paymentTransactions": payment_transactions,
                "updatedAt": iso_now(),
            })
            return {
                "paymentTransactions": payment_transactions,
                "transaction": changed_transaction,
            }

        return mutate(firestore_transaction)

    def mutate_contract_items(self, trade_id, action, contract_id=None, contract_data=None):
        trade_reference = self._collection("trades").document(trade_id)
        firestore_transaction = self.db.transaction()

        @firestore.transactional
        def mutate(transaction):
            snapshot = trade_reference.get(transaction=transaction)
            if not snapshot.exists:
                return None
            result = _apply_contract_items_mutation(
                snapshot.to_dict(),
                action,
                contract_id,
                contract_data,
            )
            if result is None:
                return None
            _updated_trade, contract_items = result
            transaction.update(trade_reference, {
                "contractItems": contract_items,
                "updatedAt": iso_now(),
            })
            return contract_items

        return mutate(firestore_transaction)

    def mutate_material_transaction(self, kind, action, transaction_id, payload=None):
        collection_name = _material_transaction_collection(kind)
        target_reference = self._collection(collection_name).document(transaction_id)
        firestore_transaction = self.db.transaction()

        @firestore.transactional
        def mutate(transaction):
            existing_snapshot = transaction.get(target_reference)
            if action == "delete":
                if not existing_snapshot.exists:
                    return None
                transaction.delete(target_reference)
                return {"id": transaction_id}

            existing = existing_snapshot.to_dict() if existing_snapshot.exists else None
            if action == "edit" and existing is None:
                return None
            category_id = (
                payload.get("categoryId")
                if isinstance(payload, dict) and "categoryId" in payload
                else (existing or {}).get("categoryId")
            )
            if not _valid_material_id(category_id):
                raise MaterialTransactionError("Select a valid material category.")
            category_reference = self._collection("material_categories").document(category_id)
            category_snapshot = transaction.get(category_reference)
            category = (
                {"id": category_id, **(category_snapshot.to_dict() or {})}
                if category_snapshot.exists else None
            )
            supplier_id = (
                payload.get("supplierId", "")
                if isinstance(payload, dict)
                else (existing or {}).get("supplierId", "")
            )
            supplier = None
            if supplier_id:
                if not _valid_material_id(supplier_id):
                    raise MaterialTransactionError("Select a valid supplier.")
                supplier_snapshot = transaction.get(self._collection("suppliers").document(supplier_id))
                supplier = (
                    {"id": supplier_id, **(supplier_snapshot.to_dict() or {})}
                    if supplier_snapshot.exists else None
                )

            material_snapshots = list(transaction.get(
                self._collection("materials").where("categoryId", "==", category_id)
            )) if category else []
            category_materials = [
                {"id": snapshot.id, **(snapshot.to_dict() or {})}
                for snapshot in material_snapshots
            ]
            legacy_brands = []
            for material in category_materials:
                brand_snapshots = transaction.get(
                    self._collection("material_brands").where("materialId", "==", material["id"])
                )
                legacy_brands.extend(
                    {"id": snapshot.id, **(snapshot.to_dict() or {})}
                    for snapshot in brand_snapshots
                )

            purchase_snapshots = {}
            if category:
                purchase_snapshots.update({
                    snapshot.id: snapshot
                    for snapshot in transaction.get(
                        self._collection("material_purchases").where("categoryId", "==", category_id)
                    )
                })
                for material in category_materials:
                    for snapshot in transaction.get(
                        self._collection("material_purchases").where("materialId", "==", material["id"])
                    ):
                        purchase_snapshots[snapshot.id] = snapshot
            category_purchases = list(purchase_snapshots.values())
            purchase_data = [
                {
                    "id": snapshot.id,
                    **(snapshot.to_dict() or {}),
                    "categoryId": (snapshot.to_dict() or {}).get("categoryId") or category_id,
                }
                for snapshot in category_purchases
            ]
            brand_options = _material_brand_options(
                category,
                category_materials,
                legacy_brands,
                purchase_data,
            )
            normalized_payload = _validate_material_transaction_payload(
                kind,
                payload,
                category,
                supplier,
                existing,
                brand_options,
            )

            if action == "create" and existing_snapshot.exists:
                if _material_transaction_matches(existing, normalized_payload):
                    return {"id": transaction_id, "payload": existing, "duplicate": True}
                raise MaterialTransactionError("This transaction request has already been used.", 409)

            if kind == "consumptions":
                purchase_records = purchase_data
                consumption_snapshots_by_id = {
                    snapshot.id: snapshot
                    for snapshot in transaction.get(
                        self._collection("material_consumptions").where("categoryId", "==", category_id)
                    )
                }
                for material in category_materials:
                    for snapshot in transaction.get(
                        self._collection("material_consumptions").where("materialId", "==", material["id"])
                    ):
                        consumption_snapshots_by_id[snapshot.id] = snapshot
                consumption_records = [
                    {
                        "id": snapshot.id,
                        **(snapshot.to_dict() or {}),
                        "categoryId": (snapshot.to_dict() or {}).get("categoryId") or category_id,
                    }
                    for snapshot in consumption_snapshots_by_id.values()
                ]
                material_units = {
                    material["id"]: material for material in category_materials
                }
                _validate_material_stock(
                    category,
                    normalized_payload,
                    existing,
                    purchase_records,
                    consumption_records,
                    material_units,
                )

            now = firestore.SERVER_TIMESTAMP if firestore is not None else iso_now()
            if action == "create":
                saved = {**normalized_payload, "createdAt": now, "updatedAt": now}
                transaction.create(target_reference, saved)
            else:
                normalized_payload["updatedAt"] = now
                transaction.update(target_reference, normalized_payload)
                saved = {**(existing or {}), **normalized_payload}
            return {"id": transaction_id, "payload": saved, "duplicate": False}

        return mutate(firestore_transaction)

    def get_user_by_username(self, username):
        username = (username or "").strip().lower()
        docs = self._collection("users").where(
            filter=FieldFilter("username_lower", "==", username)
        ).limit(1).stream(retry=Retry(deadline=5), timeout=5)
        for doc in docs:
            user = doc.to_dict()
            user["id"] = doc.id
            user["status"] = normalize_user_status(user.get("status"), default_active=bool(user.get("is_active", True)))
            user["is_active"] = user["status"] == "active"
            return user
        return None

    def get_user(self, user_id):
        doc = self._collection("users").document(user_id).get()
        if not doc.exists:
            return None
        user = doc.to_dict()
        user["id"] = doc.id
        user["status"] = normalize_user_status(user.get("status"), default_active=bool(user.get("is_active", True)))
        user["is_active"] = user["status"] == "active"
        return user

    def list_users(self):
        items = []
        for doc in self._collection("users").stream():
            user = doc.to_dict()
            user["id"] = doc.id
            user["status"] = normalize_user_status(user.get("status"), default_active=bool(user.get("is_active", True)))
            user["is_active"] = user["status"] == "active"
            items.append(user)
        return items

    def save_user(self, user):
        user_id = user.get("id")
        if not user_id:
            raise ValueError("User id is required")
        payload = dict(user)
        payload["username_lower"] = str(payload.get("username") or "").strip().lower()
        payload["status"] = normalize_user_status(payload.get("status"), default_active=bool(payload.get("is_active", True)))
        payload["is_active"] = payload["status"] == "active"
        self._collection("users").document(user_id).set(payload, merge=True)
        return self.get_user(user_id)

    def delete_user(self, user_id):
        reference = self._collection("users").document(user_id)
        if not reference.get().exists:
            return False
        reference.delete()
        return True

    def update_user_profile(self, user_id, full_name, email, recovery_email):
        reference = self._collection("users").document(user_id)
        if not reference.get().exists:
            return None
        reference.update({
            "full_name": full_name,
            "email": email,
            "recovery_email": recovery_email,
        })
        return self.get_user(user_id)

    def consume_password_reset(self, user_id, token_hash, password_hash, now):
        transaction = self.db.transaction()

        @firestore.transactional
        def consume(transaction):
            reference = self._collection("users").document(user_id)
            snapshot = reference.get(transaction=transaction)
            if not snapshot.exists:
                return False
            user = snapshot.to_dict()
            if not password_reset_token_is_valid(user, token_hash, now):
                return False
            transaction.update(reference, {
                "password_hash": password_hash,
                "must_change_password": False,
                "password_reset_token_hash": None,
                "password_reset_expires_at": None,
                "active_sessions": [],
                "authorization_version": get_authorization_version(user) + 1,
                "last_password_change": iso_now(),
                "updated_at": iso_now(),
            })
            return True

        return consume(transaction)

    def save_custom_staff_type(self, staff_type):
        staff_type_id = staff_type.get("id")
        if not staff_type_id:
            raise ValueError("Staff type id is required")
        self._collection("custom_staff_types").document(staff_type_id).set(staff_type, merge=True)
        return dict(staff_type)

    def delete_custom_staff_type(self, staff_type_id):
        self._collection("custom_staff_types").document(staff_type_id).delete()

    def list_custom_staff_types(self):
        items = []
        for doc in self._collection("custom_staff_types").stream():
            item = doc.to_dict()
            item["id"] = doc.id
            items.append(item)
        return items

    def append_audit(self, entry):
        payload = {
            "actor_id": entry.get("actor_id"),
            "target_user_id": entry.get("target_user_id"),
            "action": entry.get("action"),
            "details": entry.get("details"),
            "timestamp": entry.get("timestamp") or iso_now(),
        }
        doc_ref = self._collection("audit_log").document(uuid.uuid4().hex)
        doc_ref.set(payload)
        return payload

    def consume_camera_auth_attempt(self, subject_fingerprint=None, include_global=True, now=None):
        now = now or time.time()
        bucket_specs = []
        if subject_fingerprint is not None:
            bucket_specs.append((f"subject:{subject_fingerprint}", CAMERA_AUTH_MAX_ATTEMPTS))
        if include_global:
            bucket_specs.append(("global", CAMERA_AUTH_GLOBAL_MAX_ATTEMPTS))
        references = [
            self._collection("camera_auth_rate_limits").document(hashlib.sha256(key.encode("utf-8")).hexdigest())
            for key, _limit in bucket_specs
        ]
        transaction = self.db.transaction()

        @firestore.transactional
        def consume(transaction):
            snapshots = [reference.get(transaction=transaction) for reference in references]
            updates = []
            for snapshot, (_key, limit) in zip(snapshots, bucket_specs):
                data = snapshot.to_dict() if snapshot.exists else {}
                window_start = float(data.get("window_start", now))
                count = int(data.get("count", 0))
                if now - window_start >= CAMERA_AUTH_WINDOW_SECONDS:
                    window_start, count = now, 0
                if count >= limit:
                    return False
                updates.append({
                    "window_start": window_start,
                    "count": count + 1,
                    "expire_at": datetime.fromtimestamp(window_start + CAMERA_AUTH_WINDOW_SECONDS * 2, timezone.utc),
                })
            for reference, update in zip(references, updates):
                transaction.set(reference, update)
            return True

        return consume(transaction)


def iso_now():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


class MaterialTransactionError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class MaterialMasterDataError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


MATERIAL_CATEGORY_FIELDS = frozenset({"name", "unit", "brands", "description"})
MATERIAL_SUPPLIER_FIELDS = frozenset({
    "name",
    "categoryIds",
    "categoryBrandAssignments",
    "contactPerson",
    "address",
    "city",
    "state",
    "pinCode",
    "primaryPhone",
    "secondaryPhone",
    "whatsappNumber",
    "email",
    "gstNumber",
    "notes",
})


def normalized_material_name(value):
    return " ".join(str(value or "").split()).casefold()


def _material_master_text(value, field, maximum, *, required=False):
    if not isinstance(value, str):
        raise MaterialMasterDataError(f"Enter a valid {field}.")
    result = value.strip()
    if (
        (required and not result)
        or len(result) > maximum
        or any(
            (ord(character) < 32 and character not in "\r\n\t")
            or ord(character) == 127
            for character in result
        )
    ):
        raise MaterialMasterDataError(f"Enter a valid {field}.")
    return result


def _validate_material_category_payload(payload):
    if not isinstance(payload, dict) or set(payload) != MATERIAL_CATEGORY_FIELDS:
        raise MaterialMasterDataError("Provide only supported material category fields.")
    name = _material_master_text(payload["name"], "category name", 120, required=True)
    unit = _material_master_text(payload["unit"], "category unit", 40, required=True)
    description = _material_master_text(payload["description"], "category description", 800)
    brands = payload["brands"]
    if not isinstance(brands, list) or len(brands) > 200:
        raise MaterialMasterDataError("Provide valid category brands / quarries.")
    normalized_brands = []
    brand_ids = set()
    brand_names = set()
    for brand in brands:
        if not isinstance(brand, dict) or set(brand) != {"id", "name"}:
            raise MaterialMasterDataError("Provide only brand / quarry IDs and names.")
        brand_id = brand["id"]
        if not _valid_material_id(brand_id) or len(brand_id) > 200:
            raise MaterialMasterDataError("Provide a valid brand / quarry ID.")
        brand_name = _material_master_text(
            brand["name"], "brand / quarry name", 120, required=True
        )
        normalized_name = normalized_material_name(brand_name)
        if brand_id in brand_ids or normalized_name in brand_names:
            raise MaterialMasterDataError(
                "Brand or quarry IDs and names must be unique within a category."
            )
        brand_ids.add(brand_id)
        brand_names.add(normalized_name)
        normalized_brands.append({"id": brand_id, "name": brand_name})
    return {
        "name": name,
        "unit": unit,
        "brands": normalized_brands,
        "description": description,
    }


def _validate_material_supplier_payload(payload):
    if not isinstance(payload, dict) or set(payload) != MATERIAL_SUPPLIER_FIELDS:
        raise MaterialMasterDataError("Provide only supported supplier fields.")
    name = _material_master_text(payload["name"], "supplier name", 160, required=True)
    primary_phone = _material_master_text(
        payload["primaryPhone"], "primary phone number", 40, required=True
    )
    category_ids = payload["categoryIds"]
    assignments = payload["categoryBrandAssignments"]
    if (
        not isinstance(category_ids, list)
        or not category_ids
        or len(category_ids) > 100
        or any(not _valid_material_id(item) for item in category_ids)
        or len(set(category_ids)) != len(category_ids)
        or not isinstance(assignments, list)
        or len(assignments) != len(category_ids)
    ):
        raise MaterialMasterDataError("Assign at least one valid material category.")
    normalized_assignments = []
    assignment_categories = set()
    for assignment in assignments:
        if not isinstance(assignment, dict) or set(assignment) != {"categoryId", "brandIds"}:
            raise MaterialMasterDataError("Provide valid supplier category and brand assignments.")
        category_id = assignment["categoryId"]
        brand_ids = assignment["brandIds"]
        if (
            not _valid_material_id(category_id)
            or category_id in assignment_categories
            or not isinstance(brand_ids, list)
            or not brand_ids
            or len(brand_ids) > 200
            or any(not _valid_material_id(item) for item in brand_ids)
            or len(set(brand_ids)) != len(brand_ids)
        ):
            raise MaterialMasterDataError("Select valid brands for each supplier category.")
        assignment_categories.add(category_id)
        normalized_assignments.append({
            "categoryId": category_id,
            "brandIds": list(brand_ids),
        })
    if assignment_categories != set(category_ids):
        raise MaterialMasterDataError("Supplier category assignments are inconsistent.")

    result = {
        "name": name,
        "categoryIds": list(category_ids),
        "categoryBrandAssignments": normalized_assignments,
        "primaryPhone": primary_phone,
    }
    limits = {
        "contactPerson": 120,
        "address": 500,
        "city": 100,
        "state": 100,
        "pinCode": 20,
        "secondaryPhone": 40,
        "whatsappNumber": 40,
        "email": 254,
        "gstNumber": 40,
        "notes": 1000,
    }
    for field, maximum in limits.items():
        result[field] = _material_master_text(payload[field], field, maximum)
    if result["email"] and not re.fullmatch(
        r"[^@\s]+@[^@\s]+\.[^@\s]+", result["email"]
    ):
        raise MaterialMasterDataError("Enter a valid supplier email address.")
    return result


def _material_category_create_matches(existing, payload):
    return all(existing.get(field) == value for field, value in payload.items())


def _material_supplier_create_matches(existing, payload):
    return all(existing.get(field) == value for field, value in payload.items())


def _material_category_has_transaction_history(
    category_id, materials, purchases, consumptions
):
    material_values = (
        [{"id": item_id, **item} for item_id, item in materials.items()]
        if isinstance(materials, dict)
        else materials
    )
    material_ids = {
        item.get("id")
        for item in material_values
        if isinstance(item, dict) and item.get("categoryId") == category_id
    }
    return any(
        item.get("categoryId") == category_id or item.get("materialId") in material_ids
        for item in [*purchases, *consumptions]
        if isinstance(item, dict)
    )


def _ensure_material_category_deletable(
    category_id, materials, suppliers, purchases, consumptions
):
    material_values = (
        [{"id": item_id, **item} for item_id, item in materials.items()]
        if isinstance(materials, dict)
        else materials
    )
    if any(
        isinstance(item, dict) and item.get("categoryId") == category_id
        for item in material_values
    ):
        raise MaterialMasterDataError(
            "This category is used by saved materials, supplier assignments, or transactions.",
            409,
        )
    for supplier in suppliers:
        if not isinstance(supplier, dict):
            continue
        assignments = supplier.get("categoryBrandAssignments")
        category_ids = (
            [item.get("categoryId") for item in assignments if isinstance(item, dict)]
            if isinstance(assignments, list)
            else supplier.get("categoryIds", [])
        )
        if isinstance(category_ids, (list, tuple, set)) and category_id in category_ids:
            raise MaterialMasterDataError(
                "This category is used by saved materials, supplier assignments, or transactions.",
                409,
            )
    if any(
        isinstance(item, dict) and item.get("categoryId") == category_id
        for item in [*purchases, *consumptions]
    ):
        raise MaterialMasterDataError(
            "This category is used by saved materials, supplier assignments, or transactions.",
            409,
        )


def _validate_material_supplier_relationships(
    payload, categories, materials, legacy_brands, purchases
):
    if isinstance(materials, dict):
        if all(isinstance(value, list) for value in materials.values()):
            material_values = [
                item
                for category_materials in materials.values()
                for item in category_materials
                if isinstance(item, dict)
            ]
        else:
            material_values = [
                {"id": item_id, **item}
                for item_id, item in materials.items()
                if isinstance(item, dict)
            ]
    else:
        material_values = materials
    brand_values = (
        [{"id": item_id, **item} for item_id, item in legacy_brands.items()]
        if isinstance(legacy_brands, dict)
        else legacy_brands
    )
    purchase_values = (
        [{"id": item_id, **item} for item_id, item in purchases.items()]
        if isinstance(purchases, dict)
        else purchases
    )
    for assignment in payload["categoryBrandAssignments"]:
        category_id = assignment["categoryId"]
        category = categories.get(category_id)
        if not isinstance(category, dict):
            raise MaterialMasterDataError("Select a valid material category.")
        category_with_id = {"id": category_id, **category}
        category_materials = [
            item for item in material_values
            if isinstance(item, dict) and item.get("categoryId") == category_id
        ]
        options = _material_brand_options(
            category_with_id,
            category_materials,
            brand_values,
            [
                item for item in purchase_values
                if isinstance(item, dict)
                and (
                    item.get("categoryId") == category_id
                    or item.get("materialId")
                    in {material.get("id") for material in category_materials}
                )
            ],
        )
        if any(brand_id not in options for brand_id in assignment["brandIds"]):
            raise MaterialMasterDataError(
                "Select at least one valid brand or quarry for each assigned category."
            )


def _material_transaction_collection(kind):
    collections = {
        "purchases": "material_purchases",
        "consumptions": "material_consumptions",
    }
    if kind not in collections:
        raise MaterialTransactionError("Invalid material transaction type.")
    return collections[kind]


def _valid_material_id(value):
    if not isinstance(value, str) or not value or value in {".", ".."} or "/" in value:
        return False
    try:
        if len(value.encode("utf-8")) > 1500:
            return False
    except UnicodeEncodeError:
        return False
    return not any(ord(character) < 32 or ord(character) == 127 for character in value)


def _material_number(value, field, *, allow_none=False):
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise MaterialTransactionError(f"Enter a valid {field}.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise MaterialTransactionError(f"Enter a valid {field}.") from None
    if not math.isfinite(number) or number < 0:
        raise MaterialTransactionError(f"Enter a non-negative {field}.")
    return number


def _material_text(value, field, maximum, *, allow_empty=True):
    if not isinstance(value, str):
        raise MaterialTransactionError(f"Enter a valid {field}.")
    result = value.strip()
    if (not allow_empty and not result) or len(result) > maximum:
        raise MaterialTransactionError(f"Enter a valid {field}.")
    return result


def _material_brand_options(category, materials, legacy_brands, purchases):
    if not isinstance(category, dict):
        return {}
    embedded = category.get("brands")
    if isinstance(embedded, list):
        return {
            str(item.get("id")): str(item.get("name") or "").strip()
            for item in embedded
            if isinstance(item, dict) and item.get("id") and item.get("name")
        }
    category_materials = [item for item in materials if item.get("categoryId") == category.get("id")]
    material_ids = {item.get("id") for item in category_materials}
    options = {}
    for brand in legacy_brands:
        if brand.get("materialId") in material_ids and brand.get("name"):
            options[f"legacy-brand-{brand['id']}"] = str(brand["name"]).strip()
    for material in category_materials:
        if material.get("brand"):
            options[f"legacy-material-{material['id']}"] = str(material["brand"]).strip()
    for purchase in purchases:
        if purchase.get("categoryId") == category.get("id") and purchase.get("brand"):
            options[str(purchase.get("brandId") or f"legacy-purchase-{purchase.get('id')}")] = str(purchase["brand"]).strip()
    return options


def _material_supplier_allows(supplier, category_id, brand_id, previous):
    if not supplier:
        return False
    if (
        previous
        and previous.get("supplierId") == supplier.get("id")
        and previous.get("categoryId") == category_id
    ):
        return True
    assignments = supplier.get("categoryBrandAssignments")
    if isinstance(assignments, list):
        return any(
            isinstance(assignment, dict)
            and assignment.get("categoryId") == category_id
            and isinstance(assignment.get("brandIds"), list)
            and brand_id in assignment["brandIds"]
            for assignment in assignments
        )
    return category_id in (supplier.get("categoryIds") or [])


def _material_supplier_provides_category(supplier, category_id, previous):
    if not supplier:
        return False
    if (
        previous
        and previous.get("supplierId") == supplier.get("id")
        and previous.get("categoryId") == category_id
    ):
        return True
    assignments = supplier.get("categoryBrandAssignments")
    if isinstance(assignments, list):
        return any(
            isinstance(assignment, dict) and assignment.get("categoryId") == category_id
            for assignment in assignments
        )
    return category_id in (supplier.get("categoryIds") or [])


def _validate_material_transaction_payload(
    kind,
    payload,
    category,
    supplier,
    previous,
    brand_options,
):
    allowed_fields = {
        "purchases": {
            "date", "categoryId", "brandId", "brand", "supplierId", "supplierName",
            "quantity", "unit", "rate", "totalAmount", "billLink",
        },
        "consumptions": {
            "date", "categoryId", "quantity", "unit", "workLocation", "remarks",
        },
    }
    if kind not in allowed_fields or not isinstance(payload, dict):
        raise MaterialTransactionError("Provide valid material transaction details.")
    if set(payload) - allowed_fields[kind]:
        raise MaterialTransactionError("Material transaction contains unsupported fields.")
    category_id = payload.get("categoryId")
    if not _valid_material_id(category_id):
        raise MaterialTransactionError("Select a valid material category.")
    if not isinstance(category, dict) or category.get("id") != category_id:
        raise MaterialTransactionError("Material category not found.", 404)
    category_name = _material_text(category.get("name"), "material category", 120, allow_empty=False)
    date_value = payload.get("date")
    if not isinstance(date_value, str):
        raise MaterialTransactionError("Enter a valid transaction date.")
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value):
            raise ValueError
        datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError:
        raise MaterialTransactionError("Enter a valid transaction date.") from None

    if kind == "purchases":
        cement = category_name.strip().casefold() == "cement"
        total_amount = _material_number(payload.get("totalAmount"), "purchase amount")
        supplier_id = payload.get("supplierId") or ""
        if supplier_id and not _valid_material_id(supplier_id):
            raise MaterialTransactionError("Select a valid supplier.")
        if supplier_id and (not supplier or supplier.get("id") != supplier_id):
            raise MaterialTransactionError("Supplier not found.", 404)
        if cement:
            quantity = _material_number(payload.get("quantity"), "quantity")
            rate = _material_number(payload.get("rate"), "rate")
            if quantity <= 0:
                raise MaterialTransactionError("Enter a positive purchase quantity.")
            unit = _material_text(payload.get("unit"), "purchase unit", 40, allow_empty=False)
            configured_unit = str(category.get("unit") or "").strip()
            if configured_unit and configured_unit.casefold() != unit.casefold():
                raise MaterialTransactionError("Purchase unit must match the material category unit.")
            brand_id = _material_text(payload.get("brandId"), "brand / quarry", 1500, allow_empty=False)
            brand = brand_options.get(brand_id)
            if not brand and previous and (
                previous.get("categoryId") == category_id
                and previous.get("brandId") == brand_id
                and previous.get("brand")
            ):
                brand = str(previous["brand"]).strip()
            if not brand:
                raise MaterialTransactionError("Select a valid Cement brand / quarry.")
            if abs(total_amount - quantity * rate) > max(1e-9, abs(total_amount) * 1e-12):
                raise MaterialTransactionError("Purchase total must equal quantity multiplied by rate.")
            if supplier_id and not _material_supplier_allows(supplier, category_id, brand_id, previous):
                raise MaterialTransactionError("Select a supplier assigned to this category and brand.")
        else:
            if supplier_id and not _material_supplier_provides_category(supplier, category_id, previous):
                raise MaterialTransactionError("Select a supplier assigned to this category.")
            quantity = (previous or {}).get("quantity")
            rate = (previous or {}).get("rate")
            unit = (previous or {}).get("unit") or ""
            brand_id = ""
            brand = ""
        bill_link = _material_text(payload.get("billLink", ""), "bill link", 2000)
        if bill_link:
            try:
                parsed = urlsplit(bill_link)
            except ValueError:
                raise MaterialTransactionError("Bill Photo / Link must be an http or https URL.") from None
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise MaterialTransactionError("Bill Photo / Link must be an http or https URL.")
        return {
            "date": date_value,
            "categoryId": category_id,
            "categoryName": category_name,
            "brandId": brand_id,
            "brand": brand,
            "supplierId": supplier_id,
            "supplierName": str((supplier or {}).get("name") or "").strip() if supplier_id else "",
            "quantity": quantity,
            "unit": unit,
            "rate": rate,
            "totalAmount": total_amount,
            "billLink": bill_link,
        }

    if category_name.strip().casefold() != "cement":
        raise MaterialTransactionError("Consumption can only be recorded for Cement.")
    quantity = _material_number(payload.get("quantity"), "consumption quantity")
    if quantity <= 0:
        raise MaterialTransactionError("Enter a positive consumption quantity.")
    unit = _material_text(payload.get("unit"), "consumption unit", 40, allow_empty=False)
    return {
        "date": date_value,
        "categoryId": category_id,
        "categoryName": category_name,
        "quantity": quantity,
        "unit": unit,
        "workLocation": _material_text(payload.get("workLocation"), "work / location", 240, allow_empty=False),
        "remarks": _material_text(payload.get("remarks", ""), "remarks", 1000),
    }


def _material_stock_records(records, category_id, materials):
    material_values = (
        [{"id": material_id, **material} for material_id, material in materials.items()]
        if isinstance(materials, dict)
        else materials
    )
    category_material_ids = {
        material.get("id")
        for material in material_values
        if isinstance(material, dict) and material.get("categoryId") == category_id
    }
    return [
        record for record in records
        if isinstance(record, dict) and (
            record.get("categoryId") == category_id
            or record.get("materialId") in category_material_ids
        )
    ]


def _stored_material_quantity(record):
    value = record.get("quantity")
    if value in (None, ""):
        return 0.0
    try:
        quantity = float(value)
    except (TypeError, ValueError, OverflowError):
        raise MaterialTransactionError("Saved stock data is invalid. Contact an administrator.", 409) from None
    if not math.isfinite(quantity) or quantity < 0:
        raise MaterialTransactionError("Saved stock data is invalid. Contact an administrator.", 409)
    return quantity


def _validate_material_stock(category, payload, previous, purchases, consumptions, materials):
    category_id = category.get("id")
    category_purchases = _material_stock_records(purchases, category_id, materials)
    category_consumptions = _material_stock_records(consumptions, category_id, materials)
    material_values = (
        [{"id": material_id, **material} for material_id, material in materials.items()]
        if isinstance(materials, dict)
        else materials
    )
    category_material_ids = {
        material.get("id")
        for material in material_values
        if isinstance(material, dict) and material.get("categoryId") == category_id
    }
    configured_unit = str(category.get("unit") or "").strip()
    if configured_unit:
        unit = configured_unit
    else:
        units = {
            str(record.get("unit") or "").strip().casefold()
            for record in [*category_purchases, *category_consumptions]
            if str(record.get("unit") or "").strip()
        }
        material_values = (
            [{"id": material_id, **material} for material_id, material in materials.items()]
            if isinstance(materials, dict)
            else materials
        )
        units.update(
            str(material.get("unit") or "").strip().casefold()
            for material in material_values
            if isinstance(material, dict)
            and material.get("categoryId") == category_id
            and str(material.get("unit") or "").strip()
        )
        if len(units) != 1:
            raise MaterialTransactionError(
                "Consumption is blocked because this category has no consistent unit."
            )
        unit = next(iter(units))
    normalized_unit = unit.casefold()
    records = [*category_purchases, *category_consumptions]
    if any(str(record.get("unit") or "").strip().casefold() != normalized_unit for record in records):
        raise MaterialTransactionError(
            "Consumption is blocked because this category has no consistent unit."
        )
    if payload["unit"].casefold() != normalized_unit:
        raise MaterialTransactionError("Consumption unit must match the available stock unit.")
    purchased = sum(_stored_material_quantity(record) for record in category_purchases)
    consumed = sum(_stored_material_quantity(record) for record in category_consumptions)
    if previous and (
        previous.get("categoryId") == category_id
        or previous.get("materialId") in category_material_ids
    ):
        consumed -= _stored_material_quantity(previous)
    if payload["quantity"] > purchased - consumed + 1e-9:
        raise MaterialTransactionError("Insufficient stock for this consumption.", 409)


def _material_transaction_matches(existing, payload):
    return all(existing.get(key) == value for key, value in payload.items())


def get_user_store():
    configured_store = app.config.get("FIRESTORE_STORE")
    if configured_store is not None:
        return configured_store

    store_mode = str(app.config.get("FIRESTORE_MODE") or "").lower()
    if store_mode == "memory":
        if not hasattr(app, "_memory_store"):
            app._memory_store = InMemoryFirestoreStore()
        return app._memory_store

    if firebase_admin is not None:
        try:
            active_config = get_active_firebase_config()
            active_key = (active_config["project_id"], active_config.get("service_account_path"))
            if not hasattr(app, "_firebase_store") or getattr(app, "_firebase_store_key", None) != active_key:
                app._firebase_store = FirebaseFirestoreStore(
                    project_id=active_config["project_id"],
                    service_account_path=active_config.get("service_account_path"),
                )
                app._firebase_store_key = active_key
            return app._firebase_store
        except Exception as exc:
            if store_mode == "firestore":
                raise RuntimeError(
                    "Firestore initialization failed. Check FIREBASE_PROJECT_ID and FIREBASE_SERVICE_ACCOUNT settings."
                ) from exc
            if os.environ.get("ALLOW_IN_MEMORY_FIRESTORE", "").lower() in {"1", "true", "yes", "on"}:
                if not hasattr(app, "_memory_store"):
                    app._memory_store = InMemoryFirestoreStore()
                return app._memory_store
            raise RuntimeError(
                "Firestore is not configured. Set FIREBASE_PROJECT_ID and valid credentials or set FIRESTORE_MODE=memory explicitly for local testing."
            ) from exc

    if store_mode == "memory" or os.environ.get("ALLOW_IN_MEMORY_FIRESTORE", "").lower() in {"1", "true", "yes", "on"}:
        if not hasattr(app, "_memory_store"):
            app._memory_store = InMemoryFirestoreStore()
        return app._memory_store

    raise RuntimeError(
        "Firebase Admin SDK is not installed or Firestore is not configured. Install firebase-admin and set Firebase credentials before starting the app."
    )


def get_current_user():
    user_id = session.get("user_id")
    current_session_id = session.get("session_id")
    if not isinstance(user_id, str) or not user_id or not isinstance(current_session_id, str) or not current_session_id:
        session.clear()
        return None
    try:
        user = get_user_store().get_user(user_id)
    except RuntimeError:
        session.clear()
        return None
    if (
        not isinstance(user, dict)
        or str(user.get("id") or "") != user_id
        or not str(user.get("role") or "").strip()
        or not isinstance(user.get("active_sessions"), (list, tuple, set))
    ):
        session.clear()
        return None
    if not _user_account_is_active(user):
        invalidate_user_sessions(
            user_id,
            user=user,
            increment_authorization_version=bool(user["active_sessions"]),
        )
        session.clear()
        return None
    if current_session_id not in set(user["active_sessions"]):
        session.clear()
        return None
    return user


def _firebase_custom_token_response(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _firebase_custom_token_origin_is_valid():
    origin = request.headers.get("Origin", "")
    if not origin:
        return False
    try:
        parsed_origin = urlsplit(origin)
    except ValueError:
        return False
    if (
        parsed_origin.scheme not in {"http", "https"}
        or not parsed_origin.netloc
        or parsed_origin.username
        or parsed_origin.password
        or parsed_origin.path not in {"", "/"}
        or parsed_origin.query
        or parsed_origin.fragment
        or parsed_origin.netloc.lower() != request.host.lower()
    ):
        return False
    fetch_site = request.headers.get("Sec-Fetch-Site")
    return not fetch_site or fetch_site.lower() == "same-origin"


def _custom_role_definition_for_user(user, store):
    builtin_role_names = {"super_admin", "office_staff", "supervisor"}
    if str(user.get("role") or "").strip().casefold() in builtin_role_names:
        return None

    assigned_names = {
        " ".join(str(user.get(field) or "").split()).casefold()
        for field in ("role", "custom_staff_type")
    }
    assigned_names.discard("")
    for definition in store.list_custom_staff_types():
        if not isinstance(definition, dict):
            continue
        name = " ".join(str(definition.get("name") or "").split()).casefold()
        if name and name in assigned_names:
            return definition
    return None


@app.route("/api/firebase-custom-token", methods=["GET", "POST"])
def firebase_custom_token_api():
    user = get_current_user()
    if not user:
        return _firebase_custom_token_response({"error": "Authentication required."}, 401)
    if user.get("must_change_password"):
        return _firebase_custom_token_response({"error": "Change your password before continuing."}, 403)

    if request.method == "GET":
        csrf_token = session.get("firebase_custom_token_csrf")
        if not csrf_token:
            csrf_token = secrets.token_urlsafe(32)
            session["firebase_custom_token_csrf"] = csrf_token
        return _firebase_custom_token_response({"csrf_token": csrf_token})

    expected_csrf = session.get("firebase_custom_token_csrf", "")
    supplied_csrf = request.headers.get("X-CSRF-Token", "")
    if (
        not _firebase_custom_token_origin_is_valid()
        or not expected_csrf
        or not supplied_csrf
        or not hmac.compare_digest(expected_csrf, supplied_csrf)
    ):
        return _firebase_custom_token_response({"error": "Request validation failed."}, 400)

    user_id = user.get("id")
    current_session_id = session.get("session_id")
    try:
        user_id_bytes = user_id.encode("utf-8") if isinstance(user_id, str) else b""
    except UnicodeEncodeError:
        user_id_bytes = b""
    if (
        not isinstance(user_id, str)
        or not user_id
        or not user_id_bytes
        or len(user_id_bytes) > 128
        or any(ord(character) < 32 or ord(character) == 127 for character in user_id)
        or not isinstance(current_session_id, str)
        or not current_session_id
    ):
        return _firebase_custom_token_response({"error": "The authenticated account is not valid for Firebase."}, 403)

    try:
        store = get_user_store()
        custom_role_definition = _custom_role_definition_for_user(user, store)
        permissions = sorted(compile_effective_permissions(user, custom_role_definition))
        claims = {
            "role": str(user["role"]),
            "perms": permissions,
            "sid": current_session_id,
            "ver": get_authorization_version(user),
        }
        if len(json.dumps(claims, separators=(",", ":"), ensure_ascii=True).encode("utf-8")) > 1000:
            app.logger.error("Firebase custom-token claims exceed the supported size limit.")
            return _firebase_custom_token_response({"error": "Firebase authorization data is not configured correctly."}, 500)

        firebase_app = getattr(store, "firebase_app", None)
        if firebase_auth is None or firebase_app is None:
            raise RuntimeError("Firebase Admin Auth is not initialized.")
        token = firebase_auth.create_custom_token(user_id, claims, app=firebase_app)
        if isinstance(token, bytes):
            token = token.decode("utf-8")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Firebase Admin Auth returned an invalid token.")
    except Exception as error:
        app.logger.error("Firebase custom-token issuance failed (%s).", type(error).__name__)
        return _firebase_custom_token_response({"error": "Firebase authentication is temporarily unavailable."}, 503)

    return _firebase_custom_token_response({"token": token})


def add_audit_log(actor_id, action, details, target_user_id=None):
    store = get_user_store()
    entry = {
        "id": uuid.uuid4().hex,
        "actor_id": actor_id,
        "target_user_id": target_user_id,
        "action": action,
        "details": details,
        "timestamp": iso_now(),
    }
    store.append_audit(entry)


def set_session_for_user(user):
    if not isinstance(user, dict) or not _user_account_is_active(user):
        raise ValueError("An active user account is required to create a session.")
    if not user.get("id") or not str(user.get("role") or "").strip():
        raise ValueError("A complete user account is required to create a session.")

    session.clear()
    session.permanent = True
    current_session_id = uuid.uuid4().hex
    session["user_id"] = user["id"]
    session["role"] = user["role"]
    session["session_id"] = current_session_id
    active_sessions = set(user.get("active_sessions", []))
    active_sessions.add(current_session_id)
    user["active_sessions"] = sorted(active_sessions)
    user["authorization_version"] = get_authorization_version(user)
    user["last_login_at"] = iso_now()
    user["updated_at"] = iso_now()
    get_user_store().save_user(user)


def get_authorization_version(user):
    version = user.get("authorization_version") if isinstance(user, dict) else None
    if isinstance(version, bool):
        return 0
    try:
        parsed_version = int(version)
    except (TypeError, ValueError):
        return 0
    return parsed_version if parsed_version >= 0 else 0


def _user_account_is_active(user):
    if "is_active" in user and user.get("is_active") is not True:
        return False
    if "status" in user:
        status = str(user.get("status") or "").strip().lower()
        return status in {"active", "enabled", "true", "1", "yes", "open"}
    if "is_active" in user:
        return user.get("is_active") is True
    return True


def invalidate_user_sessions(user_id, user=None, increment_authorization_version=False):
    """Invalidate durable account sessions and optionally advance its auth version."""
    store = get_user_store()
    target = user if isinstance(user, dict) and str(user.get("id") or "") == str(user_id) else store.get_user(user_id)
    if not isinstance(target, dict):
        return None

    target["active_sessions"] = []
    if increment_authorization_version:
        target["authorization_version"] = get_authorization_version(target) + 1
    else:
        target["authorization_version"] = get_authorization_version(target)
    target["updated_at"] = iso_now()
    store.save_user(target)
    return target


def invalidate_other_sessions(user_id, keep_session_id=None):
    user = get_user_store().get_user(user_id)
    if not user:
        return
    sessions = list(user.get("active_sessions", []))
    if keep_session_id:
        user["active_sessions"] = [keep_session_id] if keep_session_id in sessions else []
    else:
        user["active_sessions"] = []
        user["authorization_version"] = get_authorization_version(user)
        user["updated_at"] = iso_now()
        get_user_store().save_user(user)


def validate_password_policy(password):
    if len(password) < 8:
        return "Password must be at least 8 characters long."
    if not re.search(r"[A-Z]", password):
        return "Password must include at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return "Password must include at least one lowercase letter."
    if not re.search(r"\d", password):
        return "Password must include at least one number."
    if not re.search(r"[^A-Za-z0-9]", password):
        return "Password must include at least one special character."
    return None


def is_valid_email(value):
    if value is None:
        return False
    value = str(value).strip()
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value))


def normalize_username(value):
    return (value or "").strip()


def normalize_staff_type_name(value):
    return " ".join((value or "").split()).strip()


def staff_type_names_match(left, right):
    return normalize_staff_type_name(left).lower() == normalize_staff_type_name(right).lower()


def is_protected_staff_type_name(value):
    normalized = normalize_staff_type_name(value).lower()
    return normalized in {role.lower() for role in BUILT_IN_STAFF_TYPES}


def list_available_roles():
    roles = ["super_admin", "office_staff", "supervisor"]
    for item in get_user_store().list_custom_staff_types():
        role_name = normalize_staff_type_name(item.get("name"))
        if role_name and role_name not in roles:
            roles.append(role_name)
    return roles


def is_super_admin(user):
    return bool(user) and str(user.get("role", "")).lower() == "super_admin"


def get_effective_permissions(user):
    if not user:
        return set()
    permissions = {
        permission.strip()
        for permission in (user.get("permissions") or [])
        if isinstance(permission, str) and permission.strip()
    }
    role = str(user.get("role", "")).strip().lower()
    permissions.update(ROLE_PERMISSIONS.get(role, []))
    return permissions


def has_permission(user, permission):
    return bool(user) and permission in get_effective_permissions(user)


def _camera_auth_secret(name):
    if name in app.config:
        return str(app.config.get(name) or "")
    return str(os.environ.get(name) or "")


def _camera_auth_error(status_code):
    return jsonify(authenticated=False, error="Camera authentication failed."), status_code


def _camera_auth_serializer():
    secret = _camera_auth_secret("CAMERA_AUTH_ASSERTION_SECRET")
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("Camera assertion signing is not configured.")
    return URLSafeTimedSerializer(
        secret,
        salt=CAMERA_AUTH_ASSERTION_SALT,
        signer_kwargs={"digest_method": hashlib.sha256},
    )


def verify_camera_auth_assertion(assertion, max_age=CAMERA_AUTH_ASSERTION_TTL_SECONDS):
    claims = _camera_auth_serializer().loads(assertion, max_age=max_age)
    if not isinstance(claims, dict) or claims.get("iss") != "mannat-moon" or claims.get("aud") != "camera":
        raise BadSignature("Invalid camera authentication assertion.")
    if int(claims.get("exp", 0)) <= int(time.time()):
        raise SignatureExpired("Camera authentication assertion has expired.")
    return claims


def require_login(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = get_current_user()
        if not user:
            return redirect(url_for("login_page"))
        return view(*args, **kwargs)

    return wrapped


def require_role(*allowed_roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = get_current_user()
            if not user:
                return redirect(url_for("login_page"))
            if allowed_roles and str(user.get("role", "")).lower() not in {role.lower() for role in allowed_roles}:
                flash("You do not have permission to access that page.", "error")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def ensure_initial_super_admin():
    """Create the first built-in super admin only with an explicit bootstrap secret."""
    store = get_user_store()
    if not store:
        return None

    try:
        existing_users = store.list_users()
    except Exception:
        return None

    if existing_users:
        return None

    bootstrap_password = os.environ.get("INITIAL_SUPER_ADMIN_PASSWORD", "")
    password_error = validate_password_policy(bootstrap_password)
    if password_error:
        app.logger.warning(
            "Initial Super Admin was not created; configure a policy-compliant "
            "INITIAL_SUPER_ADMIN_PASSWORD before bootstrapping an empty user store."
        )
        return None

    admin_user = {
        "id": uuid.uuid4().hex,
        "username": "Rahim",
        "username_lower": "rahim",
        "full_name": "Rahim",
        "email": "",
        "recovery_email": "",
        "role": "super_admin",
        "custom_staff_type": "",
        "password_hash": generate_password_hash(bootstrap_password),
        "must_change_password": True,
        "status": "active",
        "is_active": True,
        "active_sessions": [],
        "created_at": iso_now(),
        "updated_at": iso_now(),
        "last_login_at": None,
        "last_password_change": None,
        "recovery_code_hash": None,
        "recovery_code_used": False,
        "password_reset_token_hash": None,
        "password_reset_expires_at": None,
        "permissions": list(ROLE_PERMISSIONS.get("super_admin", [])),
        "created_by": None,
        "avatarId": DEFAULT_PROFILE_AVATAR_ID,
    }
    store.save_user(admin_user)
    return admin_user


def send_email(to_address, subject, body, html_body=None):
    if not to_address:
        raise ValueError("Recipient email is required.")

    recipients = [to_address] if isinstance(to_address, str) else list(to_address)
    message = Message(subject=subject, sender=app.config.get("MAIL_DEFAULT_SENDER"), recipients=recipients)
    message.body = body
    if html_body:
        message.html = html_body
    mail.send(message)
    return True


def sanitize_mail_error(error):
    message = str(error)
    for secret in (
        app.config.get("MAIL_PASSWORD"),
        app.config.get("MAIL_USERNAME"),
        app.config.get("MAIL_DEFAULT_SENDER"),
    ):
        if secret:
            message = message.replace(str(secret), "[REDACTED]")
    message = re.sub(
        r"(?i)\b(password|passwd|app[_ -]?password|token|access[_ -]?token|refresh[_ -]?token|authorization|secret|credential|api[_ -]?key|client[_ -]?secret)\b\s*[:=]\s*(?:'[^']*'|\"[^\"]*\"|[^\s,;]+)",
        r"\1=[REDACTED]",
        message,
    )
    message = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", message)
    message = re.sub(r"(?i)(://[^:/\s]+:)[^@/\s]+@", r"\1[REDACTED]@", message)
    return message[:1000]


# =========================================================
# DASHBOARD / AUTH ROUTES
# =========================================================

@app.route("/health", methods=["GET", "HEAD"])
def health_check():
    response = jsonify(status="ok")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/camera/authenticate")
def camera_authenticate():
    api_secret = _camera_auth_secret("CAMERA_AUTH_API_SECRET")
    assertion_secret = _camera_auth_secret("CAMERA_AUTH_ASSERTION_SECRET")
    if len(api_secret.encode("utf-8")) < 32 or len(assertion_secret.encode("utf-8")) < 32:
        app.logger.error("Camera authentication API secrets are not configured with sufficient entropy.")
        return _camera_auth_error(503)

    expected_authorization = f"Bearer {api_secret}"
    supplied_authorization = request.headers.get("Authorization", "")
    if not hmac.compare_digest(supplied_authorization, expected_authorization):
        return _camera_auth_error(401)

    if request.mimetype != "application/json" or request.content_length is None or request.content_length > 4096:
        return _camera_auth_error(400)
    raw_body = request.get_data(cache=True)
    try:
        payload = json.loads(raw_body)
    except (TypeError, ValueError):
        return _camera_auth_error(400)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"username", "password"}
        or not isinstance(payload.get("username"), str)
        or not isinstance(payload.get("password"), str)
        or not payload["username"].strip()
        or not payload["password"]
        or len(payload["username"]) > 256
        or len(payload["password"]) > 1024
    ):
        return _camera_auth_error(400)

    username = normalize_username(payload["username"])
    try:
        store = get_user_store()
        if not store.consume_camera_auth_attempt():
            return _camera_auth_error(429)
        user = store.get_user_by_username(username)
        subject_id = str(user.get("id")) if user else "__unknown_subject__"
        subject_fingerprint = hmac.new(
            api_secret.encode("utf-8"), subject_id.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not store.consume_camera_auth_attempt(subject_fingerprint, include_global=False):
            return _camera_auth_error(429)
    except Exception:
        app.logger.exception("Camera authentication could not access its account or rate-limit store.")
        return _camera_auth_error(503)

    password_hash = user.get("password_hash", "") if user else CAMERA_AUTH_DUMMY_PASSWORD_HASH
    try:
        password_matches = check_password_hash(password_hash, payload["password"])
    except (TypeError, ValueError):
        password_matches = False
    if not user or not password_matches:
        add_audit_log(
            None,
            "camera_auth_failed",
            f"Camera authentication failed; subject_fingerprint={subject_fingerprint}",
        )
        return _camera_auth_error(401)

    if normalize_user_status(user.get("status"), default_active=bool(user.get("is_active", True))) != "active":
        add_audit_log(user.get("id"), "camera_auth_denied", "Camera authentication denied; account inactive", user.get("id"))
        return _camera_auth_error(401)

    if user.get("must_change_password"):
        add_audit_log(user.get("id"), "camera_auth_denied", "Camera authentication denied; password change required", user.get("id"))
        return _camera_auth_error(401)

    if not has_permission(user, "camera_upload"):
        add_audit_log(user.get("id"), "camera_auth_denied", "Camera authentication denied; camera_upload permission missing", user.get("id"))
        return _camera_auth_error(401)

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=CAMERA_AUTH_ASSERTION_TTL_SECONDS)
    claims = {
        "iss": "mannat-moon",
        "aud": "camera",
        "sub": str(user.get("id") or ""),
        "user_id": str(user.get("id") or ""),
        "username": str(user.get("username") or ""),
        "full_name": str(user.get("full_name") or ""),
        "role": str(user.get("role") or ""),
        "permissions": ["camera_upload"],
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    assertion = _camera_auth_serializer().dumps(claims)
    add_audit_log(user.get("id"), "camera_auth_success", "Camera authentication succeeded", user.get("id"))
    response = jsonify(
        authenticated=True,
        user_id=claims["user_id"],
        username=claims["username"],
        full_name=claims["full_name"],
        role=claims["role"],
        permissions=claims["permissions"],
        expires_at=expires_at.isoformat(),
        assertion=assertion,
    )
    response.headers["Cache-Control"] = "no-store"
    return response, 200


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "favicon.svg",
        mimetype="image/svg+xml",
        max_age=86400,
    )


@app.after_request
def inject_office_connection_monitor(response):
    if response.status_code >= 300 or response.mimetype != "text/html":
        return response

    page = response.get_data(as_text=True)
    if "office-connection-monitor.js" in page:
        return response

    stylesheet = f'<link rel="stylesheet" href="{url_for("static", filename="office_connection.css")}">'
    script = f'<script defer src="{url_for("static", filename="office_connection.js")}"></script>'
    if "</head>" in page:
        page = page.replace("</head>", f"    {stylesheet}\n</head>", 1)
    if "</body>" in page:
        page = page.replace("</body>", f"    {script}\n</body>", 1)
    response.set_data(page)
    return response


@app.route("/")
@require_login
def dashboard():
    user = get_current_user()
    return render_template("dashboard.html", current_user=user)


@app.route("/profile-settings")
@require_login
def profile_settings():
    user = get_current_user()
    if not user:
        return redirect(url_for("login_page"))

    csrf_token = session.get("profile_avatar_csrf")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        session["profile_avatar_csrf"] = csrf_token

    role_labels = {
        "super_admin": "Super Admin",
        "office_staff": "Office Staff",
        "supervisor": "Supervisor",
    }
    stored_avatar_id = user.get("avatarId")
    safe_profile = {
        "full_name": user.get("full_name") or user.get("username") or "User",
        "username": user.get("username") or "",
        "email": user.get("email") or "",
        "recovery_email": user.get("recovery_email") or "",
        "role": role_labels.get(
            str(user.get("role") or "").lower(),
            user.get("custom_staff_type") or str(user.get("role") or "User").replace("_", " ").title(),
        ),
        "status": normalize_user_status(
            user.get("status"),
            default_active=bool(user.get("is_active", True)),
        ),
        "created_at": user.get("created_at") or "",
        "last_login_at": user.get("last_login_at") or "",
        "avatar_id": stored_avatar_id
        if isinstance(stored_avatar_id, str) and stored_avatar_id in PROFILE_AVATAR_IDS
        else DEFAULT_PROFILE_AVATAR_ID,
    }
    return render_template("profile_settings.html", profile=safe_profile, csrf_token=csrf_token, current_user=get_current_user())


@app.route("/api/profile", methods=["PATCH"])
def update_profile():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Sign in to update your profile."}), 401

    expected_token = session.get("profile_avatar_csrf", "")
    supplied_token = request.headers.get("X-CSRF-Token", "")
    if not expected_token or not supplied_token or not hmac.compare_digest(expected_token, supplied_token):
        return jsonify({"error": "Your session expired. Reload the page and try again."}), 403

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"full_name", "email", "recovery_email"}:
        return jsonify({"error": "Provide only your full name, account email, and recovery email."}), 400
    if not all(isinstance(payload[field], str) for field in ("full_name", "email", "recovery_email")):
        return jsonify({"error": "Full name and email addresses must be text."}), 400

    full_name = " ".join(payload["full_name"].split())
    email = payload["email"].strip()
    recovery_email = payload["recovery_email"].strip()
    if not full_name:
        return jsonify({"error": "Full name cannot be blank."}), 400
    if email and not is_valid_email(email):
        return jsonify({"error": "Enter a valid account email address."}), 400
    if recovery_email and not is_valid_email(recovery_email):
        return jsonify({"error": "Enter a valid recovery email address."}), 400

    try:
        store = get_user_store()
        updated_user = store.update_user_profile(user["id"], full_name, email, recovery_email)
        if not updated_user:
            return jsonify({"error": "Your profile could not be found."}), 404
    except RuntimeError:
        app.logger.exception("Unable to access the user profile while saving profile information")
        return jsonify({"error": "Profile updates are temporarily unavailable."}), 503
    except Exception:
        app.logger.exception("Unable to save the authenticated user's profile information")
        return jsonify({"error": "Unable to save your profile. Please try again."}), 500

    return jsonify({
        "full_name": full_name,
        "email": email,
        "recovery_email": recovery_email,
    })


@app.route("/api/profile/avatar", methods=["POST"])
def save_profile_avatar():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Sign in to update your profile avatar."}), 401

    payload = request.get_json(silent=True)
    avatar_id = payload.get("avatarId") if isinstance(payload, dict) else None
    expected_token = session.get("profile_avatar_csrf", "")
    supplied_token = request.headers.get("X-CSRF-Token", "")
    if not expected_token or not supplied_token or not hmac.compare_digest(expected_token, supplied_token):
        return jsonify({"error": "Your session expired. Reload the page and try again."}), 403
    if not isinstance(avatar_id, str) or avatar_id not in PROFILE_AVATAR_IDS:
        return jsonify({"error": "Choose an avatar from the available library."}), 400

    try:
        store = get_user_store()
        current_user = store.get_user(user["id"])
        if not current_user:
            return jsonify({"error": "Your profile could not be found."}), 404
        current_user["avatarId"] = avatar_id
        store.save_user(current_user)
    except RuntimeError:
        app.logger.exception("Unable to access the user profile while saving an avatar")
        return jsonify({"error": "Profile updates are temporarily unavailable."}), 503
    except Exception:
        app.logger.exception("Unable to save the authenticated user's profile avatar")
        return jsonify({"error": "Unable to save your avatar. Please try again."}), 500

    return jsonify({"avatarId": avatar_id})


@app.route("/login-assets/<path:filename>")
def login_asset(filename):
    return send_from_directory(os.path.join(app.root_path, "templates", "images"), filename)


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        app.logger.info("Login POST received")
        username = normalize_username(request.form.get("username"))
        password = request.form.get("password", "")
        lookup_started = time.monotonic()
        try:
            store = get_user_store()
            user = store.get_user_by_username(username)
        except DeadlineExceeded:
            app.logger.warning(
                "Firestore user lookup timed out during sign-in after %.3f seconds",
                time.monotonic() - lookup_started,
            )
            flash("Sign-in is temporarily unavailable. Please try again shortly.", "error")
            return render_template("login.html"), 503
        except GoogleAPICallError as error:
            app.logger.warning(
                "Firestore user lookup failed during sign-in (%s) after %.3f seconds",
                type(error).__name__,
                time.monotonic() - lookup_started,
            )
            flash("Sign-in is temporarily unavailable. Please try again shortly.", "error")
            return render_template("login.html"), 503
        except RuntimeError as error:
            app.logger.warning(
                "User store unavailable during sign-in (%s) after %.3f seconds",
                type(error).__name__,
                time.monotonic() - lookup_started,
            )
            flash("Sign-in is temporarily unavailable. Please contact your administrator.", "error")
            return render_template("login.html"), 503
        app.logger.info(
            "Login account lookup completed in %.3f seconds",
            time.monotonic() - lookup_started,
        )

        if (
            not isinstance(user, dict)
            or not user.get("id")
            or not str(user.get("role") or "").strip()
            or not isinstance(user.get("active_sessions"), (list, tuple, set))
            or not _user_account_is_active(user)
        ):
            add_audit_log(None, "login_failed", f"Failed login attempt for username {username}", None)
            flash("Invalid username or password.", "error")
            return redirect(url_for("login_page"))

        if not check_password_hash(user.get("password_hash", ""), password):
            add_audit_log(user.get("id"), "login_failed", "Failed login attempt using invalid password", user.get("id"))
            flash("Invalid username or password.", "error")
            return redirect(url_for("login_page"))

        if user.get("must_change_password") and check_password_hash(
            user.get("password_hash", ""),
            LEGACY_SHARED_TEMP_PASSWORD,
        ):
            add_audit_log(
                user.get("id"),
                "legacy_shared_password_retired",
                "Blocked a legacy shared temporary password; a private password reset is required",
                user.get("id"),
            )
            flash("This legacy temporary password can no longer be used. Use Forgot Password to set a private password.", "warning")
            return redirect(url_for("login_page"))

        set_session_for_user(user)
        add_audit_log(user.get("id"), "login_success", "User signed in successfully", user.get("id"))
        if user.get("must_change_password"):
            flash("Please change your password before continuing.", "warning")
            return redirect(url_for("change_password"))
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/password-reset-request", methods=["GET", "POST"])
def password_reset_request():
    csrf_token = session.get("password_reset_csrf")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        session["password_reset_csrf"] = csrf_token

    if request.method == "POST":
        submitted_csrf = request.form.get("csrf_token", "")
        user_identifier = (request.form.get("user_identifier") or request.form.get("user_id") or "").strip()
        csrf_valid = bool(submitted_csrf) and hmac.compare_digest(csrf_token, submitted_csrf)

        if csrf_valid and allow_password_reset_request(request.remote_addr, user_identifier):
            try:
                store = get_user_store()
                user = store.get_user(user_identifier) if user_identifier else None
                if not user and user_identifier:
                    user = store.get_user_by_username(user_identifier)
                saved_email = str(user.get("email") or "").strip() if user else ""
                eligible = (
                    user
                    and normalize_user_status(user.get("status"), default_active=bool(user.get("is_active", True))) == "active"
                    and is_valid_email(saved_email)
                )
                base_url = get_request_base_url()

                if eligible and base_url:
                    try:
                        send_password_reset_link(user, base_url, store)
                    except Exception as error:  # Keep SMTP details and reset tokens out of logs.
                        app.logger.warning("Password reset email delivery failed (%s).", type(error).__name__)
                elif eligible and not base_url:
                    app.logger.warning("Password reset email skipped because the canonical public origin is not configured.")
            except Exception as error:
                app.logger.warning("Password reset request could not be processed (%s).", type(error).__name__)

        flash(PASSWORD_RESET_RESPONSE, "success")
        return redirect(url_for("password_reset_request"))

    return render_template("password_reset_request.html", csrf_token=csrf_token)


def _password_reset_csrf_valid(payload):
    expected = session.get("password_reset_csrf", "")
    supplied = payload.get("csrf_token", "") if isinstance(payload, dict) else ""
    return bool(expected and supplied) and hmac.compare_digest(expected, str(supplied))


def send_password_reset_link(user, base_url, store=None):
    store = store or get_user_store()
    saved_email = str(user.get("email") or "").strip()
    if not is_valid_email(saved_email):
        raise ValueError("The user's saved email address is missing or invalid.")
    canonical_base_url = get_request_base_url()
    if not canonical_base_url or base_url != canonical_base_url:
        raise ValueError("The configured public HTTPS origin is invalid.")
    base_url = canonical_base_url

    reset_user_id = str(user.get("id") or "")
    if not reset_user_id:
        raise ValueError("The user ID is missing.")

    reset_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(reset_token.encode("utf-8")).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    user["password_reset_token_hash"] = token_hash
    user["password_reset_expires_at"] = expires_at.isoformat()
    user["updated_at"] = iso_now()
    store.save_user(user)

    reset_path = url_for("password_reset_page", user_id=reset_user_id)
    reset_link = f"{base_url}{reset_path}#{reset_token}"
    display_name = str(user.get("full_name") or user.get("username") or "there").strip()
    expires_label = expires_at.strftime("%d/%m/%Y %I:%M %p UTC")
    email_body = (
        f"Hello {display_name},\n\n"
        "A password reset was requested for your Mannat Moon Construction account.\n\n"
        f"Reset your password using this link:\n{reset_link}\n\n"
        f"This link expires at {expires_label} (30 minutes after it was issued).\n\n"
        "If you did not request this reset, you can ignore this email.\n"
    )
    email_html = render_template_string(
        """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:24px;background:#f1f5f9;color:#1e293b;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;margin:0 auto;background:#fff;border:1px solid #dbe3eb;border-radius:12px;">
    <tr><td style="padding:22px 26px;background:#111827;color:#fff;border-radius:12px 12px 0 0;">
      <div style="font-size:18px;font-weight:700;letter-spacing:1px;">MANNAT MOON</div>
      <div style="margin-top:5px;color:#cbd5e1;font-size:13px;">Construction Management System</div>
    </td></tr>
    <tr><td style="padding:26px;line-height:1.6;font-size:15px;">
      <h1 style="margin:0 0 18px;font-size:22px;color:#18212b;">Password reset request</h1>
      <p>Hello {{ display_name }},</p>
      <p>A password reset was requested for your Mannat Moon Construction account.</p>
      <p style="margin:24px 0;"><a href="{{ reset_link }}" style="display:inline-block;padding:12px 18px;border-radius:7px;background:#0284c7;color:#fff;text-decoration:none;font-weight:700;">Reset Password</a></p>
      <p>If the button does not work, use this link:<br><a href="{{ reset_link }}" style="color:#0369a1;word-break:break-all;">{{ reset_link }}</a></p>
      <p>This link expires at <strong>{{ expires_label }}</strong> (30 minutes after it was issued).</p>
      <p>If you did not request this reset, you can ignore this email. Your password will not change unless the link is used.</p>
      <p style="margin-bottom:0;">Regards,<br><strong>Mannat Moon Construction Team</strong></p>
    </td></tr>
  </table>
</body></html>""",
        display_name=display_name,
        reset_link=reset_link,
        expires_label=expires_label,
    )
    try:
        sent = send_email(
            saved_email,
            "Mannat Moon \u2014 Password Reset Request",
            email_body,
            html_body=email_html,
        )
        if sent is False:
            raise RuntimeError("Password reset email was not accepted for delivery.")
    except Exception:
        try:
            current_user = store.get_user(reset_user_id)
            if current_user and current_user.get("password_reset_token_hash") == token_hash:
                current_user["password_reset_token_hash"] = None
                current_user["password_reset_expires_at"] = None
                store.save_user(current_user)
        except Exception as cleanup_error:
            app.logger.warning("Password reset token cleanup failed (%s).", type(cleanup_error).__name__)
        raise

    return saved_email


@app.route("/reset-password/<user_id>/validate", methods=["POST"])
def validate_password_reset_link(user_id):
    payload = request.get_json(silent=True) or {}
    if not _password_reset_csrf_valid(payload):
        return jsonify(valid=False, error="This reset link is invalid or expired."), 400
    token = payload.get("token", "")
    if not isinstance(token, str) or not token:
        return jsonify(valid=False, error="This reset link is invalid or expired."), 400
    user = get_user_store().get_user(user_id)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not password_reset_token_is_valid(user, token_hash):
        return jsonify(valid=False, error="This reset link is invalid or expired."), 400
    return jsonify(valid=True)


@app.route("/reset-password/<user_id>", methods=["GET", "POST"])
def password_reset_page(user_id):
    csrf_token = session.get("password_reset_csrf")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        session["password_reset_csrf"] = csrf_token

    if request.method == "POST":
        if not _password_reset_csrf_valid(request.form):
            flash("This reset link is invalid or expired. Request a new reset link.", "error")
            return render_template("reset_password.html", user_id=user_id, csrf_token=csrf_token, invalid_link=True)

        token = request.form.get("token", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest() if token else ""
        user = get_user_store().get_user(user_id)
        if not password_reset_token_is_valid(user, token_hash):
            flash("This reset link is invalid or expired. Request a new reset link.", "error")
            return render_template("reset_password.html", user_id=user_id, csrf_token=csrf_token, invalid_link=True)

        password_error = validate_password_policy(new_password)
        if password_error:
            flash(password_error, "error")
            return render_template("reset_password.html", user_id=user_id, csrf_token=csrf_token, invalid_link=False)
        if new_password != confirm_password:
            flash("The passwords do not match.", "error")
            return render_template("reset_password.html", user_id=user_id, csrf_token=csrf_token, invalid_link=False)

        try:
            consumed = get_user_store().consume_password_reset(
                user_id,
                token_hash,
                generate_password_hash(new_password),
                datetime.now(timezone.utc),
            )
        except Exception as error:
            app.logger.warning("Password reset could not be completed (%s).", type(error).__name__)
            consumed = False

        if not consumed:
            flash("This reset link is invalid or expired. Request a new reset link.", "error")
            return render_template("reset_password.html", user_id=user_id, csrf_token=csrf_token, invalid_link=True)

        flash("Your password has been reset. You can now sign in.", "success")
        return redirect(url_for("login_page"))

    return render_template("reset_password.html", user_id=user_id, csrf_token=csrf_token, invalid_link=False)


@app.route("/test-email", methods=["GET", "POST"])
@require_authenticated_form_csrf
@require_login
@require_role("super_admin")
def test_email():
    if request.method == "POST":
        recipient = (request.form.get("recipient_email") or "").strip()
        if not recipient:
            flash("Recipient email is required.", "error")
            return redirect(url_for("test_email"))
        if not app.config.get("MAIL_PASSWORD") or app.config.get("MAIL_PASSWORD") == "your_gmail_app_password_here":
            flash("Email is not ready to send. Please contact your administrator.", "error")
            return redirect(url_for("test_email"))

        try:
            send_email(
                recipient,
                "Mannat Moon Construction \u2013 Test Email",
                "Hello,\n\n"
                "This is a test email from the Mannat Moon Construction application.\n\n"
                "The email service is working, and the application can send emails successfully.\n\n"
                "Project: Mannat Moon Construction\n"
                "Purpose: Email Configuration Test\n\n"
                "Regards,\n"
                "Mannat Moon Construction Team",
            )
        except Exception as error:  # pragma: no cover - SMTP connection failures are environment-specific
            app.logger.warning(
                "Test email delivery failed: exception_class=%s smtp_host=%s smtp_port=%s tls_enabled=%s message=%s",
                type(error).__name__,
                sanitize_mail_error(app.config.get("MAIL_SERVER", "")),
                app.config.get("MAIL_PORT"),
                bool(app.config.get("MAIL_USE_TLS")),
                sanitize_mail_error(error),
            )
            flash("The test email could not be sent. Please check the recipient address or contact your administrator.", "error")
        else:
            flash("Test email sent successfully.", "success")
        return redirect(url_for("test_email"))

    return render_template_string("""
        <!doctype html>
        <html lang="en">
            <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <title>Mannat Moon - Test Email</title>
                <style>
                    * { box-sizing: border-box; }
                    body { margin: 0; min-height: 100vh; background: #f8fafc; color: #1e293b; font-family: Arial, Helvetica, sans-serif; }
                    header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 18px max(20px, calc((100vw - 1000px) / 2)); background: #111827; color: white; }
                    header h1 { margin: 0; font-size: 20px; font-weight: 600; }
                    header nav { display: flex; align-items: center; gap: 12px; }
                    header a { color: #cbd5e1; text-decoration: none; }
                    header button, .send-button { border: 0; border-radius: 8px; padding: 10px 15px; background: #0ea5e9; color: white; font-weight: 600; cursor: pointer; }
                    main { width: min(620px, calc(100% - 32px)); margin: 48px auto; }
                    .panel { padding: 28px; border: 1px solid #e2e8f0; border-radius: 12px; background: white; box-shadow: 0 8px 24px rgba(15, 23, 42, .06); }
                    h2 { margin: 0 0 8px; font-size: 21px; }
                    .intro { margin: 0 0 22px; color: #64748b; font-size: 14px; line-height: 1.5; }
                    label { display: block; margin-bottom: 7px; font-size: 14px; font-weight: 600; }
                    input { width: 100%; min-height: 44px; margin-bottom: 16px; padding: 10px 12px; border: 1px solid #cbd5e1; border-radius: 7px; font: inherit; }
                    .toast { margin-bottom: 16px; padding: 12px 14px; border-radius: 7px; font-size: 14px; }
                    .toast.success { border: 1px solid #86efac; background: #dcfce7; color: #166534; }
                    .toast.error { border: 1px solid #fca5a5; background: #fee2e2; color: #991b1b; }
                    @media (max-width: 480px) { header { align-items: flex-start; flex-direction: column; } main { margin-top: 24px; } .panel { padding: 20px; } }
                </style>
            </head>
            <body>
                <header>
                    <h1>MANNAT MOON</h1>
                    <nav><a href="{{ url_for('dashboard') }}">Dashboard</a><form method="post" action="{{ url_for('logout') }}" style="margin:0;"><input type="hidden" name="csrf_token" value="{{ authenticated_form_csrf_token() }}"><button type="submit">Logout</button></form></nav>
                </header>
                <main>
                    <section class="panel">
                        <h2>Test Email</h2>
                        <p class="intro">Send a test message using the application's configured email service.</p>
                                                {% with messages = get_flashed_messages(with_categories=true) %}
                                                        {% if messages %}
                                                                {% for category, message in messages %}
                            <div class="toast {{ category }}" role="{{ 'alert' if category == 'error' else 'status' }}" aria-live="{{ 'assertive' if category == 'error' else 'polite' }}">{{ message }}</div>
                                                                {% endfor %}
                                                        {% endif %}
                                                {% endwith %}
                        <form method="post" action="{{ url_for('test_email') }}">
                                                    <input type="hidden" name="csrf_token" value="{{ authenticated_form_csrf_token() }}">
                            <label for="recipient_email">Recipient email address</label>
                            <input type="email" id="recipient_email" name="recipient_email" autocomplete="email" required>
                            <button class="send-button" type="submit">Send Test Email</button>
                        </form>
                    </section>
                </main>
            </body>
        </html>
    """)


@app.route("/logout", methods=["POST"]) 
@require_authenticated_form_csrf
@require_login
def logout():
    user = get_current_user()
    if user:
        current_session_id = session.get("session_id")
        active_sessions = list(user.get("active_sessions", []))
        if current_session_id in active_sessions:
            active_sessions.remove(current_session_id)
            user["active_sessions"] = active_sessions
            user["updated_at"] = iso_now()
            get_user_store().save_user(user)
        add_audit_log(user.get("id"), "logout", "User logged out successfully", user.get("id"))
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login_page"))


@app.route("/change-password", methods=["GET", "POST"])
@require_authenticated_form_csrf
@require_login
def change_password():
    user = get_current_user()
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_new_password", "")

        if not check_password_hash(user.get("password_hash", ""), current_password):
            flash("Current password is incorrect.", "error")
            return redirect(url_for("change_password"))

        if new_password != confirm_password:
            flash("New password and confirmation do not match.", "error")
            return redirect(url_for("change_password"))

        password_error = validate_password_policy(new_password)
        if password_error:
            flash(password_error, "error")
            return redirect(url_for("change_password"))

        if check_password_hash(user.get("password_hash", ""), new_password):
            flash("New password must be different from the current password.", "error")
            return redirect(url_for("change_password"))

        user["password_hash"] = generate_password_hash(new_password)
        user["must_change_password"] = False
        user["last_password_change"] = iso_now()
        user["updated_at"] = iso_now()
        user["authorization_version"] = get_authorization_version(user) + 1
        current_session_id = session.get("session_id")
        user["active_sessions"] = [current_session_id] if current_session_id else []
        get_user_store().save_user(user)
        session.pop("authenticated_form_csrf", None)
        session.pop("authenticated_form_csrf_binding", None)
        add_audit_log(user.get("id"), "password_changed", "Password changed successfully", user.get("id"))
        flash("Password updated successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template("change_password.html", current_user=user)


@app.route("/forgot-password", methods=["GET", "POST"])
@require_authenticated_form_csrf
@require_login
@require_role("super_admin")
def forgot_password():
    store = get_user_store()
    users = store.list_users()
    if request.method == "POST":
        target_user_id = request.form.get("user_id")
        target = store.get_user(target_user_id)
        if not target:
            flash("Selected user could not be found.", "error")
            return redirect(url_for("forgot_password"))
        if str(target.get("role", "")).lower() == "super_admin":
            flash("Use the dedicated Super Admin recovery process for the Super Admin account.", "error")
            return redirect(url_for("forgot_password"))
        try:
            saved_email = send_password_reset_link(target, get_request_base_url(), store)
        except Exception as error:
            app.logger.warning("Admin password reset email failed (%s).", type(error).__name__)
            if isinstance(error, ValueError):
                flash("Unable to send a password-reset link. Check the saved email address and configured public origin.", "error")
            else:
                flash("Unable to send the password-reset email. No password change was made; check the mail configuration and try again.", "error")
            return redirect(url_for("forgot_password"))
        add_audit_log(
            get_current_user().get("id"),
            "password_reset",
            "Password reset link emailed to the user's saved email address",
            target["id"],
        )
        flash(f"A password-reset link was sent to {saved_email}. It expires in 30 minutes.", "success")
        return redirect(url_for("user_management_page"))

    return render_template("forgot_password.html", users=users, current_user=get_current_user())


@app.route("/super-admin-recovery", methods=["GET", "POST"])
def super_admin_recovery():
    if request.method == "POST":
        username = normalize_username(request.form.get("username"))
        recovery_email = (request.form.get("recovery_email") or "").strip()
        recovery_code = (request.form.get("recovery_code") or "").strip()
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        user = get_user_store().get_user_by_username(username)
        if not user:
            flash("Account not found.", "error")
            return redirect(url_for("super_admin_recovery"))
        if str(user.get("role", "")).lower() != "super_admin":
            flash("This recovery process is only available for the Super Admin account.", "error")
            return redirect(url_for("super_admin_recovery"))
        if not user.get("recovery_email"):
            flash("Recovery email is not configured for the Super Admin account. Please set it up from the user management page.", "warning")
            return redirect(url_for("super_admin_recovery"))
        if user.get("recovery_email").lower() != recovery_email.lower():
            flash("The provided recovery email does not match the stored email for this account.", "error")
            return redirect(url_for("super_admin_recovery"))
        if user.get("recovery_code_used"):
            flash("This recovery code has already been used.", "error")
            return redirect(url_for("super_admin_recovery"))
        stored_code = app.config.get("INITIAL_SUPER_ADMIN_RECOVERY_CODE")
        if stored_code and recovery_code.upper() == stored_code.upper():
            pass
        elif user.get("recovery_code_hash") and check_password_hash(user.get("recovery_code_hash"), recovery_code):
            pass
        else:
            flash("The recovery code is invalid.", "error")
            return redirect(url_for("super_admin_recovery"))

        if new_password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for("super_admin_recovery"))

        password_error = validate_password_policy(new_password)
        if password_error:
            flash(password_error, "error")
            return redirect(url_for("super_admin_recovery"))

        user["password_hash"] = generate_password_hash(new_password)
        user["must_change_password"] = False
        user["recovery_code_hash"] = None
        user["recovery_code_used"] = True
        user["updated_at"] = iso_now()
        invalidate_user_sessions(
            user["id"],
            user=user,
            increment_authorization_version=True,
        )
        add_audit_log(user.get("id"), "super_admin_recovery", "Super Admin password recovery completed successfully", user.get("id"))
        flash("Super Admin password successfully recovered.", "success")
        return redirect(url_for("login_page"))

    return render_template("super_admin_recovery.html")


# =========================================================
# USER MANAGEMENT ROUTES
# =========================================================

@app.route("/user-management")
@require_login
@require_role("super_admin")
def user_management_page():
    store = get_user_store()
    users = sorted(store.list_users(), key=lambda item: item.get("username", "").lower())
    custom_types = store.list_custom_staff_types()
    return render_template("user_management.html", users=users, custom_types=custom_types, current_user=get_current_user())


@app.route("/user-management/<user_id>/delete", methods=["POST"])
@require_authenticated_form_csrf
@require_login
@require_role("super_admin")
def user_management_delete_user(user_id):
    store = get_user_store()
    target = store.get_user(user_id)
    actor = get_current_user()
    if not target:
        flash("User not found.", "error")
        return redirect(url_for("user_management_page"))

    actor_id = str(actor.get("id") or "") if actor else ""
    target_id = str(target.get("id") or "")
    if target_id == actor_id:
        flash("You cannot delete your own account while signed in.", "error")
        return redirect(url_for("user_management_page"))

    if str(target.get("role", "")).lower() == "super_admin":
        remaining_super_admins = [
            user for user in store.list_users()
            if str(user.get("id")) != target_id
            and str(user.get("role", "")).lower() == "super_admin"
            and bool(user.get("is_active", True))
        ]
        if not remaining_super_admins:
            flash("At least one active Super Admin account must remain in the system.", "error")
            return redirect(url_for("user_management_page"))

    if target.get("is_active") and str(target.get("role", "")).lower() == "super_admin":
        active_super_admins = [
            user for user in store.list_users()
            if str(user.get("role", "")).lower() == "super_admin"
            and bool(user.get("is_active", True))
        ]
        if len(active_super_admins) <= 1:
            flash("The last active Super Admin account cannot be deleted.", "error")
            return redirect(url_for("user_management_page"))

    destroyed_user = dict(target)
    try:
        deleted = store.delete_user(target_id)
    except Exception as error:
        app.logger.error("User account deletion failed (%s).", type(error).__name__)
        flash("User could not be deleted. Please try again.", "error")
        return redirect(url_for("user_management_page"))
    if not deleted:
        flash("User could not be deleted. Please try again.", "error")
        return redirect(url_for("user_management_page"))

    add_audit_log(actor_id, "user_deleted", f"Deleted user account {destroyed_user.get('username') or destroyed_user.get('full_name') or target_id}", target_id)
    flash(f"User account '{destroyed_user.get('username') or destroyed_user.get('full_name') or 'Unknown'}' was removed.", "success")
    return redirect(url_for("user_management_page"))


@app.route("/user-management/create", methods=["POST"])
@require_authenticated_form_csrf
@require_login
@require_role("super_admin")
def user_management_create():
    username = normalize_username(request.form.get("username"))
    full_name = (request.form.get("full_name") or "").strip()
    email = (request.form.get("email") or "").strip()
    role = (request.form.get("role") or "").strip()
    custom_staff_type = (request.form.get("custom_staff_type") or "").strip()

    if not username:
        flash("Username is required.", "error")
        return ("", 400)
    if not full_name:
        flash("Full name is required.", "error")
        return ("", 400)
    if not is_valid_email(email):
        flash("A valid email address is required to securely set up the account.", "error")
        return ("", 400)
    if get_user_store().get_user_by_username(username):
        flash("A user with that username already exists.", "error")
        return ("", 400)
    normalized_role = normalize_staff_type_name(role)
    if normalized_role.lower() not in {"super_admin", "office_staff", "supervisor"} and not any(staff_type_names_match(item.get("name", ""), normalized_role) for item in get_user_store().list_custom_staff_types()):
        flash("Please select a valid role.", "error")
        return ("", 400)

    user = {
        "id": uuid.uuid4().hex,
        "username": username,
        "full_name": full_name,
        "email": email,
        "recovery_email": (request.form.get("recovery_email") or "").strip(),
        "role": role,
        "custom_staff_type": custom_staff_type,
        "password_hash": generate_password_hash(secrets.token_urlsafe(48)),
        "must_change_password": True,
        "status": "active",
        "is_active": True,
        "active_sessions": [],
        "created_at": iso_now(),
        "updated_at": iso_now(),
        "last_login_at": None,
        "last_password_change": None,
        "recovery_code_hash": None,
        "recovery_code_used": False,
        "permissions": list(ROLE_PERMISSIONS.get(role, [])),
        "created_by": get_current_user().get("id"),
    }
    get_user_store().save_user(user)
    add_audit_log(get_current_user().get("id"), "user_created", f"Created user {username} with role {role}", user["id"])
    try:
        send_password_reset_link(user, get_request_base_url(), get_user_store())
    except Exception as error:
        app.logger.error("Unable to send new account setup link (%s).", type(error).__name__)
        flash("User created, but the password setup email could not be sent. Use Reset User Password to send a new link.", "error")
        return redirect(url_for("user_management_page"))
    flash("User created successfully. A one-time password setup link was sent to the saved email address.", "success")
    return redirect(url_for("user_management_page"))


@app.route("/user-management/custom-type", methods=["POST"])
@require_authenticated_form_csrf
@require_login
@require_role("super_admin")
def custom_staff_type_create():
    type_name = normalize_staff_type_name(request.form.get("type_name"))
    if not type_name:
        flash("Staff type name is required.", "error")
        return redirect(url_for("user_management_page"))
    if is_protected_staff_type_name(type_name):
        flash("That name is reserved for a built-in role and cannot be used as a custom staff type.", "error")
        return redirect(url_for("user_management_page"))
    if any(staff_type_names_match(type_item.get("name", ""), type_name) for type_item in get_user_store().list_custom_staff_types()):
        flash("That staff type already exists.", "error")
        return redirect(url_for("user_management_page"))
    staff_type = {
        "id": uuid.uuid4().hex,
        "name": type_name,
        "created_by": get_current_user().get("id"),
        "created_at": iso_now(),
    }
    get_user_store().save_custom_staff_type(staff_type)
    add_audit_log(get_current_user().get("id"), "custom_staff_type_created", f"Created custom staff type {type_name}", None)
    flash("Custom staff type created.", "success")
    return redirect(url_for("user_management_page"))


@app.route("/user-management/custom-type/<staff_type_id>/edit", methods=["POST"])
@require_authenticated_form_csrf
@require_login
@require_role("super_admin")
def custom_staff_type_edit(staff_type_id):
    store = get_user_store()
    target = next((item for item in store.list_custom_staff_types() if str(item.get("id")) == str(staff_type_id)), None)
    if not target:
        message = "Staff type not found."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": message}), 404
        flash(message, "error")
        return redirect(url_for("user_management_page"))

    if is_protected_staff_type_name(target.get("name", "")):
        message = "Built-in roles cannot be edited."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": message}), 400
        flash(message, "error")
        return redirect(url_for("user_management_page"))

    new_name = normalize_staff_type_name(request.form.get("type_name"))
    if not new_name:
        message = "Staff type name is required."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": message}), 400
        flash(message, "error")
        return redirect(url_for("user_management_page"))

    duplicate = next((item for item in store.list_custom_staff_types() if str(item.get("id")) != str(staff_type_id) and staff_type_names_match(item.get("name", ""), new_name)), None)
    if duplicate:
        message = "That staff type already exists."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": message}), 409
        flash(message, "error")
        return redirect(url_for("user_management_page"))

    previous_name = normalize_staff_type_name(target.get("name", ""))
    previous_permissions = target.get("permissions")
    assigned_users = [
        user for user in store.list_users()
        if staff_type_names_match(user.get("role", ""), previous_name)
        or staff_type_names_match(user.get("custom_staff_type", ""), previous_name)
    ]
    target["name"] = new_name
    target["updated_at"] = iso_now()
    store.save_custom_staff_type(target)
    if previous_name != new_name or previous_permissions != target.get("permissions"):
        for assigned_user in assigned_users:
            invalidate_user_sessions(
                assigned_user.get("id"),
                user=assigned_user,
                increment_authorization_version=True,
            )
    add_audit_log(get_current_user().get("id"), "custom_staff_type_updated", f"Updated custom staff type {new_name}", None)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True, "message": "Custom staff type updated."})

    flash("Custom staff type updated.", "success")
    return redirect(url_for("user_management_page"))


@app.route("/user-management/custom-type/<staff_type_id>/delete", methods=["POST"])
@require_authenticated_form_csrf
@require_login
@require_role("super_admin")
def custom_staff_type_delete(staff_type_id):
    store = get_user_store()
    target = next((item for item in store.list_custom_staff_types() if str(item.get("id")) == str(staff_type_id)), None)
    if not target:
        message = "Staff type not found."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": message}), 404
        flash(message, "error")
        return redirect(url_for("user_management_page"))

    protected_name = normalize_staff_type_name(target.get("name", ""))
    if is_protected_staff_type_name(protected_name):
        message = "Built-in roles cannot be deleted."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": message}), 400
        flash(message, "error")
        return redirect(url_for("user_management_page"))

    assigned_users = [
        user for user in store.list_users()
        if staff_type_names_match(user.get("custom_staff_type", ""), protected_name)
    ]
    if assigned_users:
        user_count = len(assigned_users)
        message = f"This staff type is assigned to {user_count} existing user(s). Reassign them before deleting it."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": message}), 409
        flash(message, "error")
        return redirect(url_for("user_management_page"))

    store.delete_custom_staff_type(staff_type_id)
    add_audit_log(get_current_user().get("id"), "custom_staff_type_deleted", f"Deleted custom staff type {protected_name}", None)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True, "message": "Custom staff type deleted."})

    flash("Custom staff type deleted.", "success")
    return redirect(url_for("user_management_page"))


@app.route("/user-management/<user_id>/edit", methods=["POST"])
@require_authenticated_form_csrf
@require_login
@require_role("super_admin")
def user_management_edit(user_id):
    store = get_user_store()
    target = store.get_user(user_id)
    actor = get_current_user()
    if not target:
        message = "User not found."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": message}), 404
        flash(message, "error")
        return redirect(url_for("user_management_page"))

    new_username = normalize_username(request.form.get("username"))
    new_full_name = (request.form.get("full_name") or "").strip()
    new_email = (request.form.get("email") or "").strip()
    new_recovery_email = (request.form.get("recovery_email") or "").strip()
    new_role = normalize_staff_type_name(request.form.get("role"))
    new_custom_staff_type = normalize_staff_type_name(request.form.get("custom_staff_type"))
    is_active_requested = str(request.form.get("is_active") or "true").strip().lower()
    new_is_active = is_active_requested not in {"false", "0", "no", "off", "inactive"}

    if not new_username:
        message = "Username is required."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": message}), 400
        flash(message, "error")
        return redirect(url_for("user_management_details", user_id=user_id))
    if not new_full_name:
        message = "Full name is required."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": message}), 400
        flash(message, "error")
        return redirect(url_for("user_management_details", user_id=user_id))
    if not is_valid_email(new_email):
        message = "Please enter a valid email address."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": message}), 400
        flash(message, "error")
        return redirect(url_for("user_management_details", user_id=user_id))
    if new_recovery_email and not is_valid_email(new_recovery_email):
        message = "Please enter a valid recovery email address."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": message}), 400
        flash(message, "error")
        return redirect(url_for("user_management_details", user_id=user_id))
    if not new_role:
        message = "Role is required."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": message}), 400
        flash(message, "error")
        return redirect(url_for("user_management_details", user_id=user_id))

    duplicate = store.get_user_by_username(new_username)
    if duplicate and str(duplicate.get("id")) != str(user_id):
        message = "Another user already has that username."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": message}), 409
        flash(message, "error")
        return redirect(url_for("user_management_details", user_id=user_id))

    supported_roles = {str(role).lower() for role in list_available_roles()}
    if new_role.lower() not in supported_roles:
        message = "Please select a valid role for this user."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": message}), 400
        flash(message, "error")
        return redirect(url_for("user_management_details", user_id=user_id))

    if new_custom_staff_type:
        custom_types = store.list_custom_staff_types()
        if not any(staff_type_names_match(item.get("name", ""), new_custom_staff_type) for item in custom_types):
            message = "The selected custom staff type does not exist."
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "message": message}), 400
            flash(message, "error")
            return redirect(url_for("user_management_details", user_id=user_id))

    if str(target.get("id")) == str(actor.get("id")) and new_role.lower() != "super_admin":
        message = "You cannot remove your own Super Admin privileges."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": message}), 400
        flash(message, "error")
        return redirect(url_for("user_management_details", user_id=user_id))

    if str(target.get("id")) == str(actor.get("id")) and not new_is_active:
        message = "You cannot deactivate your own Super Admin account while logged in."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": message}), 400
        flash(message, "error")
        return redirect(url_for("user_management_details", user_id=user_id))

    if str(target.get("role", "")).lower() == "super_admin":
        remaining_super_admins = [
            user for user in store.list_users()
            if str(user.get("id")) != str(user_id)
            and str(user.get("role", "")).lower() == "super_admin"
            and bool(user.get("is_active", True))
        ]
        if new_role.lower() != "super_admin" or not new_is_active:
            if not remaining_super_admins:
                message = "At least one active Super Admin account must remain in the system."
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({"success": False, "message": message}), 400
                flash(message, "error")
                return redirect(url_for("user_management_details", user_id=user_id))

    camera_upload_enabled = request.form.get("camera_upload") == "true"
    changes = {}
    for field_name, new_value in {
        "username": new_username,
        "full_name": new_full_name,
        "email": new_email,
        "recovery_email": new_recovery_email or target.get("recovery_email", ""),
        "role": new_role,
        "custom_staff_type": new_custom_staff_type or target.get("custom_staff_type", ""),
        "is_active": new_is_active,
        "camera_upload": camera_upload_enabled,
    }.items():
        previous_value = target.get(field_name)
        if field_name == "camera_upload":
            previous_value = has_permission(target, "camera_upload")
        if previous_value != new_value:
            changes[field_name] = {"previous": previous_value, "updated": new_value}

    previous_authorization = (
        str(target.get("role") or "").strip().casefold(),
        str(target.get("custom_staff_type") or "").strip().casefold(),
        _user_account_is_active(target),
        tuple(sorted({
            permission
            for permission in target.get("permissions", [])
            if isinstance(permission, str)
        })),
    )
    target["username"] = new_username
    target["full_name"] = new_full_name
    target["email"] = new_email
    target["recovery_email"] = new_recovery_email or target.get("recovery_email", "")
    target["role"] = new_role
    target["custom_staff_type"] = new_custom_staff_type or target.get("custom_staff_type", "")
    target["status"] = "active" if new_is_active else "inactive"
    target["is_active"] = new_is_active
    target["permissions"] = list(ROLE_PERMISSIONS.get(new_role, []))
    if camera_upload_enabled:
        target["permissions"].append("camera_upload")
    target["username_lower"] = new_username.lower()
    target["updated_at"] = iso_now()
    updated_authorization = (
        str(target.get("role") or "").strip().casefold(),
        str(target.get("custom_staff_type") or "").strip().casefold(),
        _user_account_is_active(target),
        tuple(sorted({
            permission
            for permission in target.get("permissions", [])
            if isinstance(permission, str)
        })),
    )
    if previous_authorization != updated_authorization:
        invalidate_user_sessions(
            target["id"],
            user=target,
            increment_authorization_version=True,
        )
    else:
        store.save_user(target)

    details_payload = {
        "changed_fields": list(changes.keys()),
        "changes": changes,
    }
    add_audit_log(actor.get("id"), "user_updated", json.dumps(details_payload, ensure_ascii=False), target["id"])

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True, "message": "User details updated.", "redirect": url_for("user_management_details", user_id=user_id)})

    flash("User details updated.", "success")
    return redirect(url_for("user_management_details", user_id=user_id))


@app.route("/user-management/<user_id>/toggle-status", methods=["POST"])
@require_authenticated_form_csrf
@require_login
@require_role("super_admin")
def user_management_toggle_status(user_id):
    target = get_user_store().get_user(user_id)
    if not target:
        flash("User not found.", "error")
        return redirect(url_for("user_management_page"))

    if str(target.get("role", "")).lower() == "super_admin" and target.get("is_active") and len([u for u in get_user_store().list_users() if u.get("role") == "super_admin" and u.get("is_active")]) <= 1:
        flash("The last active Super Admin account cannot be deactivated.", "error")
        return redirect(url_for("user_management_page"))

    target["status"] = "active" if not bool(target.get("is_active")) else "inactive"
    target["is_active"] = not bool(target.get("is_active"))
    target["updated_at"] = iso_now()
    invalidate_user_sessions(
        target["id"],
        user=target,
        increment_authorization_version=True,
    )
    action = "activated" if target.get("is_active") else "deactivated"
    add_audit_log(get_current_user().get("id"), "account_status_changed", f"User account {action}", target["id"])
    flash(f"User account {action} successfully.", "success")
    return redirect(url_for("user_management_page"))


@app.route("/user-management/<user_id>/reset-password", methods=["POST"])
@require_authenticated_form_csrf
@require_login
@require_role("super_admin")
def user_management_reset_password(user_id):
    store = get_user_store()
    target = store.get_user(user_id)
    if not target:
        flash("User not found.", "error")
        return redirect(url_for("user_management_page"))
    if str(target.get("role", "")).lower() == "super_admin":
        flash("The Super Admin password must be recovered using the special recovery flow.", "error")
        return redirect(url_for("user_management_page"))

    try:
        saved_email = send_password_reset_link(target, get_request_base_url(), store)
    except Exception as error:
        app.logger.warning("Admin password reset email failed (%s).", type(error).__name__)
        if isinstance(error, ValueError):
            flash("Unable to send a password-reset link. Check the saved email address and configured public origin.", "error")
        else:
            flash("Unable to send the password-reset email. No password change was made; check the mail configuration and try again.", "error")
        return redirect(url_for("user_management_page"))

    add_audit_log(
        get_current_user().get("id"),
        "password_reset",
        "Password reset link emailed to the user's saved email address",
        target["id"],
    )
    flash(f"A password-reset link was sent to {saved_email}. It expires in 30 minutes.", "success")
    return redirect(url_for("user_management_page"))


@app.route("/user-management/<user_id>")
@require_login
@require_role("super_admin")
def user_management_details(user_id):
    user = get_user_store().get_user(user_id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("user_management_page"))
    return render_template(
        "user_details.html",
        user=user,
        current_user=get_current_user(),
        roles=list_available_roles(),
        custom_types=get_user_store().list_custom_staff_types(),
        camera_upload_enabled=has_permission(user, "camera_upload"),
    )


# =========================================================
# PHOTO ROUTES
# =========================================================

@app.route("/trade-builder")
@require_role("super_admin")
def trade_builder():
    csrf_token = session.get("contract_values_csrf")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        session["contract_values_csrf"] = csrf_token
    trade_builder_csrf = session.get("trade_builder_csrf")
    if not trade_builder_csrf:
        trade_builder_csrf = secrets.token_urlsafe(32)
        session["trade_builder_csrf"] = trade_builder_csrf
    return render_template(
        "index.html",
        current_user=get_current_user(),
        contract_values_csrf=csrf_token,
        trade_builder_csrf=trade_builder_csrf,
    )


@app.route("/ledger-prints")
@require_login
def ledger_prints():
    user = get_current_user()
    if user.get("must_change_password"):
        return redirect(url_for("change_password"))
    try:
        can_read_ledger = _user_has_compiled_permission(user, "ledger.read")
        can_edit_ledger = (
            _user_has_compiled_permission(user, "contract_values.edit")
            and _user_has_compiled_permission(user, "trade_builder.write")
        )
    except Exception as error:
        app.logger.error("Trade Ledger page authorization failed (%s).", type(error).__name__)
        return Response("Unable to verify Trade Ledger permissions.", status=503, mimetype="text/plain")
    if not can_read_ledger:
        flash("You do not have permission to view Trade Ledgers.", "error")
        return redirect(url_for("dashboard"))
    csrf_token = session.get("contract_values_csrf")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        session["contract_values_csrf"] = csrf_token
    trade_builder_csrf = session.get("trade_builder_csrf")
    if not trade_builder_csrf:
        trade_builder_csrf = secrets.token_urlsafe(32)
        session["trade_builder_csrf"] = trade_builder_csrf
    return render_template(
        "ledger_prints.html",
        current_user=get_current_user(),
        contract_values_csrf=csrf_token,
        trade_builder_csrf=trade_builder_csrf,
        can_edit_ledger=can_edit_ledger,
    )


@app.route("/drawings")
@require_login
def drawings():
    csrf_token = session.get("drawings_csrf")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        session["drawings_csrf"] = csrf_token
    return render_template(
        "drawings.html",
        current_user=get_current_user(),
        drawings_csrf=csrf_token,
    )


class DrawingMutationError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


DRAWING_CREATE_FIELDS = frozenset({
    "categoryId",
    "drawingName",
    "drawingNumber",
    "revision",
    "googleDriveUrl",
    "description",
})


def _drawings_api_response(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _drawings_api_authorize(permission):
    user = get_current_user()
    if not user:
        return None, _drawings_api_response({"error": "Authentication required."}, 401)
    if user.get("must_change_password"):
        return None, _drawings_api_response(
            {"error": "Change your password before continuing."}, 403
        )
    try:
        permitted = _user_has_compiled_permission(user, permission)
    except Exception as error:
        app.logger.error("Drawings authorization failed (%s).", type(error).__name__)
        return None, _drawings_api_response(
            {"error": "Unable to verify Drawings permissions."}, 503
        )
    if not permitted:
        return None, _drawings_api_response(
            {"error": "You do not have permission to perform this Drawings action."}, 403
        )
    expected = session.get("drawings_csrf", "")
    supplied = request.headers.get("X-CSRF-Token", "")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        return None, _drawings_api_response(
            {"error": "Your session expired. Reload the page and try again."}, 403
        )
    return user, None


def _valid_drawing_document_id(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 200
        and value not in {".", ".."}
        and "/" not in value
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _drawing_uuid(value):
    try:
        parsed = uuid.UUID(value) if isinstance(value, str) else None
    except (AttributeError, ValueError):
        parsed = None
    if parsed is None or str(parsed) != value.lower():
        raise DrawingMutationError("Invalid drawing request identifier.")
    return str(parsed)


def _drawing_text(value, field, maximum, *, required=False):
    if not isinstance(value, str):
        raise DrawingMutationError(f"Enter a valid {field}.")
    normalized = value.strip()
    allowed_controls = "\r\n\t" if field == "description" else ""
    has_unsupported_control = any(
        (ord(character) < 32 and character not in allowed_controls)
        or ord(character) == 127
        for character in normalized
    )
    if (
        (required and not normalized)
        or len(normalized) > maximum
        or has_unsupported_control
    ):
        raise DrawingMutationError(f"Enter a valid {field}.")
    return normalized


def _validate_drawing_category_name(value):
    return _drawing_text(value, "category name", 120, required=True)


def _validate_drawing_url(value):
    url = _drawing_text(value, "Google Drive PDF URL", 2048, required=True)
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
    except ValueError:
        hostname = ""
        parsed = None
    if (
        parsed is None
        or parsed.scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or not (
            hostname == "drive.google.com"
            or hostname.endswith(".drive.google.com")
            or hostname == "docs.google.com"
            or hostname.endswith(".docs.google.com")
        )
    ):
        raise DrawingMutationError("Enter a valid Google Drive PDF URL.")
    return url


def _validate_drawing_payload(payload):
    if not isinstance(payload, dict) or set(payload) != DRAWING_CREATE_FIELDS:
        raise DrawingMutationError("Provide only the supported drawing fields.")
    category_id = payload.get("categoryId")
    if not _valid_drawing_document_id(category_id):
        raise DrawingMutationError("Select a valid drawing category.")
    return {
        "categoryId": category_id,
        "drawingName": _drawing_text(payload.get("drawingName"), "drawing name", 160, required=True),
        "drawingNumber": _drawing_text(payload.get("drawingNumber"), "drawing number", 80),
        "revision": _drawing_text(payload.get("revision"), "revision", 80),
        "googleDriveUrl": _validate_drawing_url(payload.get("googleDriveUrl")),
        "description": _drawing_text(payload.get("description"), "description", 1000),
    }


def _drawing_dict(item):
    if hasattr(item, "to_dict"):
        return item.to_dict() or {}
    return item if isinstance(item, dict) else {}


def _drawing_sort_order(drawing):
    value = _drawing_dict(drawing).get("sortOrder")
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 and str(value).strip() == str(parsed) else None


def _drawing_create_fields(payload, category, category_drawings):
    orders = [
        order
        for order in (_drawing_sort_order(item) for item in category_drawings)
        if order is not None
    ]
    return {
        **payload,
        "categoryName": category.get("name", ""),
        "sortOrder": max(orders, default=-1) + 1,
    }


def _drawing_update_fields(payload, category, existing, category_drawings, drawing_id=None):
    def item_id(item):
        if isinstance(item, dict):
            return item.get("id")
        return getattr(item, "id", None)

    existing_category_id = existing.get("categoryId")
    if existing_category_id == payload["categoryId"]:
        sort_order = _drawing_sort_order(existing)
        if sort_order is None:
            relevant = category_drawings
            if drawing_id:
                relevant = [
                    item for item in relevant
                    if item_id(item) != drawing_id
                ]
            orders = [
                order
                for order in (_drawing_sort_order(item) for item in relevant)
                if order is not None
            ]
            sort_order = max(orders, default=-1) + 1
    else:
        relevant = [
            item for item in category_drawings
            if item_id(item) != drawing_id
        ]
        orders = [
            order
            for order in (_drawing_sort_order(item) for item in relevant)
            if order is not None
        ]
        sort_order = max(orders, default=-1) + 1
    return {
        **payload,
        "categoryName": category.get("name", ""),
        "sortOrder": sort_order,
    }


def _drawings_mutation_result(operation, *args):
    try:
        return operation(*args), None
    except DrawingMutationError as error:
        return None, _drawings_api_response({"error": str(error)}, error.status)
    except Exception as error:
        app.logger.error("Drawings mutation failed (%s).", type(error).__name__)
        return None, _drawings_api_response(
            {"error": "Unable to save the Drawings Library change."}, 503
        )


@app.route("/api/drawings/categories", methods=["POST"])
def drawing_category_create_api():
    _user, denied = _drawings_api_authorize("drawings.write")
    if denied:
        return denied
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"id", "name"}:
        return _drawings_api_response({"error": "Provide a valid category request."}, 400)
    try:
        category_id = _drawing_uuid(payload["id"])
        name = _validate_drawing_category_name(payload["name"])
    except DrawingMutationError as error:
        return _drawings_api_response({"error": str(error)}, error.status)
    result, error_response = _drawings_mutation_result(
        get_user_store().create_drawing_category, category_id, name
    )
    if error_response:
        return error_response
    return _drawings_api_response(
        {"id": result["id"], "duplicate": result["duplicate"]},
        200 if result["duplicate"] else 201,
    )


@app.route("/api/drawings/categories/<category_id>", methods=["PATCH", "DELETE"])
def drawing_category_api(category_id):
    permission = "drawings.delete" if request.method == "DELETE" else "drawings.write"
    _user, denied = _drawings_api_authorize(permission)
    if denied:
        return denied
    if not _valid_drawing_document_id(category_id):
        return _drawings_api_response({"error": "Invalid category identifier."}, 400)
    if request.method == "PATCH":
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {"name"}:
            return _drawings_api_response({"error": "Provide only the category name."}, 400)
        try:
            name = _validate_drawing_category_name(payload["name"])
        except DrawingMutationError as error:
            return _drawings_api_response({"error": str(error)}, error.status)
        result, error_response = _drawings_mutation_result(
            get_user_store().update_drawing_category, category_id, name
        )
        if error_response:
            return error_response
        if result is None:
            return _drawings_api_response({"error": "Drawing category not found."}, 404)
        return _drawings_api_response(result)
    result, error_response = _drawings_mutation_result(
        get_user_store().delete_drawing_category, category_id
    )
    if error_response:
        return error_response
    if not result:
        return _drawings_api_response({"error": "Drawing category not found."}, 404)
    return _drawings_api_response({"deleted": True})


@app.route("/api/drawings/categories/order", methods=["POST"])
def drawing_categories_reorder_api():
    _user, denied = _drawings_api_authorize("drawings.write")
    if denied:
        return denied
    payload = request.get_json(silent=True)
    ids = payload.get("ids") if isinstance(payload, dict) and set(payload) == {"ids"} else None
    if (
        not isinstance(ids, list)
        or len(ids) > 500
        or any(not _valid_drawing_document_id(item) for item in ids)
        or len(set(ids)) != len(ids)
    ):
        return _drawings_api_response({"error": "Provide a valid category order."}, 400)
    result, error_response = _drawings_mutation_result(
        get_user_store().reorder_drawing_categories, ids
    )
    if error_response:
        return error_response
    return _drawings_api_response({"updated": True})


@app.route("/api/drawings", methods=["POST"])
def drawing_create_api():
    _user, denied = _drawings_api_authorize("drawings.write")
    if denied:
        return denied
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != DRAWING_CREATE_FIELDS | {"id"}:
        return _drawings_api_response({"error": "Provide a valid drawing request."}, 400)
    try:
        drawing_id = _drawing_uuid(payload["id"])
        drawing_payload = _validate_drawing_payload(
            {key: value for key, value in payload.items() if key != "id"}
        )
    except DrawingMutationError as error:
        return _drawings_api_response({"error": str(error)}, error.status)
    result, error_response = _drawings_mutation_result(
        get_user_store().create_drawing, drawing_id, drawing_payload
    )
    if error_response:
        return error_response
    return _drawings_api_response(
        {"id": result["id"], "duplicate": result["duplicate"]},
        200 if result["duplicate"] else 201,
    )


@app.route("/api/drawings/<drawing_id>", methods=["PATCH", "DELETE"])
def drawing_api(drawing_id):
    permission = "drawings.delete" if request.method == "DELETE" else "drawings.write"
    _user, denied = _drawings_api_authorize(permission)
    if denied:
        return denied
    if not _valid_drawing_document_id(drawing_id):
        return _drawings_api_response({"error": "Invalid drawing identifier."}, 400)
    if request.method == "PATCH":
        try:
            payload = _validate_drawing_payload(request.get_json(silent=True))
        except DrawingMutationError as error:
            return _drawings_api_response({"error": str(error)}, error.status)
        result, error_response = _drawings_mutation_result(
            get_user_store().update_drawing, drawing_id, payload
        )
        if error_response:
            return error_response
        if result is None:
            return _drawings_api_response({"error": "Drawing not found."}, 404)
        return _drawings_api_response(result)
    result, error_response = _drawings_mutation_result(
        get_user_store().delete_drawing, drawing_id
    )
    if error_response:
        return error_response
    if not result:
        return _drawings_api_response({"error": "Drawing not found."}, 404)
    return _drawings_api_response({"deleted": True})


@app.route("/api/drawings/categories/<category_id>/order", methods=["POST"])
def drawings_reorder_api(category_id):
    _user, denied = _drawings_api_authorize("drawings.write")
    if denied:
        return denied
    if not _valid_drawing_document_id(category_id):
        return _drawings_api_response({"error": "Invalid category identifier."}, 400)
    payload = request.get_json(silent=True)
    ids = payload.get("ids") if isinstance(payload, dict) and set(payload) == {"ids"} else None
    if (
        not isinstance(ids, list)
        or len(ids) > 500
        or any(not _valid_drawing_document_id(item) for item in ids)
        or len(set(ids)) != len(ids)
    ):
        return _drawings_api_response({"error": "Provide a valid drawing order."}, 400)
    result, error_response = _drawings_mutation_result(
        get_user_store().reorder_drawings, category_id, ids
    )
    if error_response:
        return error_response
    return _drawings_api_response({"updated": True})


@app.route("/materials")
@require_login
def material_builder():
    csrf_token = session.get("material_transactions_csrf")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        session["material_transactions_csrf"] = csrf_token
    return render_template(
        "materials.html",
        current_user=get_current_user(),
        material_transactions_csrf=csrf_token,
    )


def _material_transactions_api_response(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _material_transactions_api_authorize(permission):
    user = get_current_user()
    if not user:
        return None, _material_transactions_api_response({"error": "Authentication required."}, 401)
    if user.get("must_change_password"):
        return None, _material_transactions_api_response(
            {"error": "Change your password before continuing."}, 403
        )
    try:
        permitted = _user_has_compiled_permission(user, permission)
    except Exception as error:
        app.logger.error("Material transaction authorization failed (%s).", type(error).__name__)
        return None, _material_transactions_api_response(
            {"error": "Unable to verify material transaction permissions."}, 503
        )
    if not permitted:
        return None, _material_transactions_api_response(
            {"error": "You do not have permission to perform this material transaction."}, 403
        )
    expected = session.get("material_transactions_csrf", "")
    supplied = request.headers.get("X-CSRF-Token", "")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        return None, _material_transactions_api_response(
            {"error": "Your session expired. Reload the page and try again."}, 403
        )
    return user, None


def _material_master_api_error(error, action):
    if isinstance(error, MaterialMasterDataError):
        return _material_transactions_api_response({"error": str(error)}, error.status)
    app.logger.error("Material master-data %s failed (%s).", action, type(error).__name__)
    return _material_transactions_api_response(
        {"error": f"Unable to {action} material master data. Please try again."}, 503
    )


@app.route("/api/material-master/categories", methods=["POST"])
def material_category_create_api():
    _user, denied = _material_transactions_api_authorize("materials.write")
    if denied:
        return denied
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != MATERIAL_CATEGORY_FIELDS | {"id"}:
        return _material_transactions_api_response(
            {"error": "Provide a valid material category request."}, 400
        )
    category_id = payload.get("id")
    if not _valid_material_id(category_id) or len(category_id) > 200:
        return _material_transactions_api_response(
            {"error": "Provide a valid material category identifier."}, 400
        )
    try:
        category_payload = _validate_material_category_payload(
            {field: payload[field] for field in MATERIAL_CATEGORY_FIELDS}
        )
        result = get_user_store().mutate_material_category(
            "create", category_id, category_payload
        )
    except Exception as error:
        return _material_master_api_error(error, "create")
    return _material_transactions_api_response(
        {"id": category_id, "duplicate": result["duplicate"]},
        200 if result.get("duplicate") else 201,
    )


@app.route("/api/material-master/categories/<category_id>", methods=["PATCH", "DELETE"])
def material_category_item_api(category_id):
    action = "delete" if request.method == "DELETE" else "edit"
    permission = "materials.delete" if action == "delete" else "materials.write"
    _user, denied = _material_transactions_api_authorize(permission)
    if denied:
        return denied
    if not _valid_material_id(category_id) or len(category_id) > 200:
        return _material_transactions_api_response(
            {"error": "Invalid material category identifier."}, 400
        )
    try:
        payload = None
        if action == "edit":
            payload = _validate_material_category_payload(request.get_json(silent=True))
        result = get_user_store().mutate_material_category(
            action, category_id, payload
        )
    except Exception as error:
        return _material_master_api_error(error, action)
    if result is None:
        return _material_transactions_api_response(
            {"error": "Material category not found."}, 404
        )
    return _material_transactions_api_response(
        {"id": category_id, "deleted": action == "delete"}
    )


@app.route("/api/material-master/suppliers", methods=["POST"])
def material_supplier_create_api():
    _user, denied = _material_transactions_api_authorize("materials.write")
    if denied:
        return denied
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != MATERIAL_SUPPLIER_FIELDS | {"id"}:
        return _material_transactions_api_response(
            {"error": "Provide a valid supplier request."}, 400
        )
    supplier_id = payload.get("id")
    if not _valid_material_id(supplier_id) or len(supplier_id) > 200:
        return _material_transactions_api_response(
            {"error": "Provide a valid supplier identifier."}, 400
        )
    try:
        supplier_payload = _validate_material_supplier_payload(
            {field: payload[field] for field in MATERIAL_SUPPLIER_FIELDS}
        )
        result = get_user_store().mutate_material_supplier(
            "create", supplier_id, supplier_payload
        )
    except Exception as error:
        return _material_master_api_error(error, "create")
    return _material_transactions_api_response(
        {"id": supplier_id, "duplicate": result["duplicate"]},
        200 if result.get("duplicate") else 201,
    )


@app.route("/api/material-master/suppliers/<supplier_id>", methods=["PATCH", "DELETE"])
def material_supplier_item_api(supplier_id):
    action = "delete" if request.method == "DELETE" else "edit"
    permission = "materials.delete" if action == "delete" else "materials.write"
    _user, denied = _material_transactions_api_authorize(permission)
    if denied:
        return denied
    if not _valid_material_id(supplier_id) or len(supplier_id) > 200:
        return _material_transactions_api_response(
            {"error": "Invalid supplier identifier."}, 400
        )
    try:
        payload = None
        if action == "edit":
            payload = _validate_material_supplier_payload(request.get_json(silent=True))
        result = get_user_store().mutate_material_supplier(action, supplier_id, payload)
    except Exception as error:
        return _material_master_api_error(error, action)
    if result is None:
        return _material_transactions_api_response({"error": "Supplier not found."}, 404)
    return _material_transactions_api_response(
        {"id": supplier_id, "deleted": action == "delete"}
    )


@app.route("/api/material-transactions/<kind>", methods=["POST"])
def material_transaction_create_api(kind):
    if kind not in {"purchases", "consumptions"}:
        return _material_transactions_api_response({"error": "Invalid material transaction type."}, 404)
    _user, denied = _material_transactions_api_authorize("materials.transactions.write")
    if denied:
        return denied
    try:
        request_id = request.headers.get("Idempotency-Key", "")
        try:
            transaction_id = str(uuid.UUID(request_id))
        except (AttributeError, ValueError):
            return _material_transactions_api_response(
                {"error": "A valid transaction request identifier is required."}, 400
            )
        if transaction_id != request_id.lower():
            return _material_transactions_api_response(
                {"error": "A valid transaction request identifier is required."}, 400
            )
        result = get_user_store().mutate_material_transaction(
            kind,
            "create",
            transaction_id,
            request.get_json(silent=True),
        )
    except MaterialTransactionError as error:
        return _material_transactions_api_response({"error": str(error)}, error.status)
    except Exception as error:
        app.logger.error("Material transaction creation failed (%s).", type(error).__name__)
        return _material_transactions_api_response(
            {"error": "Unable to save the material transaction. Please try again."}, 503
        )
    return _material_transactions_api_response(
        {"id": result["id"]},
        200 if result.get("duplicate") else 201,
    )


@app.route("/api/material-transactions/<kind>/<path:transaction_id>", methods=["PATCH", "DELETE"])
def material_transaction_item_api(kind, transaction_id):
    if kind not in {"purchases", "consumptions"}:
        return _material_transactions_api_response({"error": "Invalid material transaction type."}, 404)
    action = "delete" if request.method == "DELETE" else "edit"
    permission = (
        "materials.transactions.delete"
        if action == "delete"
        else "materials.transactions.write"
    )
    _user, denied = _material_transactions_api_authorize(permission)
    if denied:
        return denied
    if not _valid_material_id(transaction_id):
        return _material_transactions_api_response({"error": "Invalid material transaction ID."}, 400)
    try:
        result = get_user_store().mutate_material_transaction(
            kind,
            action,
            transaction_id,
            request.get_json(silent=True) if action == "edit" else None,
        )
    except MaterialTransactionError as error:
        return _material_transactions_api_response({"error": str(error)}, error.status)
    except Exception as error:
        app.logger.error("Material transaction mutation failed (%s).", type(error).__name__)
        return _material_transactions_api_response(
            {"error": "Unable to update the material transaction. Please try again."}, 503
        )
    if result is None:
        return _material_transactions_api_response({"error": "Material transaction not found."}, 404)
    return _material_transactions_api_response({"id": transaction_id}, 200)


@app.route("/building-cost-summary")
@require_login
def building_cost_summary():
    return render_template("building_cost_summary.html", current_user=get_current_user())


@app.route("/labour-payments")
@require_login
def labour_payments():
    csrf_token = session.get("labour_payments_csrf")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        session["labour_payments_csrf"] = csrf_token
    return render_template(
        "labour_payments.html",
        current_user=get_current_user(),
        labour_payments_csrf=csrf_token,
    )


@app.route("/owner-scope")
@require_login
def owner_scope_page():
    csrf_token = session.get("owner_scope_csrf")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        session["owner_scope_csrf"] = csrf_token
    return render_template(
        "owner_scope.html",
        current_user=get_current_user(),
        csrf_token=csrf_token,
    )


def _owner_scope_api_authorize(permission):
    user = get_current_user()
    if not user:
        return None, jsonify(error="Authentication required."), 401
    if user.get("must_change_password"):
        return None, jsonify(error="Change your password before continuing."), 403
    try:
        permitted = _user_has_compiled_permission(user, permission)
    except Exception as error:
        app.logger.error("Owner Scope permission check failed (%s).", type(error).__name__)
        return None, jsonify(error="Unable to verify Owner Scope permissions."), 503
    if not permitted:
        return None, jsonify(error="You do not have permission to perform this Owner Scope action."), 403
    if not _owner_scope_api_csrf_valid():
        return None, jsonify(error="Your session expired. Reload the page and try again."), 403
    return user, None, None


def _owner_scope_api_csrf_valid():
    expected = session.get("owner_scope_csrf", "")
    supplied = request.headers.get("X-CSRF-Token", "")
    return bool(expected) and bool(supplied) and hmac.compare_digest(expected, supplied)


class ContractMutationError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class TradeMutationError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


TRADE_COMMON_FIELDS = (
    "Work / Activity",
    "Location / Floor",
    "Materials",
    "Notes / Issues / Decisions",
    "Remarks / Additional Items",
    "Visual Evidence",
)
TRADE_WORKER_FIELDS = {
    "id",
    "name",
    "payment_type",
    "currentWage",
    "effectiveFrom",
    "effective_from",
    "wageHistory",
    "lump_sum_amount",
    "work_description",
}


def _valid_trade_id(value):
    return (
        _valid_material_id(value)
        and value != LEGACY_OWNER_ACCOUNT_DOCUMENT_ID
    )


def _trade_create_matches(existing, payload):
    return (
        all(existing.get(key) == value for key, value in payload.items())
        and existing.get("contractItems") == []
        and existing.get("paymentTransactions") == []
    )


def _trade_number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TradeMutationError(f"Enter a valid {field}.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise TradeMutationError(f"Enter a valid {field}.") from None
    if not math.isfinite(number) or number < 0:
        raise TradeMutationError(f"{field} must be zero or greater.")
    return number


def _trade_date(value, field):
    if not isinstance(value, str) or not value:
        raise TradeMutationError(f"Enter a valid {field}.")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise TradeMutationError(f"Enter a valid {field}.") from None
    if parsed.strftime("%Y-%m-%d") != value:
        raise TradeMutationError(f"Enter a valid {field}.")
    return value


def _validate_trade_configuration(payload, *, creating):
    if not isinstance(payload, dict):
        raise TradeMutationError("Provide valid trade details.")
    expected_fields = {"name", "workerCategories", "commonFields"}
    if not creating and "completedWorkValue" in payload:
        expected_fields.add("completedWorkValue")
    if set(payload) != expected_fields:
        raise TradeMutationError("Provide only supported trade configuration fields.")

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
        raise TradeMutationError("Enter a valid trade name.")
    name = name.strip().upper()

    workers = payload.get("workerCategories")
    if not isinstance(workers, list) or not workers or len(workers) > 100:
        raise TradeMutationError("Add at least one valid worker category.")
    normalized_workers = []
    worker_ids = set()
    worker_names = set()
    for worker in workers:
        if not isinstance(worker, dict) or set(worker) - TRADE_WORKER_FIELDS:
            raise TradeMutationError("Provide valid worker category details.")
        worker_id = worker.get("id")
        worker_name = worker.get("name")
        payment_type = worker.get("payment_type")
        if not _valid_material_id(worker_id):
            raise TradeMutationError("Invalid worker category ID.")
        if worker_id in worker_ids:
            raise TradeMutationError("Worker category IDs must be unique.")
        worker_ids.add(worker_id)
        if not isinstance(worker_name, str) or not worker_name.strip() or len(worker_name.strip()) > 200:
            raise TradeMutationError("Enter a valid worker category name.")
        normalized_name = worker_name.strip()
        normalized_name_key = normalized_name.casefold()
        if normalized_name_key in worker_names:
            raise TradeMutationError("Worker category names must be unique.")
        worker_names.add(normalized_name_key)
        if not isinstance(payment_type, str) or payment_type not in {
            "daily_wage", "lump_sum"
        }:
            raise TradeMutationError("Choose a valid worker payment type.")

        effective_date = worker.get("effective_from") or worker.get("effectiveFrom") or ""
        if effective_date:
            effective_date = _trade_date(effective_date, "effective date")
        if (
            worker.get("effective_from", effective_date) != effective_date
            or worker.get("effectiveFrom", effective_date) != effective_date
        ):
            raise TradeMutationError("Worker effective dates must match.")

        normalized_worker = {
            **worker,
            "id": worker_id,
            "name": normalized_name,
            "payment_type": payment_type,
            "effective_from": effective_date,
            "effectiveFrom": effective_date,
        }
        if payment_type == "daily_wage":
            normalized_worker["currentWage"] = _trade_number(
                worker.get("currentWage", 0), "Daily wage"
            )
        else:
            amount = _trade_number(worker.get("lump_sum_amount"), "Lump-sum amount")
            if amount <= 0 or not effective_date:
                raise TradeMutationError(
                    "Lump-sum categories need an amount greater than zero and an effective date."
                )
            normalized_worker["lump_sum_amount"] = amount
            description = worker.get("work_description", "")
            if not isinstance(description, str) or len(description) > 2000:
                raise TradeMutationError("Enter a valid lump-sum work description.")
            normalized_worker["work_description"] = description.strip()

        history = worker.get("wageHistory", [])
        if not isinstance(history, list) or len(history) > 500:
            raise TradeMutationError("Provide valid worker wage history.")
        normalized_history = []
        for entry in history:
            if not isinstance(entry, dict) or set(entry) - {
                "dailyWage", "effectiveFrom", "recordedAt"
            }:
                raise TradeMutationError("Provide valid worker wage history.")
            history_date = _trade_date(entry.get("effectiveFrom"), "wage history date")
            recorded_at = entry.get("recordedAt")
            if not isinstance(recorded_at, str) or not recorded_at.strip() or len(recorded_at) > 64:
                raise TradeMutationError("Provide valid worker wage history.")
            normalized_history.append({
                "dailyWage": _trade_number(entry.get("dailyWage"), "Historical wage"),
                "effectiveFrom": history_date,
                "recordedAt": recorded_at,
            })
        if "wageHistory" in worker:
            normalized_worker["wageHistory"] = normalized_history
        normalized_workers.append(normalized_worker)

    common_fields = payload.get("commonFields")
    if common_fields != list(TRADE_COMMON_FIELDS):
        raise TradeMutationError("Trade common fields are not valid.")

    normalized = {
        "name": name,
        "workerCategories": normalized_workers,
        "commonFields": list(TRADE_COMMON_FIELDS),
    }
    if "completedWorkValue" in payload:
        normalized["completedWorkValue"] = _trade_number(
            payload["completedWorkValue"], "Completed work value"
        )
    return normalized


def _trade_api_response(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _trade_api_authorize(permission):
    user = get_current_user()
    if not user:
        return None, _trade_api_response({"error": "Authentication required."}, 401)
    if user.get("must_change_password"):
        return None, _trade_api_response(
            {"error": "Change your password before continuing."}, 403
        )
    try:
        permitted = _user_has_compiled_permission(user, permission)
    except Exception as error:
        app.logger.error("Trade authorization failed (%s).", type(error).__name__)
        return None, _trade_api_response(
            {"error": "Unable to verify Trade Builder permissions."}, 503
        )
    if not permitted:
        return None, _trade_api_response(
            {"error": "You do not have permission to perform this Trade Builder action."},
            403,
        )
    expected = session.get("trade_builder_csrf", "")
    supplied = request.headers.get("X-CSRF-Token", "")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        return None, _trade_api_response(
            {"error": "Your session expired. Reload the page and try again."}, 403
        )
    return user, None


def _validate_contract_item(item, *, allow_id):
    if not isinstance(item, dict):
        raise ContractMutationError("Provide valid contract item details.")
    allowed_fields = {"name", "amount"}
    if allow_id:
        allowed_fields.add("id")
    if set(item) - allowed_fields:
        raise ContractMutationError("Provide only contract item name, amount, and ID.")
    name = item.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 500:
        raise ContractMutationError("Enter a valid contract item name.")
    amount = item.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise ContractMutationError("Enter a valid contract amount.")
    try:
        amount = float(amount)
    except (TypeError, ValueError, OverflowError):
        raise ContractMutationError("Enter a valid contract amount.") from None
    if not math.isfinite(amount) or amount < 0:
        raise ContractMutationError("Contract amounts must be zero or greater.")
    item_id = item.get("id") if allow_id else None
    if item_id is not None and (
        not isinstance(item_id, str)
        or not item_id
        or len(item_id) > 200
        or "/" in item_id
        or any(ord(character) < 32 or ord(character) == 127 for character in item_id)
    ):
        raise ContractMutationError("Invalid contract item ID.")
    return {"id": item_id, "name": name.strip(), "amount": amount}


def _apply_contract_items_mutation(trade, action, contract_id=None, contract_data=None):
    if not isinstance(trade, dict):
        raise ContractMutationError("Trade not found.", 404)
    current_items = trade.get("contractItems", [])
    if not isinstance(current_items, list) or any(not isinstance(item, dict) for item in current_items):
        raise ContractMutationError("Stored contract data is invalid.", 409)
    item_ids = [item.get("id") for item in current_items]
    if any(not isinstance(item_id, str) or not item_id for item_id in item_ids) or len(item_ids) != len(set(item_ids)):
        raise ContractMutationError("Stored contract data is invalid.", 409)
    updated_items = [dict(item) for item in current_items]
    now = iso_now()

    if action == "create":
        item = _validate_contract_item(contract_data, allow_id=False)
        if len(updated_items) >= 500:
            raise ContractMutationError("This trade has reached the contract item limit.")
        created = {
            "id": str(uuid.uuid4()),
            "name": item["name"],
            "amount": item["amount"],
            "createdAt": now,
            "updatedAt": now,
        }
        updated_items.append(created)
    elif action in {"edit", "delete"}:
        target_index = next(
            (index for index, item in enumerate(updated_items) if item.get("id") == contract_id),
            None,
        )
        if target_index is None:
            raise ContractMutationError("Contract item not found.", 404)
        if action == "delete":
            updated_items.pop(target_index)
        else:
            item = _validate_contract_item(contract_data, allow_id=False)
            updated_items[target_index].update({
                "name": item["name"],
                "amount": item["amount"],
                "updatedAt": now,
            })
    elif action == "replace":
        if not isinstance(contract_data, list) or len(contract_data) > 500:
            raise ContractMutationError("Provide a valid list of contract items.")
        existing_by_id = {item["id"]: item for item in updated_items}
        replacement = []
        seen_ids = set()
        for raw_item in contract_data:
            item = _validate_contract_item(raw_item, allow_id=True)
            existing = existing_by_id.get(item["id"]) if item["id"] else None
            item_id = existing["id"] if existing else str(uuid.uuid4())
            if item_id in seen_ids:
                raise ContractMutationError("Contract item IDs must be unique.")
            seen_ids.add(item_id)
            replacement.append({
                **(existing or {}),
                "id": item_id,
                "name": item["name"],
                "amount": item["amount"],
                "createdAt": (existing or {}).get("createdAt") or now,
                "updatedAt": now if not existing or (
                    existing.get("name") != item["name"]
                    or existing.get("amount") != item["amount"]
                ) else (existing.get("updatedAt") or now),
            })
        updated_items = replacement
    else:
        raise ContractMutationError("Invalid contract operation.")

    updated_trade = dict(trade)
    updated_trade["contractItems"] = updated_items
    return updated_trade, updated_items


def _contract_values_api_response(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _contract_values_api_authorize():
    user = get_current_user()
    if not user:
        return None, _contract_values_api_response({"error": "Authentication required."}, 401)
    if user.get("must_change_password"):
        return None, _contract_values_api_response({"error": "Change your password before continuing."}, 403)
    try:
        permitted = _user_has_compiled_permission(user, "contract_values.edit")
    except Exception as error:
        app.logger.error("Contract permission check failed (%s).", type(error).__name__)
        return None, _contract_values_api_response({"error": "Unable to verify contract permissions."}, 503)
    if not permitted:
        return None, _contract_values_api_response({"error": "You do not have permission to edit contract values."}, 403)
    expected = session.get("contract_values_csrf", "")
    supplied = request.headers.get("X-CSRF-Token", "")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        return None, _contract_values_api_response({"error": "Your session expired. Reload the page and try again."}, 403)
    return user, None


@app.route("/api/trades", methods=["POST"])
def trade_create_api():
    _user, denied = _trade_api_authorize("trade_builder.write")
    if denied:
        return denied
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {
        "id", "name", "workerCategories", "commonFields"
    }:
        return _trade_api_response({"error": "Provide valid trade details."}, 400)
    trade_id = payload.get("id")
    try:
        parsed_id = uuid.UUID(trade_id) if isinstance(trade_id, str) else None
    except (AttributeError, ValueError):
        parsed_id = None
    if (
        parsed_id is None
        or str(parsed_id) != trade_id.lower()
        or not _valid_trade_id(trade_id)
    ):
        return _trade_api_response({"error": "Invalid trade ID."}, 400)
    try:
        trade_data = _validate_trade_configuration(
            {key: value for key, value in payload.items() if key != "id"},
            creating=True,
        )
        result = get_user_store().create_trade(trade_id, trade_data)
    except TradeMutationError as error:
        return _trade_api_response({"error": str(error)}, error.status)
    except Exception as error:
        app.logger.error("Trade creation failed (%s).", type(error).__name__)
        return _trade_api_response(
            {"error": "Unable to create the trade. Please try again."}, 503
        )
    return _trade_api_response(
        {"id": result["id"]}, 200 if result.get("duplicate") else 201
    )


@app.route("/api/trades/<trade_id>", methods=["PATCH", "DELETE"])
def trade_item_api(trade_id):
    permission = (
        "trade_builder.delete"
        if request.method == "DELETE"
        else "trade_builder.write"
    )
    _user, denied = _trade_api_authorize(permission)
    if denied:
        return denied
    if not _valid_trade_id(trade_id):
        return _trade_api_response({"error": "Invalid trade ID."}, 400)

    try:
        store = get_user_store()
        if request.method == "DELETE":
            deleted = store.delete_trade(trade_id)
            if not deleted:
                return _trade_api_response({"error": "Trade not found."}, 404)
            return _trade_api_response({"deleted": True})

        payload = request.get_json(silent=True)
        trade_data = _validate_trade_configuration(payload, creating=False)
        updated = store.update_trade_configuration(trade_id, trade_data)
    except TradeMutationError as error:
        return _trade_api_response({"error": str(error)}, error.status)
    except Exception as error:
        app.logger.error("Trade mutation failed (%s).", type(error).__name__)
        return _trade_api_response(
            {"error": "Unable to update the trade. Please try again."}, 503
        )
    if updated is None:
        return _trade_api_response({"error": "Trade not found."}, 404)
    return _trade_api_response({"id": trade_id})


@app.route("/api/trades/<trade_id>/name", methods=["PATCH"])
def trade_name_api(trade_id):
    _user, denied = _trade_api_authorize("trade_builder.write")
    if denied:
        return denied
    if not _valid_trade_id(trade_id):
        return _trade_api_response({"error": "Invalid trade ID."}, 400)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"name"}:
        return _trade_api_response({"error": "Provide only a trade name."}, 400)
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
        return _trade_api_response({"error": "Enter a valid trade name."}, 400)
    try:
        result = get_user_store().rename_trade(trade_id, name.strip().upper())
    except Exception as error:
        app.logger.error("Trade name update failed (%s).", type(error).__name__)
        return _trade_api_response(
            {"error": "Unable to rename the trade. Please try again."}, 503
        )
    if result is None:
        return _trade_api_response({"error": "Trade not found."}, 404)
    return _trade_api_response({
        "id": trade_id,
        "name": result["name"],
        "updatedAt": result["updatedAt"],
    })


@app.route("/api/trades/<trade_id>/contract-items", methods=["POST", "PUT"])
def trade_contract_items_api(trade_id):
    _user, denied = _contract_values_api_authorize()
    if denied:
        return denied
    if not trade_id or "/" in trade_id or len(trade_id.encode("utf-8")) > 1500:
        return _contract_values_api_response({"error": "Invalid trade ID."}, 400)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _contract_values_api_response({"error": "Provide valid contract details."}, 400)
    if request.method == "POST":
        if set(payload) != {"name", "amount"}:
            return _contract_values_api_response({"error": "Provide only contract item name and amount."}, 400)
        action, contract_data = "create", payload
    else:
        if set(payload) != {"contractItems"}:
            return _contract_values_api_response({"error": "Provide only the contract items to save."}, 400)
        action, contract_data = "replace", payload["contractItems"]

    try:
        items = get_user_store().mutate_contract_items(
            trade_id,
            action,
            contract_data=contract_data,
        )
    except ContractMutationError as error:
        return _contract_values_api_response({"error": str(error)}, error.status)
    except Exception as error:
        app.logger.error("Contract item save failed (%s).", type(error).__name__)
        return _contract_values_api_response({"error": "Unable to save contract values. Please try again."}, 503)
    if items is None:
        return _contract_values_api_response({"error": "Trade not found."}, 404)
    return _contract_values_api_response({"contractItems": items}, 201 if action == "create" else 200)


@app.route("/api/trades/<trade_id>/contract-items/<contract_id>", methods=["PATCH", "DELETE"])
def trade_contract_item_api(trade_id, contract_id):
    _user, denied = _contract_values_api_authorize()
    if denied:
        return denied
    if (
        not trade_id
        or "/" in trade_id
        or len(trade_id.encode("utf-8")) > 1500
        or not contract_id
        or "/" in contract_id
        or len(contract_id) > 200
    ):
        return _contract_values_api_response({"error": "Invalid trade or contract item ID."}, 400)
    if request.method == "PATCH":
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {"name", "amount"}:
            return _contract_values_api_response({"error": "Provide only contract item name and amount."}, 400)
        action, contract_data = "edit", payload
    else:
        action, contract_data = "delete", None

    try:
        items = get_user_store().mutate_contract_items(
            trade_id,
            action,
            contract_id=contract_id,
            contract_data=contract_data,
        )
    except ContractMutationError as error:
        return _contract_values_api_response({"error": str(error)}, error.status)
    except Exception as error:
        app.logger.error("Contract item mutation failed (%s).", type(error).__name__)
        return _contract_values_api_response({"error": "Unable to update contract values. Please try again."}, 503)
    if items is None:
        return _contract_values_api_response({"error": "Trade not found."}, 404)
    return _contract_values_api_response({"contractItems": items})


@app.route("/api/owner-scope", methods=["GET"])
def owner_scope_data_api():
    user = get_current_user()
    if not user:
        return jsonify(error="Authentication required."), 401
    if user.get("must_change_password"):
        return jsonify(error="Change your password before continuing."), 403
    try:
        permitted = _user_has_compiled_permission(user, "owner_scope.read")
    except Exception as error:
        app.logger.error("Owner Scope read authorization failed (%s).", type(error).__name__)
        return jsonify(error="Unable to verify Owner Scope permissions."), 503
    if not permitted:
        return jsonify(error="You do not have permission to read Owner Scope data."), 403
    try:
        data = get_owner_scope_data(get_user_store().db)
        response = jsonify(data)
        response.headers["Cache-Control"] = "no-store"
        return response
    except Exception:
        app.logger.exception("Owner Scope data could not be loaded.")
        return jsonify(error="Owner Scope data is temporarily unavailable."), 503


@app.route("/api/owner-scope/items", methods=["POST"])
def owner_scope_item_create_api():
    _user, error_response, status = _owner_scope_api_authorize("owner_scope.create")
    if error_response:
        return error_response, status
    payload = request.get_json(silent=True)
    request_id = request.headers.get("Idempotency-Key", "")
    try:
        parsed_request_id = uuid.UUID(request_id)
    except (AttributeError, ValueError):
        return jsonify(error="A valid Owner Scope request ID is required."), 400
    if str(parsed_request_id) != request_id.lower():
        return jsonify(error="A valid Owner Scope request ID is required."), 400
    item_id = str(parsed_request_id)
    payment_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"owner-scope-payment:{item_id}"))
    try:
        database = get_user_store().db
        item_reference = (
            database.collection("owner_scopes")
            .document("owner-scope")
            .collection("items")
            .document(item_id)
        )
        already_exists = item_reference.get().exists
        create_owner_scope_item(
            database,
            payload,
            item_id=item_id,
            payment_id=payment_id,
        )
    except OwnerScopeConflictError as exc:
        return jsonify(error=str(exc)), exc.status
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception as error:
        app.logger.error("Owner Scope item creation failed (%s).", type(error).__name__)
        return jsonify(error="Unable to save the Owner Scope item."), 503
    response = jsonify(id=item_id)
    response.headers["Cache-Control"] = "no-store"
    return response, 200 if already_exists else 201


@app.route("/api/owner-scope/items/<item_id>", methods=["PUT", "DELETE"])
def owner_scope_item_api(item_id):
    permission = "owner_scope.edit" if request.method == "PUT" else "owner_scope.delete"
    _user, error_response, status = _owner_scope_api_authorize(permission)
    if error_response:
        return error_response, status
    if not item_id or "/" in item_id or len(item_id.encode("utf-8")) > 1500:
        return jsonify(error="Invalid Owner Scope item ID."), 400

    try:
        if request.method == "PUT":
            payload = request.get_json(silent=True)
            if not update_owner_scope_item(get_user_store().db, item_id, payload):
                return jsonify(error="Owner Scope item not found."), 404
            return jsonify(id=item_id)

        result = delete_owner_scope_item(get_user_store().db, item_id)
    except OwnerScopeConflictError as exc:
        return jsonify(error=str(exc)), exc.status
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception as error:
        app.logger.error("Owner Scope item mutation failed (%s).", type(error).__name__)
        return jsonify(error="Unable to update or delete the Owner Scope item."), 503

    if result == "not_found":
        return jsonify(error="Owner Scope item not found."), 404
    return jsonify(deleted=True)


class PaymentMutationError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _payment_number(value, label, *, required=True):
    if value is None and not required:
        return 0.0
    if isinstance(value, bool):
        raise PaymentMutationError(f"{label} must be a valid number.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PaymentMutationError(f"{label} must be a valid number.") from None
    if not math.isfinite(number):
        raise PaymentMutationError(f"{label} must be a valid number.")
    return number


def _parse_payment_mutation_payload(payload, action):
    allowed_fields = {
        "type",
        "date",
        "amount",
        "labourEarned",
        "advanceAdjustmentMode",
        "requestedAdjustment",
        "paymentMode",
        "remarks",
        "workerCategoryId",
    }
    if not isinstance(payload, dict) or set(payload) - allowed_fields:
        raise PaymentMutationError("Provide valid payment details.")
    for field in ("type", "date", "amount", "paymentMode"):
        if field not in payload:
            raise PaymentMutationError("Some required payment details are missing.")

    payment_type = payload.get("type")
    if not isinstance(payment_type, str) or payment_type not in {
        "mobilization_advance",
        "labour_advance",
        "labour_settlement",
        "lump_sum_payment",
    }:
        raise PaymentMutationError("Select a valid payment type.")

    payment_date = payload.get("date")
    if not isinstance(payment_date, str):
        raise PaymentMutationError("Enter a valid payment date.")
    try:
        if datetime.strptime(payment_date, "%Y-%m-%d").strftime("%Y-%m-%d") != payment_date:
            raise ValueError
    except ValueError:
        raise PaymentMutationError("Enter a valid payment date.") from None

    payment_mode = payload.get("paymentMode")
    if not isinstance(payment_mode, str) or payment_mode not in {"Cash", "Bank Transfer", "UPI", "Cheque", "Other"}:
        raise PaymentMutationError("Select a valid payment mode.")

    remarks = payload.get("remarks", "")
    if not isinstance(remarks, str) or len(remarks) > 5000:
        raise PaymentMutationError("Remarks must be text within the allowed length.")

    amount = _payment_number(payload.get("amount"), "Payment amount")
    if amount < 0 or (payment_type != "labour_settlement" and amount <= 0):
        raise PaymentMutationError("Enter a valid payment amount greater than zero.")

    labour_earned = _payment_number(
        payload.get("labourEarned", 0),
        "Labour earned",
        required=False,
    )
    if labour_earned < 0:
        raise PaymentMutationError("Labour earned cannot be negative.")

    adjustment_mode = payload.get("advanceAdjustmentMode", "none")
    if not isinstance(adjustment_mode, str) or adjustment_mode not in {"none", "partial", "full"}:
        raise PaymentMutationError("Select a valid advance adjustment.")
    requested_adjustment = _payment_number(
        payload.get("requestedAdjustment", 0),
        "Advance adjustment",
        required=False,
    )
    if requested_adjustment < 0:
        raise PaymentMutationError("Advance adjustment cannot be negative.")

    category_id = payload.get("workerCategoryId", "")
    if not isinstance(category_id, str) or len(category_id) > 200:
        raise PaymentMutationError("Select a valid worker category.")
    if payment_type == "lump_sum_payment" and not category_id:
        raise PaymentMutationError("Select a lump-sum worker category.")

    return {
        "type": payment_type,
        "date": payment_date,
        "amount": amount,
        "labourEarned": labour_earned,
        "advanceAdjustmentMode": adjustment_mode,
        "requestedAdjustment": requested_adjustment,
        "paymentMode": payment_mode,
        "remarks": remarks.strip(),
        "workerCategoryId": category_id,
    }


def _finite_stored_payment_number(value, default=0.0):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise PaymentMutationError("Stored payment data is invalid.", 409)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PaymentMutationError("Stored payment data is invalid.", 409) from None
    if not math.isfinite(number):
        raise PaymentMutationError("Stored payment data is invalid.", 409)
    return number


def _payment_advance_balance(transactions):
    balance = 0.0
    for transaction in transactions:
        if not isinstance(transaction, dict):
            raise PaymentMutationError("Stored payment data is invalid.", 409)
        if transaction.get("type") == "labour_advance":
            balance += _finite_stored_payment_number(transaction.get("amount"))
        elif transaction.get("type") == "labour_settlement":
            balance -= _finite_stored_payment_number(transaction.get("advanceAdjustment"))
    return max(0.0, balance)


def _payment_outstanding_balance(transactions):
    running_outstanding = 0.0
    for transaction in sorted(transactions, key=lambda item: str(item.get("date") or "")):
        if transaction.get("type") != "labour_settlement":
            continue
        stored_outstanding = transaction.get("outstandingBalance")
        if stored_outstanding not in (None, ""):
            try:
                running_outstanding = _finite_stored_payment_number(stored_outstanding)
                continue
            except PaymentMutationError:
                pass
        earned = _finite_stored_payment_number(transaction.get("labourEarned"))
        adjustment = _finite_stored_payment_number(transaction.get("advanceAdjustment"))
        paid = _finite_stored_payment_number(transaction.get("amount"))
        running_outstanding = max(0.0, running_outstanding + earned - adjustment - paid)
    return running_outstanding


def _apply_payment_transaction_mutation(trade, action, transaction_id, payment_data):
    if not isinstance(trade, dict):
        raise PaymentMutationError("Trade not found.", 404)
    transactions = trade.get("paymentTransactions", [])
    if not isinstance(transactions, list) or any(not isinstance(item, dict) for item in transactions):
        raise PaymentMutationError("Stored payment data is invalid.", 409)
    updated_transactions = list(transactions)
    existing_index = next(
        (index for index, item in enumerate(updated_transactions) if item.get("id") == transaction_id),
        None,
    ) if transaction_id else None

    if action == "create" and existing_index is not None:
        existing_payment = updated_transactions[existing_index]
        _candidate_trade, _candidate_transactions, candidate_payment = _apply_payment_transaction_mutation(
            trade,
            "edit",
            transaction_id,
            payment_data,
        )
        compared_fields = (
            "id",
            "type",
            "date",
            "amount",
            "labourEarned",
            "advanceAdjustment",
            "outstandingBalance",
            "paymentMode",
            "remarks",
            "workerCategoryId",
            "workerCategoryName",
        )
        if all(existing_payment.get(field) == candidate_payment.get(field) for field in compared_fields):
            return dict(trade), updated_transactions, existing_payment
        raise PaymentMutationError("This payment request was already used for different details.", 409)

    if action == "delete":
        if existing_index is None:
            raise PaymentMutationError("Payment transaction not found.", 404)
        removed = updated_transactions.pop(existing_index)
        updated_trade = dict(trade)
        updated_trade["paymentTransactions"] = updated_transactions
        return updated_trade, updated_transactions, removed

    if action == "edit":
        if existing_index is None:
            raise PaymentMutationError("Payment transaction not found.", 404)
        prior = updated_transactions[existing_index]
        base_transactions = updated_transactions[:existing_index] + updated_transactions[existing_index + 1:]
    else:
        prior = None
        base_transactions = updated_transactions

    payment_type = payment_data["type"]
    amount = payment_data["amount"]
    labour_earned = payment_data["labourEarned"] if payment_type == "labour_settlement" else 0.0
    advance_adjustment = 0.0
    outstanding_balance = 0.0
    category = None

    if payment_type == "labour_settlement":
        advance_balance = _payment_advance_balance(base_transactions)
        adjustment_mode = payment_data["advanceAdjustmentMode"]
        if adjustment_mode == "full":
            advance_adjustment = min(advance_balance, labour_earned)
        elif adjustment_mode == "partial":
            advance_adjustment = min(
                advance_balance,
                labour_earned,
                payment_data["requestedAdjustment"],
            )
        previous_outstanding = _payment_outstanding_balance(base_transactions)
        maximum_amount = max(0.0, previous_outstanding + max(0.0, labour_earned - advance_adjustment))
        if amount > maximum_amount:
            raise PaymentMutationError("Amount paid now cannot exceed the current outstanding labour balance.")
        outstanding_balance = max(
            0.0,
            previous_outstanding + labour_earned - advance_adjustment - amount,
        )

    if payment_type == "lump_sum_payment":
        categories = trade.get("workerCategories")
        if not isinstance(categories, list):
            categories = trade.get("workers")
        if not isinstance(categories, list):
            categories = []
        category_id = payment_data["workerCategoryId"]
        category = next(
            (
                worker for worker in categories
                if isinstance(worker, dict)
                and worker.get("payment_type") == "lump_sum"
                and (
                    worker.get("id") == category_id
                    or (not worker.get("id") and category_id == f"legacy:{worker.get('name', '')}")
                )
            ),
            None,
        )
        if not category:
            raise PaymentMutationError("The selected lump-sum agreement no longer exists.")
        agreement_amount = _finite_stored_payment_number(category.get("lump_sum_amount"))
        paid_for_category = sum(
            _finite_stored_payment_number(item.get("amount"))
            for item in base_transactions
            if item.get("type") == "lump_sum_payment"
            and (
                (category.get("id") and item.get("workerCategoryId") == category.get("id"))
                or item.get("workerCategoryName") == category.get("name")
            )
        )
        if amount > max(0.0, agreement_amount - paid_for_category):
            raise PaymentMutationError("Payment exceeds the remaining lump-sum balance.")

    now = iso_now()
    updated_payment = {
        **(prior or {}),
        "id": prior.get("id") if prior else transaction_id,
        "type": payment_type,
        "date": payment_data["date"],
        "amount": amount,
        "labourEarned": labour_earned,
        "advanceAdjustment": advance_adjustment,
        "outstandingBalance": outstanding_balance,
        "paymentMode": payment_data["paymentMode"],
        "remarks": payment_data["remarks"],
        "createdAt": (prior or {}).get("createdAt") or now,
        "updatedAt": now,
    }
    if category is not None:
        updated_payment["workerCategoryId"] = category.get("id") or ""
        updated_payment["workerCategoryName"] = category.get("name") or ""

    if action == "edit":
        updated_transactions[existing_index] = updated_payment
    else:
        if any(item.get("id") == transaction_id for item in updated_transactions):
            raise PaymentMutationError("Unable to create a unique payment transaction.", 409)
        updated_transactions.append(updated_payment)

    updated_trade = dict(trade)
    updated_trade["paymentTransactions"] = updated_transactions
    return updated_trade, updated_transactions, updated_payment


def _user_has_compiled_permission(user, permission):
    store = get_user_store()
    role_definition = _custom_role_definition_for_user(user, store)
    return permission in compile_effective_permissions(user, role_definition)


def _labour_payment_api_response(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _labour_payment_api_authorize(permission):
    user = get_current_user()
    if not user:
        return None, _labour_payment_api_response({"error": "Authentication required."}, 401)
    if user.get("must_change_password"):
        return None, _labour_payment_api_response({"error": "Change your password before continuing."}, 403)
    try:
        permitted = _user_has_compiled_permission(user, permission)
    except Exception as error:
        app.logger.error("Labour payment authorization failed (%s).", type(error).__name__)
        return None, _labour_payment_api_response({"error": "Unable to verify payment permissions."}, 503)
    if not permitted:
        return None, _labour_payment_api_response({"error": "You do not have permission to perform this payment operation."}, 403)
    expected = session.get("labour_payments_csrf", "")
    supplied = request.headers.get("X-CSRF-Token", "")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        return None, _labour_payment_api_response({"error": "Your session expired. Reload the page and try again."}, 403)
    return user, None


@app.route("/api/labour-payments/<trade_id>/transactions", methods=["POST"])
def labour_payment_create_api(trade_id):
    user, denied = _labour_payment_api_authorize("labour_payments.create")
    if denied:
        return denied
    if not trade_id or "/" in trade_id or len(trade_id.encode("utf-8")) > 1500:
        return _labour_payment_api_response({"error": "Invalid trade."}, 400)
    try:
        payment_data = _parse_payment_mutation_payload(request.get_json(silent=True), "create")
        request_id = request.headers.get("Idempotency-Key", "")
        if request_id:
            try:
                parsed_request_id = uuid.UUID(request_id)
            except (AttributeError, ValueError):
                return _labour_payment_api_response({"error": "Invalid payment request identifier."}, 400)
            if str(parsed_request_id) != request_id.lower():
                return _labour_payment_api_response({"error": "Invalid payment request identifier."}, 400)
            transaction_id = str(parsed_request_id)
        else:
            transaction_id = str(uuid.uuid4())
        result = get_user_store().mutate_payment_transaction(
            trade_id,
            "create",
            transaction_id,
            payment_data,
        )
    except PaymentMutationError as error:
        return _labour_payment_api_response({"error": str(error)}, error.status)
    except Exception as error:
        app.logger.error("Labour payment creation failed (%s).", type(error).__name__)
        return _labour_payment_api_response({"error": "Unable to save the payment. Please try again."}, 503)
    if result is None:
        return _labour_payment_api_response({"error": "Trade not found."}, 404)
    return _labour_payment_api_response(result, 201)


@app.route("/api/labour-payments/<trade_id>/transactions/<transaction_id>", methods=["PATCH", "DELETE"])
def labour_payment_transaction_api(trade_id, transaction_id):
    permission = "labour_payments.edit" if request.method == "PATCH" else "labour_payments.delete"
    _user, denied = _labour_payment_api_authorize(permission)
    if denied:
        return denied
    if (
        not trade_id
        or "/" in trade_id
        or len(trade_id.encode("utf-8")) > 1500
        or not transaction_id
        or "/" in transaction_id
        or len(transaction_id) > 200
    ):
        return _labour_payment_api_response({"error": "Invalid trade or payment transaction."}, 400)
    try:
        payment_data = (
            _parse_payment_mutation_payload(request.get_json(silent=True), "edit")
            if request.method == "PATCH"
            else {}
        )
        result = get_user_store().mutate_payment_transaction(
            trade_id,
            "edit" if request.method == "PATCH" else "delete",
            transaction_id,
            payment_data,
        )
    except PaymentMutationError as error:
        return _labour_payment_api_response({"error": str(error)}, error.status)
    except Exception as error:
        app.logger.error("Labour payment mutation failed (%s).", type(error).__name__)
        return _labour_payment_api_response({"error": "Unable to update the payment. Please try again."}, 503)
    if result is None:
        return _labour_payment_api_response({"error": "Trade not found."}, 404)
    return _labour_payment_api_response(result)


def _normalize_daily_report_date(date_value):
    candidate = (date_value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
        return None
    try:
        datetime.strptime(candidate, "%Y-%m-%d")
        return candidate
    except ValueError:
        return None


def _format_daily_report_date_for_display(date_value):
    normalized = _normalize_daily_report_date(date_value)
    if not normalized:
        return ""
    try:
        parsed = datetime.strptime(normalized, "%Y-%m-%d")
        return parsed.strftime("%d/%m/%Y")
    except ValueError:
        return ""


def _sanitize_daily_report_for_view(record_data):
    if not isinstance(record_data, dict):
        return None

    allowed_keys = {
        "date",
        "floor",
        "activity",
        "tradeId",
        "tradeName",
        "workerCounts",
        "masonCount",
        "manMazdoorCount",
        "womanMazdoorCount",
        "totalWorkers",
        "weatherCondition",
        "weatherDescription",
        "materials",
        "notes",
        "evidence",
        "createdByName",
        "createdByUsername",
        "createdAt",
        "updatedAt",
        "last_edited_by_name",
        "last_edited_by_username",
        "last_edited_at",
    }
    display_data = {
        key: value
        for key, value in record_data.items()
        if key in allowed_keys
    }
    if not display_data.get("date"):
        return None

    display_data["displayDate"] = _format_daily_report_date_for_display(display_data["date"])
    created_by_user_id = record_data.get("createdByUserId")
    if isinstance(created_by_user_id, str) and created_by_user_id:
        user_profile = get_user_store().get_user(created_by_user_id)
        avatar_id = user_profile.get("avatarId") if user_profile else None
        display_data["createdByAvatarId"] = (
            avatar_id if isinstance(avatar_id, str) and avatar_id in PROFILE_AVATAR_IDS else DEFAULT_PROFILE_AVATAR_ID
        )
    else:
        display_data["createdByAvatarId"] = DEFAULT_PROFILE_AVATAR_ID
    display_data["createdByDisplayName"] = (
        str(record_data.get("createdByName") or record_data.get("createdByUsername") or "Unknown").strip()
        or "Unknown"
    )
    if not display_data.get("createdByDisplayName"):
        display_data["createdByDisplayName"] = "Unknown"
    if "evidence" in display_data and not isinstance(display_data["evidence"], list):
        display_data["evidence"] = []
    if "workerCounts" in display_data and not isinstance(display_data["workerCounts"], dict):
        display_data["workerCounts"] = {}
    return display_data


def _get_daily_record_for_date(date_value):
    normalized_date = _normalize_daily_report_date(date_value)
    if not normalized_date:
        return None

    store = get_user_store()
    if hasattr(store, "daily_records"):
        for record in store.daily_records.values():
            if isinstance(record, dict) and record.get("date") == normalized_date:
                return record
        return None

    try:
        docs = get_daily_records_collection().where("date", "==", normalized_date).limit(1).stream()
    except RuntimeError:
        return None
    for doc in docs:
        record = doc.to_dict()
        if isinstance(record, dict):
            return record
    return None


def _build_daily_report_share_image_svg(date_value, report=None):
    normalized_date = _normalize_daily_report_date(date_value) or date_value
    display_date = _format_daily_report_date_for_display(normalized_date) or normalized_date
    activity = ""
    if isinstance(report, dict):
        activity = str(report.get("activity") or report.get("tradeName") or "Construction Progress").strip()
    if not activity:
        activity = "Construction Progress"
    safe_date = escape(display_date)
    safe_activity = escape(activity)
    safe_brand = escape("MANNAT MOON")
    svg = f"""<svg xmlns='http://www.w3.org/2000/svg' width='1200' height='630' viewBox='0 0 1200 630'>
      <defs>
        <linearGradient id='mm-bg' x1='0' x2='1'>
          <stop offset='0%' stop-color='#0f172a'/>
          <stop offset='100%' stop-color='#1e293b'/>
        </linearGradient>
      </defs>
      <rect width='1200' height='630' fill='url(#mm-bg)'/>
      <rect x='60' y='60' width='1080' height='510' rx='28' fill='rgba(15,23,42,0.72)' stroke='#facc15' stroke-width='2'/>
      <circle cx='150' cy='155' r='50' fill='#facc15' opacity='0.18'/>
      <circle cx='1030' cy='135' r='60' fill='#22c55e' opacity='0.12'/>
      <text x='90' y='160' fill='#f8fafc' font-family='Arial, Helvetica, sans-serif' font-size='48' font-weight='700' letter-spacing='3'>{safe_brand}</text>
      <text x='90' y='240' fill='#facc15' font-family='Arial, Helvetica, sans-serif' font-size='28' font-weight='600' letter-spacing='2'>DAILY REPORT</text>
      <text x='90' y='320' fill='#f8fafc' font-family='Arial, Helvetica, sans-serif' font-size='70' font-weight='700'>{safe_date}</text>
      <text x='90' y='390' fill='#cbd5e1' font-family='Arial, Helvetica, sans-serif' font-size='34' font-weight='500'>{safe_activity}</text>
      <text x='90' y='500' fill='#94a3b8' font-family='Arial, Helvetica, sans-serif' font-size='24'>Private-LAN daily report viewer</text>
    </svg>"""
    return Response(svg, mimetype="image/svg+xml")


GOOGLE_PHOTOS_PAGE_HOSTS = frozenset({"photos.google.com", "photos.app.goo.gl"})
GOOGLE_PHOTOS_BROWSER_ASSET_HOSTS = frozenset({
    "photos.google.com",
    "photos.app.goo.gl",
    "www.gstatic.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
})
GOOGLE_PHOTOS_MAX_HTML_BYTES = 2 * 1024 * 1024
GOOGLE_IMAGE_MAX_BYTES = 8 * 1024 * 1024
GOOGLE_PHOTOS_MAX_REDIRECTS = 5
GOOGLE_PHOTOS_TIMEOUT = (5, 10)
GOOGLE_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})


def _validate_public_google_host(hostname):
    try:
        addresses = {
            ipaddress.ip_address(result[4][0])
            for result in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as error:
        raise ValueError("The Google Photos host could not be resolved.") from error
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("The Google Photos URL must resolve to a public address.")


def _validate_google_photos_url(url, *, image=False):
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise ValueError("Enter a valid Google Photos URL.")
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as error:
        raise ValueError("Enter a valid Google Photos URL.") from error
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or parsed.fragment
    ):
        raise ValueError("Google Photos URLs must use HTTPS and must not include credentials or fragments.")

    if image:
        if not re.fullmatch(r"lh\d+\.googleusercontent\.com", hostname) or not parsed.path.startswith("/pw/"):
            raise ValueError("Only Google Photos image URLs are accepted.")
    elif (
        hostname not in GOOGLE_PHOTOS_PAGE_HOSTS
        or (
            hostname == "photos.google.com"
            and not (parsed.path.startswith("/share/") or "/photo/" in parsed.path)
        )
        or (hostname == "photos.app.goo.gl" and not parsed.path.strip("/"))
    ):
        raise ValueError("Enter a Google Photos sharing URL.")

    _validate_public_google_host(hostname)
    return parsed


def _fetch_google_photos_page(url):
    current_url = url
    headers = {"User-Agent": "MannatMoonEvidencePreview/1.0"}
    for redirect_number in range(GOOGLE_PHOTOS_MAX_REDIRECTS + 1):
        _validate_google_photos_url(current_url)
        response = requests.get(
            current_url,
            headers=headers,
            timeout=GOOGLE_PHOTOS_TIMEOUT,
            allow_redirects=False,
            stream=True,
        )
        try:
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                if not location or redirect_number == GOOGLE_PHOTOS_MAX_REDIRECTS:
                    raise ValueError("Google Photos returned too many or invalid redirects.")
                current_url = requests.compat.urljoin(current_url, location)
                continue
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                raise ValueError("Google Photos did not return an HTML sharing page.")
            if int(response.headers.get("Content-Length") or 0) > GOOGLE_PHOTOS_MAX_HTML_BYTES:
                raise ValueError("The Google Photos page is too large to process.")
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > GOOGLE_PHOTOS_MAX_HTML_BYTES:
                    raise ValueError("The Google Photos page is too large to process.")
            return bytes(content).decode(response.encoding or "utf-8", errors="replace")
        finally:
            response.close()
    raise ValueError("Google Photos returned too many redirects.")


def _fetch_google_image(url):
    current_url = url
    for redirect_number in range(GOOGLE_PHOTOS_MAX_REDIRECTS + 1):
        _validate_google_photos_url(current_url, image=True)
        response = requests.get(
            current_url,
            headers={"User-Agent": "MannatMoonEvidencePreview/1.0"},
            timeout=GOOGLE_PHOTOS_TIMEOUT,
            allow_redirects=False,
            stream=True,
        )
        try:
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                if not location or redirect_number == GOOGLE_PHOTOS_MAX_REDIRECTS:
                    raise ValueError("Google Photos returned too many or invalid image redirects.")
                current_url = requests.compat.urljoin(current_url, location)
                continue
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in GOOGLE_IMAGE_TYPES:
                raise ValueError("Google Photos returned an unsupported image type.")
            if int(response.headers.get("Content-Length") or 0) > GOOGLE_IMAGE_MAX_BYTES:
                raise ValueError("The Google Photos image is too large to display.")
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > GOOGLE_IMAGE_MAX_BYTES:
                    raise ValueError("The Google Photos image is too large to display.")
            return bytes(content), content_type
        finally:
            response.close()
    raise ValueError("Google Photos returned too many image redirects.")


def _is_allowed_photos_browser_url(url):
    if url.startswith(("data:", "blob:", "about:")):
        return True
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            return False
        if hostname in GOOGLE_PHOTOS_PAGE_HOSTS or hostname in GOOGLE_PHOTOS_BROWSER_ASSET_HOSTS:
            _validate_public_google_host(hostname)
            return True
        if re.fullmatch(r"lh\d+\.googleusercontent\.com", hostname) and parsed.path.startswith("/pw/"):
            _validate_public_google_host(hostname)
            return True
    except (ValueError, OSError):
        return False
    return False


def _extract_google_photos_image(url):
    try:
        page_html = _fetch_google_photos_page(url)
        matches = re.findall(
            r"https://lh\d+\.googleusercontent\.com/pw/[A-Za-z0-9_-]+",
            page_html,
        )
        if matches:
            direct_url = matches[0] + "=s0"
            _validate_google_photos_url(direct_url.split("=", 1)[0], image=True)
            return direct_url
    except (requests.RequestException, ValueError, OSError):
        pass

    browser = None
    page = None
    try:
        browser = browser_pool.get_browser()
        if not browser:
            raise RuntimeError("Google Photos preview service is busy.")
        page = browser.new_page()
        page.route(
            "**/*",
            lambda route: route.continue_()
            if _is_allowed_photos_browser_url(route.request.url)
            else route.abort(),
        )
        captured = []

        def response_handler(response):
            response_url = response.url
            image_url = response_url.split("=", 1)[0]
            try:
                _validate_google_photos_url(image_url, image=True)
            except (ValueError, OSError):
                return
            captured.append(image_url)

        page.on("response", response_handler)
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        return f"{captured[-1]}=s0" if captured else None
    except Exception as error:
        app.logger.warning("Google Photos preview extraction failed (%s).", type(error).__name__)
        return None
    finally:
        try:
            if page:
                page.close()
        finally:
            if browser:
                browser_pool.return_browser(browser)


def _google_photos_permission_denial(permission):
    user = get_current_user()
    if user and user.get("must_change_password"):
        return Response("Change your password before continuing.", status=403, mimetype="text/plain")
    try:
        if user and _user_has_compiled_permission(user, permission):
            return None
    except Exception as error:
        app.logger.error("Google Photos authorization failed (%s).", type(error).__name__)
        return Response("Unable to verify Google Photos permissions.", status=503, mimetype="text/plain")
    return Response("You do not have permission to use this Google Photos feature.", status=403, mimetype="text/plain")


@app.route("/daily-reports/share-image")
def daily_report_share_image():
    _current_user, denied = _daily_record_api_authorize("daily_reports.read")
    if denied:
        return denied

    date_value = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
    normalized_date = _normalize_daily_report_date(date_value)
    if not normalized_date:
        return Response("A valid date in YYYY-MM-DD format is required.", status=400, mimetype="text/plain")

    report = _sanitize_daily_report_for_view(_get_daily_record_for_date(normalized_date))
    response = _build_daily_report_share_image_svg(normalized_date, report)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.route("/daily-reports")
def daily_reports():
    current_user, denied = _daily_record_api_authorize("daily_reports.read")
    if denied:
        return denied

    csrf_token = session.get("daily_reports_csrf")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        session["daily_reports_csrf"] = csrf_token
    response = app.make_response(
        render_template(
            "daily_reports.html",
            current_user=current_user,
            daily_reports_csrf=csrf_token,
        )
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.route("/user-guide")
@require_login
def user_guide():
    return render_template("user_guide.html", current_user=get_current_user())


@app.route("/developer-guide")
@require_login
@require_role("super_admin")
def developer_guide():
    return render_template("developer_guide.html", current_user=get_current_user())


DAILY_RECORD_EDITABLE_FIELDS = {
    "date",
    "floor",
    "activity",
    "tradeId",
    "tradeName",
    "workerCounts",
    "masonCount",
    "manMazdoorCount",
    "womanMazdoorCount",
    "totalWorkers",
    "weatherCondition",
    "weatherDescription",
    "materials",
    "notes",
    "evidence",
}


def get_daily_records_collection():
    store = get_user_store()
    database = getattr(store, "db", None)
    if database is None:
        raise RuntimeError("Daily Reports require Firestore.")
    return database.collection("daily_records")


def _daily_record_api_authorize(permission):
    current_user = get_current_user()
    if not current_user:
        return None, (jsonify(error="Authentication required."), 401)
    if current_user.get("must_change_password"):
        return None, (jsonify(error="Change your password before continuing."), 403)

    try:
        permitted = _user_has_compiled_permission(current_user, permission)
    except Exception as error:
        app.logger.error("Daily Report API authorization failed (%s).", type(error).__name__)
        return None, (jsonify(error="Unable to verify Daily Reports permissions."), 503)
    if not permitted:
        return None, (jsonify(error="You do not have permission to access Daily Reports."), 403)
    return current_user, None


def _daily_record_api_csrf_valid():
    expected = session.get("daily_reports_csrf", "")
    supplied = request.headers.get("X-CSRF-Token", "")
    return bool(expected) and bool(supplied) and hmac.compare_digest(expected, supplied)


@app.route("/api/daily-records/dates", methods=["GET"])
def get_daily_record_dates():
    _current_user, denied = _daily_record_api_authorize("daily_reports.read")
    if denied:
        return denied

    start_date = request.args.get("start", "")
    end_date = request.args.get("end", "")
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_date) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_date):
            raise ValueError
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")
        if start_date > end_date:
            raise ValueError
    except ValueError:
        return jsonify(error="A valid start and end date are required."), 400

    try:
        records = get_daily_records_collection().where("date", ">=", start_date).where("date", "<=", end_date).stream()
        dates = {
            data.get("date")
            for record in records
            if isinstance((data := record.to_dict()), dict)
            and isinstance(data.get("date"), str)
            and start_date <= data["date"] <= end_date
        }
    except RuntimeError:
        app.logger.exception("Unable to load Daily Reports dates")
        return jsonify(error="Daily Reports are temporarily unavailable. Please contact your administrator."), 503

    return jsonify(dates=sorted(dates))


@app.route("/api/daily-records/author-avatars", methods=["GET"])
def get_daily_record_author_avatars():
    _current_user, denied = _daily_record_api_authorize("daily_reports.read")
    if denied:
        return denied

    user_ids = request.args.getlist("user_id")
    usernames = request.args.getlist("username")
    if (
        len(user_ids) + len(usernames) > 100
        or any(not value or len(value) > 128 for value in user_ids)
        or any(not value or len(value) > 64 for value in usernames)
    ):
        return jsonify(error="A valid list of Daily Reports authors is required."), 400

    try:
        store = get_user_store()
        avatars_by_user_id = {}
        avatars_by_username = {}
        for user_id in set(user_ids):
            user = store.get_user(user_id)
            avatar_id = user.get("avatarId") if user else None
            avatars_by_user_id[user_id] = (
                avatar_id if isinstance(avatar_id, str) and avatar_id in PROFILE_AVATAR_IDS
                else DEFAULT_PROFILE_AVATAR_ID
            )
        for username in set(usernames):
            normalized_username = username.strip().lower()
            user = store.get_user_by_username(normalized_username)
            avatar_id = user.get("avatarId") if user else None
            avatars_by_username[normalized_username] = (
                avatar_id if isinstance(avatar_id, str) and avatar_id in PROFILE_AVATAR_IDS
                else DEFAULT_PROFILE_AVATAR_ID
            )
    except Exception:
        app.logger.exception("Unable to load Daily Reports author avatars")
        return jsonify(error="Daily Reports avatars are temporarily unavailable."), 503

    return jsonify(user_ids=avatars_by_user_id, usernames=avatars_by_username)


@app.route("/api/daily-records/trades", methods=["GET"])
def get_daily_report_trade_options():
    _current_user, denied = _daily_record_api_authorize("daily_reports.read")
    if denied:
        return denied

    try:
        trade_options = []
        for trade in get_user_store().list_trades():
            if not isinstance(trade, dict):
                continue
            categories = trade.get("workerCategories")
            if not isinstance(categories, list):
                categories = trade.get("workers")
            if not isinstance(categories, list):
                categories = []
            trade_options.append({
                "id": trade.get("id"),
                "name": trade.get("name") if isinstance(trade.get("name"), str) else "",
                "workerCategories": [
                    {"name": category["name"]}
                    for category in categories
                    if isinstance(category, dict)
                    and isinstance(category.get("name"), str)
                    and category["name"].strip()
                ],
            })
    except Exception:
        app.logger.exception("Unable to load Daily Reports trade options.")
        return jsonify(error="Daily Reports trade options are temporarily unavailable."), 503

    response = jsonify(trades=trade_options)
    response.headers["Cache-Control"] = "no-store"
    return response


def get_daily_record_payload():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None
    return {
        key: value
        for key, value in payload.items()
        if key in DAILY_RECORD_EDITABLE_FIELDS
    }


@app.route("/api/daily-records", methods=["POST"])
def create_daily_record():
    current_user, denied = _daily_record_api_authorize("daily_reports.create")
    if denied:
        return denied
    if not _daily_record_api_csrf_valid():
        return jsonify(error="Your session expired. Reload the page and try again."), 403

    record_data = get_daily_record_payload()
    if not record_data:
        return jsonify(error="Daily report data is required."), 400

    record_data["createdByName"] = (
        str(current_user.get("full_name") or "").strip()
        or str(current_user.get("username") or "").strip()
        or "Unknown"
    )
    record_data["createdByUsername"] = str(current_user.get("username") or "").strip()
    record_data["createdByUserId"] = current_user["id"]
    record_data["createdAt"] = iso_now()
    record_data["updatedAt"] = record_data["createdAt"]

    try:
        record_ref = get_daily_records_collection().document()
        record_ref.set(record_data)
    except RuntimeError:
        app.logger.exception("Unable to save a Daily Report")
        return jsonify(error="Unable to save the daily report. Please try again."), 503

    return jsonify(id=record_ref.id), 201


@app.route("/api/daily-records/<record_id>", methods=["PATCH"])
def update_daily_record(record_id):
    current_user, denied = _daily_record_api_authorize("daily_reports.edit")
    if denied:
        return denied
    if not _daily_record_api_csrf_valid():
        return jsonify(error="Your session expired. Reload the page and try again."), 403

    record_data = get_daily_record_payload()
    if not record_data:
        return jsonify(error="Daily report data is required."), 400

    try:
        record_ref = get_daily_records_collection().document(record_id)
        if not record_ref.get().exists:
            return jsonify(error="Daily report not found."), 404
        edited_at = iso_now()
        record_data["updatedAt"] = edited_at
        record_data["last_edited_by_name"] = (
            str(current_user.get("full_name") or "").strip()
            or str(current_user.get("username") or "").strip()
            or "Unknown"
        )
        record_data["last_edited_by_username"] = str(current_user.get("username") or "").strip()
        record_data["last_edited_by_user_id"] = current_user["id"]
        record_data["last_edited_at"] = edited_at
        record_ref.update(record_data)
    except RuntimeError:
        app.logger.exception("Unable to update a Daily Report")
        return jsonify(error="Unable to update the daily report. Please try again."), 503

    return jsonify(id=record_id), 200


@app.route("/api/daily-records/<path:record_id>", methods=["DELETE"])
def delete_daily_record(record_id):
    current_user = get_current_user()
    if not current_user:
        return jsonify(error="Authentication required."), 401
    if current_user.get("must_change_password"):
        return jsonify(error="Change your password before continuing."), 403

    try:
        permitted = _user_has_compiled_permission(current_user, "daily_reports.delete")
    except Exception as error:
        app.logger.error("Daily Report delete authorization failed (%s).", type(error).__name__)
        return jsonify(error="Unable to verify Daily Report permissions."), 503
    if not permitted:
        return jsonify(error="You do not have permission to delete Daily Reports."), 403

    if not _daily_record_api_csrf_valid():
        return jsonify(error="Your session expired. Reload the page and try again."), 403

    if (
        not record_id
        or "/" in record_id
        or record_id in {".", ".."}
        or len(record_id.encode("utf-8")) > 1500
        or any(ord(character) < 32 or ord(character) == 127 for character in record_id)
    ):
        return jsonify(error="Invalid Daily Report ID."), 400

    try:
        record_ref = get_daily_records_collection().document(record_id)
        if not record_ref.get().exists:
            return jsonify(error="Daily Report not found."), 404
        record_ref.delete()
    except Exception as error:
        app.logger.error("Daily Report deletion failed (%s).", type(error).__name__)
        return jsonify(error="Unable to delete the Daily Report. Please try again."), 503

    response = jsonify(id=record_id)
    response.headers["Cache-Control"] = "no-store"
    return response, 200


@app.route("/google-photos", methods=["GET", "POST"])
@require_authenticated_form_csrf
@require_login
def google_photos():
    denied = _google_photos_permission_denial("google_photos.use")
    if denied:
        return denied
    image = None
    error = None

    if request.method == "POST":
        url = request.form.get("url", "").strip()
        try:
            _validate_google_photos_url(url)
        except ValueError as validation_error:
            error = str(validation_error)
        else:
            image = get_cached_url(url)
            if not image:
                image = _extract_google_photos_image(url)
                if image:
                    cache_url(url, image)
                else:
                    error = "No Google Photos image was found. Check the sharing link and try again."

    return render_template("google_photos.html", image=image, error=error, current_user=get_current_user())


@app.route("/extract-google-photos-url")
@require_login
def extract_google_photos_url():
    denied = _google_photos_permission_denial("google_photos.use")
    if denied:
        return denied
    url = request.args.get("url", "")
    try:
        _validate_google_photos_url(url)
    except ValueError as error:
        return Response(str(error), status=400, mimetype="text/plain")

    cached = get_cached_url(url)
    if cached:
        return Response(cached, mimetype="text/plain", headers={"Cache-Control": "private, no-store"})
    direct_url = _extract_google_photos_image(url)
    if not direct_url:
        return Response("No Google Photos image found in the shared page.", status=502, mimetype="text/plain")
    cache_url(url, direct_url)
    return Response(direct_url, mimetype="text/plain", headers={"Cache-Control": "private, no-store"})


@app.route("/image")
@require_login
def image():
    denied = _google_photos_permission_denial("google_photos.use")
    if denied:
        return denied
    url = request.args.get("url", "")
    try:
        _validate_google_photos_url(url, image=True)
        content, content_type = _fetch_google_image(url)
    except ValueError as error:
        return Response(str(error), status=400, mimetype="text/plain")
    except requests.RequestException as error:
        app.logger.warning("Google Photos image fetch failed (%s).", type(error).__name__)
        return Response("Unable to load this Google Photos image.", status=502, mimetype="text/plain")
    response = send_file(BytesIO(content), mimetype=content_type, max_age=0)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"
    return response


from snapshot_routes import snapshot_blueprint

app.register_blueprint(snapshot_blueprint)


if __name__ == "__main__":
    if IS_PRODUCTION_ENVIRONMENT:
        raise SystemExit("Use the Waitress WSGI entry point in production.")
    try:
        lan_ip = "unavailable"
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as ip_socket:
                ip_socket.connect(("8.8.8.8", 80))
                lan_ip = ip_socket.getsockname()[0]
        except OSError:
            pass

        print("Local URL: http://127.0.0.1:5020")
        print(f"Network URL: http://{lan_ip}:5020")
        app.run(host="0.0.0.0", port=5020, debug=False)
    finally:
        browser_pool.close_all()