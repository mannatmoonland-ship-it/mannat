import assert from "node:assert/strict";
import test from "node:test";
import { calculateCementPurchaseTotal, calculatePurchaseInvestment, calculateStockTotals, isCementCategory } from "../static/materials_logic.mjs";

const transactionCategoryId = transaction => transaction.categoryId || "";

test("stock totals include Cement purchases and consumption only", () => {
  const cement = { id: "cement", name: "Cement", unit: "bags" };
  const sand = { id: "sand", name: "Sand", unit: "tons" };
  const purchases = [
    { categoryId: "cement", quantity: 120, unit: "bags" },
    { categoryId: "cement", quantity: 80, unit: "BAGS" },
    { categoryId: "sand", quantity: 25, unit: "tons" }
  ];
  const consumptions = [
    { categoryId: "cement", quantity: 65, unit: "bags" },
    { categoryId: "sand", quantity: 3, unit: "tons" }
  ];

  assert.deepEqual(calculateStockTotals(cement, purchases, consumptions, "bags", transactionCategoryId), {
    purchased: 200,
    consumed: 65,
    available: 135,
    unit: "bags"
  });
  assert.equal(calculateStockTotals(sand, purchases, consumptions, "tons", transactionCategoryId), null);
  assert.equal(isCementCategory({ name: "  CEMENT " }), true);
});

test("Cement stock is unavailable when transaction units conflict", () => {
  const cement = { id: "cement", name: "Cement", unit: "bags" };
  const purchases = [{ categoryId: "cement", quantity: 2, unit: "tons" }];
  assert.equal(calculateStockTotals(cement, purchases, [], "bags", transactionCategoryId), null);
});

test("purchase investment includes Cement and all other categories", () => {
  const categories = [
    { id: "cement", name: "Cement" },
    { id: "sand", name: "Sand" },
    { id: "rmc", name: "RMC M20" }
  ];
  const purchases = [
    { id: "p1", categoryId: "cement", totalAmount: 1800 },
    { id: "p2", categoryId: "sand", totalAmount: 2500 },
    { id: "p3", categoryId: "rmc", totalAmount: 720 },
    { id: "p4", categoryId: "cement", totalAmount: "500" }
  ];

  const result = calculatePurchaseInvestment(categories, purchases, transactionCategoryId);
  assert.equal(result.total, 5520);
  assert.equal(result.categories.reduce((sum, category) => sum + category.total, 0), result.total);
  assert.equal(result.categories.filter(category => category.id === "cement").length, 1);
  assert.equal(result.categories.find(category => category.id === "sand").total, 2500);
  assert.deepEqual(result.categories.map(category => category.name), ["Cement", "RMC M20", "Sand"]);
  assert.equal("cementTotal" in result, false);
  assert.equal("otherTotal" in result, false);
});

test("Cement purchase total is quantity multiplied by rate", () => {
  assert.equal(calculateCementPurchaseTotal(100, 1155), 115500);
  assert.equal(calculateCementPurchaseTotal("invalid", 1155), null);
});