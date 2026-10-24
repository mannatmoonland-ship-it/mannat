import { firebaseAuthReady, getFirebaseApp, getFirebaseFirestoreSdk } from "./firebase_auth.js";

(() => {
  const list = document.getElementById("redBookArchiveItems");
  const status = document.getElementById("redBookArchiveStatus");
  const empty = document.getElementById("redBookArchiveEmpty");
  const csrfToken = document.body.dataset.redBookCsrf || "";
  const canRestore = document.body.dataset.canRestore === "true";
  const canDeleteArchived = document.body.dataset.canDeleteArchived === "true";
  const restoreDialog = document.getElementById("redBookRestoreDialog");
  const deleteDialog = document.getElementById("redBookArchiveDeleteDialog");
  const deleteDialogItem = document.getElementById("redBookArchiveDeleteItem");
  const restoreDialogItem = document.getElementById("redBookRestoreDialogItem");
  let archivedItems = [];
  let archiveLoadInFlight = false;
  let archiveReloadQueued = false;

  async function loadArchive() {
    if (archiveLoadInFlight) {
      archiveReloadQueued = true;
      return;
    }
    archiveLoadInFlight = true;
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
    } finally {
      archiveLoadInFlight = false;
      if (archiveReloadQueued) {
        archiveReloadQueued = false;
        void loadArchive();
      }
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

  function confirmPermanentDelete(item) {
    deleteDialogItem.textContent = item.text;
    return new Promise(resolve => {
      deleteDialog.addEventListener("close", () => {
        resolve(deleteDialog.returnValue === "delete");
      }, { once: true });
      deleteDialog.showModal();
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
      if (!archivedItems.some(item => item.id === row.dataset.itemId)) return [];
      const actions = [];
      if (canRestore) actions.push({ action: "restore", label: "Restore to Red Book" });
      if (canDeleteArchived) actions.push({ action: "delete", label: "Delete permanently" });
      return actions;
    },
    async onAction(action, row) {
      if (!["restore", "delete"].includes(action)) return;
      const item = archivedItems.find(candidate => candidate.id === row?.dataset.itemId);
      if (!item) return;
      if (action === "restore" && !(await confirmRestore(item))) return;
      if (action === "delete" && !(await confirmPermanentDelete(item))) return;
      const restoring = action === "restore";
      setRestoreProgress(row, restoring ? "Restoring to Red Book..." : "Deleting permanently...");
      status.dataset.kind = "success";
      status.textContent = restoring ? "Restoring item to the Red Book..." : "Deleting archived item...";
      try {
        const response = await fetch(restoring
          ? `/api/red-book/archive/${encodeURIComponent(item.id)}/restore`
          : `/api/red-book/archive/${encodeURIComponent(item.id)}`, {
          method: restoring ? "POST" : "DELETE",
          headers: { "Accept": "application/json", "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
          body: JSON.stringify({}),
          cache: "no-store",
          credentials: "same-origin"
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || `Unable to ${restoring ? "restore" : "delete"} the item.`);
        status.dataset.kind = "success";
        status.textContent = restoring
          ? "Item restored to the Red Book."
          : "Archived item permanently deleted.";
      } catch (error) {
        clearRestoreProgress(row);
        status.dataset.kind = "error";
        status.textContent = error.message || "Unable to restore the item.";
      }
    }
  });

  async function subscribeArchive() {
    try {
      await firebaseAuthReady;
      const firestore = await getFirebaseFirestoreSdk();
      const db = firestore.getFirestore(await getFirebaseApp());
      const unsubscribe = firestore.onSnapshot(
        firestore.doc(db, "sync_metadata", "red-book-archive"),
        () => {
          contextMenu.close();
          void loadArchive();
        },
        error => {
          console.error("Unable to subscribe to Red Book archive revisions:", error?.code || error?.name);
          status.dataset.kind = "error";
          status.textContent = "Archived entries could not receive live updates.";
          void loadArchive();
        }
      );
      window.addEventListener("pagehide", unsubscribe, { once: true });
    } catch (error) {
      console.error("Unable to initialize Red Book archive live updates:", error?.stage || error?.code || error?.name);
      status.dataset.kind = "error";
      status.textContent = "Archived entries could not receive live updates.";
      await loadArchive();
    }
  }

  void subscribeArchive();
})();
