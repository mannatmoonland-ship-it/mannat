const CONTRACT_PAYMENT_CATEGORIES = [
  { type: "mobilization_advance", label: "Mobilization Advances" },
  { type: "labour_advance", label: "Labour Advances Issued" },
  { type: "labour_settlement", label: "Labour Settlement Payments Made" },
  { type: "completed_work_payment", label: "Payments Against Completed Work" }
];

export function calculateContractPaymentSummary(contractItems = [], transactions = []) {
  const contractTotal = Array.isArray(contractItems)
    ? contractItems.reduce((total, item) => total + Number(item?.amount || 0), 0)
    : 0;

  const contractPaymentBreakdown = CONTRACT_PAYMENT_CATEGORIES.map(category => ({
    ...category,
    amount: Array.isArray(transactions)
      ? transactions.reduce((total, transaction) => {
          if (!transaction || transaction.type !== category.type) {
            return total;
          }
          return total + Number(transaction.amount || 0);
        }, 0)
      : 0
  }));
  const contractPayments = contractPaymentBreakdown.reduce(
    (total, category) => total + category.amount,
    0
  );

  const paidPercentage = contractTotal > 0
    ? (contractPayments / contractTotal) * 100
    : 0;

  return {
    contractTotal,
    contractPaymentBreakdown,
    contractPayments,
    balanceContractValue: contractTotal - contractPayments,
    paidPercentage,
    remainingPercentage: Math.max(0, 100 - paidPercentage)
  };
}