import assert from "node:assert/strict";
import test from "node:test";
import { calculateContractPaymentSummary } from "../static/contract_payment_summary.mjs";
import {
  calculateAdvanceBalance,
  calculateNetLabourPayable,
  calculateOutstandingLabourBalance
} from "../static/settlement_logic.mjs";
import { paymentLedgerSummary } from "../static/ledger_pdf.js";

function assertClose(actual, expected, message) {
  assert.ok(Math.abs(actual - expected) < 1e-9, `${message}: expected ${expected}, got ${actual}`);
}

test("TEST A: settlement-only contract payment counts the actual settlement amount", () => {
  const summary = calculateContractPaymentSummary(
    [{ amount: 30000 }],
    [{ id: "settlement", type: "labour_settlement", amount: 10000, labourEarned: 10000, advanceAdjustment: 0 }]
  );

  assert.equal(summary.contractTotal, 30000);
  assert.equal(summary.contractPayments, 10000);
  assert.equal(summary.balanceContractValue, 20000);
  assertClose(summary.paidPercentage, 33.333333333333336, "paid percentage");
  assertClose(summary.remainingPercentage, 66.66666666666666, "remaining percentage");
});

test("TEST B: labour advance and settlement both count as actual payments", () => {
  const summary = calculateContractPaymentSummary(
    [{ amount: 30000 }],
    [
      { id: "labour-advance", type: "labour_advance", amount: 2000 },
      { id: "settlement", type: "labour_settlement", amount: 10000, labourEarned: 10000, advanceAdjustment: 0 }
    ]
  );

  assert.equal(summary.contractPayments, 12000);
  assert.equal(summary.balanceContractValue, 18000);
  assertClose(summary.paidPercentage, 40, "paid percentage");
  assertClose(summary.remainingPercentage, 60, "remaining percentage");
});

test("TEST C: mobilization advance, labour advance, and settlement all count as actual payments", () => {
  const summary = calculateContractPaymentSummary(
    [{ amount: 30000 }],
    [
      { id: "mobilization", type: "mobilization_advance", amount: 5000 },
      { id: "labour-advance", type: "labour_advance", amount: 2000 },
      { id: "settlement", type: "labour_settlement", amount: 10000, labourEarned: 10000, advanceAdjustment: 0 }
    ]
  );

  assert.equal(summary.contractPayments, 17000);
  assert.equal(summary.balanceContractValue, 13000);
  assertClose(summary.paidPercentage, 56.666666666666664, "paid percentage");
  assertClose(summary.remainingPercentage, 43.333333333333336, "remaining percentage");
});

test("TEST D: advance deduction is not counted as an extra payment", () => {
  const summary = calculateContractPaymentSummary(
    [{ amount: 30000 }],
    [
      { id: "labour-advance", type: "labour_advance", amount: 2000 },
      { id: "settlement", type: "labour_settlement", amount: 10000, labourEarned: 12000, advanceAdjustment: 3000 }
    ]
  );

  assert.equal(summary.contractPayments, 12000);
  assert.equal(summary.balanceContractValue, 18000);
});

test("TEST E: unpaid labour earnings are not treated as actual payments", () => {
  const summary = calculateContractPaymentSummary(
    [{ amount: 30000 }],
    [
      { id: "settlement", type: "labour_settlement", amount: 0, labourEarned: 15000, advanceAdjustment: 0 }
    ]
  );

  assert.equal(summary.contractPayments, 0);
  assert.equal(summary.balanceContractValue, 30000);
  assertClose(summary.paidPercentage, 0, "paid percentage");
  assertClose(summary.remainingPercentage, 100, "remaining percentage");
});

test("TEST F: supplied ledger figures stay separated across contract and labour accounts", () => {
  const transactions = [
    { id: "advance", type: "labour_advance", amount: 25000 },
    {
      id: "settlement-1",
      type: "labour_settlement",
      amount: 32000,
      labourEarned: 50000,
      advanceAdjustment: 18000,
      outstandingBalance: 0
    },
    {
      id: "settlement-2",
      type: "labour_settlement",
      amount: 13000,
      labourEarned: 20000,
      advanceAdjustment: 7000,
      outstandingBalance: 0
    }
  ];
  const contract = calculateContractPaymentSummary(
    [{ amount: 1125000 }],
    transactions
  );
  const ledgerSummary = paymentLedgerSummary({
    contractItems: [{ amount: 1125000 }],
    paymentTransactions: [...transactions, { ...transactions[0] }]
  });
  const labourEarned = transactions
    .filter(transaction => transaction.type === "labour_settlement")
    .reduce((total, transaction) => total + transaction.labourEarned, 0);
  const advanceRecovered = transactions
    .filter(transaction => transaction.type === "labour_settlement")
    .reduce((total, transaction) => total + transaction.advanceAdjustment, 0);
  const cashPaidForLabour = transactions
    .filter(transaction => transaction.type === "labour_settlement")
    .reduce((total, transaction) => total + transaction.amount, 0);

  assert.equal(contract.contractPayments, 70000);
  assert.equal(contract.balanceContractValue, 1055000);
  assert.deepEqual(
    contract.contractPaymentBreakdown.map(({ type, amount }) => [type, amount]),
    [
      ["mobilization_advance", 0],
      ["labour_advance", 25000],
      ["labour_settlement", 45000],
      ["completed_work_payment", 0]
    ]
  );
  assert.equal(labourEarned, 70000);
  assert.equal(advanceRecovered, 25000);
  assert.equal(calculateNetLabourPayable(labourEarned, advanceRecovered), 45000);
  assert.equal(cashPaidForLabour, 45000);
  assert.equal(calculateOutstandingLabourBalance(transactions), 0);
  assert.equal(calculateAdvanceBalance(transactions), 0);
  assert.equal(ledgerSummary.contractPayments, 70000);
  assert.equal(ledgerSummary.labourEarned, 70000);
  assert.equal(ledgerSummary.advanceDeductions, 25000);
  assert.equal(ledgerSummary.netLabourPayable, 45000);
  assert.equal(ledgerSummary.labourSettlements, 45000);
  assert.equal(ledgerSummary.outstandingBalance, 0);
  assert.equal(ledgerSummary.labourAdvances, 25000);
  assert.equal(ledgerSummary.advanceBalance, 0);
});

test("labour settlement payments count settlement amounts only, not advances or recovered deductions", () => {
  const summary = paymentLedgerSummary({
    paymentTransactions: [
      { id: "mobilization", type: "mobilization_advance", amount: 90000 },
      { id: "advance", type: "labour_advance", amount: 25000 },
      {
        id: "settlement-1",
        type: "labour_settlement",
        amount: 32000,
        labourEarned: 50000,
        advanceAdjustment: 18000
      },
      {
        id: "settlement-2",
        type: "labour_settlement",
        amount: 13000,
        labourEarned: 20000,
        advanceAdjustment: 7000
      }
    ]
  });

  assert.equal(summary.labourSettlements, 45000);
  assert.equal(summary.labourEarned, 70000);
  assert.equal(summary.labourAdvances, 25000);
  assert.equal(summary.advanceDeductions, 25000);
});

test("advance summary separates lifetime advances, saved recoveries, and the current balance", () => {
  const transactions = [
    { id: "advance-1", type: "labour_advance", amount: 25000 },
    {
      id: "settlement-1",
      type: "labour_settlement",
      amount: 0,
      labourEarned: 25000,
      advanceAdjustment: 25000,
      outstandingBalance: 0
    },
    { id: "mobilization-1", type: "mobilization_advance", amount: 100000 },
    { id: "advance-2", type: "labour_advance", amount: 25000 }
  ];

  const trade = { paymentTransactions: transactions };
  const summary = paymentLedgerSummary(trade);
  assert.equal(summary.labourAdvances, 50000);
  assert.equal(summary.advanceDeductions, 25000);
  assert.equal(summary.advanceBalance, 25000);
  assert.equal(paymentLedgerSummary(trade).advanceBalance, 25000);

  const afterEditingRecovery = paymentLedgerSummary({
    paymentTransactions: transactions.map(transaction =>
      transaction.id === "settlement-1"
        ? { ...transaction, advanceAdjustment: 20000 }
        : transaction
    )
  });
  assert.equal(afterEditingRecovery.advanceBalance, 30000);

  const afterDeletingRecovery = paymentLedgerSummary({
    paymentTransactions: transactions.filter(transaction => transaction.id !== "settlement-1")
  });
  assert.equal(afterDeletingRecovery.advanceBalance, 50000);

  const anotherTrade = paymentLedgerSummary({
    paymentTransactions: [{ id: "other-advance", type: "labour_advance", amount: 7000 }]
  });
  assert.equal(anotherTrade.labourAdvances, 7000);
  assert.equal(anotherTrade.advanceBalance, 7000);
});

test("multiple partial recoveries are counted once and mobilization advances stay outside labour totals", () => {
  const firstRecovery = {
    id: "recovery-1",
    type: "labour_settlement",
    date: "2026-01-03",
    amount: 2000,
    labourEarned: 10000,
    advanceAdjustment: 5000,
    outstandingBalance: 3000
  };
  const secondRecovery = {
    id: "recovery-2",
    type: "labour_settlement",
    date: "2026-01-04",
    amount: 2500,
    labourEarned: 10000,
    advanceAdjustment: 7500,
    outstandingBalance: 3000
  };
  const summary = paymentLedgerSummary({
    contractItems: [{ amount: 50000 }],
    paymentTransactions: [
      { id: "advance-1", type: "labour_advance", amount: 10000 },
      { id: "advance-2", type: "labour_advance", amount: 15000 },
      { id: "mobilization", type: "mobilization_advance", amount: 99000 },
      firstRecovery,
      { ...firstRecovery },
      secondRecovery
    ]
  });

  assert.equal(summary.labourAdvances, 25000);
  assert.equal(summary.advanceDeductions, 12500);
  assert.equal(summary.advanceBalance, 12500);
  assert.equal(summary.labourSettlements, 4500);
  assert.equal(summary.labourEarned, 20000);
  assert.equal(summary.outstandingBalance, 3000);
  assert.equal(summary.contractPayments, 128500);
  assert.equal(summary.balanceContractValue, -78500);
});

test("ledger account fixture reconciles earned, paid, recovered, outstanding, and contract amounts", () => {
  const transactions = [
    { id: "advance-1", type: "labour_advance", amount: 25000, date: "2026-01-01" },
    {
      id: "settlement-1",
      type: "labour_settlement",
      date: "2026-01-02",
      amount: 20000,
      labourEarned: 45000,
      advanceAdjustment: 25000,
      outstandingBalance: 0
    },
    { id: "advance-2", type: "labour_advance", amount: 25000, date: "2026-01-03" },
    {
      id: "settlement-2",
      type: "labour_settlement",
      date: "2026-01-04",
      amount: 40000,
      labourEarned: 40000,
      advanceAdjustment: 0,
      outstandingBalance: 0
    }
  ];
  const summary = paymentLedgerSummary({
    contractItems: [{ amount: 1125000 }],
    paymentTransactions: [...transactions, { ...transactions[0] }]
  });

  assert.equal(summary.labourEarned, 85000);
  assert.equal(summary.labourSettlements, 60000);
  assert.equal(summary.advanceDeductions, 25000);
  assert.equal(summary.netLabourPayable, 60000);
  assert.equal(summary.outstandingBalance, 0);
  assert.equal(summary.labourAdvances, 50000);
  assert.equal(summary.advanceBalance, 25000);
  assert.equal(summary.contractPayments, 110000);
  assert.equal(summary.balanceContractValue, 1015000);
  assert.deepEqual(
    summary.contractPaymentBreakdown.map(({ type, amount }) => [type, amount]),
    [
      ["mobilization_advance", 0],
      ["labour_advance", 50000],
      ["labour_settlement", 60000],
      ["completed_work_payment", 0]
    ]
  );
  assertClose(summary.paidPercentage, 9.777777777777779, "contract paid percentage");
  assertClose(summary.remainingPercentage, 90.22222222222223, "contract remaining percentage");
  assert.equal(
    summary.contractPaymentBreakdown.reduce((total, category) => total + category.amount, 0),
    summary.contractPayments
  );
});
