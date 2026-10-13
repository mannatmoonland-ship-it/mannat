import assert from "node:assert/strict";
import test from "node:test";

import {
  totalPaymentAmount,
  uniquePaymentTransactions
} from "../static/trade_payment_history.mjs";

test("trade payment history deduplicates by transaction ID and sorts newest first", () => {
  const transactions = [
    { id: "older", date: "2026-01-01", amount: 100 },
    { id: "newer", date: "2026-03-01", amount: 200 },
    { id: "newer", date: "2026-03-01", amount: 200 }
  ];

  assert.deepEqual(
    uniquePaymentTransactions(transactions).map(transaction => transaction.id),
    ["newer", "older"]
  );
  assert.equal(totalPaymentAmount(transactions), 300);
});

test("trade total paid uses only transaction.amount", () => {
  assert.equal(totalPaymentAmount([
    {
      id: "payment",
      amount: 1250,
      contractAmount: 90000,
      labourEarned: 5000,
      advanceAdjustment: 300
    }
  ]), 1250);
});

test("transactions without IDs remain visible and are included in the actual total", () => {
  const transactions = [
    { date: "2026-02-01", amount: 25 },
    { date: "2026-01-01", amount: 50 }
  ];

  assert.equal(uniquePaymentTransactions(transactions).length, 2);
  assert.equal(totalPaymentAmount(transactions), 75);
});
