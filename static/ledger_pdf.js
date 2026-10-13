import {
  calculateAdvanceBalance,
  calculateNetLabourPayable,
  calculateOutstandingLabourBalance,
  calculateOutstandingBalanceAfterTransaction
} from "./settlement_logic.mjs";
import { sortPaymentTransactions } from "./payment_sort.mjs";
import {
  uniquePaymentTransactions
} from "./trade_payment_history.mjs";
import { calculateContractPaymentSummary } from "./contract_payment_summary.mjs";
import { formatDate, formatDateWithDay } from "./date_format.js";

const COMPLETED_WORK_PAYMENT_TYPE = "completed_work_payment";
const CONTRACT_PAYMENT_CATEGORY_LABELS = {
  mobilization_advance: "Mobilization Advances",
  labour_advance: "Labour Advances Issued",
  labour_settlement: "Labour Settlement Payments Made",
  completed_work_payment: "Payments Against Completed Work"
};

function money(value) {
  return `INR ${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(Number(value || 0))}`;
}

function workerCategories(trade) {
  const source = Array.isArray(trade?.workerCategories)
    ? trade.workerCategories
    : Array.isArray(trade?.workers) ? trade.workers : [];
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

function contractTotal(items) {
  return Array.isArray(items) ? items.reduce((total, item) => total + Number(item?.amount || 0), 0) : 0;
}

function totalPayments(transactions) {
  return Array.isArray(transactions) ? transactions.reduce((total, item) => total + (item?.type === "lump_sum_payment" ? 0 : Number(item?.amount || 0)), 0) : 0;
}

function paymentTypeLabel(type) {
  return {
    mobilization_advance: "Mobilization Advance",
    labour_advance: "Labour Advance",
    labour_settlement: "Labour Settlement",
    lump_sum_payment: "Lump-Sum Payment",
    [COMPLETED_WORK_PAYMENT_TYPE]: "Payment Against Completed Work"
  }[type] || type || "";
}

export function paymentLedgerSummary(trade) {
  const contractItems = Array.isArray(trade?.contractItems) ? trade.contractItems : [];
  const transactions = uniquePaymentTransactions(trade?.paymentTransactions);
  const validTransactions = transactions.filter(transaction =>
    transaction && ["mobilization_advance", "labour_advance", "labour_settlement", COMPLETED_WORK_PAYMENT_TYPE].includes(transaction.type)
  );
  const contractSummary = calculateContractPaymentSummary(contractItems, validTransactions);
  const { contractTotal: contractValue, contractPayments } = contractSummary;
  const labourSettlements = validTransactions
    .filter(transaction => transaction.type === "labour_settlement")
    .reduce((total, transaction) => total + Number(transaction.amount || 0), 0);
  const labourSettlementBreakdown = contractSummary.contractPaymentBreakdown.find(category => category.type === "labour_settlement") || { amount: 0 };
  const labourEarned = validTransactions
    .filter(transaction => transaction.type === "labour_settlement")
    .reduce((total, transaction) => total + Number(transaction.labourEarned || 0), 0);
  const labourAdvances = validTransactions
    .filter(transaction => transaction.type === "labour_advance")
    .reduce((total, transaction) => total + Number(transaction.amount || 0), 0);
  const advanceDeductions = validTransactions
    .filter(transaction => transaction.type === "labour_settlement")
    .reduce((total, transaction) => total + Number(transaction.advanceAdjustment || 0), 0);
  const lumpSumPayments = transactions
    .filter(transaction => transaction.type === "lump_sum_payment")
    .reduce((total, transaction) => total + Number(transaction.amount || 0), 0);
  const lumpSumAgreements = workerCategories(trade)
    .filter(worker => worker.payment_type === "lump_sum")
    .map(worker => {
      const payments = transactions.filter(transaction =>
        transaction.type === "lump_sum_payment" &&
        ((worker.id && transaction.workerCategoryId === worker.id) || transaction.workerCategoryName === worker.name)
      );
      const agreedAmount = Number(worker.lump_sum_amount || 0);
      const totalPaid = payments.reduce((total, payment) => total + Number(payment.amount || 0), 0);
      return { name: worker.name, agreedAmount, totalPaid, remainingBalance: Math.max(0, agreedAmount - totalPaid) };
    });

  return {
    contractValue,
    contractPaymentBreakdown: contractSummary.contractPaymentBreakdown,
    labourSettlementBreakdown,
    contractPayments,
    balanceContractValue: contractSummary.balanceContractValue,
    paidPercentage: contractSummary.paidPercentage,
    remainingPercentage: contractSummary.remainingPercentage,
    labourSettlements,
    labourEarned,
    labourAdvances,
    advanceDeductions,
    netLabourPayable: calculateNetLabourPayable(labourEarned, advanceDeductions),
    lumpSumPayments,
    lumpSumAgreements,
    advanceBalance: calculateAdvanceBalance(validTransactions),
    outstandingBalance: calculateOutstandingLabourBalance(validTransactions)
  };
}

function renderAccountSummaries(doc, summary, startY) {
  let y = startY;
  const tableOptions = {
    head: [["Summary", "Amount"]],
    theme: "grid",
    headStyles: { fillColor: [242, 242, 242], textColor: [51, 51, 51], fontStyle: "bold", fontSize: 9 },
    styles: { fontSize: 9, textColor: [51, 51, 51], lineColor: [184, 184, 184], lineWidth: 0.1 },
    columnStyles: { 1: { halign: "right" } },
    rowPageBreak: "avoid",
    showHead: "everyPage"
  };

  doc.setFont("helvetica", "bold");
  doc.setFontSize(11);
  doc.text("Contract Account", 20, y);
  doc.autoTable({
    ...tableOptions,
    startY: y + 5,
    body: [
      ["Total Contract Value", money(summary.contractValue)],
      ...summary.contractPaymentBreakdown.map(category => [
        CONTRACT_PAYMENT_CATEGORY_LABELS[category.type] || category.label,
        money(category.amount)
      ]),
      ["Total Payments Counted Against Contract", money(summary.contractPayments)],
      ["Remaining Contract Value", money(summary.balanceContractValue)],
      ["Contract Paid", `${summary.paidPercentage.toFixed(2)}%`],
      ["Contract Remaining", `${summary.remainingPercentage.toFixed(2)}%`]
    ]
  });

  y = doc.lastAutoTable.finalY + 8;
  doc.setFont("helvetica", "bold");
  doc.setFontSize(11);
  doc.text("Labour Account", 20, y);
  doc.autoTable({
    ...tableOptions,
    startY: y + 5,
    body: [
      ["Total Labour Earned", money(summary.labourEarned)],
      ["Total Advances Issued (Lifetime)", money(summary.labourAdvances)],
      ["Less: Total Advance Recovered", money(summary.advanceDeductions)],
      ["Current Advance Balance to Recover", money(summary.advanceBalance)]
    ]
  });

  return doc.lastAutoTable.finalY + 8;
}

function renderPaymentHistory(doc, transactions, tradeName, startY) {
  const orderedTransactions = sortPaymentTransactions(transactions);
  const chronologicalTransactions = [...orderedTransactions].reverse();
  const body = orderedTransactions.length
    ? orderedTransactions.map(transaction => {
      let pendingLabourPayment = "-";
      if (transaction.type === "labour_settlement") {
        const outstandingBalance = transaction.id
          ? calculateOutstandingBalanceAfterTransaction(transactions, transaction.id)
          : calculateOutstandingLabourBalance(
            chronologicalTransactions.slice(
              0,
              chronologicalTransactions.indexOf(transaction) + 1
            )
          );
        pendingLabourPayment = money(outstandingBalance);
      }

      const tradeAndCategory = [
        tradeName,
        transaction.workerCategoryName
      ].filter(value => String(value || "").trim()).join("\n");
      const paymentModeAndReference = [
        transaction.paymentMode,
        transaction.remarks
      ].filter(value => String(value || "").trim()).join("\n");

      return [
        `${formatDate(transaction.date)}\n${paymentTypeLabel(transaction.type)}`,
        tradeAndCategory,
        transaction.type === "labour_settlement" ? money(transaction.labourEarned) : "-",
        transaction.type === "labour_settlement" ? money(transaction.advanceAdjustment) : "-",
        money(transaction.amount),
        pendingLabourPayment,
        paymentModeAndReference
      ];
    })
    : [["-", "No payment transactions recorded.", "", "", "", "", ""]];

  doc.setFont("helvetica", "bold");
  doc.setFontSize(10);
  doc.text("Payment History", 20, startY);
  doc.autoTable({
    startY: startY + 5,
    head: [[
      "Date / Type",
      "Trade / Worker Category",
      "Labour Earned",
      "Advance Deduction",
      "Amount Paid",
      "Pending Labour Payment",
      "Payment Mode / Reference"
    ]],
    body,
    theme: "grid",
    margin: { left: 12, right: 12 },
    headStyles: { fillColor: [242, 242, 242], textColor: [51, 51, 51], fontStyle: "bold", fontSize: 7 },
    styles: { fontSize: 7, textColor: [51, 51, 51], lineColor: [184, 184, 184], lineWidth: 0.1, overflow: "linebreak", cellPadding: 1.2 },
    columnStyles: {
      0: { cellWidth: 25 },
      1: { cellWidth: 34 },
      2: { cellWidth: 23, halign: "right" },
      3: { cellWidth: 23, halign: "right" },
      4: { cellWidth: 23, halign: "right" },
      5: { cellWidth: 26, halign: "right" },
      6: { cellWidth: 31 }
    },
    rowPageBreak: "avoid",
    showHead: "everyPage"
  });
  return doc.lastAutoTable.finalY + 8;
}

export async function downloadLedgerPDF(trade) {
  const jsPDFConstructor = window.jspdf?.jsPDF || window.jsPDF;
  if (!jsPDFConstructor) throw new Error("jsPDF library not loaded. Please refresh the page.");

  const doc = new jsPDFConstructor({ unit: "mm", format: "a4" });
  const pageWidth = doc.internal.pageSize.getWidth();
  const workers = workerCategories(trade);
  const contracts = Array.isArray(trade?.contractItems) ? trade.contractItems : [];
  const transactions = uniquePaymentTransactions(trade?.paymentTransactions);
  const summary = paymentLedgerSummary(trade);
  let y = 20;

  doc.setFillColor(13, 79, 145);
  doc.rect(0, 0, pageWidth, 35, "F");
  doc.setTextColor(255, 255, 255);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(18);
  doc.text("MANNAT MOON CONSTRUCTION", pageWidth / 2, 15, { align: "center" });
  doc.setFont("helvetica", "normal");
  doc.setFontSize(12);
  doc.text("Trade Ledger", pageWidth / 2, 28, { align: "center" });
  doc.setTextColor(51, 51, 51);

  doc.setFont("helvetica", "bold");
  doc.setFontSize(11);
  doc.text(`Trade: ${String(trade?.name || "")}`, 20, 45);
  doc.setFont("helvetica", "normal");
  doc.setTextColor(102, 102, 102);
  doc.text(`Generated: ${formatDateWithDay(new Date().toISOString().slice(0, 10))}`, 20, 53);
  doc.setTextColor(51, 51, 51);
  y = 65;

  y = renderAccountSummaries(doc, summary, y);

  if (workers.length) {
    doc.setFont("helvetica", "bold");
    doc.setFontSize(10);
    doc.text("Worker Categories", 20, y);
    y += 5;
    doc.autoTable({
      startY: y,
      head: [["Category", "Payment Type", "Amount", "Effective From", "Agreed Paid", "Remaining", "Work Description"]],
      body: workers.map(worker => {
        const isLumpSum = worker.payment_type === "lump_sum";
        const agreement = summary.lumpSumAgreements.find(item => item.name === worker.name);
        return [
          String(worker.name || ""),
          isLumpSum ? "Lump Sum" : "Daily Wage",
          isLumpSum ? money(worker.lump_sum_amount) : `${money(worker.currentWage)} / day`,
          formatDateWithDay(worker.effective_from || worker.effectiveFrom),
          isLumpSum ? money(agreement?.totalPaid) : "-",
          isLumpSum ? money(agreement?.remainingBalance) : "-",
          String(worker.work_description || "")
        ];
      }),
      theme: "grid",
      headStyles: { fillColor: [242, 242, 242], textColor: [51, 51, 51], fontStyle: "bold", fontSize: 9 },
      styles: { fontSize: 7, textColor: [51, 51, 51], lineColor: [184, 184, 184], lineWidth: 0.1, overflow: "linebreak" }
    });
    y = doc.lastAutoTable.finalY + 10;
  }

  if (contracts.length) {
    doc.setFont("helvetica", "bold");
    doc.setFontSize(10);
    doc.text("Work Contracts / Capping", 20, y);
    y += 5;
    doc.autoTable({
      startY: y,
      head: [["Contract Item", "Amount"]],
      body: contracts.map(item => [String(item.name || ""), money(item.amount)]),
      theme: "grid",
      headStyles: { fillColor: [242, 242, 242], textColor: [51, 51, 51], fontStyle: "bold", fontSize: 9 },
      styles: { fontSize: 8, textColor: [51, 51, 51], lineColor: [184, 184, 184], lineWidth: 0.1 },
      columnStyles: { 1: { halign: "right" } }
    });
    y = doc.lastAutoTable.finalY + 10;
  }

  renderPaymentHistory(doc, transactions, String(trade?.name || ""), y);

  const totalPages = doc.internal.getNumberOfPages();
  for (let pageNumber = 1; pageNumber <= totalPages; pageNumber += 1) {
    doc.setPage(pageNumber);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(8);
    doc.setTextColor(102, 102, 102);
    doc.text(`Page ${pageNumber} of ${totalPages}`, pageWidth - 20, 287, { align: "right" });
  }

  const fileName = `${String(trade?.name || "trade").replace(/[^a-zA-Z0-9]/g, "_")}_trade_ledger.pdf`;
  doc.save(fileName);
}

export async function downloadPaymentLedgerPDF(trade) {
  const jsPDFConstructor = window.jspdf?.jsPDF || window.jsPDF;
  if (!jsPDFConstructor) throw new Error("jsPDF library not loaded. Please refresh the page.");

  const doc = new jsPDFConstructor({ unit: "mm", format: "a4" });
  const pageWidth = doc.internal.pageSize.getWidth();
  const transactions = uniquePaymentTransactions(trade?.paymentTransactions);
  const summary = paymentLedgerSummary(trade);
  let y = 20;

  doc.setFillColor(13, 79, 145);
  doc.rect(0, 0, pageWidth, 35, "F");
  doc.setTextColor(255, 255, 255);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(18);
  doc.text("MANNAT MOON CONSTRUCTION", pageWidth / 2, 15, { align: "center" });
  doc.setFont("helvetica", "normal");
  doc.setFontSize(12);
  doc.text("Labour Payment Ledger", pageWidth / 2, 28, { align: "center" });
  doc.setTextColor(51, 51, 51);

  doc.setFont("helvetica", "bold");
  doc.setFontSize(11);
  doc.text(`Trade: ${String(trade?.name || "")}`, 20, 45);
  doc.setFont("helvetica", "normal");
  doc.setTextColor(102, 102, 102);
  doc.text(`Generated: ${formatDateWithDay(new Date().toISOString().slice(0, 10))}`, 20, 53);
  doc.setTextColor(51, 51, 51);
  y = 66;

  y = renderAccountSummaries(doc, summary, y);
  renderPaymentHistory(doc, transactions, String(trade?.name || ""), y);

  const totalPages = doc.internal.getNumberOfPages();
  for (let pageNumber = 1; pageNumber <= totalPages; pageNumber += 1) {
    doc.setPage(pageNumber);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(8);
    doc.setTextColor(102, 102, 102);
    doc.text(`Page ${pageNumber} of ${totalPages}`, pageWidth - 20, 287, { align: "right" });
  }

  const fileName = `${String(trade?.name || "trade").replace(/[^a-zA-Z0-9]/g, "_")}_payment_ledger.pdf`;
  doc.save(fileName);
}

export { workerCategories, totalPayments, contractTotal };
