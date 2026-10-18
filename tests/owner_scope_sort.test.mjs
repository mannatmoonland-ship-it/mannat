import assert from "node:assert/strict";
import test from "node:test";

import { sortOwnerScopeItemsByPaymentDate } from "../static/owner_scope_sort.mjs";

test("sorts Owner Scope items by linked payment date descending with stable ties and missing dates last", () => {
  const items = [
    { id: "same-date-first" },
    { id: "old" },
    { id: "missing" },
    { id: "new" },
    { id: "same-date-second" }
  ];
  const payments = [
    { expenseItemId: "same-date-first", date: "2026-09-26" },
    { expenseItemId: "old", date: "2026-06-26" },
    { expenseItemId: "new", date: "2026-09-29" },
    { expenseItemId: "same-date-second", date: "2026-09-26" }
  ];

  assert.deepEqual(
    sortOwnerScopeItemsByPaymentDate(items, payments).map(item => item.id),
    ["new", "same-date-first", "same-date-second", "old", "missing"]
  );
});

test("sorts same-date Owner Scope items by payment entry time newest first", () => {
  const items = [
    { id: "morning" },
    { id: "afternoon" },
    { id: "previous-day" },
    { id: "time-unavailable" },
    { id: "no-payment" }
  ];
  const payments = [
    { expenseItemId: "morning", date: "2026-10-07", createdAt: "2026-10-07T09:15:00Z" },
    { expenseItemId: "afternoon", date: "2026-10-07", createdAt: "2026-10-07T15:45:00Z" },
    { expenseItemId: "previous-day", date: "2026-10-06", createdAt: "2026-10-07T18:00:00Z" },
    { expenseItemId: "time-unavailable", date: "2026-10-07" }
  ];

  assert.deepEqual(
    sortOwnerScopeItemsByPaymentDate(items, payments).map(item => item.id),
    ["afternoon", "morning", "time-unavailable", "previous-day", "no-payment"]
  );
});

test("uses the most recent linked payment date and time when an item has multiple payments", () => {
  const items = [{ id: "multiple" }, { id: "other" }];
  const payments = [
    { expenseItemId: "multiple", date: "2026-10-07", createdAt: "2026-10-07T09:00:00Z" },
    { expenseItemId: "other", date: "2026-10-07", createdAt: "2026-10-07T11:00:00Z" },
    { expenseItemId: "multiple", date: "2026-10-07", createdAt: "2026-10-07T14:00:00Z" }
  ];

  assert.deepEqual(
    sortOwnerScopeItemsByPaymentDate(items, payments).map(item => item.id),
    ["multiple", "other"]
  );
});
