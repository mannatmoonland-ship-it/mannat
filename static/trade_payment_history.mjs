import { sortPaymentTransactions } from "./payment_sort.mjs";

export function uniquePaymentTransactions(transactions = []) {
  const seenIds = new Set();
  return sortPaymentTransactions(
    (Array.isArray(transactions) ? transactions : []).filter(transaction => {
      if (!transaction || typeof transaction !== "object") return false;
      if (transaction.id === undefined || transaction.id === null || transaction.id === "") {
        return true;
      }
      if (seenIds.has(transaction.id)) return false;
      seenIds.add(transaction.id);
      return true;
    })
  );
}

export function totalPaymentAmount(transactions = []) {
  return uniquePaymentTransactions(transactions).reduce((total, transaction) => {
    const amount = Number(transaction.amount);
    return Number.isFinite(amount) ? total + amount : total;
  }, 0);
}
