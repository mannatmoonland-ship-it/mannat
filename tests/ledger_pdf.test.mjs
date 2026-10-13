import assert from "node:assert/strict";
import test from "node:test";

import {
  downloadLedgerPDF,
  downloadPaymentLedgerPDF
} from "../static/ledger_pdf.js";

class FakePDF {
  static instances = [];

  constructor(options) {
    this.options = options;
    this.tables = [];
    this.textEntries = [];
    this.pageCount = 1;
    this.lastAutoTable = { finalY: 0 };
    this.internal = {
      pageSize: {
        getWidth: () => 210,
        getHeight: () => 297
      },
      getNumberOfPages: () => this.pageCount
    };
    FakePDF.instances.push(this);
  }

  setFillColor() {}
  rect() {}
  setTextColor() {}
  setFont() {}
  setFontSize() {}
  setPage() {}
  addPage() { this.pageCount += 1; }
  splitTextToSize(text) { return [text]; }
  text(value) { this.textEntries.push(String(value)); }
  save() {}

  autoTable(options) {
    this.tables.push(options);
    this.lastAutoTable = { finalY: (options.startY || 0) + 20 };
  }
}

const tradeFixture = {
  name: "Masonry",
  contractItems: [{ id: "contract-1", name: "Wall", amount: 100000 }],
  paymentTransactions: [
    {
      id: "mobilization-1",
      type: "mobilization_advance",
      date: "2026-10-01",
      amount: 9000,
      paymentMode: "Bank Transfer"
    },
    {
      id: "advance-1",
      type: "labour_advance",
      date: "2026-10-02",
      amount: 3000,
      paymentMode: "Cash"
    },
    {
      id: "settlement-1",
      type: "labour_settlement",
      date: "2026-10-03",
      workerCategoryName: "Masons",
      labourEarned: 15000,
      advanceAdjustment: 2000,
      amount: 13000,
      outstandingBalance: 0,
      paymentMode: "UPI",
      remarks: "First settlement"
    },
    {
      id: "settlement-2",
      type: "labour_settlement",
      date: "2026-10-04",
      workerCategoryName: "Masons",
      labourEarned: 4000,
      advanceAdjustment: 1000,
      amount: 2000,
      paymentMode: "Cash",
      remarks: "Second settlement"
    },
    {
      id: "settlement-2",
      type: "labour_settlement",
      date: "2026-10-04",
      workerCategoryName: "Masons",
      labourEarned: 4000,
      advanceAdjustment: 1000,
      amount: 9999,
      paymentMode: "Cash",
      remarks: "Duplicate must be ignored"
    }
  ]
};

for (const [name, exportPdf] of [
  ["trade ledger PDF", downloadLedgerPDF],
  ["labour payment PDF", downloadPaymentLedgerPDF]
]) {
  test(`${name} includes settlement breakdown only in Payment History`, async () => {
    globalThis.window = { jspdf: { jsPDF: FakePDF } };
    FakePDF.instances = [];

    try {
      await exportPdf(tradeFixture);
    } finally {
      delete globalThis.window;
    }

    const pdf = FakePDF.instances[0];
    assert.ok(pdf);
    assert.equal(pdf.options.format, "a4");
    assert.ok(pdf.textEntries.some(text => text === "Contract Account"));
    assert.ok(pdf.textEntries.some(text => text === "Labour Account"));
    assert.ok(pdf.textEntries.includes("Payment History"));
    assert.equal(pdf.textEntries.includes("Labour Settlement Details"), false);

    const history = pdf.tables.find(table =>
      table.head?.[0]?.includes("Labour Earned")
    );
    assert.ok(history, "Payment History should contain settlement financial columns");
    assert.deepEqual(history.head[0], [
      "Date / Type",
      "Trade / Worker Category",
      "Labour Earned",
      "Advance Deduction",
      "Amount Paid",
      "Pending Labour Payment",
      "Payment Mode / Reference"
    ]);
    assert.ok(
      history.columnStyles &&
      Object.values(history.columnStyles).reduce((width, column) => width + column.cellWidth, 0)
        <= 210 - history.margin.left - history.margin.right,
      "Payment History columns should fit within the A4 printable width"
    );

    const firstSettlement = history.body.find(row => row[0].includes("Labour Settlement") && row[6].includes("First settlement"));
    const secondSettlement = history.body.find(row => row[6].includes("Second settlement"));
    assert.deepEqual(firstSettlement, [
      "03/10/2026\nLabour Settlement",
      "Masonry\nMasons",
      "INR 15,000",
      "INR 2,000",
      "INR 13,000",
      "INR 0",
      "UPI\nFirst settlement"
    ]);
    assert.deepEqual(secondSettlement, [
      "04/10/2026\nLabour Settlement",
      "Masonry\nMasons",
      "INR 4,000",
      "INR 1,000",
      "INR 2,000",
      "INR 1,000",
      "Cash\nSecond settlement"
    ]);
    assert.equal(history.body.filter(row => row[6].includes("Second settlement")).length, 1);
    assert.equal(history.body.some(row => row[4] === "INR 9,999"), false);

    const labourAccount = pdf.tables.find(table =>
      table.body?.some(row => row[0] === "Current Advance Balance to Recover")
    );
    assert.ok(labourAccount);
    assert.deepEqual(labourAccount.body, [
      ["Total Labour Earned", "INR 19,000"],
      ["Total Advances Issued (Lifetime)", "INR 3,000"],
      ["Less: Total Advance Recovered", "INR 3,000"],
      ["Current Advance Balance to Recover", "INR 0"]
    ]);

    const contractAccount = pdf.tables.find(table =>
      table.body?.some(row => row[0] === "Total Payments Counted Against Contract")
    );
    assert.ok(contractAccount);
    assert.equal(
      contractAccount.body.find(row => row[0] === "Total Payments Counted Against Contract")[1],
      "INR 27,000"
    );
  });
}
