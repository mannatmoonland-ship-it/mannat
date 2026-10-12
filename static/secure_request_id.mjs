const requestIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function generateSecureRequestId(cryptoApi = globalThis.crypto) {
    if (typeof cryptoApi?.randomUUID === "function") {
        return cryptoApi.randomUUID();
    }
    if (typeof cryptoApi?.getRandomValues !== "function") {
        throw new Error("Secure random number generation is unavailable.");
    }

    const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;

    const hex = Array.from(
        bytes,
        byte => byte.toString(16).padStart(2, "0")
    ).join("");

    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function reserveSecureRequestId(
    storage,
    storageKey,
    { forceNew = false, cryptoApi = globalThis.crypto } = {}
) {
    try {
        const storedValue = storage.getItem(storageKey);
        let savedRequest = null;
        if (storedValue && !forceNew) {
            try {
                savedRequest = JSON.parse(storedValue);
            } catch {
                storage.removeItem(storageKey);
            }
        }
        if (
            !forceNew
            && typeof savedRequest?.id === "string"
            && requestIdPattern.test(savedRequest.id)
            && (savedRequest.body === null || typeof savedRequest.body === "string")
        ) {
            return savedRequest.id;
        }

        const requestId = generateSecureRequestId(cryptoApi);
        storage.setItem(storageKey, JSON.stringify({ id: requestId, body: null }));
        return requestId;
    } catch (error) {
        if (error.message === "Secure random number generation is unavailable.") {
            throw error;
        }
        throw new Error(
            "Owner Scope cannot safely initialize a request ID. Enable session storage and use a supported browser."
        );
    }
}
