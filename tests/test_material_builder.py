from pathlib import Path

import pytest

from app import app

ROOT = Path(__file__).parents[1]


class FakeUserStore:
    def __init__(self, role="office_staff"):
        self.user = {
            "id": "material-user",
            "username": "material-user",
            "role": role,
            "active_sessions": ["material-session"],
        }

    def get_user(self, user_id):
        return self.user if user_id == self.user["id"] else None


@pytest.fixture

def client(monkeypatch):
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SECRET_KEY", "material-builder-test")
    monkeypatch.setitem(app.config, "FIRESTORE_STORE", FakeUserStore())
    return app.test_client()


def login(client, role="office_staff"):
    app.config["FIRESTORE_STORE"] = FakeUserStore(role)
    with client.session_transaction() as session:
        session["user_id"] = "material-user"
        session["session_id"] = "material-session"


def test_material_builder_requires_login_and_allows_existing_operational_roles(client):
    unauthenticated = client.get("/materials")
    assert unauthenticated.status_code == 401
    assert b"You need to sign in to view this page." in unauthenticated.data
    assert b'href="/login?next=/materials"' in unauthenticated.data
    for role in ("office_staff", "supervisor", "super_admin", "Site engineer"):
        login(client, role)
        response = client.get("/materials")
        assert response.status_code == 200
        assert b"Material Builder" in response.data


def test_dashboard_has_material_builder_card():
    dashboard = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")
    assert 'href="/materials"' in dashboard
    assert "Material Builder" in dashboard


def test_dashboard_cards_stretch_and_pin_actions_to_the_bottom():
    dashboard = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")
    assert "align-items:\n                stretch;" in dashboard
    card_content = dashboard.split(".card-content {", 1)[1].split("}", 1)[0]
    card_button = dashboard.split(".card-button {", 1)[1].split("}", 1)[0]
    assert "display:\n                flex;" in card_content
    assert "flex-direction:\n                column;" in card_content
    assert "flex:\n                1;" in card_content
    assert "margin-top:\n                auto;" in card_button


def test_material_builder_uses_expected_collections_and_operations():
    source = (ROOT / "static/materials.js").read_text(encoding="utf-8")
    for collection_name in (
        "material_categories",
        "materials",
        "material_brands",
        "suppliers",
        "material_purchases",
        "material_consumptions",
    ):
        assert f'"{collection_name}"' in source
    for operation in ("getDocs(", "collection(db, COLLECTIONS.categories)"):
        assert operation in source
    for operation in ("addDoc(", "setDoc(", "updateDoc(", "deleteDoc(", "writeBatch("):
        assert operation not in source


def test_categories_store_units_and_category_scoped_brands():
    source = (ROOT / "static/materials.js").read_text(encoding="utf-8")
    template = (ROOT / "templates/materials.html").read_text(encoding="utf-8")
    assert 'name="categoryId"' in template
    assert 'name="unit" required' in source
    assert 'name="brandName"' in source
    assert 'A category with this name already exists.' in source
    assert "function categoryBrands(category)" in source
    assert 'brands: categoryBrands' in source
    assert "const categoryHasTransactions = category && (" in source
    assert "function createCategoryBrandId()" in source
    assert "cryptoApi?.randomUUID" in source
    assert "cryptoApi?.getRandomValues" in source
    assert 'Unable to save material category:' in source
    assert 'if (saving) return;' in source


def test_supplier_fields_mapping_and_relationship_safety_are_present():
    source = (ROOT / "static/materials.js").read_text(encoding="utf-8")
    supplier_validation = source[
        source.index("async function saveSupplier("):
        source.index("async function viewSupplier(", source.index("async function saveSupplier("))
    ]
    for field_name in (
        "contactPerson", "address", "city", "state", "pinCode", "primaryPhone",
        "secondaryPhone", "whatsappNumber", "email", "gstNumber", "notes",
    ):
        assert f'name="{field_name}"' in source
    assert "function associationId(materialId, supplierId)" not in source
    assert "material_supplier_associations" not in source
    assert "Category in use" in source
    assert "categoryBrandAssignments" in source
    assert "purchase rate" not in source.lower()
    assert 'name="categoryIds"' in source
    assert 'name="supplierBrandIds"' in source
    assert "function supplierProvidesBrand(supplier, categoryId, brandId)" in source
    assert 'categoryIds,' in source
    assert "Select Categories" in source
    assert "Select Brands / Quarries" in source
    assert "supplier-category-option${selectedCategories.has(category.id) ? \" is-selected\" : \"\"}" in source
    assert 'brandIds: [...new Set(' in source
    assert "options.length > 0 && !assignment.brandIds.length" in supplier_validation
    assert "Category only (no brand or quarry restriction)" in source
    assert "error?.safeApiMessage ? error.message" in supplier_validation
    assert "updatePurchaseSupplierOptions(true);" in source


def test_material_entities_have_no_status_controls_or_visibility_gates():
    template = (ROOT / "templates/materials.html").read_text(encoding="utf-8")
    source = (ROOT / "static/materials.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "static/materials.css").read_text(encoding="utf-8")
    for removed_status_reference in (
        "categoryStatusFilter", "supplierStatusFilter", "category.status", "supplier.status",
        "fields.status", "material-status",
    ):
        assert removed_status_reference not in template + source + stylesheet
    assert 'name="status"' not in source
    assert 'const filtered = categories.filter(category => !query ||' in source
    assert "return !query || searchable.includes(query);" in source
    assert "${categories.map(category =>" in source
    assert "supplierProvidesBrand(supplier, categoryId, brandId)" in source


def test_category_and_supplier_create_edit_paths_keep_crud_without_status_fields():
    source = (ROOT / "static/materials.js").read_text(encoding="utf-8")
    category_save = source.split("async function saveCategory(event)", 1)[1].split("async function deleteCategory", 1)[0]
    supplier_save = source.split("async function saveSupplier(event)", 1)[1].split("async function viewSupplier", 1)[0]
    assert '"/api/material-master/categories"' in category_save
    assert '`/api/material-master/categories/${encodeURIComponent(fields.id)}`' in category_save
    assert '"/api/material-master/suppliers"' in supplier_save
    assert '`/api/material-master/suppliers/${encodeURIComponent(fields.id)}`' in supplier_save
    assert "updateDoc(" not in category_save + supplier_save
    assert "addDoc(" not in category_save + supplier_save
    assert "fields.status" not in category_save
    assert "status:" not in category_save.lower()
    assert "fields.status" not in supplier_save
    assert "status:" not in supplier_save.lower()


def test_brands_are_managed_inside_categories_with_legacy_compatibility():
    template = (ROOT / "templates/materials.html").read_text(encoding="utf-8")
    source = (ROOT / "static/materials.js").read_text(encoding="utf-8")
    assert 'id="brandList"' not in template
    assert 'id="createBrandButton"' not in template
    assert 'id="categoryBrandRows"' in source
    assert 'name="brandName"' in source
    assert "function categoryBrands(category)" in source
    assert "legacyMaterialBrands" in source
    assert "async function saveBrand(event)" not in source


def test_active_master_data_mutations_use_flask_and_brand_fields_are_embedded():
    source = (ROOT / "static/materials.js").read_text(encoding="utf-8")
    assert "async function sendMaterialMasterMutation(" in source
    assert "COLLECTIONS.materials" in source
    assert "COLLECTIONS.brands" in source
    assert 'updateDoc(doc(db, COLLECTIONS.categories' not in source
    assert 'addDoc(collection(db, COLLECTIONS.categories' not in source
    assert 'deleteDoc(doc(db, COLLECTIONS.categories' not in source
    assert 'updateDoc(doc(db, COLLECTIONS.suppliers' not in source
    assert 'addDoc(collection(db, COLLECTIONS.suppliers' not in source
    assert 'deleteDoc(doc(db, COLLECTIONS.suppliers' not in source
    assert 'name: brand.name' in source
    assert 'brands: categoryBrands' in source


def test_material_details_do_not_invent_stock_without_receipt_or_consumption_data():
    source = (ROOT / "static/materials.js").read_text(encoding="utf-8")
    logic = (ROOT / "static/materials_logic.mjs").read_text(encoding="utf-8")
    assert "function transactionCategoryId(transaction)" in source
    assert "function stockTotals(category)" in source
    assert "calculateStockTotals(category, purchases, consumptions, categoryUnit(category), transactionCategoryId)" in source
    assert "transactionCategoryId(item) === category.id" in logic
    assert 'available: purchased - consumed' in logic
    assert '"material_purchases"' in source
    assert '"material_consumptions"' in source
    assert "function renderStock()" in source
    assert "const cementCategories = categories.filter(isCementCategory);" in source
    assert "isCementCategory(category)" in logic
    assert "This category is used by saved materials, supplier assignments, or transactions." in source


def test_investment_summary_uses_unique_stored_purchase_totals_and_category_names():
    template = (ROOT / "templates/materials.html").read_text(encoding="utf-8")
    source = (ROOT / "static/materials.js").read_text(encoding="utf-8")
    logic = (ROOT / "static/materials_logic.mjs").read_text(encoding="utf-8")
    panel = template.split('<section id="materialsPanel"', 1)[1]
    assert 'id="investmentSummary"' in panel
    assert panel.index('id="investmentSummary"') < panel.index('class="materials-subtabs"')
    assert "function calculatePurchaseInvestment()" in source
    assert "new Map()" in logic
    assert "Number(purchase.totalAmount)" in logic
    assert "transactionCategoryId(purchase)" in logic
    assert 'investment.categories.map(category =>' in source
    assert "investment.cementTotal" not in source
    assert "investment.otherTotal" not in source
    stylesheet = (ROOT / "static/materials.css").read_text(encoding="utf-8")
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in stylesheet
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in stylesheet
    assert "grid-template-columns: 1fr" in stylesheet
    assert 'new Intl.NumberFormat("en-IN"' in source
    assert "Total Material Investment" in source
    assert 'investment.categories.map(category =>' in source
    assert source.index("renderInvestmentSummary();") > source.index("purchases = purchaseSnapshot.docs.map")


def test_consumption_entry_is_only_available_for_cement():
    template = (ROOT / "templates/materials.html").read_text(encoding="utf-8")
    source = (ROOT / "static/materials.js").read_text(encoding="utf-8")
    assert 'id="consumptionUnavailable"' in template
    assert 'id="consumptionForm"' in template
    assert 'formId === "consumptionForm" ? categories.filter(isCementCategory) : categories' in source
    assert '$("consumptionForm").hidden = !hasCement;' in source
    assert 'if (!isCementCategory(category))' in source
    assert 'isCementCategory(categories.find(category => category.id === transactionCategoryId(consumption)))' in source


def test_purchase_and_consumption_forms_cover_requested_fields_and_crud():
    template = (ROOT / "templates/materials.html").read_text(encoding="utf-8")
    source = (ROOT / "static/materials.js").read_text(encoding="utf-8")
    for field in ("date", "categoryId", "brandId", "supplierId", "quantity", "unit", "rate", "totalAmount", "billLink"):
        assert f'name="{field}"' in template
    for field in ("date", "categoryId", "quantity", "workLocation", "remarks"):
        assert f'name="{field}"' in template
    consumption_form = template.split('<form id="consumptionForm"', 1)[1].split("</form>", 1)[0]
    assert 'name="unit"' not in consumption_form
    assert 'name="supplierId"' not in consumption_form
    assert 'name="brand"' not in consumption_form
    assert 'name="brandId"' not in consumption_form
    assert 'data-panel="purchasesPanel"' in template
    assert 'data-panel="consumptionPanel"' in template
    assert "calculateCementPurchaseTotal(quantity, rate)" in source
    assert 'getDocs(collection(db, COLLECTIONS.purchases))' in source
    assert 'getDocs(collection(db, COLLECTIONS.consumptions))' in source
    purchase_save = source.split("async function savePurchase(event)", 1)[1].split("async function saveConsumption", 1)[0]
    consumption_save = source.split("async function saveConsumption(event)", 1)[1].split("async function deleteTransaction", 1)[0]
    transaction_delete = source.split("async function deleteTransaction(", 1)[1].split("function handleTransactionAction", 1)[0]
    assert 'sendMaterialTransaction("purchases", fields.id, "PATCH", payload)' in purchase_save
    assert 'sendMaterialTransaction(\n        "purchases",\n        "",\n        "POST",\n        payload,' in purchase_save
    assert 'sendMaterialTransaction("consumptions", fields.id, "PATCH", payload)' in consumption_save
    assert 'sendMaterialTransaction(\n        "consumptions",\n        "",\n        "POST",\n        payload,' in consumption_save
    assert 'sendMaterialTransaction(kind, transaction.id, "DELETE")' in transaction_delete
    assert "updateDoc(doc(db, COLLECTIONS.purchases" not in purchase_save
    assert "addDoc(collection(db, COLLECTIONS.purchases" not in purchase_save
    assert "updateDoc(doc(db, COLLECTIONS.consumptions" not in consumption_save
    assert "addDoc(collection(db, COLLECTIONS.consumptions" not in consumption_save
    assert "deleteDoc(doc(db, collectionName" not in transaction_delete
    assert "categoryId: category.id" in source
    assert "brand: brand?.name" in source
    assert "supplierProvidesBrand(supplier, category.id, brand.id)" in source
    assert "function updatePurchaseBrandOptions(keepCurrent = false)" in source
    assert "legacy-purchase-${priorPurchase.id}" in source
    assert 'name="brand"' not in consumption_form
    assert "const totals = stockTotals(category);" in source
    assert "if (quantity > available + 1e-9)" in source
    assert "unit: categoryUnit(category)" in source
    assert 'id="purchaseAmountField"' in template
    assert 'name="amount" type="number" min="0" step="any" required' not in template
    assert "function updatePurchaseFields()" in source
    assert 'form.elements.quantity.required = cement;' in source
    assert 'form.elements.rate.required = cement;' in source
    assert 'form.elements.amount.required = !cement;' in source
    assert 'form.elements.brandId.required = cement;' in source
    assert '$("purchaseBrandField").hidden = !cement;' in source
    assert "calculateCementPurchaseTotal(quantity, rate)" in source


def test_transaction_ui_omits_server_derived_category_name():
    source = (ROOT / "static/materials.js").read_text(encoding="utf-8")
    purchase_save = source.split("async function savePurchase(event)", 1)[1].split(
        "async function saveConsumption", 1
    )[0]
    consumption_save = source.split("async function saveConsumption(event)", 1)[1].split(
        "async function deleteTransaction", 1
    )[0]

    for save_function in (purchase_save, consumption_save):
        payload = save_function.split("const payload = {", 1)[1].split("\n    };", 1)[0]
        assert "categoryName" not in payload
        assert "categoryId: category.id" in payload


def test_purchase_brand_is_only_required_for_cement_and_legacy_values_are_preserved():
    template = (ROOT / "templates/materials.html").read_text(encoding="utf-8")
    source = (ROOT / "static/materials.js").read_text(encoding="utf-8")
    purchase_save = source.split("async function savePurchase(event)", 1)[1].split("async function saveConsumption", 1)[0]
    purchase_edit = source.split("function editPurchase(purchase)", 1)[1].split("function editConsumption", 1)[0]
    purchase_history = source.split("function renderPurchases()", 1)[1].split("function renderConsumptions", 1)[0]

    assert 'id="purchaseBrandField"' in template
    assert 'name="brandId" required' not in template
    assert "(cementPurchase && !brand)" in purchase_save
    assert 'brand: brand?.name || (priorPurchase && transactionCategoryId(priorPurchase) === category.id ? priorPurchase.brand || "" : "")' in purchase_save
    assert "updatePurchaseFields();" in purchase_edit
    assert "supplierProvidesCategory(supplier, categoryId)" in source
    assert 'isCementPurchase ? brand : ""' in purchase_history
    assert 'form.elements.amount.value = form.elements.totalAmount.value' not in source


def test_purchase_supplier_is_optional_for_new_and_existing_purchases():
    template = (ROOT / "templates/materials.html").read_text(encoding="utf-8")
    source = (ROOT / "static/materials.js").read_text(encoding="utf-8")
    purchase_form = template.split('<form id="purchaseForm"', 1)[1].split("</form>", 1)[0]
    purchase_save = source.split("async function savePurchase(event)", 1)[1].split("async function saveConsumption", 1)[0]
    purchase_edit = source.split("function editPurchase(purchase)", 1)[1].split("function editConsumption", 1)[0]
    purchase_history = source.split("function renderPurchases()", 1)[1].split("function renderConsumptions", 1)[0]

    assert '<label>Supplier<select name="supplierId">' in purchase_form
    assert 'name="supplierId" required' not in purchase_form
    assert "(fields.supplierId && !supplier)" in purchase_save
    assert 'supplierId: supplier?.id || ""' in purchase_save
    assert 'supplierName: supplier?.name || ""' in purchase_save
    assert 'form.elements.supplierId.value = purchase.supplierId || "";' in purchase_edit
    assert 'purchase.supplierName || "Walk-in / Unregistered Supplier"' in purchase_history
    assert "addDoc(collection(db, COLLECTIONS.suppliers)" not in purchase_save
    assert "updatePurchaseFields();" in purchase_edit


def test_dashboard_and_route_do_not_require_super_admin_only():
    from app import ROLE_PERMISSIONS

    assert "materials" in ROLE_PERMISSIONS["office_staff"]
    assert "materials" in ROLE_PERMISSIONS["supervisor"]
    route_source = (ROOT / "app.py").read_text(encoding="utf-8")
    material_route = route_source.split('def material_builder():', 1)[0].rsplit('@app.route("/materials")', 1)[1]
    assert "@require_login" in material_route
    assert "@require_role" not in material_route
