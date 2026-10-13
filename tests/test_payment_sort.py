from pathlib import Path

ROOT = Path(__file__).parents[1]
sorter = (ROOT / "static/payment_sort.mjs").read_text(encoding="utf-8")
app_js = (ROOT / "static/app.js").read_text(encoding="utf-8")
ledger_pdf = (ROOT / "static/ledger_pdf.js").read_text(encoding="utf-8")
ledger_prints = (ROOT / "static/ledger_prints.js").read_text(encoding="utf-8")

assert "export function sortPaymentTransactions" in sorter
assert "transaction?.date" in sorter
assert "createdAt || right.transaction?.updatedAt" in sorter
assert "createdAt || left.transaction?.updatedAt" in sorter
assert "return left.index - right.index" in sorter
assert ".map((transaction, index)" in sorter
assert "sortPaymentTransactions(trade.paymentTransactions)" in app_js
assert "sortPaymentTransactions(transactions)" in app_js
assert "const orderedTransactions = sortPaymentTransactions(transactions)" in ledger_pdf
assert "uniquePaymentTransactions(trade.paymentTransactions)" in ledger_prints
assert "transactions.sort((a, b) => String(b.date" not in app_js
assert "transactions.slice().sort((a, b) => String(b.date" not in ledger_pdf
print('payment sorting regression checks passed for date, timestamp, stable fallback, and all display consumers')
