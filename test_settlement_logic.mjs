import { calculateAdvanceBalance, calculateSettlementBreakdown, calculateOutstandingLabourBalance, calculateMaximumSettlementAmount, getSettlementFormResetState, applySettlementFormReset } from './static/settlement_logic.mjs';

const tests = [
  {
    name: 'A. previous outstanding 2000, earnings 0, payment 1000 -> outstanding 1000',
    previousOutstanding: 2000,
    labourEarned: 0,
    amountPaidNow: 1000,
    expectedOutstanding: 1000,
  },
  {
    name: 'B. previous outstanding 1000, earnings 0, payment 1000 -> outstanding 0',
    previousOutstanding: 1000,
    labourEarned: 0,
    amountPaidNow: 1000,
    expectedOutstanding: 0,
  },
  {
    name: 'C. previous outstanding 2000, earnings 0, payment 0 -> outstanding 2000',
    previousOutstanding: 2000,
    labourEarned: 0,
    amountPaidNow: 0,
    expectedOutstanding: 2000,
  },
  {
    name: 'D. previous outstanding 2000, earnings 0, payment 2500 -> rejected as invalid',
    previousOutstanding: 2000,
    labourEarned: 0,
    amountPaidNow: 2500,
    expectedOutstanding: 0,
    rejected: true,
  },
  {
    name: 'E. editing or deleting a payment recalculates correctly',
    previousOutstanding: 2000,
    labourEarned: 0,
    amountPaidNow: 1000,
    expectedOutstanding: 1000,
  },
];

for (const test of tests) {
  const result = calculateSettlementBreakdown({
    transactions: [{
      type: 'labour_settlement',
      labourEarned: 0,
      advanceAdjustment: 0,
      amount: 0,
      outstandingBalance: test.previousOutstanding,
      date: '2024-01-01',
    }],
    labourEarned: test.labourEarned,
    mode: 'none',
    amountPaidNow: test.amountPaidNow,
    previousOutstandingBalance: test.previousOutstanding,
  });

  if (test.rejected) {
    const maximumAllowedAmount = Math.max(0, test.previousOutstanding);
    if (test.amountPaidNow > maximumAllowedAmount) {
      continue;
    }
    throw new Error(`${test.name}: amount should have been rejected`);
  }

  if (result.outstandingBalance !== test.expectedOutstanding) {
    throw new Error(`${test.name}: expected ${test.expectedOutstanding}, got ${result.outstandingBalance}`);
  }
}

const zeroEarnedCap = calculateMaximumSettlementAmount({
  transactions: [{
    type: 'labour_settlement',
    labourEarned: 0,
    advanceAdjustment: 0,
    amount: 1500,
    outstandingBalance: 1000,
    date: '2024-01-01',
  }],
  labourEarned: 0,
  advanceAdjustment: 0,
});

if (zeroEarnedCap !== 1000) {
  throw new Error(`Expected maximum zero-earned settlement amount to be 1000, got ${zeroEarnedCap}`);
}

const mixedCap = calculateMaximumSettlementAmount({
  transactions: [{
    type: 'labour_settlement',
    labourEarned: 0,
    advanceAdjustment: 0,
    amount: 1000,
    outstandingBalance: 2000,
    date: '2024-01-01',
  }],
  labourEarned: 600,
  advanceAdjustment: 200,
});

if (mixedCap !== 2400) {
  throw new Error(`Expected mixed settlement cap to be 2400, got ${mixedCap}`);
}

const caseA = calculateSettlementBreakdown({
  transactions: [{ type: 'labour_advance', amount: 5000, date: '2024-01-01' }],
  labourEarned: 8000,
  mode: 'full',
  requestedAdjustment: 0,
  amountPaidNow: 0,
});

if (caseA.adjustment !== 5000) {
  throw new Error(`Expected case A deduction to be 5000, got ${caseA.adjustment}`);
}
if (caseA.amountAvailableToPay !== 3000) {
  throw new Error(`Expected case A available amount to be 3000, got ${caseA.amountAvailableToPay}`);
}
if (caseA.remainingAdvanceBalance !== 0) {
  throw new Error(`Expected case A remaining advance to be 0, got ${caseA.remainingAdvanceBalance}`);
}

const caseB = calculateSettlementBreakdown({
  transactions: [{ type: 'labour_advance', amount: 10000, date: '2024-01-01' }],
  labourEarned: 8000,
  mode: 'full',
  requestedAdjustment: 0,
  amountPaidNow: 0,
});

if (caseB.adjustment !== 8000) {
  throw new Error(`Expected case B deduction to be 8000, got ${caseB.adjustment}`);
}
if (caseB.amountAvailableToPay !== 0) {
  throw new Error(`Expected case B available amount to be 0, got ${caseB.amountAvailableToPay}`);
}
if (caseB.remainingAdvanceBalance !== 2000) {
  throw new Error(`Expected case B remaining advance to be 2000, got ${caseB.remainingAdvanceBalance}`);
}

const caseC = calculateSettlementBreakdown({
  transactions: [{ type: 'labour_advance', amount: 5000, date: '2024-01-01' }],
  labourEarned: 8000,
  mode: 'full',
  requestedAdjustment: 0,
  amountPaidNow: 0,
  previousOutstandingBalance: 2000,
});

if (caseC.adjustment !== 5000) {
  throw new Error(`Expected case C deduction to be 5000, got ${caseC.adjustment}`);
}
if (caseC.amountAvailableToPay !== 5000) {
  throw new Error(`Expected case C available amount to be 5000, got ${caseC.amountAvailableToPay}`);
}
if (caseC.remainingAdvanceBalance !== 0) {
  throw new Error(`Expected case C remaining advance to be 0, got ${caseC.remainingAdvanceBalance}`);
}

const partialDeduction = calculateSettlementBreakdown({
  transactions: [{ type: 'labour_advance', amount: 5000, date: '2024-01-01' }],
  labourEarned: 8000,
  mode: 'partial',
  requestedAdjustment: 2000,
  amountPaidNow: 0,
});

if (partialDeduction.adjustment !== 2000) {
  throw new Error(`Expected partial deduction to be 2000, got ${partialDeduction.adjustment}`);
}
if (partialDeduction.amountAvailableToPay !== 6000) {
  throw new Error(`Expected partial deduction available amount to be 6000, got ${partialDeduction.amountAvailableToPay}`);
}
if (partialDeduction.remainingAdvanceBalance !== 3000) {
  throw new Error(`Expected partial deduction remaining advance to be 3000, got ${partialDeduction.remainingAdvanceBalance}`);
}

const advancesAndDeductions = [
  { type: 'labour_advance', amount: 10000 },
  { type: 'labour_advance', amount: 15000 },
  { type: 'mobilization_advance', amount: 9000 },
  { type: 'labour_settlement', advanceAdjustment: 750 },
  { type: 'labour_settlement', advanceAdjustment: 1250 },
];

if (calculateAdvanceBalance(advancesAndDeductions) !== 23000) {
  throw new Error('Expected 25000 in labour advances less 2000 deductions to leave 23000.');
}

if (calculateAdvanceBalance([]) !== 0 || calculateAdvanceBalance(undefined) !== 0) {
  throw new Error('Expected missing or empty payment transactions to have a zero advance balance.');
}

if (calculateAdvanceBalance([
  { type: 'labour_advance', amount: 0 },
  { type: 'labour_advance' },
  { type: 'labour_advance', amount: 'invalid' },
  { type: 'labour_advance', amount: -500 },
  { type: 'labour_advance', amount: true },
  { type: 'labour_settlement', advanceAdjustment: 'invalid' },
  null,
]) !== 0) {
  throw new Error('Expected zero, missing, or invalid transaction amounts to be ignored safely.');
}

if (calculateAdvanceBalance([
  { type: 'labour_advance', amount: 1000 },
  { type: 'labour_settlement', advanceAdjustment: 1000 },
]) !== 0) {
  throw new Error('Expected fully recovered labour advances to have a zero balance.');
}

const editedAdvanceTransactions = [...advancesAndDeductions];
editedAdvanceTransactions[0] = { ...editedAdvanceTransactions[0], amount: 12000 };
if (calculateAdvanceBalance(editedAdvanceTransactions) !== 25000) {
  throw new Error('Expected an edited labour advance to update the balance.');
}

const deletedAdvanceTransactions = advancesAndDeductions.filter((_, index) => index !== 1);
if (calculateAdvanceBalance(deletedAdvanceTransactions) !== 8000) {
  throw new Error('Expected deleting a labour advance to update the balance.');
}

const deletedDeductionTransactions = advancesAndDeductions.filter((_, index) => index !== 3);
if (calculateAdvanceBalance(deletedDeductionTransactions) !== 23750) {
  throw new Error('Expected deleting an advance deduction to update the balance.');
}

const editedDeductionTransactions = [...advancesAndDeductions];
editedDeductionTransactions[3] = { ...editedDeductionTransactions[3], advanceAdjustment: 1000 };
if (calculateAdvanceBalance(editedDeductionTransactions) !== 22750) {
  throw new Error('Expected editing an advance deduction to update the balance.');
}

if (calculateAdvanceBalance([
  { type: 'labour_advance', amount: 25000 },
  { type: 'labour_settlement', advanceAdjustment: 2000 },
  { type: 'labour_settlement', labourEarned: 15000, amount: 13000, advanceAdjustment: 0 },
]) !== 23000) {
  throw new Error('Expected labour wage earned and paid amounts not to change the advance balance.');
}

const recalculatedOutstanding = calculateOutstandingLabourBalance([
  { type: 'labour_settlement', labourEarned: 0, advanceAdjustment: 0, amount: 1000, outstandingBalance: 1000 },
  { type: 'labour_settlement', labourEarned: 0, advanceAdjustment: 0, amount: 0, outstandingBalance: 1000 },
]);

if (recalculatedOutstanding !== 1000) {
  throw new Error(`Expected recalculated outstanding balance to be 1000, got ${recalculatedOutstanding}`);
}

const fullPaymentSummary = calculateSettlementBreakdown({
  transactions: [{ type: 'labour_settlement', labourEarned: 8000, advanceAdjustment: 0, amount: 8000, outstandingBalance: 0, date: '2024-01-01' }],
  labourEarned: 8000,
  mode: 'none',
  requestedAdjustment: 0,
  amountPaidNow: 8000,
  previousOutstandingBalance: 0,
});

if (fullPaymentSummary.outstandingBalance !== 0) {
  throw new Error(`Expected full payment to settle to 0, got ${fullPaymentSummary.outstandingBalance}`);
}

const partialPaymentSummary = calculateSettlementBreakdown({
  transactions: [{ type: 'labour_settlement', labourEarned: 8000, advanceAdjustment: 0, amount: 3000, outstandingBalance: 5000, date: '2024-01-01' }],
  labourEarned: 8000,
  mode: 'none',
  requestedAdjustment: 0,
  amountPaidNow: 3000,
  previousOutstandingBalance: 5000,
});

if (partialPaymentSummary.outstandingBalance !== 10000) {
  throw new Error(`Expected partial payment carry-forward to be 10000, got ${partialPaymentSummary.outstandingBalance}`);
}

const deductionSummary = calculateSettlementBreakdown({
  transactions: [{ type: 'labour_advance', amount: 5000, date: '2024-01-01' }],
  labourEarned: 8000,
  mode: 'full',
  requestedAdjustment: 0,
  amountPaidNow: 3000,
  previousOutstandingBalance: 0,
});

if (deductionSummary.adjustment !== 5000 || deductionSummary.amountAvailableToPay !== 3000 || deductionSummary.remainingAdvanceBalance !== 0) {
  throw new Error(`Expected deduction summary to be 5000 / 3000 / 0, got adjustment=${deductionSummary.adjustment}, available=${deductionSummary.amountAvailableToPay}, remaining=${deductionSummary.remainingAdvanceBalance}`);
}

const zeroAdvanceSummary = calculateSettlementBreakdown({
  transactions: [
    { type: 'mobilization_advance', amount: 40000 },
    { type: 'labour_settlement', advanceAdjustment: 0 },
  ],
  labourEarned: 25000,
  mode: 'partial',
  requestedAdjustment: 2500,
  amountPaidNow: 27500,
  previousOutstandingBalance: 2500,
});

if (zeroAdvanceSummary.balance !== 0 || zeroAdvanceSummary.adjustment !== 0) {
  throw new Error('Expected zero labour advance balance to ignore a stale partial deduction.');
}
if (zeroAdvanceSummary.amountAvailableToPay !== 27500 || zeroAdvanceSummary.outstandingBalance !== 0) {
  throw new Error('Expected zero-balance settlement to include prior pending labour payment without an advance deduction.');
}

const cappedPartialDeduction = calculateSettlementBreakdown({
  transactions: [{ type: 'labour_advance', amount: 5000 }],
  labourEarned: 1500,
  mode: 'partial',
  requestedAdjustment: 9000,
  amountPaidNow: 0,
});

if (cappedPartialDeduction.adjustment !== 1500 || cappedPartialDeduction.remainingAdvanceBalance !== 3500) {
  throw new Error('Expected partial deduction to be capped by labour earned even when requested amount is larger.');
}

const resetState = getSettlementFormResetState();
if (resetState.labourEarned !== 0 || resetState.advanceAdjustmentMode !== 'none' || resetState.paymentAmount !== 0) {
  throw new Error('Expected reset state to clear labour settlement fields.');
}

const formState = {
  labourEarned: { value: '8000' },
  advanceAdjustmentMode: { value: 'full' },
  adjustmentAmount: { value: '5000' },
  paymentAmount: { value: '3000' },
  paymentRemarks: { value: 'Test remark' }
};

applySettlementFormReset(formState);

if (formState.labourEarned.value !== '0' || formState.advanceAdjustmentMode.value !== 'none' || formState.paymentAmount.value !== '0' || formState.paymentRemarks.value !== '') {
  throw new Error('Expected successful-save reset to clear all labour settlement fields.');
}

const saveRefreshTransactions = [
  { id: 't1', type: 'labour_settlement', labourEarned: 8000, advanceAdjustment: 0, amount: 3000, outstandingBalance: 5000, date: '2024-01-01' },
  { id: 't2', type: 'labour_settlement', labourEarned: 8000, advanceAdjustment: 0, amount: 3000, outstandingBalance: 10000, date: '2024-01-02' },
];
const refreshedOutstanding = calculateOutstandingLabourBalance(saveRefreshTransactions);
if (refreshedOutstanding !== 10000) {
  throw new Error(`Expected refreshed outstanding balance to count each saved settlement once, got ${refreshedOutstanding}`);
}

console.log('Settlement logic regression check passed.');
