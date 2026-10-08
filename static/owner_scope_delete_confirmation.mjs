import { formatDateWithDay } from "./date_format.js";

export function buildOwnerScopeDeleteConfirmation({
  item,
  payments,
  formatMoney
}) {
  const linkedPayments = payments.filter(
    payment => payment.expenseItemId === item.id
  );
  const paymentDates = [...new Set(
    linkedPayments.map(payment => formatDateWithDay(payment.date, "—"))
  )];
  const linkedPaymentTotal = linkedPayments.reduce(
    (total, payment) => total + Number(payment.amount ?? 0),
    0
  );

  return {
    title: "Mannat Moon",
    message: "Are you sure you want to delete this expense?",
    details: [
      `Item: ${item.name || "Unnamed Owner Scope item"}`,
      paymentDates.join(", ") || "—",
      formatMoney(linkedPaymentTotal)
    ].join("\n"),
    confirmText: "Delete",
    cancelText: "Cancel",
    type: "danger"
  };
}
