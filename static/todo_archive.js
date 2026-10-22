const root = document.querySelector("[data-todo-archive-page]");
if (root) {
    const list = root.querySelector("[data-todo-archive]");
    const status = root.querySelector("[data-todo-status]");
    const contextMenu = root.querySelector("[data-todo-context-menu]");
    document.body.append(contextMenu);
    const message = value => { status.textContent = value; };
    const copyTaskText = async text => {
        if (window.isSecureContext && navigator.clipboard?.writeText) {
            try { await navigator.clipboard.writeText(text); return true; }
            catch { /* Continue with the browser-compatible fallback. */ }
        }
        const field = document.createElement("textarea");
        field.value = text;
        field.setAttribute("readonly", "");
        field.setAttribute("aria-hidden", "true");
        field.style.position = "fixed";
        field.style.opacity = "0";
        field.style.pointerEvents = "none";
        document.body.append(field);
        field.focus();
        field.select();
        let copied = false;
        try { copied = document.execCommand("copy"); }
        catch { copied = false; }
        field.remove();
        return copied;
    };
    const setItemProgress = (row, value) => {
        row.setAttribute("aria-busy", "true");
        const progress = document.createElement("div");
        progress.className = "todo-item-progress";
        progress.setAttribute("role", "status");
        progress.setAttribute("aria-live", "polite");
        progress.textContent = value;
        row.append(progress);
    };
    const clearItemProgress = row => {
        row.removeAttribute("aria-busy");
        row.querySelector(".todo-item-progress")?.remove();
    };
    root.querySelector("[data-print-todos]").addEventListener("click", () => window.print());
    const readApiResponse = async response => {
        const body = await response.text();
        let result;
        try { result = JSON.parse(body); }
        catch { throw new Error(`The server returned HTTP ${response.status} instead of JSON. Reload the page and try again.`); }
        if (!response.ok) throw new Error(result.error || `Request failed (HTTP ${response.status}).`);
        return result;
    };
    const hideContextMenu = () => { contextMenu.hidden = true; };
    const showContextMenu = (x, y, actions) => {
        contextMenu.replaceChildren();
        actions.forEach(({label, action}) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "red-book-context-menu-item";
            button.setAttribute("role", "menuitem");
            button.textContent = label;
            button.addEventListener("click", async () => { hideContextMenu(); await action(); });
            contextMenu.append(button);
        });
        contextMenu.hidden = false;
        const bounds = contextMenu.getBoundingClientRect();
        contextMenu.style.left = `${Math.max(8, Math.min(x, innerWidth - bounds.width - 8))}px`;
        contextMenu.style.top = `${Math.max(8, Math.min(y, innerHeight - bounds.height - 8))}px`;
        contextMenu.querySelector("button")?.focus();
    };
    document.addEventListener("pointerdown", event => { if (!contextMenu.contains(event.target)) hideContextMenu(); });
    document.addEventListener("keydown", event => { if (event.key === "Escape") hideContextMenu(); });
    window.addEventListener("scroll", hideContextMenu, true);
    const loadArchive = async () => {
        const response = await fetch("/api/profile/todos", {headers: {"Accept": "application/json"}, cache: "no-store"});
        const result = await readApiResponse(response);
        const items = result.archived || [];
        list.replaceChildren();
        if (!items.length) {
            const empty = document.createElement("li");
            empty.className = "todo-empty";
            empty.textContent = "No archived tasks yet.";
            list.append(empty);
            return;
        }
        items.forEach(item => {
            const row = document.createElement("li");
            const text = document.createElement("span");
            const timestamps = document.createElement("span");
            const createdStamp = document.createElement("time");
            const completedStamp = document.createElement("time");
            row.dataset.todoId = item.id;
            row.className = "todo-archive-row";
            text.className = "todo-item-text";
            text.textContent = item.text;
            const createdDate = item.created_at ? new Date(item.created_at) : null;
            const completedDate = item.completed_at ? new Date(item.completed_at) : null;
            const formatStamp = (stamp, label, date, fallback) => {
                const validDate = date && !Number.isNaN(date.getTime());
                stamp.className = "todo-archive-timestamp";
                stamp.dateTime = validDate ? date.toISOString() : "";
                const formatted = validDate
                    ? date.toLocaleString("en-GB", {hour12: true}).replace(/\b(am|pm)\b/i, value => value.toUpperCase())
                    : fallback;
                stamp.textContent = `${label}: ${formatted}`;
            };
            timestamps.className = "todo-archive-timestamps";
            formatStamp(createdStamp, "Created", createdDate, "Creation time unavailable");
            formatStamp(completedStamp, "Completed", completedDate, "Completion time unavailable");
            timestamps.append(createdStamp, completedStamp);
            row.append(text, timestamps);
            row.addEventListener("contextmenu", event => {
                event.preventDefault();
                showContextMenu(event.clientX, event.clientY, [
                    {label: "Copy task", action: async () => {
                        const copied = await copyTaskText(item.text);
                        message(copied ? "Task copied to clipboard." : "Clipboard access is unavailable.");
                    }},
                    {label: "Restore to to-do list", action: async () => {
                        setItemProgress(row, "Restoring task...");
                        try {
                            const response = await fetch(`/api/profile/todos/${encodeURIComponent(item.id)}`, {
                                method: "PATCH", headers: {"Content-Type": "application/json"},
                                body: JSON.stringify({completed: false, csrf_token: root.dataset.csrfToken})
                            });
                            await readApiResponse(response);
                            await loadArchive();
                            message("Task restored to your to-do list.");
                        } catch (error) { clearItemProgress(row); message(error.message); }
                    }},
                    {label: "Delete from archive", action: async () => {
                        setItemProgress(row, "Deleting archived task...");
                        try {
                            const response = await fetch(`/api/profile/todos/${encodeURIComponent(item.id)}`, {
                                method: "DELETE", headers: {"Content-Type": "application/json"},
                                body: JSON.stringify({csrf_token: root.dataset.csrfToken})
                            });
                            await readApiResponse(response);
                            await loadArchive();
                            message("Archived task deleted.");
                        } catch (error) { clearItemProgress(row); message(error.message); }
                    }}
                ]);
            });
            list.append(row);
        });
    };
    loadArchive().catch(error => message(error.message));
}
