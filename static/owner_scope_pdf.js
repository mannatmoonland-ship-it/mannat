import { formatDateWithDay } from "./date_format.js";
import { ownerScopePaymentDateValue } from "./owner_scope_sort.mjs";

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

const money = new Intl.NumberFormat("en-IN", {
  maximumFractionDigits: 2
});

export function createOwnerScopeReport(data) {
  const items = Array.isArray(data?.items) ? data.items : [];
  const payments = Array.isArray(data?.payments) ? data.payments : [];
  const paymentsByItemId = new Map();
  for (const payment of payments) {
    if (!payment?.expenseItemId) continue;
    const itemPayments = paymentsByItemId.get(payment.expenseItemId) || [];
    itemPayments.push(payment);
    paymentsByItemId.set(payment.expenseItemId, itemPayments);
  }
  const rows = [];

  for (const item of items) {
    const itemPayments = paymentsByItemId.get(item.id) || [];
    if (!itemPayments.length) {
      rows.push({
        itemName: String(item.name || "—"),
        date: "—",
        dateSortValue: null,
        category: categoryLabels[item.category] || String(item.category || "—"),
        amount: 0,
        paymentMode: "—"
      });
      continue;
    }

    for (const payment of itemPayments) {
      const amount = Number(payment.amount ?? 0);
      if (!Number.isFinite(amount)) {
        throw new Error("Owner Scope contains an invalid payment amount.");
      }
      rows.push({
        itemName: String(item.name || "—"),
        date: formatDateWithDay(payment.date, "—").replace(", ", ",\n"),
        dateSortValue: ownerScopePaymentDateValue(payment.date),
        category: categoryLabels[item.category] || String(item.category || "—"),
        amount,
        paymentMode: String(payment.paymentMode || "—")
      });
    }
  }

  rows.sort((left, right) => {
    if (left.dateSortValue === null) return right.dateSortValue === null ? 0 : 1;
    if (right.dateSortValue === null) return -1;
    return right.dateSortValue - left.dateSortValue;
  });

  const totalPaid = rows.reduce((total, row) => total + row.amount, 0);
  return {
    itemCount: items.length,
    totalPaid,
    rows: rows.map((row, index) => ({
      serialNumber: index + 1,
      itemName: row.itemName,
      date: row.date,
      category: row.category,
      amount: row.amount,
      paymentMode: row.paymentMode
    }))
  };
}

function createRupeeGlyph(fontWeight) {
  const canvas = document.createElement("canvas");
  canvas.width = 80;
  canvas.height = 100;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("The browser could not prepare the PDF currency symbol.");
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = "#333333";
  context.font = `${fontWeight} 72px Arial`;
  context.textBaseline = "alphabetic";
  context.fillText("₹", 4, 79);
  return canvas.toDataURL("image/png");
}

export function downloadOwnerScopePDF(data) {
  const jsPDFConstructor = window.jspdf?.jsPDF || window.jsPDF;
  if (!jsPDFConstructor) throw new Error("jsPDF library not loaded. Please refresh the page.");

  const report = createOwnerScopeReport(data);
  const doc = new jsPDFConstructor({
    orientation: "landscape",
    unit: "mm",
    format: "a4"
  });
  if (typeof doc.autoTable !== "function") {
    throw new Error("jsPDF AutoTable library not loaded. Please refresh the page.");
  }

  const pageWidth = doc.internal.pageSize.getWidth();
  const pageHeight = doc.internal.pageSize.getHeight();
  const margin = 12;
  const regularRupeeGlyph = createRupeeGlyph("normal");
  const boldRupeeGlyph = createRupeeGlyph("bold");

  doc.setFillColor(13, 79, 145);
  doc.rect(0, 0, pageWidth, 32, "F");
  doc.setTextColor(255, 255, 255);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(17);
  doc.text("MANNAT MOON CONSTRUCTION", pageWidth / 2, 13, { align: "center" });
  doc.setFontSize(12);
  doc.text("OWNER SCOPE EXPENSE REPORT", pageWidth / 2, 24, { align: "center" });

  doc.setTextColor(51, 51, 51);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(10);
  doc.text(`Scope Items: ${report.itemCount}`, margin, 42);
  const paidLabel = "Total Paid:";
  const paidLabelX = margin + 70;
  doc.text(paidLabel, paidLabelX, 42);
  const paidGlyphX = paidLabelX + doc.getTextWidth(paidLabel) + 1;
  doc.addImage(boldRupeeGlyph, "PNG", paidGlyphX, 38.8, 2.8, 3.3);
  doc.text(money.format(report.totalPaid), paidGlyphX + 3.8, 42);

  doc.autoTable({
    startY: 49,
    margin: { left: margin, right: margin, top: margin, bottom: 18 },
    head: [["S.No.", "Item", "Date", "Category", "Amount Paid", "Payment Mode"]],
    body: report.rows.map(row => [
      String(row.serialNumber),
      row.itemName,
      row.date,
      row.category,
      money.format(row.amount),
      row.paymentMode
    ]),
    theme: "grid",
    tableWidth: pageWidth - margin * 2,
    headStyles: {
      fillColor: [24, 59, 42],
      textColor: [255, 255, 255],
      fontStyle: "bold",
      fontSize: 9
    },
    styles: {
      font: "helvetica",
      fontSize: 9,
      cellPadding: 2.5,
      textColor: [51, 51, 51],
      lineColor: [184, 184, 184],
      lineWidth: 0.1,
      overflow: "linebreak",
      valign: "top"
    },
    columnStyles: {
      0: { cellWidth: 14, halign: "center" },
      1: { cellWidth: 84 },
      2: { cellWidth: 42 },
      3: { cellWidth: 43 },
      4: { cellWidth: 42, halign: "right" },
      5: { cellWidth: 48 }
    },
    didDrawCell: ({ column, cell, section }) => {
      if (section !== "body" || column.index !== 4) return;
      const amountWidth = doc.getTextWidth(String(cell.text[0] || ""));
      const glyphX = cell.x + cell.width - cell.padding("right") - amountWidth - 3.8;
      const glyphY = cell.y + (cell.height - 3.3) / 2;
      doc.addImage(regularRupeeGlyph, "PNG", glyphX, glyphY, 2.8, 3.3);
    },
    rowPageBreak: "avoid",
    showHead: "everyPage"
  });

  let totalY = doc.lastAutoTable.finalY + 9;
  if (totalY > pageHeight - 18) {
    doc.addPage();
    totalY = margin + 5;
  }
  doc.setFont("helvetica", "bold");
  doc.setFontSize(11);
  doc.setTextColor(51, 51, 51);
  const totalLabel = "TOTAL OWNER SCOPE PAID:";
  const totalAmount = money.format(report.totalPaid);
  const totalAmountWidth = doc.getTextWidth(totalAmount);
  const totalLabelWidth = doc.getTextWidth(totalLabel);
  const totalGlyphX = pageWidth - margin - totalAmountWidth - 3.8;
  const totalLabelX = totalGlyphX - totalLabelWidth - 1;
  doc.text(totalLabel, totalLabelX, totalY);
  doc.addImage(boldRupeeGlyph, "PNG", totalGlyphX, totalY - 3.2, 2.8, 3.3);
  doc.text(totalAmount, totalGlyphX + 3.8, totalY);

  const totalPages = doc.internal.getNumberOfPages();
  for (let pageNumber = 1; pageNumber <= totalPages; pageNumber += 1) {
    doc.setPage(pageNumber);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(8);
    doc.setTextColor(102, 102, 102);
    doc.text(`Page ${pageNumber} of ${totalPages}`, pageWidth - margin, pageHeight - 5, {
      align: "right"
    });
  }

  doc.save("owner_scope_expense_report.pdf");
}
