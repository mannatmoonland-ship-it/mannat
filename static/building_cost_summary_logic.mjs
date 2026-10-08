import {
  LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
  OWNER_SCOPE_COLLECTION,
  OWNER_SCOPE_DOCUMENT_ID
} from "./owner_scope_constants.mjs";

const ACTUAL_PAYMENT_TYPES = new Set([
  "mobilization_advance",
  "labour_advance",
  "labour_settlement",
  "completed_work_payment",
  "lump_sum_payment"
]);

export function formatRupees(value) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0
  }).format(Number(value || 0));
}

function dateKey(value) {
  if (value && typeof value.toDate === "function") {
    return value.toDate().toISOString().slice(0, 10);
  }
  if (value instanceof Date && Number.isFinite(value.getTime())) {
    return value.toISOString().slice(0, 10);
  }
  const match = String(value ?? "").match(/^\d{4}-\d{2}-\d{2}/);
  return match ? match[0] : "";
}

function isInDateRange(value, fromDate, toDate) {
  if (!fromDate && !toDate) return true;
  const key = dateKey(value);
  if (!key) return false;
  return (!fromDate || key >= fromDate) && (!toDate || key <= toDate);
}

function validAmount(value) {
  if (value === null || value === undefined || String(value).trim() === "") return null;
  const amount = Number(value);
  return Number.isFinite(amount) && amount >= 0 ? amount : null;
}

export function calculateBuildingCosts({
  categories = [],
  purchases = [],
  trades = [],
  ownerScopePayments = [],
  fromDate = "",
  toDate = "",
  includeOwnerScope = true
} = {}) {
  if (fromDate && toDate && fromDate > toDate) {
    throw new RangeError("From date must be on or before To date.");
  }

  const categoryTotals = new Map(categories.map(category => [category.id, {
    id: category.id,
    name: String(category.name || "Uncategorized"),
    total: 0
  }]));
  const seenPurchaseIds = new Set();

  purchases.forEach((purchase, index) => {
    if (purchase?.id && seenPurchaseIds.has(purchase.id)) return;
    if (purchase?.id) seenPurchaseIds.add(purchase.id);
    if (!isInDateRange(purchase?.date, fromDate, toDate)) return;
    const amount = validAmount(purchase?.totalAmount);
    if (amount === null) return;

    const categoryId = purchase.categoryId || "";
    const category = categories.find(item => item.id === categoryId);
    const key = category?.id || `uncategorized:${categoryId || purchase.categoryName || index}`;
    if (!categoryTotals.has(key)) {
      categoryTotals.set(key, {
        id: key,
        name: String(category?.name || purchase.categoryName || "Uncategorized"),
        total: 0
      });
    }
    categoryTotals.get(key).total += amount;
  });

  const tradeTotals = new Map(trades.map(trade => [trade.id, {
    id: trade.id,
    name: String(trade.name || "Unnamed trade"),
    total: 0
  }]).filter(([tradeId]) => tradeId !== LEGACY_OWNER_ACCOUNT_DOCUMENT_ID));
  const seenPaymentIds = new Set();

  trades.forEach((trade, tradeIndex) => {
    if (trade.id === LEGACY_OWNER_ACCOUNT_DOCUMENT_ID) return;
    const transactions = Array.isArray(trade.paymentTransactions) ? trade.paymentTransactions : [];
    transactions.forEach((transaction, transactionIndex) => {
      if (!ACTUAL_PAYMENT_TYPES.has(transaction?.type)) return;
      if (transaction?.id && seenPaymentIds.has(transaction.id)) return;
      if (transaction?.id) seenPaymentIds.add(transaction.id);
      if (!isInDateRange(transaction?.date, fromDate, toDate)) return;
      const amount = validAmount(transaction?.amount);
      if (amount === null) return;

      const tradeId = trade.id || `trade:${tradeIndex}`;
      if (!tradeTotals.has(tradeId)) {
        tradeTotals.set(tradeId, { id: tradeId, name: String(trade.name || "Unnamed trade"), total: 0 });
      }
      tradeTotals.get(tradeId).total += amount;
    });
  });

  const materialBreakdown = [...categoryTotals.values()].sort((left, right) => left.name.localeCompare(right.name));
  const tradeBreakdown = [...tradeTotals.values()].sort((left, right) => left.name.localeCompare(right.name));
  const materialTotal = materialBreakdown.reduce((total, category) => total + category.total, 0);
  const tradeTotal = tradeBreakdown.reduce((total, trade) => total + trade.total, 0);
  const seenOwnerPaymentIds = new Set();
  const ownerScopeTotal = ownerScopePayments.reduce((total, payment) => {
    if (payment?.id && seenOwnerPaymentIds.has(payment.id)) return total;
    if (payment?.id) seenOwnerPaymentIds.add(payment.id);
    if (!isInDateRange(payment?.date, fromDate, toDate)) return total;
    const amount = validAmount(payment?.amount);
    return amount === null ? total : total + amount;
  }, 0);

  return {
    materialTotal,
    tradeTotal,
    ownerScopeTotal,
    total: materialTotal + tradeTotal + (includeOwnerScope ? ownerScopeTotal : 0),
    materials: materialBreakdown,
    trades: tradeBreakdown,
    ownerScope: {
      id: OWNER_SCOPE_DOCUMENT_ID,
      collection: OWNER_SCOPE_COLLECTION,
      total: ownerScopeTotal
    }
  };
}
