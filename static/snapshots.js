import { showConfirmDialog } from "./dialogs.js";
import { formatDateTime } from "./date_format.js";

const app = document.getElementById("snapshotApp");
const csrfToken = app.dataset.csrfToken;
const message = document.getElementById("snapshotMessage");
const rows = document.getElementById("snapshotRows");
const restoreSelect = document.getElementById("restoreSnapshotSelect");
const previewContainer = document.getElementById("restorePreview");
const progress = document.getElementById("restoreProgress");
let restoreToken = "";
let selectedSnapshot = "";

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[character]));
    document.getElementById("cancelRestore")?.remove();
}

function showMessage(text, isError = false) {
    message.textContent = text;
    message.className = `snapshot-message${isError ? " error" : ""}`;
}

function showOperationError(error, messageText) {
    console.error("Backup operation failed:", error);
    showMessage(messageText, true);
}

async function request(url, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("X-Snapshot-CSRF", csrfToken);
    const response = await fetch(url, { ...options, headers, credentials: "same-origin" });
    let data;
    try {
        data = await response.json();
    } catch {
        throw new Error("The server returned an unreadable response.");
    }
    if (!response.ok) {
        const error = new Error(data.error || `Request failed (${response.status}).`);
        error.result = data.result;
        throw error;
    }
    return data;
}

function formatDate(value) {
    return formatDateTime(value, "Unknown");
}

function formatSize(bytes) {
    if (!Number.isFinite(bytes)) return "Unknown";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function actionButton(action, filename, label) {
    return `<button type="button" class="button secondary" data-action="${action}" data-filename="${escapeHtml(filename)}">${label}</button>`;
}

function renderSnapshots(snapshots) {
    const verified = snapshots.filter(snapshot => snapshot.verified);
    restoreSelect.innerHTML = '<option value="">Select a verified snapshot</option>' + verified
        .map(snapshot => `<option value="${escapeHtml(snapshot.filename)}">${escapeHtml(snapshot.filename)} (${escapeHtml(snapshot.project_id)})</option>`)
        .join("");
    if (!snapshots.length) {
        rows.innerHTML = '<tr><td colspan="8">No local snapshots found.</td></tr>';
        return;
    }
    rows.innerHTML = snapshots.map(snapshot => `
        <tr>
            <td>${escapeHtml(snapshot.filename)}</td>
            <td>${escapeHtml(snapshot.project_id || "Unknown")}</td>
            <td>${escapeHtml(formatDate(snapshot.created_at))}</td>
            <td>${escapeHtml(formatSize(snapshot.size))}</td>
            <td>${Number(snapshot.collection_count || 0)}</td>
            <td>${Number(snapshot.document_count || 0)}</td>
            <td class="${snapshot.verified ? "status-verified" : "status-failed"}" title="${snapshot.verified ? "" : "Contact your administrator for help checking this backup."}">${escapeHtml(snapshot.verification_status)}</td>
            <td><div class="table-actions">
                ${actionButton("verify", snapshot.filename, "Verify")}
                ${snapshot.verified ? actionButton("restore", snapshot.filename, "Restore") : ""}
                ${actionButton("folder", snapshot.filename, "Open Folder")}
                ${actionButton("delete", snapshot.filename, "Delete")}
            </div></td>
        </tr>`).join("");
}

function renderRestorableProjects(projects) {
    const select = document.getElementById("restoredProjectSelect");
    select.innerHTML = '<option value="">No eligible restored project</option>' + projects
        .map(project => `<option value="${escapeHtml(project.project_id)}">${escapeHtml(project.project_id)} (restored ${escapeHtml(formatDate(project.restored_at))})</option>`)
        .join("");
    document.getElementById("switchProject").disabled = projects.length === 0;
}

async function refreshHistory() {
    try {
        const data = await request("/api/snapshots");
        renderSnapshots(data.snapshots || []);
        renderRestorableProjects(data.restorable_projects || []);
        document.getElementById("activeProject").textContent = data.active_project_id || "Unknown";
        document.getElementById("previousProject").textContent = data.previous_project_id || "Not available";
        document.getElementById("switchBack").disabled = !data.previous_project_id;
    } catch (error) {
        showOperationError(error, "Unable to load backup history. Please try again.");
        rows.innerHTML = '<tr><td colspan="8">Unable to load backup history. Please try again.</td></tr>';
    }
}

async function openFolder() {
    await request("/api/snapshots/open-folder", { method: "POST" });
    showMessage("Backup folder opened on the server computer.");
}

document.getElementById("createSnapshot").addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    showMessage("Creating and checking the backup...");
    try {
        const data = await request("/api/snapshots", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        showMessage(`Backup created and checked: ${data.snapshot.filename}`);
        await refreshHistory();
    } catch (error) {
        showOperationError(error, "Unable to create the backup. Please try again.");
    } finally {
        button.disabled = false;
    }
});

document.getElementById("openBackupFolder").addEventListener("click", () => openFolder().catch(error => showOperationError(error, "Unable to open the backup folder on this computer.")));
document.getElementById("refreshHistory").addEventListener("click", refreshHistory);

rows.addEventListener("click", async event => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const { action, filename } = button.dataset;
    try {
        if (action === "verify") {
            const result = await request(`/api/snapshots/${encodeURIComponent(filename)}/verify`, { method: "POST" });
            showMessage(`Backup checked: ${result.metadata.document_count} records found.`);
            await refreshHistory();
        } else if (action === "folder") {
            await openFolder();
        } else if (action === "delete") {
            const confirmed = await showConfirmDialog({
                title: "Delete Backup",
                message: `Are you sure you want to delete "${filename}"?`,
                details: "This permanently removes the backup file from this computer. It does not change information in the active project.",
                confirmText: "Delete",
                cancelText: "Cancel",
                type: "danger"
            });
            if (!confirmed) return;
            await request(`/api/snapshots/${encodeURIComponent(filename)}`, { method: "DELETE" });
            showMessage(`Backup deleted: ${filename}.`);
            await refreshHistory();
        } else if (action === "restore") {
            restoreSelect.value = filename;
            selectedSnapshot = filename;
            document.getElementById("restoreSnapshotTitle").scrollIntoView({ behavior: "smooth", block: "start" });
            showMessage(`Selected ${filename} for restore preview.`);
        }
    } catch (error) {
        showOperationError(error, "Unable to complete this backup action. Please try again.");
    }
});

document.getElementById("previewRestore").addEventListener("click", async event => {
    const button = event.currentTarget;
    const filename = restoreSelect.value || selectedSnapshot;
    const credentialFile = document.getElementById("serviceAccountFile").files[0];
    const projectId = document.getElementById("destinationProjectId").value.trim();
    if (!filename || !credentialFile || !projectId) {
        showMessage("Select a checked backup, enter the destination, and choose the required credentials.", true);
        return;
    }
    let webConfig;
    try {
        webConfig = JSON.parse(document.getElementById("webConfig").value);
    } catch {
        showMessage("Enter valid application configuration details.", true);
        return;
    }
    const form = new FormData();
    form.set("destination_project_id", projectId);
    form.set("destination_type", document.getElementById("destinationType").value);
    form.set("service_account", credentialFile);
    form.set("web_config", JSON.stringify(webConfig));
    button.disabled = true;
    progress.className = "restore-progress";
    progress.textContent = "Checking the destination details, access, and backup contents...";
    try {
        const data = await request(`/api/snapshots/${encodeURIComponent(filename)}/restore/preview`, { method: "POST", body: form });
        restoreToken = data.restore_token;
        selectedSnapshot = filename;
        renderRestorePreview(data.preview);
        progress.textContent = "Destination validated. Review the preview before starting restore.";
    } catch (error) {
        console.error("Unable to validate backup destination:", error);
        progress.className = "restore-progress error";
        progress.textContent = "Unable to check the destination. Verify the details and try again.";
        previewContainer.hidden = true;
    } finally {
        button.disabled = false;
    }
});

function renderRestorePreview(preview) {
    const conflicts = preview.conflict_count || 0;
    previewContainer.hidden = false;
    previewContainer.innerHTML = `
        <h3>Restore Preview</h3>
        <p>Source project: <strong>${escapeHtml(preview.source_project_id)}</strong></p>
        <p>Snapshot date: ${escapeHtml(formatDate(preview.created_at))}</p>
        <p>Destination project: <strong>${escapeHtml(document.getElementById("destinationProjectId").value.trim())}</strong></p>
        <p>Collections: ${Number((preview.collections || []).length)} · Documents: ${Number(preview.document_count || 0)}</p>
        <p>Existing destination documents: ${Number(preview.destination_existing_document_count || 0)}</p>
        <p class="${conflicts ? "conflict-note" : ""}">Document ID conflicts: ${Number(conflicts)}</p>
        ${(preview.conflicts || []).length ? `<p>${preview.conflicts.map(escapeHtml).join("<br>")}${preview.conflicts_truncated ? "<br>Additional conflicts not listed" : ""}</p>` : ""}
        <p>${(preview.collections || []).map(item => `${escapeHtml(item.path)}: ${Number(item.document_count)} documents`).join("<br>")}</p>
        <div class="restore-confirm">
            <label>Type the destination project ID to confirm
                <input id="confirmRestoreProjectId" type="text" autocomplete="off" spellcheck="false">
            </label>
            <button type="button" id="confirmRestore" class="button primary">Start Restore</button>
        </div>
        ${conflicts ? `<fieldset class="conflict-choice"><legend>Resolve ${Number(conflicts)} conflicts</legend>
            <label><input name="conflictPolicy" type="radio" value="skip"> Skip existing documents. Restore will be partial.</label>
            <label><input name="conflictPolicy" type="radio" value="overwrite"> Replace existing documents with snapshot contents.</label>
                <button type="button" id="cancelRestore" class="button secondary">Cancel Restore</button>
        </fieldset>` : ""}
    `;
    document.getElementById("confirmRestore").addEventListener("click", runRestore);
    document.getElementById("cancelRestore")?.addEventListener("click", () => cancelRestorePreview().catch(error => showOperationError(error, "Unable to cancel the restore preview. Please try again.")));
}

async function runRestore() {
    const confirmId = document.getElementById("confirmRestoreProjectId").value.trim();
    const projectId = document.getElementById("destinationProjectId").value.trim();
    const conflictPolicy = document.querySelector('input[name="conflictPolicy"]:checked')?.value || "";
    if (confirmId !== projectId) {
        progress.className = "restore-progress error";
        progress.textContent = "The confirmed project ID does not match the destination.";
        return;
    }
    if (document.querySelector('input[name="conflictPolicy"]') && !conflictPolicy) {
        progress.className = "restore-progress error";
        progress.textContent = "Choose a conflict policy or cancel the restore.";
        return;
    }
    const button = document.getElementById("confirmRestore");
    button.disabled = true;
    document.getElementById("cancelRestore")?.remove();
    progress.className = "restore-progress";
    progress.textContent = "Restoring the information and checking the results...";
    try {
        const data = await request(`/api/snapshots/${encodeURIComponent(selectedSnapshot)}/restore`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                restore_token: restoreToken,
                confirmed_project_id: confirmId,
                skip_conflicts: conflictPolicy === "skip",
                overwrite_conflicts: conflictPolicy === "overwrite"
            })
        });
        const result = data.result;
        progress.className = `restore-progress${data.status === "success" ? "" : " error"}`;
        progress.textContent = data.status === "success"
            ? `Restore completed: ${result.documents_restored} records restored and ${result.documents_overwritten} replaced. ${data.switchable ? "You can now switch to this project." : "This project is not available to switch to."}`
            : `Restore completed with skipped records: ${result.documents_restored} restored, ${result.documents_overwritten} replaced, and ${result.documents_skipped} skipped. Review the results before continuing.`;
        if (data.switchable) document.getElementById("restoredProjectSelect").value = projectId;
        await refreshHistory();
    } catch (error) {
        console.error("Backup restore failed:", error);
        progress.className = "restore-progress error";
        const result = error.result;
        progress.textContent = result
            ? `Restore stopped: ${result.documents_restored} records restored, ${result.documents_overwritten} replaced, ${result.documents_skipped} skipped, and ${result.documents_incomplete} incomplete. Please contact your administrator before trying again.`
            : "We could not confirm that the restore completed. Check the restore history or contact your administrator before trying again.";
    } finally {
        button.disabled = false;
    }
}

async function cancelRestorePreview() {
    if (restoreToken) {
        await request(`/api/snapshots/pending/${encodeURIComponent(restoreToken)}`, { method: "DELETE" });
    }
    restoreToken = "";
    previewContainer.hidden = true;
    progress.textContent = "Restore cancelled. No destination documents were changed.";
}

document.getElementById("restoredProjectSelect").addEventListener("change", event => {
    document.getElementById("switchConfirmProjectId").value = event.target.value;
});

document.getElementById("switchProject").addEventListener("click", async event => {
    const projectId = document.getElementById("restoredProjectSelect").value;
    const confirmId = document.getElementById("switchConfirmProjectId").value.trim();
    if (!projectId || confirmId !== projectId) return;
    const confirmed = await showConfirmDialog({
        title: "Switch Active Project",
        message: `Switch the application to ${projectId}?`,
        details: "This changes where the application reads and saves information. It does not move or merge data. The current project remains separate.",
        confirmText: "Switch Project",
        cancelText: "Cancel",
        type: "danger"
    });
    if (!confirmed) return;
    event.currentTarget.disabled = true;
    try {
        await request("/api/snapshots/switch-project", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ project_id: projectId, confirmed_project_id: confirmId })
        });
        window.location.assign("/");
    } catch (error) {
        showOperationError(error, "Unable to switch projects. The current project has not been changed. Please try again.");
        event.currentTarget.disabled = false;
    }
});

document.getElementById("switchBack").addEventListener("click", async event => {
    const activeId = document.getElementById("activeProject").textContent;
    const confirmId = document.getElementById("rollbackConfirmProjectId").value.trim();
    if (!activeId || confirmId !== activeId) return;
    const previousProject = document.getElementById("previousProject").textContent;
    const confirmed = await showConfirmDialog({
        title: "Switch Back",
        message: `Switch back to ${previousProject}?`,
        details: "This changes where the application reads and saves information. It does not move or merge data.",
        confirmText: "Switch Back",
        cancelText: "Cancel",
        type: "danger"
    });
    if (!confirmed) return;
    event.currentTarget.disabled = true;
    try {
        await request("/api/snapshots/switch-back", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirmed_project_id: confirmId })
        });
        window.location.assign("/");
    } catch (error) {
        showOperationError(error, "Unable to switch back. The current project has not been changed. Please try again.");
        event.currentTarget.disabled = false;
    }
});

refreshHistory();