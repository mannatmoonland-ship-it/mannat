from pathlib import Path
import re


ROOT = Path(__file__).parents[1]
STYLES = (ROOT / "static/site_motion.css").read_text(encoding="utf-8")


def _rule(selector):
    match = re.search(
        rf"(?m)^{re.escape(selector)}\s*\{{([^}}]*)\}}",
        STYLES,
    )
    assert match, f"Missing scoped theme rule for {selector}"
    return match.group(1)


def test_payment_editor_and_history_use_scoped_dark_surfaces():
    editor = _rule(".financial-module #paymentModal .payment-form")
    history = _rule(".financial-module #paymentModal .payment-history")
    modal = _rule(".financial-module #paymentModal .modal-panel")

    for styles in (editor, history, modal):
        assert "linear-gradient(145deg, rgba(18,34,26" in styles
        assert "rgba(8,16,12" in styles
        assert "rgba(123,224,182" in styles
    assert not re.search(r"(?m)^\.modal-panel\s*\{", STYLES)


def test_payment_form_and_history_copy_remain_readable_on_dark_surfaces():
    assert "#paymentModal .payment-form h3 { color: #edf8f2; }" in STYLES
    assert "#paymentModal .payment-form label { color: #dcebe2 !important; }" in STYLES
    assert "#paymentModal .payment-form .form-help { color: #b4c8ba; }" in STYLES
    assert ".financial-module #paymentHistory { color: #dcebe2; }" in STYLES
    assert ".financial-module #paymentHistory .transaction-row > strong { color: #83dfbb; }" in STYLES
    assert ".financial-module #paymentHistory .transaction-row small { color: #b4c8ba; }" in STYLES


def test_existing_dark_form_control_styles_are_preserved():
    assert ".financial-module .payment-form input," in STYLES
    assert ".financial-module .payment-form select," in STYLES
    assert ".financial-module .payment-form textarea {" in STYLES
    assert "background-color: rgba(3,10,6,.92);" in STYLES


def test_payment_theme_overrides_are_scoped_to_financial_payment_modal():
    assert ".financial-module #paymentModal .payment-form" in STYLES
    assert ".financial-module #paymentModal .payment-history" in STYLES
    assert ".financial-module #paymentModal .summary-box" in STYLES


def test_trade_ledger_modal_uses_readable_dark_theme():
    panel = _rule("#tradeLedgerModal .modal-panel")
    cells = _rule("#tradeLedgerModal .ledger-modal-table td")
    headers = _rule("#tradeLedgerModal .ledger-modal-table th")

    assert "color: #edf8f2;" in panel
    assert "linear-gradient(145deg, rgba(18,34,26" in panel
    assert "color: #dcebe2;" in cells
    assert "color: #83dfbb;" in headers
    assert "rgba(72,148,108,.14)" in headers
    assert "#tradeLedgerModal h3 { border-bottom-color:" in STYLES
    assert "#tradeLedgerModal .ledger-modal-meta strong { color: #b4c8ba; }" in STYLES
    assert "tbody tr:nth-child(even)" in STYLES
