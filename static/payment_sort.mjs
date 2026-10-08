function timestampValue(value) {
  if (!value) return Number.NEGATIVE_INFINITY;
  if (typeof value === "number") return Number.isFinite(value) ? value : Number.NEGATIVE_INFINITY;
  if (typeof value?.toDate === "function") {
    const dateValue = value.toDate().getTime();
    return Number.isFinite(dateValue) ? dateValue : Number.NEGATIVE_INFINITY;
  }
  if (typeof value === "object" && Number.isFinite(value.seconds)) {
    return Number(value.seconds) * 1000 + Number(value.nanoseconds || 0) / 1000000;
  }
  const parsed = Date.parse(String(value));
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

export function sortPaymentTransactions(transactions = []) {
  return (Array.isArray(transactions) ? transactions : [])
    .map((transaction, index) => ({ transaction, index }))
    .sort((left, right) => {
      const dateOrder = String(right.transaction?.date || "").localeCompare(String(left.transaction?.date || ""));
      if (dateOrder !== 0) return dateOrder;

      const rightTimestamp = timestampValue(right.transaction?.createdAt || right.transaction?.updatedAt);
      const leftTimestamp = timestampValue(left.transaction?.createdAt || left.transaction?.updatedAt);
      if (rightTimestamp !== leftTimestamp) return rightTimestamp - leftTimestamp;

      return left.index - right.index;
    })
    .map(({ transaction }) => transaction);
}
