export function initializeConnectivityIndicator(documentApi, navigatorApi, windowApi) {
    const banner = documentApi.createElement("div");
    banner.className = "mm-connectivity-banner";
    banner.setAttribute("role", "status");
    banner.setAttribute("aria-live", "polite");
    banner.setAttribute("aria-atomic", "true");
    banner.textContent = "You are offline. Please check your internet connection.";

    const pageHeader = documentApi.querySelector("header.mm-app-header");
    const loginShell = documentApi.querySelector(".login-shell");
    if (pageHeader) {
        pageHeader.before(banner);
    } else if (loginShell) {
        loginShell.prepend(banner);
    } else {
        documentApi.body.prepend(banner);
    }

    const updateConnectivityMessage = () => {
        banner.hidden = navigatorApi.onLine;
    };

    updateConnectivityMessage();
    windowApi.addEventListener("online", updateConnectivityMessage);
    windowApi.addEventListener("offline", updateConnectivityMessage);
    return banner;
}

if (typeof document !== "undefined" && typeof navigator !== "undefined" && typeof window !== "undefined") {
    initializeConnectivityIndicator(document, navigator, window);
}
