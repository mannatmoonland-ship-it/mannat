import { formatDateWithDay, localDateInputValue } from "./date_format.js";
import { sortOwnerScopeItemsByPaymentDate } from "./owner_scope_sort.mjs";
import { downloadOwnerScopePDF } from "./owner_scope_pdf.js";
import { showConfirmDialog } from "./dialogs.js";
import { buildOwnerScopeDeleteConfirmation } from "./owner_scope_delete_confirmation.mjs";

const categoryLabels = Object.freeze({
    labour: "Labour",
    material: "Material",
    equipment_plant: "Equipment / Plant",
    professional_technical: "Professional / Technical",
    government_statutory: "Government / Statutory",
    legal_documentation: "Legal / Documentation",
    site_expense: "Site Expense",
    mixed: "Mixed",
    other: "Other"
});

const page = {
    status: document.getElementById("ownerScopeStatus"),
    paid: document.getElementById("ownerScopePaid"),
    itemCount: document.getElementById("ownerScopeItemCount"),
    downloadPdf: document.getElementById("downloadOwnerScopePdf"),
    itemsBody: document.getElementById("ownerScopeItemsBody"),
    itemsEmpty: document.getElementById("ownerScopeEmpty"),
    dialog: document.getElementById("ownerScopeItemDialog"),
    form: document.getElementById("ownerScopeItemForm"),
    dialogTitle: document.getElementById("ownerScopeDialogTitle"),
    itemName: document.getElementById("ownerScopeItemName"),
    category: document.getElementById("ownerScopeItemCategory"),
    paymentFields: document.getElementById("ownerScopePaymentFields"),
    paymentDate: document.getElementById("ownerScopePaymentDate"),
    amountPaid: document.getElementById("ownerScopeAmountPaid"),
    paymentMode: document.getElementById("ownerScopePaymentMode"),
    remarks: document.getElementById("ownerScopeItemRemarks"),
    formError: document.getElementById("ownerScopeFormError"),
    saveButton: document.getElementById("saveOwnerScopeItem"),
    historyDialog: document.getElementById("ownerScopePaymentHistory"),
    historyTitle: document.getElementById("ownerScopeHistoryTitle"),
    historyBody: document.getElementById("ownerScopePaymentHistoryBody"),
    historyEmpty: document.getElementById("ownerScopePaymentHistoryEmpty"),
    historyTotal: document.getElementById("ownerScopeHistoryTotal")
};

const csrfToken = document.querySelector('meta[name="owner-scope-csrf"]').content;
const createRequestStorageKey = "ownerScope.createRequest";
const money = new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0
});

let ownerScopeData = null;
let editingItemId = null;
let editingPaymentDetails = false;

function showStatus(message, kind = "") {
    page.status.textContent = message;
    if (kind) page.status.dataset.kind = kind;
    else delete page.status.dataset.kind;
}

function asAmount(value) {
    const amount = Number(value ?? 0);
    if (!Number.isFinite(amount)) throw new Error("Owner Scope contains an invalid payment amount.");
    return amount;
}

function formatMoney(amount) {
    return money.format(asAmount(amount));
}

function appendCell(row, value, className = "") {
    const cell = document.createElement("td");
    cell.textContent = value;
    if (className) cell.className = className;
    row.append(cell);
    return cell;
}

function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("en-GB", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        timeZone: "UTC"
    }).format(date);
}

function itemPaymentTotals() {
    const totals = new Map();
    let totalPaid = 0;
    for (const payment of ownerScopeData.payments) {
        const amount = asAmount(payment.amount);
        totalPaid += amount;
        if (payment.expenseItemId) {
            totals.set(payment.expenseItemId, (totals.get(payment.expenseItemId) || 0) + amount);
        }
    }
    return { totals, totalPaid };
}

function renderItems() {
    const { totals, totalPaid } = itemPaymentTotals();
    page.paid.textContent = formatMoney(totalPaid);
    page.itemCount.textContent = String(ownerScopeData.items.length);
    page.itemsBody.replaceChildren();
    page.itemsEmpty.hidden = ownerScopeData.items.length > 0;

    for (const item of sortOwnerScopeItemsByPaymentDate(ownerScopeData.items, ownerScopeData.payments)) {
        const row = document.createElement("tr");
        const paid = totals.get(item.id) || 0;
        const linkedPayment = ownerScopeData.payments.find(payment => payment.expenseItemId === item.id);
        appendCell(row, item.name || "—");
        appendCell(
            row,
            formatDateWithDay(linkedPayment?.date, "—").replace(", ", ",\n"),
            "owner-scope-date"
        );
        appendCell(row, categoryLabels[item.category] || item.category || "—");
        appendCell(row, formatMoney(paid), "owner-scope-money");

        const actionsCell = appendCell(row, "");
        actionsCell.className = "owner-scope-actions";
        for (const [action, label] of [["edit", "Edit"], ["delete", "Delete"]]) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "owner-scope-row-button";
            button.dataset.action = action;
            button.dataset.itemId = item.id;
            button.textContent = label;
            actionsCell.append(button);
        }
        page.itemsBody.append(row);
    }
}

function openPaymentHistory(item) {
    const payments = ownerScopeData.payments.filter(payment => payment.expenseItemId === item.id);
    page.historyTitle.textContent = `${item.name || "Owner Scope Item"} Payment History`;
    page.historyBody.replaceChildren();
    page.historyEmpty.hidden = payments.length > 0;
    page.historyTotal.textContent = formatMoney(
        payments.reduce((total, payment) => total + asAmount(payment.amount), 0)
    );
    for (const payment of payments) {
        const row = document.createElement("tr");
        appendCell(row, formatDate(payment.date));
        appendCell(row, formatMoney(payment.amount), "owner-scope-money");
        appendCell(row, payment.paymentMode || "—");
        appendCell(row, payment.remarks || "—");
        page.historyBody.append(row);
    }
    page.historyDialog.showModal();
}

async function requestJson(url, options = {}) {
    const response = await fetch(url, {
        ...options,
        credentials: "same-origin",
        headers: {
            ...(options.body ? { "Content-Type": "application/json" } : {}),
            ...(options.method && options.method !== "GET" ? { "X-CSRF-Token": csrfToken } : {}),
            ...options.headers
        }
    });
    let body;
    try {
        body = await response.json();
    } catch {
        throw new Error("The server returned an unreadable response.");
    }
    if (!response.ok) throw new Error(body.error || "Owner Scope request failed.");
    return body;
}

function ownerScopeCreateRequestId(payload) {
    const body = JSON.stringify(payload);
    const savedRequest = JSON.parse(
        sessionStorage.getItem(createRequestStorageKey) || "null"
    );
    if (
        savedRequest?.body === body
        && typeof savedRequest.id === "string"
        && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(savedRequest.id)
    ) {
        return savedRequest.id;
    }
    if (typeof globalThis.crypto?.randomUUID !== "function") {
        throw new Error("Secure Owner Scope request IDs are unavailable.");
    }
    const requestId = globalThis.crypto.randomUUID();
    sessionStorage.setItem(createRequestStorageKey, JSON.stringify({ id: requestId, body }));
    return requestId;
}

async function loadOwnerScope() {
    showStatus("Loading Owner Scope data…");
    try {
        ownerScopeData = await requestJson("/api/owner-scope");
        renderItems();
        showStatus("");
    } catch (error) {
        showStatus(error.message, "error");
    }
}

function openItemDialog(item = null) {
    editingItemId = item?.id || null;
    const linkedPayments = item
        ? ownerScopeData.payments.filter(payment => payment.expenseItemId === item.id)
        : [];
    editingPaymentDetails = linkedPayments.length > 0;
    page.form.reset();
    page.formError.hidden = true;
    page.formError.textContent = "";
    const hasPaymentFields = !item || editingPaymentDetails;
    page.paymentFields.hidden = !hasPaymentFields;
    for (const field of [page.paymentDate, page.amountPaid, page.paymentMode]) {
        field.disabled = !hasPaymentFields;
    }
    page.dialogTitle.textContent = editingItemId ? "Edit Owner Scope Item" : "Add Owner Scope Item";
    page.saveButton.textContent = editingItemId ? "Save Changes" : "Save Item";
    if (!item) page.paymentDate.value = localDateInputValue();
    if (item) {
        page.itemName.value = item.name || "";
        page.category.value = item.category || "";
        page.remarks.value = item.remarks || "";
        if (editingPaymentDetails) {
            const payment = linkedPayments[0];
            page.paymentDate.value = String(payment.date || "").match(/^\d{4}-\d{2}-\d{2}/)?.[0] || "";
            page.amountPaid.value = payment.amount ?? "";
            const mode = payment.paymentMode || "";
            if (mode && !Array.from(page.paymentMode.options).some(option => option.value === mode)) {
                page.paymentMode.add(new Option(mode, mode));
            }
            page.paymentMode.value = mode;
        }
    }
    page.dialog.showModal();
    page.itemName.focus();
}

function closeItemDialog() {
    page.dialog.close();
    editingItemId = null;
}

document.getElementById("addOwnerScopeItem").addEventListener("click", () => openItemDialog());
document.getElementById("closeOwnerScopeDialog").addEventListener("click", closeItemDialog);
document.getElementById("cancelOwnerScopeDialog").addEventListener("click", closeItemDialog);
document.getElementById("closeOwnerScopeHistory").addEventListener("click", () => page.historyDialog.close());

page.downloadPdf.addEventListener("click", () => {
    if (!ownerScopeData) {
        showStatus("Owner Scope data is not loaded yet. Please try again.", "error");
        return;
    }
    page.downloadPdf.disabled = true;
    try {
        downloadOwnerScopePDF(ownerScopeData);
        showStatus("Owner Scope PDF downloaded.");
    } catch (error) {
        showStatus(error.message, "error");
    } finally {
        page.downloadPdf.disabled = false;
    }
});

page.itemsBody.addEventListener("click", async event => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const item = ownerScopeData.items.find(candidate => candidate.id === button.dataset.itemId);
    if (!item) return;

    if (button.dataset.action === "edit") {
        openItemDialog(item);
        return;
    }
    if (button.dataset.action === "history") {
        openPaymentHistory(item);
        return;
    }
    const confirmed = await showConfirmDialog(buildOwnerScopeDeleteConfirmation({
        item,
        payments: ownerScopeData.payments,
        formatMoney
    }));
    if (!confirmed) return;

    showStatus("Deleting Owner Scope item…");
    try {
        await requestJson(`/api/owner-scope/items/${encodeURIComponent(item.id)}`, { method: "DELETE" });
        await loadOwnerScope();
    } catch (error) {
        showStatus(error.message, "error");
    }
});

page.form.addEventListener("submit", async event => {
    event.preventDefault();
    page.formError.hidden = true;
    if (!page.form.reportValidity()) return;

    const category = page.category.value;
    if (!Object.hasOwn(categoryLabels, category)) {
        page.formError.textContent = "Choose one of the allowed Owner Scope categories.";
        page.formError.hidden = false;
        return;
    }
    const payload = {
        name: page.itemName.value,
        category,
        remarks: page.remarks.value
    };
    if (!editingItemId || editingPaymentDetails) {
        payload.paymentDate = page.paymentDate.value;
        payload.amountPaid = page.amountPaid.value;
        payload.paymentMode = page.paymentMode.value;
    }
    page.saveButton.disabled = true;
    try {
        if (editingItemId) {
            await requestJson(`/api/owner-scope/items/${encodeURIComponent(editingItemId)}`, {
                method: "PUT",
                body: JSON.stringify(payload)
            });
        } else {
            await requestJson("/api/owner-scope/items", {
                method: "POST",
                body: JSON.stringify(payload),
                headers: {
                    "Idempotency-Key": ownerScopeCreateRequestId(payload)
                }
            });
            sessionStorage.removeItem(createRequestStorageKey);
        }
        closeItemDialog();
        await loadOwnerScope();
    } catch (error) {
        page.formError.textContent = error.message;
        page.formError.hidden = false;
    } finally {
        page.saveButton.disabled = false;
    }
});

loadOwnerScope();
