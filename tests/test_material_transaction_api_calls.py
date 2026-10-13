from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = (ROOT / "static/materials.js").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "templates/materials.html").read_text(encoding="utf-8")


def section(start, end):
    return SOURCE[SOURCE.index(start):SOURCE.index(end, SOURCE.index(start))]


def test_all_purchase_and_consumption_mutations_call_flask_api():
    purchase = section("async function savePurchase(event)", "async function saveConsumption")
    consumption = section("async function saveConsumption(event)", "async function deleteTransaction")
    deletion = section("async function deleteTransaction(", "function handleTransactionAction")

    assert 'sendMaterialTransaction("purchases", fields.id, "PATCH", payload)' in purchase
    assert 'sendMaterialTransaction(\n        "purchases",\n        "",\n        "POST",\n        payload,' in purchase
    assert 'sendMaterialTransaction("consumptions", fields.id, "PATCH", payload)' in consumption
    assert 'sendMaterialTransaction(\n        "consumptions",\n        "",\n        "POST",\n        payload,' in consumption
    assert 'sendMaterialTransaction(kind, transaction.id, "DELETE")' in deletion
    assert 'delete-purchase": purchase => deleteTransaction("purchases"' in SOURCE
    assert 'delete-consumption": consumption => deleteTransaction("consumptions"' in SOURCE


def test_transaction_mutations_no_longer_use_browser_firestore_writes():
    purchase = section("async function savePurchase(event)", "async function saveConsumption")
    consumption = section("async function saveConsumption(event)", "async function deleteTransaction")
    deletion = section("async function deleteTransaction(", "function handleTransactionAction")

    for mutation_source in (purchase, consumption, deletion):
        assert "addDoc(" not in mutation_source
        assert "updateDoc(" not in mutation_source
        assert "deleteDoc(" not in mutation_source
    for mutation in ("addDoc(", "setDoc(", "updateDoc(", "deleteDoc(", "writeBatch("):
        assert mutation not in SOURCE
    assert 'sendMaterialMasterMutation("/api/material-master/categories"' in SOURCE
    assert 'sendMaterialMasterMutation("/api/material-master/suppliers"' in SOURCE


def test_master_data_mutations_are_api_based_and_operational_api_is_unchanged():
    category = section("async function saveCategory(event)", "async function deleteCategory")
    category_delete = section("async function deleteCategory(", "async function saveSupplier")
    supplier = section("async function saveSupplier(event)", "async function viewSupplier")
    supplier_delete = section("async function deleteSupplier(", "function closeDetailsDialog")

    assert "/api/material-master/categories" in category
    assert "/api/material-master/categories" in category_delete
    assert "/api/material-master/suppliers" in supplier
    assert "/api/material-master/suppliers" in supplier_delete
    assert "sendMaterialTransaction(" in SOURCE
    assert '`/api/material-transactions/${kind}' in SOURCE


def test_material_api_uses_session_csrf_idempotency_and_refreshes_data():
    helper = section("async function sendMaterialTransaction(", "async function saveCategory")
    purchase = section("async function savePurchase(event)", "async function saveConsumption")
    consumption = section("async function saveConsumption(event)", "async function deleteTransaction")

    assert "`/api/material-transactions/${kind}" in helper
    assert '"X-CSRF-Token": document.body.dataset.materialTransactionsCsrf || ""' in helper
    assert 'credentials: "same-origin"' in helper
    assert 'headers["Idempotency-Key"] = requestId' in helper
    assert "if (!response.ok)" in helper
    assert 'data-material-transactions-csrf="{{ material_transactions_csrf }}"' in TEMPLATE
    assert "await loadData();" in purchase
    assert "await loadData();" in consumption


def test_delete_confirmation_and_failed_api_keep_existing_error_behavior():
    deletion = section("async function deleteTransaction(", "function handleTransactionAction")

    assert "await showConfirmDialog({" in deletion
    assert deletion.index("await sendMaterialTransaction(") < deletion.index('await showSuccess(`${label} deleted.`')
    assert deletion.index('await showSuccess(`${label} deleted.`') < deletion.index("await loadData();")
    assert "Unable to delete material ${label.toLocaleLowerCase()}" in deletion
    helper = section("async function sendMaterialTransaction(", "async function saveCategory")
    assert "if (!response.ok)" in helper
    assert "throw new Error(result.error" in helper
