export function ownerScopePaymentDateValue(value) {
  if (!value) return null;
  if (typeof value.toDate === "function") value = value.toDate();
  else if (Number.isFinite(value.seconds)) value = new Date(value.seconds * 1000);

  const timestamp = value instanceof Date ? value.getTime() : new Date(value).getTime();
  return Number.isFinite(timestamp) ? timestamp : null;
}

export function sortOwnerScopeItemsByPaymentDate(items, payments) {
  const linkedPayments = new Map();
  for (const payment of payments) {
    if (payment.expenseItemId && !linkedPayments.has(payment.expenseItemId)) {
      linkedPayments.set(payment.expenseItemId, payment);
    }
  }

  return items
    .map((item, index) => ({
      item,
      index,
      date: ownerScopePaymentDateValue(linkedPayments.get(item.id)?.date)
    }))
    .sort((left, right) => {
      if (left.date === null) return right.date === null ? left.index - right.index : 1;
      if (right.date === null) return -1;
      return right.date - left.date || left.index - right.index;
    })
    .map(({ item }) => item);
}
