export function calculateAdvanceBalance(transactions) {
    let balance = 0;

    if (!Array.isArray(transactions)) {
        return 0;
    }

    const positiveAmount = value => {
        if (typeof value === 'boolean' || value === null || value === '') {
            return 0;
        }

        const amount = Number(value);
        return Number.isFinite(amount) && amount > 0 ? amount : 0;
    };

    transactions.forEach(transaction => {
        if (!transaction || typeof transaction !== 'object') {
            return;
        }

        if (transaction.type === 'labour_advance') {
            balance += positiveAmount(transaction.amount);
        }

        if (transaction.type === 'labour_settlement') {
            balance -= positiveAmount(transaction.advanceAdjustment);
        }
    });

    return Math.max(0, balance);
}

export function calculateNetLabourPayable(labourEarned = 0, advanceRecovered = 0) {
    const earned = Number(labourEarned);
    const recovered = Number(advanceRecovered);
    const validEarned = Number.isFinite(earned) ? Math.max(0, earned) : 0;
    const validRecovered = Number.isFinite(recovered) ? Math.max(0, recovered) : 0;

    return Math.max(0, validEarned - validRecovered);
}

export function getSettlementFormResetState() {
    return {
        labourEarned: 0,
        advanceAdjustmentMode: 'none',
        adjustmentAmount: 0,
        paymentAmount: 0,
        remarks: ''
    };
}

export function applySettlementFormReset(form = {}) {
    const resetState = getSettlementFormResetState();
    const labourEarnedField = form.labourEarned || form.labourEarnedField;
    const advanceAdjustmentModeField = form.advanceAdjustmentMode || form.advanceAdjustmentModeField;
    const adjustmentAmountField = form.adjustmentAmount || form.adjustmentAmountField;
    const paymentAmountField = form.paymentAmount || form.paymentAmountField;
    const remarksField = form.paymentRemarks || form.remarks || form.remarksField;

    if (labourEarnedField && typeof labourEarnedField.value !== 'undefined') {
        labourEarnedField.value = String(resetState.labourEarned);
    }

    if (advanceAdjustmentModeField && typeof advanceAdjustmentModeField.value !== 'undefined') {
        advanceAdjustmentModeField.value = resetState.advanceAdjustmentMode;
    }

    if (adjustmentAmountField && typeof adjustmentAmountField.value !== 'undefined') {
        adjustmentAmountField.value = String(resetState.adjustmentAmount);
    }

    if (paymentAmountField && typeof paymentAmountField.value !== 'undefined') {
        paymentAmountField.value = String(resetState.paymentAmount);
    }

    if (remarksField && typeof remarksField.value !== 'undefined') {
        remarksField.value = resetState.remarks;
    }

    return resetState;
}

export function calculateMaximumSettlementAmount({
    transactions = [],
    labourEarned = 0,
    advanceAdjustment = 0,
    editingPaymentId = null
} = {}) {
    const activeTransactions = Array.isArray(transactions)
        ? [...transactions]
        : [];

    const filteredTransactions = editingPaymentId
        ? activeTransactions.filter(transaction => transaction.id !== editingPaymentId)
        : activeTransactions;

    const previousOutstandingBalance = calculateOutstandingLabourBalance(filteredTransactions);
    const netCurrentPayable = Math.max(0, Number(labourEarned || 0) - Number(advanceAdjustment || 0));

    return Math.max(0, previousOutstandingBalance + netCurrentPayable);
}

export function calculateSettlementBreakdown({
    transactions = [],
    labourEarned = 0,
    mode = 'none',
    requestedAdjustment = 0,
    amountPaidNow = 0,
    editingPaymentId = null,
    previousOutstandingBalance = null
} = {}) {
    const activeTransactions = Array.isArray(transactions)
        ? [...transactions]
        : [];

    const filteredTransactions = editingPaymentId
        ? activeTransactions.filter(transaction => transaction.id !== editingPaymentId)
        : activeTransactions;

    const balance = Math.max(0, calculateAdvanceBalance(filteredTransactions));
    const earned = Number(labourEarned || 0);

    let adjustment = 0;

    if (mode === 'full') {
        adjustment = Math.min(balance, earned);
    }

    if (mode === 'partial') {
        adjustment = Math.min(
            balance,
            earned,
            Math.max(0, Number(requestedAdjustment || 0))
        );
    }

    const previousOutstanding =
        previousOutstandingBalance !== null && previousOutstandingBalance !== undefined
            ? Number(previousOutstandingBalance || 0)
            : calculateOutstandingLabourBalance(filteredTransactions);

    const finalAdjustment = adjustment;
    const netLabourPayable = Math.max(0, earned - finalAdjustment);
    const amountAvailableToPay = Math.max(0, previousOutstanding + netLabourPayable);
    const paidNow = Number(amountPaidNow || 0);
    const outstandingBalance = Math.max(0, previousOutstanding + earned - finalAdjustment - paidNow);
    const remainingAdvanceBalance = Math.max(0, balance - finalAdjustment);

    return {
        balance,
        adjustment: finalAdjustment,
        amountPaidNow: paidNow,
        amountAvailableToPay,
        outstandingBalance,
        netLabourPayable,
        previousOutstandingBalance: previousOutstanding,
        remainingAdvanceBalance
    };
}

export function calculateOutstandingLabourBalance(transactions = []) {
    if (!Array.isArray(transactions)) {
        return 0;
    }

    let runningOutstanding = 0;

    const chronologicalTransactions = [...transactions].sort((a, b) =>
        String(a.date || '').localeCompare(String(b.date || ''))
    );

    chronologicalTransactions.forEach(transaction => {
        if (transaction.type !== 'labour_settlement') {
            return;
        }

        const hasStoredOutstanding =
            transaction.outstandingBalance !== undefined &&
            transaction.outstandingBalance !== null &&
            transaction.outstandingBalance !== '' &&
            Number.isFinite(Number(transaction.outstandingBalance));
        if (hasStoredOutstanding) {
            runningOutstanding = Number(transaction.outstandingBalance || 0);
            return;
        }

        const earned = Number(transaction.labourEarned || 0);
        const deduction = Number(transaction.advanceAdjustment || 0);
        const paid = Number(transaction.amount || 0);

        runningOutstanding = Math.max(0, runningOutstanding + earned - deduction - paid);
    });

    return runningOutstanding;
}

export function calculateOutstandingBalanceAfterTransaction(transactions, transactionId) {
    if (!Array.isArray(transactions) || !transactionId) {
        return calculateOutstandingLabourBalance(transactions);
    }

    const chronologicalTransactions = [...transactions].sort((a, b) =>
        String(a.date || '').localeCompare(String(b.date || ''))
    );

    const transactionIndex = chronologicalTransactions.findIndex(
        transaction => transaction.id === transactionId
    );

    if (transactionIndex === -1) {
        return calculateOutstandingLabourBalance(transactions);
    }

    return calculateOutstandingLabourBalance(
        chronologicalTransactions.slice(0, transactionIndex + 1)
    );
}
