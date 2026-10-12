import assert from "node:assert/strict";
import test from "node:test";

import {
  createOwnerScopeReport,
  downloadOwnerScopePDF
} from "../static/owner_scope_pdf.js";

function makePdfConstructor() {
  const state = {
    options: null,
    text: [],
    table: null,
    fileName: null,
    images: [],
    pages: 1
  };

  class MockPdf {
    constructor(options) {
      state.options = options;
      this.internal = {
        pageSize: {
          getWidth: () => 297,
          getHeight: () => 210
        },
        getNumberOfPages: () => state.pages
      };
    }

    setFillColor() {}
    rect() {}
    setTextColor() {}
    setFont() {}
    setFontSize() {}
    setPage() {}
    addPage() {
      state.pages += 1;
    }
    text(value) {
      state.text.push(String(value));
    }
    getTextWidth(value) {
      return String(value).length * 2;
    }
    addImage(...image) {
      state.images.push(image);
    }
    autoTable(options) {
      state.table = options;
      this.lastAutoTable = { finalY: 120 };
      for (const _row of options.body) {
        options.didDrawCell({
          column: { index: 4 },
          cell: { text: ["1,234"], x: 1, y: 1, width: 20, height: 10, padding: () => 2 },
          section: "body"
        });
      }
    }
    save(fileName) {
      state.fileName = fileName;
    }
  }

  MockPdf.state = state;
  return MockPdf;
}

function setupPdfGlobals(MockPdf) {
  const glyphs = [];
  globalThis.window = { jspdf: { jsPDF: MockPdf } };
  globalThis.document = {
    createElement: () => ({
      getContext: () => ({
        clearRect() {},
        fillText: value => glyphs.push(value)
      }),
      toDataURL: () => "data:image/png;base64,mock"
    })
  };
  return glyphs;
}

function clearPdfGlobals() {
  delete globalThis.window;
  delete globalThis.document;
}

function sampleData() {
  return {
    metadata: {
      name: "Owner Scope",
      legacyOwnerAccount: { name: "OWNERS ACCOUNT" }
    },
    items: [
      { id: "older", name: "Older expense", category: "material" },
      {
        id: "newer",
        name: "An unusually long Owner Scope item name ".repeat(8),
        category: "professional_technical"
      },
      { id: "unpaid", name: "Unpaid scope item", category: "other" }
    ],
    payments: [
      { expenseItemId: "older", date: "2026-10-01", amount: 1250, paymentMode: "Cash" },
      { expenseItemId: "newer", date: "2026-10-06", amount: 5000, paymentMode: "UPI" },
      { expenseItemId: "newer", date: "2026-10-06", amount: 250, paymentMode: "Bank Transfer" },
      { expenseItemId: "older", date: "2026-09-29", amount: 750, paymentMode: "Cheque" },
      { expenseItemId: "not-a-current-item", date: "2026-10-07", amount: 99999, paymentMode: "Cash" }
    ],
    legacyPayments: [{ amount: 123456 }]
  };
}

test("report uses all live Owner Scope items and only their linked payment records", () => {
  const report = createOwnerScopeReport(sampleData());

  assert.equal(report.itemCount, 3);
  assert.deepEqual(
    report.rows.map(row => row.itemName),
    [
      "An unusually long Owner Scope item name ".repeat(8),
      "An unusually long Owner Scope item name ".repeat(8),
      "Older expense",
      "Older expense",
      "Unpaid scope item"
    ]
  );
  assert.deepEqual(report.rows.map(row => row.amount), [5000, 250, 1250, 750, 0]);
});

test("report sorts by actual payment date newest first, stably for ties, and puts missing dates last", () => {
  const report = createOwnerScopeReport(sampleData());

  assert.deepEqual(
    report.rows.slice(0, 2).map(row => [row.date, row.paymentMode]),
    [
      ["Tuesday,\n06/10/2026", "UPI"],
      ["Tuesday,\n06/10/2026", "Bank Transfer"]
    ]
  );
  assert.equal(report.rows.at(-1).date, "—");
});

test("report total equals each linked Owner Scope payment exactly once", () => {
  const report = createOwnerScopeReport(sampleData());

  assert.equal(report.totalPaid, 7250);
  assert.equal(report.rows.reduce((total, row) => total + row.amount, 0), report.totalPaid);
});

test("PDF wraps full long item names within fixed-width landscape columns", () => {
  const MockPdf = makePdfConstructor();
  const glyphs = setupPdfGlobals(MockPdf);
  downloadOwnerScopePDF(sampleData());

  const [serialNumber, itemName] = MockPdf.state.table.body[0];
  assert.equal(serialNumber, "1");
  assert.equal(itemName, "An unusually long Owner Scope item name ".repeat(8));
  assert.equal(MockPdf.state.options.orientation, "landscape");
  assert.equal(MockPdf.state.options.format, "a4");
  assert.equal(MockPdf.state.table.styles.overflow, "linebreak");
  assert.equal(MockPdf.state.table.styles.fontSize, 9);
  assert.equal(MockPdf.state.table.columnStyles[1].cellWidth, 84);
  assert.equal(MockPdf.state.table.showHead, "everyPage");
  assert.equal(MockPdf.state.fileName, "owner_scope_expense_report.pdf");
  assert.equal(glyphs.filter(glyph => glyph === "₹").length, 2);
  assert.ok(MockPdf.state.images.length >= 3);
  clearPdfGlobals();
});

test("PDF contains no separate legacy account or budget and balance summary fields", () => {
  const MockPdf = makePdfConstructor();
  setupPdfGlobals(MockPdf);
  downloadOwnerScopePDF(sampleData());

  const visiblePdfText = [
    ...MockPdf.state.text,
    ...MockPdf.state.table.head.flat(),
    ...MockPdf.state.table.body.flat()
  ].join(" ");
  for (const excluded of ["OWNERS ACCOUNT", "Legacy", "Budget", "Capping", "Balance", "Remaining", "Contract Value", "History"]) {
    assert.equal(visiblePdfText.includes(excluded), false, `unexpected ${excluded} in PDF output`);
  }
  assert.ok(visiblePdfText.includes("Total Paid:"));
  assert.ok(visiblePdfText.includes("TOTAL OWNER SCOPE PAID:"));
  assert.ok(visiblePdfText.includes("7,250"));
  assert.ok(MockPdf.state.images.length >= 3);
  clearPdfGlobals();
});

test("PDF download does not mutate Owner Scope data or perform Firestore writes", () => {
  const MockPdf = makePdfConstructor();
  setupPdfGlobals(MockPdf);
  const data = sampleData();
  const original = structuredClone(data);
  const writes = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (...args) => {
    writes.push(args);
    throw new Error("PDF generation must not make network writes.");
  };
  try {
    downloadOwnerScopePDF(data);
    assert.deepEqual(data, original);
    assert.deepEqual(writes, []);
  } finally {
    globalThis.fetch = originalFetch;
    clearPdfGlobals();
  }
});
