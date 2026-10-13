from pathlib import Path

ROOT = Path(__file__).parents[1]
app = (ROOT / "app.py").read_text(encoding="utf-8")
dashboard = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")
template = (ROOT / "templates/ledger_prints.html").read_text(encoding="utf-8")
page_js = (ROOT / "static/ledger_prints.js").read_text(encoding="utf-8")
pdf_js = (ROOT / "static/ledger_pdf.js").read_text(encoding="utf-8")

assert '@app.route("/ledger-prints")' in app
assert 'render_template(\n        "ledger_prints.html",' in app
assert 'Ledger Prints' in dashboard
assert 'href="/ledger-prints"' in dashboard
assert 'View, print, and download trade ledgers.' in dashboard
assert 'id="tradeSearch"' in template
assert 'id="ledgerModal"' in template
assert 'collection(db, "trades")' in page_js
assert 'getDocs' in page_js
assert 'data-action="view"' in page_js
assert 'data-action="download"' in page_js
assert 'downloadLedgerPDF' in page_js
assert 'downloadPaymentLedgerPDF' in (ROOT / "static/ledger_pdf.js").read_text(encoding="utf-8")
assert 'data-edit-action="add-contract"' in page_js
assert 'data-edit-action="remove-contract"' in page_js
assert "`/api/trades/" in page_js
assert 'method: "PUT"' in page_js
assert 'X-CSRF-Token' in page_js
assert 'contractItems: contractResult.contractItems' in page_js
assert '`/api/trades/${encodeURIComponent(selectedTrade.id)}/name`' in page_js
assert '"X-CSRF-Token": document.body.dataset.tradeBuilderCsrf || ""' in page_js
assert 'updateDoc(doc(db, "trades", selectedTrade.id)' not in page_js
assert 'data-contract-id' in page_js
assert 'contractItems, updatedAt:' not in page_js
assert 'contractItems' in page_js
assert 'paymentTransactions' not in page_js.split('async function saveLedgerEdits', 1)[1].split('function renderPreview', 1)[0], 'Ledger editing must not modify payment transactions'
assert 'id="editLedger"' in template
assert 'data-can-edit-ledger="{{ \'true\' if can_edit_ledger else \'false\' }}"' in template
assert 'canEditLedger && selectedTrade' in page_js
assert 'id="downloadModalPdf"' in template
assert 'import { registerEscClose, unregisterEscClose } from "./dialogs.js";' in page_js
assert "registerEscClose(modal, closeLedgerModal);" in page_js
assert "unregisterEscClose(modal);" in page_js
assert "if (opener?.isConnected) opener.focus();" in page_js
assert 'document.getElementById("closeLedger").addEventListener("click", closeLedgerModal);' in page_js
assert 'addDoc' not in page_js
assert 'deleteDoc' not in page_js
assert 'Wage History' not in pdf_js
assert 'Payment History' in pdf_js
assert 'Total Paid' in page_js
assert 'paymentLedgerSummary(trade)' in page_js
assert 'uniquePaymentTransactions' in page_js
assert 'uniquePaymentTransactions' in pdf_js
assert 'transaction.amount' in (ROOT / "static/trade_payment_history.mjs").read_text(encoding="utf-8")
assert 'id !== LEGACY_OWNER_ACCOUNT_DOCUMENT_ID' in page_js
assert 'formatDate(item.date)' in page_js
assert "Date / Type</th>" in page_js
assert "Labour Earned</th>" in page_js
assert "Advance Deduction</th>" in page_js
assert "Amount Paid</th>" in page_js
assert "Pending Labour Payment</th>" in page_js
assert "Payment Mode / Reference</th>" in page_js
assert "No payment transactions recorded." in page_js
assert pdf_js.count("No payment transactions recorded.") == 1
assert "const button = event.currentTarget;" in page_js
assert "finally { button.disabled = false; }" in page_js
assert 'Worker Categories' in pdf_js
assert 'Work Contracts / Capping' in pdf_js
assert 'getNumberOfPages' in pdf_js
assert 'Page ${pageNumber} of ${totalPages}' in pdf_js
assert 'Total Payments Counted Against Contract' in pdf_js
assert 'summary.contractPaymentBreakdown.map(category' in pdf_js
assert 'Labour Advances Issued' in pdf_js
assert 'Labour Settlement Details' not in pdf_js
assert 'renderPaymentHistory(doc, transactions' in pdf_js
assert 'Labour Earned' in pdf_js
assert 'Advance Deduction' in pdf_js
assert 'Amount Paid' in pdf_js
assert 'Pending Labour Payment' in pdf_js
assert 'calculateOutstandingBalanceAfterTransaction(transactions, transaction.id)' in pdf_js
assert 'Total Labour Earned' in pdf_js
assert 'Total Advances Issued (Lifetime)' in pdf_js
assert 'Less: Total Advance Recovered' in pdf_js
assert 'Current Advance Balance to Recover' in pdf_js
assert 'Pending Labour Payment' in pdf_js
pdf_labour_summary = pdf_js.split('function renderAccountSummaries(', 1)[1].split(
    'function renderPaymentHistory(', 1
)[0]
for redundant_summary_label in (
    'Net Labour Payable After Advance Recovery',
    'Labour Settlement Payments Made',
    'Pending Labour Payment',
    'Lump-Sum Payments Recorded',
):
    assert redundant_summary_label not in pdf_labour_summary
assert 'Labour Earned' in pdf_js
assert 'Advance Deduction' in pdf_js
assert 'Amount Paid' in pdf_js
assert 'Pending Labour Payment' in pdf_js.split('function renderPaymentHistory(', 1)[1]
assert 'Contract Account' in pdf_js
assert 'Labour Account' in pdf_js
assert 'Daily Wage Payments' not in pdf_js
assert 'paymentLedgerSummary(trade)' in page_js
assert 'Total Payments Counted Against Contract' in page_js
assert 'summary.contractPaymentBreakdown.map(category' in page_js
assert 'Cash Paid for Labour' not in page_js
assert 'Total Advances Issued (Lifetime)' in page_js
assert 'Less: Total Advance Recovered' in page_js
assert 'Current Advance Balance to Recover' in page_js
page_labour_summary = page_js.split('<h3>Labour Account</h3>', 1)[1].split(
    '<h3>Worker Categories</h3>', 1
)[0]
for retained_summary_field in (
    'Total Labour Earned',
    'Total Advances Issued (Lifetime)',
    'Less: Total Advance Recovered',
    'Current Advance Balance to Recover',
):
    assert retained_summary_field in page_labour_summary
for redundant_summary_label in (
    'Net Labour Payable After Advance Recovery',
    'Labour Settlement Payments Made',
    'Pending Labour Payment',
    'Lump-Sum Payments Recorded',
):
    assert redundant_summary_label not in page_labour_summary
assert 'Labour Earned' in page_js
assert 'Advance Deduction' in page_js
assert 'Amount Paid' in page_js
page_history_renderer = page_js.split('function paymentHistoryTable(', 1)[1].split(
    'function renderPreview(', 1
)[0]
for transaction_field in (
    'Labour Earned',
    'Advance Deduction',
    'Amount Paid',
    'Pending Labour Payment',
):
    assert transaction_field in page_history_renderer
assert 'calculateOutstandingBalanceAfterTransaction(transactions, item.id)' in page_history_renderer
assert 'item.labourEarned' in page_history_renderer
assert 'item.advanceAdjustment' in page_history_renderer
assert 'Contract Account' in page_js
assert 'Labour Account' in page_js
assert 'Outstanding Balance' not in pdf_js
assert 'rowPageBreak: "avoid"' in pdf_js
assert 'showHead: "everyPage"' in pdf_js
print('ledger_prints regression checks passed')
