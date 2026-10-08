const CONTRACT_PAYMENT_TYPES = new Set([
  "mobilization_advance",
  "labour_advance",
  "labour_settlement",
  "completed_work_payment"
]);

export function calculateContractPaymentSummary(contractItems = [], transactions = []) {
  const contractTotal = Array.isArray(contractItems)
    ? contractItems.reduce((total, item) => total + Number(item?.amount || 0), 0)
    : 0;

  const contractPayments = Array.isArray(transactions)
    ? transactions.reduce((total, transaction) => {
        if (!transaction || !CONTRACT_PAYMENT_TYPES.has(transaction.type)) {
          return total;
        }

        const amount = Number(transaction.amount || 0);
        return total + amount;
      }, 0)
    : 0;

  const paidPercentage = contractTotal > 0
    ? (contractPayments / contractTotal) * 100
    : 0;

  return {
    contractTotal,
    contractPayments,
    balanceContractValue: contractTotal - contractPayments,
    paidPercentage,
    remainingPercentage: Math.max(0, 100 - paidPercentage)
  };
}