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
const doc = firestoreMethod("doc");
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
    status.textContent = loaded.size === 4 ? "Live cost updates are active." : "Loading recorded costs...";
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
    const unsubscribeListeners = [];
    unsubscribeListeners.push(subscribe("material_categories", value => { categories = value; }));
    unsubscribeListeners.push(subscribe("material_purchases", value => { purchases = value; }));
    let tradesRefreshInFlight = false;
    let pendingTradeChange = { refresh_all: true, trade_ids: [] };
    const refreshTrades = async change => {
      if (change && pendingTradeChange) {
        const refreshAll = pendingTradeChange.refresh_all || change.refresh_all;
        pendingTradeChange = {
          refresh_all: refreshAll,
          trade_ids: refreshAll
            ? []
            : [...new Set([...(pendingTradeChange.trade_ids || []), ...(change.trade_ids || [])])]
        };
      } else if (change) {
        pendingTradeChange = change;
      }
      if (tradesRefreshInFlight) return;
      tradesRefreshInFlight = true;
      while (pendingTradeChange) {
        const currentChange = pendingTradeChange;
        pendingTradeChange = null;
        try {
          if (currentChange.refresh_all || !currentChange.trade_ids?.length) {
            const response = await fetch("/api/trades?view=building_cost_summary", {
              credentials: "same-origin",
              cache: "no-store"
            });
            const payload = await response.json();
            if (!response.ok || !Array.isArray(payload.trades)) {
              throw new Error(payload.error || "Trade costs could not be loaded.");
            }
            trades = payload.trades;
          } else {
            const changes = await Promise.all(currentChange.trade_ids.map(async tradeId => {
              const response = await fetch(`/api/trades/${encodeURIComponent(tradeId)}?view=building_cost_summary`, {
                credentials: "same-origin",
                cache: "no-store"
              });
              const payload = await response.json();
              if (response.status === 404) return { tradeId, trade: null };
              if (!response.ok || !payload.trade) throw new Error(payload.error || "Trade costs could not be loaded.");
              return { tradeId, trade: payload.trade };
            }));
            const tradesById = new Map(trades.map(trade => [trade.id, trade]));
            changes.forEach(({ tradeId, trade }) => {
              if (trade) tradesById.set(tradeId, trade);
              else tradesById.delete(tradeId);
            });
            trades = [...tradesById.values()];
          }
          loaded.add("trades");
          loadErrors.delete("trades");
          render();
        } catch (error) {
          loadErrors.add("trades");
          console.error("Unable to load trade costs:", error?.status || error?.name);
          status.textContent = "Trade cost data could not be loaded.";
          status.className = "summary-status error";
        }
      }
      tradesRefreshInFlight = false;
    };
    let firstTradeRevisionSnapshot = true;
    unsubscribeListeners.push(onSnapshot(
      doc(db, "sync_metadata", "trade-projection"),
      snapshot => {
        if (firstTradeRevisionSnapshot) {
          firstTradeRevisionSnapshot = false;
          void refreshTrades({ refresh_all: true, trade_ids: [] });
          return;
        }
        const revision = snapshot.exists() ? snapshot.data() : null;
        void refreshTrades(revision || { refresh_all: true, trade_ids: [] });
      },
      error => {
        loadErrors.add("trades");
        console.error("Unable to subscribe to trade projection revisions:", error?.code || error?.name);
        status.textContent = `${describeFirebaseError(error)} (trade projection revisions)`;
        status.className = "summary-status error";
        if (firstTradeRevisionSnapshot) {
          firstTradeRevisionSnapshot = false;
          void refreshTrades({ refresh_all: true, trade_ids: [] });
        }
      }
    ));
    unsubscribeListeners.push(onSnapshot(
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
    ));
    window.addEventListener("pagehide", () => {
      unsubscribeListeners.forEach(unsubscribe => unsubscribe?.());
    }, { once: true });
  } catch (error) {
    firebaseBootstrapError = error;
    console.error("Unable to initialize Building Cost Summary:", error?.stage || error?.code || error?.name);
    status.textContent = describeFirebaseError(error);
    status.className = "summary-status error";
  }
}

initialize();