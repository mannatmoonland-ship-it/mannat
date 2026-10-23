(() => {
  const ICON_PATHS = {
    "Pin It": "M16 9V4l1-1V2H7v1l1 1v5l-3 3v2h6v8l1 1 1-1v-8h6v-2l-3-3z",
    "Unpin": "M2 3.3 3.3 2l18.7 18.7-1.3 1.3-6.2-6.2H13v5l-1 1-1-1v-5H5v-2l3-3V7.2L2 3.3zM16 9V4l1-1V2H7v1l1 1v1.2l8 8V12h3v-2l-3-1z",
    "Edit": "M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z",
    "Move Up": "M4 12l1.4 1.4L11 7.8V20h2V7.8l5.6 5.6L20 12l-8-8-8 8z",
    "Move Down": "M20 12l-1.4-1.4L13 16.2V4h-2v12.2l-5.6-5.6L4 12l8 8 8-8z",
    "Duplicate": "M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z",
    "Copy Text": "M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2z",
    "Copy task": "M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2z",
    "View Image": "M21 19V5H3v14h18zM5 7h14v10H5V7zm1 8 3-4 2 2 3-4 4 6H6z",
    "Move to Archive": "M20 6h-2.2l-1.2-2H7.4L6.2 6H4v14h16V6zm-8 11-4-4h3v-3h2v3h3l-4 4z",
    "Mark as done": "M9 16.2 4.8 12 3.4 13.4 9 19l12-12-1.4-1.4z",
    "Restore to Red Book": "M12 5V1L7 6l5 5V7c3.3 0 6 2.7 6 6a6 6 0 0 1-10.9 3.5l-1.5 1.3A8 8 0 1 0 12 5z",
    "Restore to to-do list": "M12 5V1L7 6l5 5V7c3.3 0 6 2.7 6 6a6 6 0 0 1-10.9 3.5l-1.5 1.3A8 8 0 1 0 12 5z",
    "Delete": "M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z",
    "Delete permanently": "M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z",
    "Delete from archive": "M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"
  };

  function bind(root, { getActions, onAction, longPressMs = 600 }) {
    let menu;
    let menuTarget;
    let activePress;
    let suppressClickUntil = 0;

    function close({ restoreFocus = false } = {}) {
      const target = menuTarget;
      if (menu) menu.remove();
      menu = null;
      menuTarget = null;
      if (restoreFocus && target?.isConnected) target.focus();
    }

    function getItem(target) {
      if (!(target instanceof Element)) return null;
      const item = target.closest("[data-context-item-id]");
      return item && root.contains(item) ? item : null;
    }

    function interactiveTarget(target) {
      return target.closest("input, textarea, select, [contenteditable='true']");
    }

    function open(item, x, y) {
      const actions = getActions(item);
      if (!actions.length) return;
      close();
      menuTarget = item;
      menu = document.createElement("div");
      menu.className = "red-book-context-menu";
      menu.setAttribute("role", "menu");
      menu.setAttribute("aria-label", "Site instruction actions");
      menu.style.left = "0px";
      menu.style.top = "0px";
      actions.forEach(({ label, action, disabled = false }, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "red-book-context-menu-item";
        button.setAttribute("role", "menuitem");
        const iconPath = ICON_PATHS[label];
        if (iconPath) {
          const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
          icon.setAttribute("viewBox", "0 0 24 24");
          icon.setAttribute("aria-hidden", "true");
          icon.classList.add("red-book-context-menu-icon");
          const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
          path.setAttribute("d", iconPath);
          icon.append(path);
          button.append(icon, document.createTextNode(label));
        } else button.textContent = label;
        button.dataset.menuIndex = String(index);
        button.disabled = disabled;
        button.addEventListener("click", event => {
          event.stopPropagation();
          const selectedItem = menuTarget;
          close();
          if (typeof action === "function") action(selectedItem);
          else onAction(action, selectedItem);
        });
        menu.append(button);
      });
      menu.addEventListener("keydown", event => {
        menu.querySelector("[data-menu-initial-focus]")?.removeAttribute("data-menu-initial-focus");
        const buttons = [...menu.querySelectorAll("button:not(:disabled)")];
        const index = buttons.indexOf(document.activeElement);
        let nextIndex;
        if (event.key === "ArrowDown") nextIndex = (index + 1) % buttons.length;
        else if (event.key === "ArrowUp") nextIndex = (index - 1 + buttons.length) % buttons.length;
        else if (event.key === "Home") nextIndex = 0;
        else if (event.key === "End") nextIndex = buttons.length - 1;
        else return;
        event.preventDefault();
        buttons[nextIndex]?.focus();
      });
      document.body.append(menu);
      requestAnimationFrame(() => {
        if (!menu) return;
        const bounds = menu.getBoundingClientRect();
        menu.style.left = `${Math.max(6, Math.min(x, window.innerWidth - bounds.width - 6))}px`;
        menu.style.top = `${Math.max(6, Math.min(y, window.innerHeight - bounds.height - 6))}px`;
        const firstEnabledButton = menu.querySelector("button:not(:disabled)");
        if (firstEnabledButton) {
          firstEnabledButton.dataset.menuInitialFocus = "true";
          firstEnabledButton.focus();
        }
      });
    }

    function cancelPress() {
      if (activePress?.timer) window.clearTimeout(activePress.timer);
      activePress = null;
    }

    root.addEventListener("contextmenu", event => {
      const item = getItem(event.target);
      if (!item || interactiveTarget(event.target)) return;
      event.preventDefault();
      if (activePress?.item === item) cancelPress();
      open(item, event.clientX, event.clientY);
    });

    root.addEventListener("pointerdown", event => {
      if (!["touch", "pen"].includes(event.pointerType) || !event.isPrimary) return;
      if (!activePress) suppressClickUntil = 0;
      const item = getItem(event.target);
      if (!item || interactiveTarget(event.target)) return;
      cancelPress();
      const press = {
        item,
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        timer: window.setTimeout(() => {
          if (activePress !== press) return;
          suppressClickUntil = Date.now() + 1000;
          open(item, event.clientX, event.clientY);
          press.timer = null;
        }, longPressMs)
      };
      activePress = press;
    });

    document.addEventListener("pointermove", event => {
      if (!activePress || event.pointerId !== activePress.pointerId) return;
      if (Math.hypot(event.clientX - activePress.x, event.clientY - activePress.y) > 10) {
        cancelPress();
      }
    }, { passive: true });
    document.addEventListener("pointerup", event => {
      if (activePress && event.pointerId === activePress.pointerId) cancelPress();
    }, { passive: true });
    document.addEventListener("pointercancel", event => {
      if (activePress?.pointerId === event.pointerId) cancelPress();
    }, { passive: true });
    document.addEventListener("scroll", cancelPress, { capture: true, passive: true });

    root.addEventListener("click", event => {
      if (Date.now() < suppressClickUntil && getItem(event.target) === menuTarget) {
        event.preventDefault();
        event.stopImmediatePropagation();
        suppressClickUntil = 0;
      }
    }, true);

    document.addEventListener("pointerdown", event => {
      if (menu && !menu.contains(event.target)) close();
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && menu) {
        event.preventDefault();
        close({ restoreFocus: true });
        return;
      }
      if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
      const item = getItem(document.activeElement);
      if (!item) return;
      event.preventDefault();
      const bounds = item.getBoundingClientRect();
      open(item, bounds.left + 24, bounds.top + 24);
    });

    const cleanup = () => {
      cancelPress();
      close();
    };
    window.addEventListener("pagehide", cleanup, { once: true });
    return { close: cleanup };
  }

  window.MannatRedBookContextMenu = { bind };
})();
