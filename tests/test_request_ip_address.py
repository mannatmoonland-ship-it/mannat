import pytest

import app as app_module
from app import app


@pytest.mark.parametrize("path", ["/daily-reports", "/daily-reports/share-image"])
@pytest.mark.parametrize(
    ("remote_addr", "forwarded_for"),
    [
        ("127.0.0.1", "192.168.1.12"),
        ("192.168.1.12", "8.8.8.8"),
        ("8.8.8.8", "192.168.1.12"),
    ],
)
def test_daily_report_routes_require_authentication_regardless_of_client_ip(
    monkeypatch, path, remote_addr, forwarded_for
):
    monkeypatch.setattr(app_module, "IS_PRODUCTION_ENVIRONMENT", True)
    response = app.test_client().get(
        path,
        environ_base={"REMOTE_ADDR": remote_addr},
        headers={"X-Forwarded-For": forwarded_for},
    )

    assert response.status_code == 401
    assert "<svg" not in response.get_data(as_text=True)
