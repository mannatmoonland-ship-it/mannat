import { firebaseAuthReady, getFirebaseApp, getFirebaseFirestoreSdk } from "./firebase_auth.js";

(() => {
  const section = document.getElementById("pinnedInstructions");
  if (!section) return;
  const hero = document.getElementById("dashboardHero");
  const grid = document.getElementById("pinnedInstructionsGrid");
  const empty = document.getElementById("pinnedInstructionsEmpty");
  const status = document.getElementById("pinnedInstructionsStatus");
  const csrfToken = section.dataset.redBookCsrf || "";
  let items;
  let pendingRevision = null;
  let revisionRefreshInFlight = false;

  function showStatus(message, kind = "success") {
    status.textContent = message;
    status.dataset.kind = kind;
  }

  async function request(path, options = {}) {
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
    let payload;
    try {
      payload = await response.json();
    } catch (error) {
      throw new Error("The pinned instructions service returned an invalid response.");
    }
    if (!response.ok) {
      const error = new Error(payload.error || "Unable to load pinned site instructions.");
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function render() {
    grid.replaceChildren();
    hero.hidden = items.length !== 0;
    section.hidden = items.length === 0;
    empty.hidden = true;
    if (items.length === 0) {
      status.textContent = "";
      delete status.dataset.kind;
    }
    items.forEach(item => {
      const card = document.createElement("article");
      card.className = "pinned-instruction-card";
      card.dataset.contextItemId = item.id;
      card.tabIndex = 0;

      const text = document.createElement("p");
      text.className = "pinned-instruction-text";
      text.textContent = item.text;
      card.append(text);
      window.MannatRedBookImages.appendImages(
        card,
        Array.isArray(item.image_urls) ? item.image_urls : (item.image_url ? [item.image_url] : [])
      );

      grid.append(card);
    });
  }

  function imageAction(item, action) {
    if (action === "view-image") {
      const link = document.createElement("a");
      link.href = item.image_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.click();
      return;
    }
    if (action === "open") {
      window.location.href = `/red-book#red-book-item-${encodeURIComponent(item.id)}`;
    }
  }

  const contextMenu = window.MannatRedBookContextMenu.bind(grid, {
    getActions(card) {
      const item = items.find(candidate => candidate.id === card.dataset.contextItemId);
      if (!item) return [];
      const actions = [
        { action: "unpin", label: "Unpin" },
        { action: "open", label: "Open in Red Book" },
        { action: "copy-text", label: "Copy Text" }
      ];
      if (item.image_urls?.length || item.image_url) actions.push({ action: "view-image", label: "View Image" });
      return actions;
    },
    async onAction(action, card) {
      const item = items.find(candidate => candidate.id === card?.dataset.contextItemId);
      if (!item) return;
      if (action === "copy-text") {
        try {
          await navigator.clipboard.writeText(item.text);
          showStatus("Instruction text copied.");
        } catch (error) {
          showStatus("Could not copy the instruction text.", "error");
        }
      } else if (action === "unpin") {
        try {
          await request(`/api/red-book/items/${encodeURIComponent(item.id)}/pin`, {
            method: "PUT",
            body: JSON.stringify({ pinned: false })
          });
          items = items.filter(candidate => candidate.id !== item.id);
          render();
          showStatus("Instruction unpinned.");
        } catch (error) {
          showStatus(error.message, "error");
        }
      } else {
        imageAction(item, action);
      }
    }
  });

  async function refreshPinnedItems(revision) {
    if (pendingRevision) {
      const refreshAll = pendingRevision.refresh_all || revision.refresh_all;
      pendingRevision = {
        refresh_all: refreshAll,
        item_ids: refreshAll
          ? []
          : [...new Set([...(pendingRevision.item_ids || []), ...(revision.item_ids || [])])],
        deleted_ids: [...new Set([...(pendingRevision.deleted_ids || []), ...(revision.deleted_ids || [])])]
      };
    } else {
      pendingRevision = revision;
    }
    if (revisionRefreshInFlight) return;
    revisionRefreshInFlight = true;
    try {
      while (pendingRevision) {
        const next = pendingRevision;
        pendingRevision = null;
        if (next.refresh_all || !next.item_ids?.length && !next.deleted_ids?.length) {
          const payload = await request("/api/red-book/pinned");
          items = payload.items;
        } else {
          for (const id of next.deleted_ids || []) {
            items = items.filter(item => item.id !== id);
          }
          for (const id of next.item_ids || []) {
            try {
              const payload = await request(`/api/red-book/items/${encodeURIComponent(id)}`);
              items = [...items.filter(item => item.id !== id), payload.item]
                .filter(item => item.pinned === true);
            } catch (error) {
              if (error.status === 404) items = items.filter(item => item.id !== id);
              else throw error;
            }
          }
          items.sort((a, b) => Number(a.sort_order ?? Number.MAX_SAFE_INTEGER) - Number(b.sort_order ?? Number.MAX_SAFE_INTEGER)
            || String(a.id).localeCompare(String(b.id)));
        }
        contextMenu.close();
        render();
        status.textContent = "";
        delete status.dataset.kind;
      }
    } catch (error) {
      showStatus(error.message, "error");
    } finally {
      revisionRefreshInFlight = false;
      if (pendingRevision) void refreshPinnedItems(pendingRevision);
    }
  }

  async function subscribe() {
    try {
      await firebaseAuthReady;
      const firestore = await getFirebaseFirestoreSdk();
      const db = firestore.getFirestore(await getFirebaseApp());
      let isInitialSnapshot = true;
      const unsubscribe = firestore.onSnapshot(
        firestore.doc(db, "sync_metadata", "red-book-items"),
        snapshot => {
          const revision = isInitialSnapshot
            ? { refresh_all: true, item_ids: [], deleted_ids: [] }
            : snapshot.exists()
              ? {
                  refresh_all: snapshot.data().refresh_all === true,
                  item_ids: snapshot.data().item_id && !snapshot.data().deleted ? [snapshot.data().item_id] : [],
                  deleted_ids: snapshot.data().item_id && snapshot.data().deleted ? [snapshot.data().item_id] : []
                }
              : { refresh_all: true, item_ids: [], deleted_ids: [] };
          isInitialSnapshot = false;
          void refreshPinnedItems(revision);
        },
        error => {
        console.error("Unable to subscribe to pinned Red Book instructions:", error?.code || error?.name);
        showStatus("Pinned site instructions could not receive live updates.", "error");
        void refreshPinnedItems({ refresh_all: true, item_ids: [], deleted_ids: [] });
      });
      window.addEventListener("pagehide", unsubscribe, { once: true });
    } catch (error) {
      console.error("Unable to initialize pinned-instruction updates:", error?.stage || error?.code || error?.name);
      showStatus("Pinned site instructions could not receive live updates.", "error");
    }
  }

  try {
    items = JSON.parse(document.getElementById("pinnedInstructionsData").textContent);
    if (!Array.isArray(items)) throw new Error("Pinned instructions data must be a list.");
    render();
  } catch (error) {
    items = [];
    render();
    showStatus("Pinned site instructions could not be displayed.", "error");
  }

  void subscribe();
  window.addEventListener("pagehide", () => contextMenu.close(), { once: true });
})();
