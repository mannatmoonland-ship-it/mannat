import {
  downloadLedgerPDF,
  paymentLedgerSummary,
  workerCategories
} from "./ledger_pdf.js";
import { uniquePaymentTransactions } from "./trade_payment_history.mjs";
import { sortPaymentTransactions } from "./payment_sort.mjs";
import { formatDate as formatDisplayDate, formatDateWithDay } from "./date_format.js";
import {
  calculateOutstandingBalanceAfterTransaction,
  calculateOutstandingLabourBalance
} from "./settlement_logic.mjs";
import { registerEscClose, unregisterEscClose } from "./dialogs.js";
import { firebaseAuthReady, getFirebaseApp, getFirebaseFirestoreSdk } from "./firebase_auth.js";

const tradeList = document.getElementById("tradeList");
const status = document.getElementById("ledgerStatus");
const search = document.getElementById("tradeSearch");
const modal = document.getElementById("ledgerModal");
const modalTitle = document.getElementById("ledgerModalTitle");
const preview = document.getElementById("ledgerPreview");
let trades = [];
let selectedTrade = null;
let modalOpener = null;
let tradeRevisionUnsubscribe = null;
let pendingTradeRevision = null;
let tradeRevisionRefreshInFlight = false;
const canEditLedger = document.body.dataset.canEditLedger === "true";

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[character]));
}

function money(value) {
  return `INR ${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(Number(value || 0))}`;
}

function formatDate(value) {
  return formatDisplayDate(value);
}

function paymentTypeLabel(type) {
  return { mobilization_advance: "Mobilization Advance", labour_advance: "Labour Advance", labour_settlement: "Labour Settlement", lump_sum_payment: "Lump-Sum Payment", completed_work_payment: "Payment Against Completed Work" }[type] || type || "";
}

function lumpSumTotals(worker, transactions) {
  const payments = transactions.filter(transaction => transaction.type === "lump_sum_payment" &&
    ((worker.id && transaction.workerCategoryId === worker.id) || transaction.workerCategoryName === worker.name));
  const totalPaid = payments.reduce((total, payment) => total + Number(payment.amount || 0), 0);
  const agreedAmount = Number(worker.lump_sum_amount || 0);
  return { totalPaid, remainingBalance: Math.max(0, agreedAmount - totalPaid) };
}

function showStatus(message, type = "normal") {
  status.textContent = message;
  status.className = `ledger-status ${type === "error" ? "error-state" : ""}`;
}

function closeLedgerModal() {
  unregisterEscClose(modal);
  modal.hidden = true;
  const opener = modalOpener;
  modalOpener = null;
  if (opener?.isConnected) opener.focus();
}

function renderTrades() {
  const query = search.value.trim().toLowerCase();
  const visibleTrades = trades.filter(trade => String(trade.name || "").toLowerCase().includes(query));
  if (!visibleTrades.length) {
    tradeList.innerHTML = `<div class="empty-state">${query ? "No trades match your search." : "No trades found in the production collection."}</div>`;
    return;
  }

  tradeList.innerHTML = visibleTrades.map(trade => {
    const categories = workerCategories(trade);
    return `<article class="trade-card">
      <h3>${escapeHtml(trade.name || "Unnamed trade")}</h3>
      <ul class="category-list">${categories.length ? categories.map(worker => `<li>${escapeHtml(worker.name)}</li>`).join("") : "<li>No worker categories</li>"}</ul>
      <div class="trade-actions">
        <button type="button" class="action-button secondary" data-action="view" data-id="${escapeHtml(trade.id)}">View Ledger</button>
        <button type="button" class="action-button" data-action="download" data-id="${escapeHtml(trade.id)}">Download PDF</button>
      </div>
    </article>`;
  }).join("");
}

function paymentHistoryTable(transactions, tradeName) {
  const orderedTransactions = sortPaymentTransactions(transactions);
  const chronologicalTransactions = [...orderedTransactions].reverse();
  const rows = orderedTransactions.length
    ? orderedTransactions.map(item => {
      let pendingLabourPayment = "-";
      if (item.type === "labour_settlement") {
        const outstandingBalance = item.id
          ? calculateOutstandingBalanceAfterTransaction(transactions, item.id)
          : calculateOutstandingLabourBalance(
            chronologicalTransactions.slice(
              0,
              chronologicalTransactions.indexOf(item) + 1
            )
          );
        pendingLabourPayment = money(outstandingBalance);
      }

      const tradeAndCategory = [tradeName, item.workerCategoryName]
        .filter(value => String(value || "").trim())
        .map(escapeHtml)
        .join("<br>");
      const paymentModeAndReference = [item.paymentMode, item.remarks]
        .filter(value => String(value || "").trim())
        .map(escapeHtml)
        .join("<br>");

      return `<tr>
        <td>${escapeHtml(formatDate(item.date))}<br>${escapeHtml(paymentTypeLabel(item.type))}</td>
        <td>${tradeAndCategory}</td>
        <td class="number">${item.type === "labour_settlement" ? money(item.labourEarned) : "-"}</td>
        <td class="number">${item.type === "labour_settlement" ? money(item.advanceAdjustment) : "-"}</td>
        <td class="number">${money(item.amount)}</td>
        <td class="number">${pendingLabourPayment}</td>
        <td>${paymentModeAndReference}</td>
      </tr>`;
    }).join("")
    : `<tr><td colspan="7">No payment transactions recorded.</td></tr>`;
  return `<h3>Payment History</h3><div class="payment-history-table-wrap"><table class="preview-table payment-history-table"><thead><tr><th>Date / Type</th><th>Trade / Worker Category</th><th class="number">Labour Earned</th><th class="number">Advance Deduction</th><th class="number">Amount Paid</th><th class="number">Pending Labour Payment</th><th>Payment Mode / Reference</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function renderPreview(trade) {
  selectedTrade = trade;
  const workers = workerCategories(trade);
  const contracts = Array.isArray(trade.contractItems) ? trade.contractItems : [];
  const transactions = uniquePaymentTransactions(trade.paymentTransactions);
  const summary = paymentLedgerSummary(trade);
  modalTitle.textContent = `${trade.name || "Trade"} Ledger`;
  preview.innerHTML = `<div class="preview-meta">
    <div><strong>Trade</strong>${escapeHtml(trade.name || "Unnamed trade")}</div>
  </div>
  <h3>Contract Account</h3>
  <div class="preview-meta">
    <div><strong>Total Contract Value</strong>${money(summary.contractValue)}</div>
    <div><strong>Total Payments Counted Against Contract</strong>${money(summary.contractPayments)}</div>
    <div><strong>Remaining Contract Value</strong>${money(summary.balanceContractValue)}</div>
    <div><strong>Contract Paid</strong>${summary.paidPercentage.toFixed(2)}%</div>
    <div><strong>Contract Remaining</strong>${summary.remainingPercentage.toFixed(2)}%</div>
  </div>
  <div class="preview-meta contract-payment-breakdown" aria-label="Contract payment breakdown">
    ${summary.contractPaymentBreakdown.map(category => `<div><strong>${escapeHtml(category.label)}</strong>${money(category.amount)}</div>`).join("")}
  </div>
  <h3>Labour Account</h3>
  <div class="preview-meta">
    <div><strong>Total Labour Earned</strong>${money(summary.labourEarned)}</div>
    <div><strong>Total Advances Issued (Lifetime)</strong>${money(summary.labourAdvances)}</div>
    <div><strong>Less: Total Advance Recovered</strong>${money(summary.advanceDeductions)}</div>
    <div><strong>Current Advance Balance to Recover</strong>${money(summary.advanceBalance)}</div>
  </div>
  <h3>Worker Categories</h3>
  ${workers.length ? `<table class="preview-table"><thead><tr><th>Category</th><th>Payment Type</th><th>Amount</th><th>Total Paid</th><th>Remaining</th><th>Effective From</th><th>Work Description</th></tr></thead><tbody>${workers.map(worker => { const totals = worker.payment_type === "lump_sum" ? lumpSumTotals(worker, transactions) : null; return `<tr><td>${escapeHtml(worker.name)}</td><td>${worker.payment_type === "lump_sum" ? "Lump Sum" : "Daily Wage"}</td><td class="number">${money(worker.payment_type === "lump_sum" ? worker.lump_sum_amount : worker.currentWage)}${worker.payment_type === "daily_wage" ? " / day" : ""}</td><td>${totals ? money(totals.totalPaid) : "-"}</td><td>${totals ? money(totals.remainingBalance) : "-"}</td><td>${escapeHtml(formatDateWithDay(worker.effective_from || worker.effectiveFrom || "-"))}</td><td>${escapeHtml(worker.work_description || "")}</td></tr>`; }).join("")}</tbody></table>` : `<div class="empty-state">No worker categories recorded.</div>`}
  ${contracts.length ? `<h3>Work Contracts / Capping</h3><table class="preview-table"><thead><tr><th>Contract Item</th><th class="number">Amount</th></tr></thead><tbody>${contracts.map(item => `<tr><td>${escapeHtml(item.name)}</td><td class="number">${money(item.amount)}</td></tr>`).join("")}</tbody></table>` : ""}
  ${paymentHistoryTable(transactions, trade.name || "")}`;
  modal.hidden = false;
  registerEscClose(modal, closeLedgerModal);
}

function renderEditForm(trade) {
  selectedTrade = trade;
  const contracts = Array.isArray(trade.contractItems) ? trade.contractItems : [];
  modalTitle.textContent = `Edit ${trade.name || "Trade"} Ledger`;
  preview.innerHTML = `<form id="ledgerEditForm" class="edit-form">
    <label>Trade Name<input name="tradeName" value="${escapeHtml(trade.name || "")}" required></label>
    <div>
      <h3>Work Contracts / Capping</h3>
      <div id="contractEditor" class="contract-editor">${contracts.map((item, index) => contractEditorRow(item, index)).join("")}</div>
      <button type="button" class="action-button secondary" data-edit-action="add-contract">+ Add Contract Item</button>
    </div>
    <div class="edit-form-actions">
      <button type="button" class="action-button secondary" data-edit-action="cancel">Cancel</button>
      <button type="submit" class="action-button">Save Changes</button>
    </div>
  </form>`;
}

function contractEditorRow(item = {}, index = 0) {
  return `<div class="contract-editor-row" data-contract-row>
    <input type="hidden" data-contract-id value="${escapeHtml(item.id || "")}">
    <input name="contractName" data-contract-name value="${escapeHtml(item.name || "")}" placeholder="Contract item" aria-label="Contract item ${index + 1}">
    <input name="contractAmount" data-contract-amount type="number" min="0" step="0.01" value="${Number(item.amount || 0)}" placeholder="Amount" aria-label="Contract amount ${index + 1}">
    <button type="button" data-edit-action="remove-contract" aria-label="Remove contract item">×</button>
  </div>`;
}

async function saveLedgerEdits(event) {
  event.preventDefault();
  if (!canEditLedger) return;
  if (!selectedTrade) return;
  const form = event.currentTarget;
  const contractItems = [...form.querySelectorAll("[data-contract-row]")].map(row => ({
    id: row.querySelector("[data-contract-id]").value || undefined,
    name: row.querySelector("[data-contract-name]").value.trim(),
    amount: Number(row.querySelector("[data-contract-amount]").value || 0)
  })).filter(item => item.name);
  if (contractItems.some(item => !Number.isFinite(item.amount) || item.amount < 0)) {
    showStatus("Contract amounts must be zero or greater.", "error");
    return;
  }
  const name = form.elements.tradeName.value.trim().toUpperCase();
  if (!name) {
    showStatus("Enter a trade name.", "error");
    return;
  }
  try {
    const csrfToken = document.body.dataset.contractValuesCsrf || "";
    const contractResponse = await fetch(
      `/api/trades/${encodeURIComponent(selectedTrade.id)}/contract-items`,
      {
        method: "PUT",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken
        },
        body: JSON.stringify({ contractItems })
      }
    );
    const contractResult = await contractResponse.json();
    if (!contractResponse.ok) {
      const error = new Error(contractResult.error || "Unable to save contract values.");
      error.safeApiMessage = true;
      throw error;
    }
    if (!Array.isArray(contractResult.contractItems)) {
      throw new Error("The server returned an invalid contract update.");
    }
    const nameResponse = await fetch(
      `/api/trades/${encodeURIComponent(selectedTrade.id)}/name`,
      {
        method: "PATCH",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": document.body.dataset.tradeBuilderCsrf || ""
        },
        body: JSON.stringify({ name })
      }
    );
    const nameResult = await nameResponse.json();
    if (!nameResponse.ok) {
      const error = new Error(nameResult.error || "Unable to update the trade name.");
      error.safeApiMessage = true;
      throw error;
    }
    const updatedTrade = {
      ...selectedTrade,
      name,
      contractItems: contractResult.contractItems,
      updatedAt: nameResult.updatedAt
    };
    trades = trades.map(trade => trade.id === updatedTrade.id ? updatedTrade : trade).sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")));
    renderTrades();
    modalOpener = [...tradeList.querySelectorAll('button[data-action="view"]')]
      .find(button => button.dataset.id === updatedTrade.id) || modalOpener;
    renderPreview(updatedTrade);
    showStatus("Ledger updated successfully.");
  } catch (error) {
    console.error(error);
    showStatus(error?.safeApiMessage ? error.message : "Unable to save the ledger. Please try again.", "error");
  }
}

async function loadTrades(tradeIds = null) {
  try {
    let nextTrades;
    if (Array.isArray(tradeIds)) {
      const changes = await Promise.all(tradeIds.map(async tradeId => {
        const response = await fetch(`/api/trades/${encodeURIComponent(tradeId)}?view=ledger`, {
          credentials: "same-origin",
          cache: "no-store"
        });
        const payload = await response.json();
        if (response.status === 404) return { tradeId, trade: null };
        if (!response.ok || !payload.trade) throw new Error(payload.error || "Unable to load trade ledger data.");
        return { tradeId, trade: payload.trade };
      }));
      const tradesById = new Map(trades.map(trade => [trade.id, trade]));
      changes.forEach(({ tradeId, trade }) => {
        if (trade) tradesById.set(tradeId, trade);
        else tradesById.delete(tradeId);
      });
      nextTrades = [...tradesById.values()];
    } else {
      showStatus("Loading trades...");
      const response = await fetch("/api/trades?view=ledger", {
        credentials: "same-origin",
        cache: "no-store"
      });
      const payload = await response.json();
      if (!response.ok || !Array.isArray(payload.trades)) {
        throw new Error(payload.error || "Unable to load trade ledger data.");
      }
      nextTrades = payload.trades;
    }
    trades = nextTrades
      .sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")));
    showStatus(`${trades.length} trade${trades.length === 1 ? "" : "s"} available.`);
    renderTrades();
  } catch (error) {
    console.error("Unable to load Trade Ledger data:", error?.code || error?.stage || error?.name);
    showStatus(error?.message || "Unable to load ledger data.", "error");
    tradeList.innerHTML = "";
  }
}

async function subscribeToTradeRevisions() {
  try {
    await firebaseAuthReady;
    const firestore = await getFirebaseFirestoreSdk();
    const db = firestore.getFirestore(await getFirebaseApp());
    let isInitialSnapshot = true;
    tradeRevisionUnsubscribe = firestore.onSnapshot(
      firestore.doc(db, "sync_metadata", "trade-projection"),
      snapshot => {
        if (isInitialSnapshot) {
          isInitialSnapshot = false;
          pendingTradeRevision = { refresh_all: true, trade_ids: [] };
          if (!modal.open) void applyPendingTradeRevision();
          return;
        }
        const revision = snapshot.exists()
          ? snapshot.data()
          : { refresh_all: true, trade_ids: [] };
        if (pendingTradeRevision) {
          const refreshAll = pendingTradeRevision.refresh_all || revision.refresh_all;
          pendingTradeRevision = {
            refresh_all: refreshAll,
            trade_ids: refreshAll
              ? []
              : [...new Set([...(pendingTradeRevision.trade_ids || []), ...(revision.trade_ids || [])])]
          };
        } else {
          pendingTradeRevision = revision;
        }
        if (!modal.open) void applyPendingTradeRevision();
      },
      error => {
        console.error("Unable to subscribe to Trade Ledger revisions:", error?.code || error?.name);
        showStatus("Trade Ledger could not receive live updates.", "error");
        if (isInitialSnapshot) void loadTrades();
      }
    );
    window.addEventListener("pagehide", () => tradeRevisionUnsubscribe?.(), { once: true });
  } catch (error) {
    console.error("Unable to initialize Trade Ledger live updates:", error?.stage || error?.code || error?.name);
    showStatus("Trade Ledger could not receive live updates.", "error");
    await loadTrades();
  }
}

async function applyPendingTradeRevision() {
  if (!pendingTradeRevision || modal.open || tradeRevisionRefreshInFlight) return;
  const revision = pendingTradeRevision;
  pendingTradeRevision = null;
  tradeRevisionRefreshInFlight = true;
  try {
    await loadTrades(revision.refresh_all ? null : revision.trade_ids);
  } finally {
    tradeRevisionRefreshInFlight = false;
    if (pendingTradeRevision && !modal.open) void applyPendingTradeRevision();
  }
}

modal.addEventListener("close", () => void applyPendingTradeRevision());
modal.addEventListener("cancel", () => void applyPendingTradeRevision());

tradeList.addEventListener("click", async event => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const trade = trades.find(item => item.id === button.dataset.id);
  if (!trade) return;
  if (button.dataset.action === "view") {
    modalOpener = button;
    renderPreview(trade);
    return;
  }
  button.disabled = true;
  try {
    await downloadLedgerPDF(trade);
  } catch (error) {
    console.error(error);
    showStatus("Unable to create the PDF. Please try again.", "error");
  } finally {
    button.disabled = false;
  }
});

preview.addEventListener("submit", saveLedgerEdits);
preview.addEventListener("click", event => {
  const button = event.target.closest("button[data-edit-action]");
  if (!button) return;
  if (button.dataset.editAction === "cancel") {
    renderPreview(selectedTrade);
  } else if (button.dataset.editAction === "add-contract") {
    const editor = document.getElementById("contractEditor");
    editor.insertAdjacentHTML("beforeend", contractEditorRow({}, editor.children.length));
  } else if (button.dataset.editAction === "remove-contract") {
    button.closest("[data-contract-row]").remove();
  }
});

search.addEventListener("input", renderTrades);
document.getElementById("editLedger").addEventListener("click", () => {
  if (canEditLedger && selectedTrade) renderEditForm(selectedTrade);
});
document.getElementById("downloadModalPdf").addEventListener("click", async event => {
  if (!selectedTrade) return;
  const button = event.currentTarget;
  button.disabled = true;
  try { await downloadLedgerPDF(selectedTrade); } catch (error) { console.error(error); showStatus("Unable to create the PDF. Please try again.", "error"); } finally { button.disabled = false; }
});
document.getElementById("closeLedger").addEventListener("click", closeLedgerModal);
modal.addEventListener("click", event => { if (event.target === modal) closeLedgerModal(); });
void subscribeToTradeRevisions();
