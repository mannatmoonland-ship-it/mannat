from pathlib import Path

ROOT = Path(__file__).parents[1]
template = (ROOT / "templates/index.html").read_text(encoding="utf-8")
labour_payments_template = (ROOT / "templates/labour_payments.html").read_text(encoding="utf-8")
app_js = (ROOT / "static/app.js").read_text(encoding="utf-8")
firebase_auth_js = (ROOT / "static/firebase_auth.js").read_text(encoding="utf-8")

assert 'id="financialTradeSelect"' in template
assert '{% set financial_only = true %}' in labour_payments_template
assert '{% include "index.html" %}' in labour_payments_template
assert 'Select Trade' in template
assert 'collection(' in app_js and 'TRADES_COLLECTION' in app_js
assert 'await withTimeout(getDocs(' in app_js, 'Trade loading must time out instead of leaving the dropdown in a loading state'
assert 'Timed out while loading trades. Please check your connection and try again.' in app_js, 'Trade loading timeout must display a clear error'
assert 'firebaseInitializationError' in app_js and 'Unable to load trades.' in app_js, 'Firebase initialization failures must leave the loading state'
assert 'const REQUEST_TIMEOUT_MS = 15000' in firebase_auth_js, 'Shared Firebase requests must have a bounded timeout'
assert 'setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)' in firebase_auth_js, 'Firebase API fetches must be abortable on timeout'
assert 'if (document.readyState === "loading")' in app_js, 'Async Firebase initialization must handle DOMContentLoaded firing before module initialization completes'
assert 'else {\n    initializeApplication();\n}' in app_js, 'Application initialization must run immediately when DOMContentLoaded already fired'
assert 'selector.innerHTML = \'<option value="">Select Trade</option>\' + trades.map' in app_js
assert 'item.id === LEGACY_OWNER_ACCOUNT_DOCUMENT_ID' in app_js
assert 'LEGACY_OWNER_ACCOUNT_DOCUMENT_ID' in app_js
assert 'Select Trade' in app_js
assert 'Please select a trade to view its payment details.' in app_js
assert 'const previousSelection = selector?.value || ""' in app_js
assert 'renderSelectedFinancialTrade(selectedTradeId)' in app_js
assert 'renderSelectedFinancialTrade' in app_js
assert 'list.innerHTML = ""' in app_js
assert 'No trades available.' in app_js
assert 'openPaymentModal' in app_js
assert 'paymentTransactions' in app_js
assert 'calculateTradeFinancialSummary' in app_js
assert 'contractPayments' in app_js
assert 'labourEarned' in app_js
assert 'labourAdvances' in app_js
assert 'advanceDeductions' in app_js
advance_summary = template.split('<div class="summary-box advance-summary-box">', 1)[1].split("{% else %}", 1)[0]
assert "Total Advances Issued (Lifetime)" in advance_summary
assert "Less: Total Advance Recovered" in advance_summary
assert "Current Advance Balance to Recover" in advance_summary
assert 'id="labourAdvancesTotal"' in advance_summary
assert 'id="advanceDeductionsTotal"' in advance_summary
assert 'id="advanceBalance"' in advance_summary
assert template.count('class="summary-box advance-summary-box"') == 2
assert "Labour Advances Taken" not in template
assert "Pending Labour Payment After Settlement:" in app_js
assert ".advance-summary-balance .summary-value" in template

payment_summary_template = template.split(
    "<!-- =================================================\n             PAYMENT SUMMARY", 1
)[1]
financial_payment_summary_template = payment_summary_template.split(
    "{% if financial_only %}", 1
)[1].split("{% else %}", 1)[0]
standard_payment_summary_template = payment_summary_template.split(
    "{% else %}", 1
)[1].split("{% endif %}", 1)[0]
financial_labour_summary_template = financial_payment_summary_template.split(
    '<section class="account-summary-section">\n                <h4>Labour Account</h4>', 1
)[1].split("</section>", 1)[0]
for retained_figure in (
    "Total Labour Earned",
    "Total Advances Issued (Lifetime)",
    "Less: Total Advance Recovered",
    "Current Advance Balance to Recover",
):
    assert retained_figure in financial_labour_summary_template
for removed_figure in (
    "Net Labour Payable After Advance Recovery",
    "Labour Settlement Payments Made",
    "Pending Labour Payment",
    "Lump-Sum Payments Recorded",
):
    assert removed_figure not in financial_labour_summary_template
assert "settlement-summary-help" not in financial_labour_summary_template
assert "Advance recovery is shown separately from settlement payments." not in financial_labour_summary_template
assert "id=\"labourEarnedTotal\"" in financial_labour_summary_template
assert "id=\"labourAdvancesTotal\"" in financial_labour_summary_template
assert "id=\"advanceDeductionsTotal\"" in financial_labour_summary_template
assert "id=\"advanceBalance\"" in financial_labour_summary_template
assert "Contract Account" in financial_payment_summary_template
assert "Total Payments Counted Against Contract" in financial_payment_summary_template
assert 'id="totalPayments"' in standard_payment_summary_template
for retained_figure in (
    "Total Labour Earned",
    "Total Advances Issued (Lifetime)",
    "Less: Total Advance Recovered",
    "Current Advance Balance to Recover",
):
    assert retained_figure in standard_payment_summary_template
for removed_figure in (
    "Pending Labour Payment",
    "Labour Settlement Payments Made",
    "Lump-Sum Payments Recorded",
):
    assert removed_figure not in standard_payment_summary_template
assert 'id="paymentHistory"' in template
assert 'id="paymentLedgerDownloadPdf"' in template
payment_history_renderer = app_js.split("function renderPaymentHistory(", 1)[1].split(
    "function editPayment(", 1
)[0]
for dynamic_value in (
    "financialSummary.labourEarned",
    "financialSummary.labourAdvances",
    "financialSummary.advanceDeductions",
    "advanceBalance",
):
    assert dynamic_value in payment_history_renderer
assert 'document.getElementById("netLabourPayableTotal")' not in payment_history_renderer
assert "renderLumpSumAgreements(trade, transactions);" in payment_history_renderer
assert "container.innerHTML =" in payment_history_renderer

ledger_modal_renderer = app_js.split("function openTradeLedgerModal(", 1)[1].split(
    "function closeTradeLedgerModal(", 1
)[0]
ledger_meta = ledger_modal_renderer.split(
    '<div class="ledger-modal-meta">', 1
)[1].split("<h3>Worker Categories</h3>", 1)[0]
assert "Current Advance Balance to Recover" not in ledger_meta
ledger_labour_summary = ledger_modal_renderer.split(
    "<h4>Labour Account</h4>", 1
)[1].split("<h3>Payment History</h3>", 1)[0]
financial_only_ledger_summary = ledger_labour_summary
for retained_figure, value in (
    ("Total Labour Earned", "summary.labourEarned"),
    ("Total Advances Issued (Lifetime)", "summary.labourAdvances"),
    ("Less: Total Advance Recovered", "summary.advanceDeductions"),
    ("Current Advance Balance to Recover", "summary.advanceBalance"),
):
    assert retained_figure in ledger_labour_summary
    assert value in ledger_labour_summary
for removed_figure in (
    "Net Labour Payable After Advance Recovery",
    "Labour Settlement Payments Made",
    "Pending Labour Payment",
    "Lump-Sum Payments Recorded",
):
    assert removed_figure not in financial_only_ledger_summary
assert "summary-explanation" not in ledger_labour_summary
assert "Payment History" in ledger_modal_renderer
for transaction_field in (
    "Date / Type",
    "Worker Category",
    "Labour Earned",
    "Advance Deduction",
    "Amount Paid",
    "Pending Labour Payment",
    "Payment Mode / Remarks",
):
    assert transaction_field in ledger_modal_renderer
assert "transaction.labourEarned" in ledger_modal_renderer
assert "transaction.advanceAdjustment" in ledger_modal_renderer
assert "calculateOutstandingBalanceAfterTransaction(transactions, transaction.id)" in ledger_modal_renderer
transaction_details_renderer = app_js.split(
    "function openPaymentTransactionDetails(", 1
)[1].split("async function copyPaymentTransactionId(", 1)[0]
for transaction_field in (
    "Amount Paid",
    "Labour Earned",
    "Advance Deduction",
    "Pending Labour Payment",
):
    assert transaction_field in transaction_details_renderer

trade_payment_summary = app_js.split("<!-- =============================================\n             LABOUR PAYMENTS", 1)[1].split('            <button\n                class="btn-primary payment-btn"', 1)[0]
assert 'class="payment-summary labour-account-summary"' in trade_payment_summary
assert trade_payment_summary.count('class="summary-box') == 2
assert "TOTAL LABOUR EARNED" in trade_payment_summary
for retained_figure, dynamic_value in (
    ("Total Advances Issued (Lifetime)", "labourAdvances"),
    ("Less: Total Advance Recovered", "advanceDeductions"),
    ("Current Advance Balance to Recover", "advanceBalance"),
):
    assert retained_figure in trade_payment_summary
    assert f"${{money({dynamic_value})}}" in trade_payment_summary
for removed_figure in (
    "NET LABOUR PAYABLE AFTER ADVANCE RECOVERY",
    "LABOUR SETTLEMENT PAYMENTS MADE",
    "PENDING LABOUR PAYMENT",
    "LUMP-SUM PAYMENTS RECORDED",
):
    assert removed_figure not in trade_payment_summary
assert "settlement-summary-help" not in trade_payment_summary
assert "const financialSummary = calculateTradeFinancialSummary(contractItems, transactions);" in app_js
assert "Open Payment Ledger" in app_js
assert '.addEventListener(\n        "click",\n        () =>\n            openPaymentModal(' in app_js
assert "#tradeList .labour-account-summary" in template
assert "#tradeList .labour-account-summary {" in template
assert "#tradeList .labour-account-summary" in template.split("@media(max-width:700px)", 1)[1]
assert "Labour Settlement Payments Made" not in template
help_text = "Actual payments recorded through labour settlements. Advance recovery is shown separately and is not counted as an additional payment."
assert help_text not in template + app_js
assert '"labour_settlement"' in app_js
assert "Less: Total Advance Recovered" in template
assert "Total Advances Issued (Lifetime)" in template
assert "Current Advance Balance to Recover" in template
assert "Pending Labour Payment" not in template
assert "Net Labour Payable After Advance Recovery" not in financial_labour_summary_template
assert "cash paid for labour" not in (template + app_js).lower()
assert "dailyWagePaymentsTotal" not in template
assert "dailyWagePaymentsTotal" not in app_js
assert "Contract Account" in template
assert "Labour Account" in template
assert "Total Payments Counted Against Contract" in template
assert 'id="contractPaymentBreakdown"' in template
assert "contractPaymentBreakdown.map(category" in app_js
assert 'COMPLETED_WORK_PAYMENT_TYPE' in app_js
assert 'id="paymentLedgerDownloadPdf"' in template
assert 'downloadPaymentLedgerPDF(currentPaymentTrade)' in app_js
assert 'id="settlementTotal"' not in template
assert 'id="outstandingBalance"' not in template
assert 'document.getElementById("netLabourPayableTotal")' not in app_js
print('labour_payments trade selector regression checks passed')