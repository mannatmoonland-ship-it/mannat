(() => {
  const list = document.getElementById("redBookArchiveItems");
  const status = document.getElementById("redBookArchiveStatus");
  const empty = document.getElementById("redBookArchiveEmpty");
  const csrfToken = document.body.dataset.redBookCsrf || "";
  const canRestore = document.body.dataset.canRestore === "true";
  const restoreDialog = document.getElementById("redBookRestoreDialog");
  const restoreDialogItem = document.getElementById("redBookRestoreDialogItem");
  let archivedItems = [];

  async function loadArchive() {
    try {
      const response = await fetch("/api/red-book/archive", {
        headers: { "Accept": "application/json" },
        cache: "no-store",
        credentials: "same-origin"
      });
      let payload;
      try { payload = await response.json(); }
      catch { throw new Error("The Archive service returned an invalid response."); }
      if (!response.ok) throw new Error(payload.error || "Unable to load archived entries.");

      const items = Array.isArray(payload.items) ? payload.items : [];
      archivedItems = items;
      list.replaceChildren();
      empty.hidden = items.length > 0;
      status.textContent = items.length ? `${items.length} archived ${items.length === 1 ? "entry" : "entries"}` : "";

      items.forEach((item, index) => {
        const row = document.createElement("li");
        row.className = "red-book-item red-book-archived-item";
        row.id = `red-book-item-${item.id}`;
        row.dataset.contextItemId = item.id;
        row.dataset.itemId = item.id;
        row.tabIndex = 0;
        row.setAttribute("aria-label", `Archived Red Book item: ${item.text}`);
        const number = document.createElement("span");
        number.className = "red-book-number";
        number.textContent = `${index + 1}.`;

        const content = document.createElement("div");
        content.className = "red-book-content";
        const text = document.createElement("span");
        text.className = "red-book-text";
        text.textContent = typeof item.text === "string" ? item.text : "";
        content.append(text);
        const imageUrls = Array.isArray(item.image_urls)
          ? item.image_urls
          : (item.image_url ? [item.image_url] : []);
        window.MannatRedBookImages.appendImages(content, imageUrls);

        const archivedDate = document.createElement("small");
        archivedDate.className = "red-book-archived-date";
        const date = item.archived_at ? new Date(item.archived_at) : null;
        const archivedBy = typeof item.archived_by_name === "string" && item.archived_by_name.trim()
          ? item.archived_by_name.trim()
          : "Unknown user";
        archivedDate.textContent = `${date && !Number.isNaN(date.valueOf())
          ? `Archived ${date.toLocaleString()}`
          : "Archived"} by ${archivedBy}`;
        row.append(number, content, archivedDate);
        list.append(row);
      });
    } catch (error) {
      status.dataset.kind = "error";
      status.textContent = error.message || "Unable to load archived entries.";
    }
  }

  function confirmRestore(item) {
    restoreDialogItem.textContent = item.text;
    return new Promise(resolve => {
      restoreDialog.addEventListener("close", () => {
        resolve(restoreDialog.returnValue === "restore");
      }, { once: true });
      restoreDialog.showModal();
    });
  }

  function setRestoreProgress(row, message) {
    row.setAttribute("aria-busy", "true");
    const progress = document.createElement("div");
    progress.className = "red-book-item-progress";
    progress.setAttribute("role", "status");
    progress.setAttribute("aria-live", "polite");
    progress.textContent = message;
    row.append(progress);
  }

  function clearRestoreProgress(row) {
    row.removeAttribute("aria-busy");
    row.querySelector(".red-book-item-progress")?.remove();
  }

  window.MannatRedBookContextMenu.bind(list, {
    getActions(row) {
      return canRestore && archivedItems.some(item => item.id === row.dataset.itemId)
        ? [{ action: "restore", label: "Restore to Red Book" }]
        : [];
    },
    async onAction(action, row) {
      if (action !== "restore") return;
      const item = archivedItems.find(candidate => candidate.id === row?.dataset.itemId);
      if (!item || !(await confirmRestore(item))) return;
      setRestoreProgress(row, "Restoring to Red Book...");
      status.dataset.kind = "success";
      status.textContent = "Restoring item to the Red Book...";
      try {
        const response = await fetch(`/api/red-book/archive/${encodeURIComponent(item.id)}/restore`, {
          method: "POST",
          headers: { "Accept": "application/json", "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
          body: JSON.stringify({}),
          cache: "no-store",
          credentials: "same-origin"
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "Unable to restore the item.");
        await loadArchive();
        status.dataset.kind = "success";
        status.textContent = "Item restored to the Red Book.";
      } catch (error) {
        clearRestoreProgress(row);
        status.dataset.kind = "error";
        status.textContent = error.message || "Unable to restore the item.";
      }
    }
  });

  loadArchive();
})();
