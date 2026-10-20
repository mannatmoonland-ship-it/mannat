import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
const projectId = "demo-mannat-moon-rules";
const databaseName = `${projectId}-default-rtdb`;
const emulatorAvailable = Boolean(process.env.FIREBASE_DATABASE_EMULATOR_HOST);
const rulesTest = emulatorAvailable ? test : test.skip;
test("Realtime Database rules deny all direct client access", async () => {
  const rules = JSON.parse(await readFile(new URL("../database.rules.json", import.meta.url), "utf8"));
  assert.equal(rules.rules[".read"], false);
  assert.equal(rules.rules[".write"], false);
  assert.deepEqual(rules.rules.presence.sessions[".indexOn"], ["expires_at"]);
});

rulesTest("Realtime Database emulator denies unauthenticated client reads and writes", async () => {
  const host = process.env.FIREBASE_DATABASE_EMULATOR_HOST;
  const [emulatorHost, emulatorPort] = host.split(":");
  assert.equal(emulatorHost, "127.0.0.1");
  assert.equal(Number(emulatorPort), 9000);

  const nodeKey = `client-test-${Date.now()}`;
  const path = `presence/sessions/${nodeKey}`;
  const url = new URL(
    `/${path}.json?ns=${encodeURIComponent(databaseName)}`,
    `http://${host}`
  );
  const adminUrl = new URL(`/.json?ns=${encodeURIComponent(databaseName)}`, `http://${host}`);
  const adminHeaders = { Authorization: "Bearer owner" };
  const originalValue = { display_name: "Emulator seed" };
  const adminOptions = {
    method: "PATCH",
    headers: { ...adminHeaders, "Content-Type": "application/json" },
    body: JSON.stringify({ presence: { sessions: { [nodeKey]: originalValue } } })
  };

  const readAdminNode = async () => {
    const response = await fetch(adminUrl, { headers: adminHeaders });
    const body = await response.text();
    assert.equal(response.status, 200, body);
    const data = JSON.parse(body);
    return data?.presence?.sessions?.[nodeKey] ?? null;
  };

  try {
    const seedResponse = await fetch(adminUrl, adminOptions);
    assert.equal(seedResponse.status, 200, await seedResponse.text());
    assert.deepEqual(await readAdminNode(), originalValue,
      "privileged setup must seed the configured emulator namespace");

    const readResponse = await fetch(url);
    assert.equal(readResponse.status, 401, await readResponse.text());

    const writeResponse = await fetch(url, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: "Unauthorized" })
    });
    assert.equal(writeResponse.status, 401, await writeResponse.text());

    assert.deepEqual(await readAdminNode(), originalValue,
      "a denied write must leave the existing database value unchanged");
  } finally {
    const cleanupResponse = await fetch(adminUrl, {
      method: "PATCH",
      headers: { ...adminHeaders, "Content-Type": "application/json" },
      body: JSON.stringify({ presence: { sessions: { [nodeKey]: null } } })
    });
    assert.equal(cleanupResponse.status, 200, await cleanupResponse.text());
  }
});
