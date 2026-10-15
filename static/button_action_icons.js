(() => {
  const icons = [
    { pattern: /\b(delete|remove|discard|clear)\b/i, path: '<path d="M4 7h16M10 11v6m4-6v6M6 7l1 14h10l1-14M9 7V4h6v3"/>' },
    { pattern: /\b(cancel|close|dismiss)\b/i, path: '<path d="m6 6 12 12M18 6 6 18"/>' },
    { pattern: /\b(camera|photo|image|picture|avatar)\b/i, path: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8.5" cy="10" r="1.5"/><path d="m5 17 5-5 3 3 2-2 4 4"/>' },
    { pattern: /\b(phone|mobile|sms|call)\b/i, path: '<path d="M7 3.5h10A1.5 1.5 0 0 1 18.5 5v14a1.5 1.5 0 0 1-1.5 1.5H7A1.5 1.5 0 0 1 5.5 19V5A1.5 1.5 0 0 1 7 3.5Z"/><path d="M10 17.5h4"/>' },
    { pattern: /\b(password|sign in|log in|unlock|lock)\b/i, path: '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 1 1 8 0v3m-4 4v3"/>' },
    { pattern: /\b(save|update)\b/i, path: '<path d="M4 3h13l3 3v15H4zM8 3v6h8V3M8 21v-8h8v8"/>' },
    { pattern: /\b(print|printer)\b/i, path: '<path d="M7 8V3h10v5M7 17H5a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><path d="M7 14h10v7H7z"/>' },
    { pattern: /\b(export|pdf|report)\b/i, path: '<path d="M6 3h8l5 5v13H6zM14 3v6h5M9 14h6m-6 3h6"/>' },
    { pattern: /\b(share|publish)\b/i, path: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.7 10.7 6.6-4.4M8.7 13.3l6.6 4.4"/>' },
    { pattern: /\b(copy|duplicate|clone)\b/i, path: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>' },
    { pattern: /\b(upload|import|restore|recover)\b/i, path: '<path d="M12 16V4m-5 5 5-5 5 5M4 15v5h16v-5"/>' },
    { pattern: /\b(download|backup|save image)\b/i, path: '<path d="M12 3v12m-5-5 5 5 5-5M4 19h16"/>' },
    { pattern: /\b(connect|link|attach)\b/i, path: '<path d="M10 13.5 8.5 15a4 4 0 0 1-5.7-5.7l3-3a4 4 0 0 1 5.7 0M14 10.5l1.5-1.5a4 4 0 0 1 5.7 5.7l-3 3a4 4 0 0 1-5.7 0M8.5 15.5l7-7"/>' },
    { pattern: /\b(edit|rename|modify)\b/i, path: '<path d="m4 16.5-.8 4.3 4.3-.8L20 7.5 16.5 4 4 16.5Z"/><path d="m14.5 6 3.5 3.5"/>' },
    { pattern: /\b(add|create|new|invite)\b/i, path: '<path d="M12 5v14M5 12h14"/>' },
    { pattern: /\b(search|find)\b/i, path: '<circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 5 5"/>' },
    { pattern: /\b(filter)\b/i, path: '<path d="M3 5h18l-7 8v5l-4 3v-8L3 5Z"/>' },
    { pattern: /\b(next|continue|forward)\b/i, path: '<path d="m9 18 6-6-6-6"/>' },
    { pattern: /\b(previous|back|return)\b/i, path: '<path d="m15 18-6-6 6-6"/>' },
    { pattern: /\b(today|date|calendar|month)\b/i, path: '<rect x="3.5" y="5" width="17" height="16" rx="2"/><path d="M16 3v4M8 3v4M4 10h16"/>' },
    { pattern: /\b(verify|confirm|approve|done|apply|submit|send|check)\b/i, path: '<path d="m5 12 4 4L19 6"/>' },
    { pattern: /\b(view|open|preview|show|details)\b/i, path: '<path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z"/><circle cx="12" cy="12" r="2.5"/>' },
    { pattern: /\b(user|account|profile|staff|worker|member)\b/i, path: '<circle cx="9" cy="8" r="3"/><path d="M3.5 20a5.5 5.5 0 0 1 11 0M16 5.5a3 3 0 0 1 0 5.8M17 14a5 5 0 0 1 3.5 4.7"/>' }
  ];

  function buttonLabel(button) {
    return [
      button.getAttribute("aria-label"),
      button.getAttribute("title"),
      button.dataset.action,
      button.id.replace(/([a-z])([A-Z])/g, "$1 $2"),
      button.textContent
    ].filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
  }

  function addIcon(button) {
    if (!(button instanceof HTMLButtonElement) || button.querySelector("svg")) return;
    if (button.closest(".mm-nav-list, .dr-calendar-days, .mm-nav-profile-trigger")) return;

    const label = buttonLabel(button);
    const match = icons.find(icon => icon.pattern.test(label));
    if (!match) return;

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", "18");
    svg.setAttribute("height", "18");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    svg.style.cssText = "display:inline-block;flex:0 0 18px;vertical-align:middle;margin-right:.45em;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round";
    svg.innerHTML = match.path;
    button.insertBefore(svg, button.firstChild);
    button.classList.add("mm-has-action-icon");
    const tooltip = button.getAttribute("aria-label") || button.textContent.trim();
    if (!button.hasAttribute("title") && tooltip) button.title = tooltip;
  }

  function scan(root) {
    if (!root) return;
    if (root instanceof HTMLButtonElement) addIcon(root);
    if (root.querySelectorAll) root.querySelectorAll("button").forEach(addIcon);
  }

  scan(document);
  new MutationObserver(mutations => {
    mutations.forEach(mutation => {
      if (mutation.type === "childList") {
        scan(mutation.target instanceof Element ? mutation.target : mutation.target.parentElement);
        mutation.addedNodes.forEach(node => {
          if (node.nodeType === Node.ELEMENT_NODE) scan(node);
        });
      } else {
        const target = mutation.target instanceof Element ? mutation.target : mutation.target.parentElement;
        if (target) scan(target.closest("button"));
      }
    });
  }).observe(document.documentElement, {
    childList: true,
    characterData: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["aria-label", "title", "data-action", "id"]
  });
})();
