import assert from "node:assert/strict";
import test from "node:test";
import { calculateBuildingCosts, formatRupees } from "../static/building_cost_summary_logic.mjs";
import { LEGACY_OWNER_ACCOUNT_DOCUMENT_ID } from "../static/owner_scope_constants.mjs";

test("summary currency uses Indian grouping with no decimal places", () => {
  assert.equal(formatRupees(131750), "₹1,31,750");
  assert.equal(formatRupees(56000), "₹56,000");
  assert.equal(formatRupees(243850), "₹2,43,850");
});

test("building costs include purchase amounts and every actual payment type once", () => {
  const result = calculateBuildingCosts({
    categories: [{ id: "cement", name: "Cement" }, { id: "sand", name: "Sand" }, { id: "zero", name: "Unused" }],
    purchases: [
      { id: "p1", categoryId: "cement", totalAmount: 1200, date: "2026-09-01" },
      { id: "p2", categoryId: "sand", totalAmount: "800", date: "2026-09-02" },
      { id: "p2", categoryId: "sand", totalAmount: 800, date: "2026-09-02" },
      { id: "bad", categoryId: "sand", totalAmount: "invalid", date: "2026-09-02" },
      { id: "negative", categoryId: "sand", totalAmount: -10, date: "2026-09-02" }
    ],
    trades: [
      {
        id: "masonry",
        name: "Masonry",
        contractItems: [{ amount: 100000 }],
        paymentTransactions: [
          { id: "a1", type: "labour_advance", amount: 100, date: "2026-09-01" },
          { id: "s1", type: "labour_settlement", amount: 250, labourEarned: 1000, advanceAdjustment: 50, date: "2026-09-02" },
          { id: "c1", type: "completed_work_payment", amount: 300, date: "2026-09-03" },
          { id: "m1", type: "mobilization_advance", amount: 400, date: "2026-09-04" },
          { id: "l1", type: "lump_sum_payment", amount: 500, date: "2026-09-05" },
          { id: "l1", type: "lump_sum_payment", amount: 500, date: "2026-09-05" },
          { id: "unknown", type: "contract_value", amount: 90000, date: "2026-09-05" },
          { id: "invalid", type: "labour_advance", amount: "NaN", date: "2026-09-05" }
        ]
      },
      { id: "plumbing", name: "Plumbing", paymentTransactions: [] }
    ]
  });

  assert.equal(result.materialTotal, 2000);
  assert.equal(result.tradeTotal, 1550);
  assert.equal(result.total, 3550);
  assert.deepEqual(result.materials.map(item => [item.name, item.total]), [["Cement", 1200], ["Sand", 800], ["Unused", 0]]);
  assert.deepEqual(result.trades.map(item => [item.name, item.total]), [["Masonry", 1550], ["Plumbing", 0]]);
  assert.equal(result.total, result.materialTotal + result.tradeTotal);
});

test("date range filters material and trade transactions inclusively", () => {
  const data = {
    categories: [{ id: "cement", name: "Cement" }],
    purchases: [
      { id: "before", categoryId: "cement", totalAmount: 100, date: "2026-08-31" },
      { id: "start", categoryId: "cement", totalAmount: 200, date: "2026-09-01" },
      { id: "end", categoryId: "cement", totalAmount: 300, date: "2026-09-30" }
    ],
    trades: [{ id: "trade", name: "Trade", paymentTransactions: [
      { id: "t-before", type: "labour_advance", amount: 10, date: "2026-08-31" },
      { id: "t-in", type: "labour_settlement", amount: 20, date: "2026-09-15" },
      { id: "t-after", type: "labour_settlement", amount: 30, date: "2026-10-01" }
    ] }]
  };

  const allTime = calculateBuildingCosts(data);
  const range = calculateBuildingCosts({ ...data, fromDate: "2026-09-01", toDate: "2026-09-30" });
  assert.equal(allTime.total, 660);
  assert.equal(range.materialTotal, 500);
  assert.equal(range.tradeTotal, 20);
  assert.equal(range.total, 520);
});

test("invalid date bounds are rejected", () => {
  assert.throws(() => calculateBuildingCosts({ fromDate: "2026-09-30", toDate: "2026-09-01" }), RangeError);
});

test("migrated Owner Account is not double-counted as a Labour Trade", () => {
  const result = calculateBuildingCosts({
    trades: [
      {
        id: LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
        name: "OWNERS ACCOUNT",
        paymentTransactions: [
          { id: "legacy-payment-1", type: "mobilization_advance", amount: 5745171 }
        ]
      },
      {
        id: "masons",
        name: "MASONS",
        paymentTransactions: [
          { id: "mason-payment-1", type: "labour_settlement", amount: 125000 }
        ]
      }
    ]
  });
  assert.equal(result.tradeTotal, 125000);
  assert.deepEqual(result.trades.map(trade => trade.name), ["MASONS"]);
});

test("Owner Scope payments are included once as actual expenditure", () => {
  const result = calculateBuildingCosts({
    categories: [],
    purchases: [{ id: "materials", totalAmount: 2000, date: "2026-10-01" }],
    trades: [
      {
        id: LEGACY_OWNER_ACCOUNT_DOCUMENT_ID,
        name: "OWNERS ACCOUNT",
        contractItems: [{ amount: 10000000 }],
        paymentTransactions: [{ id: "legacy", type: "mobilization_advance", amount: 5745171 }]
      },
      {
        id: "masons",
        name: "MASONS",
        contractItems: [{ amount: 500000 }],
        paymentTransactions: [
          { id: "settlement", type: "labour_settlement", amount: 400, labourEarned: 9000, advanceAdjustment: 250 },
          { id: "settlement", type: "labour_settlement", amount: 400, labourEarned: 9000, advanceAdjustment: 250 }
        ]
      }
    ],
    ownerScopePayments: [
      { id: "owner-1", amount: 5745171, date: "2026-10-01" },
      { id: "owner-1", amount: 5745171, date: "2026-10-01" }
    ]
  });

  assert.equal(result.materialTotal, 2000);
  assert.equal(result.tradeTotal, 400);
  assert.equal(result.ownerScopeTotal, 5745171);
  assert.equal(result.total, 5747571);
});

test("Owner Scope actual payments follow the inclusive date filter", () => {
  const result = calculateBuildingCosts({
    ownerScopePayments: [
      { id: "before", amount: 100, date: "2026-08-31" },
      { id: "start", amount: 200, date: "2026-09-01" },
      { id: "end", amount: 300, date: "2026-09-30" },
      { id: "after", amount: 400, date: "2026-10-01" }
    ],
    fromDate: "2026-09-01",
    toDate: "2026-09-30"
  });

  assert.equal(result.ownerScopeTotal, 500);
  assert.equal(result.total, 500);
});

test("updated Owner Scope payment amount recalculates Owner Scope and building totals once", () => {
  const data = {
    categories: [],
    trades: [],
    ownerScopePayments: [
      { id: "stable-payment-id", amount: 1250, date: "2026-10-01" }
    ]
  };
  const before = calculateBuildingCosts(data);
  const afterDateEdit = calculateBuildingCosts({
    ...data,
    ownerScopePayments: [{ ...data.ownerScopePayments[0], date: "2026-10-02" }]
  });
  const after = calculateBuildingCosts({
    ...data,
    ownerScopePayments: [{ ...data.ownerScopePayments[0], amount: 2500 }]
  });

  assert.equal(before.ownerScopeTotal, 1250);
  assert.equal(before.total, 1250);
  assert.equal(afterDateEdit.ownerScopeTotal, before.ownerScopeTotal);
  assert.equal(afterDateEdit.total, before.total);
  assert.equal(after.ownerScopeTotal, 2500);
  assert.equal(after.total, 2500);
  assert.equal(after.ownerScopeTotal, after.total);
});

test("a newly linked Owner Scope payment is included exactly once in building cost", () => {
  const newPayment = {
    id: "new-payment",
    expenseItemId: "new-item",
    date: "2026-10-07",
    amount: 1250,
    paymentMode: "Bank Transfer"
  };
  const result = calculateBuildingCosts({
    ownerScopePayments: [newPayment]
  });

  assert.equal(result.ownerScopeTotal, 1250);
  assert.equal(result.total, 1250);
  assert.equal(result.ownerScopeTotal + result.tradeTotal + result.materialTotal, 1250);
});

test("deleting an Owner Scope item removes its linked payments from building cost once", () => {
  const payments = [
    { id: "deleted-1", expenseItemId: "selected", amount: 1, date: "2026-10-07" },
    { id: "deleted-2", expenseItemId: "selected", amount: 250, date: "2026-10-07" },
    { id: "kept", expenseItemId: "other", amount: 700, date: "2026-10-07" }
  ];
  const before = calculateBuildingCosts({ ownerScopePayments: payments });
  const after = calculateBuildingCosts({
    ownerScopePayments: payments.filter(payment => payment.expenseItemId !== "selected")
  });

  assert.equal(before.ownerScopeTotal, 951);
  assert.equal(before.total, 951);
  assert.equal(after.ownerScopeTotal, 700);
  assert.equal(after.total, 700);
  assert.equal(before.total - after.total, 251);
});

test("Owner Scope inclusion toggle changes only its contribution to total building cost", () => {
  const data = {
    categories: [{ id: "cement", name: "Cement" }],
    purchases: [{ id: "purchase", categoryId: "cement", totalAmount: 300, date: "2026-10-07" }],
    trades: [{
      id: "masons",
      name: "MASONS",
      paymentTransactions: [{
        id: "labour",
        type: "labour_settlement",
        amount: 200,
        date: "2026-10-07"
      }]
    }],
    ownerScopePayments: [{ id: "owner", amount: 500, date: "2026-10-07" }]
  };

  const included = calculateBuildingCosts(data);
  const excluded = calculateBuildingCosts({ ...data, includeOwnerScope: false });

  assert.equal(included.total, 1000);
  assert.equal(excluded.total, 500);
  assert.equal(excluded.materialTotal, included.materialTotal);
  assert.equal(excluded.tradeTotal, included.tradeTotal);
  assert.equal(excluded.ownerScopeTotal, included.ownerScopeTotal);
  assert.equal(excluded.ownerScope.total, included.ownerScope.total);
});
