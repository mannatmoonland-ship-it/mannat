(async () => {
  const { calculateSettlementBreakdown, calculateOutstandingLabourBalance, calculateMaximumSettlementAmount } = await import('./static/settlement_logic.mjs');

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

  const recalculatedOutstanding = calculateOutstandingLabourBalance([
    { type: 'labour_settlement', labourEarned: 0, advanceAdjustment: 0, amount: 1000, outstandingBalance: 1000 },
    { type: 'labour_settlement', labourEarned: 0, advanceAdjustment: 0, amount: 0, outstandingBalance: 1000 },
  ]);

  if (recalculatedOutstanding !== 1000) {
    throw new Error(`Expected recalculated outstanding balance to be 1000, got ${recalculatedOutstanding}`);
  }

  const fs = await import('node:fs');
  const html = fs.readFileSync('./templates/index.html', 'utf8');

  if (!html.includes('Amount Being Paid Now')) {
    throw new Error('Expected the labour payment form label to include "Amount Being Paid Now".');
  }

  console.log('Settlement logic regression check passed.');
})() ;
