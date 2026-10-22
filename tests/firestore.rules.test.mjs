import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import {
  assertFails,
  assertSucceeds,
  initializeTestEnvironment
} from "@firebase/rules-unit-testing";
import {
  collection,
  deleteDoc,
  doc,
  getDoc,
  getDocs,
  setDoc,
  updateDoc
} from "firebase/firestore";

const projectId = "demo-mannat-moon-rules";
let testEnvironment;
let userSequence = 0;
const emulatorAvailable = Boolean(process.env.FIRESTORE_EMULATOR_HOST);
const rulesTest = emulatorAvailable ? test : test.skip;

before(async () => {
  if (!emulatorAvailable) return;
  testEnvironment = await initializeTestEnvironment({
    projectId,
    firestore: {
      host: "127.0.0.1",
      port: 8080
    }
  });
});

after(async () => {
  await testEnvironment?.cleanup();
});

async function createUserContext({
  permissions = [],
  status = "active",
  isActive = true,
  mustChangePassword = false,
  activeSessions = ["session-1"],
  authorizationVersion = 1
} = {}) {
  const uid = `rules-user-${++userSequence}`;

  await testEnvironment.withSecurityRulesDisabled(async context => {
    const db = context.firestore();
    await setDoc(doc(db, "users", uid), {
      status,
      is_active: isActive,
      must_change_password: mustChangePassword,
      active_sessions: activeSessions,
      authorization_version: authorizationVersion
    });

    const seedDocuments = {
      trades: { name: "Trade" },
      daily_records: { date: "2025-01-01" },
      material_categories: { name: "Category" },
      materials: { name: "Material" },
      material_brands: { name: "Brand" },
      suppliers: { name: "Supplier" },
      material_purchases: { amount: 10 },
      material_consumptions: { amount: 2 },
      drawings_categories: { name: "Drawings" },
      drawings: { title: "Drawing" },
      users: { username: "Seed user" },
      custom_staff_types: { name: "Custom staff type" },
      audit_log: { action: "test" },
      camera_auth_rate_limits: { count: 1 },
      daily_reports_drive_oauth_tokens: { encrypted_credentials: "ciphertext" },
      owner_scopes: { name: "Owner Scope" },
      "owner_scopes/owner-scope/payments": { amount: 5 },
      "owner_scopes/other-owner/payments": { amount: 9 },
      "owner_scopes/owner-scope/items": { name: "Item" }
    };
    for (const [path, data] of Object.entries(seedDocuments)) {
      await setDoc(doc(db, path, "seed"), data);
    }
  });

  return testEnvironment.authenticatedContext(uid, {
    perms: permissions,
    role: "rules_test",
    sid: "session-1",
    ver: 1
  }).firestore();
}

rulesTest("unauthenticated clients cannot read or write Firestore data", async () => {
  const db = testEnvironment.unauthenticatedContext().firestore();

  await assertFails(getDoc(doc(db, "trades", "seed")));
  await assertFails(setDoc(doc(db, "material_categories", "unauth-create"), { name: "No" }));
});

rulesTest("browser trade reads are denied while other module reads remain permission-scoped", async () => {
  const permissionReads = [
    {
      permission: "trade_builder.read",
      allowed: [],
      denied: ["trades/seed", "daily_records/seed"]
    },
    {
      permission: "daily_reports.read",
      allowed: ["daily_records/seed"],
      denied: ["trades/seed", "drawings/seed"]
    },
    {
      permission: "labour_payments.read",
      allowed: [],
      denied: ["trades/seed", "daily_records/seed"]
    },
    {
      permission: "ledger.read",
      allowed: [],
      denied: ["trades/seed", "daily_records/seed"]
    },
    {
      permission: "materials.read",
      allowed: [
        "material_categories/seed",
        "materials/seed",
        "material_brands/seed",
        "suppliers/seed",
        "material_purchases/seed",
        "material_consumptions/seed"
      ],
      denied: ["drawings/seed"]
    },
    {
      permission: "drawings.read",
      allowed: ["drawings_categories/seed", "drawings/seed"],
      denied: ["trades/seed"]
    },
    {
      permission: "building_cost_summary.read",
      allowed: [
        "material_categories/seed",
        "material_purchases/seed",
        "owner_scopes/owner-scope/payments/seed"
      ],
      denied: ["trades/seed", "material_consumptions/seed"]
    }
  ];

  for (const { permission, allowed, denied } of permissionReads) {
    const db = await createUserContext({ permissions: [permission] });
    for (const path of allowed) {
      await assertSucceeds(getDoc(doc(db, path)));
    }
    for (const path of denied) {
      await assertFails(getDoc(doc(db, path)));
    }
  }

  const reportsDb = await createUserContext({ permissions: ["daily_reports.read"] });
  await assertSucceeds(getDocs(collection(reportsDb, "daily_records")));
  const allPermissionsDb = await createUserContext({ permissions: [
    "trade_builder.read", "labour_payments.read", "ledger.read",
    "building_cost_summary.read", "daily_reports.read"
  ] });
  await assertFails(getDoc(doc(allPermissionsDb, "trades", "seed")));
  await assertFails(getDocs(collection(allPermissionsDb, "trades")));
  await assertSucceeds(getDocs(collection(allPermissionsDb, "daily_records")));
  const materialsDb = await createUserContext({ permissions: ["materials.read"] });
  await assertSucceeds(getDocs(collection(materialsDb, "material_categories")));
});

rulesTest("missing permissions and user-administration reads are denied", async () => {
  const db = await createUserContext({ permissions: ["dashboard.read"] });

  await assertFails(getDoc(doc(db, "trades", "seed")));
  await assertFails(getDoc(doc(db, "daily_records", "seed")));
  await assertFails(getDoc(doc(db, "users", "rules-user-1")));
  await assertFails(getDocs(collection(db, "custom_staff_types")));
});

rulesTest("user, audit, camera-limit, and backup system data are not browser-accessible", async () => {
  const db = await createUserContext({
    permissions: ["user_admin.manage", "system_backup.manage", "daily_reports.read"]
  });

  for (const collectionName of [
    "users",
    "custom_staff_types",
    "audit_log",
    "camera_auth_rate_limits",
    "daily_reports_drive_oauth_tokens"
  ]) {
    await assertFails(getDocs(collection(db, collectionName)));
    await assertFails(setDoc(doc(db, collectionName, "browser-write"), { value: "No" }));
    await assertFails(updateDoc(doc(db, collectionName, "seed"), { value: "No" }));
    await assertFails(deleteDoc(doc(db, collectionName, "seed")));
  }
});

rulesTest("inactive, forced-password-change, revoked-session, and stale-version users are denied", async () => {
  const scenarios = [
    { status: "inactive", isActive: false },
    { mustChangePassword: true },
    { activeSessions: [] },
    { authorizationVersion: 2 }
  ];

  for (const scenario of scenarios) {
    const db = await createUserContext({
      permissions: ["daily_reports.read"],
      ...scenario
    });
    await assertFails(getDoc(doc(db, "daily_records", "seed")));
  }
});

rulesTest("Owner Scope access is fixed to its project document and summary permission", async () => {
  const summaryDb = await createUserContext({
    permissions: ["building_cost_summary.read"]
  });
  await assertSucceeds(getDoc(doc(summaryDb, "owner_scopes/owner-scope/payments/seed")));
  await assertFails(getDoc(doc(summaryDb, "owner_scopes/other-owner/payments/seed")));
  await assertFails(setDoc(doc(summaryDb, "owner_scopes/other-owner/payments/client-write"), { amount: 1 }));
  await assertFails(getDoc(doc(summaryDb, "owner_scopes/owner-scope/items/seed")));
  await assertFails(getDoc(doc(summaryDb, "owner_scopes/owner-scope")));
  await assertFails(getDocs(collection(summaryDb, "owner_scopes")));
  await assertFails(getDocs(collection(summaryDb, "owner_scopes/owner-scope/items")));
  await assertFails(getDocs(collection(summaryDb, "owner_scopes/other-owner/payments")));
  await assertFails(setDoc(
    doc(summaryDb, "owner_scopes/owner-scope/items/client-write"),
    { name: "No" }
  ));

  const ownerScopeDb = await createUserContext({
    permissions: ["owner_scope.read"]
  });
  await assertFails(getDoc(doc(ownerScopeDb, "owner_scopes/owner-scope/payments/seed")));
});

rulesTest("Materials clients can read permitted data but cannot create, edit, or delete it", async () => {
  const db = await createUserContext({ permissions: ["materials.read"] });
  const writableCollections = [
    "material_categories",
    "materials",
    "material_brands",
    "suppliers",
    "material_purchases",
    "material_consumptions"
  ];

  for (const collectionName of writableCollections) {
    await assertSucceeds(getDoc(doc(db, collectionName, "seed")));
    await assertFails(setDoc(doc(db, collectionName, "new-client-write"), { name: "No" }));
    await assertFails(updateDoc(doc(db, collectionName, "seed"), { name: "No" }));
    await assertFails(deleteDoc(doc(db, collectionName, "seed")));
  }
});

rulesTest("server-mediated operational and configuration writes are denied to browser clients", async () => {
  const db = await createUserContext({
    permissions: [
      "trade_builder.write",
      "trade_builder.delete",
      "daily_reports.create",
      "daily_reports.edit",
      "daily_reports.delete",
      "drawings.write",
      "drawings.delete",
      "materials.write",
      "materials.delete",
      "materials.transactions.write",
      "materials.transactions.delete",
      "owner_scope.create",
      "owner_scope.edit",
      "owner_scope.delete"
    ]
  });

  for (const path of [
    "trades/seed",
    "daily_records/seed",
    "drawings/seed",
    "drawings_categories/seed",
    "owner_scopes/owner-scope",
    "owner_scopes/owner-scope/items/seed",
    "owner_scopes/owner-scope/payments/seed"
  ]) {
    const segments = path.split("/");
    const collectionPath = segments.slice(0, -1).join("/");
    await assertFails(setDoc(doc(db, collectionPath, "browser-create"), { directWrite: true }));
    await assertFails(updateDoc(doc(db, path), { directWrite: true }));
    await assertFails(deleteDoc(doc(db, path)));
  }
});
