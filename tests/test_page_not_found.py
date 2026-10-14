import pytest

from app import app


@pytest.fixture
def client():
    app.config.update(TESTING=True, SECRET_KEY="test-secret")
    with app.test_client() as test_client:
        yield test_client


def test_unknown_page_renders_branded_not_found_page(client):
    response = client.get("/this-page-does-not-exist")

    assert response.status_code == 404
    assert response.mimetype == "text/html"
    assert b"404" in response.data
    assert b"find that page" in response.data
    assert b'href="/login"' in response.data


def test_not_found_page_uses_shared_site_theme():
    response = app.test_client().get("/this-page-does-not-exist")
    html = response.get_data(as_text=True)

    assert "/static/design_system.css" in html
    assert "/static/site_motion.css" in html
    assert "var(--mm-canvas)" in html
    assert "var(--mm-accent)" in html


def test_unknown_api_route_keeps_json_not_found_response(client):
    response = client.get("/api/this-route-does-not-exist")

    assert response.status_code == 404
    assert response.is_json
    assert response.get_json() == {"error": "Not found."}


def test_unknown_static_asset_keeps_http_error_response(client):
    response = client.get("/static/missing-page-not-found-test.css")

    assert response.status_code == 404
    assert b"We couldn't find that page" not in response.data
