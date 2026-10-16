const page = document.getElementById("resetPage");
const status = document.getElementById("resetStatus");
const form = document.getElementById("resetForm");

function showError(message) {
  status.textContent = message;
  status.classList.add("error");
}

async function validateResetLink(token) {
  const response = await fetch(page.dataset.validationUrl, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      token,
      csrf_token: page.dataset.csrfToken,
    }),
  });
  const result = await response.json();
  if (!response.ok || !result.valid || typeof result.username !== "string" || !result.username) {
    throw new Error(result.error || "This reset link is invalid or expired. Request a new reset link.");
  }
  return result;
}

if (page.dataset.invalidLink === "true") {
  showError("This reset link is invalid or expired. Request a new reset link.");
} else {
  const token = window.location.hash.slice(1) || document.getElementById("resetToken").value;
  window.history.replaceState(null, "", window.location.pathname);
  if (!token) {
    showError("This reset link is invalid or expired. Request a new reset link.");
  } else {
    document.getElementById("resetToken").value = token;
    validateResetLink(token)
      .then(result => {
        const username = document.getElementById("resetAccountUsername");
        username.textContent = `Account username: ${result.username}`;
        username.hidden = false;
        status.textContent = "Reset link verified. Choose a new password.";
        status.classList.remove("error");
        form.hidden = false;
      })
      .catch(error => showError(error.message || "Unable to validate this reset link."));
  }
}
