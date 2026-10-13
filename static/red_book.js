(() => {
  const body = document.body;
  const csrfToken = body.dataset.redBookCsrf || "";
  const canCreate = body.dataset.canCreate === "true";
  const canEdit = body.dataset.canEdit === "true";
  const canDelete = body.dataset.canDelete === "true";
  const list = document.getElementById("redBookItems");
  const status = document.getElementById("redBookStatus");
  const emptyMessage = document.getElementById("redBookEmpty");
  const addForm = document.getElementById("redBookAddForm");
  let items = [];
  let draggedItemId = null;
  let orderIsSaving = false;

  function showStatus(message, kind = "success") {
    status.textContent = message;
    status.dataset.kind = kind;
  }

  async function apiRequest(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.method && options.method !== "GET" ? { "X-CSRF-Token": csrfToken } : {}),
        ...options.headers
      },
      cache: "no-store",
      credentials: "same-origin"
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch (error) {
      throw new Error("The Red Book service returned an invalid response.");
    }
    if (!response.ok) {
      throw new Error(payload.error || "Unable to complete the Red Book request.");
    }
    return payload;
  }

  async function saveOrder() {
    if (orderIsSaving) return;
    orderIsSaving = true;
    renderItems();
    showStatus("Saving Red Book order...");
    try {
      await apiRequest("/api/red-book/order", {
        method: "POST",
        body: JSON.stringify({ ids: items.map(item => item.id) })
      });
      items.forEach((item, index) => {
        item.sort_order = index;
      });
      showStatus("Red Book order saved.");
    } catch (error) {
      showStatus(error.message, "error");
      await loadItems();
    } finally {
      orderIsSaving = false;
      renderItems();
    }
  }

  async function moveItem(itemId, offset) {
    if (orderIsSaving) return;
    const index = items.findIndex(item => item.id === itemId);
    const destination = index + offset;
    if (index < 0 || destination < 0 || destination >= items.length) return;
    const [item] = items.splice(index, 1);
    items.splice(destination, 0, item);
    renderItems();
    await saveOrder();
  }

  function actionButton(label, action, disabled = false, accessibleLabel = label) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "red-book-button";
    button.textContent = label;
    button.setAttribute("aria-label", accessibleLabel);
    button.title = accessibleLabel;
    button.disabled = disabled;
    button.addEventListener("click", action);
    return button;
  }

  function renderItems() {
    list.replaceChildren();
    emptyMessage.hidden = items.length !== 0;
    if (addForm) {
      addForm.elements.text.disabled = orderIsSaving;
      addForm.querySelector('button[type="submit"]').disabled = orderIsSaving;
    }
    items.forEach((item, index) => {
      const row = document.createElement("li");
      row.className = "red-book-item";
      row.dataset.itemId = item.id;
      row.draggable = canEdit && !orderIsSaving;
      row.addEventListener("dragstart", event => {
        draggedItemId = item.id;
        row.classList.add("red-book-dragging");
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", item.id);
      });
      row.addEventListener("dragend", () => {
        draggedItemId = null;
        row.classList.remove("red-book-dragging");
      });
      row.addEventListener("dragover", event => event.preventDefault());
      row.addEventListener("drop", event => {
        event.preventDefault();
        const sourceId = draggedItemId || event.dataTransfer.getData("text/plain");
        if (!sourceId || sourceId === item.id) return;
        const sourceIndex = items.findIndex(candidate => candidate.id === sourceId);
        const targetIndex = items.findIndex(candidate => candidate.id === item.id);
        if (sourceIndex < 0 || targetIndex < 0) return;
        const [moving] = items.splice(sourceIndex, 1);
        items.splice(targetIndex, 0, moving);
        renderItems();
        saveOrder();
      });

      const number = document.createElement("span");
      number.className = "red-book-number";
      number.textContent = `${index + 1}.`;
      number.setAttribute("aria-label", `Priority ${index + 1}`);
      row.append(number);

      const text = document.createElement("span");
      text.className = "red-book-text";
      text.textContent = item.text;
      row.append(text);

      const actions = document.createElement("div");
      actions.className = "red-book-item-actions";
      if (canEdit) {
        actions.append(
          actionButton("↑", () => moveItem(item.id, -1), orderIsSaving || index === 0, "Move Up"),
          actionButton("↓", () => moveItem(item.id, 1), orderIsSaving || index === items.length - 1, "Move Down"),
          actionButton("Edit", () => editItem(item, text), orderIsSaving)
        );
      }
      if (canDelete) {
        actions.append(actionButton("Delete", () => deleteItem(item), orderIsSaving));
      }
      row.append(actions);
      list.append(row);
    });
  }

  async function editItem(item, textNode) {
    const input = document.createElement("input");
    input.type = "text";
    input.maxLength = 500;
    input.required = true;
    input.value = item.text;
    input.className = "red-book-edit-input";
    textNode.replaceWith(input);
    input.focus();
    input.select();

    const actions = input.parentElement.querySelector(".red-book-item-actions");
    actions.replaceChildren(
      actionButton("Save", async () => {
        try {
          await apiRequest(`/api/red-book/items/${encodeURIComponent(item.id)}`, {
            method: "PATCH",
            body: JSON.stringify({ text: input.value })
          });
          item.text = input.value.trim();
          renderItems();
          showStatus("Red Book item updated.");
        } catch (error) {
          showStatus(error.message, "error");
        }
      }),
      actionButton("Cancel", renderItems)
    );
  }

  async function deleteItem(item) {
    if (!window.confirm("Delete this Red Book item?")) return;
    try {
      await apiRequest(`/api/red-book/items/${encodeURIComponent(item.id)}`, {
        method: "DELETE"
      });
      items = items.filter(candidate => candidate.id !== item.id);
      items.forEach((candidate, index) => {
        candidate.sort_order = index;
      });
      renderItems();
      showStatus("Red Book item deleted.");
    } catch (error) {
      showStatus(error.message, "error");
    }
  }

  if (addForm && canCreate) {
    addForm.addEventListener("submit", async event => {
      event.preventDefault();
      const input = addForm.elements.text;
      try {
        await apiRequest("/api/red-book/items", {
          method: "POST",
          body: JSON.stringify({ text: input.value })
        });
        input.value = "";
        await loadItems();
        showStatus("Red Book item added.");
      } catch (error) {
        showStatus(error.message, "error");
      }
    });
  }

  async function loadItems() {
    try {
      const payload = await apiRequest("/api/red-book/items");
      items = payload.items;
      renderItems();
    } catch (error) {
      showStatus(error.message, "error");
    }
  }

  loadItems();
})();
