from verify_deployment_artifact import find_excluded_artifact_paths


def test_deployment_artifact_verifier_flags_local_only_files_and_directories(tmp_path):
    excluded_paths = (
        ".env",
        "mannat-moon-construction-firebase-adminsdk-test.json",
        "private/service-account.json",
        "private/cloud-credentials.json",
        "private/tls-key.pem",
        "tests/secret.txt",
        "cache/generated.tmp",
    )
    for relative_path in excluded_paths:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder, not a credential", encoding="utf-8")
    for relative_path in (
        "backups/recovery.zip",
        "instance/runtime.json",
        "dist/MannatMoon.exe",
        ".venv_backup/pyvenv.cfg",
        "node_modules/example/index.js",
    ):
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder, not a credential", encoding="utf-8")

    assert find_excluded_artifact_paths(tmp_path) == sorted((
        *excluded_paths,
        "backups",
        "instance",
        "dist",
        ".venv_backup",
        "node_modules",
    ))


def test_deployment_artifact_verifier_accepts_source_runtime_files(tmp_path):
    (tmp_path / "app.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "dashboard.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "app.js").write_text("export {};\n", encoding="utf-8")

    assert find_excluded_artifact_paths(tmp_path) == []
