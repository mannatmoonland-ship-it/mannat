import {
  registerEscClose,
  showConfirmDialog,
  showError,
  showPrompt,
  showSuccess,
  unregisterEscClose
} from "./dialogs.js";
import { formatDate } from "./date_format.js";
import {
  describeFirebaseError,
  firebaseAuthReady,
  getFirebaseApp,
  getFirebaseFirestoreSdk
} from "./firebase_auth.js";

let db = null;
let firestoreSdk = null;
let firebaseBootstrapError = null;
try {
  await firebaseAuthReady;
  firestoreSdk = await getFirebaseFirestoreSdk();
  db = firestoreSdk.getFirestore(await getFirebaseApp());
} catch (error) {
  firebaseBootstrapError = error;
  console.error("Firebase initialization failed:", error?.stage || error?.code || error?.name);
}
const firestoreMethod = name => (...args) => {
  const method = firestoreSdk?.[name];
  if (typeof method !== "function") {
    throw firebaseBootstrapError || new Error("The Firebase Firestore SDK is unavailable.");
  }
  return method(...args);
};
const collection = firestoreMethod("collection");
const getDocs = firestoreMethod("getDocs");
const categoryList = document.getElementById("categoryList");
const status = document.getElementById("drawingsStatus");
const search = document.getElementById("drawingSearch");
const categoryFilter = document.getElementById("categoryFilter");
const drawingModal = document.getElementById("drawingModal");
let categories = [];
let drawings = [];
let savingDrawing = false;
let savingOrder = false;
let pendingCategoryCreate = null;
let pendingDrawingCreate = null;
const drawingsCsrfToken = document.body.dataset.drawingsCsrf || "";

async function sendDrawingsMutation(path, method, payload) {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": drawingsCsrfToken
    },
    ...(payload === undefined ? {} : { body: JSON.stringify(payload) })
  });
  let result;
  try {
    result = await response.json();
  } catch {
    throw new Error("The server returned an invalid response.");
  }
  if (!response.ok) {
    throw new Error(typeof result.error === "string" ? result.error : "Unable to save the Drawings Library change.");
  }
  return result;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[character]));
}

function setStatus(message, isError = false) {
  status.textContent = message;
  status.className = `drawings-status${isError ? " error" : ""}`;
}

function timestampDate(value) {
  if (!value) return null;
  if (typeof value.toDate === "function") return value.toDate();
  if (typeof value === "object" && Number.isFinite(value.seconds)) return new Date(value.seconds * 1000);
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function formatUpdatedDate(drawing) {
  const date = timestampDate(drawing.updatedAt || drawing.createdAt);
  return date ? formatDate(date, "Not recorded") : "Not recorded";
}

function sortCategoriesByCreationOrder(categoriesToSort) {
  return categoriesToSort
    .map((item, index) => ({ item, index }))
    .sort((a, b) => {
      const aCreated = timestampDate(a.item.createdAt) ?? timestampDate(a.item.updatedAt) ?? new Date(0);
      const bCreated = timestampDate(b.item.createdAt) ?? timestampDate(b.item.updatedAt) ?? new Date(0);
      if (aCreated.getTime() !== bCreated.getTime()) {
        return aCreated.getTime() - bCreated.getTime();
      }
      return a.index - b.index;
    })
    .map(({ item }) => item);
}

function sortCategoriesByOrder(categoriesToSort) {
  return categoriesToSort
    .map((item, index) => ({ item, index }))
    .sort((a, b) => {
      const aHasOrder = a.item.sortOrder !== undefined && a.item.sortOrder !== null && a.item.sortOrder !== "" && Number.isFinite(Number(a.item.sortOrder));
      const bHasOrder = b.item.sortOrder !== undefined && b.item.sortOrder !== null && b.item.sortOrder !== "" && Number.isFinite(Number(b.item.sortOrder));
      const aOrder = aHasOrder ? Number(a.item.sortOrder) : a.index;
      const bOrder = bHasOrder ? Number(b.item.sortOrder) : b.index;
      return aOrder - bOrder || a.index - b.index;
    })
    .map(({ item }) => item);
}

function sortDrawingsByCreationOrder(drawingsToSort) {
  return drawingsToSort
    .map((item, index) => ({ item, index }))
    .sort((a, b) => {
      const aCreated = timestampDate(a.item.createdAt) ?? timestampDate(a.item.updatedAt) ?? new Date(0);
      const bCreated = timestampDate(b.item.createdAt) ?? timestampDate(b.item.updatedAt) ?? new Date(0);
      if (aCreated.getTime() !== bCreated.getTime()) {
        return aCreated.getTime() - bCreated.getTime();
      }
      return a.index - b.index;
    })
    .map(({ item }) => item);
}

function sortDrawingsByOrder(drawingsToSort) {
  return [...drawingsToSort].sort((a, b) => Number(a.sortOrder) - Number(b.sortOrder));
}

function isGoogleDriveUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && /(^|\.)drive\.google\.com$|(^|\.)docs\.google\.com$/i.test(url.hostname);
  } catch {
    return false;
  }
}

function categoryMatches(category, categoryDrawings, query) {
  if (!query) return true;
  const categoryText = `${category.name} ${categoryDrawings.map(drawing => `${drawing.drawingName} ${drawing.drawingNumber} ${drawing.description}`).join(" ")}`;
  return categoryText.toLowerCase().includes(query);
}

function renderCategories() {
  const query = search.value.trim().toLowerCase();
  const selectedCategoryId = categoryFilter.value;
  const visibleGroups = categories.map((category, categoryIndex) => {
    const categoryDrawings = sortDrawingsByOrder(drawings.filter(drawing => drawing.categoryId === category.id));
    const matchingDrawings = query
      ? categoryDrawings.filter(drawing => `${drawing.drawingName} ${drawing.drawingNumber} ${category.name} ${drawing.description}`.toLowerCase().includes(query))
      : categoryDrawings;
    return { category, categoryIndex, categoryDrawings, drawings: matchingDrawings, show: categoryMatches(category, categoryDrawings, query) && (!selectedCategoryId || category.id === selectedCategoryId) };
  }).filter(group => group.show && (!query || group.drawings.length));

  if (!categories.length) {
    categoryList.innerHTML = '<div class="drawings-empty">No drawings added yet.</div>';
    return;
  }
  if (!visibleGroups.length) {
    categoryList.innerHTML = '<div class="drawings-empty">No drawings found.</div>';
    return;
  }

  categoryList.innerHTML = visibleGroups.map(({ category, categoryIndex, categoryDrawings, drawings: visibleDrawings }) => `
    <article class="category-card">
      <div class="category-header">
        <div><h2>${escapeHtml(category.name)}</h2><div class="category-count">${categoryDrawings.length} drawing${categoryDrawings.length === 1 ? "" : "s"}</div></div>
        <div class="category-actions">
          <button type="button" class="drawings-action small drawings-order-button" data-action="move-category-up" data-id="${escapeHtml(category.id)}" aria-label="Move Up" title="Move Up" ${savingOrder || categoryIndex === 0 ? "disabled" : ""}>↑</button>
          <button type="button" class="drawings-action small drawings-order-button" data-action="move-category-down" data-id="${escapeHtml(category.id)}" aria-label="Move Down" title="Move Down" ${savingOrder || categoryIndex === categories.length - 1 ? "disabled" : ""}>↓</button>
          <button type="button" class="drawings-action small" data-action="edit-category" data-id="${escapeHtml(category.id)}">Edit Category</button>
          <button type="button" class="drawings-action small danger" data-action="delete-category" data-id="${escapeHtml(category.id)}">Delete Category</button>
          <button type="button" class="drawings-action small" data-action="add-drawing" data-id="${escapeHtml(category.id)}">Add Drawing</button>
        </div>
      </div>
      <div class="drawing-list category-drawings">${categoryDrawings.length ? '<div class="drawing-table-head"><span>Drawing</span><span>Number / Revision</span><span>Description</span><span>Last Updated</span><span>Actions</span></div>' + visibleDrawings.map(drawing => renderDrawing(drawing, categoryDrawings.findIndex(item => item.id === drawing.id), categoryDrawings.length)).join("") : '<div class="category-empty">No drawings in this category.</div>'}</div>
    </article>
  `).join("");
}

function renderDrawing(drawing, orderIndex, orderCount) {
  return `<div class="drawing-row" data-id="${escapeHtml(drawing.id)}">
    <div><h3>${escapeHtml(drawing.drawingName)}</h3></div>
    <div class="drawing-meta">${escapeHtml(drawing.drawingNumber || "-")}<br>Revision ${escapeHtml(drawing.revision || "-")}</div>
    <div class="drawing-description">${escapeHtml(drawing.description || "No description")}</div>
    <div class="drawing-updated">${escapeHtml(formatUpdatedDate(drawing))}</div>
    <div class="drawing-actions">
      <button type="button" class="drawings-action small drawings-order-button" data-action="move-drawing-up" data-id="${escapeHtml(drawing.id)}" aria-label="Move Up" title="Move Up" ${savingOrder || orderIndex === 0 ? "disabled" : ""}>↑</button>
      <button type="button" class="drawings-action small drawings-order-button" data-action="move-drawing-down" data-id="${escapeHtml(drawing.id)}" aria-label="Move Down" title="Move Down" ${savingOrder || orderIndex === orderCount - 1 ? "disabled" : ""}>↓</button>
      <a class="drawings-action small" href="${escapeHtml(drawing.googleDriveUrl)}" target="_blank" rel="noopener noreferrer">Open PDF</a>
      <button type="button" class="drawings-action small" data-action="edit-drawing" data-id="${escapeHtml(drawing.id)}">Edit</button>
      <button type="button" class="drawings-action small danger" data-action="delete-drawing" data-id="${escapeHtml(drawing.id)}">Delete</button>
    </div>
  </div>`;
}

async function loadData() {
  if (firebaseBootstrapError) {
    setStatus(describeFirebaseError(firebaseBootstrapError), true);
    categoryList.innerHTML = "";
    return;
  }
  try {
    setStatus("Loading drawings...");
    const [categorySnapshot, drawingSnapshot] = await Promise.all([
      getDocs(collection(db, "drawings_categories")),
      getDocs(collection(db, "drawings"))
    ]);
    categories = sortCategoriesByOrder(sortCategoriesByCreationOrder(categorySnapshot.docs.map(item => ({ id: item.id, ...item.data() }))));
    drawings = sortDrawingsByCreationOrder(drawingSnapshot.docs.map(item => ({ id: item.id, ...item.data() })));
    const categoryOrder = new Map();
    drawings.forEach(drawing => {
      const nextOrder = categoryOrder.get(drawing.categoryId) || 0;
      if (!Number.isFinite(Number(drawing.sortOrder))) drawing.sortOrder = nextOrder;
      categoryOrder.set(drawing.categoryId, Math.max(nextOrder + 1, Number(drawing.sortOrder) + 1));
    });
    categoryFilter.innerHTML = '<option value="">All categories</option>' + categories.map(category => `<option value="${escapeHtml(category.id)}">${escapeHtml(category.name)}</option>`).join("");
    setStatus(`${categories.length} categor${categories.length === 1 ? "y" : "ies"} · ${drawings.length} drawing${drawings.length === 1 ? "" : "s"} available.`);
    renderCategories();
  } catch (error) {
    console.error(error);
    setStatus(describeFirebaseError(error), true);
    categoryList.innerHTML = "";
  }
}

async function createCategory() {
  const value = await showPrompt("Enter a category name.", "", "Create Category", "Save", "Cancel");
  const name = String(value || "").trim();
  if (!name) return;
  if (categories.some(category => String(category.name).trim().toLowerCase() === name.toLowerCase())) {
    await showError("A category with this name already exists.", "Drawings Library");
    return;
  }
  try {
    if (pendingCategoryCreate?.name !== name) {
      pendingCategoryCreate = { id: crypto.randomUUID(), name };
    }
    await sendDrawingsMutation("/api/drawings/categories", "POST", pendingCategoryCreate);
    pendingCategoryCreate = null;
    await showSuccess("Category created successfully.", "Drawings Library");
    await loadData();
  } catch (error) {
    console.error(error);
    setStatus("Unable to create the category. Please try again.", true);
  }
}

async function editCategory(category) {
  const value = await showPrompt("Enter a new category name.", category.name, "Edit Category", "Save", "Cancel");
  const name = String(value || "").trim();
  if (!name || name === category.name) return;
  if (categories.some(item => item.id !== category.id && String(item.name).trim().toLowerCase() === name.toLowerCase())) {
    await showError("A category with this name already exists.", "Drawings Library");
    return;
  }
  try {
    await sendDrawingsMutation(
      `/api/drawings/categories/${encodeURIComponent(category.id)}`,
      "PATCH",
      { name }
    );
    await showSuccess("Category updated successfully.", "Drawings Library");
    await loadData();
  } catch (error) {
    console.error(error);
    setStatus("Unable to update the category. Please try again.", true);
  }
}

async function deleteCategory(category) {
  const associatedDrawings = drawings.filter(drawing => drawing.categoryId === category.id);
  if (associatedDrawings.length) {
    await showError("This category contains drawings. Delete or move those drawings before deleting the category.", "Category cannot be deleted");
    return;
  }
  const confirmed = await showConfirmDialog({ title: "Delete Category", message: `Are you sure you want to delete "${category.name}"?`, details: "This removes the category from the Drawings Library. Categories containing drawings cannot be deleted.", confirmText: "Delete", cancelText: "Cancel", type: "danger" });
  if (!confirmed) return;
  try {
    await sendDrawingsMutation(
      `/api/drawings/categories/${encodeURIComponent(category.id)}`,
      "DELETE"
    );
    await showSuccess("Category deleted successfully.", "Drawings Library");
    await loadData();
  } catch (error) {
    console.error(error);
    setStatus("Unable to delete the category. Please try again.", true);
  }
}

function openDrawingForm(categoryId, drawing = null) {
  document.getElementById("drawingForm").reset();
  document.getElementById("drawingCategoryId").innerHTML = categories.map(category => `<option value="${escapeHtml(category.id)}">${escapeHtml(category.name)}</option>`).join("");
  document.getElementById("drawingId").value = drawing?.id || "";
  document.getElementById("drawingCategoryId").value = categoryId;
  document.getElementById("drawingName").value = drawing?.drawingName || "";
  document.getElementById("drawingNumber").value = drawing?.drawingNumber || "";
  document.getElementById("drawingRevision").value = drawing?.revision || "";
  document.getElementById("googleDriveUrl").value = drawing?.googleDriveUrl || "";
  document.getElementById("drawingDescription").value = drawing?.description || "";
  document.getElementById("drawingModalTitle").textContent = drawing ? "Edit Drawing" : "Add Drawing";
  drawingModal.hidden = false;
  document.body.classList.add("drawings-modal-open");
  registerEscClose(drawingModal, closeDrawingForm);
}

function closeDrawingForm() {
  unregisterEscClose(drawingModal);
  drawingModal.hidden = true;
  document.body.classList.remove("drawings-modal-open");
}

async function saveDrawing(event) {
  event.preventDefault();
  if (savingDrawing) return;
  const drawingId = document.getElementById("drawingId").value;
  const categoryId = document.getElementById("drawingCategoryId").value;
  const category = categories.find(item => item.id === categoryId);
  const drawingName = document.getElementById("drawingName").value.trim();
  const googleDriveUrl = document.getElementById("googleDriveUrl").value.trim();
  if (!category || !drawingName || !isGoogleDriveUrl(googleDriveUrl)) {
    setStatus("Enter a drawing name and a valid Google Drive PDF URL.", true);
    return;
  }
  const data = {
    categoryId,
    drawingName,
    drawingNumber: document.getElementById("drawingNumber").value.trim(),
    revision: document.getElementById("drawingRevision").value.trim(),
    googleDriveUrl,
    description: document.getElementById("drawingDescription").value.trim()
  };
  savingDrawing = true;
  try {
    if (drawingId) {
      await sendDrawingsMutation(
        `/api/drawings/${encodeURIComponent(drawingId)}`,
        "PATCH",
        data
      );
      await showSuccess("Drawing updated.", "Drawings Library");
    } else {
      const fingerprint = JSON.stringify(data);
      if (pendingDrawingCreate?.fingerprint !== fingerprint) {
        pendingDrawingCreate = { fingerprint, id: crypto.randomUUID() };
      }
      await sendDrawingsMutation("/api/drawings", "POST", {
        id: pendingDrawingCreate.id,
        ...data
      });
      pendingDrawingCreate = null;
      await showSuccess("Drawing saved.", "Drawings Library");
    }
    closeDrawingForm();
    await loadData();
  } catch (error) {
    console.error(error);
    setStatus("Unable to save the drawing. Please check your connection and try again.", true);
  } finally {
    savingDrawing = false;
  }
}

async function deleteDrawing(drawing) {
  const confirmed = await showConfirmDialog({ title: "Delete Drawing", message: `Are you sure you want to delete "${drawing.drawingName}"?`, details: "This removes the drawing from the Drawings Library. The PDF in Google Drive will not be deleted.", confirmText: "Delete", cancelText: "Cancel", type: "danger" });
  if (!confirmed) return;
  try {
    await sendDrawingsMutation(
      `/api/drawings/${encodeURIComponent(drawing.id)}`,
      "DELETE"
    );
    await showSuccess("Drawing deleted.", "Drawings Library");
    await loadData();
  } catch (error) {
    console.error(error);
    setStatus("Unable to delete the drawing. Please try again.", true);
  }
}

async function moveCategory(categoryId, direction) {
  if (savingOrder) return;
  const fromIndex = categories.findIndex(category => category.id === categoryId);
  const toIndex = fromIndex + direction;
  if (fromIndex < 0 || toIndex < 0 || toIndex >= categories.length) return;
  const previousOrder = categories.map(category => ({ category, sortOrder: category.sortOrder }));
  const [movedCategory] = categories.splice(fromIndex, 1);
  categories.splice(toIndex, 0, movedCategory);
  categories.forEach((category, sortOrder) => { category.sortOrder = sortOrder; });
  savingOrder = true;
  renderCategories();
  setStatus("Saving category order...");
  try {
    await sendDrawingsMutation("/api/drawings/categories/order", "POST", {
      ids: categories.map(category => category.id)
    });
  } catch (error) {
    console.error(error);
    previousOrder.forEach(({ category, sortOrder }) => { category.sortOrder = sortOrder; });
    categories = previousOrder.map(({ category }) => category);
    savingOrder = false;
    renderCategories();
    setStatus("Unable to save the category order. The previous order has been restored. Please try again.", true);
    await showError("The category order could not be saved. The previous order has been restored.", "Drawings Library");
    return;
  }
  savingOrder = false;
  renderCategories();
  setStatus("Category order saved.");
  await showSuccess("Category order saved.", "Drawings Library");
}

async function moveDrawing(drawingId, direction) {
  if (savingOrder) return;
  const drawing = drawings.find(item => item.id === drawingId);
  if (!drawing) return;
  const categoryDrawings = sortDrawingsByOrder(drawings.filter(item => item.categoryId === drawing.categoryId));
  const fromIndex = categoryDrawings.findIndex(item => item.id === drawingId);
  const toIndex = fromIndex + direction;
  if (fromIndex < 0 || toIndex < 0 || toIndex >= categoryDrawings.length) return;
  const previousOrder = categoryDrawings.map(item => ({ drawing: item, sortOrder: item.sortOrder }));
  const [movedDrawing] = categoryDrawings.splice(fromIndex, 1);
  categoryDrawings.splice(toIndex, 0, movedDrawing);
  categoryDrawings.forEach((item, sortOrder) => { item.sortOrder = sortOrder; });
  savingOrder = true;
  renderCategories();
  await persistDrawingOrder(categoryDrawings, previousOrder);
}

async function persistDrawingOrder(categoryDrawings, previousOrder) {
  setStatus("Saving drawing order...");
  try {
    const categoryId = categoryDrawings[0]?.categoryId;
    if (!categoryId || categoryDrawings.some(drawing => drawing.categoryId !== categoryId)) {
      throw new Error("The drawing order is invalid.");
    }
    await sendDrawingsMutation(
      `/api/drawings/categories/${encodeURIComponent(categoryId)}/order`,
      "POST",
      { ids: categoryDrawings.map(drawing => drawing.id) }
    );
  } catch (error) {
    console.error(error);
    previousOrder.forEach(({ drawing, sortOrder }) => { drawing.sortOrder = sortOrder; });
    setStatus("Unable to save the drawing order. The previous order has been restored. Please try again.", true);
    savingOrder = false;
    renderCategories();
    await showError("The drawing order could not be saved. The previous order has been restored.", "Drawings Library");
    return;
  }
  savingOrder = false;
  setStatus("Drawing order saved.");
  renderCategories();
  await showSuccess("Drawing order saved.", "Drawings Library");
}

document.getElementById("createCategoryButton").addEventListener("click", createCategory);
document.getElementById("drawingForm").addEventListener("submit", saveDrawing);
document.getElementById("closeDrawingModal").addEventListener("click", closeDrawingForm);
document.getElementById("cancelDrawing").addEventListener("click", closeDrawingForm);
drawingModal.addEventListener("click", event => { if (event.target === drawingModal) closeDrawingForm(); });
search.addEventListener("input", renderCategories);
categoryFilter.addEventListener("change", renderCategories);
categoryList.addEventListener("click", event => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  if (button.dataset.action === "move-category-up") return moveCategory(button.dataset.id, -1);
  if (button.dataset.action === "move-category-down") return moveCategory(button.dataset.id, 1);
  if (button.dataset.action === "move-drawing-up") return moveDrawing(button.dataset.id, -1);
  if (button.dataset.action === "move-drawing-down") return moveDrawing(button.dataset.id, 1);
  const isCategoryAction = ["edit-category", "delete-category", "add-drawing"].includes(button.dataset.action);
  const item = isCategoryAction
    ? categories.find(category => category.id === button.dataset.id)
    : drawings.find(drawing => drawing.id === button.dataset.id);
  if (!item) return;
  if (button.dataset.action === "edit-category") editCategory(item);
  if (button.dataset.action === "delete-category") deleteCategory(item);
  if (button.dataset.action === "add-drawing") openDrawingForm(item.id);
  if (button.dataset.action === "edit-drawing") openDrawingForm(item.categoryId, item);
  if (button.dataset.action === "delete-drawing") deleteDrawing(item);
});

loadData();
