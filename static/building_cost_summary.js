import { calculateBuildingCosts, formatRupees } from "./building_cost_summary_logic.mjs";
import {
  OWNER_SCOPE_COLLECTION,
  OWNER_SCOPE_DOCUMENT_ID
} from "./owner_scope_constants.mjs";
import { formatDate } from "./date_format.js";
import {
  describeFirebaseError,
  firebaseAuthReady,
  getFirebaseApp,
  getFirebaseFirestoreSdk
} from "./firebase_auth.js";

const $ = id => document.getElementById(id);
const status = $("summaryStatus");
let categories = [];
let purchases = [];
let trades = [];
let ownerScopePayments = [];
const loaded = new Set();
const loadErrors = new Set();
let db;
let firebaseBootstrapError = null;
let firestoreSdk = null;
const firestoreMethod = name => (...args) => {
  const method = firestoreSdk?.[name];
  if (typeof method !== "function") {
    throw firebaseBootstrapError || new Error("The Firebase Firestore SDK is unavailable.");
  }
  return method(...args);
};
const collection = firestoreMethod("collection");
const onSnapshot = firestoreMethod("onSnapshot");

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[character]));
}

function renderBreakdown(container, entries, emptyMessage) {
  container.innerHTML = entries.length
    ? entries.map(entry => `<article class="cost-card"><h3>${escapeHtml(entry.name)}</h3><strong>${escapeHtml(formatRupees(entry.total))}</strong></article>`).join("")
    : `<div class="empty-state">${escapeHtml(emptyMessage)}</div>`;
}

function render() {
  if (!db) {
    $("buildingTotal").textContent = "—";
    $("materialTotal").textContent = "—";
    $("tradeTotal").textContent = "—";
    $("ownerScopeTotal").textContent = "—";
    renderBreakdown($("materialBreakdown"), [], "Cost data is unavailable.");
    renderBreakdown($("tradeBreakdown"), [], "Cost data is unavailable.");
    status.textContent = describeFirebaseError(firebaseBootstrapError);
    status.className = "summary-status error";
    return;
  }
  const custom = $("dateFilterMode").value === "custom";
  const fromDate = custom ? $("fromDate").value : "";
  const toDate = custom ? $("toDate").value : "";
  if (fromDate && toDate && fromDate > toDate) {
    status.textContent = "From date must be on or before To date.";
    status.className = "summary-status error";
    return;
  }

  const result = calculateBuildingCosts({
    categories,
    purchases,
    trades,
    ownerScopePayments,
    fromDate,
    toDate,
    includeOwnerScope: $("includeOwnerScope").checked
  });
  $("buildingTotal").textContent = formatRupees(result.total);
  $("materialTotal").textContent = formatRupees(result.materialTotal);
  $("tradeTotal").textContent = formatRupees(result.tradeTotal);
  $("ownerScopeTotal").textContent = formatRupees(result.ownerScopeTotal);
  $("selectedDateLabel").textContent = custom && (fromDate || toDate)
    ? `${fromDate ? formatDate(fromDate) : "Beginning"} to ${toDate ? formatDate(toDate) : "Latest"}`
    : "All Time";
  renderBreakdown($("materialBreakdown"), result.materials, "No material categories found.");
  renderBreakdown($("tradeBreakdown"), result.trades, "No trades found.");
  if (loadErrors.size) {
    status.className = "summary-status error";
    status.textContent = `Some cost data failed to load (${[...loadErrors].join(", ")}); totals may be incomplete.`;
  } else {
    status.className = "summary-status";
    status.textContent = loaded.size === 4 ? "Totals update automatically when recorded costs change." : "Loading recorded costs...";
  }
}

function subscribe(collectionName, onData) {
  return onSnapshot(collection(db, collectionName), snapshot => {
    onData(snapshot.docs.map(document => ({ id: document.id, ...document.data() })));
    loaded.add(collectionName);
    render();
  }, error => {
    loadErrors.add(collectionName);
    console.error(`Unable to subscribe to ${collectionName}:`, error?.code || error?.name);
    status.textContent = `${describeFirebaseError(error)} (${collectionName})`;
    status.className = "summary-status error";
  });
}

async function initialize() {
  $("dateFilterMode").addEventListener("change", () => {
    $("customDateRange").hidden = $("dateFilterMode").value !== "custom";
    render();
  });
  $("includeOwnerScope").addEventListener("change", render);
  $("fromDate").addEventListener("change", render);
  $("toDate").addEventListener("change", render);

  try {
    await firebaseAuthReady;
    firestoreSdk = await getFirebaseFirestoreSdk();
    db = firestoreSdk.getFirestore(await getFirebaseApp());
    subscribe("material_categories", value => { categories = value; });
    subscribe("material_purchases", value => { purchases = value; });
    subscribe("trades", value => { trades = value; });
    onSnapshot(
      collection(db, OWNER_SCOPE_COLLECTION, OWNER_SCOPE_DOCUMENT_ID, "payments"),
      snapshot => {
        ownerScopePayments = snapshot.docs.map(document => ({
          id: document.id,
          ...document.data()
        }));
        loaded.add("ownerScopePayments");
        render();
      },
      error => {
        loadErrors.add("Owner Scope payments");
        console.error("Unable to subscribe to Owner Scope payments:", error?.code || error?.name);
        status.textContent = `${describeFirebaseError(error)} (Owner Scope payments)`;
        status.className = "summary-status error";
      }
    );
  } catch (error) {
    firebaseBootstrapError = error;
    console.error("Unable to initialize Building Cost Summary:", error?.stage || error?.code || error?.name);
    status.textContent = describeFirebaseError(error);
    status.className = "summary-status error";
  }
}

initialize();