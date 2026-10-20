const root = document.querySelector("[data-todo-page]");
if (root) {
    const form = root.querySelector("[data-todo-form]");
    const input = form.querySelector("input[name='text']");
    const list = root.querySelector("[data-todo-list]");
    const status = root.querySelector("[data-todo-status]");
    let busy = false;
    let dragChanged = false;
    const message = value => { status.textContent = value; };
    const readApiResponse = async response => {
        const body = await response.text();
        let result;
        try { result = JSON.parse(body); }
        catch { throw new Error(`The server returned HTTP ${response.status} instead of JSON. Restart the app server and try again.`); }
        if (!response.ok) throw new Error(result.error || `Request failed (HTTP ${response.status}).`);
        return result;
    };
    const persistOrder = async () => {
        const items = [...list.querySelectorAll("li[data-todo-id]")].map(row => row.dataset.todoId);
        const response = await fetch("/api/profile/todos/reorder", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({items, csrf_token: root.dataset.csrfToken})
        });
        await readApiResponse(response);
    };
    const moveItem = async (row, offset) => {
        const rows = [...list.querySelectorAll("li[data-todo-id]")];
        const index = rows.indexOf(row);
        const target = index + offset;
        if (target < 0 || target >= rows.length) return;
        if (offset < 0) list.insertBefore(row, rows[target]);
        else list.insertBefore(rows[target], row);
        try { await persistOrder(); message("Task order saved."); }
        catch (error) { message(error.message); await loadItems(); }
    };
    const loadItems = async () => {
        const response = await fetch("/api/profile/todos", {headers: {"Accept": "application/json"}, cache: "no-store"});
        const result = await readApiResponse(response);
        render(result.items || []);
    };
    const render = items => {
        list.replaceChildren();
        if (!items.length) {
            const empty = document.createElement("li");
            const emptyText = document.createElement("span");
            empty.className = "todo-empty";
            emptyText.className = "todo-empty-text";
            emptyText.textContent = "No tasks yet. Add your first task above.";
            empty.append(emptyText);
            list.append(empty);
            return;
        }
        items.forEach(item => {
            const row = document.createElement("li");
            const text = document.createElement("span");
            const actions = document.createElement("div");
            const up = document.createElement("button");
            const down = document.createElement("button");
            const edit = document.createElement("button");
            const remove = document.createElement("button");
            row.dataset.todoId = item.id;
            row.draggable = true;
            row.setAttribute("aria-label", `Task: ${item.text}. Drag to reorder.`);
            text.className = "todo-item-text";
            text.textContent = item.text;
            actions.className = "todo-item-actions";
            up.type = down.type = edit.type = remove.type = "button";
            up.className = "todo-move-button todo-move-up";
            down.className = "todo-move-button todo-move-down";
            up.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 12l1.41 1.41L11 7.83V20h2V7.83l5.59 5.58L20 12l-8-8-8 8z"/></svg>';
            down.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M20 12l-1.41-1.41L13 16.17V4h-2v12.17l-5.59-5.58L4 12l8 8 8-8z"/></svg>';
            edit.className = "todo-icon-button todo-edit-button";
            edit.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zm17.71-10.04a1 1 0 0 0 0-1.41l-2.5-2.5a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.99-1.67z"/></svg>';
            edit.title = "Edit task";
            edit.setAttribute("aria-label", `Edit ${item.text}`);
            edit.addEventListener("click", () => startEdit());
            up.title = "Move up";
            down.title = "Move down";
            up.setAttribute("aria-label", `Move ${item.text} up`);
            down.setAttribute("aria-label", `Move ${item.text} down`);
            up.addEventListener("click", () => moveItem(row, -1));
            down.addEventListener("click", () => moveItem(row, 1));
            remove.className = "todo-icon-button todo-delete-button";
            remove.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>';
            remove.title = "Delete task";
            remove.setAttribute("aria-label", `Delete ${item.text}`);
            remove.addEventListener("click", async () => {
                if (busy) return;
                busy = true;
                remove.disabled = true;
                try {
                    const response = await fetch(`/api/profile/todos/${encodeURIComponent(item.id)}`, {
                        method: "DELETE", headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({csrf_token: root.dataset.csrfToken})
                    });
                    await readApiResponse(response);
                    await loadItems();
                    message("Task deleted.");
                } catch (error) { message(error.message); remove.disabled = false; }
                finally { busy = false; }
            });
            const startEdit = () => {
                if (busy || row.classList.contains("todo-editing")) return;
                row.classList.add("todo-editing");
                row.draggable = false;
                const input = document.createElement("input");
                const save = document.createElement("button");
                const cancel = document.createElement("button");
                input.className = "todo-edit-input";
                input.type = "text";
                input.maxLength = 240;
                input.value = item.text;
                input.setAttribute("aria-label", `Edit task: ${item.text}`);
                save.type = cancel.type = "button";
                save.className = "todo-icon-button todo-save-button";
                save.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>';
                save.title = "Save task";
                save.setAttribute("aria-label", "Save task");
                cancel.className = "todo-icon-button todo-cancel-button";
                cancel.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>';
                cancel.title = "Cancel editing";
                cancel.setAttribute("aria-label", "Cancel editing");
                text.replaceWith(input);
                [up, down, edit, remove].forEach(button => { button.hidden = true; });
                actions.append(save, cancel);
                const cancelEdit = () => { loadItems().catch(error => message(error.message)); };
                cancel.addEventListener("click", cancelEdit, {once: true});
                input.addEventListener("keydown", event => {
                    if (event.key === "Enter") { event.preventDefault(); save.click(); }
                    if (event.key === "Escape") cancel.click();
                });
                save.addEventListener("click", async () => {
                    const updatedText = input.value.trim();
                    if (!updatedText) { message("Task cannot be empty."); input.focus(); return; }
                    if (busy) return;
                    busy = true;
                    save.disabled = cancel.disabled = true;
                    try {
                        const response = await fetch(`/api/profile/todos/${encodeURIComponent(item.id)}`, {
                            method: "PATCH", headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({text: updatedText, csrf_token: root.dataset.csrfToken})
                        });
                        await readApiResponse(response);
                        await loadItems();
                        message("Task updated.");
                    } catch (error) { message(error.message); save.disabled = cancel.disabled = false; }
                    finally { busy = false; }
                });
                input.focus();
                input.select();
            };
            actions.append(up, down, edit, remove);
            row.append(text, actions);
            list.append(row);
        });
    };
    list.addEventListener("dragstart", event => {
        const row = event.target.closest("li[data-todo-id]");
        if (!row) return;
        dragChanged = false;
        row.classList.add("todo-dragging");
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", row.dataset.todoId);
    });
    list.addEventListener("dragover", event => {
        event.preventDefault();
        const dragging = list.querySelector(".todo-dragging");
        const target = event.target.closest("li[data-todo-id]");
        if (!dragging || !target || dragging === target) return;
        const bounds = target.getBoundingClientRect();
        const insertBefore = event.clientY < bounds.top + bounds.height / 2;
        list.insertBefore(dragging, insertBefore ? target : target.nextSibling);
        dragChanged = true;
    });
    list.addEventListener("drop", event => event.preventDefault());
    list.addEventListener("dragend", async event => {
        event.target.closest("li[data-todo-id]")?.classList.remove("todo-dragging");
        if (!dragChanged) return;
        busy = true;
        try { await persistOrder(); message("Task order saved."); }
        catch (error) { message(error.message); await loadItems(); }
        finally { busy = false; dragChanged = false; }
    });
    form.addEventListener("submit", async event => {
        event.preventDefault();
        const text = input.value.trim();
        const button = form.querySelector("button[type='submit']");
        if (!text || busy) return;
        busy = true;
        button.disabled = true;
        try {
            const response = await fetch("/api/profile/todos", {
                method: "POST", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({text, csrf_token: root.dataset.csrfToken})
            });
            await readApiResponse(response);
            input.value = "";
            await loadItems();
            message("Task added.");
        } catch (error) { message(error.message); }
        finally { busy = false; button.disabled = false; }
    });
    loadItems().catch(error => message(error.message));
}
