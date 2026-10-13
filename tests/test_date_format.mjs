import test from "node:test";
import assert from "node:assert/strict";
import {
  formatDate,
  formatDateTime,
  formatDateWithDay,
  localDateInputValue
} from "../static/date_format.js";

test("formats ISO date-only values as DD/MM/YYYY without timezone shifts", () => {
  assert.equal(formatDate("2026-09-25"), "25/09/2026");
  assert.equal(formatDateWithDay("2026-09-25"), "Friday, 25/09/2026");
  assert.equal(formatDateWithDay("2026-10-06"), "Tuesday, 06/10/2026");
});

test("formats date and time values with a DD/MM/YYYY date portion", () => {
  assert.equal(formatDateTime("2026-10-03 02:43 PM"), "03/10/2026 02:43 PM");
});

test("formats Firestore-like timestamps and safely preserves invalid values", () => {
  const timestamp = { toDate: () => new Date(2026, 8, 25, 14, 43) };
  assert.equal(formatDateTime(timestamp), "25/09/2026 02:43 PM");
  assert.equal(formatDate("not-a-date"), "not-a-date");
  assert.equal(formatDateWithDay(null, "—"), "—");
});

test("formats a date input value using the local calendar date", () => {
  const localDate = new Date(2026, 9, 7, 23, 59);
  assert.equal(localDateInputValue(localDate), "2026-10-07");
});
