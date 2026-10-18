export function ownerScopePaymentDateValue(value) {
  if (!value) return null;
  if (typeof value.toDate === "function") value = value.toDate();
  else if (Number.isFinite(value.seconds)) value = new Date(value.seconds * 1000);

  const timestamp = value instanceof Date ? value.getTime() : new Date(value).getTime();
  return Number.isFinite(timestamp) ? timestamp : null;
}

function comparePaymentsByDateAndTime(left, right) {
  const leftDate = ownerScopePaymentDateValue(left.date);
  const rightDate = ownerScopePaymentDateValue(right.date);
  if (leftDate !== rightDate) {
    if (leftDate === null) return 1;
    if (rightDate === null) return -1;
    return rightDate - leftDate;
  }

  const leftTime = ownerScopePaymentDateValue(left.createdAt || left.updatedAt);
  const rightTime = ownerScopePaymentDateValue(right.createdAt || right.updatedAt);
  if (leftTime === null) return rightTime === null ? 0 : 1;
  if (rightTime === null) return -1;
  return rightTime - leftTime;
}

export function sortOwnerScopeItemsByPaymentDate(items, payments) {
  const linkedPayments = new Map();
  for (const payment of payments) {
    if (!payment.expenseItemId) continue;
    const current = linkedPayments.get(payment.expenseItemId);
    if (!current || comparePaymentsByDateAndTime(payment, current) < 0) {
      linkedPayments.set(payment.expenseItemId, payment);
    }
  }

  return items
    .map((item, index) => ({
      item,
      index,
      payment: linkedPayments.get(item.id)
    }))
    .sort((left, right) => {
      if (!left.payment) return right.payment ? 1 : left.index - right.index;
      if (!right.payment) return -1;
      return comparePaymentsByDateAndTime(left.payment, right.payment) || left.index - right.index;
    })
    .map(({ item }) => item);
}
