from app import app as flask_app


def test_wsgi_exports_existing_flask_application(monkeypatch):
    monkeypatch.setitem(flask_app.config, "SECRET_KEY", "test-wsgi-secret-with-at-least-32-bytes")
    monkeypatch.setitem(flask_app.config, "SESSION_COOKIE_SECURE", True)

    from wsgi import app as wsgi_app

    assert wsgi_app is flask_app
    assert "health_check" in wsgi_app.view_functions
    assert "camera_authenticate" in wsgi_app.view_functions