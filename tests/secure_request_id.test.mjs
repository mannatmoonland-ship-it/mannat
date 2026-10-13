import assert from "node:assert/strict";
import test from "node:test";

import {
    generateSecureRequestId,
    reserveSecureRequestId
} from "../static/secure_request_id.mjs";

function createMemoryStorage() {
    const values = new Map();
    return {
        values,
        getItem: key => values.get(key) ?? null,
        setItem: (key, value) => values.set(key, value),
        removeItem: key => values.delete(key)
    };
}

test("secure request IDs use randomUUID when available", () => {
    const expected = "22f2a333-4444-4555-8666-777777777777";
    assert.equal(
        generateSecureRequestId({
            randomUUID: () => expected,
            getRandomValues: () => {
                throw new Error("randomUUID should be preferred");
            }
        }),
        expected
    );
});

test("secure request IDs fall back to cryptographic random values", () => {
    const result = generateSecureRequestId({
        getRandomValues: bytes => bytes.fill(0)
    });
    assert.equal(result, "00000000-0000-4000-8000-000000000000");
});

test("secure request ID generation fails without a cryptographic source", () => {
    assert.throws(
        () => generateSecureRequestId({}),
        /Secure random number generation is unavailable/
    );
});

test("page initialization reserves and persists a secure request ID", () => {
    const storage = createMemoryStorage();
    const requestId = reserveSecureRequestId(storage, "owner-scope", {
        cryptoApi: { getRandomValues: bytes => bytes.fill(0) }
    });

    assert.equal(requestId, "00000000-0000-4000-8000-000000000000");
    assert.deepEqual(JSON.parse(storage.getItem("owner-scope")), {
        id: requestId,
        body: null
    });
});

test("page initialization preserves a pending request ID unless a fresh one is requested", () => {
    const storage = createMemoryStorage();
    const firstId = "b92c0d94-686f-4d87-9fbc-49f62f228654";
    storage.setItem("owner-scope", JSON.stringify({ id: firstId, body: '{"name":"Item"}' }));

    assert.equal(reserveSecureRequestId(storage, "owner-scope"), firstId);

    const freshId = reserveSecureRequestId(storage, "owner-scope", {
        forceNew: true,
        cryptoApi: { getRandomValues: bytes => bytes.fill(0) }
    });
    assert.notEqual(freshId, firstId);
    assert.deepEqual(JSON.parse(storage.getItem("owner-scope")), {
        id: freshId,
        body: null
    });
});

test("request ID initialization fails clearly when session storage cannot persist", () => {
    const storage = {
        getItem: () => null,
        setItem: () => {
            throw new Error("storage blocked");
        },
        removeItem: () => {}
    };

    assert.throws(
        () => reserveSecureRequestId(storage, "owner-scope", {
            cryptoApi: { getRandomValues: bytes => bytes.fill(0) }
        }),
        /Enable session storage/
    );
});
