import {
  describeFirebaseError,
  firebaseAuthReady,
  getFirebaseApp,
  getFirebaseFirestoreSdk
} from "./firebase_auth.js";
import { registerEscClose, showConfirmDialog, showError, showSuccess, unregisterEscClose } from "./dialogs.js";
import { calculateCementPurchaseTotal } from "./materials_logic.mjs";
import { calculatePurchaseInvestment as calculatePurchaseInvestmentTotals, calculateStockTotals, isCementCategory } from "./materials_logic.mjs";
import { formatDate } from "./date_format.js";

const COLLECTIONS = {
  categories: "material_categories",
  materials: "materials",
  brands: "material_brands",
  suppliers: "suppliers",
  purchases: "material_purchases",
  consumptions: "material_consumptions"
};
let db = null;
let firestoreSdk = null;
let firebaseBootstrapError = null;
try {
  await firebaseAuthReady;
  firestoreSdk = await getFirebaseFirestoreSdk();
  db = firestoreSdk.getFirestore(await getFirebaseApp());
} catch (error) {
  firebaseBootstrapError = error;
  console.error("Firebase initialization failed:", error?.stage || error?.code || error?.name);
}
const firestoreMethod = name => (...args) => {
  const method = firestoreSdk?.[name];
  if (typeof method !== "function") {
    throw firebaseBootstrapError || new Error("The Firebase Firestore SDK is unavailable.");
  }
  return method(...args);
};
const collection = firestoreMethod("collection");
const getDocs = firestoreMethod("getDocs");
const $ = id => document.getElementById(id);
const status = $("materialsStatus");
const entityDialog = $("materialEntityDialog");
const entityForm = $("entityForm");
const detailsDialog = $("materialDetailsDialog");
let categories = [];
let materials = [];
let brands = [];
let suppliers = [];
let purchases = [];
let consumptions = [];
let saving = false;
let brandIdSequence = 0;
let pendingCategoryCreate = null;
let pendingSupplierCreate = null;
const materialTransactionsCsrf = document.body.dataset.materialTransactionsCsrf || "";

function createMaterialMasterRequestId() {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === "function") return cryptoApi.randomUUID();
  if (typeof cryptoApi?.getRandomValues === "function") {
    const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  throw new Error("Secure master-data request IDs are unavailable.");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[character]));
}

function normalized(value) {
  return String(value ?? "").trim().toLocaleLowerCase();
}

function createCategoryBrandId() {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === "function") return cryptoApi.randomUUID();
  if (typeof cryptoApi?.getRandomValues === "function") {
    const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
    return `brand-${Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("")}`;
  }
  brandIdSequence += 1;
  return `brand-${Date.now().toString(36)}-${brandIdSequence.toString(36)}`;
}

function setStatus(message, isError = false) {
  status.textContent = message;
  status.className = `drawings-status${isError ? " error" : ""}`;
}

function escapeAttribute(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}

function categoryName(categoryId) {
  return categories.find(item => item.id === categoryId)?.name || "Uncategorized";
}

function legacyMaterialBrands(categoryId) {
  const materialIds = new Set(materials.filter(material => material.categoryId === categoryId).map(material => material.id));
  const legacyNames = [
    ...brands.filter(brand => materialIds.has(brand.materialId)).map(brand => ({ id: `legacy-brand-${brand.id}`, name: brand.name })),
    ...materials.filter(material => material.categoryId === categoryId && material.brand).map(material => ({ id: `legacy-material-${material.id}`, name: material.brand })),
    ...purchases.filter(purchase => transactionCategoryId(purchase) === categoryId && purchase.brand).map(purchase => ({ id: `legacy-purchase-${purchase.id}`, name: purchase.brand }))
  ];
  const unique = new Map();
  for (const brand of legacyNames) {
    const key = normalized(brand.name);
    if (key && !unique.has(key)) unique.set(key, { ...brand, name: String(brand.name).trim() });
  }
  return [...unique.values()].sort((left, right) => left.name.localeCompare(right.name));
}

function categoryBrands(category) {
  if (Array.isArray(category.brands)) return category.brands;
  return legacyMaterialBrands(category.id);
}

function categoryBrandAssignments(supplier) {
  if (Array.isArray(supplier.categoryBrandAssignments)) return supplier.categoryBrandAssignments;
  return (Array.isArray(supplier.categoryIds) ? supplier.categoryIds : []).map(categoryId => ({
    categoryId,
    brandIds: categoryBrands(categories.find(item => item.id === categoryId) || { id: categoryId }).map(brand => brand.id),
    legacyCategoryAssignment: true
  }));
}

function supplierCategoryIds(supplier) {
  return categoryBrandAssignments(supplier).map(assignment => assignment.categoryId);
}

function supplierProvidesBrand(supplier, categoryId, brandId) {
  const assignment = categoryBrandAssignments(supplier).find(item => item.categoryId === categoryId);
  return Boolean(assignment && (assignment.legacyCategoryAssignment || assignment.brandIds.includes(brandId)));
}

function supplierProvidesCategory(supplier, categoryId) {
  return categoryBrandAssignments(supplier).some(assignment => assignment.categoryId === categoryId);
}

function transactionCategoryId(transaction) {
  if (transaction.categoryId) return transaction.categoryId;
  return materials.find(material => material.id === transaction.materialId)?.categoryId || "";
}

function categoryUnit(category) {
  if (String(category?.unit || "").trim()) return String(category.unit).trim();
  const units = new Map();
  for (const material of materials.filter(item => item.categoryId === category?.id)) {
    const unit = String(material.unit || "").trim();
    if (unit) units.set(normalized(unit), unit);
  }
  for (const record of [...purchases, ...consumptions].filter(item => transactionCategoryId(item) === category?.id)) {
    const unit = String(record.unit || "").trim();
    if (unit) units.set(normalized(unit), unit);
  }
  return units.size === 1 ? [...units.values()][0] : "";
}

function supplierLocation(supplier) {
  return [supplier.city, supplier.state, supplier.pinCode].filter(Boolean).join(", ") || supplier.address || "Location not provided";
}

function stockTotals(category) {
  return calculateStockTotals(category, purchases, consumptions, categoryUnit(category), transactionCategoryId);
}

function formatQuantity(value) {
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 3 });
}

function formatAmount(value) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 0,
    maximumFractionDigits: 2
  }).format(Number(value || 0));
}

function calculatePurchaseInvestment() {
  return calculatePurchaseInvestmentTotals(categories, purchases, transactionCategoryId);
}

function renderInvestmentSummary() {
  const investment = calculatePurchaseInvestment();
  $("investmentSummary").innerHTML = `
    <div class="investment-total">
      <span>Total Material Investment</span>
      <strong>${escapeHtml(formatAmount(investment.total))}</strong>
    </div>
    <div class="investment-category-totals" aria-label="Investment by category">
      ${investment.categories.map(category => `<div class="investment-category-total"><span>${escapeHtml(category.name)}</span><strong>${escapeHtml(formatAmount(category.total))}</strong></div>`).join("")
        || '<div class="materials-empty">No material categories yet.</div>'}
    </div>`;
}

function stockSummary(material) {
  const totals = stockTotals(material);
  if (!totals) return '<div class="stock-summary">Stock unavailable: set one consistent unit for this category and its existing transactions.</div>';
  const unit = escapeHtml(totals.unit);
  const availableClass = totals.available < 0 ? " stock-negative" : "";
  return `<div class="stock-summary"><span>Purchased: <strong>${formatQuantity(totals.purchased)} ${unit}</strong></span><span>Consumed: <strong>${formatQuantity(totals.consumed)} ${unit}</strong></span><span class="${availableClass.trim()}">Available Stock: <strong>${formatQuantity(totals.available)} ${unit}</strong></span></div>`;
}

function renderEmpty(container, message) {
  container.innerHTML = `<div class="materials-empty">${escapeHtml(message)}</div>`;
}

function renderCategories() {
  const query = normalized($("categorySearch").value);
  const filtered = categories.filter(category => !query || `${category.name} ${category.description || ""}`.toLocaleLowerCase().includes(query));
  if (!categories.length) return renderEmpty($("categoryList"), "No material categories yet. Create a category to organize materials.");
  if (!filtered.length) return renderEmpty($("categoryList"), "No categories match the current search.");
  $("categoryList").innerHTML = filtered.map(category => `
    <article class="category-card material-item">
      <div class="material-item-header">
        <div><h2>${escapeHtml(category.name)}</h2></div>
        <div class="material-item-actions">
          <button type="button" class="drawings-action small" data-action="edit-category" data-id="${escapeAttribute(category.id)}">Edit</button>
          <button type="button" class="drawings-action small danger" data-action="delete-category" data-id="${escapeAttribute(category.id)}">Delete</button>
        </div>
      </div>
      <p class="material-item-description">${escapeHtml(category.description || "No description")}</p>
      <div class="material-summary"><span>Unit: ${escapeHtml(categoryUnit(category) || "Not set")}</span><span>Brands / Quarries: ${escapeHtml(categoryBrands(category).map(brand => brand.name).join(", ") || "None added")}</span></div>
    </article>`).join("");
}

function renderStock() {
  const cementCategories = categories.filter(isCementCategory);
  if (!cementCategories.length) return renderEmpty($("stockList"), "Create a Cement category to start tracking stock.");
  $("stockList").innerHTML = cementCategories.map(category => `
    <article class="category-card material-item">
      <div class="material-item-header">
        <div><h2>${escapeHtml(category.name)}</h2><span class="material-code">${escapeHtml(categoryUnit(category) || "Unit not set")}</span></div>
      </div>
      ${stockSummary(category)}
    </article>`).join("");
}

  function safeExternalUrl(value) {
    try {
      const parsed = new URL(value);
      return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "";
    } catch {
      return "";
    }
  }

  function renderPurchases() {
    if (!purchases.length) return renderEmpty($("purchaseList"), "No purchases recorded yet.");
    const ordered = [...purchases].sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")));
    $("purchaseList").innerHTML = ordered.map(purchase => {
      const billUrl = safeExternalUrl(purchase.billLink);
      const isCementPurchase = isCementCategory(categories.find(category => category.id === transactionCategoryId(purchase)) || { name: purchase.categoryName });
      const brand = purchase.brand || materials.find(item => item.id === purchase.materialId)?.brand || "";
      const details = [isCementPurchase ? brand : "", isCementPurchase ? `${formatQuantity(purchase.quantity)} ${purchase.unit || ""}`.trim() : ""].filter(Boolean).join(" · ");
      return `<article class="category-card material-item transaction-row">
        <div class="transaction-row-main"><strong>${escapeHtml(formatDate(purchase.date, "Undated"))}</strong><small>${escapeHtml(purchase.categoryName || categoryName(transactionCategoryId(purchase)))}</small></div>
        <div class="transaction-row-main"><strong>${escapeHtml(purchase.supplierName || "Walk-in / Unregistered Supplier")}</strong><small>${escapeHtml(details)}</small></div>
        <div class="transaction-row-main"><strong>${formatAmount(purchase.totalAmount)}</strong><small>${isCementPurchase ? `${formatAmount(purchase.rate)} / ${escapeHtml(purchase.unit || "unit")}` : "Purchase amount"}</small></div>
        <div class="transaction-row-main">${billUrl ? `<a href="${escapeAttribute(billUrl)}" target="_blank" rel="noopener noreferrer">Bill / Photo</a>` : "No bill link"}</div>
        <div class="material-item-actions"><button type="button" class="drawings-action small" data-action="edit-purchase" data-id="${escapeAttribute(purchase.id)}">Edit</button><button type="button" class="drawings-action small danger" data-action="delete-purchase" data-id="${escapeAttribute(purchase.id)}">Delete</button></div>
      </article>`;
    }).join("");
  }

  function renderConsumptions() {
    if (!consumptions.length) return renderEmpty($("consumptionList"), "No consumption recorded yet.");
    const ordered = [...consumptions].sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")));
    $("consumptionList").innerHTML = ordered.map(consumption => {
      const editable = isCementCategory(categories.find(category => category.id === transactionCategoryId(consumption)));
      return `
      <article class="category-card material-item transaction-row">
        <div class="transaction-row-main"><strong>${escapeHtml(formatDate(consumption.date, "Undated"))}</strong><small>${escapeHtml(consumption.categoryName || categoryName(transactionCategoryId(consumption)))}</small></div>
        <div class="transaction-row-main"><strong>${formatQuantity(consumption.quantity)} ${escapeHtml(consumption.unit || "")}</strong><small>${escapeHtml(consumption.workLocation || "No work/location")}</small></div>
        <div class="transaction-row-main"><strong>Remarks</strong><small>${escapeHtml(consumption.remarks || "-")}</small></div>
        <div></div>
          <div class="material-item-actions">${editable ? `<button type="button" class="drawings-action small" data-action="edit-consumption" data-id="${escapeAttribute(consumption.id)}">Edit</button>` : ""}<button type="button" class="drawings-action small danger" data-action="delete-consumption" data-id="${escapeAttribute(consumption.id)}">Delete</button></div>
        </article>`;
    }).join("");
  }

function renderSuppliers() {
  const query = normalized($("supplierSearch").value);
  const filtered = suppliers.filter(supplier => {
    const searchable = `${supplier.name} ${supplier.contactPerson || ""} ${supplier.primaryPhone} ${supplier.secondaryPhone || ""} ${supplier.whatsappNumber || ""} ${supplier.city || ""} ${supplier.state || ""} ${supplier.pinCode || ""} ${supplier.address || ""}`.toLocaleLowerCase();
    return !query || searchable.includes(query);
  });
  if (!suppliers.length) return renderEmpty($("supplierList"), "No suppliers yet. Add a supplier to build your directory.");
  if (!filtered.length) return renderEmpty($("supplierList"), "No suppliers match the current search.");
  $("supplierList").innerHTML = filtered.map(supplier => `
    <article class="category-card material-item">
      <div class="material-item-header">
        <div><h2>${escapeHtml(supplier.name)}</h2><span class="material-code">${escapeHtml(supplierLocation(supplier))}</span></div>
        <div class="material-item-actions">
          <button type="button" class="drawings-action small" data-action="view-supplier" data-id="${escapeAttribute(supplier.id)}">View</button>
          <button type="button" class="drawings-action small" data-action="edit-supplier" data-id="${escapeAttribute(supplier.id)}">Edit</button>
          <button type="button" class="drawings-action small danger" data-action="delete-supplier" data-id="${escapeAttribute(supplier.id)}">Delete</button>
        </div>
      </div>
      <div class="material-summary"><span>Contact: ${escapeHtml(supplier.contactPerson || "-")}</span><span>Phone: ${escapeHtml(supplier.primaryPhone)}</span><span>Email: ${escapeHtml(supplier.email || "-")}</span><span>Categories / Brands: ${escapeHtml(categoryBrandAssignments(supplier).map(assignment => `${categoryName(assignment.categoryId)}: ${assignment.brandIds.map(brandId => categoryBrands(categories.find(category => category.id === assignment.categoryId) || { id: assignment.categoryId }).find(brand => brand.id === brandId)?.name).filter(Boolean).join(", ")}`).join("; ") || "None assigned")}</span></div>
    </article>`).join("");
}

function categoryBrandRow(brandId = "", name = "") {
  return `<div class="category-brand-row">
    <input class="category-brand-name" data-brand-id="${escapeAttribute(brandId)}" name="brandName" maxlength="120" value="${escapeAttribute(name)}" aria-label="Brand or quarry name">
    <button type="button" class="drawings-action small danger" data-remove-category-brand aria-label="Remove brand or quarry">Remove</button>
  </div>`;
}

function renderCategoryBrandRows(category) {
  const entries = category ? categoryBrands(category) : [];
  return `<fieldset class="material-association-box">
    <legend>Brands / Quarries</legend>
    <div id="categoryBrandRows">${entries.map(brand => categoryBrandRow(brand.id, brand.name)).join("")}</div>
    <button type="button" class="drawings-secondary" data-add-category-brand>+ Add Brand / Quarry</button>
  </fieldset>`;
}

function renderSupplierAssignments(supplier = {}) {
  const selectedCategories = new Set(supplierCategoryIds(supplier));
  const assignments = categoryBrandAssignments(supplier);
  return `<div class="supplier-assignment-layout">
    <fieldset class="material-association-box supplier-assignment-listbox">
      <legend>Select Categories</legend>
      <div class="supplier-category-options" id="supplierCategoryOptions">
        ${categories.map(category => `
          <label class="supplier-category-option${selectedCategories.has(category.id) ? " is-selected" : ""}">
            <input class="supplier-category-checkbox" type="checkbox" name="categoryIds" value="${escapeAttribute(category.id)}" ${selectedCategories.has(category.id) ? "checked" : ""}>
            <span>${escapeHtml(category.name)}</span>
          </label>`).join("") || '<div class="materials-empty">No categories available.</div>'}
      </div>
    </fieldset>
    <fieldset class="material-association-box supplier-assignment-listbox">
      <legend>Select Brands / Quarries</legend>
      <div class="supplier-brand-category-list" id="supplierBrandOptions">
        ${categories.map(category => {
        const assignment = assignments.find(item => item.categoryId === category.id);
        const selectedBrands = new Set(assignment?.brandIds || []);
        const categorySelected = selectedCategories.has(category.id);
        return `<section class="supplier-brand-category" data-category-id="${escapeAttribute(category.id)}" ${categorySelected ? "" : "hidden"}>
          <h3>${escapeHtml(category.name)}</h3>
          <div class="supplier-brand-options">
            ${categoryBrands(category).map(brand => `<label class="supplier-brand-option">
              <input class="supplier-brand-checkbox" type="checkbox" name="supplierBrandIds" data-category-id="${escapeAttribute(category.id)}" value="${escapeAttribute(brand.id)}" ${selectedBrands.has(brand.id) ? "checked" : ""}>
              <span>${escapeHtml(brand.name)}</span>
            </label>`).join("") || '<span class="materials-no-suppliers">This category has no brands or quarries yet.</span>'}
          </div>
        </section>`;
      }).join("") || '<div class="materials-empty">No categories available.</div>'}
      </div>
    </fieldset>
  </div>`;
}

function openDialog() {
  entityDialog.showModal();
  registerEscClose(entityDialog, closeEntityDialog);
}

function closeEntityDialog() {
  unregisterEscClose(entityDialog);
  entityDialog.close();
  entityForm.replaceChildren();
}

function openCategoryForm(category = null) {
  $("entityDialogTitle").textContent = category ? "Edit Category" : "Create Category";
  const categoryHasTransactions = category && (
    purchases.some(item => transactionCategoryId(item) === category.id)
    || consumptions.some(item => transactionCategoryId(item) === category.id)
  );
  entityForm.innerHTML = `
    <input type="hidden" name="id" value="${escapeAttribute(category?.id || "")}">
    <label>Category Name *<input name="name" required maxlength="120" value="${escapeAttribute(category?.name || "")}"></label>
    <label>Unit *<input name="unit" required maxlength="40" placeholder="e.g. bags, tons, pieces" value="${escapeAttribute(category ? categoryUnit(category) : "")}" ${categoryHasTransactions ? "readonly aria-readonly=\"true\"" : ""}></label>
    ${categoryHasTransactions ? '<p class="materials-no-suppliers">Unit is locked because this category has transaction history.</p>' : ""}
    <label>Description<textarea name="description" rows="3" maxlength="800">${escapeHtml(category?.description || "")}</textarea></label>
    ${renderCategoryBrandRows(category)}
    <div class="material-form-actions"><button type="button" class="drawings-secondary" data-form-cancel>Cancel</button><button type="submit" class="drawings-primary">Save Category</button></div>`;
  entityForm.onsubmit = saveCategory;
  openDialog();
}

function openSupplierForm(supplier = null) {
  $("entityDialogTitle").textContent = supplier ? "Edit Supplier" : "Create Supplier";
  entityForm.innerHTML = `
    <input type="hidden" name="id" value="${escapeAttribute(supplier?.id || "")}">
    <div class="material-form-grid">
      <label>Supplier Name *<input name="name" required maxlength="160" value="${escapeAttribute(supplier?.name || "")}"></label>
      <label>Contact Person<input name="contactPerson" maxlength="120" value="${escapeAttribute(supplier?.contactPerson || "")}"></label>
      <label>Primary Phone Number *<input name="primaryPhone" type="tel" required maxlength="40" value="${escapeAttribute(supplier?.primaryPhone || "")}"></label>
      <label>Secondary Phone Number<input name="secondaryPhone" type="tel" maxlength="40" value="${escapeAttribute(supplier?.secondaryPhone || "")}"></label>
      <label>WhatsApp Number<input name="whatsappNumber" type="tel" maxlength="40" value="${escapeAttribute(supplier?.whatsappNumber || "")}"></label>
      <label>Email Address<input name="email" type="email" maxlength="254" value="${escapeAttribute(supplier?.email || "")}"></label>
      <label>City<input name="city" maxlength="100" value="${escapeAttribute(supplier?.city || "")}"></label>
      <label>State<input name="state" maxlength="100" value="${escapeAttribute(supplier?.state || "")}"></label>
      <label>PIN Code<input name="pinCode" inputmode="numeric" maxlength="20" value="${escapeAttribute(supplier?.pinCode || "")}"></label>
      <label>GST Number<input name="gstNumber" maxlength="40" value="${escapeAttribute(supplier?.gstNumber || "")}"></label>
    </div>
    ${renderSupplierAssignments(supplier || {})}
    <label>Shop / Business Address<textarea name="address" rows="2" maxlength="500">${escapeHtml(supplier?.address || "")}</textarea></label>
    <label>Notes<textarea name="notes" rows="3" maxlength="1000">${escapeHtml(supplier?.notes || "")}</textarea></label>
    <div class="material-form-actions"><button type="button" class="drawings-secondary" data-form-cancel>Cancel</button><button type="submit" class="drawings-primary">Save Supplier</button></div>`;
  entityForm.onsubmit = saveSupplier;
  openDialog();
}

function todayInputValue() {
  const today = new Date();
  const offset = today.getTimezoneOffset() * 60000;
  return new Date(today.getTime() - offset).toISOString().slice(0, 10);
}

function populateTransactionSelectors() {
  ["purchaseForm", "consumptionForm"].forEach(formId => {
    const form = $(formId);
    const selectedCategoryId = form.elements.categoryId.value;
    const availableCategories = formId === "consumptionForm" ? categories.filter(isCementCategory) : categories;
    form.elements.categoryId.innerHTML = '<option value="">Select category</option>' + availableCategories
      .map(category => `<option value="${escapeAttribute(category.id)}">${escapeHtml(category.name)}</option>`).join("");
    form.elements.categoryId.value = selectedCategoryId;
  });
  const hasCement = categories.some(isCementCategory);
  $("consumptionForm").hidden = !hasCement;
  $("consumptionUnavailable").hidden = hasCement;
  updatePurchaseFields();
  updatePurchaseBrandOptions(Boolean($("purchaseForm").elements.id.value));
  updatePurchaseSupplierOptions(Boolean($("purchaseForm").elements.id.value));
}

function updatePurchaseSupplierOptions(keepCurrent = false) {
  const form = $("purchaseForm");
  const selector = form.elements.supplierId;
  const selectedSupplierId = selector.value;
  const categoryId = form.elements.categoryId.value;
  const brandId = form.elements.brandId.value;
  const category = categories.find(item => item.id === categoryId);
  const priorPurchase = keepCurrent ? purchases.find(item => item.id === form.elements.id.value) : null;
  const eligible = suppliers.filter(supplier =>
    (isCementCategory(category)
      ? supplierProvidesBrand(supplier, categoryId, brandId)
      : supplierProvidesCategory(supplier, categoryId))
    || supplier.id === priorPurchase?.supplierId
  );
  selector.innerHTML = '<option value="">Select supplier</option>' + eligible
    .map(supplier => `<option value="${escapeAttribute(supplier.id)}">${escapeHtml(supplier.name)} · ${escapeHtml(supplier.primaryPhone)}</option>`).join("");
  selector.value = eligible.some(supplier => supplier.id === selectedSupplierId) ? selectedSupplierId : "";
}

function updatePurchaseBrandOptions(keepCurrent = false) {
  const form = $("purchaseForm");
  const selector = form.elements.brandId;
  const selectedBrandId = selector.value;
  const category = categories.find(item => item.id === form.elements.categoryId.value);
  const options = category ? [...categoryBrands(category)] : [];
  const priorPurchase = keepCurrent ? purchases.find(item => item.id === form.elements.id.value) : null;
  if (priorPurchase?.brand && !options.some(brand => normalized(brand.name) === normalized(priorPurchase.brand))) {
    options.push({ id: priorPurchase.brandId || `legacy-purchase-${priorPurchase.id}`, name: priorPurchase.brand });
  }
  selector.innerHTML = '<option value="">Select brand / quarry</option>'
    + options.map(brand => `<option value="${escapeAttribute(brand.id)}">${escapeHtml(brand.name)}</option>`).join("");
  const priorBrand = priorPurchase && options.find(brand => brand.id === priorPurchase.brandId || normalized(brand.name) === normalized(priorPurchase.brand));
  selector.value = options.some(brand => brand.id === selectedBrandId) ? selectedBrandId : (priorBrand?.id || "");
  if (category) form.elements.unit.value = categoryUnit(category);
  updatePurchaseSupplierOptions(keepCurrent);
}

function bindTransactionUnit(formId) {
  const form = $(formId);
  const category = categories.find(item => item.id === form.elements.categoryId.value);
  if (form.elements.unit) form.elements.unit.value = category ? categoryUnit(category) : "";
  if (formId === "purchaseForm") updatePurchaseFields();
}

function updatePurchaseAmount() {
  const form = $("purchaseForm");
  const quantity = Number(form.elements.quantity.value);
  const rate = Number(form.elements.rate.value);
  const totalAmount = calculateCementPurchaseTotal(quantity, rate);
  form.elements.totalAmount.value = totalAmount !== null && quantity >= 0 && rate >= 0
    ? totalAmount.toFixed(2)
    : "";
}

function updatePurchaseFields() {
  const form = $("purchaseForm");
  const category = categories.find(item => item.id === form.elements.categoryId.value);
  const cement = isCementCategory(category);
  $("purchaseQuantityField").hidden = !cement;
  $("purchaseUnitField").hidden = !cement;
  $("purchaseRateField").hidden = !cement;
  $("purchaseTotalAmountField").hidden = !cement;
  $("purchaseAmountField").hidden = cement;
  $("purchaseBrandField").hidden = !cement;
  form.elements.brandId.required = cement;
  form.elements.quantity.required = cement;
  form.elements.rate.required = cement;
  form.elements.amount.required = !cement;
  if (cement) updatePurchaseAmount();
}

function resetTransactionForm(formId) {
  const form = $(formId);
  form.reset();
  delete form.dataset.createRequestId;
  delete form.dataset.createRequestBody;
  form.elements.namedItem("id").value = "";
  form.elements.date.value = todayInputValue();
  form.querySelector('[type="submit"]').textContent = formId === "purchaseForm" ? "Save Purchase" : "Save Consumption";
  bindTransactionUnit(formId);
  if (formId === "purchaseForm") updatePurchaseBrandOptions();
}

function editPurchase(purchase) {
  const form = $("purchaseForm");
  form.elements.namedItem("id").value = purchase.id;
  form.elements.date.value = purchase.date || "";
  form.elements.categoryId.value = transactionCategoryId(purchase);
  updatePurchaseFields();
  form.elements.amount.value = purchase.totalAmount ?? "";
  form.elements.totalAmount.value = purchase.totalAmount ?? "";
  updatePurchaseFields();
  updatePurchaseBrandOptions(true);
  form.elements.supplierId.value = purchase.supplierId || "";
  updatePurchaseSupplierOptions(true);
  form.elements.quantity.value = purchase.quantity;
  form.elements.rate.value = purchase.rate;
  form.elements.billLink.value = purchase.billLink || "";
  bindTransactionUnit("purchaseForm");
  form.querySelector('[type="submit"]').textContent = "Update Purchase";
  form.scrollIntoView({ behavior: "smooth", block: "center" });
}

function editConsumption(consumption) {
  if (!isCementCategory(categories.find(item => item.id === transactionCategoryId(consumption)))) return;
  const form = $("consumptionForm");
  form.elements.namedItem("id").value = consumption.id;
  form.elements.date.value = consumption.date || "";
  form.elements.categoryId.value = transactionCategoryId(consumption);
  form.elements.quantity.value = consumption.quantity;
  form.elements.workLocation.value = consumption.workLocation || "";
  form.elements.remarks.value = consumption.remarks || "";
  bindTransactionUnit("consumptionForm");
  form.querySelector('[type="submit"]').textContent = "Update Consumption";
  form.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function savePurchase(event) {
  event.preventDefault();
  if (saving) return;
  const form = event.currentTarget;
  const fields = formObject(form);
  const category = categories.find(item => item.id === fields.categoryId);
  const priorPurchase = fields.id ? purchases.find(item => item.id === fields.id) : null;
  const cementPurchase = isCementCategory(category);
  const brand = cementPurchase && category && (categoryBrands(category).find(item => item.id === fields.brandId)
    || (priorPurchase && normalized(priorPurchase.brand) === normalized(fields.brandId) ? { id: priorPurchase.brandId || fields.brandId, name: priorPurchase.brand } : null)
    || (priorPurchase && (priorPurchase.brandId || `legacy-purchase-${priorPurchase.id}`) === fields.brandId ? { id: fields.brandId, name: priorPurchase.brand } : null));
  const supplier = suppliers.find(item => item.id === fields.supplierId);
  const quantity = Number(fields.quantity);
  const rate = Number(fields.rate);
  const enteredAmount = Number(fields.amount);
  const billLink = fields.billLink.trim();
  if (!category || (cementPurchase && !brand) || (fields.supplierId && !supplier) || !fields.date) {
    setStatus("Enter a date and category. Cement requires a brand / quarry; any selected supplier must be registered.", true);
    return;
  }
  if (cementPurchase && (!Number.isFinite(quantity) || quantity <= 0 || !Number.isFinite(rate) || rate < 0)) {
    setStatus("Enter a positive quantity and a non-negative rate for Cement.", true);
    return;
  }
  if (!cementPurchase && (!fields.amount.trim() || !Number.isFinite(enteredAmount) || enteredAmount < 0)) {
    setStatus("Enter a non-negative amount for this purchase.", true);
    return;
  }
  const preservingLegacySupplier = priorPurchase
    && supplier
    && transactionCategoryId(priorPurchase) === category.id
    && priorPurchase.supplierId === supplier.id;
  if (supplier && cementPurchase && !supplierProvidesBrand(supplier, category.id, brand.id) && !preservingLegacySupplier) {
    setStatus("Select a supplier assigned to this Cement brand / quarry.", true);
    return;
  }
  if (supplier && !cementPurchase && !supplierProvidesCategory(supplier, category.id)
    && !(priorPurchase && priorPurchase.supplierId === supplier.id && transactionCategoryId(priorPurchase) === category.id)) {
    setStatus("Select a supplier assigned to this category.", true);
    return;
  }
  const unit = cementPurchase ? categoryUnit(category) : (priorPurchase?.unit ?? "");
  if (cementPurchase && (!unit || String(fields.unit).trim() !== unit)) {
    setStatus("Purchase unit must match the selected category unit. Set the category unit first if needed.", true);
    return;
  }
  if (billLink && !safeExternalUrl(billLink)) {
    setStatus("Bill Photo / Link must be an http or https URL.", true);
    return;
  }
  const totalAmount = cementPurchase ? calculateCementPurchaseTotal(quantity, rate) : enteredAmount;
  if (!Number.isFinite(totalAmount)) {
    setStatus("Quantity and rate produce an invalid total amount.", true);
    return;
  }
  saving = true;
  try {
    const payload = {
      date: fields.date,
      categoryId: category.id,
      categoryName: category.name,
      brandId: brand?.id || (priorPurchase && transactionCategoryId(priorPurchase) === category.id ? priorPurchase.brandId || "" : ""),
      brand: brand?.name || (priorPurchase && transactionCategoryId(priorPurchase) === category.id ? priorPurchase.brand || "" : ""),
      supplierId: supplier?.id || "",
      supplierName: supplier?.name || "",
      quantity: cementPurchase ? quantity : (priorPurchase?.quantity ?? null),
      unit,
      rate: cementPurchase ? rate : (priorPurchase?.rate ?? null),
      totalAmount,
      billLink
    };
    if (fields.id) {
      await sendMaterialTransaction("purchases", fields.id, "PATCH", payload);
    } else {
      await sendMaterialTransaction(
        "purchases",
        "",
        "POST",
        payload,
        materialCreateRequestId(form, payload),
      );
    }
    resetTransactionForm("purchaseForm");
    await showSuccess(fields.id ? "Purchase updated." : "Purchase recorded.", "Materials");
    await loadData();
  } catch (error) {
    console.error("Unable to save material purchase:", error);
    setStatus("Unable to save purchase. Check the connection and try again.", true);
  } finally {
    saving = false;
  }
}

async function saveConsumption(event) {
  event.preventDefault();
  if (saving) return;
  const form = event.currentTarget;
  const fields = formObject(form);
  const category = categories.find(item => item.id === fields.categoryId);
  const quantity = Number(fields.quantity);
  if (!category || !fields.date || !fields.workLocation.trim() || !Number.isFinite(quantity) || quantity <= 0) {
    setStatus("Enter a date, category, positive quantity, and work/location.", true);
    return;
  }
  if (!isCementCategory(category)) {
    setStatus("Consumption can only be recorded for Cement.", true);
    return;
  }
  const totals = stockTotals(category);
  if (!totals) {
    setStatus("Consumption is blocked because this category has no consistent unit. Check the category and saved transaction units.", true);
    return;
  }
  let available = totals.available;
  const existingConsumption = fields.id ? consumptions.find(item => item.id === fields.id) : null;
  if (existingConsumption && transactionCategoryId(existingConsumption) === category.id) available += Number(existingConsumption.quantity || 0);
  if (quantity > available + 1e-9) {
    setStatus(`Insufficient stock. Available: ${formatQuantity(Math.max(0, available))} ${totals.unit}.`, true);
    return;
  }
  saving = true;
  try {
    const payload = {
      date: fields.date,
      categoryId: category.id,
      categoryName: category.name,
      quantity,
      unit: categoryUnit(category),
      workLocation: fields.workLocation.trim(),
      remarks: fields.remarks.trim()
    };
    if (fields.id) {
      await sendMaterialTransaction("consumptions", fields.id, "PATCH", payload);
    } else {
      await sendMaterialTransaction(
        "consumptions",
        "",
        "POST",
        payload,
        materialCreateRequestId(form, payload),
      );
    }
    resetTransactionForm("consumptionForm");
    await showSuccess(fields.id ? "Consumption updated." : "Consumption recorded.", "Materials");
    await loadData();
  } catch (error) {
    console.error("Unable to save material consumption:", error);
    setStatus("Unable to save consumption. Check the connection and try again.", true);
  } finally {
    saving = false;
  }
}

async function deleteTransaction(kind, transaction, label) {
  const confirmed = await showConfirmDialog({
    title: `Delete ${label}`,
    message: `Delete this ${label.toLocaleLowerCase()} record?`,
    details: "Material stock totals will be recalculated from the remaining saved records.",
    confirmText: "Delete",
    cancelText: "Cancel",
    type: "danger"
  });
  if (!confirmed) return;
  try {
    await sendMaterialTransaction(kind, transaction.id, "DELETE");
    await showSuccess(`${label} deleted.`, "Materials");
    await loadData();
  } catch (error) {
    console.error(`Unable to delete material ${label.toLocaleLowerCase()}:`, error);
    setStatus(`Unable to delete ${label.toLocaleLowerCase()}. Check the connection and try again.`, true);
  }
}

function handleTransactionAction(event, records, actions) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const record = records.find(item => item.id === button.dataset.id);
  const action = record && actions[button.dataset.action];
  if (action) action(record);
}

function formObject(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function createMaterialRequestId() {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  if (typeof globalThis.crypto?.getRandomValues === "function") {
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  throw new Error("Secure transaction request IDs are unavailable.");
}

function materialCreateRequestId(form, payload) {
  const requestBody = JSON.stringify(payload);
  if (form.dataset.createRequestBody !== requestBody) {
    form.dataset.createRequestId = createMaterialRequestId();
    form.dataset.createRequestBody = requestBody;
  }
  return form.dataset.createRequestId;
}

async function sendMaterialTransaction(kind, transactionId, method, payload, requestId = "") {
  const path = `/api/material-transactions/${kind}${transactionId ? `/${encodeURIComponent(transactionId)}` : ""}`;
  const headers = {
    "Accept": "application/json",
    "X-CSRF-Token": document.body.dataset.materialTransactionsCsrf || ""
  };
  if (payload) headers["Content-Type"] = "application/json";
  if (requestId) headers["Idempotency-Key"] = requestId;
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers,
    ...(payload ? { body: JSON.stringify(payload) } : {})
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(result.error || `Material transaction request failed (${response.status}).`);
  }
  return result;
}

async function sendMaterialMasterMutation(path, method, payload) {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: {
      "Accept": "application/json",
      "X-CSRF-Token": materialTransactionsCsrf,
      ...(payload ? { "Content-Type": "application/json" } : {})
    },
    ...(payload ? { body: JSON.stringify(payload) } : {})
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(result.error || `Material master-data request failed (${response.status}).`);
  }
  return result;
}

async function saveCategory(event) {
  event.preventDefault();
  if (saving) return;
  const fields = formObject(entityForm);
  const name = fields.name.trim();
  const unit = fields.unit.trim();
  if (!name || !unit) return setStatus("Category name and unit are required.", true);
  if (categories.some(item => item.id !== fields.id && normalized(item.name) === normalized(name))) {
    await showError("A category with this name already exists.", "Duplicate Category");
    return;
  }
  const brandNames = new Map();
  for (const input of entityForm.querySelectorAll(".category-brand-name")) {
    const brandName = input.value.trim();
    if (!brandName) continue;
    const key = normalized(brandName);
    if (brandNames.has(key)) {
      await showError("Brand or quarry names must be unique within a category.", "Duplicate Brand / Quarry");
      return;
    }
    brandNames.set(key, { id: input.dataset.brandId || "", name: brandName });
  }
  saving = true;
  try {
    const categoryBrands = [...brandNames.values()].map(brand => {
      if (!brand.id) {
        brand.id = createCategoryBrandId();
        const input = [...entityForm.querySelectorAll(".category-brand-name")]
          .find(item => item.value.trim() === brand.name);
        if (input) input.dataset.brandId = brand.id;
      }
      return { id: brand.id, name: brand.name };
    });
    const payload = { name, unit, brands: categoryBrands, description: fields.description.trim() };
    if (fields.id) {
      await sendMaterialMasterMutation(
        `/api/material-master/categories/${encodeURIComponent(fields.id)}`,
        "PATCH",
        payload
      );
    } else {
      const fingerprint = JSON.stringify(payload);
      if (pendingCategoryCreate?.fingerprint !== fingerprint) {
        pendingCategoryCreate = { id: createMaterialMasterRequestId(), fingerprint };
      }
      await sendMaterialMasterMutation("/api/material-master/categories", "POST", {
        id: pendingCategoryCreate.id,
        ...payload
      });
      pendingCategoryCreate = null;
    }
    closeEntityDialog();
    await showSuccess(fields.id ? "Category updated." : "Category created.", "Material Builder");
    await loadData();
  } catch (error) {
    console.error("Unable to save material category:", error);
    setStatus("Unable to save the category. Please check your connection and try again.", true);
  } finally {
    saving = false;
  }
}

async function deleteCategory(category) {
  const hasLegacyMaterials = materials.some(item => item.categoryId === category.id);
  const hasSuppliers = suppliers.some(supplier => supplierCategoryIds(supplier).includes(category.id));
  const hasTransactions = purchases.some(item => transactionCategoryId(item) === category.id)
    || consumptions.some(item => transactionCategoryId(item) === category.id);
  if (hasLegacyMaterials || hasSuppliers || hasTransactions) {
    await showError("This category is used by saved materials, supplier assignments, or transactions. Keep it to preserve those records.", "Category in use");
    return;
  }
  const confirmed = await showConfirmDialog({ title: "Delete Category", message: `Delete ${category.name}?`, details: "Only this category will be deleted.", confirmText: "Delete", cancelText: "Cancel", type: "danger" });
  if (!confirmed) return;
  try {
    await sendMaterialMasterMutation(
      `/api/material-master/categories/${encodeURIComponent(category.id)}`,
      "DELETE"
    );
    await showSuccess("Category deleted.", "Material Builder");
    await loadData();
  } catch (error) {
    console.error("Unable to delete material category:", error);
    setStatus("Unable to delete category. Check the connection and try again.", true);
  }
}

async function saveSupplier(event) {
  event.preventDefault();
  if (saving) return;
  const fields = formObject(entityForm);
  const name = fields.name.trim();
  const primaryPhone = fields.primaryPhone.trim();
  const categoryIds = [...new Set([...entityForm.querySelectorAll('input[name="categoryIds"]:checked')].map(input => input.value))];
  const categoryBrandAssignments = categoryIds.map(categoryId => ({
    categoryId,
    brandIds: [...new Set([...entityForm.querySelectorAll("input[name=\"supplierBrandIds\"]:checked")]
      .filter(input => input.dataset.categoryId === categoryId)
      .map(input => input.value))]
  }));
  if (!name || !primaryPhone) {
    setStatus("Supplier name and primary phone number are required.", true);
    return;
  }
  if (!categoryIds.length || categoryIds.some(id => !categories.some(category => category.id === id))) {
    setStatus("Assign at least one valid material category to this supplier.", true);
    return;
  }
  if (categoryBrandAssignments.some(assignment => !assignment.brandIds.length
    || assignment.brandIds.some(brandId => !categoryBrands(categories.find(category => category.id === assignment.categoryId)).some(brand => brand.id === brandId)))) {
    setStatus("Select at least one valid brand or quarry for each assigned category.", true);
    return;
  }
  if (fields.email.trim() && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(fields.email.trim())) {
    setStatus("Enter a valid supplier email address.", true);
    return;
  }

  saving = true;
  try {
    const payload = {
      name,
      categoryIds,
      categoryBrandAssignments,
      contactPerson: fields.contactPerson.trim(),
      address: fields.address.trim(),
      city: fields.city.trim(),
      state: fields.state.trim(),
      pinCode: fields.pinCode.trim(),
      primaryPhone,
      secondaryPhone: fields.secondaryPhone.trim(),
      whatsappNumber: fields.whatsappNumber.trim(),
      email: fields.email.trim(),
      gstNumber: fields.gstNumber.trim(),
      notes: fields.notes.trim()
    };
    if (fields.id) {
      await sendMaterialMasterMutation(
        `/api/material-master/suppliers/${encodeURIComponent(fields.id)}`,
        "PATCH",
        payload
      );
    } else {
      const fingerprint = JSON.stringify(payload);
      if (pendingSupplierCreate?.fingerprint !== fingerprint) {
        pendingSupplierCreate = { id: createMaterialMasterRequestId(), fingerprint };
      }
      await sendMaterialMasterMutation("/api/material-master/suppliers", "POST", {
        id: pendingSupplierCreate.id,
        ...payload
      });
      pendingSupplierCreate = null;
    }
    closeEntityDialog();
    await showSuccess(fields.id ? "Supplier updated." : "Supplier created.", "Material Builder");
    await loadData();
  } catch (error) {
    console.error("Unable to save supplier:", error);
    setStatus("Unable to save supplier. Check the connection and try again.", true);
  } finally {
    saving = false;
  }
}

async function viewSupplier(supplier) {
  $("materialDetailsTitle").textContent = supplier.name;
  const details = [
    ["Categories and Brands / Quarries", categoryBrandAssignments(supplier).map(assignment => {
      const names = assignment.brandIds.map(brandId => categoryBrands(categories.find(category => category.id === assignment.categoryId) || { id: assignment.categoryId }).find(brand => brand.id === brandId)?.name).filter(Boolean);
      return `${categoryName(assignment.categoryId)}: ${names.join(", ")}`;
    }).join("; ")],
    ["Contact Person", supplier.contactPerson], ["Shop / Business Address", supplier.address],
    ["City", supplier.city], ["State", supplier.state], ["PIN Code", supplier.pinCode],
    ["Primary Phone", supplier.primaryPhone], ["Secondary Phone", supplier.secondaryPhone],
    ["WhatsApp", supplier.whatsappNumber], ["Email", supplier.email], ["GST Number", supplier.gstNumber],
    ["Notes", supplier.notes]
  ];
  $("materialDetailsBody").innerHTML = `<div class="material-details-grid">${details.map(([label, value]) => `<div class="material-detail"><strong>${escapeHtml(label)}</strong><span>${escapeHtml(value || "-")}</span></div>`).join("")}</div>`;
  detailsDialog.showModal();
  registerEscClose(detailsDialog, closeDetailsDialog);
}

async function deleteSupplier(supplier) {
  if (purchases.some(item => item.supplierId === supplier.id)) {
    await showError("This supplier is referenced by saved purchases. Keep it to preserve purchase history.", "Supplier in use");
    return;
  }
  const confirmed = await showConfirmDialog({ title: "Delete Supplier", message: `Delete ${supplier.name}?`, details: "This supplier is not referenced by saved purchases.", confirmText: "Delete", cancelText: "Cancel", type: "danger" });
  if (!confirmed) return;
  try {
    await sendMaterialMasterMutation(
      `/api/material-master/suppliers/${encodeURIComponent(supplier.id)}`,
      "DELETE"
    );
    await showSuccess("Supplier deleted.", "Material Builder");
    await loadData();
  } catch (error) {
    console.error("Unable to delete supplier:", error);
    setStatus("Unable to delete supplier. Check the connection and try again.", true);
  }
}

function closeDetailsDialog() {
  unregisterEscClose(detailsDialog);
  detailsDialog.close();
}

function handleListAction(event, items, actions) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const item = items.find(candidate => candidate.id === button.dataset.id);
  if (!item) return;
  const action = actions[button.dataset.action];
  if (action) action(item);
}

async function loadData() {
  try {
    setStatus("Loading categories, suppliers, and transactions...");
    const [categorySnapshot, materialSnapshot, brandSnapshot, supplierSnapshot, purchaseSnapshot, consumptionSnapshot] = await Promise.all([
      getDocs(collection(db, COLLECTIONS.categories)),
      getDocs(collection(db, COLLECTIONS.materials)),
      getDocs(collection(db, COLLECTIONS.brands)),
      getDocs(collection(db, COLLECTIONS.suppliers)),
      getDocs(collection(db, COLLECTIONS.purchases)),
      getDocs(collection(db, COLLECTIONS.consumptions))
    ]);
    categories = categorySnapshot.docs.map(item => ({ id: item.id, ...item.data() })).sort((a, b) => String(a.name).localeCompare(String(b.name)));
    materials = materialSnapshot.docs.map(item => ({ id: item.id, ...item.data() })).sort((a, b) => String(a.name).localeCompare(String(b.name)));
    brands = brandSnapshot.docs.map(item => ({ id: item.id, ...item.data() })).sort((a, b) => String(a.name).localeCompare(String(b.name)));
    suppliers = supplierSnapshot.docs.map(item => ({ id: item.id, ...item.data() })).sort((a, b) => String(a.name).localeCompare(String(b.name)));
    purchases = purchaseSnapshot.docs.map(item => ({ id: item.id, ...item.data() }));
    consumptions = consumptionSnapshot.docs.map(item => ({ id: item.id, ...item.data() }));
    populateTransactionSelectors();
    renderCategories();
    renderStock();
    renderInvestmentSummary();
    renderSuppliers();
    renderPurchases();
    renderConsumptions();
    setStatus(`${categories.length} categories · ${suppliers.length} suppliers · ${purchases.length} purchases · ${consumptions.length} consumptions`);
  } catch (error) {
    console.error("Unable to load Material Builder data:", error);
    setStatus(describeFirebaseError(error), true);
    renderEmpty($("categoryList"), "Material categories could not be loaded.");
    renderEmpty($("stockList"), "Category stock could not be loaded.");
    renderEmpty($("supplierList"), "Suppliers could not be loaded.");
    renderEmpty($("purchaseList"), "Purchases could not be loaded.");
    renderEmpty($("consumptionList"), "Consumption records could not be loaded.");
  }
}

function activateTab(panelId) {
  document.querySelectorAll(".materials-tab").forEach(tab => {
    const active = tab.dataset.panel === panelId;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".materials-panel").forEach(panel => {
    panel.hidden = panel.id !== panelId;
  });
}

async function bootstrap() {
  document.querySelectorAll(".materials-tab").forEach(tab => tab.addEventListener("click", () => activateTab(tab.dataset.panel)));
  document.querySelectorAll(".materials-subtab").forEach(tab => tab.addEventListener("click", () => {
    document.querySelectorAll(".materials-subtab").forEach(item => {
      const active = item.dataset.panel === tab.dataset.panel;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", String(active));
    });
    document.querySelectorAll(".materials-subpanel").forEach(panel => {
      panel.hidden = panel.id !== tab.dataset.panel;
    });
  }));
  $("categorySearch").addEventListener("input", renderCategories);
  $("supplierSearch").addEventListener("input", renderSuppliers);
  $("createCategoryButton").addEventListener("click", () => openCategoryForm());
  $("createSupplierButton").addEventListener("click", () => openSupplierForm());
  $("purchaseForm").addEventListener("submit", savePurchase);
  $("consumptionForm").addEventListener("submit", saveConsumption);
  $("purchaseForm").elements.categoryId.addEventListener("change", () => {
    updatePurchaseFields();
    updatePurchaseBrandOptions();
    updatePurchaseSupplierOptions();
    bindTransactionUnit("purchaseForm");
  });
  $("purchaseForm").elements.brandId.addEventListener("change", () => updatePurchaseSupplierOptions());
  $("consumptionForm").elements.categoryId.addEventListener("change", () => bindTransactionUnit("consumptionForm"));
  $("purchaseForm").elements.quantity.addEventListener("input", updatePurchaseAmount);
  $("purchaseForm").elements.rate.addEventListener("input", updatePurchaseAmount);
  document.querySelectorAll("[data-reset-transaction]").forEach(button => {
    button.addEventListener("click", () => resetTransactionForm(button.dataset.resetTransaction));
  });
  $("purchaseList").addEventListener("click", event => handleTransactionAction(event, purchases, {
    "edit-purchase": editPurchase,
    "delete-purchase": purchase => deleteTransaction("purchases", purchase, "Purchase")
  }));
  $("consumptionList").addEventListener("click", event => handleTransactionAction(event, consumptions, {
    "edit-consumption": editConsumption,
    "delete-consumption": consumption => deleteTransaction("consumptions", consumption, "Consumption")
  }));
  $("closeEntityDialog").addEventListener("click", closeEntityDialog);
  $("closeMaterialDetails").addEventListener("click", closeDetailsDialog);
  entityForm.addEventListener("click", event => {
    if (event.target.closest("[data-add-category-brand]")) {
      $("categoryBrandRows")?.insertAdjacentHTML("beforeend", categoryBrandRow());
    }
    if (event.target.closest("[data-remove-category-brand]")) {
      event.target.closest(".category-brand-row")?.remove();
    }
    if (event.target.closest("[data-form-cancel]")) closeEntityDialog();
  });
  entityForm.addEventListener("change", event => {
    if (event.target.matches(".supplier-category-checkbox")) {
      const categoryOption = event.target.closest(".supplier-category-option");
      categoryOption.classList.toggle("is-selected", event.target.checked);
      const brandGroup = [...entityForm.querySelectorAll(".supplier-brand-category")]
        .find(group => group.dataset.categoryId === event.target.value);
      if (brandGroup) brandGroup.hidden = !event.target.checked;
    }
    if (event.target.matches(".supplier-brand-checkbox") && event.target.checked) {
      const categoryOption = [...entityForm.querySelectorAll(".supplier-category-option")]
        .find(option => option.querySelector(".supplier-category-checkbox")?.value === event.target.dataset.categoryId);
      const categoryCheckbox = categoryOption?.querySelector(".supplier-category-checkbox");
      if (categoryCheckbox) {
        categoryCheckbox.checked = true;
        categoryOption.classList.add("is-selected");
      }
      const brandGroup = [...entityForm.querySelectorAll(".supplier-brand-category")]
        .find(group => group.dataset.categoryId === event.target.dataset.categoryId);
      if (brandGroup) brandGroup.hidden = false;
    }
  });
  entityDialog.addEventListener("click", event => { if (event.target === entityDialog) closeEntityDialog(); });
  detailsDialog.addEventListener("click", event => { if (event.target === detailsDialog) closeDetailsDialog(); });
  $("categoryList").addEventListener("click", event => handleListAction(event, categories, {
    "edit-category": openCategoryForm,
    "delete-category": deleteCategory
  }));
  $("supplierList").addEventListener("click", event => handleListAction(event, suppliers, {
    "view-supplier": viewSupplier,
    "edit-supplier": openSupplierForm,
    "delete-supplier": deleteSupplier
  }));
  resetTransactionForm("purchaseForm");
  resetTransactionForm("consumptionForm");
  if (firebaseBootstrapError) {
    setStatus(describeFirebaseError(firebaseBootstrapError), true);
    renderEmpty($("categoryList"), "Material data is unavailable because Firebase could not be initialized.");
    renderEmpty($("stockList"), "Material data is unavailable because Firebase could not be initialized.");
    renderEmpty($("supplierList"), "Material data is unavailable because Firebase could not be initialized.");
    renderEmpty($("purchaseList"), "Material data is unavailable because Firebase could not be initialized.");
    renderEmpty($("consumptionList"), "Material data is unavailable because Firebase could not be initialized.");
    return;
  }
  await loadData();
}

bootstrap().catch(error => {
  console.error("Material Builder initialization failed:", error);
  setStatus(describeFirebaseError(error), true);
});
