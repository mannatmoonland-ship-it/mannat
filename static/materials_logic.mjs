export function isCementCategory(category) {
  return String(category?.name || "").trim().toLocaleLowerCase() === "cement";
}

export function calculateCementPurchaseTotal(quantity, rate) {
  const total = Number(quantity) * Number(rate);
  return Number.isFinite(total) ? total : null;
}

export function calculateStockTotals(category, purchases, consumptions, unit, transactionCategoryId) {
  if (!isCementCategory(category) || !unit) return null;

  const categoryPurchases = purchases.filter(item => transactionCategoryId(item) === category.id);
  const categoryConsumptions = consumptions.filter(item => transactionCategoryId(item) === category.id);
  const records = [...categoryPurchases, ...categoryConsumptions];
  const normalizedUnit = String(unit).trim().toLocaleLowerCase();
  if (records.some(record => String(record.unit || "").trim().toLocaleLowerCase() !== normalizedUnit)) return null;

  const purchased = categoryPurchases.reduce((total, item) => total + Number(item.quantity || 0), 0);
  const consumed = categoryConsumptions.reduce((total, item) => total + Number(item.quantity || 0), 0);
  return { purchased, consumed, available: purchased - consumed, unit };
}

export function calculatePurchaseInvestment(categories, purchases, transactionCategoryId) {
  const uniquePurchases = new Map();
  purchases.forEach((purchase, index) => {
    const key = purchase.id ? `id:${purchase.id}` : `index:${index}`;
    uniquePurchases.set(key, purchase);
  });

  const categoryTotals = new Map(categories.map(category => [category.id, {
    id: category.id,
    name: category.name,
    total: 0
  }]));
  let total = 0;

  for (const purchase of uniquePurchases.values()) {
    const amount = Number(purchase.totalAmount);
    if (!Number.isFinite(amount)) continue;
    total += amount;

    const categoryId = transactionCategoryId(purchase);
    const category = categories.find(item => item.id === categoryId);

    const key = category?.id || `uncategorized:${categoryId || purchase.categoryName || ""}`;
    if (!categoryTotals.has(key)) {
      categoryTotals.set(key, {
        id: key,
        name: category?.name || purchase.categoryName || "Uncategorized",
        total: 0
      });
    }
    categoryTotals.get(key).total += amount;
  }

  return {
    total,
    categories: [...categoryTotals.values()].sort((left, right) => left.name.localeCompare(right.name))
  };
}