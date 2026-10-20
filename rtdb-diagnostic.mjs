const host = process.env.FIREBASE_DATABASE_EMULATOR_HOST;
console.log("Emulator host:", host);

const databaseName = "demo-mannat-moon-rules-default-rtdb";
const path = `presence/sessions/diagnostic-${Date.now()}`;
const url = `http://${host}/${path}.json?ns=${encodeURIComponent(databaseName)}`;

const adminUrl = `http://${host}/.json?ns=${encodeURIComponent(databaseName)}`;
const adminHeaders = { Authorization: "Bearer owner" };

try {
  for (const [name, options] of [
    ["READ", {}],
    ["WRITE", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: "Unauthorized" })
    }]
  ]) {
    const response = await fetch(url, options);
    const body = await response.text();
    console.log(name, "status:", response.status, "body:", body);
    if (response.ok) {
      throw new Error(`Unauthenticated ${name.toLowerCase()} unexpectedly succeeded`);
    }
  }
} finally {
  const cleanup = await fetch(adminUrl, {
    method: "PATCH",
    headers: { ...adminHeaders, "Content-Type": "application/json" },
    body: JSON.stringify({ presence: { sessions: { [path.split("/").at(-1)]: null } } })
  });
  if (!cleanup.ok) {
    throw new Error(`Emulator diagnostic cleanup failed: ${cleanup.status} ${await cleanup.text()}`);
  }
}
