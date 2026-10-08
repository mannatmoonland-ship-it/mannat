import hmac
import csv
import json
import os
import re
import secrets
import subprocess
import threading
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for

from snapshot_service import (
    SNAPSHOT_PREFIX,
    SnapshotError,
    create_snapshot,
    is_snapshot_filename,
    preview_restore,
    restore_snapshot,
    verify_snapshot,
)


snapshot_blueprint = Blueprint("snapshots", __name__)
_restore_lock = threading.RLock()
_restoring_filenames = set()
_pending_restores = {}
_secured_paths = set()
_secure_path_lock = threading.Lock()


def _secure_local_path(path, is_directory=False):
    path = Path(path)
    resolved = str(path.resolve())
    with _secure_path_lock:
        if resolved in _secured_paths:
            return
        try:
            if os.name == "nt":
                identity = subprocess.run(
                    ["whoami", "/user", "/fo", "csv", "/nh"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                identity_rows = list(csv.reader(identity.stdout.splitlines()))
                sid = identity_rows[0][-1] if identity_rows else ""
                if not re.fullmatch(r"S-1-(?:\d+-)+\d+", sid):
                    raise OSError("The current Windows account SID could not be determined.")
                account = f"*{sid}"
                permission = "(OI)(CI)F" if is_directory else "(F)"
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:{permission}"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
            else:
                os.chmod(path, 0o700 if is_directory else 0o600)
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError("Private local Snapshot storage permissions could not be set.") from error
        _secured_paths.add(resolved)


def _private_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    _secure_local_path(path, is_directory=True)
    return path


def _backup_directory():
    directory = Path(current_app.root_path) / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _instance_directory():
    return Path(current_app.instance_path)


def _credentials_directory():
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return _private_directory(root / "MannatMoonConstruction" / "snapshot_credentials")


def _target_directory():
    directory = _instance_directory() / "restored_projects"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _restore_log_path():
    return _backup_directory() / "restore_history.jsonl"


def _audit_log_path():
    return _backup_directory() / "snapshot_audit.jsonl"


def _write_json_atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
    os.replace(temporary, path)


def _write_local_event(event, actor_id, project_id=None, filename=None, result=None):
    directory = _backup_directory()
    directory.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "actor_id": str(actor_id or ""),
        "project_id": str(project_id or ""),
        "filename": str(filename or ""),
        "result": str(result or ""),
    }
    with _audit_log_path().open("a", encoding="utf-8") as output:
        output.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")


def _write_restore_event(entry):
    directory = _backup_directory()
    directory.mkdir(parents=True, exist_ok=True)
    with _restore_log_path().open("a", encoding="utf-8") as output:
        output.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")


def _prune_pending_restores():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    with _restore_lock:
        for token, pending in list(_pending_restores.items()):
            if pending["created_at"] < cutoff and pending["filename"] not in _restoring_filenames:
                _pending_restores.pop(token, None)
                if not pending.get("retained"):
                    Path(pending["credential_path"]).unlink(missing_ok=True)


def _admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        from app import get_current_user, is_super_admin

        user = get_current_user()
        if not user:
            return redirect(url_for("login_page"))
        if not is_super_admin(user):
            if request.path.startswith("/api/"):
                return jsonify(error="Super Admin access is required."), 403
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped


def _csrf_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        expected = session.get("snapshot_csrf_token", "")
        supplied = request.headers.get("X-Snapshot-CSRF", "")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            return jsonify(error="Request validation failed. Reload the Snapshot page and retry."), 400
        return view(*args, **kwargs)
    return wrapped


def _login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        from app import get_current_user

        if not get_current_user():
            return redirect(url_for("login_page"))
        return view(*args, **kwargs)
    return wrapped


def _snapshot_path(filename):
    if not is_snapshot_filename(filename):
        return None
    directory = _backup_directory().resolve()
    candidate = (directory / filename).resolve()
    if candidate.parent != directory or not candidate.is_file():
        return None
    return candidate


def _web_config(value, project_id):
    allowed_keys = {
        "apiKey", "authDomain", "projectId", "storageBucket",
        "messagingSenderId", "appId", "measurementId",
    }
    if not isinstance(value, dict) or any(key not in allowed_keys for key in value):
        raise ValueError("Provide a Firebase Web App configuration JSON containing only public web configuration fields.")
    if value.get("projectId") != project_id:
        raise ValueError("The Firebase Web App configuration project ID does not match the destination project.")
    for key in ("apiKey", "authDomain", "projectId", "appId"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"Firebase Web App configuration is missing {key}.")
    return {key: item for key, item in value.items() if key in allowed_keys}


def _service_account_payload(upload):
    if upload is None:
        raise ValueError("Select the destination service-account JSON file.")
    raw = upload.read(2 * 1024 * 1024 + 1)
    if not raw or len(raw) > 2 * 1024 * 1024:
        raise ValueError("The service-account JSON file is empty or too large.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("The selected service-account file is not valid JSON.") from error
    if not isinstance(payload, dict) or payload.get("type") != "service_account":
        raise ValueError("The selected file is not a Google service-account JSON file.")
    project_id = payload.get("project_id")
    if not isinstance(project_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project_id):
        raise ValueError("The service-account file does not contain a valid Firebase project ID.")
    return raw, project_id


def _new_firestore_store(project_id, service_account_path, purpose):
    from app import FirebaseFirestoreStore

    return FirebaseFirestoreStore(
        project_id=project_id,
        service_account_path=str(service_account_path),
        app_name=f"snapshot-{purpose}-{uuid.uuid4().hex[:12]}",
    )


def _list_restorable_projects():
    projects = {}
    for path in _target_directory().glob("*.json") if _target_directory().is_dir() else []:
        try:
            descriptor = json.loads(path.read_text(encoding="utf-8"))
            if descriptor.get("verified") is True and descriptor.get("conflict_count") == 0:
                projects[descriptor["project_id"]] = {
                    "project_id": descriptor["project_id"],
                    "source_project_id": descriptor.get("source_project_id", ""),
                    "filename": descriptor.get("filename", ""),
                    "restored_at": descriptor.get("restored_at", ""),
                }
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(projects.values(), key=lambda item: item["restored_at"], reverse=True)


@snapshot_blueprint.route("/snapshots")
@_admin_required
def snapshot_page():
    from app import get_active_firebase_config

    csrf_token = session.get("snapshot_csrf_token")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        session["snapshot_csrf_token"] = csrf_token
    active_config = get_active_firebase_config()
    previous_path = _instance_directory() / "firebase_previous_project.json"
    previous_project_id = ""
    if previous_path.is_file():
        try:
            previous_project_id = json.loads(previous_path.read_text(encoding="utf-8")).get("project_id", "")
        except (OSError, ValueError, AttributeError):
            pass
    return render_template(
        "snapshots.html",
        csrf_token=csrf_token,
        active_project_id=active_config["project_id"],
        previous_project_id=previous_project_id,
        restorable_projects=_list_restorable_projects(),
    )


@snapshot_blueprint.route("/api/firebase-web-config")
@_login_required
def firebase_web_config():
    from app import get_active_firebase_config

    response = jsonify(get_active_firebase_config()["web_config"])
    response.headers["Cache-Control"] = "no-store"
    return response


@snapshot_blueprint.route("/api/snapshots", methods=["GET"])
@_admin_required
def snapshot_history():
    from app import get_active_firebase_config

    directory = _backup_directory()
    directory.mkdir(parents=True, exist_ok=True)
    snapshots = []
    for path in sorted(directory.glob(f"{SNAPSHOT_PREFIX}*.zip"), key=lambda item: item.stat().st_mtime, reverse=True):
        result = verify_snapshot(path)
        metadata = result.get("metadata") or {}
        snapshots.append({
            "filename": path.name,
            "project_id": metadata.get("project_id", ""),
            "created_at": metadata.get("created_at", ""),
            "size": path.stat().st_size,
            "collection_count": len(metadata.get("collections", [])),
            "document_count": metadata.get("document_count", 0),
            "verified": result["verified"],
            "verification_status": "Verified" if result["verified"] else "Verification Failed",
            "verification_errors": result["errors"][:2],
        })
    previous_path = _instance_directory() / "firebase_previous_project.json"
    previous_project_id = ""
    if previous_path.is_file():
        try:
            previous_project_id = json.loads(previous_path.read_text(encoding="utf-8")).get("project_id", "")
        except (OSError, ValueError, AttributeError):
            pass
    return jsonify({
        "snapshots": snapshots,
        "active_project_id": get_active_firebase_config()["project_id"],
        "previous_project_id": previous_project_id,
        "restorable_projects": _list_restorable_projects(),
    })


@snapshot_blueprint.route("/api/snapshots", methods=["POST"])
@_admin_required
@_csrf_required
def create_snapshot_route():
    from app import get_active_firebase_config, get_user_store

    user = session.get("user_id")
    config = get_active_firebase_config()
    try:
        store = get_user_store()
        if not hasattr(store, "db"):
            raise SnapshotError("Configured storage is not a Firestore database.")
        snapshot = create_snapshot(store.db, config["project_id"], _backup_directory())
        _write_local_event("snapshot_created", user, config["project_id"], snapshot["filename"], "verified")
        return jsonify(snapshot=snapshot)
    except Exception as error:
        current_app.logger.exception("Snapshot creation failed")
        _write_local_event("snapshot_create_failed", user, config["project_id"], result=type(error).__name__)
        return jsonify(error="Unable to create the backup. Please try again or contact your administrator."), 500


@snapshot_blueprint.route("/api/snapshots/<filename>/verify", methods=["POST"])
@_admin_required
@_csrf_required
def verify_snapshot_route(filename):
    path = _snapshot_path(filename)
    if path is None:
        return jsonify(error="Snapshot file not found."), 404
    result = verify_snapshot(path)
    _write_local_event("snapshot_verified", session.get("user_id"), result.get("metadata", {}).get("project_id"), filename, "verified" if result["verified"] else "failed")
    return jsonify(result), 200 if result["verified"] else 422


@snapshot_blueprint.route("/api/snapshots/<filename>", methods=["DELETE"])
@_admin_required
@_csrf_required
def delete_snapshot_route(filename):
    path = _snapshot_path(filename)
    if path is None:
        return jsonify(error="Snapshot file not found."), 404
    with _restore_lock:
        if filename in _restoring_filenames:
            return jsonify(error="This snapshot is currently being restored and cannot be deleted."), 409
        path.unlink()
    _write_local_event("snapshot_deleted", session.get("user_id"), filename=filename, result="deleted")
    return jsonify(deleted=True)


@snapshot_blueprint.route("/api/snapshots/open-folder", methods=["POST"])
@_admin_required
@_csrf_required
def open_snapshot_folder():
    folder = _backup_directory()
    folder.mkdir(parents=True, exist_ok=True)
    startfile = getattr(os, "startfile", None)
    if startfile is None:
        return jsonify(error="Open the backup folder from the computer running the application."), 501
    try:
        startfile(str(folder))
    except OSError:
        current_app.logger.exception("Unable to open the local backup folder")
        return jsonify(error="Unable to open the backup folder on this computer."), 500
    _write_local_event("snapshot_folder_opened", session.get("user_id"), result="opened")
    return jsonify(opened=True)


@snapshot_blueprint.route("/api/snapshots/<filename>/restore/preview", methods=["POST"])
@_admin_required
@_csrf_required
def restore_preview_route(filename):
    from app import firebase_admin

    snapshot_path = _snapshot_path(filename)
    if snapshot_path is None:
        return jsonify(error="Snapshot file not found."), 404
    if not request.is_secure and request.remote_addr not in {"127.0.0.1", "::1"}:
        return jsonify(error="For your security, upload the credentials over a secure connection or from this computer."), 403
    snapshot_check = verify_snapshot(snapshot_path)
    if not snapshot_check["verified"]:
        return jsonify(error="This backup could not be checked. Choose another backup or contact your administrator."), 422

    destination_project_id = request.form.get("destination_project_id", "").strip()
    destination_type = request.form.get("destination_type", "")
    if destination_type not in {"existing", "new"}:
        return jsonify(error="Choose a new or existing destination."), 400
    try:
        credential_bytes, credential_project_id = _service_account_payload(request.files.get("service_account"))
        if credential_project_id != destination_project_id:
            raise ValueError("The selected service-account project ID does not match the destination project ID.")
        web_config = _web_config(json.loads(request.form.get("web_config", "{}")), destination_project_id)
    except (ValueError, json.JSONDecodeError) as error:
        current_app.logger.info("Backup destination details were rejected: %s", error)
        return jsonify(error="The credentials file could not be read. Choose a valid file and check the destination details."), 400
    if firebase_admin is None:
        return jsonify(error="Backup restore is temporarily unavailable. Please contact your administrator."), 503

    credential_directory = _credentials_directory()
    credential_path = credential_directory / f"{uuid.uuid4().hex}.json"
    try:
        credential_path.write_bytes(credential_bytes)
        _secure_local_path(credential_path)
        candidate_store = _new_firestore_store(destination_project_id, credential_path, "validate")
        list(candidate_store.db.collections())
        preview = preview_restore(snapshot_path, candidate_store.db)
    except Exception as error:
        credential_path.unlink(missing_ok=True)
        current_app.logger.exception("Unable to validate the backup destination")
        _write_local_event("restore_preview_failed", session.get("user_id"), destination_project_id, filename, type(error).__name__)
        return jsonify(error="Unable to reach the destination with these details. Check the credentials and try again."), 422

    token = secrets.token_urlsafe(32)
    _prune_pending_restores()
    _pending_restores[token] = {
        "user_id": session.get("user_id"),
        "filename": filename,
        "destination_project_id": destination_project_id,
        "destination_type": destination_type,
        "credential_path": str(credential_path),
        "web_config": web_config,
        "store": candidate_store,
        "preview": preview,
        "created_at": datetime.now(timezone.utc),
        "retained": False,
    }
    _write_local_event("restore_preview_created", session.get("user_id"), destination_project_id, filename, "validated")
    return jsonify(preview={key: value for key, value in preview.items() if not key.startswith("_")}, restore_token=token)


@snapshot_blueprint.route("/api/snapshots/pending/<token>", methods=["DELETE"])
@_admin_required
@_csrf_required
def cancel_pending_restore(token):
    pending = _pending_restores.get(token)
    if pending and pending.get("user_id") == session.get("user_id"):
        with _restore_lock:
            if pending["filename"] in _restoring_filenames:
                return jsonify(error="This restore is running; credentials cannot be removed yet."), 409
            _pending_restores.pop(token, None)
        if not pending.get("retained"):
            Path(pending["credential_path"]).unlink(missing_ok=True)
    return jsonify(cancelled=True)


@snapshot_blueprint.route("/api/snapshots/<filename>/restore", methods=["POST"])
@_admin_required
@_csrf_required
def restore_snapshot_route(filename):
    snapshot_path = _snapshot_path(filename)
    payload = request.get_json(silent=True) or {}
    token = payload.get("restore_token", "")
    pending = _pending_restores.get(token)
    if snapshot_path is None:
        return jsonify(error="Snapshot file not found."), 404
    if not pending or pending.get("filename") != filename or pending.get("user_id") != session.get("user_id"):
        return jsonify(error="Restore preview expired. Upload credentials and validate the destination again."), 400
    if datetime.now(timezone.utc) - pending["created_at"] > timedelta(hours=1):
        with _restore_lock:
            if filename in _restoring_filenames:
                return jsonify(error="This restore is already running."), 409
            if _pending_restores.get(token) is pending:
                _pending_restores.pop(token, None)
                if not pending.get("retained"):
                    Path(pending["credential_path"]).unlink(missing_ok=True)
        return jsonify(error="Restore preview expired. Validate the destination again."), 400
    destination_project_id = pending["destination_project_id"]
    if payload.get("confirmed_project_id") != destination_project_id:
        return jsonify(error="Confirm the exact destination project ID before restoring."), 400
    if payload.get("skip_conflicts") is True and payload.get("overwrite_conflicts") is True:
        return jsonify(error="Choose only one destination conflict policy."), 400

    with _restore_lock:
        if _pending_restores.get(token) is not pending:
            return jsonify(error="Restore preview was cancelled. Validate the destination again."), 400
        if filename in _restoring_filenames:
            return jsonify(error="This snapshot is already being restored."), 409
        _restoring_filenames.add(filename)
    result = None
    status = "failed"
    try:
        result = restore_snapshot(
            snapshot_path,
            pending["store"].db,
            allow_skip_conflicts=payload.get("skip_conflicts") is True,
            overwrite_conflicts=payload.get("overwrite_conflicts") is True,
        )
        status = "success" if result["documents_skipped"] == 0 else "partial"
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "filename": filename,
            "source_project_id": result["source_project_id"],
            "destination_project_id": destination_project_id,
            "status": status,
            "verified": result["verified"],
            "documents_restored": result["documents_restored"],
            "documents_skipped": result["documents_skipped"],
            "documents_overwritten": result["documents_overwritten"],
            "collections_restored": result["collections_restored"],
            "collections_overwritten": result["collections_overwritten"],
        }
        _write_restore_event(log_entry)
        if status == "success" and result["verified"]:
            descriptor = {
                "project_id": destination_project_id,
                "service_account_path": pending["credential_path"],
                "web_config": pending["web_config"],
                "source_project_id": result["source_project_id"],
                "filename": filename,
                "restored_at": log_entry["timestamp"],
                "verified": True,
                "conflict_count": 0,
            }
            _write_json_atomic(_target_directory() / f"{destination_project_id}.json", descriptor)
            pending["retained"] = True
        _write_local_event("restore_finished", session.get("user_id"), destination_project_id, filename, status)
        return jsonify(status=status, result=result, switchable=status == "success" and result["verified"])
    except Exception as error:
        current_app.logger.exception("Backup restore failed")
        failure_report = getattr(error, "report", {})
        _write_restore_event({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "filename": filename,
            "source_project_id": (pending.get("preview") or {}).get("source_project_id", ""),
            "destination_project_id": destination_project_id,
            "status": "failed",
            "verified": False,
            "error": str(error)[:500],
            "result": failure_report,
        })
        _write_local_event("restore_failed", session.get("user_id"), destination_project_id, filename, type(error).__name__)
        return jsonify(status="failed", error="The restore did not complete. Review the results and contact your administrator before trying again.", result=failure_report), 500
    finally:
        with _restore_lock:
            _restoring_filenames.discard(filename)


def _activate_project(project_config, actor_id):
    from app import get_active_firebase_config, get_current_user, is_super_admin, save_active_firebase_config

    if not is_super_admin(get_current_user()):
        raise ValueError("The current account must be a Super Admin in the destination project.")
    project_id = project_config["project_id"]
    candidate_store = _new_firestore_store(project_id, project_config["service_account_path"], "activate")
    list(candidate_store.db.collections())
    destination_user = candidate_store.get_user(str(actor_id))
    if not destination_user or not is_super_admin(destination_user):
        raise ValueError("The restored project does not contain this Super Admin account.")
    active_sessions = set(destination_user.get("active_sessions", []))
    current_session_id = session.get("session_id")
    if current_session_id:
        active_sessions.add(current_session_id)
        destination_user["active_sessions"] = sorted(active_sessions)
        candidate_store.save_user(destination_user)

    current_config = get_active_firebase_config()
    previous_path = _instance_directory() / "firebase_previous_project.json"
    _write_json_atomic(previous_path, current_config)
    save_active_firebase_config(project_config, candidate_store)
    _write_local_event("firebase_project_switched", actor_id, project_id, result="verified")


@snapshot_blueprint.route("/api/snapshots/switch-project", methods=["POST"])
@_admin_required
@_csrf_required
def switch_project_route():
    payload = request.get_json(silent=True) or {}
    project_id = payload.get("project_id", "")
    if not isinstance(project_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project_id):
        return jsonify(error="The destination project ID is invalid."), 400
    if payload.get("confirmed_project_id") != project_id:
        return jsonify(error="Confirm the exact destination project ID before switching."), 400
    descriptor_path = _target_directory() / f"{project_id}.json"
    try:
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        if descriptor.get("verified") is not True or descriptor.get("conflict_count") != 0:
            raise ValueError("Only a fully verified, conflict-free restore can become active.")
        _activate_project(descriptor, session.get("user_id"))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        current_app.logger.exception("Unable to switch the active project")
        _write_local_event("firebase_project_switch_failed", session.get("user_id"), project_id, result=type(error).__name__)
        return jsonify(error="Unable to switch projects. The current project has not been changed. Please contact your administrator."), 422
    return jsonify(switched=True, project_id=project_id, reload_required=True)


@snapshot_blueprint.route("/api/snapshots/switch-back", methods=["POST"])
@_admin_required
@_csrf_required
def switch_back_route():
    payload = request.get_json(silent=True) or {}
    from app import get_active_firebase_config

    current_project_id = get_active_firebase_config()["project_id"]
    if payload.get("confirmed_project_id") != current_project_id:
        return jsonify(error="Confirm the currently active project ID before rolling back."), 400
    previous_path = _instance_directory() / "firebase_previous_project.json"
    try:
        previous_config = json.loads(previous_path.read_text(encoding="utf-8"))
        _activate_project(previous_config, session.get("user_id"))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        current_app.logger.exception("Unable to switch back to the previous project")
        _write_local_event("firebase_project_rollback_failed", session.get("user_id"), current_project_id, result=type(error).__name__)
        return jsonify(error="Unable to switch back. The current project has not been changed. Please contact your administrator."), 422
    return jsonify(switched=True, project_id=previous_config["project_id"], reload_required=True)