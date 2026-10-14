const page = document.getElementById("resetPage");
const status = document.getElementById("resetStatus");
const form = document.getElementById("resetForm");
const challenge = document.getElementById("phoneChallenge");
const sendCodeButton = document.getElementById("sendResetPhoneCode");
const verifyCodeButton = document.getElementById("verifyResetPhoneCode");
const codeInput = document.getElementById("resetPhoneCode");
let resetToken = "";

function showError(message) {
  status.textContent = message;
  status.classList.add("error");
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The request could not be completed.");
  return result;
}

if (page.dataset.invalidLink === "true") {
  showError("This reset link is invalid or expired. Request a new reset link.");
} else {
  resetToken = window.location.hash.slice(1) || document.getElementById("resetToken").value;
  window.history.replaceState(null, "", window.location.pathname);
  if (!resetToken) {
    showError("This reset link is invalid or expired. Request a new reset link.");
  } else {
    document.getElementById("resetToken").value = resetToken;
    postJson(page.dataset.validationUrl, { token: resetToken, csrf_token: page.dataset.csrfToken })
      .then(result => {
        if (!result.valid || typeof result.username !== "string" || !result.username || !result.masked_mobile_phone) {
          throw new Error("This reset link is invalid or expired. Request a new reset link.");
        }
        const username = document.getElementById("resetAccountUsername");
        username.textContent = `Account username: ${result.username}`;
        username.hidden = false;
        document.getElementById("phoneChallengeHint").textContent =
          `A verification code will be sent to ${result.masked_mobile_phone}.`;
        status.textContent = "Verify your registered mobile number to continue.";
        challenge.hidden = false;
      })
      .catch(error => showError(error.message || "Unable to validate this reset link."));
  }
}

sendCodeButton.addEventListener("click", async () => {
  sendCodeButton.disabled = true;
  try {
    const result = await postJson(page.dataset.sendCodeUrl, {
      token: resetToken,
      csrf_token: page.dataset.csrfToken,
    });
    codeInput.disabled = false;
    verifyCodeButton.disabled = false;
    const requestReference = result.provider_request_id
      ? ` Fast2SMS request ID: ${result.provider_request_id}.`
      : "";
    status.textContent =
      `Fast2SMS accepted the request for ${result.masked_mobile_phone}, but that does not confirm delivery. Check Fast2SMS Delivery Reports; if it shows Failed, use that report's failure details.${requestReference}`;
    status.classList.remove("error");
  } catch (error) {
    showError(error.message || "Unable to send the SMS code.");
  } finally {
    sendCodeButton.disabled = false;
  }
});

verifyCodeButton.addEventListener("click", async () => {
  verifyCodeButton.disabled = true;
  try {
    await postJson(page.dataset.verifyCodeUrl, {
      token: resetToken,
      code: codeInput.value.trim(),
      csrf_token: page.dataset.csrfToken,
    });
    challenge.hidden = true;
    status.textContent = "Mobile number verified. Choose a new password.";
    status.classList.remove("error");
    form.hidden = false;
  } catch (error) {
    showError(error.message || "The SMS code could not be verified.");
    verifyCodeButton.disabled = false;
  }
});
