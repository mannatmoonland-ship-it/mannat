const dialogQueue = [];
let activeDialog = null;
const registeredEscClosers = new Map();
let globalEscHandlerAttached = false;

function isEscVisible(element) {
  if (!(element instanceof HTMLElement)) {
    return false;
  }

  if (element.hidden) {
    return false;
  }

  const computedStyle = window.getComputedStyle(element);
  if (computedStyle.display === "none" || computedStyle.visibility === "hidden") {
    return false;
  }

  if (element.classList.contains("dr-hidden") || element.classList.contains("hidden")) {
    return false;
  }

  if (element.classList.contains("modal") && !element.classList.contains("show")) {
    return false;
  }

  if (element.classList.contains("dr-modal") && element.classList.contains("dr-hidden")) {
    return false;
  }

  return true;
}

function handleGlobalEsc(event) {
  if (event.key !== "Escape" || event.defaultPrevented) {
    return;
  }

  if (activeDialog) {
    return;
  }

  const visibleClosers = [...registeredEscClosers.keys()]
    .filter(isEscVisible);

  const topmost = visibleClosers[visibleClosers.length - 1];
  if (!topmost) {
    return;
  }

  const closer = registeredEscClosers.get(topmost);
  if (typeof closer?.onClose === "function") {
    event.preventDefault();
    closer.onClose();
  }
}

function ensureGlobalEscHandler() {
  if (globalEscHandlerAttached) {
    return;
  }

  document.addEventListener("keydown", handleGlobalEsc);
  globalEscHandlerAttached = true;
}

export function registerEscClose(element, onClose) {
  if (!(element instanceof HTMLElement) || typeof onClose !== "function") {
    return () => {};
  }

  unregisterEscClose(element);
  registeredEscClosers.set(element, { onClose });
  ensureGlobalEscHandler();

  return () => unregisterEscClose(element);
}

export function unregisterEscClose(element) {
  registeredEscClosers.delete(element);
}

function ensureDialogRoot() {
  let root = document.getElementById("customDialogRoot");
  if (root) return root;
  root = document.createElement("div");
  root.id = "customDialogRoot";
  root.className = "custom-dialog-root";
  document.body.appendChild(root);
  return root;
}

function showDialog({ message, title, type, input, defaultValue, details, confirmText, cancelText }) {
  return new Promise(resolve => {
    dialogQueue.push({ message, title, type, input, defaultValue, details, confirmText, cancelText, resolve });
    renderNextDialog();
  });
}

function renderNextDialog() {
  if (activeDialog || !dialogQueue.length) return;

  activeDialog = dialogQueue.shift();
  const dialog = activeDialog;
  const root = ensureDialogRoot();
  const inputId = "customDialogInput";
  const title = dialog.title || (dialog.type === "error" ? "Error" : dialog.type === "success" ? "Success" : "Mannat Moon");
  const isConfirmation = dialog.type === "confirm" || dialog.type === "danger";
  const hasCancel = isConfirmation || dialog.input;

  root.innerHTML = `
    <div class="custom-dialog-overlay">
      <section class="custom-dialog custom-dialog-${dialog.type}${dialog.type === "danger" ? " custom-dialog-danger" : ""}" role="dialog" aria-modal="true" aria-labelledby="customDialogTitle" aria-describedby="customDialogMessage">
        <button type="button" class="custom-dialog-close" aria-label="Close dialog">&times;</button>
        <h2 id="customDialogTitle">${escapeHtml(title)}</h2>
        <div id="customDialogMessage" class="custom-dialog-message">${escapeHtml(dialog.message)}</div>
        ${dialog.details ? `<div class="custom-dialog-details">${escapeHtml(dialog.details)}</div>` : ""}
        ${dialog.input ? `<input id="${inputId}" class="custom-dialog-input" type="text" value="${escapeHtml(dialog.defaultValue || "")}" aria-label="Dialog input">` : ""}
        <div class="custom-dialog-actions">
          ${hasCancel ? `<button type="button" class="custom-dialog-cancel">${escapeHtml(dialog.cancelText || "Cancel")}</button>` : ""}
          <button type="button" class="custom-dialog-confirm${dialog.type === "danger" ? " custom-dialog-danger-button" : ""}">${escapeHtml(dialog.confirmText || "OK")}</button>
        </div>
      </section>
    </div>
  `;

  const inputElement = root.querySelector(`#${inputId}`);
  const confirmButton = root.querySelector(".custom-dialog-confirm");
  const cancelButton = root.querySelector(".custom-dialog-cancel");
  const closeButton = root.querySelector(".custom-dialog-close");

  const finish = value => {
    if (!activeDialog || activeDialog !== dialog) return;
    document.removeEventListener("keydown", onKeyDown);
    unregisterDialogEscIfNeeded();
    root.innerHTML = "";
    const resolve = activeDialog.resolve;
    activeDialog = null;
    resolve(value);
    renderNextDialog();
  };

  const cancel = () => finish(dialog.input ? null : false);
  const submitDialog = () => finish(dialog.input ? inputElement.value : true);

  registerEscClose(root, cancel);

  function onKeyDown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      cancel();
    } else if (event.key === "Enter" && (dialog.input || document.activeElement === confirmButton)) {
      event.preventDefault();
      submitDialog();
    } else if (event.key === "Tab") {
      const focusable = [...root.querySelectorAll("button, input")];
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  }

  confirmButton.addEventListener("click", submitDialog);
  cancelButton?.addEventListener("click", cancel);
  closeButton.addEventListener("click", cancel);
  root.querySelector(".custom-dialog-overlay").addEventListener("click", event => {
    if (event.target === event.currentTarget) cancel();
  });
  document.addEventListener("keydown", onKeyDown);
  (inputElement || confirmButton).focus();
  inputElement?.select();
}

function unregisterDialogEscIfNeeded() {
  const root = document.getElementById("customDialogRoot");
  if (root) {
    unregisterEscClose(root);
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  })[character]);
}

export function showAlert(message, title) {
  return showDialog({ message, title, type: "alert" });
}

export function showConfirm(message, title) {
  return showConfirmDialog({ message, title });
}

export function showConfirmDialog({ title = "Mannat Moon", message, details = "", confirmText = "Confirm", cancelText = "Cancel", type = "confirm" }) {
  return showDialog({ message, title, details, confirmText, cancelText, type });
}

export function showPrompt(message, defaultValue = "", title, confirmText = "OK", cancelText = "Cancel") {
  return showDialog({ message, defaultValue, title, type: "input", input: true, confirmText, cancelText });
}

export function showSuccess(message, title) {
  return showDialog({ message, title, type: "success" });
}

export function showError(message, title) {
  return showDialog({ message, title, type: "error" });
}

Object.assign(window, { showAlert, showConfirm, showConfirmDialog, showPrompt, showSuccess, showError, registerEscClose, unregisterEscClose });