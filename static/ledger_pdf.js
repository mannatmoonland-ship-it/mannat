import { calculateAdvanceBalance, calculateOutstandingLabourBalance } from "./settlement_logic.mjs";
import { sortPaymentTransactions } from "./payment_sort.mjs";
import {
  totalPaymentAmount,
  uniquePaymentTransactions
} from "./trade_payment_history.mjs";
import { calculateContractPaymentSummary } from "./contract_payment_summary.mjs";
import { formatDate, formatDateWithDay } from "./date_format.js";

const COMPLETED_WORK_PAYMENT_TYPE = "completed_work_payment";

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

function paymentLedgerSummary(trade) {
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
    contractPayments,
    balanceContractValue: contractSummary.balanceContractValue,
    paidPercentage: contractSummary.paidPercentage,
    remainingPercentage: contractSummary.remainingPercentage,
    labourSettlements,
    labourEarned,
    labourAdvances,
    advanceDeductions,
    lumpSumPayments,
    lumpSumAgreements,
    advanceBalance: calculateAdvanceBalance(validTransactions),
    outstandingBalance: calculateOutstandingLabourBalance(validTransactions)
  };
}

export async function downloadLedgerPDF(trade) {
  const jsPDFConstructor = window.jspdf?.jsPDF || window.jsPDF;
  if (!jsPDFConstructor) throw new Error("jsPDF library not loaded. Please refresh the page.");

  const doc = new jsPDFConstructor({ unit: "mm", format: "a4" });
  const pageWidth = doc.internal.pageSize.getWidth();
  const workers = workerCategories(trade);
  const contracts = Array.isArray(trade?.contractItems) ? trade.contractItems : [];
  const transactions = uniquePaymentTransactions(trade?.paymentTransactions);
  const lumpSumSummary = paymentLedgerSummary(trade);
  const contractSummary = calculateContractPaymentSummary(contracts, transactions);
  const { contractTotal: contractValue, contractPayments: paid, balanceContractValue, paidPercentage } = contractSummary;
  const advanceBalance = calculateAdvanceBalance(transactions);
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
        const agreement = lumpSumSummary.lumpSumAgreements.find(item => item.name === worker.name);
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
    y = doc.lastAutoTable.finalY + 5;
    doc.setFont("helvetica", "bold");
    doc.text(`Total Contract Value: ${money(contractValue)}`, 20, y);
    y += 12;
  }

  doc.setFont("helvetica", "bold");
  doc.setFontSize(10);
  doc.text("Payment Summary", 20, y);
  y += 6;
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.text(`Payments Against Contract: ${money(paid)}`, 20, y);
  doc.text(`Total Paid: ${money(totalPaymentAmount(transactions))}`, 20, y + 5);
  doc.text(`Balance Contract Value: ${money(balanceContractValue)}`, 20, y + 10);
  doc.text(`Payment Progress: ${paidPercentage.toFixed(2)}%`, 20, y + 15);
  doc.text(`Labour Advance Balance: ${money(advanceBalance)}`, 20, y + 20);
  doc.text(`Lump-Sum Payments: ${money(lumpSumSummary.lumpSumPayments)}`, 20, y + 25);
  y += 37;

  doc.setFont("helvetica", "bold");
  doc.setFontSize(10);
  doc.text("Payment History", 20, y);
  y += 5;
  doc.autoTable({
    startY: y,
    head: [["Date", "Payment Type", "Worker Category", "Amount", "Payment Mode", "Remarks"]],
    body: transactions.length
      ? sortPaymentTransactions(transactions).map(transaction => [
        formatDate(transaction.date),
        paymentTypeLabel(transaction.type),
        String(transaction.workerCategoryName || ""),
        money(transaction.amount),
        String(transaction.paymentMode || ""),
        String(transaction.remarks || "")
      ])
      : [["-", "No payment transactions recorded.", "", "", "", ""]],
    theme: "grid",
    headStyles: { fillColor: [242, 242, 242], textColor: [51, 51, 51], fontStyle: "bold", fontSize: 8 },
    styles: { fontSize: 8, textColor: [51, 51, 51], lineColor: [184, 184, 184], lineWidth: 0.1 },
    columnStyles: { 3: { halign: "right" } }
  });

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
  const summary = paymentLedgerSummary(trade);
  const transactions = uniquePaymentTransactions(trade?.paymentTransactions);
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

  doc.setFont("helvetica", "bold");
  doc.setFontSize(11);
  doc.text("Payment Summary", 20, y);
  y += 5;
  doc.autoTable({
    startY: y,
    head: [["Summary", "Amount"]],
    body: [
      ["Total Contract Value", money(summary.contractValue)],
      ["Total Paid", money(totalPaymentAmount(transactions))],
      ["Total Payments Against Contract", money(summary.contractPayments)],
      ["Balance Contract Value", money(summary.balanceContractValue)],
      ["Payment Progress", `${summary.paidPercentage.toFixed(2)}%`],
      ["Remaining Percentage", `${summary.remainingPercentage.toFixed(2)}%`],
      ["Labour Advance Balance", money(summary.advanceBalance)],
      ["Outstanding Balance", money(summary.outstandingBalance)],
      ["Labour Settlements", money(summary.labourSettlements)],
      ["Daily Wage Payments", money(summary.labourSettlements)],
      ["Total Labour Earned", money(summary.labourEarned)],
      ["Labour Advances Received", money(summary.labourAdvances)],
      ["Advance Deductions", money(summary.advanceDeductions)],
      ["Lump-Sum Payments", money(summary.lumpSumPayments)],
      ...summary.lumpSumAgreements.map(agreement => [
        `${agreement.name} — Agreed / Paid / Remaining`,
        `${money(agreement.agreedAmount)} / ${money(agreement.totalPaid)} / ${money(agreement.remainingBalance)}`
      ])
    ],
    theme: "grid",
    headStyles: { fillColor: [242, 242, 242], textColor: [51, 51, 51], fontStyle: "bold", fontSize: 9 },
    styles: { fontSize: 9, textColor: [51, 51, 51], lineColor: [184, 184, 184], lineWidth: 0.1 },
    columnStyles: { 1: { halign: "right" } },
    rowPageBreak: "avoid",
    showHead: "everyPage"
  });
  y = doc.lastAutoTable.finalY + 12;

  doc.setFont("helvetica", "bold");
  doc.setFontSize(11);
  doc.text("Payment History", 20, y);
  y += 5;
  doc.autoTable({
    startY: y,
    head: [["Date", "Payment Type", "Worker Category", "Amount", "Payment Mode", "Remarks"]],
    body: transactions.length
      ? sortPaymentTransactions(transactions).map(transaction => [
        formatDate(transaction.date),
        paymentTypeLabel(transaction.type),
        String(transaction.workerCategoryName || ""),
        money(transaction.amount),
        String(transaction.paymentMode || ""),
        String(transaction.remarks || "")
      ])
      : [["-", "No payment transactions recorded.", "", "", "", ""]],
    theme: "grid",
    headStyles: { fillColor: [242, 242, 242], textColor: [51, 51, 51], fontStyle: "bold", fontSize: 8 },
    styles: { fontSize: 8, textColor: [51, 51, 51], lineColor: [184, 184, 184], lineWidth: 0.1, overflow: "linebreak" },
    columnStyles: { 0: { cellWidth: 23 }, 1: { cellWidth: 35 }, 2: { cellWidth: 30 }, 3: { cellWidth: 23, halign: "right" }, 4: { cellWidth: 25 }, 5: { cellWidth: "auto" } },
    rowPageBreak: "avoid",
    showHead: "everyPage"
  });

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
