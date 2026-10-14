(() => {
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
        button.textContent = label;
        button.dataset.menuIndex = String(index);
        button.disabled = disabled;
        button.addEventListener("click", event => {
          event.stopPropagation();
          const selectedItem = menuTarget;
          close();
          onAction(action, selectedItem);
        });
        menu.append(button);
      });
      menu.addEventListener("keydown", event => {
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
        menu.querySelector("button:not(:disabled)")?.focus();
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
      open(item, event.clientX, event.clientY);
    });

    root.addEventListener("pointerdown", event => {
      if (!["touch", "pen"].includes(event.pointerType) || !event.isPrimary) return;
      if (!activePress) suppressClickUntil = 0;
      const item = getItem(event.target);
      if (!item || interactiveTarget(event.target)) return;
      cancelPress();
      activePress = {
        item,
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        timer: window.setTimeout(() => {
          if (!activePress) return;
          suppressClickUntil = Date.now() + 1000;
          open(item, event.clientX, event.clientY);
          activePress.timer = null;
        }, longPressMs)
      };
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
    document.addEventListener("pointercancel", cancelPress, { passive: true });
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
