import { firebaseAuthReady, getFirebaseApp, getFirebaseFirestoreSdk, describeFirebaseError } from "./firebase_auth.js";
const rows = document.getElementById("summaryRows");
const count = document.getElementById("workDays");
const status = document.getElementById("summaryStatus");
const escapeHtml = value => String(value).replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
document.getElementById("summaryPrint").addEventListener("click", () => window.print());
try {
  await firebaseAuthReady;
  const sdk = await getFirebaseFirestoreSdk();
  const db = sdk.getFirestore(await getFirebaseApp());
  const snapshot = await sdk.getDocs(sdk.collection(db, "daily_records"));
  const byDate = new Map();
  snapshot.forEach(doc => {
    const record = doc.data();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(record.date || "")) return;
    if (!byDate.has(record.date)) byDate.set(record.date, new Set());
    byDate.get(record.date).add(String(record.activity || "Daily Site Record").trim() || "Daily Site Record");
  });
  const dates = [...byDate.keys()].sort((a, b) => b.localeCompare(a));
  count.textContent = dates.length;
  rows.innerHTML = dates.length ? dates.map(date => {
    const formattedDate = new Date(`${date}T12:00:00`).toLocaleDateString(undefined, {year:"numeric",month:"long",day:"numeric"});
    const reportUrl = new URL(`/daily-reports?date=${encodeURIComponent(date)}`, window.location.origin).href;
    return `<tr><td><div class="summary-date-cell"><span>${escapeHtml(formattedDate)}</span><a class="summary-open-report" href="${reportUrl}" target="_blank" rel="noopener">Open report</a></div></td><td>${[...byDate.get(date)].map(escapeHtml).join("<br>")}</td></tr>`;
  }).join("") : '<tr><td colspan="2">No daily reports found.</td></tr>';
  status.textContent = `Showing ${dates.length} work day${dates.length === 1 ? "" : "s"}.`;
} catch (error) {
  count.textContent = "-";
  rows.innerHTML = '<tr><td colspan="2">Daily reports could not be loaded. Check your connection and try again.</td></tr>';
  status.textContent = describeFirebaseError(error);
  status.classList.add("is-error");
  console.error("Daily Report Summary load failed:", error?.code || error?.name || "unknown");
}