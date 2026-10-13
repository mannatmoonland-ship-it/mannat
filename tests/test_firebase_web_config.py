import json

import pytest

import app as app_module


@pytest.fixture
def config_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(
        app_module,
        "ACTIVE_FIREBASE_CONFIG_PATH",
        tmp_path / "firebase_active_project.json",
    )
    monkeypatch.setattr(app_module, "find_firebase_service_account_path", lambda: None)
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "mannat-moon-construction")
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", False)
    monkeypatch.delenv("FIREBASE_WEB_CONFIG", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_BASE64", raising=False)


def test_environment_web_config_is_used(config_environment, monkeypatch):
    web_config = {
        "apiKey": "test-public-key",
        "authDomain": "test.firebaseapp.com",
        "projectId": "mannat-moon-construction",
        "appId": "test-app-id",
    }
    monkeypatch.setenv("FIREBASE_WEB_CONFIG", json.dumps(web_config))

    active_config = app_module.get_active_firebase_config()

    assert active_config["project_id"] == "mannat-moon-construction"
    assert active_config["web_config"] == web_config


@pytest.mark.parametrize(
    ("environment_value", "message"),
    [
        ("", "FIREBASE_WEB_CONFIG must contain valid JSON."),
        ("{malformed", "FIREBASE_WEB_CONFIG must contain valid JSON."),
        ("[]", "FIREBASE_WEB_CONFIG must contain a JSON object."),
        (
            '{"projectId":"mannat-moon-construction"}',
            "FIREBASE_WEB_CONFIG is missing required public configuration fields.",
        ),
    ],
)
def test_invalid_environment_web_config_fails_without_echoing_values(
    config_environment,
    monkeypatch,
    environment_value,
    message,
):
    monkeypatch.setenv("FIREBASE_WEB_CONFIG", environment_value)

    with pytest.raises(RuntimeError, match=message):
        app_module.get_active_firebase_config()


def test_environment_web_config_must_match_active_project(config_environment, monkeypatch):
    web_config = {
        "apiKey": "test-public-key",
        "authDomain": "test.firebaseapp.com",
        "projectId": "different-project",
        "appId": "test-app-id",
    }
    monkeypatch.setenv("FIREBASE_WEB_CONFIG", json.dumps(web_config))

    with pytest.raises(RuntimeError, match="Firebase Admin and Web project IDs differ"):
        app_module.get_active_firebase_config()


def test_environment_web_config_rejects_non_web_fields(config_environment, monkeypatch):
    web_config = {
        "apiKey": "test-public-key",
        "authDomain": "test.firebaseapp.com",
        "projectId": "mannat-moon-construction",
        "appId": "test-app-id",
        "private_key": "must-not-be-exposed",
    }
    monkeypatch.setenv("FIREBASE_WEB_CONFIG", json.dumps(web_config))

    with pytest.raises(RuntimeError, match="unsupported configuration fields"):
        app_module.get_active_firebase_config()


def test_missing_environment_web_config_fails_closed(config_environment):
    with pytest.raises(RuntimeError, match="FIREBASE_WEB_CONFIG must be explicitly configured"):
        app_module.get_active_firebase_config()


def _web_config(project_id):
    return {
        "apiKey": "test-public-key",
        "authDomain": f"{project_id}.firebaseapp.com",
        "projectId": project_id,
        "appId": "test-app-id",
    }


def _write_service_account(path, project_id):
    path.write_text(
        json.dumps({
            "type": "service_account",
            "project_id": project_id,
            "client_email": "synthetic@example.invalid",
            "private_key": "synthetic-not-a-real-private-key",
        }),
        encoding="utf-8",
    )
    return path


def test_deployed_configuration_requires_explicit_project_id(config_environment, monkeypatch):
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", True)
    monkeypatch.delenv("FIREBASE_PROJECT_ID")
    monkeypatch.setenv("FIREBASE_WEB_CONFIG", json.dumps(_web_config("mannat-moon-construction")))

    with pytest.raises(RuntimeError, match="FIREBASE_PROJECT_ID must be explicitly configured"):
        app_module.get_active_firebase_config()


def test_deployed_configuration_requires_explicit_web_config(config_environment, monkeypatch):
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", True)
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "staging-mannat-moon")

    with pytest.raises(RuntimeError, match="FIREBASE_WEB_CONFIG must be explicitly configured"):
        app_module.get_active_firebase_config()


def test_deployed_configuration_requires_explicit_admin_credentials(config_environment, monkeypatch):
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", True)
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "staging-mannat-moon")
    monkeypatch.setenv("FIREBASE_WEB_CONFIG", json.dumps(_web_config("staging-mannat-moon")))

    with pytest.raises(RuntimeError, match="Firebase Admin credentials must be explicitly configured"):
        app_module.get_active_firebase_config()


def test_deployed_configuration_uses_explicit_matching_project_and_credentials(
    config_environment, monkeypatch, tmp_path
):
    project_id = "staging-mannat-moon"
    credentials_path = _write_service_account(tmp_path / "service-account.json", project_id)
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", True)
    monkeypatch.setenv("FIREBASE_PROJECT_ID", project_id)
    monkeypatch.setenv("FIREBASE_WEB_CONFIG", json.dumps(_web_config(project_id)))
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT", str(credentials_path))

    active_config = app_module.get_active_firebase_config()

    assert active_config["project_id"] == project_id
    assert active_config["web_config"]["projectId"] == project_id
    assert active_config["service_account_path"] == str(credentials_path)


def test_deployed_configuration_rejects_credentials_for_another_project(
    config_environment, monkeypatch, tmp_path
):
    project_id = "staging-mannat-moon"
    credentials_path = _write_service_account(tmp_path / "service-account.json", "different-project")
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", True)
    monkeypatch.setenv("FIREBASE_PROJECT_ID", project_id)
    monkeypatch.setenv("FIREBASE_WEB_CONFIG", json.dumps(_web_config(project_id)))
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT", str(credentials_path))

    with pytest.raises(RuntimeError, match="Firebase Admin credentials do not match"):
        app_module.get_active_firebase_config()


def test_firestore_store_rejects_deployed_production_fallback(config_environment, monkeypatch):
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", True)
    monkeypatch.delenv("FIREBASE_PROJECT_ID")
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)

    with pytest.raises(RuntimeError, match="FIREBASE_PROJECT_ID must be explicitly configured"):
        app_module.FirebaseFirestoreStore()
