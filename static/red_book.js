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

  function driveImageCandidates(url) {
    try {
      const parsed = new URL(url);
      if (parsed.protocol !== "https:" || parsed.hostname !== "drive.google.com") return [];
      const idMatch = parsed.pathname.match(/\/d\/([A-Za-z0-9_-]{1,200})(?:\/|$)/);
      const id = idMatch ? idMatch[1] : parsed.searchParams.get("id");
      if (!id || !/^[A-Za-z0-9_-]{1,200}$/.test(id)) return [];
      const encoded = encodeURIComponent(id);
      return [
        `https://lh3.googleusercontent.com/d/${encoded}=w1600`,
        `https://lh3.googleusercontent.com/d/${encoded}`,
        `https://drive.google.com/thumbnail?id=${encoded}&sz=w1600`,
        `https://drive.google.com/uc?export=view&id=${encoded}`
      ];
    } catch (error) {
      return [];
    }
  }

  function appendImage(content, imageUrl) {
    if (!imageUrl) return;
    const candidates = driveImageCandidates(imageUrl);
    if (!candidates.length) return;

    const link = document.createElement("a");
    link.className = "red-book-image-link";
    link.href = imageUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.setAttribute("aria-label", "Open image in Google Drive");

    const image = document.createElement("img");
    image.className = "red-book-image";
    image.alt = "Image attached to this Red Book item";
    image.loading = "lazy";
    const unavailable = document.createElement("span");
    unavailable.className = "red-book-image-error";
    unavailable.textContent = "Image unavailable. Check Google Drive sharing settings.";
    unavailable.hidden = true;
    let candidateIndex = 0;
    image.addEventListener("error", () => {
      candidateIndex += 1;
      if (candidateIndex < candidates.length) {
        image.src = candidates[candidateIndex];
      } else {
        image.hidden = true;
        unavailable.hidden = false;
      }
    });
    image.src = candidates[candidateIndex];
    link.append(image);
    content.append(link, unavailable);
  }

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
      const imageUrlInput = addForm.elements.namedItem("image_url");
      if (imageUrlInput) imageUrlInput.disabled = orderIsSaving;
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

      const content = document.createElement("div");
      content.className = "red-book-content";
      const text = document.createElement("span");
      text.className = "red-book-text";
      text.textContent = item.text;
      content.append(text);
      appendImage(content, item.image_url);
      row.append(content);

      const actions = document.createElement("div");
      actions.className = "red-book-item-actions";
      if (canEdit) {
        actions.append(
          actionButton("↑", () => moveItem(item.id, -1), orderIsSaving || index === 0, "Move Up"),
          actionButton("↓", () => moveItem(item.id, 1), orderIsSaving || index === items.length - 1, "Move Down"),
          actionButton("Edit", () => editItem(item, content), orderIsSaving)
        );
      }
      if (canDelete) {
        actions.append(actionButton("Delete", () => deleteItem(item), orderIsSaving));
      }
      row.append(actions);
      list.append(row);
    });
  }

  async function editItem(item, contentNode) {
    const fields = document.createElement("div");
    fields.className = "red-book-edit-fields";
    const input = document.createElement("textarea");
    input.rows = 3;
    input.maxLength = 500;
    input.required = true;
    input.value = item.text;
    input.className = "red-book-edit-input";
    input.setAttribute("aria-label", "Construction instruction");

    const imageLabel = document.createElement("label");
    imageLabel.textContent = "Image (Google Drive link)";
    const imageInput = document.createElement("input");
    imageInput.type = "url";
    imageInput.maxLength = 2048;
    imageInput.value = item.image_url || "";
    imageInput.placeholder = "https://drive.google.com/file/d/...";
    imageInput.className = "red-book-edit-image-input";
    imageLabel.append(imageInput);
    fields.append(input, imageLabel);
    contentNode.replaceWith(fields);
    input.focus();
    input.select();

    const actions = fields.parentElement.querySelector(".red-book-item-actions");
    actions.replaceChildren(
      actionButton("Save", async () => {
        try {
          await apiRequest(`/api/red-book/items/${encodeURIComponent(item.id)}`, {
            method: "PATCH",
            body: JSON.stringify({ text: input.value, image_url: imageInput.value })
          });
          item.text = input.value.trim();
          item.image_url = imageInput.value.trim();
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
      const imageUrlInput = addForm.elements.namedItem("image_url");
      try {
        await apiRequest("/api/red-book/items", {
          method: "POST",
          body: JSON.stringify({ text: input.value, image_url: imageUrlInput?.value || "" })
        });
        input.value = "";
        if (imageUrlInput) imageUrlInput.value = "";
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
