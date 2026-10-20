(() => {
  const body = document.body;
  const csrfToken = body.dataset.redBookCsrf || "";
  const canCreate = body.dataset.canCreate === "true";
  const canEdit = body.dataset.canEdit === "true";
  const canDelete = body.dataset.canDelete === "true";
  const canArchive = body.dataset.canArchive === "true";
  const archiveDialog = document.getElementById("redBookArchiveDialog");
  const archiveDialogEyebrow = document.getElementById("redBookArchiveDialogEyebrow");
  const archiveDialogTitle = document.getElementById("redBookArchiveDialogTitle");
  const archiveDialogDescription = document.getElementById("redBookArchiveDialogDescription");
  const archiveDialogItem = document.getElementById("redBookArchiveDialogItem");
  const archiveDialogConfirm = document.getElementById("redBookArchiveDialogConfirm");
  const list = document.getElementById("redBookItems");
  const status = document.getElementById("redBookStatus");
  const emptyMessage = document.getElementById("redBookEmpty");
  const addForm = document.getElementById("redBookAddForm");
  const newImageUrls = document.getElementById("redBookNewImageUrls");
  const addImageUrlButton = document.getElementById("redBookAddImageUrl");
  const printWithImagesButton = document.getElementById("redBookPrintWithImages");
  const printWithoutImagesButton = document.getElementById("redBookPrintWithoutImages");
  let items = [];
  let imageFieldId = 0;
  let draggedItemId = null;
  let touchDragState = null;
  let orderIsSaving = false;
  let isRefreshingPinStates = false;

  function showStatus(message, kind = "success") {
    status.textContent = message;
    status.dataset.kind = kind;
  }

  function printRedBook(includeImages) {
    document.body.dataset.printImages = includeImages ? "true" : "false";
    window.print();
    delete document.body.dataset.printImages;
  }

  printWithImagesButton.addEventListener("click", () => printRedBook(true));
  printWithoutImagesButton.addEventListener("click", () => printRedBook(false));

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

  function setImageUrlFields(container, urls = [""]) {
    container.replaceChildren();
    urls.forEach(url => {
      const row = document.createElement("div");
      row.className = "red-book-image-url-row";
      const input = document.createElement("input");
      const inputId = `redBookImageUrl${imageFieldId++}`;
      input.id = inputId;
      input.name = "image_urls";
      input.type = "url";
      input.maxLength = 2048;
      input.inputMode = "url";
      input.autocomplete = "url";
      input.placeholder = "https://drive.google.com/file/d/...";
      input.value = url || "";
      const label = document.createElement("label");
      label.htmlFor = inputId;
      const removeButton = actionButton("Remove", () => {
        row.remove();
        if (!container.children.length) setImageUrlFields(container);
        updateImageUrlLabels(container);
      }, false, "Remove image link");
      removeButton.classList.add("red-book-remove-image");
      row.append(label, input, removeButton);
      container.append(row);
    });
    updateImageUrlLabels(container);
  }

  function updateImageUrlLabels(container) {
    [...container.querySelectorAll(".red-book-image-url-row label")].forEach((label, index) => {
      label.textContent = `Image ${index + 1}`;
    });
  }

  function getImageUrls(item) {
    if (Array.isArray(item.image_urls)) return item.image_urls;
    return item.image_url ? [item.image_url] : [];
  }

  function restoreTouchDrag(pointerId) {
    if (touchDragState?.pointerId !== pointerId) return;
    const { row, wasDraggable } = touchDragState;
    touchDragState = null;
    if (row.isConnected) row.draggable = wasDraggable;
  }

  list.addEventListener("pointerdown", event => {
    if (!["touch", "pen"].includes(event.pointerType) || !event.isPrimary) return;
    const row = event.target.closest(".red-book-item");
    if (!row || !list.contains(row) || !row.draggable) return;
    touchDragState = { row, pointerId: event.pointerId, wasDraggable: row.draggable };
    row.draggable = false;
  }, true);

  document.addEventListener("pointerup", event => restoreTouchDrag(event.pointerId), { passive: true });
  document.addEventListener("pointercancel", event => restoreTouchDrag(event.pointerId), { passive: true });

  function renderItems() {
    list.replaceChildren();
    emptyMessage.hidden = items.length !== 0;
    if (addForm) {
      addForm.elements.text.disabled = orderIsSaving;
      addForm.querySelectorAll('input[name="image_urls"], .red-book-remove-image')
        .forEach(field => { field.disabled = orderIsSaving; });
      if (addImageUrlButton) addImageUrlButton.disabled = orderIsSaving;
      addForm.querySelector('button[type="submit"]').disabled = orderIsSaving;
    }
    items.forEach((item, index) => {
      const row = document.createElement("li");
      row.className = "red-book-item";
      row.classList.toggle("red-book-item-pinned", item.pinned === true);
      row.dataset.itemId = item.id;
      row.dataset.contextItemId = item.id;
      row.id = `red-book-item-${item.id}`;
      row.tabIndex = 0;
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
      window.MannatRedBookImages.appendImages(content, getImageUrls(item));
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
      if (canArchive) {
        actions.append(actionButton("Archive", () => archiveItem(item), orderIsSaving));
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

    const imageHeading = document.createElement("span");
    imageHeading.className = "red-book-image-heading";
    imageHeading.textContent = "Images (Google Drive links)";
    const imageFields = document.createElement("div");
    imageFields.className = "red-book-image-fields";
    const addImageButton = actionButton("Add another image", () => {
      const urls = [...imageFields.querySelectorAll('input[name="image_urls"]')].map(field => field.value);
      urls.push("");
      setImageUrlFields(imageFields, urls);
    });
    fields.append(input, imageHeading, imageFields, addImageButton);
    setImageUrlFields(imageFields, getImageUrls(item).length ? getImageUrls(item) : [""]);
    contentNode.replaceWith(fields);
    input.focus();
    input.select();

    const actions = fields.parentElement.querySelector(".red-book-item-actions");
    actions.replaceChildren(
      actionButton("Save", async () => {
        try {
          const imageUrls = [...imageFields.querySelectorAll('input[name="image_urls"]')]
            .map(field => field.value);
          await apiRequest(`/api/red-book/items/${encodeURIComponent(item.id)}`, {
            method: "PATCH",
            body: JSON.stringify({ text: input.value, image_urls: imageUrls })
          });
          item.text = input.value.trim();
          item.image_urls = imageUrls.map(url => url.trim()).filter(Boolean);
          item.image_url = item.image_urls[0] || "";
          renderItems();
          showStatus("Red Book item updated.");
        } catch (error) {
          showStatus(error.message, "error");
        }
      }),
      actionButton("Cancel", renderItems)
    );
  }

  function confirmArchive(item, fromDelete = false) {
    archiveDialogEyebrow.textContent = fromDelete ? "CONFIRM DELETE" : "CONFIRM ARCHIVE";
    archiveDialogTitle.textContent = fromDelete ? "Delete this Red Book item?" : "Move to Archive?";
    archiveDialogDescription.textContent = fromDelete
      ? "This removes the item from the active Red Book and keeps it in Archive. You can restore it later."
      : "This item will be removed from the active Red Book and kept in Archive. You can restore it later.";
    archiveDialogConfirm.textContent = fromDelete ? "Delete item" : "Move to Archive";
    archiveDialogItem.textContent = item.text;
    return new Promise(resolve => {
      const finish = () => {
        archiveDialog.removeEventListener("close", onClose);
        resolve(archiveDialog.returnValue === "confirm");
      };
      const onClose = () => finish();
      archiveDialog.addEventListener("close", onClose, { once: true });
      archiveDialog.returnValue = "";
      archiveDialog.showModal();
    });
  }

  function setItemProgress(row, message) {
    if (!row) return;
    row.setAttribute("aria-busy", "true");
    row.querySelectorAll("button").forEach(button => { button.disabled = true; });
    let progress = row.querySelector(".red-book-item-progress");
    if (!progress) {
      progress = document.createElement("div");
      progress.className = "red-book-item-progress";
      progress.setAttribute("role", "status");
      progress.setAttribute("aria-live", "polite");
      row.append(progress);
    }
    progress.textContent = message;
  }

  function clearItemProgress(row) {
    if (!row) return;
    row.removeAttribute("aria-busy");
    row.querySelector(".red-book-item-progress")?.remove();
    row.querySelectorAll("button").forEach(button => { button.disabled = false; });
  }

  async function archiveItem(item, fromDelete = false) {
    if (!(await confirmArchive(item, fromDelete))) return;
    const row = document.getElementById(`red-book-item-${item.id}`);
    setItemProgress(row, "Archiving item...");
    showStatus("Archiving Red Book item...");
    try {
      const itemPath = `/api/red-book/items/${encodeURIComponent(item.id)}`;
      await apiRequest(fromDelete ? itemPath : `${itemPath}/archive`, fromDelete
        ? { method: "DELETE" }
        : { method: "POST", body: JSON.stringify({}) });
      items = items.filter(candidate => candidate.id !== item.id);
      renderItems();
      showStatus("Red Book item moved to Archive.");
    } catch (error) {
      clearItemProgress(row);
      showStatus(error.message, "error");
    }
  }

  async function deleteItem(item) {
    await archiveItem(item, true);
  }

  if (addForm && canCreate) {
    setImageUrlFields(newImageUrls);
    addImageUrlButton.addEventListener("click", () => {
      const urls = [...newImageUrls.querySelectorAll('input[name="image_urls"]')].map(field => field.value);
      urls.push("");
      setImageUrlFields(newImageUrls, urls);
    });
    addForm.addEventListener("submit", async event => {
      event.preventDefault();
      const input = addForm.elements.text;
      const imageUrls = [...newImageUrls.querySelectorAll('input[name="image_urls"]')]
        .map(field => field.value);
      try {
        await apiRequest("/api/red-book/items", {
          method: "POST",
          body: JSON.stringify({ text: input.value, image_urls: imageUrls })
        });
        input.value = "";
        setImageUrlFields(newImageUrls);
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
      if (window.location.hash.startsWith("#red-book-item-")) {
        document.getElementById(window.location.hash.slice(1))?.scrollIntoView();
      }
    } catch (error) {
      showStatus(error.message, "error");
    }
  }

  window.MannatRedBookContextMenu.bind(list, {
    getActions(row) {
      const index = items.findIndex(item => item.id === row.dataset.itemId);
      const item = items[index];
      if (!item) return [];
      const actions = [{
        action: "pin",
        label: item.pinned ? "Unpin" : "Pin It"
      }];
      if (canEdit) {
        actions.push(
          { action: "edit", label: "Edit", disabled: orderIsSaving },
          { action: "move-up", label: "Move Up", disabled: orderIsSaving || index === 0 },
          { action: "move-down", label: "Move Down", disabled: orderIsSaving || index === items.length - 1 }
        );
      }
      if (canCreate) actions.push({ action: "duplicate", label: "Duplicate", disabled: orderIsSaving });
      if (getImageUrls(item).length) actions.push({ action: "view-image", label: "View Image" });
      actions.push({ action: "copy-text", label: "Copy Text" });
      if (canArchive) actions.push({ action: "archive", label: "Move to Archive", disabled: orderIsSaving });
      if (canDelete) actions.push({ action: "delete", label: "Delete", disabled: orderIsSaving });
      return actions;
    },
    async onAction(action, row) {
      const item = items.find(candidate => candidate.id === row?.dataset.itemId);
      if (!item) return;
      const content = row.querySelector(".red-book-content");
      if (action === "pin") {
        const pinned = !item.pinned;
        try {
          await apiRequest(`/api/red-book/items/${encodeURIComponent(item.id)}/pin`, {
            method: "PUT",
            body: JSON.stringify({ pinned })
          });
          item.pinned = pinned;
          row.classList.toggle("red-book-item-pinned", pinned);
          showStatus(pinned ? "Instruction pinned to the Dashboard." : "Instruction unpinned.");
        } catch (error) {
          showStatus(error.message, "error");
        }
      } else if (action === "edit") {
        editItem(item, content);
      } else if (action === "duplicate") {
        try {
          await apiRequest("/api/red-book/items", {
            method: "POST",
            body: JSON.stringify({ text: item.text, image_urls: getImageUrls(item) })
          });
          await loadItems();
          showStatus("Red Book item duplicated.");
        } catch (error) {
          showStatus(error.message, "error");
        }
      } else if (action === "move-up") {
        await moveItem(item.id, -1);
      } else if (action === "move-down") {
        await moveItem(item.id, 1);
      } else if (action === "view-image") {
        const imageLink = document.createElement("a");
        imageLink.href = getImageUrls(item)[0];
        imageLink.target = "_blank";
        imageLink.rel = "noopener noreferrer";
        imageLink.click();
      } else if (action === "copy-text") {
        try {
          await navigator.clipboard.writeText(item.text);
          showStatus("Instruction text copied.");
        } catch (error) {
          showStatus("Could not copy the instruction text.", "error");
        }
      } else if (action === "archive") {
        await archiveItem(item);
      } else if (action === "delete") {
        await deleteItem(item);
      }
    }
  });

  loadItems();
  window.setInterval(async () => {
    if (document.hidden || orderIsSaving || isRefreshingPinStates) return;
    isRefreshingPinStates = true;
    try {
      const payload = await apiRequest("/api/red-book/items");
      const pinStates = new Map(payload.items.map(item => [item.id, item.pinned === true]));
      items.forEach(item => {
        item.pinned = pinStates.get(item.id) === true;
        document.getElementById(`red-book-item-${item.id}`)
          ?.classList.toggle("red-book-item-pinned", item.pinned);
      });
    } catch (error) {
      showStatus(error.message, "error");
    } finally {
      isRefreshingPinStates = false;
    }
  }, 10000);
})();
