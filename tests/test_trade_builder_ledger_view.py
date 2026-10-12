from pathlib import Path

ROOT = Path(__file__).parents[1]
template = (ROOT / "templates/index.html").read_text(encoding="utf-8")
app_js = (ROOT / "static/app.js").read_text(encoding="utf-8")
pdf_js = (ROOT / "static/ledger_pdf.js").read_text(encoding="utf-8")

assert 'view-ledger-btn' in app_js
assert 'openTradeLedgerModal' in app_js
assert 'id="tradeLedgerModal"' in template
assert 'id="tradeLedgerDownloadPdf"' in template
assert 'id="tradeLedgerClose"' in template
assert 'registerEscClose(modal, closeTradeLedgerModal)' in app_js
assert 'body.ledger-modal-open' in template
assert 'currentViewLedgerTrade' in app_js
assert 'await downloadLedgerPDF(currentViewLedgerTrade)' in app_js
assert 'calculateTradeFinancialSummary' in app_js
assert 'paymentHistory' in app_js
assert 'Wage History' not in pdf_js
assert 'paymentTransactions' not in app_js.split('function openTradeLedgerModal', 1)[1].split('function closeTradeLedgerModal', 1)[0] or 'trade.paymentTransactions' in app_js.split('function openTradeLedgerModal', 1)[1].split('function closeTradeLedgerModal', 1)[0]
selector_guard = app_js.index('if (financialTradeSelect)')
close_listener = app_js.index('"tradeLedgerClose"')
assert close_listener > selector_guard
assert app_js[selector_guard:close_listener].count('{') == app_js[selector_guard:close_listener].count('}')
print('trade_builder ledger view regression checks passed')
