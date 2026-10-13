from pathlib import Path


ROOT = Path(__file__).parents[1]
APP_JS = (ROOT / "static/app.js").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "templates/index.html").read_text(encoding="utf-8")


def function_source(source, declaration, next_declaration):
    start = source.index(declaration)
    end = source.index(next_declaration, start)
    return source[start:end]


def test_labour_payment_create_edit_delete_use_flask_api_without_firestore_mutations():
    delete_source = function_source(
        APP_JS,
        "async function deletePayment(",
        "async function sendLabourPaymentMutation(",
    )
    save_source = function_source(
        APP_JS,
        "async function savePayment()",
        "async function downloadTradePDF(",
    )

    assert "sendLabourPaymentMutation(" in delete_source
    assert "sendLabourPaymentMutation(" in save_source
    assert "await updateDoc(" not in delete_source
    assert "await updateDoc(" not in save_source
    assert '"/api/labour-payments/' in APP_JS or "`/api/labour-payments/" in APP_JS
    assert "data-labour-payments-csrf" in TEMPLATE


def test_payment_mutation_helper_sends_session_credentials_and_csrf():
    helper_source = function_source(
        APP_JS,
        "async function sendLabourPaymentMutation(",
        "/* =========================================================\n   SAVE / UPDATE PAYMENT",
    )

    assert 'credentials: "same-origin"' in helper_source
    assert '"X-CSRF-Token": csrfToken' in helper_source
    assert '"Idempotency-Key": requestId' in helper_source
    assert "if (!response.ok)" in helper_source
    assert "result.paymentTransactions" in helper_source


def test_settlement_displays_use_advance_recovery_and_labour_payable_terms():
    assert "Advance Deduction:" in APP_JS
    assert "Pending Labour Payment After Settlement:" in APP_JS
    assert "Outstanding Labour Balance:" not in APP_JS
    assert "Advance Balance After Settlement:" in APP_JS
    assert "Amount Being Paid Now:" in APP_JS
    assert "Advance Deduction Amount" in TEMPLATE
    assert "This recovers a previously issued Labour Advance." in TEMPLATE
    assert "No Advance Deduction" in TEMPLATE
    assert "Partial Advance Deduction" in TEMPLATE
    assert "Full Advance Deduction" in TEMPLATE
    assert "Outstanding Balance:" not in APP_JS
    assert "<strong>\n                                            Deduction:" not in APP_JS
    assert "<strong>\n                                            Outstanding:" not in APP_JS


def test_zero_labour_advance_disables_modes_and_clears_stale_deduction():
    availability_source = function_source(
        APP_JS,
        "function updateAdvanceDeductionAvailability(",
        "/* =========================================================\n   CALCULATE SETTLEMENT",
    )
    calculation_source = function_source(
        APP_JS,
        "function calculateSettlementAmount()",
        "/* =========================================================\n   ",
    )
    save_source = function_source(
        APP_JS,
        "async function savePayment()",
        "async function downloadTradePDF(",
    )

    assert "calculateAdvanceBalance(activeTransactions)" in availability_source
    assert "const unavailable = balance <= 0" in availability_source
    assert "partialOption.disabled = unavailable" in availability_source
    assert "fullOption.disabled = unavailable" in availability_source
    assert "adjustmentAmountInput.value = \"\"" in availability_source
    assert "modeSelect.value = \"none\"" in availability_source
    assert "unavailableMessage.hidden = !unavailable" in availability_source
    assert "updateAdvanceDeductionAvailability()" in calculation_source
    assert "balance <= 0" in calculation_source
    assert "updateAdvanceDeductionAvailability(" in save_source
    assert "freshTrade.paymentTransactions" in save_source
    assert "calculateSettlementAmount()" in save_source
    assert "advanceAdjustmentMode: document.getElementById(\"advanceAdjustmentMode\").value" in save_source
    assert 'id="advanceDeductionUnavailable"' in TEMPLATE
    assert (
        "No outstanding labour advance. Advance deduction is not applicable."
        in TEMPLATE
    )


def test_trade_builder_save_no_longer_rewrites_existing_payment_transactions():
    save_trade_source = function_source(
        APP_JS,
        "async function saveTrade()",
        "function editTrade(",
    )

    assert 'await sendTradeMutation(\n                `/api/trades/${encodeURIComponent(editingTradeId)}`,\n                "PATCH",\n                data\n            )' in save_trade_source
    assert "paymentTransactions" not in save_trade_source
    assert "contractItems" not in save_trade_source
