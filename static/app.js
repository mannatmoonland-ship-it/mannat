import {
    showAlert,
    showConfirm,
    showConfirmDialog,
    showPrompt,
    showSuccess,
    showError,
    registerEscClose,
    unregisterEscClose
} from "./dialogs.js";

import {
    describeFirebaseError,
    firebaseAuthReady,
    getFirebaseApp,
    getFirebaseFirestoreSdk
} from "./firebase_auth.js";

import {
    calculateAdvanceBalance,
    calculateMaximumSettlementAmount,
    calculateSettlementBreakdown,
    calculateOutstandingBalanceAfterTransaction,
    applySettlementFormReset
} from "./settlement_logic.mjs";

import {
    calculateContractPaymentSummary
} from "./contract_payment_summary.mjs";

import {
    downloadLedgerPDF,
    downloadPaymentLedgerPDF
} from "./ledger_pdf.js";

import {
    sortPaymentTransactions
} from "./payment_sort.mjs";

import {
    formatDateWithDay
} from "./date_format.js";

import {
    LEGACY_OWNER_ACCOUNT_DOCUMENT_ID
} from "./owner_scope_constants.mjs";


function generateUuid() {

    const cryptoApi = globalThis.crypto;

    if (typeof cryptoApi.randomUUID === "function") {

        return cryptoApi.randomUUID();

    }

    if (typeof cryptoApi.getRandomValues !== "function") {

        throw new Error("Secure random number generation is unavailable.");

    }

    const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;

    const hex = Array.from(
        bytes,
        byte => byte.toString(16).padStart(2, "0")
    ).join("");

    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;

}


/* =========================================================
   FIREBASE
========================================================= */

function withTimeout(promise, timeoutMs, message) {
    return new Promise((resolve, reject) => {
        const timeoutId = setTimeout(() => reject(new Error(message)), timeoutMs);
        Promise.resolve(promise).then(
            value => {
                clearTimeout(timeoutId);
                resolve(value);
            },
            error => {
                clearTimeout(timeoutId);
                reject(error);
            }
        );
    });
}

let db = null;
let firebaseInitializationError = null;
let firestoreSdk = null;
const firestoreMethod = name => (...args) => {
    const method = firestoreSdk?.[name];
    if (typeof method !== "function") {
        throw firebaseInitializationError || new Error("The Firebase Firestore SDK is unavailable.");
    }
    return method(...args);
};
const collection = firestoreMethod("collection");
const getDocs = firestoreMethod("getDocs");
const getDoc = firestoreMethod("getDoc");
const doc = firestoreMethod("doc");
try {
    await firebaseAuthReady;
    firestoreSdk = await getFirebaseFirestoreSdk();
    const firebaseApp = await getFirebaseApp();
    db = firestoreSdk.getFirestore(firebaseApp);
} catch (error) {
    firebaseInitializationError = error;
    console.error("Firebase initialization failed:", error?.stage || error?.code || error?.name);
}


/* =========================================================
   COLLECTION
========================================================= */

const TRADES_COLLECTION =
    "trades";

const COMPLETED_WORK_PAYMENT_TYPE =
    "completed_work_payment";


/* =========================================================
   GLOBAL VARIABLES
========================================================= */

let editingTradeId = null;
let pendingTradeCreateRequest = null;

async function sendTradeMutation(url, method, payload = null) {
    const response = await fetch(url, {
        method,
        credentials: "same-origin",
        headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": document.body.dataset.tradeBuilderCsrf || ""
        },
        body: payload === null ? null : JSON.stringify(payload)
    });
    let result;
    try {
        result = await response.json();
    } catch {
        throw new Error("The server returned an unreadable response.");
    }
    if (!response.ok) {
        throw new Error(result.error || "Unable to save Trade Builder changes.");
    }
    return result;
}

let currentPaymentTrade = null;

let currentViewLedgerTrade = null;

let editingPaymentId = null;

let paymentSaveInProgress = false;

let paymentDeleteInProgress = false;

let pendingPaymentCreateId = null;

let settlementAmountUserEdited = false;

const financialOnly =
    document.body.classList.contains("financial-module");

const tradesById = new Map();

function getTradeWorkerCategories(trade) {
    const loadedTrade = tradesById.get(trade?.id) || trade;
    const source = Array.isArray(loadedTrade?.workerCategories)
        ? loadedTrade.workerCategories
        : Array.isArray(loadedTrade?.workers)
            ? loadedTrade.workers
            : [];

    const seen = new Set();

    return source.filter(worker => {
        const name = String(worker?.name || "").trim();
        const key = name.toLowerCase();
        if (!name || seen.has(key)) return false;
        seen.add(key);
        return true;
    }).map(worker => ({
        ...worker,
        payment_type: worker.payment_type || "daily_wage",
        effective_from: worker.effective_from || worker.effectiveFrom || ""
    }));
}


/* =========================================================
   COMMON FIELDS
========================================================= */

const COMMON_FIELDS = [

    "Work / Activity",

    "Location / Floor",

    "Materials",

    "Notes / Issues / Decisions",

    "Remarks / Additional Items",

    "Visual Evidence"

];


/* =========================================================
   TODAY
========================================================= */

function todayString() {

    const now =
        new Date();

    const year =
        now.getFullYear();

    const month =
        String(
            now.getMonth() + 1
        ).padStart(2, "0");

    const day =
        String(
            now.getDate()
        ).padStart(2, "0");

    return `${year}-${month}-${day}`;
}


/* =========================================================
   MONEY
========================================================= */

function money(value) {

    const num = Number(value || 0);

    const formatted = new Intl.NumberFormat(
        "en-IN",
        {
            maximumFractionDigits: 0
        }
    ).format(
        num
    );

    return `INR ${formatted}`;

}


/* =========================================================
   STATUS
========================================================= */

function showStatus(
    message,
    isError = false
) {

    const el =
        document.getElementById(
            "status"
        );

    if (!el) {
        return;
    }

    el.textContent =
        message;

    el.classList.toggle(
        "error",
        isError
    );

    el.style.display =
        "block";

    setTimeout(
        () => {

            el.style.display =
                "none";

        },
        3500
    );

}


/* =========================================================
   ESCAPE HTML
========================================================= */

function escapeHtml(value) {

    return String(
        value ?? ""
    )
        .replaceAll(
            "&",
            "&amp;"
        )
        .replaceAll(
            "<",
            "&lt;"
        )
        .replaceAll(
            ">",
            "&gt;"
        )
        .replaceAll(
            '"',
            "&quot;"
        )
        .replaceAll(
            "'",
            "&#039;"
        );

}


/* =========================================================
   WORKER CATEGORY
========================================================= */

function addWorkerCategory(worker = null) {
        const container = document.getElementById("workerContainer");
        if (!container) return;

        const wrapper = document.createElement("div");
        const paymentType = worker?.payment_type === "lump_sum" ? "lump_sum" : "daily_wage";
        const effectiveFrom = worker?.effective_from || worker?.effectiveFrom || todayString();
        wrapper.className = "worker-card";
        wrapper.dataset.workerId = worker?.id || "";
        wrapper.innerHTML = `
            <div class="worker-header">
                <div class="worker-name">Worker Category</div>
                <button type="button" class="btn-danger btn-small delete-worker">Delete</button>
            </div>
            <div class="worker-grid">
                <div>
                    <label>Worker Category</label>
                    <input class="worker-name-input" type="text" value="${escapeHtml(worker?.name || "")}" placeholder="Example: Helpers">
                </div>
                <div>
                    <label>Payment Type</label>
                    <select class="worker-payment-type">
                        <option value="daily_wage" ${paymentType === "daily_wage" ? "selected" : ""}>Daily Wage</option>
                        <option value="lump_sum" ${paymentType === "lump_sum" ? "selected" : ""}>Lump Sum</option>
                    </select>
                </div>
            </div>
            <div class="worker-daily-fields">
                <div>
                    <label>Daily Wage (₹)</label>
                    <input class="worker-wage-input" type="number" min="0" step="1" value="${worker?.currentWage ?? ""}" placeholder="0">
                </div>
                <div>
                    <label>Effective From</label>
                    <input class="worker-date-input" type="date" value="${effectiveFrom}">
                </div>
                <div class="wage-history">
                    <div style="font-size:11px;font-weight:bold;color:#64748b;margin-bottom:5px;">Wage History</div>
                    <div class="history-container">${renderHistory(worker?.wageHistory || [])}</div>
                </div>
            </div>
            <div class="worker-lump-sum-fields" ${paymentType !== "lump_sum" ? "hidden" : ""}>
                <div>
                    <label>Agreed Lump-Sum Amount (₹)</label>
                    <input class="worker-lump-sum-input" type="number" min="0" step="1" value="${worker?.lump_sum_amount ?? ""}" placeholder="0">
                </div>
                <div>
                    <label>Effective From</label>
                    <input class="worker-lump-sum-date-input" type="date" value="${effectiveFrom}">
                </div>
                <div>
                    <label>Work Description (Optional)</label>
                    <textarea class="worker-work-description-input" rows="2" placeholder="Describe the agreed work">${escapeHtml(worker?.work_description || "")}</textarea>
                </div>
            </div>
        `;

        wrapper.querySelector(".worker-payment-type").addEventListener("change", () => updateWorkerPaymentFields(wrapper));
        wrapper.querySelector(".delete-worker").addEventListener("click", () => wrapper.remove());
        container.appendChild(wrapper);
        updateWorkerPaymentFields(wrapper);
    }

    function updateWorkerPaymentFields(card) {
        const isLumpSum = card.querySelector(".worker-payment-type").value === "lump_sum";
        card.querySelector(".worker-daily-fields").hidden = isLumpSum;
        card.querySelector(".worker-lump-sum-fields").hidden = !isLumpSum;
    }

function renderHistory(history) {
    if (
        !history ||
        history.length === 0
    ) {

        return `

            <div
                style="
                    color:#94a3b8;
                    font-size:11px;
                "
            >
                No wage history yet.
            </div>

        `;

    }

    return history
        .slice()
        .sort(
            (
                a,
                b
            ) =>

                String(
                    b.effectiveFrom || ""
                ).localeCompare(
                    String(
                        a.effectiveFrom || ""
                    )
                )

        )
        .map(
            item => `

                <div class="history-row">

                    <span>

                        ${formatDateWithDay(
                            item.effectiveFrom
                        )}

                    </span>

                    <strong>

                        ${money(
                            item.dailyWage
                        )}

                    </strong>

                </div>

            `
        )
        .join("");

}


/* =========================================================
   GET WORKER CATEGORIES
========================================================= */

function getWorkerCategories() {
    return [...document.querySelectorAll(".worker-card")].flatMap(card => {
        const name = card.querySelector(".worker-name-input").value.trim();
        const paymentType = card.querySelector(".worker-payment-type").value;
        if (!name) return [];

        const effectiveFrom = paymentType === "lump_sum"
            ? card.querySelector(".worker-lump-sum-date-input").value
            : card.querySelector(".worker-date-input").value;
        const worker = {
            id: card.dataset.workerId || "",
            name,
            payment_type: paymentType,
            effectiveFrom,
            effective_from: effectiveFrom
        };

        if (paymentType === "lump_sum") {
            worker.lump_sum_amount = Number(card.querySelector(".worker-lump-sum-input").value || 0);
            worker.work_description = card.querySelector(".worker-work-description-input").value.trim();
        } else {
            worker.currentWage = Number(card.querySelector(".worker-wage-input").value || 0);
            worker.wageHistory = [];
        }
        return [worker];
    });
}


/* =========================================================
   BUILD WAGE HISTORY
========================================================= */

function buildWorkerWithHistory(
    newWorker,
    oldWorker
) {

    const workerId =
        newWorker.id || oldWorker?.id || generateUuid();

    if (newWorker.payment_type === "lump_sum") {
        return {
            ...(oldWorker || {}),
            ...newWorker,
            id: workerId,
            payment_type: "lump_sum",
            lump_sum_amount: Number(newWorker.lump_sum_amount || 0),
            work_description: newWorker.work_description || "",
            effective_from: newWorker.effective_from || newWorker.effectiveFrom || "",
            effectiveFrom: newWorker.effectiveFrom || newWorker.effective_from || ""
        };
    }

    const oldHistory =
        Array.isArray(
            oldWorker?.wageHistory
        )
            ? [
                ...oldWorker.wageHistory
            ]
            : [];

    const oldWage =
        Number(
            oldWorker?.currentWage || 0
        );

    const oldDate =
        oldWorker?.effective_from || oldWorker?.effectiveFrom ||
        "";

    const newWage =
        Number(
            newWorker.currentWage || 0
        );

    const newDate =
        newWorker.effective_from || newWorker.effectiveFrom ||
        "";

    let history = [
        ...oldHistory
    ];

    if (
        !oldWorker &&
        newWage > 0 &&
        newDate
    ) {

        history.push({

            dailyWage:
                newWage,

            effectiveFrom:
                newDate,

            recordedAt:
                new Date().toISOString()

        });

    }

    else if (

        oldWorker &&

        (
            oldWage !== newWage ||
            oldDate !== newDate
        )

    ) {

        history.push({

            dailyWage:
                newWage,

            effectiveFrom:
                newDate,

            recordedAt:
                new Date().toISOString()

        });

    }

    const unique = [];

    const seen =
        new Set();

    history.forEach(
        item => {

            const key =
                `${item.effectiveFrom}_${item.dailyWage}`;

            if (
                !seen.has(key)
            ) {

                seen.add(key);

                unique.push(
                    item
                );

            }

        }
    );

    return {
        ...(oldWorker || {}),
        ...newWorker,
        id: workerId,
        payment_type: "daily_wage",

        name:
            newWorker.name,

        currentWage:
            newWage,

        effectiveFrom:
            newDate,

        effective_from:
            newDate,

        wageHistory:
            unique

    };

}


/* =========================================================
   RESET FORM
========================================================= */

function resetForm() {

    editingTradeId =
        null;

    const formTitle =
        document.getElementById(
            "formTitle"
        );

    if (formTitle) {

        formTitle.textContent =
            "Create Trade";

    }

    document.getElementById(
        "tradeName"
    ).value =
        "";

    document.getElementById(
        "workerContainer"
    ).innerHTML =
        "";

    addWorkerCategory();

}


/* =========================================================
   LOAD TRADES
========================================================= */

async function loadTrades() {

    const list =
        document.getElementById(
            "tradeList"
        );

    try {

        if (!db) {
            throw firebaseInitializationError || new Error("Firebase is not initialized.");
        }

        const snapshot =
            await withTimeout(getDocs(
                collection(
                    db,
                    TRADES_COLLECTION
                )
            ), 20000, "Timed out while loading trades. Please check your connection and try again.");

        tradesById.clear();

        const trades = [];
        snapshot.forEach(item => {
            if (item.id === LEGACY_OWNER_ACCOUNT_DOCUMENT_ID) {
                return;
            }
            trades.push({
                id: item.id,
                ...item.data()
            });
        });

        if (!trades.length) {

            const selector = document.getElementById("financialTradeSelect");
            if (selector) {
                selector.innerHTML = '<option value="">No trades available.</option>';
                selector.disabled = true;
            }

            list.innerHTML = `

                <div class="empty">

                    No Trades created yet.

                    <br><br>

                    Create the first Trade
                    on the left.

                </div>

            `;

            return;

        }

        trades.forEach(trade => tradesById.set(trade.id, trade));

        trades.sort(
            (
                a,
                b
            ) =>

                String(
                    a.name || ""
                ).localeCompare(
                    String(
                        b.name || ""
                    )
                )

        );

        if (financialOnly) {
            const selector = document.getElementById("financialTradeSelect");
            const previousSelection = selector?.value || "";
            if (selector) {
                selector.disabled = false;
                selector.innerHTML = '<option value="">Select Trade</option>' + trades.map(
                    trade => `<option value="${escapeHtml(trade.id)}">${escapeHtml(trade.name || "Unnamed trade")}</option>`
                ).join("");
            }

            const selectedTradeId = trades.some(trade => trade.id === previousSelection)
                ? previousSelection
                : "";
            if (selector) {
                selector.value = selectedTradeId;
            }
            renderSelectedFinancialTrade(selectedTradeId);
            return;
        }

        list.innerHTML =
            "";

        trades.forEach(
            trade => {

                renderTrade(
                    trade
                );

            }
        );

    }

    catch (error) {

        console.error(
            error
        );

        list.innerHTML = `

            <div class="empty">

                Unable to load Trades.

                <br><br>

                ${escapeHtml(describeFirebaseError(error))}

            </div>

        `;

        const selector = document.getElementById("financialTradeSelect");
        if (selector) {
            selector.innerHTML = '<option value="">Unable to load trades.</option>';
            selector.disabled = true;
        }

    }

}

function renderSelectedFinancialTrade(tradeId) {
    const trade = tradesById.get(tradeId);
    const list = document.getElementById("tradeList");
    if (!list) return;

    list.innerHTML = "";
    if (trade) {
        renderTrade(trade);
    } else {
        list.innerHTML = '<div class="empty">Please select a trade to view its payment details.</div>';
    }
}


async function sendContractValueMutation(path, method, payload) {
    const csrfToken = document.body.dataset.contractValuesCsrf || "";
    if (!csrfToken) {
        throw new Error("Your session expired. Reload the page and try again.");
    }
    const response = await fetch(path, {
        method,
        credentials: "same-origin",
        headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken
        },
        ...(payload ? { body: JSON.stringify(payload) } : {})
    });
    let result = {};
    try {
        result = await response.json();
    } catch {
        throw new Error("Unable to save contract values. Please try again.");
    }
    if (!response.ok) {
        throw new Error(typeof result.error === "string" ? result.error : "Unable to save contract values. Please try again.");
    }
    if (!Array.isArray(result.contractItems)) {
        throw new Error("The server returned an invalid contract update.");
    }
    return result;
}


/* =========================================================
   CONTRACT TOTAL
========================================================= */

function calculateContractTotal(
    contractItems
) {

    if (
        !Array.isArray(
            contractItems
        )
    ) {

        return 0;

    }

    return contractItems.reduce(
        (
            total,
            item
        ) =>

            total +
            Number(
                item.amount || 0
            ),

        0
    );

}


/* =========================================================
   TOTAL PAYMENTS
========================================================= */

function calculateTotalPayments(
    transactions
) {

    if (
        !Array.isArray(
            transactions
        )
    ) {

        return 0;

    }

    return transactions.reduce(
        (
            total,
            transaction
        ) =>
            transaction.type === "lump_sum_payment"
                ? total
                : total + Number(transaction.amount || 0),

        0
    );

}

function getLumpSumCategories(trade) {
    return getTradeWorkerCategories(trade).filter(worker =>
        (worker.payment_type || "daily_wage") === "lump_sum"
    );
}

function getLumpSumPaymentTotals(worker, transactions = [], excludedPaymentId = null) {
    const payments = transactions.filter(transaction =>
        transaction.type === "lump_sum_payment" &&
        transaction.id !== excludedPaymentId &&
        (
            (worker.id && transaction.workerCategoryId === worker.id) ||
            transaction.workerCategoryName === worker.name
        )
    );
    const totalPaid = payments.reduce((total, payment) => total + Number(payment.amount || 0), 0);
    const agreedAmount = Number(worker.lump_sum_amount || 0);
    return {
        payments,
        agreedAmount,
        totalPaid,
        remainingBalance: Math.max(0, agreedAmount - totalPaid)
    };
}


function calculateOutstandingLabourBalance(
    transactions
) {

    if (
        !Array.isArray(
            transactions
        )
    ) {

        return 0;

    }

    let runningOutstanding =
        0;

    const chronologicalTransactions =
        [...transactions].sort(
            (
                a,
                b
            ) =>
                String(
                    a.date || ""
                ).localeCompare(
                    String(
                        b.date || ""
                    )
                )
        );

    chronologicalTransactions.forEach(
        transaction => {

            if (
                transaction.type !==
                "labour_settlement"
            ) {
                return;
            }

            const hasStoredOutstanding =
                transaction.outstandingBalance !==
                undefined &&
                transaction.outstandingBalance !==
                null &&
                transaction.outstandingBalance !==
                "" &&
                Number.isFinite(
                    Number(
                        transaction.outstandingBalance
                    )
                );

            if (
                hasStoredOutstanding
            ) {

                runningOutstanding =
                    Number(
                        transaction.outstandingBalance ||
                        0
                    );

                return;

            }

            const earned =
                Number(
                    transaction.labourEarned || 0
                );

            const deduction =
                Number(
                    transaction.advanceAdjustment || 0
                );

            const paid =
                Number(
                    transaction.amount || 0
                );

            runningOutstanding =
                Math.max(
                    0,
                    runningOutstanding +
                    earned -
                    deduction -
                    paid
                );

        }
    );

    return runningOutstanding;

}


/* =========================================================
   PAYMENT PERCENTAGE
========================================================= */

function calculatePaymentPercentage(
    totalPayments,
    contractTotal
) {

    if (
        contractTotal <= 0
    ) {

        return 0;

    }

    return (
        Number(totalPayments) /
        Number(contractTotal)
    ) * 100;

}

function calculateTradeFinancialSummary(
    contractItems,
    transactions
) {
    const validTransactions = (Array.isArray(transactions) ? transactions : []).filter(transaction =>
        transaction && [
            "mobilization_advance",
            "labour_advance",
            "labour_settlement",
            COMPLETED_WORK_PAYMENT_TYPE
        ].includes(transaction.type)
    );
    const contractSummary = calculateContractPaymentSummary(contractItems, validTransactions);
    const {
        contractTotal,
        contractPayments,
        balanceContractValue,
        paidPercentage,
        remainingPercentage
    } = contractSummary;
    const labourSettlements = validTransactions
        .filter(transaction => transaction.type === "labour_settlement")
        .reduce((total, transaction) => total + Number(transaction.amount || 0), 0);
    const labourEarned = validTransactions
        .filter(transaction => transaction.type === "labour_settlement")
        .reduce((total, transaction) => total + Number(transaction.labourEarned || 0), 0);
    const labourAdvances = validTransactions
        .filter(transaction => transaction.type === "labour_advance")
        .reduce((total, transaction) => total + Number(transaction.amount || 0), 0);
    const advanceDeductions = validTransactions
        .filter(transaction => transaction.type === "labour_settlement")
        .reduce((total, transaction) => total + Number(transaction.advanceAdjustment || 0), 0);
    return {
        contractTotal,
        contractPayments,
        balanceContractValue,
        paidPercentage,
        remainingPercentage,
        labourEarned,
        labourSettlements,
        labourAdvances,
        advanceDeductions,
        advanceBalance: calculateAdvanceBalance(validTransactions),
        outstandingLabourBalance: calculateOutstandingLabourBalance(validTransactions),
        validTransactions
    };
}


/* =========================================================
   CONTRACT TABLE
========================================================= */

function renderContractTable(
    trade
) {

    const items =
        Array.isArray(
            trade.contractItems
        )
            ? trade.contractItems
            : [];

    const total =
        calculateContractTotal(
            items
        );

    if (
        items.length === 0
    ) {

        return `

            <div
                style="
                    color:#94a3b8;
                    padding:10px 0;
                    font-size:13px;
                "
            >
                No Work Contracts / Capping Items added yet.
            </div>

        `;

    }

    return `

        <div
            style="
                overflow-x:auto;
                margin-top:10px;
            "
        >

            <table
                style="
                    width:100%;
                    min-width:650px;
                    border-collapse:collapse;
                    font-size:13px;
                "
            >

                <thead>

                    <tr>

                        <th
                            style="
                                text-align:left;
                                padding:9px;
                                background:#f1f5f9;
                            "
                        >
                            Work Contract / Item
                        </th>


                        <th
                            style="
                                text-align:right;
                                padding:9px;
                                background:#f1f5f9;
                            "
                        >
                            Contract Amount
                        </th>


                        <th
                            style="
                                text-align:center;
                                padding:9px;
                                background:#f1f5f9;
                                width:150px;
                            "
                        >
                            Actions
                        </th>

                    </tr>

                </thead>


                <tbody>

                    ${
                        items.map(
                            item => `

                                <tr>

                                    <td
                                        style="
                                            padding:9px;
                                            border-bottom:1px solid #e2e8f0;
                                        "
                                    >

                                        ${escapeHtml(
                                            item.name
                                        )}

                                    </td>


                                    <td
                                        style="
                                            padding:9px;
                                            text-align:right;
                                            border-bottom:1px solid #e2e8f0;
                                            font-weight:600;
                                        "
                                    >

                                        ${money(
                                            item.amount
                                        )}

                                    </td>


                                    <td
                                        style="
                                            padding:9px;
                                            text-align:center;
                                            border-bottom:1px solid #e2e8f0;
                                        "
                                    >

                                        <button
                                            type="button"
                                            class="btn-secondary btn-small contract-edit-btn"
                                            data-trade-id="${escapeHtml(
                                                trade.id
                                            )}"
                                            data-contract-id="${escapeHtml(
                                                item.id
                                            )}"
                                        >
                                            Edit
                                        </button>


                                        <button
                                            type="button"
                                            class="btn-danger btn-small contract-delete-btn"
                                            data-trade-id="${escapeHtml(
                                                trade.id
                                            )}"
                                            data-contract-id="${escapeHtml(
                                                item.id
                                            )}"
                                        >
                                            Delete
                                        </button>

                                    </td>

                                </tr>

                            `
                        ).join("")
                    }

                </tbody>


                <tfoot>

                    <tr>

                        <td
                            style="
                                padding:10px;
                                font-weight:700;
                                text-align:right;
                            "
                        >
                            TOTAL CONTRACT VALUE
                        </td>


                        <td
                            style="
                                padding:10px;
                                text-align:right;
                                font-weight:700;
                            "
                        >
                            ${money(total)}
                        </td>


                        <td></td>

                    </tr>

                </tfoot>

            </table>

        </div>

    `;

}


/* =========================================================
   ADD CONTRACT ITEM
========================================================= */

async function addContractItem(
    tradeId
) {

    const name =
        await showPrompt(
            "Enter Work Contract / Item name:"
        );

    if (
        name === null
    ) {

        return;

    }

    const cleanName =
        name.trim();

    if (!cleanName) {

        showStatus(
            "Enter a contract item name.",
            true
        );

        return;

    }

    const amountText =
        await showPrompt(
            "Enter Contract Amount (₹):"
        );

    if (
        amountText === null
    ) {

        return;

    }

    const amount =
        Number(
            amountText
        );

    if (
        !Number.isFinite(amount) ||
        amount < 0
    ) {

        showStatus(
            "Enter a valid contract amount.",
            true
        );

        return;

    }

    try {
        await sendContractValueMutation(
            `/api/trades/${encodeURIComponent(tradeId)}/contract-items`,
            "POST",
            { name: cleanName, amount }
        );

        showStatus(
            "Contract item added."
        );

        await loadTrades();

    }

    catch (error) {

        console.error(
            error
        );

        showStatus(
            "Unable to add the contract item. Please try again.",
            true
        );

    }

}


/* =========================================================
   EDIT CONTRACT ITEM
========================================================= */

async function editContractItem(
    tradeId,
    contractId
) {

    try {

        const tradeRef =
            doc(
                db,
                TRADES_COLLECTION,
                tradeId
            );

        const snapshot =
            await getDoc(
                tradeRef
            );

        if (
            !snapshot.exists()
        ) {

            throw new Error(
                "Trade no longer exists."
            );

        }

        const trade =
            snapshot.data();

        const items =
            Array.isArray(
                trade.contractItems
            )
                ? [
                    ...trade.contractItems
                ]
                : [];

        const itemIndex =
            items.findIndex(
                item =>
                    item.id ===
                    contractId
            );

        if (
            itemIndex === -1
        ) {

            throw new Error(
                "Contract item not found."
            );

        }

        const item =
            items[
                itemIndex
            ];

        const newName =
            await showPrompt(
                "Edit Work Contract / Item:",
                item.name || ""
            );

        if (
            newName === null
        ) {

            return;

        }

        const cleanName =
            newName.trim();

        if (!cleanName) {

            showStatus(
                "Enter a contract item name.",
                true
            );

            return;

        }

        const newAmountText =
            await showPrompt(
                "Edit Contract Amount (₹):",
                String(
                    item.amount || 0
                )
            );

        if (
            newAmountText === null
        ) {

            return;

        }

        const newAmount =
            Number(
                newAmountText
            );

        if (
            !Number.isFinite(
                newAmount
            ) ||
            newAmount < 0
        ) {

            showStatus(
                "Enter a valid contract amount.",
                true
            );

            return;

        }

        await sendContractValueMutation(
            `/api/trades/${encodeURIComponent(tradeId)}/contract-items/${encodeURIComponent(contractId)}`,
            "PATCH",
            { name: cleanName, amount: newAmount }
        );

        showStatus(
            "Contract item updated."
        );

        await loadTrades();

    }

    catch (error) {

        console.error(
            error
        );

        showStatus(
            "Unable to update the contract item. Please try again.",
            true
        );

    }

}


/* =========================================================
   DELETE CONTRACT ITEM
========================================================= */

async function deleteContractItem(
    tradeId,
    contractId
) {

    if (
        !(await showConfirmDialog({
            title: "Mannat Moon",
            message: "Delete this Work Contract / Capping item?",
            confirmText: "Delete",
            cancelText: "Cancel",
            type: "danger"
        }))
    ) {

        return;

    }

    try {

        const result = await sendContractValueMutation(
            `/api/trades/${encodeURIComponent(tradeId)}/contract-items/${encodeURIComponent(contractId)}`,
            "DELETE"
        );
        if (!result.contractItems) {
            throw new Error("The server returned an invalid contract update.");
        }

        showStatus(
            "Contract item deleted."
        );

        await loadTrades();

    }

    catch (error) {

        console.error(
            error
        );

        showStatus(
            "Unable to delete the contract item. Please try again.",
            true
        );

    }

}


/* =========================================================
   RENDER TRADE
========================================================= */

function renderTrade(
    trade
) {

    const list =
        document.getElementById(
            "tradeList"
        );

    const card =
        document.createElement(
            "div"
        );

    card.className =
        "trade-card";


    const workers = getTradeWorkerCategories(trade);


    const transactions =
        Array.isArray(
            trade.paymentTransactions
        )
            ? trade.paymentTransactions
            : [];


    const contractItems =
        Array.isArray(
            trade.contractItems
        )
            ? trade.contractItems
            : [];


    const financialSummary = financialOnly
        ? calculateTradeFinancialSummary(contractItems, transactions)
        : null;
    const contractTotal = financialOnly ? financialSummary.contractTotal : calculateContractTotal(contractItems);
    const totalPayments = financialOnly ? financialSummary.contractPayments : calculateTotalPayments(transactions);
    const paymentPercentage = financialOnly
        ? financialSummary.paidPercentage
        : calculatePaymentPercentage(totalPayments, contractTotal);


    /*
        Progress bar should never visually
        exceed 100%.
    */

    const paymentPercentageDisplay =
        Math.min(
            100,
            Math.max(
                0,
                paymentPercentage
            )
        );


    /*
        Remaining percentage.

        If payments exceed contract value,
        remaining stays at 0%.
    */

    const remainingPercentage = financialOnly
        ? financialSummary.remainingPercentage
        : Math.max(0, 100 - paymentPercentage);


    /*
        Remaining amount.

        If payments exceed contract value,
        remaining stays at ₹0.
    */

    const remainingContractAmount = financialOnly
        ? financialSummary.balanceContractValue
        : Math.max(0, contractTotal - totalPayments);


    const advanceBalance = financialOnly
        ? financialSummary.advanceBalance
        : calculateAdvanceBalance(transactions);
    const settlementTotal = financialOnly
        ? financialSummary.labourSettlements
        : transactions.filter(transaction => transaction.type === "labour_settlement").reduce((total, transaction) => total + Number(transaction.amount || 0), 0);
    const outstandingLabourBalance = financialOnly
        ? financialSummary.outstandingLabourBalance
        : calculateOutstandingLabourBalance(transactions);
    const labourEarned = financialOnly ? financialSummary.labourEarned : 0;
    const labourAdvances = financialOnly ? financialSummary.labourAdvances : 0;
    const advanceDeductions = financialOnly ? financialSummary.advanceDeductions : 0;


    card.innerHTML = `

        <!-- =============================================
             TRADE HEADER
        ============================================== -->

        <div class="trade-title-row">

            <div class="trade-title">

                ${escapeHtml(
                    trade.name
                )}

            </div>


            <div class="trade-buttons">

                <button
                    class="btn-secondary btn-small edit-btn"
                >
                    Edit
                </button>


                <button
                    class="btn-danger btn-small delete-btn"
                >
                    Delete
                </button>


                <button
                    type="button"
                    class="btn-secondary btn-small pdf-download-btn"
                    data-trade-id="${escapeHtml(
                        trade.id
                    )}"
                >
                    Download PDF
                </button>

                <button
                    type="button"
                    class="btn-secondary btn-small view-ledger-btn"
                >
                    View Ledger
                </button>

            </div>

        </div>


        <!-- =============================================
             WORKER CATEGORIES
        ============================================== -->

        <div class="worker-list">

            <strong>
                Worker Categories:
            </strong>


            ${
                workers.length

                ?

                workers.map(
                    worker => `

                        <div
                            class="worker-list-row"
                        >

                            <span>

                                ${escapeHtml(
                                    worker.name
                                )}

                            </span>


                            <span>
                                ${worker.payment_type === "lump_sum"
                                    ? `Lump Sum: ${money(worker.lump_sum_amount)}${worker.work_description ? `<span style="display:block;color:#64748b;">${escapeHtml(worker.work_description)}</span>` : ""}`
                                    : `${money(worker.currentWage)} / day`}
                                <span style="color:#64748b;margin-left:6px;">from ${formatDateWithDay(worker.effective_from || worker.effectiveFrom)}</span>
                            </span>

                        </div>

                    `
                ).join("")

                :

                `

                    <div
                        style="
                            color:#94a3b8;
                            margin-top:10px;
                        "
                    >

                        No worker categories.

                    </div>

                `

            }

        </div>


        <!-- =============================================
             COMMON FIELDS
        ============================================== -->

        <div class="common-fields">

            <strong>
                Common Fields:
            </strong>


            <ul>

                ${
                    COMMON_FIELDS
                        .map(
                            field =>
                                `<li>${field}</li>`
                        )
                        .join("")
                }

            </ul>

        </div>


        <!-- =============================================
             WORK CONTRACTS / CAPPING
        ============================================== -->

        <div
            class="contract-section"
            style="
                margin-top:20px;
                padding:18px;
                border:1px solid #e2e8f0;
                border-radius:12px;
                background:#ffffff;
            "
        >

            <div
                style="
                    display:flex;
                    justify-content:space-between;
                    align-items:center;
                    gap:10px;
                    flex-wrap:wrap;
                "
            >

                <div>

                    <h3
                        style="
                            margin:0;
                        "
                    >
                        Work Contracts / Capping
                    </h3>


                    <div
                        style="
                            margin-top:4px;
                            color:#64748b;
                            font-size:12px;
                        "
                    >
                        Declare the contract amount for each work item.
                    </div>

                </div>


                <button
                    type="button"
                    class="btn-primary btn-small add-contract-btn"
                >
                    + Add Contract Item
                </button>

            </div>


            <div class="contract-table-container">

                ${renderContractTable(
                    trade
                )}

            </div>


            <!-- CONTRACT SUMMARY -->

            <div
                style="
                    display:grid;
                    grid-template-columns:
                        repeat(3,minmax(180px,1fr));
                    gap:12px;
                    margin-top:18px;
                "
            >

                <div
                    class="summary-box"
                >

                    <div
                        class="summary-label"
                    >
                        TOTAL CONTRACT VALUE
                    </div>


                    <div
                        class="summary-value"
                    >

                        ${money(
                            contractTotal
                        )}

                    </div>

                </div>


                <div
                    class="summary-box"
                >

                    <div
                        class="summary-label"
                    >
                        TOTAL PAYMENTS AGAINST CONTRACT
                    </div>


                    <div
                        class="summary-value"
                    >

                        ${money(
                            totalPayments
                        )}

                    </div>

                </div>


                <div
                    class="summary-box"
                >

                    <div
                        class="summary-label"
                    >
                        BALANCE CONTRACT VALUE
                    </div>


                    <div
                        class="summary-value"
                    >

                        ${money(
                            remainingContractAmount
                        )}

                    </div>

                </div>


            </div>


            <!-- =========================================
                 PAYMENT AGAINST CONTRACT
            ========================================== -->

            <div
                style="
                    margin-top:20px;
                "
            >

                <div
                    style="
                        display:flex;
                        justify-content:space-between;
                        align-items:center;
                        margin-bottom:8px;
                    "
                >

                    <strong>
                        PAYMENT AGAINST CONTRACT
                    </strong>


                    <strong>
                        ${paymentPercentage.toFixed(2)}% PAID
                    </strong>

                </div>


                <!-- PROGRESS BAR -->

                <div
                    style="
                        height:16px;
                        background:#e2e8f0;
                        border-radius:20px;
                        overflow:hidden;
                    "
                >

                    <div
                        style="
                            width:${paymentPercentageDisplay}%;
                            height:100%;
                            background:#2563eb;
                            border-radius:20px;
                            transition:width .3s ease;
                        "
                    ></div>

                </div>


                <!-- PAID / REMAINING PERCENTAGE -->

                <div
                    style="
                        display:flex;
                        justify-content:space-between;
                        align-items:center;
                        margin-top:10px;
                        font-size:13px;
                    "
                >

                    <div>

                        <strong>
                            ${paymentPercentage.toFixed(2)}%
                        </strong>


                        <span
                            style="
                                color:#64748b;
                                margin-left:5px;
                            "
                        >
                            Paid
                        </span>

                    </div>


                    <div>

                        <strong>
                            ${remainingPercentage.toFixed(2)}%
                        </strong>


                        <span
                            style="
                                color:#64748b;
                                margin-left:5px;
                            "
                        >
                            Remaining
                        </span>

                    </div>

                </div>


                <!-- PAID / REMAINING RUPEES -->

                <div
                    style="
                        display:flex;
                        justify-content:space-between;
                        align-items:center;
                        margin-top:7px;
                        font-size:12px;
                        color:#64748b;
                    "
                >

                    <span>
                        Paid:
                        ${money(
                            totalPayments
                        )}
                    </span>


                    <span>
                        Remaining:
                        ${money(
                            remainingContractAmount
                        )}
                    </span>

                </div>


                <!-- EXPLANATION -->

                <div
                    style="
                        margin-top:7px;
                        font-size:12px;
                        color:#64748b;
                    "
                >

                    ${paymentPercentage.toFixed(2)}%
                    of the declared contract value has been paid,
                    and
                    ${remainingPercentage.toFixed(2)}%
                    remains.

                </div>

            </div>

        </div>


        <!-- =============================================
             LABOUR PAYMENTS
        ============================================== -->

        <div class="payment-section">

            <h3>
                Labour Payments
            </h3>


            <div class="payment-summary">


                <div class="summary-box">

                    <div class="summary-label">
                        TOTAL LABOUR EARNED
                    </div>


                    <div class="summary-value">

                        ${money(
                            labourEarned
                        )}

                    </div>

                </div>


                <div class="summary-box">

                    <div class="summary-label">
                        LABOUR SETTLEMENTS PAID
                    </div>


                    <div class="summary-value">

                        ${money(
                            settlementTotal
                        )}

                    </div>

                </div>


                <div class="summary-box">

                    <div class="summary-label">
                        LABOUR ADVANCES RECEIVED
                    </div>


                    <div class="summary-value">

                        ${money(
                            labourAdvances
                        )}

                    </div>

                </div>


                <div class="summary-box">

                    <div class="summary-label">
                        ADVANCE DEDUCTIONS
                    </div>


                    <div class="summary-value">

                        ${money(
                            advanceDeductions
                        )}

                    </div>

                </div>


                <div class="summary-box">

                    <div class="summary-label">
                        REMAINING ADVANCE BALANCE
                    </div>


                    <div class="summary-value">

                        ${money(
                            advanceBalance
                        )}

                    </div>

                </div>


                <div class="summary-box">

                    <div class="summary-label">
                        OUTSTANDING BALANCE
                    </div>


                    <div class="summary-value">

                        ${money(
                            outstandingLabourBalance
                        )}

                    </div>

                </div>


            </div>


            <button
                class="btn-primary payment-btn"
            >
                Open Payment Ledger
            </button>

        </div>

    `;


    /* =====================================================
       TRADE EDIT
    ===================================================== */

    card.querySelector(
        ".edit-btn"
    ).addEventListener(
        "click",
        () =>
            editTrade(
                trade
            )
    );


    /* =====================================================
       TRADE DELETE
    ===================================================== */

    card.querySelector(
        ".delete-btn"
    ).addEventListener(
        "click",
        () =>
            removeTrade(
                trade.id
            )
    );


    /* =====================================================
       PDF DOWNLOAD
    ===================================================== */

    card.querySelector(
        ".pdf-download-btn"
    ).addEventListener(
        "click",
        () =>
            downloadTradePDF(
                trade
            )
    );


    card.querySelector(
        ".view-ledger-btn"
    ).addEventListener(
        "click",
        () =>
            openTradeLedgerModal(
                trade
            )
    );


    /* =====================================================
       PAYMENT LEDGER
    ===================================================== */

    card.querySelector(
        ".payment-btn"
    ).addEventListener(
        "click",
        () =>
            openPaymentModal(
                trade
            )
    );


    /* =====================================================
       ADD CONTRACT
    ===================================================== */

    card.querySelector(
        ".add-contract-btn"
    ).addEventListener(
        "click",
        () =>
            addContractItem(
                trade.id
            )
    );


    /* =====================================================
       EDIT CONTRACT
    ===================================================== */

    card
        .querySelectorAll(
            ".contract-edit-btn"
        )
        .forEach(
            button => {

                button.addEventListener(
                    "click",
                    () => {

                        editContractItem(
                            button.dataset.tradeId,
                            button.dataset.contractId
                        );

                    }
                );

            }
        );


    /* =====================================================
       DELETE CONTRACT
    ===================================================== */

    card
        .querySelectorAll(
            ".contract-delete-btn"
        )
        .forEach(
            button => {

                button.addEventListener(
                    "click",
                    () => {

                        deleteContractItem(
                            button.dataset.tradeId,
                            button.dataset.contractId
                        );

                    }
                );

            }
        );


    list.appendChild(
        card
    );

}


/* =========================================================
   SAVE TRADE
========================================================= */

async function saveTrade() {

    const name =
        document.getElementById(
            "tradeName"
        ).value.trim().toUpperCase();


    if (!name) {

        showStatus(
            "Enter a Trade Name.",
            true
        );

        return;

    }


    const rawWorkers =
        getWorkerCategories();


    if (
        rawWorkers.length === 0
    ) {

        showStatus(
            "Add at least one Worker Category.",
            true
        );

        return;

    }


    const duplicateNames =
        rawWorkers
            .map(
                worker =>
                    worker.name.toLowerCase()
            )
            .filter(
                (
                    value,
                    index,
                    array
                ) =>
                    array.indexOf(
                        value
                    ) !== index
            );


    if (
        duplicateNames.length
    ) {

        showStatus(
            "Worker Category names must be unique.",
            true
        );

        return;

    }

    const invalidLumpSum = rawWorkers.find(worker =>
        worker.payment_type === "lump_sum" &&
        (!(worker.lump_sum_amount > 0) || !worker.effective_from)
    );
    if (invalidLumpSum) {
        showStatus("Lump-sum categories need an agreed amount greater than zero and an effective date.", true);
        return;
    }


    try {

        let tradeId =
            editingTradeId;


        let oldTrade =
            null;


        if (tradeId) {

            const existing =
                await getDoc(
                    doc(
                        db,
                        TRADES_COLLECTION,
                        tradeId
                    )
                );


            if (
                existing.exists()
            ) {

                oldTrade =
                    existing.data();

            }

        }


        const oldWorkers = getTradeWorkerCategories(oldTrade);


        const finalWorkers =
            rawWorkers.map(
                worker => {

                    const oldWorker =
                        oldWorkers.find(
                            old => (worker.id && old.id === worker.id) ||
                                String(old.name).toLowerCase() === String(worker.name).toLowerCase()

                        );


                    return buildWorkerWithHistory(
                        worker,
                        oldWorker
                    );

                }
            );


        const data = {

            name,

            workerCategories:
                finalWorkers,

            commonFields:
                COMMON_FIELDS,

            ...(editingTradeId && Object.prototype.hasOwnProperty.call(
                oldTrade || {},
                "completedWorkValue"
            )
                ? {
                    completedWorkValue:
                        oldTrade.completedWorkValue
                }
                : {}),

        };

        if (editingTradeId) {
            await sendTradeMutation(
                `/api/trades/${encodeURIComponent(editingTradeId)}`,
                "PATCH",
                data
            );
        } else {
            const fingerprint = JSON.stringify({
                name,
                workers: rawWorkers,
                commonFields: COMMON_FIELDS
            });
            if (pendingTradeCreateRequest?.fingerprint !== fingerprint) {
                pendingTradeCreateRequest = {
                    fingerprint,
                    payload: {
                        id: generateUuid(),
                        ...data
                    }
                };
            }
            await sendTradeMutation(
                "/api/trades",
                "POST",
                pendingTradeCreateRequest.payload
            );
        }

        pendingTradeCreateRequest = null;

        showStatus(

            editingTradeId

                ?

                "Trade updated successfully."

                :

                "Trade created successfully."

        );


        resetForm();


        await loadTrades();

    }

    catch (error) {

        console.error(
            error
        );


        showStatus(
            "Unable to save the trade. Please try again.",
            true
        );

    }

}


/* =========================================================
   EDIT TRADE
========================================================= */

function editTrade(
    trade
) {

    editingTradeId =
        trade.id;


    document.getElementById(
        "formTitle"
    ).textContent =
        "Edit Trade";


    document.getElementById(
        "tradeName"
    ).value =
        String(
            trade.name || ""
        ).toUpperCase();


    const container =
        document.getElementById(
            "workerContainer"
        );


    container.innerHTML =
        "";


    const workers = getTradeWorkerCategories(trade);


    workers.forEach(
        worker =>
            addWorkerCategory(
                worker
            )
    );


    window.scrollTo({

        top: 0,

        behavior: "smooth"

    });

}


/* =========================================================
   DELETE TRADE
========================================================= */

async function removeTrade(
    tradeId
) {

    if (
        !(await showConfirmDialog({
            title: "Mannat Moon",
            message: `Are you sure you want to delete "${trades.find(trade => trade.id === tradeId)?.name || "this trade"}"?`,
            details: "This will also remove its payment ledger and contract table.",
            confirmText: "Delete",
            cancelText: "Cancel",
            type: "danger"
        }))
    ) {

        return;

    }


    try {

        await sendTradeMutation(
            `/api/trades/${encodeURIComponent(tradeId)}`,
            "DELETE"
        );


        showStatus(
            "Trade deleted."
        );


        await loadTrades();

    }

    catch (error) {

        console.error(
            error
        );


        showStatus(
            "Unable to delete the trade. Please try again.",
            true
        );

    }

}


function openTradeLedgerModal(trade) {
    currentViewLedgerTrade = trade;

    const modal = document.getElementById("tradeLedgerModal");
    const title = document.getElementById("tradeLedgerModalTitle");
    const content = document.getElementById("tradeLedgerModalContent");
    document.getElementById("tradeLedgerDownloadPdf").hidden = false;
    const workers = getTradeWorkerCategories(trade);
    const contractItems = Array.isArray(trade.contractItems) ? trade.contractItems : [];
    const transactions = sortPaymentTransactions(trade.paymentTransactions);
    const summary = calculateTradeFinancialSummary(contractItems, transactions);
    const transactionLabel = type => ({
        mobilization_advance: "Mobilization Advance",
        labour_advance: "Labour Advance",
        labour_settlement: "Labour Settlement",
        lump_sum_payment: "Lump-Sum Payment",
        [COMPLETED_WORK_PAYMENT_TYPE]: "Payment Against Completed Work"
    }[type] || type || "Unclassified Transaction");

    title.textContent = `${trade.name || "Trade"} Ledger`;
    content.innerHTML = `
        <div class="ledger-modal-meta">
            <div><strong>Trade</strong><span>${escapeHtml(trade.name || "Unnamed trade")}</span></div>
            <div><strong>Contract Value</strong><span>${money(summary.contractTotal)}</span></div>
            <div><strong>Payments Against Contract</strong><span>${money(summary.contractPayments)}</span></div>
            <div><strong>Advance Balance</strong><span>${money(summary.advanceBalance)}</span></div>
        </div>

        <h3>Worker Categories</h3>
        ${workers.length ? `<table class="ledger-modal-table"><thead><tr><th>Category</th><th>Payment Type</th><th>Agreed / Daily Amount</th><th>Total Paid</th><th>Remaining</th><th>Effective From</th><th>Work Description</th></tr></thead><tbody>${workers.map(worker => { const totals = worker.payment_type === "lump_sum" ? getLumpSumPaymentTotals(worker, transactions) : null; return `<tr><td>${escapeHtml(worker.name)}</td><td>${worker.payment_type === "lump_sum" ? "Lump Sum" : "Daily Wage"}</td><td>${money(worker.payment_type === "lump_sum" ? worker.lump_sum_amount : worker.currentWage)}${worker.payment_type === "daily_wage" ? " / day" : ""}</td><td>${totals ? money(totals.totalPaid) : "-"}</td><td>${totals ? money(totals.remainingBalance) : "-"}</td><td>${escapeHtml(formatDateWithDay(worker.effective_from || worker.effectiveFrom || "-"))}</td><td>${escapeHtml(worker.work_description || "")}</td></tr>`; }).join("")}</tbody></table>` : '<div class="ledger-modal-empty">No worker categories recorded.</div>'}

        <h3>Work Contracts / Capping</h3>
        ${contractItems.length ? `<table class="ledger-modal-table"><thead><tr><th>Contract Item</th><th>Amount</th></tr></thead><tbody>${contractItems.map(item => `<tr><td>${escapeHtml(item.name)}</td><td>${money(item.amount)}</td></tr>`).join("")}</tbody></table>` : '<div class="ledger-modal-empty">No contract items recorded.</div>'}
        <div class="ledger-modal-financials">
            <span>Total Contract Value: <strong>${money(summary.contractTotal)}</strong></span>
            <span>Balance Contract Value: <strong>${money(summary.balanceContractValue)}</strong></span>
            <span>Paid: <strong>${summary.paidPercentage.toFixed(2)}%</strong></span>
            <span>Remaining: <strong>${summary.remainingPercentage.toFixed(2)}%</strong></span>
            <span>Total Labour Earned: <strong>${money(summary.labourEarned)}</strong></span>
            <span>Labour Settlements Paid: <strong>${money(summary.labourSettlements)}</strong></span>
            <span>Labour Advances Received: <strong>${money(summary.labourAdvances)}</strong></span>
            <span>Advance Deductions: <strong>${money(summary.advanceDeductions)}</strong></span>
            <span>Outstanding Labour Balance: <strong>${money(summary.outstandingLabourBalance)}</strong></span>
        </div>

        <h3>Payment History</h3>
        ${transactions.length ? `<div class="ledger-modal-table-wrap"><table class="ledger-modal-table"><thead><tr><th>Date</th><th>Transaction Type</th><th>Amount</th><th>Payment Mode</th><th>Remarks</th></tr></thead><tbody>${transactions.map(transaction => `<tr><td>${formatDateWithDay(transaction.date)}</td><td>${escapeHtml(transactionLabel(transaction.type))}</td><td>${money(transaction.amount)}</td><td>${escapeHtml(transaction.paymentMode || "")}</td><td>${escapeHtml(transaction.remarks || "")}</td></tr>`).join("")}</tbody></table></div>` : '<div class="ledger-modal-empty">No payment transactions yet.</div>'}
    `;

    modal.classList.add("show");
    document.body.classList.add("ledger-modal-open");
    registerEscClose(modal, closeTradeLedgerModal);
}

function closeTradeLedgerModal() {
    const modal = document.getElementById("tradeLedgerModal");
    unregisterEscClose(modal);
    modal.classList.remove("show");
    modal.style.removeProperty("z-index");
    document.body.classList.remove("ledger-modal-open");
    document.getElementById("tradeLedgerDownloadPdf").hidden = false;
    currentViewLedgerTrade = null;
}

function openPaymentTransactionDetails(paymentId) {
    const transaction = currentPaymentTrade?.paymentTransactions?.find(item => item.id === paymentId);
    if (!transaction) {
        showStatus("Payment transaction not found.", true);
        return;
    }

    const transactionTypes = {
        mobilization_advance: "Mobilization Advance",
        labour_advance: "Labour Advance",
        labour_settlement: "Labour Settlement",
        lump_sum_payment: "Lump-Sum Payment",
        [COMPLETED_WORK_PAYMENT_TYPE]: "Payment Against Completed Work"
    };
    const modal = document.getElementById("tradeLedgerModal");
    const title = document.getElementById("tradeLedgerModalTitle");
    const content = document.getElementById("tradeLedgerModalContent");
    const downloadButton = document.getElementById("tradeLedgerDownloadPdf");
    const transactionId = String(transaction.id || "");

    currentViewLedgerTrade = null;
    title.textContent = "Payment Transaction Details";
    downloadButton.hidden = true;
    modal.style.zIndex = "20";
    content.innerHTML = `
        <div class="payment-transaction-details">
            <div><strong>Transaction Type</strong><span>${escapeHtml(transactionTypes[transaction.type] || transaction.type || "Unclassified Transaction")}</span></div>
            <div><strong>Payment Date</strong><span>${escapeHtml(formatDateWithDay(transaction.date))}</span></div>
            <div><strong>Trade Name</strong><span>${escapeHtml(currentPaymentTrade.name || "Unnamed trade")}</span></div>
            ${transaction.type === "lump_sum_payment" ? `<div><strong>Worker Category</strong><span>${escapeHtml(transaction.workerCategoryName || "Unspecified")}</span></div>` : ""}
            <div><strong>Payment Mode</strong><span>${escapeHtml(transaction.paymentMode || "Not specified")}</span></div>
            <div><strong>Amount</strong><span>${money(transaction.amount)}</span></div>
            <div><strong>Remarks</strong><span>${escapeHtml(transaction.remarks || "None")}</span></div>
            <div class="payment-transaction-id">
                <strong>Transaction ID</strong>
                <div class="payment-transaction-id-value">
                    <code>${escapeHtml(transactionId || "-")}</code>
                    <button type="button" class="btn-secondary btn-small payment-copy-id-btn" ${transactionId ? "" : "disabled"}>Copy ID</button>
                </div>
            </div>
        </div>
    `;

    modal.classList.add("show");
    document.body.classList.add("ledger-modal-open");
    registerEscClose(modal, closeTradeLedgerModal);
    content.querySelector(".payment-copy-id-btn")?.addEventListener("click", () => copyPaymentTransactionId(transactionId));
}

async function copyPaymentTransactionId(transactionId) {
    try {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(transactionId);
        } else {
            const copyField = document.createElement("textarea");
            copyField.value = transactionId;
            copyField.setAttribute("readonly", "");
            copyField.style.position = "fixed";
            copyField.style.left = "-10000px";
            document.body.appendChild(copyField);
            copyField.select();
            const copied = document.execCommand("copy");
            copyField.remove();
            if (!copied) {
                throw new Error("Clipboard copy was not available.");
            }
        }
        await showSuccess("Transaction ID copied to clipboard.");
    } catch (error) {
        console.error(error);
        await showError("Unable to copy the transaction ID.");
    }
}

/* =========================================================
   OPEN PAYMENT MODAL
========================================================= */

function openPaymentModal(
    trade
) {

    currentPaymentTrade =
        trade;


    editingPaymentId =
        null;


    document.getElementById(
        "paymentTradeId"
    ).value =
        trade.id;


    document.getElementById(
        "paymentTradeName"
    ).textContent =
        trade.name;


    document.getElementById(
        "paymentDate"
    ).value =
        todayString();


    document.getElementById(
        "paymentAmount"
    ).value =
        "";


    document.getElementById(
        "labourEarned"
    ).value =
        "";


    document.getElementById(
        "adjustmentAmount"
    ).value =
        "";


    document.getElementById(
        "paymentRemarks"
    ).value =
        "";


    document.getElementById(
        "paymentType"
    ).value =
        "mobilization_advance";


    document.getElementById(
        "advanceAdjustmentMode"
    ).value =
        "none";


    document.getElementById(
        "paymentMode"
    ).value =
        "Cash";


    document.getElementById(
        "savePaymentBtn"
    ).textContent =
        "Save Payment";


    updatePaymentForm();


    renderPaymentHistory(
        trade
    );


    const paymentModal =
        document.getElementById(
            "paymentModal"
        );


    paymentModal.classList.add(
        "show"
    );

    registerEscClose(
        paymentModal,
        () => closePaymentModal()
    );


    setPaymentModalWidth();

}


/* =========================================================
   PAYMENT MODAL WIDTH
========================================================= */

function setPaymentModalWidth() {

    const modal =
        document.getElementById(
            "paymentModal"
        );


    if (!modal) {
        return;
    }


    const panel =
        modal.firstElementChild;


    if (panel) {

        panel.style.width =
            "1180px";

        panel.style.maxWidth =
            "calc(100vw - 40px)";

        panel.style.minWidth =
            "0";

    }


    modal.style.overflowX =
        "auto";


    if (
        !document.getElementById(
            "paymentLedgerDynamicStyles"
        )
    ) {

        const style =
            document.createElement(
                "style"
            );


        style.id =
            "paymentLedgerDynamicStyles";


        style.textContent = `

            #paymentModal {
                overflow-x:auto !important;
            }

            #paymentHistory {
                width:100%;
                overflow-x:auto;
            }

            #paymentHistory .transaction-row {
                min-width:950px;
            }

            .payment-transaction-details {
                display:grid;
                grid-template-columns:repeat(2, minmax(0, 1fr));
                gap:12px;
            }

            .payment-transaction-details > div {
                display:flex;
                flex-direction:column;
                gap:5px;
                min-width:0;
                padding:12px;
                border:1px solid #e2e7ec;
                border-radius:8px;
                background:#f8fafc;
            }

            .payment-transaction-details > div > strong {
                color:#667085;
                font-size:12px;
            }

            .payment-transaction-details > div > span {
                color:#18212b;
                overflow-wrap:anywhere;
            }

            .payment-transaction-id {
                grid-column:1 / -1;
            }

            .payment-transaction-id-value {
                display:flex;
                align-items:center;
                justify-content:space-between;
                gap:10px;
            }

            .payment-transaction-id-value code {
                min-width:0;
                overflow-wrap:anywhere;
                color:#344054;
                font-size:13px;
            }

            @media(max-width:800px) {

                #paymentModal > div {
                    width:calc(100vw - 20px) !important;
                    max-width:calc(100vw - 20px) !important;
                }

                #paymentHistory .transaction-row {
                    min-width:900px;
                }

                .payment-transaction-details {
                    grid-template-columns:1fr;
                }

                .payment-transaction-id {
                    grid-column:auto;
                }

            }

        `;


        document.head.appendChild(
            style
        );

    }

}


/* =========================================================
   CLOSE PAYMENT MODAL
========================================================= */

window.closePaymentModal =
    function () {

        const paymentModal =
            document.getElementById(
                "paymentModal"
            );


        if (paymentModal) {
            unregisterEscClose(
                paymentModal
            );
            paymentModal.classList.remove(
                "show"
            );
        }


        currentPaymentTrade =
            null;


        editingPaymentId =
            null;

    };


/* =========================================================
   PAYMENT FORM
========================================================= */

function updateSettlementBreakdownUI() {

    const type =
        document.getElementById(
            "paymentType"
        ).value;

    const amountHelp =
        document.getElementById(
            "paymentAmountHelp"
        );

    const settlementBreakdown =
        document.getElementById(
            "settlementBreakdown"
        );

    if (
        type !==
        "labour_settlement"
    ) {

        if (
            settlementBreakdown
        ) {

            settlementBreakdown.style.display =
                "none";

            settlementBreakdown.innerHTML =
                "";

        }

        if (
            amountHelp
        ) {

            amountHelp.textContent =
                type ===
                "mobilization_advance"

                    ?

                    "Mobilization payment is never added to the labour advance balance."

                    :

                    "Labour Advance creates an outstanding advance balance.";

        }

        return;

    }

    if (
        amountHelp
    ) {

        amountHelp.textContent =
            "Amount Available to Pay = Previous Outstanding + Labour Earned − Advance Deduction";

    }

    const earned =
        Number(
            document.getElementById(
                "labourEarned"
            ).value || 0
        );

    const mode =
        document.getElementById(
            "advanceAdjustmentMode"
        ).value;

    let transactions =
        Array.isArray(
            currentPaymentTrade?.paymentTransactions
        )
            ? [
                ...currentPaymentTrade.paymentTransactions
            ]
            : [];

    if (
        editingPaymentId
    ) {

        transactions =
            transactions.filter(
                transaction =>
                    transaction.id !==
                    editingPaymentId
            );

    }

    const paymentAmountInput =
        document.getElementById(
            "paymentAmount"
        );

    const amountPaidNow =
        Number(
            paymentAmountInput.value || 0
        );

    const previousOutstandingBalance =
        calculateOutstandingLabourBalance(
            transactions
        );

    const settlementValues =
        calculateSettlementBreakdown({
            transactions,
            labourEarned: earned,
            mode,
            requestedAdjustment:
                Number(
                    document.getElementById(
                        "adjustmentAmount"
                    ).value || 0
                ),
            amountPaidNow,
            previousOutstandingBalance
        });

    const shouldAutoFillAmount =
        (mode === "full" || mode === "partial") &&
        !settlementAmountUserEdited;

    if (
        shouldAutoFillAmount
    ) {

        paymentAmountInput.value =
            String(
                Math.max(
                    0,
                    settlementValues.amountAvailableToPay
                )
            );

    }

    if (
        settlementBreakdown
    ) {

        settlementBreakdown.style.display =
            "block";

        settlementBreakdown.innerHTML =
            `
                <div>
                    <strong>
                        Amount Available to Pay:
                    </strong>
                    ${money(
                        settlementValues.amountAvailableToPay
                    )}
                </div>

                <div>
                    <strong>
                        Advance Deduction:
                    </strong>
                    ${money(
                        settlementValues.adjustment
                    )}
                </div>

                <div>
                    <strong>
                        Remaining Advance Balance:
                    </strong>
                    ${money(
                        settlementValues.remainingAdvanceBalance
                    )}
                </div>

                <div>
                    <strong>
                        Amount Paid Now:
                    </strong>
                    ${money(
                        Number(
                            paymentAmountInput.value || 0
                        )
                    )}
                </div>

                <div>
                    <strong>
                        Outstanding Balance:
                    </strong>
                    ${money(
                        settlementValues.outstandingBalance
                    )}
                </div>
            `;

    }

}


function updatePaymentForm() {
    const type = document.getElementById("paymentType").value;
    const earned = document.getElementById("earnedField");
    const adjustment = document.getElementById("adjustmentField");
    const partial = document.getElementById("partialAdjustmentField");
    const lumpSumField = document.getElementById("lumpSumCategoryField");

    const lumpSumSummary = document.getElementById("lumpSumAgreementSummary");
    if (lumpSumField) {
        lumpSumField.hidden = type !== "lump_sum_payment";
        if (type === "lump_sum_payment") {
            updateLumpSumAgreementUI();
        } else if (lumpSumSummary) {
            lumpSumSummary.hidden = true;
        }
    }

    if (type === "labour_settlement") {
        earned.style.display = "block";
        adjustment.style.display = "block";
        updatePartialAdjustment();
        calculateSettlementAmount();
    } else {
        earned.style.display = "none";
        adjustment.style.display = "none";
        partial.style.display = "none";
        updateSettlementBreakdownUI();
    }

    const amountHelp = document.getElementById("paymentAmountHelp");
    if (type === "lump_sum_payment" && amountHelp) {
        amountHelp.textContent = "Enter a partial or final payment. Payments cannot exceed the remaining agreed amount.";
    }
}

function getLumpSumOptionValue(worker) {
    return worker.id || `legacy:${worker.name}`;
}

function updateLumpSumAgreementUI() {
    const selector = document.getElementById("lumpSumCategorySelect");
    const summary = document.getElementById("lumpSumAgreementSummary");
    if (!selector || !summary) return null;
    const categories = getLumpSumCategories(currentPaymentTrade);
    const previousSelection = selector.value;

    selector.innerHTML = categories.map(worker =>
        `<option value="${escapeHtml(getLumpSumOptionValue(worker))}">${escapeHtml(worker.name)}</option>`
    ).join("");
    if (categories.some(worker => getLumpSumOptionValue(worker) === previousSelection)) {
        selector.value = previousSelection;
    }

    const selectedWorker = categories.find(worker => getLumpSumOptionValue(worker) === selector.value);
    if (!selectedWorker) {
        summary.innerHTML = "No lump-sum worker categories are configured for this trade.";
        summary.hidden = false;
        return null;
    }

    const totals = getLumpSumPaymentTotals(
        selectedWorker,
        currentPaymentTrade?.paymentTransactions || [],
        editingPaymentId
    );
    summary.innerHTML = `Agreed: <strong>${money(totals.agreedAmount)}</strong> · Paid: <strong>${money(totals.totalPaid)}</strong> · Remaining: <strong>${money(totals.remainingBalance)}</strong>${selectedWorker.work_description ? `<div>${escapeHtml(selectedWorker.work_description)}</div>` : ""}`;
    summary.hidden = false;
    const amountInput = document.getElementById("paymentAmount");
    amountInput.max = String(totals.remainingBalance);
    return selectedWorker;
}

function renderLumpSumAgreements(trade, transactions) {
    const section = document.getElementById("lumpSumAgreementsSection");
    const container = document.getElementById("lumpSumAgreements");
    const categories = getLumpSumCategories(trade);
    const totalPaidElement = document.getElementById("lumpSumPaymentsTotal");
    if (!section || !container) {
        if (totalPaidElement) {
            totalPaidElement.textContent = money(transactions
                .filter(transaction => transaction.type === "lump_sum_payment")
                .reduce((total, transaction) => total + Number(transaction.amount || 0), 0));
        }
        return;
    }
    section.hidden = categories.length === 0;

    const totalPaid = transactions
        .filter(transaction => transaction.type === "lump_sum_payment")
        .reduce((total, transaction) => total + Number(transaction.amount || 0), 0);
    if (totalPaidElement) totalPaidElement.textContent = money(totalPaid);

    container.innerHTML = categories.map(worker => {
        const totals = getLumpSumPaymentTotals(worker, transactions);
        const history = totals.payments.length
            ? `<div class="lump-sum-agreement-details">${sortPaymentTransactions(totals.payments).map(payment => `${escapeHtml(formatDateWithDay(payment.date))}: ${money(payment.amount)}${payment.paymentMode ? ` · ${escapeHtml(payment.paymentMode)}` : ""}`).join("<br>")}</div>`
            : '<div class="lump-sum-agreement-details">No payments recorded.</div>';
        return `<article class="lump-sum-agreement-card">
            <h4>${escapeHtml(worker.name)}</h4>
            ${worker.work_description ? `<p>${escapeHtml(worker.work_description)}</p>` : ""}
            <p>Agreed Amount: <strong>${money(totals.agreedAmount)}</strong></p>
            <p>Total Paid: <strong>${money(totals.totalPaid)}</strong></p>
            <p>Remaining Balance: <strong>${money(totals.remainingBalance)}</strong></p>
            ${history}
        </article>`;
    }).join("");
}


/* =========================================================
   ADVANCE ADJUSTMENT
========================================================= */

function updatePartialAdjustment() {

    const mode =
        document.getElementById(
            "advanceAdjustmentMode"
        ).value;


    const partial =
        document.getElementById(
            "partialAdjustmentField"
        );


    if (
        mode ===
        "partial"
    ) {

        partial.style.display =
            "block";

    }

    else {

        partial.style.display =
            "none";

    }

    settlementAmountUserEdited = false;
    calculateSettlementAmount();

}


/* =========================================================
   CALCULATE SETTLEMENT
========================================================= */

function calculateSettlementAmount() {

    if (
        !currentPaymentTrade
    ) {

        return;

    }


    const earned =
        Number(
            document.getElementById(
                "labourEarned"
            ).value || 0
        );


    const mode =
        document.getElementById(
            "advanceAdjustmentMode"
        ).value;


    let transactions =
        Array.isArray(
            currentPaymentTrade.paymentTransactions
        )
            ? [
                ...currentPaymentTrade.paymentTransactions
            ]
            : [];


    if (
        editingPaymentId
    ) {

        transactions =
            transactions.filter(
                transaction =>
                    transaction.id !==
                    editingPaymentId
            );

    }


    const paymentAmountInput =
        document.getElementById(
            "paymentAmount"
        );

    const amountPaidNow =
        Number(
            paymentAmountInput.value || 0
        );

    const previousOutstandingBalance =
        calculateOutstandingLabourBalance(
            transactions
        );

    const settlementValues =
        calculateSettlementBreakdown({
            transactions,
            labourEarned: earned,
            mode,
            requestedAdjustment:
                Number(
                    document.getElementById(
                        "adjustmentAmount"
                    ).value || 0
                ),
            amountPaidNow,
            previousOutstandingBalance
        });

    if (
        (mode === "full" || mode === "partial") &&
        !settlementAmountUserEdited
    ) {

        paymentAmountInput.value =
            String(
                Math.max(
                    0,
                    settlementValues.amountAvailableToPay
                )
            );

    }

    updateSettlementBreakdownUI();

}


/* =========================================================
   PAYMENT HISTORY
========================================================= */

function renderPaymentHistory(
    trade
) {

    const container =
        document.getElementById(
            "paymentHistory"
        );


    const transactions = sortPaymentTransactions(trade.paymentTransactions);

    const financialSummary = financialOnly
        ? calculateTradeFinancialSummary(trade.contractItems, transactions)
        : null;
    const totalPayments = financialOnly ? financialSummary.contractPayments : calculateTotalPayments(transactions);
    const advanceBalance = financialOnly ? financialSummary.advanceBalance : calculateAdvanceBalance(transactions);
    const settlements = financialOnly
        ? financialSummary.labourSettlements
        : transactions.filter(transaction => transaction.type === "labour_settlement").reduce((total, transaction) => total + Number(transaction.amount || 0), 0);
    const lumpSumPayments = transactions
        .filter(transaction => transaction.type === "lump_sum_payment")
        .reduce((total, transaction) => total + Number(transaction.amount || 0), 0);


    document.getElementById(
        "totalPayments"
    ).textContent =
        money(
            totalPayments
        );


    document.getElementById(
        "advanceBalance"
    ).textContent =
        money(
            advanceBalance
        );


    document.getElementById(
        "settlementTotal"
    ).textContent =
        money(
            settlements
        );
    const dailyWagePaymentsTotal = document.getElementById("dailyWagePaymentsTotal");
    const lumpSumPaymentsTotal = document.getElementById("lumpSumPaymentsTotal");
    if (dailyWagePaymentsTotal) dailyWagePaymentsTotal.textContent = money(settlements);
    if (lumpSumPaymentsTotal) lumpSumPaymentsTotal.textContent = money(lumpSumPayments);

    if (financialOnly) {
        document.getElementById("labourEarnedTotal").textContent = money(financialSummary.labourEarned);
        document.getElementById("labourAdvancesTotal").textContent = money(financialSummary.labourAdvances);
        document.getElementById("advanceDeductionsTotal").textContent = money(financialSummary.advanceDeductions);
    }


    const outstandingBalance = financialOnly
        ? financialSummary.outstandingLabourBalance
        : calculateOutstandingLabourBalance(transactions);

    document.getElementById(
        "outstandingBalance"
    ).textContent =
        money(
            outstandingBalance
        );

    renderLumpSumAgreements(trade, transactions);


    if (
        transactions.length === 0
    ) {

        container.innerHTML = `

            <div
                style="
                    color:#94a3b8;
                    padding:15px 0;
                "
            >
                No payment transactions yet.
            </div>

        `;

        return;

    }


    container.innerHTML =
        transactions
            .map(
                transaction => {

                    const transactionIndex =
                        Array.isArray(
                            trade.paymentTransactions
                        )
                            ? trade.paymentTransactions.indexOf(
                                transaction
                            )
                            : -1;

                    let label =
                        "";

                    let className =
                        "";


                    if (
                        transaction.type ===
                        "mobilization_advance"
                    ) {

                        label =
                            "Mobilization Advance";

                        className =
                            "mobilization";

                    }


                    if (
                        transaction.type ===
                        "labour_advance"
                    ) {

                        label =
                            "Labour Advance";

                        className =
                            "labour-advance";

                    }


                    if (
                        transaction.type ===
                        "labour_settlement"
                    ) {

                        label =
                            "Labour Settlement";

                        className =
                            "settlement";

                    }


                    if (
                        transaction.type ===
                        COMPLETED_WORK_PAYMENT_TYPE
                    ) {

                        label =
                            "Payment Against Completed Work";

                        className =
                            "completed-work-payment";

                    }

                    if (transaction.type === "lump_sum_payment") {
                        label = "Lump-Sum Payment";
                        className = "completed-work-payment";
                    }


                    return `

                        <div
                            class="transaction-row"
                            style="
                                display:grid;
                                grid-template-columns:
                                    160px
                                    135px
                                    150px
                                    minmax(260px,1fr)
                                    120px
                                    190px;
                                gap:12px;
                                align-items:center;
                                padding:12px 8px;
                                border-bottom:1px solid #e2e8f0;
                            "
                        >

                            <div
                                class="transaction-type ${className}"
                            >

                                ${label}
                                ${transaction.type === "lump_sum_payment" ? `<br><small>${escapeHtml(transaction.workerCategoryName || "Worker category")}</small>` : ""}

                            </div>


                            <div>

                                ${formatDateWithDay(
                                    transaction.date
                                )}

                            </div>


                            <div>

                                ${escapeHtml(
                                    trade.name || "Unnamed trade"
                                )}

                            </div>


                            <div>

                                ${
                                    transaction.type ===
                                    "labour_settlement"

                                    ?

                                    `

                                        <strong>
                                            Earned:
                                        </strong>

                                        ${money(
                                            transaction.labourEarned
                                        )}

                                        <br>

                                        <strong>
                                            Deduction:
                                        </strong>

                                        ${money(
                                            transaction.advanceAdjustment
                                        )}

                                        <br>

                                        <strong>
                                            Paid Now:
                                        </strong>

                                        ${money(
                                            Number(
                                                transaction.amount || 0
                                            )
                                        )}

                                        <br>

                                        <strong>
                                            Outstanding:
                                        </strong>

                                        ${money(
                                            Number(
                                                transaction.outstandingBalance ??
                                                Math.max(
                                                    0,
                                                    Number(
                                                        transaction.labourEarned || 0
                                                    ) -
                                                    Number(
                                                        transaction.advanceAdjustment || 0
                                                    ) -
                                                    Number(
                                                        transaction.amount || 0
                                                    )
                                                )
                                            )
                                        )}

                                        ${
                                            transaction.paymentMode
                                            ?
                                            `
                                                <br>

                                                <span
                                                    style="
                                                        color:#64748b;
                                                        font-size:12px;
                                                    "
                                                >
                                                    ${escapeHtml(
                                                        transaction.paymentMode
                                                    )}
                                                </span>
                                            `
                                            :
                                            ""
                                        }

                                        ${
                                            transaction.remarks
                                            ?
                                            `
                                                <br>

                                                <span
                                                    style="
                                                        color:#64748b;
                                                        font-size:12px;
                                                    "
                                                >
                                                    ${escapeHtml(
                                                        transaction.remarks
                                                    )}
                                                </span>
                                            `
                                            :
                                            ""
                                        }

                                    `

                                    :

                                    `

                                        ${
                                            transaction.paymentMode
                                            ?
                                            `
                                                <strong>
                                                    ${escapeHtml(
                                                        transaction.paymentMode
                                                    )}
                                                </strong>

                                                <br>
                                            `
                                            :
                                            ""
                                        }

                                        ${escapeHtml(
                                            transaction.remarks ||
                                            ""
                                        )}

                                    `

                                }

                            </div>


                            <strong
                                style="
                                    text-align:right;
                                    white-space:nowrap;
                                "
                            >

                                ${money(
                                    transaction.amount
                                )}

                            </strong>


                            <div
                                style="
                                    display:flex;
                                    gap:6px;
                                    justify-content:flex-end;
                                "
                            >

                                <button
                                    type="button"
                                    class="btn-secondary btn-small payment-details-btn"
                                    data-payment-id="${escapeHtml(transaction.id || "") }"
                                >
                                    View Details
                                </button>

                                <button
                                    type="button"
                                    class="btn-secondary btn-small payment-edit-btn"
                                    data-payment-id="${escapeHtml(
                                        transaction.id
                                    )}"
                                    data-payment-index="${transactionIndex}"
                                >
                                    Edit
                                </button>


                                <button
                                    type="button"
                                    class="btn-danger btn-small payment-delete-btn"
                                    data-payment-id="${escapeHtml(
                                        transaction.id
                                    )}"
                                >
                                    Delete
                                </button>

                            </div>

                        </div>

                    `;

                }
            )
            .join("");


    container
        .querySelectorAll(".payment-details-btn")
        .forEach(button => {
            button.addEventListener("click", () => {
                openPaymentTransactionDetails(button.dataset.paymentId);
            });
        });


    /*
        EDIT BUTTONS
    */

    container
        .querySelectorAll(
            ".payment-edit-btn"
        )
        .forEach(
            button => {

                button.addEventListener(
                    "click",
                    () => {

                        editPayment(
                            button.dataset.paymentId
                        );

                    }
                );

            }
        );


    /*
        DELETE BUTTONS
    */

    container
        .querySelectorAll(
            ".payment-delete-btn"
        )
        .forEach(
            button => {

                button.addEventListener(
                    "click",
                    () => {

                        deletePayment(
                            button.dataset.paymentId,
                            Number(
                                button.dataset.paymentIndex
                            )
                        );

                    }
                );

            }
        );

}


/* =========================================================
   EDIT PAYMENT
========================================================= */

function editPayment(
    paymentId
) {

    if (
        !currentPaymentTrade
    ) {

        return;

    }


    const transactions =
        Array.isArray(
            currentPaymentTrade.paymentTransactions
        )
            ? currentPaymentTrade.paymentTransactions
            : [];


    const transaction =
        transactions.find(
            item =>
                item.id === paymentId
        );


    if (!transaction) {

        showStatus(
            "Payment transaction not found.",
            true
        );

        return;

    }


    if (
        transaction.type ===
        COMPLETED_WORK_PAYMENT_TYPE
    ) {

        showStatus(
            "This transaction type is temporarily read-only.",
            true
        );

        return;

    }


    editingPaymentId =
        paymentId;


    document.getElementById(
        "paymentType"
    ).value =
        transaction.type;


    document.getElementById(
        "paymentDate"
    ).value =
        transaction.date ||
        todayString();


    document.getElementById(
        "paymentMode"
    ).value =
        transaction.paymentMode ||
        "Cash";


    document.getElementById(
        "paymentRemarks"
    ).value =
        transaction.remarks ||
        "";


    document.getElementById(
        "labourEarned"
    ).value =
        transaction.labourEarned ||
        "";


    const adjustment =
        Number(
            transaction.advanceAdjustment ||
            0
        );


    if (
        transaction.type ===
        "labour_settlement"
    ) {

        if (
            adjustment <= 0
        ) {

            document.getElementById(
                "advanceAdjustmentMode"
            ).value =
                "none";


            document.getElementById(
                "adjustmentAmount"
            ).value =
                "";

        }

        else {

            document.getElementById(
                "advanceAdjustmentMode"
            ).value =
                "partial";


            document.getElementById(
                "adjustmentAmount"
            ).value =
                adjustment;

        }

    }


    document.getElementById(
        "paymentAmount"
    ).value =
        transaction.amount ||
        "";


    document.getElementById(
        "savePaymentBtn"
    ).textContent =
        "Update Payment";


    updatePaymentForm();

    if (transaction.type === "lump_sum_payment") {
        const category = getLumpSumCategories(currentPaymentTrade).find(worker =>
            worker.id === transaction.workerCategoryId || worker.name === transaction.workerCategoryName
        );
        if (category) {
            document.getElementById("lumpSumCategorySelect").value = getLumpSumOptionValue(category);
            updateLumpSumAgreementUI();
        }
    }


    if (
        transaction.type ===
        "labour_settlement"
    ) {

        updatePartialAdjustment();

        calculateSettlementAmount();

    }

}


/* =========================================================
   DELETE PAYMENT
========================================================= */

async function deletePayment(
    paymentId,
    paymentIndex = -1
) {

    if (
        !currentPaymentTrade
    ) {

        return;

    }


    const transactions =
        Array.isArray(
            currentPaymentTrade.paymentTransactions
        )
            ? currentPaymentTrade.paymentTransactions
            : [];


    const transaction =
        transactions.find(
            item =>
                item.id === paymentId
        );


    if (!transaction) {

        showStatus(
            "Payment transaction not found.",
            true
        );

        return;

    }


    const confirmed =
        await showConfirmDialog({
            title: "Mannat Moon",
            message: "Are you sure you want to delete this payment?",
            details: `${formatDateWithDay(transaction.date)}\n${money(transaction.amount)}`,
            confirmText: "Delete",
            cancelText: "Cancel",
            type: "danger"
        });


    if (!confirmed) {
        return;
    }


    if (
        paymentDeleteInProgress
    ) {

        return;

    }


    paymentDeleteInProgress =
        true;


    try {

        const tradeRef =
            doc(
                db,
                TRADES_COLLECTION,
                currentPaymentTrade.id
            );


        const freshSnapshot =
            await getDoc(
                tradeRef
            );


        if (
            !freshSnapshot.exists()
        ) {

            throw new Error(
                "Trade no longer exists."
            );

        }


        const freshTrade =
            freshSnapshot.data();


        const freshTransactions =
            Array.isArray(
                freshTrade.paymentTransactions
            )
                ? [
                    ...freshTrade.paymentTransactions
                ]
                : [];


        const selectedIndex =
            paymentId
                ? freshTransactions.findIndex(
                    item =>
                        item.id === paymentId
                )
                : paymentIndex;

        if (
            selectedIndex < 0 ||
            selectedIndex >= freshTransactions.length
        ) {

            throw new Error(
                "The selected payment transaction no longer exists."
            );

        }

        if (
            !paymentId &&
            (
                freshTransactions[selectedIndex].type !==
                transaction.type ||
                String(
                    freshTransactions[selectedIndex].date || ""
                ) !== String(
                    transaction.date || ""
                ) ||
                Number(
                    freshTransactions[selectedIndex].amount || 0
                ) !== Number(
                    transaction.amount || 0
                )
            )
        ) {

            throw new Error(
                "The selected payment transaction changed. Refresh and try again."
            );

        }

        const result = await sendLabourPaymentMutation(
            `/api/labour-payments/${encodeURIComponent(currentPaymentTrade.id)}/transactions/${encodeURIComponent(freshTransactions[selectedIndex].id)}`,
            "DELETE"
        );

        currentPaymentTrade = {

            id:
                currentPaymentTrade.id,

            ...freshTrade,

            paymentTransactions:
                result.paymentTransactions

        };


        if (
            editingPaymentId ===
            paymentId
        ) {

            editingPaymentId =
                null;


            document.getElementById(
                "savePaymentBtn"
            ).textContent =
                "Save Payment";

        }


        renderPaymentHistory(
            currentPaymentTrade
        );


        await loadTrades();


        showStatus(
            "Payment transaction deleted."
        );

        paymentDeleteInProgress =
            false;

    }

    catch (error) {

        console.error(
            error
        );

        paymentDeleteInProgress =
            false;

        showStatus(
            "Unable to delete the payment. Please try again.",
            true
        );

    }

}


async function sendLabourPaymentMutation(path, method, payload, requestId = null) {
    const csrfToken = document.body.dataset.labourPaymentsCsrf || "";
    if (!csrfToken) {
        throw new Error("Your session expired. Reload the page and try again.");
    }
    const response = await fetch(path, {
        method,
        credentials: "same-origin",
        headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken,
            ...(requestId ? { "Idempotency-Key": requestId } : {})
        },
        ...(payload ? { body: JSON.stringify(payload) } : {})
    });
    let result = {};
    try {
        result = await response.json();
    } catch {
        throw new Error("Unable to save the payment. Please try again.");
    }
    if (!response.ok) {
        throw new Error(typeof result.error === "string" ? result.error : "Unable to save the payment. Please try again.");
    }
    if (!Array.isArray(result.paymentTransactions)) {
        throw new Error("The server returned an invalid payment update.");
    }
    return result;
}


/* =========================================================
   SAVE / UPDATE PAYMENT
========================================================= */

async function savePayment() {

    if (
        !currentPaymentTrade
    ) {

        return;

    }


    const type =
        document.getElementById(
            "paymentType"
        ).value;

    let lumpSumCategory = null;


    const date =
        document.getElementById(
            "paymentDate"
        ).value;


    const remarks =
        document.getElementById(
            "paymentRemarks"
        ).value.trim();


    const paymentMode =
        document.getElementById(
            "paymentMode"
        ).value;


    if (!date) {

        showStatus(
            "Select a payment date.",
            true
        );

        return;

    }


    let amount =
        0;


    let labourEarned =
        0;


    let advanceAdjustment =
        0;


    let outstandingBalance =
        0;


    let transactions =
        Array.isArray(
            currentPaymentTrade?.paymentTransactions
        )
            ? [
                ...currentPaymentTrade.paymentTransactions
            ]
            : [];


    if (
        editingPaymentId
    ) {

        transactions =
            transactions.filter(
                transaction =>
                    transaction.id !==
                    editingPaymentId
            );

    }


    /* =====================================================
       MOBILIZATION ADVANCE
    ===================================================== */

    if (
        type ===
        "mobilization_advance"
    ) {

        amount =
            Number(
                document.getElementById(
                    "paymentAmount"
                ).value || 0
            );

    }


    /* =====================================================
       LABOUR ADVANCE
    ===================================================== */

    if (
        type ===
        "labour_advance"
    ) {

        amount =
            Number(
                document.getElementById(
                    "paymentAmount"
                ).value || 0
            );

    }


    /* =====================================================
       LABOUR SETTLEMENT
    ===================================================== */

    if (
        type ===
        "labour_settlement"
    ) {

        labourEarned =
            Number(
                document.getElementById(
                    "labourEarned"
                ).value || 0
            );


        const mode =
            document.getElementById(
                "advanceAdjustmentMode"
            ).value;


        if (
            editingPaymentId
        ) {

            transactions =
                transactions.filter(
                    transaction =>
                        transaction.id !==
                        editingPaymentId
                );

        }


        const balance =
            calculateAdvanceBalance(
                transactions
            );


        if (
            mode ===
            "none"
        ) {

            advanceAdjustment =
                0;

        }


        if (
            mode ===
            "full"
        ) {

            advanceAdjustment =
                Math.min(
                    balance,
                    labourEarned
                );

        }


        if (
            mode ===
            "partial"
        ) {

            const requested =
                Number(
                    document.getElementById(
                        "adjustmentAmount"
                    ).value || 0
                );


            advanceAdjustment =
                Math.min(
                    balance,
                    labourEarned,
                    requested
                );

        }


        amount =
            Number(
                document.getElementById(
                    "paymentAmount"
                ).value || 0
            );


        const previousOutstandingBalance =
            calculateOutstandingLabourBalance(
                transactions
            );

        const netLabourPayable =
            Math.max(
                0,
                labourEarned -
                advanceAdjustment
            );

        outstandingBalance =
            Math.max(
                0,
                previousOutstandingBalance +
                netLabourPayable -
                amount
            );

    }


    /* =====================================================
       VALIDATION
    ===================================================== */

    if (type === "lump_sum_payment") {
        const categoryValue = document.getElementById("lumpSumCategorySelect").value;
        lumpSumCategory = getLumpSumCategories(currentPaymentTrade).find(worker =>
            getLumpSumOptionValue(worker) === categoryValue
        );
        amount = Number(document.getElementById("paymentAmount").value);
        if (!lumpSumCategory) {
            showStatus("Select a lump-sum worker category.", true);
            return;
        }
        if (!Number.isFinite(amount) || amount <= 0) {
            showStatus("Enter a lump-sum payment greater than zero.", true);
            return;
        }
        const available = getLumpSumPaymentTotals(
            lumpSumCategory,
            currentPaymentTrade.paymentTransactions || [],
            editingPaymentId
        ).remainingBalance;
        if (amount > available) {
            showStatus(`Payment exceeds the remaining lump-sum balance of ${money(available)}.`, true);
            return;
        }
    }

    if (
        (!Number.isFinite(amount) || amount <= 0) &&
        type !==
        "labour_settlement"
    ) {

        showStatus(
            "Enter an amount greater than zero.",
            true
        );

        return;

    }


    if (
        type ===
        "labour_settlement" &&
        labourEarned < 0
    ) {

        showStatus(
            "Labour Earned cannot be negative.",
            true
        );

        return;

    }


    if (
        type ===
        "labour_settlement"
    ) {

        const maximumAllowedAmount =
            calculateMaximumSettlementAmount({
                transactions,
                labourEarned,
                advanceAdjustment,
                editingPaymentId
            });

        if (
            amount >
            maximumAllowedAmount
        ) {

            showStatus(
                "Amount paid now cannot exceed the current outstanding labour balance.",
                true
            );

            return;

        }

    }


    if (
        paymentSaveInProgress
    ) {

        return;

    }


    paymentSaveInProgress =
        true;


    try {

        const tradeRef =
            doc(
                db,
                TRADES_COLLECTION,
                currentPaymentTrade.id
            );


        const freshSnapshot =
            await getDoc(
                tradeRef
            );


        if (
            !freshSnapshot.exists()
        ) {

            throw new Error(
                "Trade no longer exists."
            );

        }


        const freshTrade =
            freshSnapshot.data();


        let transactions =
            Array.isArray(
                freshTrade.paymentTransactions
            )
                ? [
                    ...freshTrade.paymentTransactions
                ]
                : [];

        if (type === "lump_sum_payment") {
            const freshCategories = Array.isArray(freshTrade.workerCategories)
                ? freshTrade.workerCategories
                : Array.isArray(freshTrade.workers) ? freshTrade.workers : [];
            const freshCategory = freshCategories.find(worker =>
                (lumpSumCategory.id && worker.id === lumpSumCategory.id) ||
                worker.name === lumpSumCategory.name
            );
            if (!freshCategory || freshCategory.payment_type !== "lump_sum" || Number(freshCategory.lump_sum_amount || 0) <= 0) {
                throw new Error("The selected lump-sum agreement no longer exists or has an invalid amount.");
            }
            const freshTotals = getLumpSumPaymentTotals(freshCategory, transactions, editingPaymentId);
            if (amount > freshTotals.remainingBalance) {
                throw new Error(`Payment exceeds the remaining lump-sum balance of ${money(freshTotals.remainingBalance)}.`);
            }
            lumpSumCategory = { ...freshCategory, payment_type: "lump_sum" };
        }

        const paymentPayload = {
            type,
            date,
            amount,
            labourEarned,
            advanceAdjustmentMode: document.getElementById("advanceAdjustmentMode").value,
            requestedAdjustment: Number(document.getElementById("adjustmentAmount").value || 0),
            paymentMode,
            remarks,
            ...(type === "lump_sum_payment" ? {
                workerCategoryId: getLumpSumOptionValue(lumpSumCategory)
            } : {})
        };
        const apiPath = `/api/labour-payments/${encodeURIComponent(currentPaymentTrade.id)}/transactions`;
        if (!editingPaymentId && !pendingPaymentCreateId) {
            pendingPaymentCreateId = generateUuid();
        }
        const result = await sendLabourPaymentMutation(
            editingPaymentId ? `${apiPath}/${encodeURIComponent(editingPaymentId)}` : apiPath,
            editingPaymentId ? "PATCH" : "POST",
            paymentPayload,
            editingPaymentId ? null : pendingPaymentCreateId
        );
        if (!editingPaymentId) {
            pendingPaymentCreateId = null;
        }
        transactions = result.paymentTransactions;

        currentPaymentTrade = {

            id:
                currentPaymentTrade.id,

            ...freshTrade,

            paymentTransactions:
                transactions

        };


        const wasEditing =
            Boolean(
                editingPaymentId
            );


        editingPaymentId =
            null;


        document.getElementById(
            "savePaymentBtn"
        ).textContent =
            "Save Payment";


        renderPaymentHistory(
            currentPaymentTrade
        );


        applySettlementFormReset({
            labourEarned: document.getElementById("labourEarned"),
            advanceAdjustmentMode: document.getElementById("advanceAdjustmentMode"),
            adjustmentAmount: document.getElementById("adjustmentAmount"),
            paymentAmount: document.getElementById("paymentAmount"),
            paymentRemarks: document.getElementById("paymentRemarks")
        });


        await loadTrades();


        paymentSaveInProgress =
            false;


        showStatus(

            wasEditing

                ?

                "Payment transaction updated."

                :

                "Payment transaction saved."

        );

    }

    catch (error) {

        console.error(
            error
        );


        paymentSaveInProgress =
            false;


        showStatus(
            "Unable to save the payment. Please check your connection and try again.",
            true
        );

    }

}


/* =========================================================
   DOWNLOAD TRADE PDF
========================================================= */

async function downloadTradePDF(
    trade
) {

    try {

        const jsPDFConstructor = window.jspdf?.jsPDF || window.jsPDF;

        if (!jsPDFConstructor) {
            throw new Error("jsPDF library not loaded. Please refresh the page.");
        }


        /* =====================================================
           LOAD NOTO SANS FONT WITH ₹ SUPPORT
        ===================================================== */

        let doc = new jsPDFConstructor();

        try {

            const regularFontUrl = "https://cdn.jsdelivr.net/npm/@fontsource/noto-sans@5.0.28/files/noto-sans-all-400-normal.ttf";

            const boldFontUrl = "https://cdn.jsdelivr.net/npm/@fontsource/noto-sans@5.0.28/files/noto-sans-all-700-normal.ttf";

            const regularResponse = await fetch(
                regularFontUrl
            );

            const boldResponse = await fetch(
                boldFontUrl
            );

            if (regularResponse.ok && boldResponse.ok) {

                const regularBuffer = await regularResponse.arrayBuffer();

                const boldBuffer = await boldResponse.arrayBuffer();

                const regularBytes = new Uint8Array(
                    regularBuffer
                );

                const boldBytes = new Uint8Array(
                    boldBuffer
                );

                let regularBinary = "";

                for (
                    let i = 0;
                    i < regularBytes.byteLength;
                    i++
                ) {
                    regularBinary += String.fromCharCode(
                        regularBytes[i]
                    );
                }

                let boldBinary = "";

                for (
                    let i = 0;
                    i < boldBytes.byteLength;
                    i++
                ) {
                    boldBinary += String.fromCharCode(
                        boldBytes[i]
                    );
                }

                const regularBase64 = btoa(
                    regularBinary
                );

                const boldBase64 = btoa(
                    boldBinary
                );

                doc.addFileToVFS(
                    "NotoSans-Regular.ttf",
                    regularBase64
                );

                doc.addFileToVFS(
                    "NotoSans-Bold.ttf",
                    boldBase64
                );

                doc.addFont(
                    "NotoSans-Regular.ttf",
                    "NotoSans",
                    "normal"
                );

                doc.addFont(
                    "NotoSans-Bold.ttf",
                    "NotoSans",
                    "bold"
                );

                doc.setFont(
                    "NotoSans",
                    "normal"
                );

            }
            else {

                console.warn(
                    "Failed to load Noto Sans fonts, using standard fonts."
                );

            }

        }
        catch (error) {

            console.warn(
                "Font loading failed, using standard fonts:",
                error.message
            );

        }


        const pageWidth = doc.internal.pageSize.getWidth();

        let yPos = 20;


        /* =====================================================
           TITLE - KHATABOOK STYLE HEADER
        ===================================================== */

        doc.setFillColor(
            13,
            79,
            145
        );

        doc.rect(
            0,
            0,
            pageWidth,
            35,
            "F"
        );

        doc.setTextColor(
            255,
            255,
            255
        );

        doc.setFontSize(
            18
        );

        doc.setFont(
            "helvetica",
            "bold"
        );

        doc.text(
            "MANNAT MOON CONSTRUCTION",
            pageWidth / 2,
            15,
            {
                align: "center"
            }
        );

        doc.setFontSize(
            12
        );

        doc.setFont(
            "helvetica",
            "normal"
        );

        doc.text(
            "Trade Details Report",
            pageWidth / 2,
            28,
            {
                align: "center"
            }
        );

        doc.setTextColor(
            51,
            51,
            51
        );

        yPos = 45;


        /* =====================================================
           TRADE NAME
        ===================================================== */

        doc.setFontSize(
            11
        );

        doc.setFont(
            "helvetica",
            "bold"
        );

        doc.setTextColor(
            51,
            51,
            51
        );

        doc.text(
            `Trade: ${escapeHtml(
                trade.name || ""
            )}`,
            20,
            yPos
        );

        yPos += 8;


        /* =====================================================
           PDF DATE
        ===================================================== */

        doc.setFont(
            "helvetica",
            "normal"
        );

        doc.setTextColor(
            102,
            102,
            102
        );

        doc.text(
            `Generated: ${formatDateWithDay(
                todayString()
            )}`,
            20,
            yPos
        );

        yPos += 12;


        /* =====================================================
           WORKER CATEGORIES
        ===================================================== */

        const workers = getTradeWorkerCategories(trade);

        if (
            workers.length > 0
        ) {

            doc.setFontSize(
                10
            );

            doc.setFont(
                "helvetica",
                "bold"
            );

            doc.setTextColor(
                51,
                51,
                51
            );

            doc.text(
                "Worker Categories",
                20,
                yPos
            );

            yPos += 6;

            const workerData = workers.map(worker => {
                const lumpSumTotals = worker.payment_type === "lump_sum"
                    ? getLumpSumPaymentTotals(worker, trade.paymentTransactions || [])
                    : null;
                return [
                    escapeHtml(
                        worker.name || ""
                    ),
                    worker.payment_type === "lump_sum" ? "Lump Sum" : "Daily Wage",
                    worker.payment_type === "lump_sum"
                        ? money(worker.lump_sum_amount || 0)
                        : `${money(worker.currentWage || 0)} / day`,
                    formatDateWithDay(
                        worker.effective_from || worker.effectiveFrom || ""
                    ),
                    lumpSumTotals ? money(lumpSumTotals.totalPaid) : "-",
                    lumpSumTotals ? money(lumpSumTotals.remainingBalance) : "-",
                    escapeHtml(worker.work_description || "")
                ];
            });

            doc.autoTable({
                startY: yPos,
                head: [
                    [
                        "Category",
                        "Payment Type",
                        "Amount",
                        "Effective From",
                        "Total Paid",
                        "Remaining",
                        "Work Description"
                    ]
                ],
                body: workerData,
                theme: "grid",
                headStyles: {
                    fillColor: [242, 242, 242],
                    textColor: [51, 51, 51],
                    fontStyle: "bold",
                    fontSize: 9
                },
                styles: {
                    fontSize: 8,
                    textColor: [51, 51, 51],
                    lineColor: [184, 184, 184],
                    lineWidth: 0.1
                },
                columnStyles: {
                    0: {
                        cellWidth: "auto"
                    },
                    1: {
                        cellWidth: "auto",
                        halign: "right"
                    },
                    2: {
                        cellWidth: "auto"
                    }
                }
            });

            yPos = doc.lastAutoTable.finalY + 10;


            /* =====================================================
               WAGE HISTORY
            ===================================================== */

            workers.forEach(
                worker => {

                    const history =
                        Array.isArray(
                            worker.wageHistory
                        )
                            ? worker.wageHistory
                            : [];

                    if (history.length > 0 && worker.payment_type !== "lump_sum") {

                        doc.setFontSize(
                            10
                        );

                        doc.setFont(
                            "helvetica",
                            "bold"
                        );

                        doc.setTextColor(
                            51,
                            51,
                            51
                        );

                        doc.text(
                            `Wage History - ${escapeHtml(
                                worker.name
                            )}`,
                            20,
                            yPos
                        );

                        yPos += 6;

                        const historyData = history
                            .slice()
                            .sort(
                                (
                                    a,
                                    b
                                ) =>
                                    String(
                                        b.effectiveFrom || ""
                                    ).localeCompare(
                                        String(
                                            a.effectiveFrom || ""
                                        )
                                    )
                            )
                            .map(
                                item => [
                                    formatDateWithDay(
                                        item.effectiveFrom
                                    ),
                                    money(
                                        item.dailyWage
                                    )
                                ]
                            );

                        doc.autoTable({
                            startY: yPos,
                            head: [
                                [
                                    "Effective From",
                                    "Daily Wage"
                                ]
                            ],
                            body: historyData,
                            theme: "grid",
                            headStyles: {
                                fillColor: [242, 242, 242],
                                textColor: [51, 51, 51],
                                fontStyle: "bold",
                                fontSize: 9
                            },
                            styles: {
                                fontSize: 8,
                                textColor: [51, 51, 51],
                                lineColor: [184, 184, 184],
                                lineWidth: 0.1
                            },
                            columnStyles: {
                                0: {
                                    cellWidth: "auto"
                                },
                                1: {
                                    cellWidth: "auto",
                                    halign: "right"
                                }
                            }
                        });

                        yPos = doc.lastAutoTable.finalY + 10;

                    }

                }
            );

        }


        /* =====================================================
           WORK CONTRACTS
        ===================================================== */

        const contractItems =
            Array.isArray(
                trade.contractItems
            )
                ? trade.contractItems
                : [];

        const contractTotal =
            calculateContractTotal(
                contractItems
            );

        if (
            contractItems.length > 0
        ) {

            doc.setFontSize(
                10
            );

            doc.setFont(
                "helvetica",
                "bold"
            );

            doc.setTextColor(
                51,
                51,
                51
            );

            doc.text(
                "Work Contracts / Capping",
                20,
                yPos
            );

            yPos += 6;

            const contractData = contractItems.map(
                item => [
                    escapeHtml(
                        item.name || ""
                    ),
                    money(
                        item.amount || 0
                    )
                ]
            );

            doc.autoTable({
                startY: yPos,
                head: [
                    [
                        "Contract Item",
                        "Amount"
                    ]
                ],
                body: contractData,
                theme: "grid",
                headStyles: {
                    fillColor: [242, 242, 242],
                    textColor: [51, 51, 51],
                    fontStyle: "bold",
                    fontSize: 9
                },
                styles: {
                    fontSize: 8,
                    textColor: [51, 51, 51],
                    lineColor: [184, 184, 184],
                    lineWidth: 0.1
                },
                columnStyles: {
                    0: {
                        cellWidth: "auto"
                    },
                    1: {
                        cellWidth: "auto",
                        halign: "right"
                    }
                }
            });

            yPos = doc.lastAutoTable.finalY + 5;

            doc.setFontSize(
                10
            );

            doc.setFont(
                "helvetica",
                "bold"
            );

            doc.setTextColor(
                51,
                51,
                51
            );

            doc.text(
                `Total Contract Value: ${money(
                    contractTotal
                )}`,
                20,
                yPos
            );

            yPos += 12;

        }


        /* =====================================================
           PAYMENT SUMMARY
        ===================================================== */

        const transactions =
            Array.isArray(
                trade.paymentTransactions
            )
                ? trade.paymentTransactions
                : [];

        const totalPayments =
            calculateTotalPayments(
                transactions
            );

        const balanceContractValue =
            contractTotal - totalPayments;

        const paymentPercentage =
            calculatePaymentPercentage(
                totalPayments,
                contractTotal
            );

        const advanceBalance =
            calculateAdvanceBalance(
                transactions
            );

        doc.setFontSize(
                10
            );

            doc.setFont(
                "helvetica",
                "bold"
            );

            doc.setTextColor(
                51,
                51,
                51
            );

            doc.text(
                "Payment Summary",
                20,
                yPos
            );

            yPos += 6;

            doc.setFontSize(
                9
            );

            doc.setFont(
                "helvetica",
                "normal"
            );

            doc.setTextColor(
                51,
                51,
                51
            );

            doc.text(
                `Total Payments: ${money(
                totalPayments
            )}`,
                20,
                yPos
            );

            yPos += 5;

            doc.text(
                `Balance Contract Value: ${money(
                balanceContractValue
            )}`,
                20,
                yPos
            );

            yPos += 5;

            doc.text(
                `Payment Percentage: ${paymentPercentage.toFixed(
                2
            )}%`,
                20,
                yPos
            );

            yPos += 5;

            doc.text(
                `Labour Advance Balance: ${money(
                advanceBalance
            )}`,
                20,
                yPos
            );

            yPos += 12;

            const lumpSumPayments = transactions
                .filter(transaction => transaction.type === "lump_sum_payment")
                .reduce((total, transaction) => total + Number(transaction.amount || 0), 0);
                doc.text(`Lump-Sum Payments: ${money(lumpSumPayments)}`, 20, yPos + 5);
                yPos += 17;


        /* =====================================================
           PAYMENT HISTORY
        ===================================================== */

        if (
            transactions.length > 0
        ) {

            doc.setFontSize(
                10
            );

            doc.setFont(
                "helvetica",
                "bold"
            );

            doc.setTextColor(
                51,
                51,
                51
            );

            doc.text(
                "Payment History",
                20,
                yPos
            );

            yPos += 6;

            const paymentData = sortPaymentTransactions(transactions)
                .map(
                    transaction => {
                        let typeLabel = "";

                        if (
                            transaction.type ===
                            "mobilization_advance"
                        ) {
                            typeLabel =
                                "Mobilization Advance";
                        }
                        else if (
                            transaction.type ===
                            "labour_advance"
                        ) {
                            typeLabel =
                                "Labour Advance";
                        }
                        else if (
                            transaction.type ===
                            "labour_settlement"
                        ) {
                            typeLabel =
                                "Labour Settlement";
                        }
                        else if (
                            transaction.type ===
                            COMPLETED_WORK_PAYMENT_TYPE
                        ) {
                            typeLabel =
                                "Payment Against Completed Work";
                        }
                        else if (transaction.type === "lump_sum_payment") {
                            typeLabel = "Lump-Sum Payment";
                        }
                        else {
                            typeLabel =
                                transaction.type || "";
                        }

                        return [
                            formatDateWithDay(
                                transaction.date
                            ),
                            typeLabel,
                            escapeHtml(transaction.workerCategoryName || ""),
                            money(
                                transaction.amount || 0
                            ),
                            escapeHtml(
                                transaction.paymentMode || ""
                            ),
                            escapeHtml(
                                transaction.remarks || ""
                            ),
                            transaction.type
                        ];

                    }
                );

            doc.autoTable({
                startY: yPos,
                head: [
                    [
                        "Date",
                        "Type",
                        "Worker Category",
                        "Amount",
                        "Mode",
                        "Remarks"
                    ]
                ],
                body: paymentData.map(
                    row => row.slice(0, 6)
                ),
                theme: "grid",
                headStyles: {
                    fillColor: [242, 242, 242],
                    textColor: [51, 51, 51],
                    fontStyle: "bold",
                    fontSize: 8
                },
                styles: {
                    fontSize: 8,
                    textColor: [51, 51, 51],
                    lineColor: [184, 184, 184],
                    lineWidth: 0.1
                },
                columnStyles: {
                    0: {
                        cellWidth: "auto"
                    },
                    1: {
                        cellWidth: "auto"
                    },
                    3: {
                        cellWidth: "auto",
                        halign: "right"
                    },
                    4: {
                        cellWidth: "auto"
                    }
                },
                didParseCell: function(
                    data
                ) {
                    if (
                        data.section === "body" &&
                        data.column.index === 3
                    ) {
                        const transactionType = paymentData[
                            data.row.index
                        ][6];

                        if (
                            transactionType ===
                            "labour_settlement"
                        ) {
                            data.cell.styles.fillColor = [
                                230,
                                242,
                                236
                            ];
                            data.cell.styles.textColor = [
                                76,
                                154,
                                112
                            ];
                        }
                        else if (
                            transactionType ===
                            "labour_advance"
                        ) {
                            data.cell.styles.fillColor = [
                                248,
                                233,
                                233
                            ];
                            data.cell.styles.textColor = [
                                212,
                                91,
                                106
                            ];
                        }
                        else if (
                            transactionType ===
                            "mobilization_advance"
                        ) {
                            data.cell.styles.fillColor = [
                                248,
                                233,
                                233
                            ];
                            data.cell.styles.textColor = [
                                212,
                                91,
                                106
                            ];
                        }
                    }
                }
            });

        }


        /* =====================================================
           DOWNLOAD
        ===================================================== */

        const fileName =
            `${(
                trade.name || "trade"
            ).replace(
                /[^a-zA-Z0-9]/g,
                "_"
            )}_trade_report.pdf`;

        doc.save(
            fileName
        );

        showStatus(
            "PDF downloaded successfully."
        );

    }

    catch (error) {

        console.error(
            error
        );

        showStatus(
            "Unable to create the PDF. Please refresh the page and try again.",
            true
        );

    }

}


/* =========================================================
   EVENT LISTENERS
========================================================= */

if (!financialOnly) {

    document.getElementById(
        "addWorkerBtn"
    ).addEventListener(
        "click",
        () =>
            addWorkerCategory()
    );


    document.getElementById(
        "tradeName"
    ).addEventListener(
        "input",
        event => {

            event.target.value =
                event.target.value.toUpperCase();

        }
    );


    document.getElementById(
        "saveTradeBtn"
    ).addEventListener(
        "click",
        saveTrade
    );


    document.getElementById(
        "clearBtn"
    ).addEventListener(
        "click",
        resetForm
    );

}


document.getElementById(
    "paymentType"
).addEventListener(
    "change",
    updatePaymentForm
);

const lumpSumCategorySelect = document.getElementById("lumpSumCategorySelect");
if (lumpSumCategorySelect) {
    lumpSumCategorySelect.addEventListener("change", updateLumpSumAgreementUI);
}


document.getElementById(
    "advanceAdjustmentMode"
).addEventListener(
    "change",
    updatePartialAdjustment
);


document.getElementById(
    "labourEarned"
).addEventListener(
    "input",
    calculateSettlementAmount
);


document.getElementById(
    "adjustmentAmount"
).addEventListener(
    "input",
    () => {
        settlementAmountUserEdited = false;
        calculateSettlementAmount();
    }
);


document.getElementById(
    "paymentAmount"
).addEventListener(
    "input",
    () => {
        settlementAmountUserEdited = true;
    }
);


document.getElementById(
    "savePaymentBtn"
).addEventListener(
    "click",
    savePayment
);

document.getElementById(
    "paymentLedgerDownloadPdf"
).addEventListener(
    "click",
    async event => {
        if (!currentPaymentTrade) return;
        event.currentTarget.disabled = true;
        try {
            await downloadPaymentLedgerPDF(currentPaymentTrade);
        }
        catch (error) {
            console.error(error);
            showStatus("Unable to create the payment ledger PDF. Please try again.", true);
        }
        finally {
            event.currentTarget.disabled = false;
        }
    }
);

const financialTradeSelect = document.getElementById("financialTradeSelect");
if (financialTradeSelect) {
    financialTradeSelect.addEventListener(
        "change",
        event => renderSelectedFinancialTrade(event.target.value)
    );
}

document.getElementById(
    "tradeLedgerClose"
).addEventListener(
    "click",
    () => closeTradeLedgerModal()
);

document.getElementById(
    "tradeLedgerModal"
).addEventListener(
    "click",
    event => {
        if (event.target.id === "tradeLedgerModal") {
            closeTradeLedgerModal();
        }
    }
);

document.getElementById(
    "tradeLedgerDownloadPdf"
).addEventListener(
    "click",
    async event => {
        if (!currentViewLedgerTrade) return;
        event.currentTarget.disabled = true;
        try {
            await downloadLedgerPDF(currentViewLedgerTrade);
        }
        catch (error) {
            console.error(error);
            showStatus("Unable to create the trade ledger PDF. Please try again.", true);
        }
        finally {
            event.currentTarget.disabled = false;
        }
    }
);


/* =========================================================
   START
========================================================= */

async function initializeApplication() {
    if (!financialOnly) {
        addWorkerCategory();
    }

    await loadTrades();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeApplication, { once: true });
} else {
    initializeApplication();
}