import assert from "node:assert/strict";
import test from "node:test";
import { buildOwnerScopeDeleteConfirmation } from "../static/owner_scope_delete_confirmation.mjs";

const formatMoney = amount => `₹${amount}`;

test("single payment confirmation follows the Mannat Moon payment deletion style", () => {
  const confirmation = buildOwnerScopeDeleteConfirmation({
    item: { id: "aspp", name: "aspp", category: "labour" },
    payments: [{
      id: "payment-1",
      expenseItemId: "aspp",
      date: "2026-10-07",
      amount: 10,
      paymentMode: "Cash"
    }],
    formatMoney
  });

  assert.equal(confirmation.title, "Mannat Moon");
  assert.equal(confirmation.message, "Are you sure you want to delete this expense?");
  assert.equal(
    confirmation.details,
    "Item: aspp\nWednesday, 07/10/2026\n₹10"
  );
  assert.equal(confirmation.confirmText, "Delete");
  assert.equal(confirmation.cancelText, "Cancel");
  assert.equal(confirmation.type, "danger");
});

test("multiple payment confirmation shows distinct dates and the combined amount", () => {
  const confirmation = buildOwnerScopeDeleteConfirmation({
    item: { id: "multi", name: "Multiple payments", category: "site_expense" },
    payments: [
      {
        id: "payment-1",
        expenseItemId: "multi",
        date: "2026-10-07",
        amount: 10,
        paymentMode: "Cash"
      },
      {
        id: "payment-2",
        expenseItemId: "multi",
        date: "2026-10-08",
        amount: 25,
        paymentMode: "UPI"
      },
      {
        id: "unrelated",
        expenseItemId: "other",
        date: "2026-10-09",
        amount: 500,
        paymentMode: "Cheque"
      }
    ],
    formatMoney
  });

  assert.equal(
    confirmation.details,
    "Item: Multiple payments\nWednesday, 07/10/2026, Thursday, 08/10/2026\n₹35"
  );
  assert.doesNotMatch(confirmation.details, /Cheque/);
  assert.doesNotMatch(confirmation.details, /₹535/);
  assert.doesNotMatch(
    confirmation.details,
    /linked payment|payments linked|payment record|cannot be undone/i
  );
});
