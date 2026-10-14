(() => {
  const section = document.getElementById("pinnedInstructions");
  if (!section) return;
  const hero = document.getElementById("dashboardHero");
  const grid = document.getElementById("pinnedInstructionsGrid");
  const empty = document.getElementById("pinnedInstructionsEmpty");
  const status = document.getElementById("pinnedInstructionsStatus");
  const csrfToken = section.dataset.redBookCsrf || "";
  let items;
  let isMutating = false;
  let isRefreshing = false;

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
      throw new Error(payload.error || "Unable to load pinned site instructions.");
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
      window.MannatRedBookImages.appendImage(card, item.image_url);

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
      if (item.image_url) actions.push({ action: "view-image", label: "View Image" });
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
        isMutating = true;
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
        } finally {
          isMutating = false;
        }
      } else {
        imageAction(item, action);
      }
    }
  });

  async function refresh() {
    if (document.hidden || isMutating || isRefreshing) return;
    isRefreshing = true;
    try {
      const payload = await request("/api/red-book/pinned");
      if (isMutating) return;
      const nextItems = payload.items;
      if (JSON.stringify(items) !== JSON.stringify(nextItems)) {
        items = nextItems;
        contextMenu.close();
        render();
      }
      status.textContent = "";
      delete status.dataset.kind;
    } catch (error) {
      showStatus(error.message, "error");
    } finally {
      isRefreshing = false;
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

  refresh();
  const refreshTimer = window.setInterval(refresh, 10000);
  document.addEventListener("visibilitychange", refresh);
  window.addEventListener("pagehide", () => {
    window.clearInterval(refreshTimer);
    contextMenu.close();
  }, { once: true });
})();
