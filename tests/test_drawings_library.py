from pathlib import Path
import re

ROOT = Path(__file__).parents[1]
app = (ROOT / "app.py").read_text(encoding="utf-8")
dashboard = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")
template = (ROOT / "templates/drawings.html").read_text(encoding="utf-8")
css = (ROOT / "static/drawings.css").read_text(encoding="utf-8")
js = (ROOT / "static/drawings.js").read_text(encoding="utf-8")

assert '@app.route("/drawings")' in app
assert '"drawings.html"' in app
assert 'Drawings Library' in dashboard
assert 'href="/drawings"' in dashboard
assert 'Manage architectural, structural, and MEP drawings.' in dashboard
assert 'id="drawingSearch"' in template
assert 'id="categoryFilter"' in template
assert 'id="createCategoryButton"' in template
assert 'id="categoryList"' in template
assert 'id="drawingForm"' in template
assert 'id="drawingCategoryId"' in template
assert 'dialogs.css' in template
assert 'data-drawings-csrf="{{ drawings_csrf }}"' in template
assert 'drawings_categories' in js
assert 'collection(db, "drawings")' in js
assert 'sortCategoriesByCreationOrder' in js
assert 'sortDrawingsByCreationOrder' in js
assert 'aCreated.getTime() - bCreated.getTime()' in js
assert 'createdAt' in js
assert not re.search(
    r"\b(addDoc|setDoc|updateDoc|deleteDoc|writeBatch|runTransaction)\s*\(",
    js,
)
assert 'async function sendDrawingsMutation(' in js
assert 'error.safeApiMessage = true;' in js
assert 'error?.safeApiMessage ? error.message' in js
assert '"/api/drawings/categories"' in js
assert '"/api/drawings/categories/order"' in js
assert '"/api/drawings"' in js
assert 'X-CSRF-Token' in js
assert '"categoryName": name' in app
assert 'associatedDrawings.length' in js
assert 'Google Drive PDF URL' in template
assert 'isGoogleDriveUrl' in js
assert 'View Drawings' not in js
assert 'view-category' not in js
assert 'categoryFilter.addEventListener' in js
assert 'showPrompt("Enter a category name.", "", "Create Category", "Save", "Cancel")' in js
assert 'No drawings found.' in js
assert 'No drawings added yet.' in js
assert 'if (!categories.length || (!drawings.length && !query))' not in js
assert 'category-empty' in js
assert 'await loadData();' in js[js.index('async function createCategory'):js.index('async function editCategory')]
assert 'rel="noopener noreferrer"' in js
assert 'showConfirmDialog' in js
assert 'savingDrawing' in js
assert 'sortDrawingsByOrder' in js
assert 'sortOrder' in js
assert 'def _drawing_create_fields(' in app
assert 'data-action="move-category-up"' in js
assert 'data-action="move-category-down"' in js
assert 'data-action="move-drawing-up"' in js
assert 'data-action="move-drawing-down"' in js
assert 'drawing-drag-handle' not in js
assert 'activeDrag' not in js
assert 'pointerdown' not in js
assert 'data-id="${escapeHtml(drawing.id)}"' in js
assert '`/api/drawings/categories/${encodeURIComponent(category.id)}/order`' not in js
assert '`/api/drawings/categories/${encodeURIComponent(categoryId)}/order`' in js
assert 'previous order has been restored.' in js
assert 'sortCategoriesByOrder' in js
assert 'title="Move Up"' in js and 'title="Move Down"' in js
assert '.drawings-order-button:disabled' in css
assert '.drawing-drag-handle' not in css
assert 'drawings-modal-open' in css
assert 'paymentTransactions' not in js
print('drawings library regression checks passed')