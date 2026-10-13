from pathlib import Path


ROOT = Path(__file__).parents[1]
APP_JS = (ROOT / "static/app.js").read_text(encoding="utf-8")
LEDGER_JS = (ROOT / "static/ledger_prints.js").read_text(encoding="utf-8")
INDEX_TEMPLATE = (ROOT / "templates/index.html").read_text(encoding="utf-8")
LEDGER_TEMPLATE = (ROOT / "templates/ledger_prints.html").read_text(encoding="utf-8")


def source_between(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


def test_trade_builder_contract_create_edit_delete_use_flask_api():
    add_source = source_between(APP_JS, "async function addContractItem(", "async function editContractItem(")
    edit_source = source_between(APP_JS, "async function editContractItem(", "async function deleteContractItem(")
    delete_source = source_between(APP_JS, "async function deleteContractItem(", "function renderTrade(")

    assert "`/api/trades/${encodeURIComponent(tradeId)}/contract-items" in add_source
    assert "`/api/trades/${encodeURIComponent(tradeId)}/contract-items" in edit_source
    assert "`/api/trades/${encodeURIComponent(tradeId)}/contract-items" in delete_source
    assert "updateDoc(" not in add_source + edit_source + delete_source
    assert "setDoc(" not in add_source + edit_source + delete_source
    assert "showStatus(\n            \"Contract item added.\"" in add_source
    assert "Unable to add the contract item. Please try again." in add_source
    assert "error.safeApiMessage = true;" in APP_JS
    assert "error.safeApiMessage ? error.message" in add_source
    assert "Contract item updated." in edit_source
    assert "Unable to update the contract item. Please try again." in edit_source
    assert "error.safeApiMessage ? error.message" in edit_source
    assert "Contract item deleted." in delete_source
    assert "Unable to delete the contract item. Please try again." in delete_source


def test_contract_mutations_update_the_cached_trade_without_reloading_firestore():
    add_source = source_between(APP_JS, "async function addContractItem(", "async function editContractItem(")
    edit_source = source_between(APP_JS, "async function editContractItem(", "async function deleteContractItem(")
    delete_source = source_between(APP_JS, "async function deleteContractItem(", "function renderTrade(")

    for mutation_source in (add_source, edit_source, delete_source):
        assert "renderUpdatedContractTrade(tradeId, result.contractItems)" in mutation_source
        assert "await loadTrades()" not in mutation_source

    assert "const trade = tradesById.get(tradeId);" in edit_source
    assert "getDoc(" not in edit_source
    assert "trade.contractItems = contractItems;" in APP_JS
    assert "existingCard.replaceWith(card);" in APP_JS


def test_financial_only_page_permission_gates_contract_edit_controls():
    contract_table = source_between(APP_JS, "function renderContractTable(", "async function addContractItem(")
    render_trade = source_between(APP_JS, "function renderTrade(", "async function saveTrade(")

    assert 'document.body.dataset.canEditContractValues === "true"' in APP_JS
    assert "canEditContractValues ? `<td" in contract_table
    assert '${canEditContractValues ? `<button' in render_trade
    assert 'card.querySelector(\n        ".add-contract-btn"\n    )?.addEventListener' in render_trade


def test_trade_save_does_not_rewrite_contract_or_payment_arrays():
    save_source = source_between(APP_JS, "async function saveTrade()", "function editTrade(")

    assert "sendTradeMutation(" in save_source
    assert "`/api/trades/${encodeURIComponent(editingTradeId)}`" in save_source
    assert '"/api/trades"' in save_source
    assert "updateDoc(" not in save_source
    assert "setDoc(" not in save_source
    assert "contractItems:\n                oldContractItems" not in save_source
    assert "paymentTransactions:\n                oldTransactions" not in save_source


def test_ledger_contract_editor_submits_to_flask_not_firestore_contract_write():
    save_source = source_between(LEDGER_JS, "async function saveLedgerEdits(", "async function loadTrades(")

    assert "`/api/trades/${encodeURIComponent(selectedTrade.id)}/contract-items`" in save_source
    assert 'method: "PUT"' in save_source
    assert 'body: JSON.stringify({ contractItems })' in save_source
    assert "contractItems, updatedAt:" not in save_source
    assert '`/api/trades/${encodeURIComponent(selectedTrade.id)}/name`' in save_source
    assert "updateDoc(" not in save_source
    assert "X-CSRF-Token" in save_source
    assert 'showStatus("Ledger updated successfully.")' in save_source
    assert '"Unable to save the ledger. Please try again."' in save_source
    assert "error?.safeApiMessage ? error.message" in save_source
    assert 'id: row.querySelector("[data-contract-id]").value || undefined' in save_source
    assert 'data-contract-id value="${escapeHtml(item.id || "")}"' in LEDGER_JS


def test_trade_builder_and_ledger_pages_expose_contract_csrf_token():
    assert 'data-contract-values-csrf="{{ contract_values_csrf }}"' in INDEX_TEMPLATE
    assert 'data-can-edit-contract-values="{{ \'true\' if can_edit_contract_values else \'false\' }}"' in INDEX_TEMPLATE
    assert '<body data-contract-values-csrf="{{ contract_values_csrf }}" data-trade-builder-csrf="{{ trade_builder_csrf }}" data-can-edit-ledger=' in LEDGER_TEMPLATE


def test_trade_mutations_read_csrf_from_the_element_that_exposes_it():
    helper = source_between(APP_JS, "async function sendTradeMutation(", "\nfunction ")

    assert 'document.querySelector("[data-trade-builder-csrf]")' in helper
    assert '"X-CSRF-Token": csrfToken' in helper
    assert 'data-trade-builder-csrf="{{ trade_builder_csrf }}"' in INDEX_TEMPLATE


def test_contract_api_helper_sends_csrf_and_surfaces_failure():
    helper = source_between(
        APP_JS,
        "async function sendContractValueMutation(",
        "/* =========================================================\n   CONTRACT TOTAL",
    )
    assert 'credentials: "same-origin"' in helper
    assert '"X-CSRF-Token": csrfToken' in helper
    assert "if (!response.ok)" in helper
    assert "error.safeApiMessage = true;" in helper
    assert "result.contractItems" in helper


def test_trade_builder_and_ledger_metadata_writes_use_flask_only():
    assert "setDoc(" not in APP_JS
    assert "updateDoc(" not in APP_JS
    assert "deleteDoc(" not in APP_JS
    assert "setDoc(" not in LEDGER_JS
    assert "updateDoc(" not in LEDGER_JS
    assert "deleteDoc(" not in LEDGER_JS
    assert '"/api/trades"' in APP_JS
    assert '`/api/trades/${encodeURIComponent(tradeId)}`' in APP_JS
    assert '`/api/trades/${encodeURIComponent(selectedTrade.id)}/name`' in LEDGER_JS
