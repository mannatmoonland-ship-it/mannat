import { ANIMAL_AVATARS, getAnimalAvatar, renderAnimalAvatar } from "./animal_avatars.js";
import { formatDate, formatDateTime } from "./date_format.js";

const root = document.querySelector("[data-profile-settings]");
if (root) {
  for (const timestamp of document.querySelectorAll("[data-profile-timestamp]")) {
    const value = timestamp.dateTime;
    timestamp.textContent = /^\d{4}-\d{2}-\d{2}$/.test(value)
      ? formatDate(value)
      : formatDateTime(value);
  }

  const profileForm = document.getElementById("profile-edit-form");
  const profileNameInput = document.getElementById("profile-full-name");
  const profileEmailInput = document.getElementById("profile-email");
  const profileRecoveryEmailInput = document.getElementById("profile-recovery-email");
  const profileNameDisplay = document.getElementById("profile-display-name");
  const profileStatus = document.getElementById("profile-edit-status");
  const profileSaveButton = document.getElementById("profile-save-changes");
  profileForm.addEventListener("submit", async event => {
    event.preventDefault();
    const fullName = profileNameInput.value.trim().replace(/\s+/g, " ");
    const email = profileEmailInput.value.trim();
    const recoveryEmail = profileRecoveryEmailInput.value.trim();
    profileNameInput.value = fullName;
    profileEmailInput.value = email;
    profileRecoveryEmailInput.value = recoveryEmail;
    profileStatus.removeAttribute("data-state");
    profileStatus.removeAttribute("role");

    if (!fullName) {
      profileStatus.textContent = "Full name cannot be blank.";
      profileStatus.dataset.state = "error";
      profileStatus.setAttribute("role", "alert");
      profileNameInput.focus();
      return;
    }
    if (email && !profileEmailInput.validity.valid) {
      profileStatus.textContent = "Enter a valid account email address.";
      profileStatus.dataset.state = "error";
      profileStatus.setAttribute("role", "alert");
      profileEmailInput.focus();
      return;
    }
    if (recoveryEmail && !profileRecoveryEmailInput.validity.valid) {
      profileStatus.textContent = "Enter a valid recovery email address.";
      profileStatus.dataset.state = "error";
      profileStatus.setAttribute("role", "alert");
      profileRecoveryEmailInput.focus();
      return;
    }

    profileSaveButton.disabled = true;
    profileStatus.textContent = "Saving changes…";
    try {
      const response = await fetch("/api/profile", {
        method: "PATCH",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": root.dataset.csrfToken,
        },
        body: JSON.stringify({
          full_name: fullName,
          email,
          recovery_email: recoveryEmail,
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Unable to save your profile.");
      profileNameInput.value = result.full_name;
      profileEmailInput.value = result.email;
      profileRecoveryEmailInput.value = result.recovery_email;
      profileNameDisplay.textContent = result.full_name;
      for (const headerName of document.querySelectorAll(".dashboard-user-copy strong")) {
        headerName.textContent = result.full_name;
      }
      profileStatus.textContent = "Profile changes saved.";
      profileStatus.dataset.state = "success";
    } catch (error) {
      profileStatus.textContent = error.message || "Unable to save your profile. Please try again.";
      profileStatus.dataset.state = "error";
      profileStatus.setAttribute("role", "alert");
    } finally {
      profileSaveButton.disabled = false;
    }
  });

  const dialog = document.getElementById("avatar-library");
  const library = document.getElementById("avatar-library-list");
  const currentAvatar = document.getElementById("profile-current-avatar");
  const previewAvatar = document.getElementById("profile-preview-avatar");
  const previewName = document.getElementById("profile-preview-name");
  const saveButton = document.getElementById("profile-save-avatar");
  const status = document.getElementById("profile-avatar-status");
  let savedAvatarId = root.dataset.avatarId;
  let selectedAvatarId = savedAvatarId;

  function renderAvatar(container, avatarId) {
    container.innerHTML = renderAnimalAvatar(avatarId);
    container.setAttribute("aria-label", getAnimalAvatar(avatarId).name);
  }

  function renderAccountAvatars(avatarId) {
    for (const avatarNode of document.querySelectorAll("[data-animal-avatar]")) {
      avatarNode.dataset.animalAvatar = avatarId;
      avatarNode.innerHTML = renderAnimalAvatar(avatarId);
    }
  }

  function updatePreview() {
    const selected = getAnimalAvatar(selectedAvatarId);
    renderAvatar(previewAvatar, selected.id);
    previewName.textContent = selected.name;
    saveButton.disabled = selected.id === savedAvatarId;
    status.textContent = selected.id === savedAvatarId
      ? "Your avatar is up to date."
      : "Preview selected. Save to update your profile.";
    for (const button of library.querySelectorAll("[data-avatar-id]")) {
      const isSelected = button.dataset.avatarId === selected.id;
      button.classList.toggle("is-selected", isSelected);
      button.setAttribute("aria-pressed", String(isSelected));
    }
  }

  renderAvatar(currentAvatar, savedAvatarId);

  const groups = new Map();
  for (const avatar of ANIMAL_AVATARS) {
    if (!groups.has(avatar.group)) groups.set(avatar.group, []);
    groups.get(avatar.group).push(avatar);
  }
  library.innerHTML = [...groups].map(([group, avatars]) => `
    <section class="avatar-library-group" aria-label="${group}">
      <h3>${group}</h3>
      <div class="avatar-choice-grid">
        ${avatars.map(avatar => `
          <button type="button" class="avatar-choice" data-avatar-id="${avatar.id}" aria-pressed="false" aria-label="Select ${avatar.name}">
            <span class="avatar-choice-art">${renderAnimalAvatar(avatar.id)}</span>
            <span>${avatar.name}</span>
          </button>
        `).join("")}
      </div>
    </section>
  `).join("");

  library.addEventListener("click", event => {
    const button = event.target.closest("[data-avatar-id]");
    if (!button) return;
    selectedAvatarId = button.dataset.avatarId;
    updatePreview();
  });

  document.getElementById("profile-open-avatar-library").addEventListener("click", () => {
    updatePreview();
    dialog.showModal();
  });
  document.getElementById("avatar-library-close").addEventListener("click", () => dialog.close());
  document.getElementById("profile-surprise-avatar").addEventListener("click", () => {
    const alternatives = ANIMAL_AVATARS.filter(avatar => avatar.id !== selectedAvatarId);
    selectedAvatarId = alternatives[Math.floor(Math.random() * alternatives.length)].id;
    updatePreview();
  });

  saveButton.addEventListener("click", async () => {
    saveButton.disabled = true;
    status.textContent = "Saving your avatar…";
    try {
      const response = await fetch("/api/profile/avatar", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": root.dataset.csrfToken,
        },
        body: JSON.stringify({ avatarId: selectedAvatarId }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Unable to save your avatar.");
      savedAvatarId = result.avatarId;
      selectedAvatarId = result.avatarId;
      renderAvatar(currentAvatar, savedAvatarId);
      renderAccountAvatars(savedAvatarId);
      updatePreview();
      status.textContent = "Avatar saved to your profile.";
    } catch (error) {
      saveButton.disabled = false;
      status.textContent = error.message || "Unable to save your avatar. Please try again.";
      status.setAttribute("role", "alert");
    }
  });

  updatePreview();
  renderAccountAvatars(savedAvatarId);
}
