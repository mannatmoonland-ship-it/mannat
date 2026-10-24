import assert from "node:assert/strict";
import test from "node:test";

import {
  LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
  OWNER_SCOPE_CATEGORIES,
  OWNER_SCOPE_DOCUMENT_ID,
  applyOwnerScopeMutation,
  loadOwnerScope,
  loadOwnerScopeMetadata,
  validateOwnerScopeCategory
} from "../static/owner_scope_data.mjs";

function makeFirestore(seed) {
  const documents = new Map(Object.entries(seed));
  const writes = [];
  const reads = [];

  function path(collectionName, ...segments) {
    return [collectionName, ...segments].join("/");
  }

  const api = {
    collection: (_db, collectionName, ...segments) => ({
      path: path(collectionName, ...segments)
    }),
    doc: (_db, collectionName, ...segments) => ({
      path: path(collectionName, ...segments),
      id: segments.at(-1)
    }),
    getDoc: async reference => {
      reads.push(reference.path);
      const data = documents.get(reference.path);
      return {
        id: reference.id,
        exists: data !== undefined,
        data: () => structuredClone(data)
      };
    },
    getDocs: async reference => ({
      docs: [...documents.entries()]
        .filter(([documentPath]) => documentPath.startsWith(`${reference.path}/`))
        .map(([documentPath, data]) => ({
          id: documentPath.slice(reference.path.length + 1),
          data: () => structuredClone(data)
        }))
    })
  };

  return { db: {}, api, documents, writes, reads };
}

test("all Owner Scope categories are accepted and invalid categories rejected", () => {
  assert.equal(OWNER_SCOPE_CATEGORIES.length, 15);
  for (const category of [
    "office_expenditure",
    "office_rents",
    "office_salaries",
    "office_utilities",
    "office_supplies",
    "office_travel_conveyance"
  ]) {
    assert.ok(OWNER_SCOPE_CATEGORIES.includes(category));
  }
  for (const category of OWNER_SCOPE_CATEGORIES) {
    assert.equal(validateOwnerScopeCategory(category), category);
  }
  for (const category of ["", "Mixed", "unknown", null]) {
    assert.throws(() => validateOwnerScopeCategory(category), TypeError);
  }
});

test("loads Owner Scope metadata with the stable legacy trade document ID", async () => {
  const metadata = {
    name: "Owner Scope",
    legacySource: { collection: "trades", documentId: LEGACY_OWNER_ACCOUNT_DOCUMENT_ID }
  };
  const firestore = makeFirestore({
    [`owner_scopes/${OWNER_SCOPE_DOCUMENT_ID}`]: metadata
  });

  const loaded = await loadOwnerScopeMetadata(
    firestore.db,
    firestore.api
  );

  assert.deepEqual(loaded, { id: OWNER_SCOPE_DOCUMENT_ID, ...metadata });
  assert.deepEqual(firestore.writes, []);
});

test("loads normal Owner Scope items and payments without reading legacy trades", async () => {
  const metadata = {
    name: "Owner Scope",
    legacySource: { collection: "trades", documentId: LEGACY_OWNER_ACCOUNT_DOCUMENT_ID }
  };
  const firestore = makeFirestore({
    [`owner_scopes/${OWNER_SCOPE_DOCUMENT_ID}`]: metadata,
    [`owner_scopes/${OWNER_SCOPE_DOCUMENT_ID}/items/item-1`]: {
      name: "Pole removal",
      category: "mixed"
    },
    [`owner_scopes/${OWNER_SCOPE_DOCUMENT_ID}/payments/payment-1`]: {
      expenseItemId: "item-1",
      date: "2026-10-06",
      amount: 1250,
      paymentMode: "Cash",
      remarks: "New Owner Scope payment",
      sourceLegacyTransactionId: "legacy-payment-1"
    }
  });

  const loaded = await loadOwnerScope(firestore.db, firestore.api);

  assert.deepEqual(loaded.items, [{
    id: "item-1",
    name: "Pole removal",
    category: "mixed"
  }]);
  assert.deepEqual(loaded.payments, [{
    id: "payment-1",
    expenseItemId: "item-1",
    date: "2026-10-06",
    amount: 1250,
    paymentMode: "Cash",
    remarks: "New Owner Scope payment",
    sourceLegacyTransactionId: "legacy-payment-1"
  }]);
  assert.equal("legacyOwnerAccount" in loaded, false);
  assert.equal("legacyPayments" in loaded, false);
  assert.equal(firestore.reads.includes(`trades/${LEGACY_OWNER_ACCOUNT_DOCUMENT_ID}`), false);
  assert.deepEqual(firestore.writes, []);
});

test("missing Owner Scope metadata returns empty read-only data", async () => {
  const firestore = makeFirestore({});

  const loaded = await loadOwnerScope(firestore.db, firestore.api);

  assert.deepEqual(loaded, {
    metadata: null,
    items: [],
    payments: []
  });
  assert.deepEqual(firestore.writes, []);
});

test("invalid Owner Scope legacy reference fails explicitly", async () => {
  const firestore = makeFirestore({
    [`owner_scopes/${OWNER_SCOPE_DOCUMENT_ID}`]: {
      name: "Owner Scope",
      legacySource: { collection: "trades", documentId: "wrong-id" }
    }
  });
  await assert.rejects(
    loadOwnerScopeMetadata(firestore.db, firestore.api),
    /unexpected legacy trade reference/
  );
  assert.deepEqual(firestore.writes, []);
});


test("Owner Scope mutations update only the affected item and linked payment in memory", () => {
  const data = {
    metadata: { id: OWNER_SCOPE_DOCUMENT_ID },
    items: [
      { id: "item-1", name: "Old", category: "labour", remarks: "", createdAt: "original" },
      { id: "item-2", name: "Keep", category: "material", remarks: "" }
    ],
    payments: [
      { id: "payment-1", expenseItemId: "item-1", date: "2026-10-01", amount: 100, paymentMode: "Cash" },
      { id: "payment-2", expenseItemId: "item-2", date: "2026-10-02", amount: 250, paymentMode: "UPI" }
    ]
  };

  assert.equal(applyOwnerScopeMutation(data, "edit", "item-1", null, {
    name: "  New   name ", category: "material", remarks: " edited ",
    paymentDate: "2026-10-03", amountPaid: "175", paymentMode: " Bank Transfer "
  }, "2026-10-03T00:00:00.000Z"), true);
  assert.deepEqual(data.items[0], {
    id: "item-1", name: "New name", category: "material", remarks: "edited",
    createdAt: "original", updatedAt: "2026-10-03T00:00:00.000Z"
  });
  assert.equal(data.payments[0].amount, 175);
  assert.equal(data.payments[0].date, "2026-10-03");
  assert.equal(data.payments[0].paymentMode, "Bank Transfer");
  assert.equal(data.payments[1].amount, 250);

  assert.equal(applyOwnerScopeMutation(data, "delete", "item-1", null, {}, "2026-10-04"), true);
  assert.deepEqual(data.items.map(item => item.id), ["item-2"]);
  assert.deepEqual(data.payments.map(payment => payment.id), ["payment-2"]);
});
