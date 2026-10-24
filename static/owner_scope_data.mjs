export const OWNER_SCOPE_COLLECTION = "owner_scopes";
export const OWNER_SCOPE_DOCUMENT_ID = "owner-scope";
export const LEGACY_OWNER_ACCOUNT_DOCUMENT_ID = "8f5d405c-c65b-4b0a-88bb-cbda4619f90b";

export const OWNER_SCOPE_CATEGORIES = Object.freeze([
  "labour",
  "material",
  "equipment_plant",
  "professional_technical",
  "government_statutory",
  "legal_documentation",
  "site_expense",
  "mixed",
  "other",
  "office_expenditure",
  "office_rents",
  "office_salaries",
  "office_utilities",
  "office_supplies",
  "office_travel_conveyance"
]);

const LEGACY_OWNER_ACCOUNT_COLLECTION = "trades";
const categorySet = new Set(OWNER_SCOPE_CATEGORIES);

export function validateOwnerScopeCategory(category) {
  if (!categorySet.has(category)) {
    throw new TypeError(`Invalid Owner Scope category: ${String(category)}.`);
  }
  return category;
}

function requireFirestoreApi(api) {
  for (const method of ["collection", "doc", "getDoc", "getDocs"]) {
    if (typeof api?.[method] !== "function") {
      throw new TypeError(`Firestore read API is missing ${method}().`);
    }
  }
}

function snapshotExists(snapshot) {
  return typeof snapshot.exists === "function" ? snapshot.exists() : snapshot.exists;
}

function snapshotEntries(snapshot) {
  return snapshot.docs.map(item => ({ ...item.data(), id: item.id }));
}

export async function loadOwnerScopeMetadata(
  db,
  api,
  scopeId = OWNER_SCOPE_DOCUMENT_ID
) {
  requireFirestoreApi(api);
  const reference = api.doc(db, OWNER_SCOPE_COLLECTION, scopeId);
  const snapshot = await api.getDoc(reference);
  if (!snapshotExists(snapshot)) return null;

  const metadata = { id: snapshot.id, ...snapshot.data() };
  const legacySource = metadata.legacySource;
  if (
    metadata.name !== "Owner Scope" ||
    legacySource?.collection !== LEGACY_OWNER_ACCOUNT_COLLECTION ||
    legacySource?.documentId !== LEGACY_OWNER_ACCOUNT_DOCUMENT_ID
  ) {
    throw new Error("Owner Scope metadata has an unexpected legacy trade reference.");
  }
  return metadata;
}

export async function loadOwnerScopeItems(db, api, scopeId = OWNER_SCOPE_DOCUMENT_ID) {
  requireFirestoreApi(api);
  const reference = api.collection(db, OWNER_SCOPE_COLLECTION, scopeId, "items");
  const snapshot = await api.getDocs(reference);
  return snapshotEntries(snapshot);
}

export async function loadOwnerScopePayments(db, api, scopeId = OWNER_SCOPE_DOCUMENT_ID) {
  requireFirestoreApi(api);
  const reference = api.collection(db, OWNER_SCOPE_COLLECTION, scopeId, "payments");
  const snapshot = await api.getDocs(reference);
  return snapshotEntries(snapshot);
}

export async function loadOwnerScope(db, api, scopeId = OWNER_SCOPE_DOCUMENT_ID) {
  const metadata = await loadOwnerScopeMetadata(db, api, scopeId);
  if (!metadata) {
    return {
      metadata: null,
      items: [],
      payments: []
    };
  }

  const [items, payments] = await Promise.all([
    loadOwnerScopeItems(db, api, scopeId),
    loadOwnerScopePayments(db, api, scopeId)
  ]);

  return {
    metadata,
    items,
    payments
  };
}

export function applyOwnerScopeMutation(data, action, itemId, paymentId, payload, now = new Date().toISOString()) {
  if (!data || !Array.isArray(data.items) || !Array.isArray(data.payments)) return false;
  const name = String(payload.name || "").trim().replace(/\s+/g, " ");
  const remarks = String(payload.remarks || "").trim();

  if (action === "delete") {
    data.items = data.items.filter(item => item.id !== itemId);
    data.payments = data.payments.filter(payment => payment.expenseItemId !== itemId);
    return true;
  }
  if (action === "create") {
    data.items.push({ id: itemId, name, category: payload.category, remarks, createdAt: now, updatedAt: now });
    data.payments.push({
      id: paymentId, expenseItemId: itemId, date: payload.paymentDate,
      amount: Number(payload.amountPaid), paymentMode: String(payload.paymentMode || "").trim(),
      remarks, createdAt: now, updatedAt: now
    });
    return true;
  }

  const item = data.items.find(candidate => candidate.id === itemId);
  if (!item) return false;
  Object.assign(item, { name, category: payload.category, remarks, updatedAt: now });
  if (Object.hasOwn(payload, "paymentDate")) {
    const payment = data.payments.find(candidate => candidate.expenseItemId === itemId);
    if (!payment) return false;
    Object.assign(payment, {
      date: payload.paymentDate, amount: Number(payload.amountPaid),
      paymentMode: String(payload.paymentMode || "").trim(), updatedAt: now
    });
  }
  return true;
}
