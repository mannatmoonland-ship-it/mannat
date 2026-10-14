from types import SimpleNamespace

import pytest

import app as app_module


@pytest.fixture
def fast2sms_config(monkeypatch):
    monkeypatch.setitem(app_module.app.config, "FAST2SMS_API_KEY", "test-api-key")
    monkeypatch.setitem(app_module.app.config, "FAST2SMS_SENDER_ID", "JJROBO")
    monkeypatch.setitem(app_module.app.config, "FAST2SMS_MESSAGE_ID", "144137")


def test_fast2sms_uses_dlt_api_and_server_auth(fast2sms_config, monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "return": True,
                "status_code": 200,
                "request_id": "sms-request",
            },
        )

    monkeypatch.setattr(app_module.requests, "get", fake_get)

    request_id = app_module._send_fast2sms_otp("+919876543210", "123456")

    assert request_id == "sms-request"
    url, options = calls[0]
    assert url == "https://www.fast2sms.com/dev/bulkV2"
    assert options["headers"] == {"Authorization": "test-api-key"}
    assert options["params"] == {
        "route": "dlt",
        "sender_id": "JJROBO",
        "message": "144137",
        "variables_values": "123456",
        "numbers": "9876543210",
    }
    assert "authorization" not in options["params"]
    assert options["timeout"] == 15


def test_fast2sms_rejects_non_indian_numbers(fast2sms_config, monkeypatch):
    monkeypatch.setattr(
        app_module.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail("Must not send an international SMS."),
    )

    with pytest.raises(ValueError, match="10-digit Indian"):
        app_module._send_fast2sms_otp("+14155550123", "123456")


def test_fast2sms_sends_ten_digit_number_when_legacy_record_has_country_prefix(fast2sms_config, monkeypatch):
    calls = []
    monkeypatch.setattr(
        app_module.requests,
        "get",
        lambda url, **kwargs: (
            calls.append((url, kwargs))
            or SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"return": True},
            )
        ),
    )

    app_module._send_fast2sms_otp("+919876543210", "123456")

    assert calls[0][1]["params"]["numbers"] == "9876543210"


def test_fast2sms_rejects_provider_declined_message(fast2sms_config, monkeypatch):
    monkeypatch.setattr(
        app_module.requests,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"return": False, "message": "rejected"},
        ),
    )

    with pytest.raises(app_module.Fast2SMSProviderError, match="rejected"):
        app_module._send_fast2sms_otp("+919876543210", "123456")


def test_fast2sms_reports_provider_reason_without_exposing_api_key(fast2sms_config, monkeypatch):
    monkeypatch.setattr(
        app_module.requests,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "return": False,
                "message": ["Invalid authorization test-api-key"],
            },
        ),
    )

    with pytest.raises(app_module.Fast2SMSProviderError) as error:
        app_module._send_fast2sms_otp("+919876543210", "123456")

    assert "Invalid authorization [redacted]" in str(error.value)
    assert "test-api-key" not in str(error.value)


def test_fast2sms_requires_message_id_configuration(monkeypatch):
    monkeypatch.setitem(app_module.app.config, "FAST2SMS_API_KEY", "test-api-key")
    monkeypatch.setitem(app_module.app.config, "FAST2SMS_SENDER_ID", "JJROBO")
    monkeypatch.setitem(app_module.app.config, "FAST2SMS_MESSAGE_ID", "")

    with pytest.raises(RuntimeError, match="not fully configured"):
        app_module._fast2sms_configuration()


def test_fast2sms_rejects_invalid_message_id(monkeypatch):
    monkeypatch.setitem(app_module.app.config, "FAST2SMS_API_KEY", "test-api-key")
    monkeypatch.setitem(app_module.app.config, "FAST2SMS_SENDER_ID", "JJROBO")
    monkeypatch.setitem(app_module.app.config, "FAST2SMS_MESSAGE_ID", "invalid/message-id")

    with pytest.raises(RuntimeError, match="DLT message ID configuration is invalid"):
        app_module._fast2sms_configuration()


def test_fast2sms_requires_a_six_digit_code(fast2sms_config, monkeypatch):
    monkeypatch.setattr(
        app_module.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail("Must not send an invalid OTP."),
    )

    with pytest.raises(ValueError, match="six-digit code"):
        app_module._send_fast2sms_otp("+919876543210", "1234")
