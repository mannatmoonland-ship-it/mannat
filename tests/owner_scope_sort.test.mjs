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
