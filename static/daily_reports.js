import { registerEscClose, unregisterEscClose } from "./dialogs.js";
import { getAnimalAvatar, renderAnimalAvatar } from "./animal_avatars.js";
import { formatDate, formatDateTime, formatDateWithDay } from "./date_format.js";
import {
  describeFirebaseError,
  firebaseAuthReady,
  getFirebaseApp,
  getFirebaseFirestoreSdk
} from "./firebase_auth.js";

let db = null;
let firestoreSdk = null;
let firebaseBootstrapError = null;
try {
  await firebaseAuthReady;
  firestoreSdk = await getFirebaseFirestoreSdk();
  db = firestoreSdk.getFirestore(await getFirebaseApp());
} catch (error) {
  firebaseBootstrapError = error;
  console.error("Firebase initialization failed:", error?.stage || error?.code || error?.name);
}
const firestoreMethod = name => (...args) => {
  const method = firestoreSdk?.[name];
  if (typeof method !== "function") {
    throw firebaseBootstrapError || new Error("The Firebase Firestore SDK is unavailable.");
  }
  return method(...args);
};
const collection = firestoreMethod("collection");
const doc = firestoreMethod("doc");
const onSnapshot = firestoreMethod("onSnapshot");
const query = firestoreMethod("query");
const where = firestoreMethod("where");

// =========================================================
// DAILY REPORTS NAMESPACE
// =========================================================

const DailyReports = (function() {

  // Helper function to get elements
  const $ = id => document.getElementById(id);
  function setDataStatus(message, isError = false) {
    const status = $("dr-dataStatus");
    if (!status) return;
    status.textContent = message;
    status.className = `dr-data-status${isError ? " error" : ""}`;
  }

  // State variables
  let editingId = null;
  let editingRecordUpdatedAt = null;
  let deletingId = null;
  let trades = [];
  let selectedDate = getInitialSelectedDate();
  let calendarMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1, 12);
  let calendarReportDates = new Set();
  let calendarLoadId = 0;
  let recordsUnsubscribe = null;
  let tradeRevisionUnsubscribe = null;
  let pendingTradeRevision = null;
  let tradeRevisionRefreshInFlight = false;
  let recordsSubscriptionDate = null;
  let recordsListenerId = 0;
  let recordsRenderId = 0;
  const recordCache = new Map();
  const authorAvatarByUserId = new Map();
  const authorAvatarByUsername = new Map();
  const DAILY_REPORT_WEATHER_OPTIONS = [
    "Sunny",
    "Partly Cloudy",
    "Cloudy",
    "Rainy",
    "Thunderstorm",
    "Foggy",
    "Windy",
    "Other"
  ];
  const DAILY_REPORT_FLOOR_OPTIONS = [
    "SITE",
    "CELLAR",
    "GROUND FLOOR",
    "FIRST FLOOR",
    "SECOND FLOOR",
    "THIRD FLOOR",
    "FOURTH FLOOR",
    "FIFTH FLOOR"
  ];

  const WEATHER_ICONS = {
    Sunny: "☀️",
    "Partly Cloudy": "🌤️",
    Cloudy: "☁️",
    Rainy: "🌧️",
    Thunderstorm: "⛈️",
    Foggy: "🌫️",
    Windy: "💨",
    Other: "🌡️"
  };
  
  // Debounce timer for URL input
  let previewDebounceTimer = null;
  let driveConnected = false;
  let driveConfigured = false;
  const PREVIEW_DEBOUNCE_MS = 800;
  const GOOGLE_PHOTOS_EXTRACTION_TIMEOUT_MS = 15000;
  const GOOGLE_PHOTOS_PREVIEW_WAIT_TIMEOUT_MS = 15000;
  const GOOGLE_PHOTOS_SUCCESS_CACHE_MS = 6 * 60 * 60 * 1000;
  const GOOGLE_PHOTOS_FAILURE_CACHE_MS = 30000;
  const GOOGLE_PHOTOS_MAX_CONCURRENT_EXTRACTIONS = 3;
  const googlePhotosExtractionCache = new Map();
  const googlePhotosExtractionQueue = [];
  let activeGooglePhotosExtractions = 0;
  let googlePhotosPreviewObserver = null;

  $("dr-date").value = selectedDate;
  $("dr-selectedDateLabel").textContent = formatSelectedDate(selectedDate);

  // Helper functions
  function getDateInputValue(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function isValidDateValue(dateValue) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dateValue || "")) {
      return false;
    }
    const [year, month, day] = dateValue.split("-").map(Number);
    const date = new Date(year, month - 1, day, 12);
    return !Number.isNaN(date.getTime()) && getDateInputValue(date) === dateValue;
  }

  function getDateFromUrl() {
    return new URL(window.location.href).searchParams.get("date");
  }

  function normalizeRecordVersion(value) {
    if (value && typeof value.toDate === "function") value = value.toDate();
    if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value.toISOString();
    if (typeof value !== "string" || !value) return null;
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toISOString();
  }

  function getInitialSelectedDate() {
    const dateFromUrl = getDateFromUrl();
    return isValidDateValue(dateFromUrl) ? dateFromUrl : getDateInputValue(new Date());
  }

  function updateDateUrl(replaceState = false) {
    const url = new URL(window.location.href);
    if (url.searchParams.get("date") === selectedDate) return;
    url.searchParams.set("date", selectedDate);
    window.history[replaceState ? "replaceState" : "pushState"](
      window.history.state,
      "",
      `${url.pathname}${url.search}${url.hash}`
    );
  }

  function formatSelectedDate(dateValue) {
    return formatDateWithDay(dateValue);
  }

  function setSelectedDate(dateValue, { updateUrl = true, replaceUrl = false } = {}) {
    if (!isValidDateValue(dateValue)) {
      return;
    }

    const date = new Date(`${dateValue}T12:00:00`);

    const previousMonth = `${calendarMonth.getFullYear()}-${calendarMonth.getMonth()}`;
    const dateChanged = selectedDate !== dateValue;
    selectedDate = dateValue;
    if (updateUrl) updateDateUrl(replaceUrl || !dateChanged);
    calendarMonth = new Date(date.getFullYear(), date.getMonth(), 1, 12);
    $("dr-selectedDateLabel").textContent = formatSelectedDate(selectedDate);
    renderCalendar();
    if (previousMonth !== `${calendarMonth.getFullYear()}-${calendarMonth.getMonth()}`) {
      loadCalendarReportDates();
    }
    subscribeToRecords();
  }

  function setCalendarOpen(isOpen) {
    const calendar = $("dr-calendar");
    const toggle = $("dr-calendarToggle");
    if (isOpen) {
      const [year, month] = selectedDate.split("-").map(Number);
      const selectedMonth = new Date(year, month - 1, 1, 12);
      const monthChanged = calendarMonth.getFullYear() !== selectedMonth.getFullYear()
        || calendarMonth.getMonth() !== selectedMonth.getMonth();
      calendarMonth = selectedMonth;
      if (monthChanged) calendarReportDates = new Set();
      renderCalendar();
      if (monthChanged) loadCalendarReportDates();
    }
    calendar.hidden = !isOpen;
    toggle.setAttribute("aria-expanded", String(isOpen));
  }

  function closeCalendar() {
    setCalendarOpen(false);
  }

  function renderCalendar() {
    const year = calendarMonth.getFullYear();
    const month = calendarMonth.getMonth();
    const firstDay = new Date(year, month, 1, 12);
    const leadingDays = (firstDay.getDay() + 6) % 7;
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const today = getDateInputValue(new Date());

    $("dr-calendarMonthLabel").textContent = new Intl.DateTimeFormat(undefined, {
      month: "long",
      year: "numeric"
    }).format(firstDay);

    const cells = Array.from({ length: leadingDays }, () => '<span class="dr-calendar-empty" aria-hidden="true"></span>');
    for (let day = 1; day <= daysInMonth; day += 1) {
      const dateValue = getDateInputValue(new Date(year, month, day, 12));
      const classes = ["dr-calendar-day"];
      if (dateValue === selectedDate) classes.push("is-selected");
      if (dateValue === today) classes.push("is-today");
      const hasReport = calendarReportDates.has(dateValue);
      const label = `${formatSelectedDate(dateValue)}${hasReport ? ", has Daily Reports" : ""}`;
      cells.push(`<button type="button" class="${classes.join(" ")}" data-date="${dateValue}" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}" aria-pressed="${dateValue === selectedDate}"><span>${day}</span>${hasReport ? '<i class="dr-calendar-report-dot" aria-hidden="true"></i>' : ""}</button>`);
    }
    $("dr-calendarDays").innerHTML = cells.join("");
  }

  async function loadCalendarReportDates() {
    renderCalendar();

    const year = calendarMonth.getFullYear();
    const month = calendarMonth.getMonth();
    const monthStart = getDateInputValue(new Date(year, month, 1, 12));
    const monthEnd = getDateInputValue(new Date(year, month + 1, 0, 12));
    const requestId = ++calendarLoadId;
    try {
      const response = await fetch(`/api/daily-records/dates?start=${monthStart}&end=${monthEnd}`);
      if (!response.ok) throw new Error(`Unable to load Daily Reports calendar dates (${response.status}).`);
      const result = await response.json();
      if (requestId !== calendarLoadId) return;
      calendarReportDates = new Set((Array.isArray(result.dates) ? result.dates : [])
        .filter(dateValue => typeof dateValue === "string" && dateValue >= monthStart && dateValue <= monthEnd));
      renderCalendar();
    } catch (error) {
      console.error("Unable to load Daily Reports calendar dates:", error);
    }
  }

  function moveCalendarMonth(monthOffset) {
    calendarMonth = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth() + monthOffset, 1, 12);
    calendarReportDates = new Set();
    renderCalendar();
    loadCalendarReportDates();
  }

  function moveSelectedDate(dayOffset) {
    const [year, month, day] = selectedDate.split("-").map(Number);
    const date = new Date(year, month - 1, day, 12);
    date.setDate(date.getDate() + dayOffset);
    setSelectedDate(getDateInputValue(date));
  }

  function belongsToSelectedDate(record, dateValue) {
    return record?.date === dateValue;
  }

  function clampNonNegative(value) {
    const num = Number(value || 0);
    if (!Number.isFinite(num) || num < 0) {
      return 0;
    }
    return num;
  }

  function getTradeCategories(trade) {
    const source = Array.isArray(trade?.workerCategories)
      ? trade.workerCategories
      : Array.isArray(trade?.workers)
        ? trade.workers
        : [];
    const seen = new Set();
    return source.filter(worker => {
      const name = String(worker?.name || "").trim();
      const key = name.toLowerCase();
      if (!name || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function getSelectedTrade() {
    return trades.find(trade => trade.id === $("dr-tradeSelect").value) || null;
  }

  function renderWorkerCategories(categories, counts = {}) {
    const container = $("dr-workerCategories");
    const categoryNames = categories.length
      ? categories.map(category => String(category.name).trim())
      : Object.keys(counts);
    container.innerHTML = categoryNames.length
      ? categoryNames.map(name => {
        const key = name.toLowerCase();
        const value = counts[name] ?? counts[key] ?? 0;
        return `<label><span class="dr-field-label">${escapeHtml(name)}</span><input class="dr-worker-count" data-category="${escapeHtml(name)}" type="number" min="0" value="${clampNonNegative(value)}"></label>`;
      }).join("")
      : '<div class="dr-workforce-empty">Select a trade with saved worker categories.</div>';
    container.querySelectorAll(".dr-worker-count").forEach(input => {
      input.addEventListener("input", updateWorkforceTotal);
    });
  }

  function getLegacyWorkforceValues(counts) {
    return {
      masonCount: Number(counts.Masons ?? counts.masons ?? 0),
      manMazdoorCount: Number(counts["Man Mazdoor"] ?? counts["man mazdoor"] ?? 0),
      womanMazdoorCount: Number(counts["Woman Mazdoor"] ?? counts["woman mazdoor"] ?? 0)
    };
  }

  function normalizeWeatherData(data = {}) {
    const rawCondition = String(data.weatherCondition || "").trim();
    const weatherDescription = String(data.weatherDescription || "").trim();
    const weatherCondition = DAILY_REPORT_WEATHER_OPTIONS.find(option => option.toLowerCase() === rawCondition.toLowerCase()) || "";

    if (!weatherCondition) {
      return {
        weatherCondition: "",
        weatherDescription: ""
      };
    }

    if (weatherCondition === "Other") {
      return {
        weatherCondition: "Other",
        weatherDescription: weatherDescription
      };
    }

    return {
      weatherCondition,
      weatherDescription: ""
    };
  }

  function getWeatherBadgeText(condition, description) {
    const { weatherCondition, weatherDescription } = normalizeWeatherData({
      weatherCondition: condition,
      weatherDescription: description
    });

    if (!weatherCondition) {
      return "Not recorded";
    }

    const icon = WEATHER_ICONS[weatherCondition] || "🌤️";

    if (weatherCondition === "Other") {
      return weatherDescription ? `${icon} Other — ${weatherDescription}` : `${icon} Other`;
    }

    return `${icon} ${weatherCondition}`;
  }

  function getWeatherSummary(condition, description) {
    return getWeatherBadgeText(condition, description);
  }

  function getWeatherBadgeMarkup(condition, description) {
    const summary = getWeatherSummary(condition, description);
    return `<span class="dr-weather">${escapeHtml(summary)}</span>`;
  }

  function formatPrintDate(dateValue) {
    return formatDate(dateValue, "Not specified");
  }

  function getPostedBy(record) {
    const fullName = String(record?.createdByName || "").trim() || "Unknown";
    const username = String(record?.createdByUsername || "").trim().replace(/^@+/, "");
    return { fullName, username, userId: String(record?.createdByUserId || "") };
  }

  function getAuthorAvatarId(userId, username) {
    const avatarId = userId
      ? authorAvatarByUserId.get(userId)
      : authorAvatarByUsername.get(String(username || "").trim().toLowerCase());
    return getAnimalAvatar(avatarId).id;
  }

  function renderUserAttribution(name, userId, username, attributionLabel, className = "") {
    const avatarId = getAuthorAvatarId(userId, username);
    const tooltip = `${attributionLabel}: ${name}`;
    return `<span class="dr-user-attribution ${className}"><span class="dr-author-avatar" title="${escapeHtml(tooltip)}" aria-hidden="true">${renderAnimalAvatar(avatarId)}</span><span>${escapeHtml(name)}</span></span>`;
  }

  function formatPostedByMarkup(record) {
    const postedBy = getPostedBy(record);
    return `Posted by: ${renderUserAttribution(postedBy.fullName, postedBy.userId, postedBy.username, "Posted by", "dr-print-author-identity")}${postedBy.username ? ` (@${escapeHtml(postedBy.username)})` : ""}`;
  }

  function getLastEditedDetails(record) {
    if (!record?.last_edited_at) {
      return null;
    }

    const fullName = String(record.last_edited_by_name || "").trim() || "Unknown";
    const username = String(record.last_edited_by_username || "").trim().replace(/^@+/, "");
    const editedAt = formatDateTime(record.last_edited_at);

    return {
      fullName,
      username,
      userId: String(record.last_edited_by_user_id || ""),
      editedAt
    };
  }

  function formatLastEditedMarkup(record) {
    const editor = getLastEditedDetails(record);
    if (!editor) return "";
    return `Last edited by: ${renderUserAttribution(editor.fullName, editor.userId, editor.username, "Last edited by", "dr-print-author-identity")}${editor.username ? ` (@${escapeHtml(editor.username)})` : ""} | ${escapeHtml(editor.editedAt)}`;
  }

  async function loadAuthorAvatars(records) {
    const userIds = new Set();
    const usernames = new Set();
    records.forEach(record => {
      if (record.createdByUserId && !authorAvatarByUserId.has(String(record.createdByUserId))) {
        userIds.add(String(record.createdByUserId));
      } else if (record.createdByUsername) {
        const username = String(record.createdByUsername).trim();
        if (!authorAvatarByUsername.has(username.toLowerCase())) usernames.add(username);
      }
      if (
        record.last_edited_by_user_id
        && !authorAvatarByUserId.has(String(record.last_edited_by_user_id))
      ) {
        userIds.add(String(record.last_edited_by_user_id));
      } else if (record.last_edited_by_username) {
        const username = String(record.last_edited_by_username).trim();
        if (!authorAvatarByUsername.has(username.toLowerCase())) usernames.add(username);
      }
    });
    const identities = [
      ...[...userIds].map(value => ["user_id", value]),
      ...[...usernames].map(value => ["username", value])
    ];
    for (let index = 0; index < identities.length; index += 100) {
      const params = new URLSearchParams();
      identities.slice(index, index + 100).forEach(([key, value]) => params.append(key, value));
      try {
        const response = await fetch(`/api/daily-records/author-avatars?${params}`, { credentials: "same-origin" });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Unable to load Daily Reports author avatars.");
        Object.entries(result.user_ids || {}).forEach(([userId, avatarId]) => {
          authorAvatarByUserId.set(userId, avatarId);
        });
        Object.entries(result.usernames || {}).forEach(([username, avatarId]) => {
          const normalizedUsername = username.trim().toLowerCase();
          authorAvatarByUsername.set(normalizedUsername, avatarId);
        });
      } catch (error) {
        console.error("Unable to load Daily Reports author avatars:", error);
      }
    }
  }

  function getWeatherDataFromForm() {
    const weatherCondition = $("dr-weatherCondition")?.value || "";
    const weatherDescription = $("dr-weatherDescription")?.value || "";

    return normalizeWeatherData({
      weatherCondition,
      weatherDescription: weatherCondition === "Other" ? weatherDescription : ""
    });
  }

  function updateWeatherCustomField() {
    const weatherCondition = $("dr-weatherCondition");
    const weatherDescription = $("dr-weatherDescription");
    const customWrapper = $("dr-weatherCustomWrapper");

    if (!weatherCondition || !weatherDescription || !customWrapper) {
      return;
    }

    const isOtherSelected = weatherCondition.value === "Other";
    customWrapper.style.display = isOtherSelected ? "block" : "none";

    if (!isOtherSelected) {
      weatherDescription.value = "";
    }
  }

  function updateFloorCustomField() {
    const floorSelect = $("dr-floor");
    const floorCustom = $("dr-floorCustom");
    const customWrapper = $("dr-floorCustomWrapper");
    if (!floorSelect || !floorCustom || !customWrapper) return;

    const isOtherSelected = floorSelect.value === "OTHER";
    customWrapper.hidden = !isOtherSelected;
    floorCustom.required = isOtherSelected;
    if (!isOtherSelected) floorCustom.value = "";
  }

  function setFloorForEditing(floor) {
    const floorSelect = $("dr-floor");
    const floorCustom = $("dr-floorCustom");
    const normalizedFloor = String(floor || "").trim();
    if (DAILY_REPORT_FLOOR_OPTIONS.includes(normalizedFloor)) {
      floorSelect.value = normalizedFloor;
      floorCustom.value = "";
    } else if (normalizedFloor) {
      floorSelect.value = "OTHER";
      floorCustom.value = normalizedFloor === "OTHER" ? "" : normalizedFloor;
    } else {
      floorSelect.value = "";
      floorCustom.value = "";
    }
    updateFloorCustomField();
  }

  function getWorkforceValues() {
    const workerCounts = {};
    document.querySelectorAll(".dr-worker-count").forEach(input => {
      workerCounts[input.dataset.category] = clampNonNegative(input.value);
    });
    const legacy = getLegacyWorkforceValues(workerCounts);

    return {
      ...legacy,
      workerCounts,
      totalWorkers: Object.values(workerCounts).reduce((total, value) => total + value, 0)
    };
  }

  function updateWorkforceTotal() {
    const { totalWorkers } = getWorkforceValues();
    $("dr-totalWorkersValue").textContent = totalWorkers;
  }

  function populateTrades() {
    const select = $("dr-tradeSelect");
    const previousSelection = select.value;
    select.innerHTML = '<option value="">Select a trade</option>' + trades
      .filter(trade => getTradeCategories(trade).length)
      .sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")))
      .map(trade => `<option value="${escapeHtml(trade.id)}">${escapeHtml(trade.name || "Unnamed trade")}</option>`)
      .join("");
    select.value = trades.some(trade => trade.id === previousSelection) ? previousSelection : "";
  }

  async function loadTrades(tradeIds = null) {
    if (!db) return;
    if (Array.isArray(tradeIds)) {
      await Promise.all(tradeIds.map(async tradeId => {
        const response = await fetch(`/api/daily-records/trades?trade_id=${encodeURIComponent(tradeId)}`, {
          credentials: "same-origin",
          cache: "no-store"
        });
        const payload = await response.json();
        if (response.status === 404) {
          trades = trades.filter(trade => trade.id !== tradeId);
          return;
        }
        if (!response.ok || !payload.trade) {
          throw new Error(payload.error || "Daily Reports trade options could not be loaded.");
        }
        trades = [...trades.filter(trade => trade.id !== tradeId), payload.trade];
      }));
    } else {
      const response = await fetch("/api/daily-records/trades", {
        credentials: "same-origin",
        cache: "no-store"
      });
      let payload = {};
      try {
        payload = await response.json();
      } catch (error) {
        throw new Error("Daily Reports trade options could not be loaded.");
      }
      if (!response.ok || !Array.isArray(payload.trades)) {
        throw new Error(payload.error || "Daily Reports trade options could not be loaded.");
      }
      trades = payload.trades;
    }
    populateTrades();
    renderWorkerCategories(getTradeCategories(getSelectedTrade()));
  }

  function applyPendingTradeRevision() {
    if (
      !pendingTradeRevision
      || tradeRevisionRefreshInFlight
      || !$("dr-modal").classList.contains("dr-hidden")
    ) return;
    const revision = pendingTradeRevision;
    pendingTradeRevision = null;
    tradeRevisionRefreshInFlight = true;
    loadTrades(revision.refresh_all ? null : revision.trade_ids).catch(error => {
      console.error("Daily Reports trade options refresh failed:", error?.status || error?.name);
      $("dr-tradeStatus").textContent = "Trade options could not receive live updates.";
    }).finally(() => {
      tradeRevisionRefreshInFlight = false;
      if (pendingTradeRevision) applyPendingTradeRevision();
    });
  }

  async function subscribeToTradeRevisions() {
    const firestore = firestoreSdk;
    if (!db || !firestore) return;
    let isInitialSnapshot = true;
    tradeRevisionUnsubscribe = onSnapshot(
      doc(db, "sync_metadata", "trade-projection"),
      snapshot => {
        if (isInitialSnapshot) {
          isInitialSnapshot = false;
          pendingTradeRevision = { refresh_all: true, trade_ids: [] };
          applyPendingTradeRevision();
          return;
        }
        const revision = snapshot.exists()
          ? snapshot.data()
          : { refresh_all: true, trade_ids: [] };
        if (pendingTradeRevision) {
          const refreshAll = pendingTradeRevision.refresh_all || revision.refresh_all;
          pendingTradeRevision = {
            refresh_all: refreshAll,
            trade_ids: refreshAll
              ? []
              : [...new Set([...(pendingTradeRevision.trade_ids || []), ...(revision.trade_ids || [])])]
          };
        } else {
          pendingTradeRevision = revision;
        }
        applyPendingTradeRevision();
      },
      error => {
        console.error("Unable to subscribe to Daily Reports trade revisions:", error?.code || error?.name);
        $("dr-tradeStatus").textContent = "Trade options could not receive live updates.";
      }
    );
    const modal = $("dr-modal");
    new MutationObserver(applyPendingTradeRevision).observe(modal, {
      attributes: true,
      attributeFilter: ["class"]
    });
  }

  function setWorkerFieldsForRecord(record) {
    const hasTradeAssociation = Boolean(record.tradeId || record.tradeName);
    const trade = record.tradeId
      ? trades.find(item => item.id === record.tradeId)
      : record.tradeName
        ? trades.find(item => item.name === record.tradeName)
        : null;
    $("dr-tradeSelect").value = trade?.id || "";
    const savedCounts = { ...(record.workerCounts || {}) };
    const legacy = getLegacyWorkforceValues(savedCounts);
    if (!Object.keys(savedCounts).length) {
      savedCounts.Masons = record.masonCount ?? legacy.masonCount;
      savedCounts["Man Mazdoor"] = record.manMazdoorCount ?? legacy.manMazdoorCount;
      savedCounts["Woman Mazdoor"] = record.womanMazdoorCount ?? legacy.womanMazdoorCount;
    }
    renderWorkerCategories(getTradeCategories(trade), savedCounts);
    $("dr-tradeSelect").disabled = hasTradeAssociation && !trade;
    $("dr-tradeSelect").required = hasTradeAssociation && Boolean(trade);
    $("dr-tradeStatus").textContent = !hasTradeAssociation
      ? "Legacy report: no trade association was saved."
      : trade
        ? ""
        : "The saved trade is unavailable. Original worker data is preserved.";
    updateWorkforceTotal();
  }

  function youtubeId(url){
    try{
      const u = new URL(url);
      if(!["http:", "https:"].includes(u.protocol)) return null;

      const hostname = u.hostname.toLowerCase();
      const isShortHost = hostname === "youtu.be" || hostname.endsWith(".youtu.be");
      const isYoutubeHost = hostname === "youtube.com" || hostname.endsWith(".youtube.com") ||
        hostname === "youtube-nocookie.com" || hostname.endsWith(".youtube-nocookie.com");
      let id = null;

      if(isShortHost){
        id = u.pathname.split("/")[1];
      } else if(isYoutubeHost){
        id = u.searchParams.get("v");
        if(!id && /^\/(shorts|embed|live|v)\//.test(u.pathname)) id = u.pathname.split("/")[2];
      }

      return id && /^[A-Za-z0-9_-]+$/.test(id) ? id : null;
    }catch(e){}
    return null;
  }

  function driveId(url){
    const m = url.match(/\/d\/([a-zA-Z0-9_-]+)/) || url.match(/[?&]id=([a-zA-Z0-9_-]+)/);
    return m ? m[1] : null;
  }

  function getDriveImageCandidates(url) {
    const id = driveId(String(url || ""));
    if (!id) return [];
    const encoded = encodeURIComponent(id);
    return [
      `https://lh3.googleusercontent.com/d/${encoded}=w1600`,
      `https://lh3.googleusercontent.com/d/${encoded}`,
      `https://drive.google.com/thumbnail?id=${encoded}&sz=w1600`,
      `https://drive.google.com/uc?export=view&id=${encoded}`
    ];
  }

  function isGooglePhotosUrl(url){
    try{
      const u = new URL(url);
      return u.protocol === "https:" && (
        (u.hostname === "photos.app.goo.gl" && Boolean(u.pathname.slice(1))) ||
        (u.hostname === "photos.google.com" &&
          (u.pathname.startsWith("/share/") || u.pathname.includes("/photo/")))
      );
    }catch(e){
      return false;
    }
  }

  function isGooglePhotosPreviewUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === "https:"
        && /^lh\d+\.googleusercontent\.com$/i.test(url.hostname)
        && url.pathname.startsWith("/pw/");
    } catch (error) {
      return false;
    }
  }

  async function requestGooglePhotosDirectUrl(photosUrl, retry = false) {
    const controller = new AbortController();
    let timedOut = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, GOOGLE_PHOTOS_EXTRACTION_TIMEOUT_MS);

    try {
      const retryQuery = retry ? "&retry=1" : "";
      const response = await fetch(`/extract-google-photos-url?url=${encodeURIComponent(photosUrl)}${retryQuery}`, {
        signal: controller.signal
      });
      const responseBody = await response.text();
      if (!response.ok) {
        console.warn("Google Photos extraction returned status:", response.status);
        return null;
      }
      const contentType = response.headers.get("content-type") || "";
      let directUrl = responseBody.trim();
      if (contentType.includes("application/json")) {
        try {
          const payload = JSON.parse(responseBody);
          directUrl = payload?.url;
        } catch (error) {
          console.error("Google Photos extraction returned an invalid response.");
          return null;
        }
      }
      if (!isGooglePhotosPreviewUrl(directUrl)) {
        console.warn("Google Photos extraction did not return a supported image preview.");
        return null;
      }
      return directUrl;
    } catch (error) {
      if (error.name === "AbortError" && timedOut) {
        console.warn("Google Photos extraction timed out.");
        return null;
      }
      console.error("Google Photos extraction request failed:", error.name || "unknown error");
      return null;
    } finally {
      clearTimeout(timeout);
    }
  }

  function startQueuedGooglePhotosExtractions() {
    while (
      activeGooglePhotosExtractions < GOOGLE_PHOTOS_MAX_CONCURRENT_EXTRACTIONS
      && googlePhotosExtractionQueue.length
    ) {
      const { photosUrl, retry, cacheEntry, resolve } = googlePhotosExtractionQueue.shift();
      activeGooglePhotosExtractions += 1;
      requestGooglePhotosDirectUrl(photosUrl, retry).then(directUrl => {
        if (googlePhotosExtractionCache.get(photosUrl) === cacheEntry) {
          cacheEntry.promise = null;
          cacheEntry.directUrl = directUrl;
          cacheEntry.expiresAt = directUrl
            ? Date.now() + GOOGLE_PHOTOS_SUCCESS_CACHE_MS
            : Date.now() + GOOGLE_PHOTOS_FAILURE_CACHE_MS;
        }
        resolve(directUrl);
      }).finally(() => {
        activeGooglePhotosExtractions -= 1;
        startQueuedGooglePhotosExtractions();
      });
    }
  }

  function getGooglePhotosDirectUrl(photosUrl, { retry = false } = {}) {
    const cached = googlePhotosExtractionCache.get(photosUrl);
    if (cached?.promise) return cached.promise;
    if (!retry && cached?.directUrl && Date.now() < cached.expiresAt) {
      return Promise.resolve(cached.directUrl);
    }
    if (!retry && cached && Date.now() < cached.expiresAt) return Promise.resolve(null);
    if (cached) googlePhotosExtractionCache.delete(photosUrl);

    let resolveRequest;
    const promise = new Promise(resolve => {
      resolveRequest = resolve;
    });
    const cacheEntry = { promise, directUrl: null, expiresAt: 0 };
    googlePhotosExtractionCache.set(photosUrl, cacheEntry);
    googlePhotosExtractionQueue.push({ photosUrl, retry, cacheEntry, resolve: resolveRequest });
    startQueuedGooglePhotosExtractions();
    return promise;
  }

  function showGooglePhotosPreviewError(loadingEl) {
    const photosUrl = loadingEl.dataset.photosUrl;
    loadingEl.dataset.previewState = "failed";
    loadingEl.innerHTML = `<div class="dr-preview-error">Google Photos preview unavailable. Retry or open the original.
      <button type="button" class="dr-google-photos-retry">Retry preview</button>
      <a href="${escapeHtml(photosUrl)}" target="_blank" rel="noopener">Open original</a>
    </div>`;
  }

  function renderGooglePhotosPreview(loadingEl, directUrl) {
    const image = document.createElement("img");
    image.alt = "Photo preview";
    image.className = "dr-ev-img";
    image.loading = "lazy";
    image.decoding = "async";
    image.referrerPolicy = "no-referrer";
    image.addEventListener("error", () => {
      const cached = googlePhotosExtractionCache.get(loadingEl.dataset.photosUrl);
      if (cached && !cached.promise) {
        cached.directUrl = null;
        cached.expiresAt = Date.now() + GOOGLE_PHOTOS_FAILURE_CACHE_MS;
      }
      showGooglePhotosPreviewError(loadingEl);
    }, { once: true });
    loadingEl.dataset.previewState = "loaded";
    loadingEl.replaceChildren(image);
    image.src = directUrl;
  }

  function loadGooglePhotosPreview(loadingEl, { retry = false } = {}) {
    if (!loadingEl.isConnected || loadingEl.dataset.previewState === "loading") return;
    loadingEl.dataset.previewState = "loading";
    loadingEl.textContent = retry ? "Retrying Google Photos preview..." : "Loading Google Photos preview...";
    const waitTimeout = setTimeout(() => {
      if (loadingEl.isConnected && loadingEl.dataset.previewState === "loading") {
        showGooglePhotosPreviewError(loadingEl);
      }
    }, GOOGLE_PHOTOS_PREVIEW_WAIT_TIMEOUT_MS);
    getGooglePhotosDirectUrl(loadingEl.dataset.photosUrl, { retry }).then(directUrl => {
      clearTimeout(waitTimeout);
      if (!loadingEl.isConnected || loadingEl.dataset.previewState !== "loading") return;
      if (directUrl) {
        renderGooglePhotosPreview(loadingEl, directUrl);
      } else {
        showGooglePhotosPreviewError(loadingEl);
      }
    });
  }

  function observeGooglePhotosPreviews(root) {
    const loadingElements = root.querySelectorAll(".dr-ev-preview-loading");
    if (!("IntersectionObserver" in window)) {
      loadingElements.forEach(loadingEl => loadGooglePhotosPreview(loadingEl));
      return;
    }
    if (!googlePhotosPreviewObserver) {
      googlePhotosPreviewObserver = new IntersectionObserver(entries => {
        entries.forEach(entry => {
          if (!entry.isIntersecting) {
            if (!entry.target.isConnected) googlePhotosPreviewObserver.unobserve(entry.target);
            return;
          }
          googlePhotosPreviewObserver.unobserve(entry.target);
          loadGooglePhotosPreview(entry.target);
        });
      }, { rootMargin: "400px 0px" });
    }
    loadingElements.forEach(loadingEl => googlePhotosPreviewObserver.observe(loadingEl));
  }

  function retryGooglePhotosPreview(event) {
    const button = event.target.closest(".dr-google-photos-retry");
    const loadingEl = button?.closest(".dr-ev-preview-loading");
    if (!loadingEl) return;

    loadGooglePhotosPreview(loadingEl, { retry: true });
  }

  function youtubePreviewHtml(videoId){
    const encodedId = encodeURIComponent(videoId);
    return `<div class="dr-youtube-preview" data-youtube-id="${encodedId}">
      <button type="button" class="dr-youtube-play" aria-label="Play YouTube video">
        <img src="https://img.youtube.com/vi/${encodedId}/maxresdefault.jpg" data-fallback="https://img.youtube.com/vi/${encodedId}/hqdefault.jpg" alt="YouTube video thumbnail" onerror="if(this.dataset.fallback){this.src=this.dataset.fallback;this.dataset.fallback='';}else{this.hidden=true;this.nextElementSibling.hidden=false;}">
        <span class="dr-youtube-thumb-fallback" hidden>Thumbnail unavailable</span>
        <span class="dr-youtube-play-icon" aria-hidden="true">&#9654;</span>
      </button>
    </div>`;
  }

  function previewHtml(type,url,previewUrl="", eagerImage=false){
    const y = youtubeId(url);
    if(y) return youtubePreviewHtml(y);
    const d = driveId(url) || driveId(previewUrl);
    if(d && type==="Photo"){
      const candidates = previewUrl
        ? [previewUrl, ...getDriveImageCandidates(url).filter(candidate => candidate !== previewUrl)]
        : getDriveImageCandidates(url);
      const primaryUrl = candidates[0] || url;
      const fallbackUrls = candidates.slice(1);
      return `<div class="dr-drive-preview-loading" role="status">Loading photo preview...</div>
        <img src="${escapeHtml(primaryUrl)}"
        data-fallbacks="${escapeHtml(fallbackUrls.join(','))}"
        referrerpolicy="no-referrer"
        alt="Photo preview"
        class="dr-ev-img"
        loading="${eagerImage ? "eager" : "lazy"}"
        onload="if(this.previousElementSibling)this.previousElementSibling.remove();"
        onerror="if(this.dataset.fallbacks){const f=this.dataset.fallbacks.split(',');this.src=f.shift();this.dataset.fallbacks=f.join(',');}else{this.style.display='none';if(this.previousElementSibling)this.previousElementSibling.remove();if(this.nextElementSibling)this.nextElementSibling.style.display='block';}">
        <div class="dr-preview-error" style="display:none;">Photo preview unavailable. <a href="${escapeHtml(url)}" target="_blank" rel="noopener">Open in Google Drive</a></div>`;
    }
    if(d) return `<iframe src="https://drive.google.com/file/d/${d}/preview"></iframe>`;
    
    // Google Photos handling - return placeholder that will be updated asynchronously
    if(type==="Photo" && isGooglePhotosUrl(url)){
      return `<div class="dr-ev-preview-loading" data-photos-url="${escapeHtml(url)}" aria-live="polite">Loading Google Photos preview...</div>`;
    }
    
    // For other photos, try direct image display
    if(type==="Photo"){
      return `<img src="${escapeHtml(url)}" referrerpolicy="no-referrer" alt="Photo preview" class="dr-ev-img" onerror="this.style.display='none'; this.nextElementSibling.style.display='block';">
              <div class="dr-preview-error" style="display:none;">Photo preview unavailable. <a href="${escapeHtml(url)}" target="_blank" rel="noopener">Open original</a></div>`;
    }

    if(String(type || "").toLowerCase() === "video"){
      return `<div class="dr-preview-error">Video preview unavailable for this link. <a href="${escapeHtml(url)}" target="_blank" rel="noopener">Open link</a></div>`;
    }
    
    return `<a class="dr-open" target="_blank" href="${escapeHtml(url)}">Open link</a>`;
  }

  function activateYouTubePreview(event){
    const button = event.target.closest(".dr-youtube-play");
    if(!button) return;

    const preview = button.closest(".dr-youtube-preview");
    const videoId = preview?.dataset.youtubeId;
    if(!videoId) return;

    const iframe = document.createElement("iframe");
    iframe.src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(videoId)}?autoplay=1`;
    iframe.title = "YouTube video player";
    iframe.allow = "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture";
    iframe.allowFullscreen = true;
    iframe.loading = "lazy";
    iframe.referrerPolicy = "strict-origin-when-cross-origin";
    preview.replaceChildren(iframe);
  }

  function openEvidenceImageViewer(clickedImage) {
    const card = clickedImage.closest(".dr-card");
    if (!card) return;
    const images = [...card.querySelectorAll(".dr-ev img.dr-ev-img")]
      .filter(image => image.isConnected && image.naturalWidth > 0 && image.currentSrc);
    let currentIndex = images.indexOf(clickedImage);
    if (currentIndex < 0) return;

    const overlay = document.createElement("div");
    overlay.className = "dr-image-viewer";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", "Evidence image viewer");
    overlay.innerHTML = `
      <button type="button" class="dr-image-viewer-close" aria-label="Close image viewer">&times;</button>
      <button type="button" class="dr-image-viewer-prev" aria-label="Previous image">&#8249;</button>
      <figure class="dr-image-viewer-figure">
        <img class="dr-image-viewer-image" alt="">
        <figcaption class="dr-image-viewer-caption" aria-live="polite"></figcaption>
      </figure>
      <button type="button" class="dr-image-viewer-next" aria-label="Next image">&#8250;</button>
      <div class="dr-image-viewer-count" aria-live="polite"></div>`;
    document.body.append(overlay);

    const viewerImage = overlay.querySelector(".dr-image-viewer-image");
    const caption = overlay.querySelector(".dr-image-viewer-caption");
    const count = overlay.querySelector(".dr-image-viewer-count");
    const previous = overlay.querySelector(".dr-image-viewer-prev");
    const next = overlay.querySelector(".dr-image-viewer-next");
    const closeButton = overlay.querySelector(".dr-image-viewer-close");
    const priorOverflow = document.body.style.overflow;
    const priorFocus = document.activeElement;
    document.body.style.overflow = "hidden";

    const render = () => {
      const source = images[currentIndex];
      viewerImage.src = source.currentSrc || source.src;
      viewerImage.alt = source.alt || `Evidence image ${currentIndex + 1}`;
      const title = source.closest(".dr-ev")?.querySelector(".dr-evtitle")?.textContent?.trim();
      caption.textContent = title || "";
      count.textContent = `${currentIndex + 1} / ${images.length}`;
      previous.disabled = images.length < 2;
      next.disabled = images.length < 2;
    };
    const move = direction => {
      currentIndex = (currentIndex + direction + images.length) % images.length;
      render();
    };
    const close = () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = priorOverflow;
      overlay.remove();
      if (priorFocus instanceof HTMLElement) priorFocus.focus();
    };
    const onKeyDown = event => {
      if (event.key === "Escape") { event.preventDefault(); close(); }
      else if (event.key === "ArrowLeft") { event.preventDefault(); move(-1); }
      else if (event.key === "ArrowRight") { event.preventDefault(); move(1); }
    };
    previous.addEventListener("click", () => move(-1));
    next.addEventListener("click", () => move(1));
    closeButton.addEventListener("click", close);
    overlay.addEventListener("click", event => { if (event.target === overlay) close(); });
    document.addEventListener("keydown", onKeyDown);

    let touchStartX = null;
    overlay.addEventListener("touchstart", event => {
      touchStartX = event.changedTouches[0]?.clientX ?? null;
    }, {passive: true});
    overlay.addEventListener("touchend", event => {
      if (touchStartX === null) return;
      const delta = event.changedTouches[0].clientX - touchStartX;
      if (Math.abs(delta) > 48) move(delta > 0 ? -1 : 1);
      touchStartX = null;
    }, {passive: true});

    render();
    closeButton.focus();
  }

  function handleEvidenceImageClick(event) {
    const image = event.target.closest(".dr-ev img.dr-ev-img");
    if (image && image.closest(".dr-card")) openEvidenceImageViewer(image);
  }

  function escapeHtml(s){
    return s.replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  }

  function showToast(message, type = 'success', duration = 3000) {
    const container = $("dr-toastContainer");
    if (!container) {
      console.error("Toast container not found!");
      return;
    }

    // Create toast element
    const toast = document.createElement("div");
    toast.className = `dr-toast dr-toast-${type}`;
    
    // Icon based on type
    const icon = type === 'success' ? '✓' : '✕';
    
    toast.innerHTML = `
      <div class="dr-toast-icon">${icon}</div>
      <div class="dr-toast-message">${escapeHtml(message)}</div>
      <button class="dr-toast-close" aria-label="Close notification">×</button>
    `;

    // Add close button functionality
    const closeBtn = toast.querySelector(".dr-toast-close");
    closeBtn.onclick = () => {
      removeToast(toast);
    };

    // Add to container
    container.appendChild(toast);

    // Auto-dismiss after duration
    if (duration > 0) {
      setTimeout(() => {
        removeToast(toast);
      }, duration);
    }

    return toast;
  }

  function removeToast(toast) {
    if (!toast || toast.classList.contains("hiding")) return;
    
    toast.classList.add("hiding");
    toast.addEventListener("animationend", () => {
      if (toast.parentNode) {
        toast.parentNode.removeChild(toast);
      }
    });
  }

  function addEvidenceRow(data = {}) {
    const row = document.createElement("div");
    row.className = "dr-evrow";
    row.innerHTML = `
      <select class="dr-evtype">
        <option>Photo</option>
        <option>Video</option>
        <option>Document</option>
        <option>Drawing</option>
      </select>
      <input
        class="dr-evtitle"
        placeholder="Description"
        value="${escapeHtml(data.title || "")}">
      <input
        class="dr-evurl"
        placeholder="YouTube, Google Drive, or Google Photos link"
        value="${escapeHtml(data.url || "")}">
      <input class="dr-evupload-input dr-evupload-gallery-input" type="file" accept="image/*" multiple hidden>
      <input class="dr-evupload-input dr-evupload-camera-input" type="file" accept="image/*" capture="environment" hidden>
      <button type="button" class="dr-evupload dr-evupload-gallery dr-form-action" aria-label="Choose from Gallery" title="Choose from Gallery"><svg class="dr-form-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M20 4v12H8V4h12m0-2H8c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2ZM4 6H2v14c0 1.1.9 2 2 2h14v-2H4V6Zm10 2.5L12 11l-1.5-2L8 13h10l-4-4.5Z"/></svg></button>
      <button type="button" class="dr-evupload dr-evupload-camera dr-form-action" aria-label="Take Photo" title="Take Photo"><svg class="dr-form-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 12.2a3.3 3.3 0 1 0 0 6.6 3.3 3.3 0 0 0 0-6.6ZM9 3 7.2 5H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-3.2L15 3H9Zm3 15.8a4.6 4.6 0 1 1 0-9.2 4.6 4.6 0 0 1 0 9.2Z"/></svg></button>
      <button type="button" class="dr-remove" aria-label="Remove evidence" title="Remove evidence"><svg class="dr-form-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button>
    `;

    if (data.fileId) {
      row.dataset.driveFileId = data.fileId;
    }
    if (data.previewUrl) {
      row.dataset.drivePreviewUrl = data.previewUrl;
    }

    if (data.type) {
      row.querySelector(".dr-evtype").value = data.type;
    }

    row.querySelector(".dr-remove").onclick = () => {
      row.remove();
      updatePreview();
    };

    const uploadInputs = [...row.querySelectorAll(".dr-evupload-input")];
    const uploadButtons = [...row.querySelectorAll(".dr-evupload")];
    uploadButtons.forEach((button, index) => {
      button.disabled = false;
      button.addEventListener("click", () => uploadInputs[index].click());
    });
    const uploadPhoto = async (photo, row, uploadButton) => {
      if (!driveConnected) {
        showToast(
          driveConfigured
            ? "Connect Google Drive before uploading this photo."
            : "Google Drive needs administrator setup before photos can be uploaded.",
          "error",
          6000
        );
        return false;
      }
      const targetButtons = [...row.querySelectorAll(".dr-evupload")];
      targetButtons.forEach(button => { button.disabled = true; });
      uploadButton.setAttribute("aria-label", "Uploading photo");
      uploadButton.title = "Uploading photo";
      try {
        const photoFingerprint = `${photo.name}:${photo.size}:${photo.lastModified}`;
        if (
          row.dataset.driveUploadFingerprint !== photoFingerprint ||
          !row.dataset.driveUploadId
        ) {
          if (!window.crypto?.randomUUID) {
            throw new Error("Secure photo upload is unavailable in this browser.");
          }
          row.dataset.driveUploadId = window.crypto.randomUUID();
          row.dataset.driveUploadFingerprint = photoFingerprint;
        }
        const formData = new FormData();
        formData.append("photo", photo, photo.name);
        formData.append("upload_id", row.dataset.driveUploadId);
        const response = await fetch("/api/daily-reports/evidence/upload", {
          method: "POST",
          headers: { "X-CSRF-Token": document.body.dataset.dailyReportsCsrf || "" },
          body: formData,
          cache: "no-store",
          credentials: "same-origin"
        });
        let payload = {};
        try {
          payload = await response.json();
        } catch (error) {
          throw new Error("The photo upload service returned an invalid response.");
        }
        if (!response.ok) {
          throw new Error(payload.error || "Unable to upload the photo.");
        }
        row.querySelector(".dr-evtype").value = "Photo";
        row.querySelector(".dr-evtitle").value = payload.title || photo.name || "Site photo";
        row.querySelector(".dr-evurl").value = payload.url;
        row.dataset.driveFileId = payload.file_id;
        row.dataset.drivePreviewUrl = payload.preview_url;
        delete row.dataset.driveUploadId;
        delete row.dataset.driveUploadFingerprint;
        updatePreview();
        return true;
      } catch (error) {
        uploadButton.setAttribute("aria-label", "Retry photo upload");
        uploadButton.title = "Retry photo upload";
        showToast(error.message || "Unable to upload the photo.", "error", 5000);
        return false;
      } finally {
        targetButtons.forEach(button => { button.disabled = false; });
        if (!row.dataset.driveUploadId) {
          targetButtons[0].setAttribute("aria-label", "Choose from Gallery");
          targetButtons[0].title = "Choose from Gallery";
          targetButtons[1].setAttribute("aria-label", "Take Photo");
          targetButtons[1].title = "Take Photo";
        }
      }
    };
    const handlePhotoSelection = async event => {
      const uploadInput = event.currentTarget;
      const uploadButton = uploadButtons[uploadInputs.indexOf(uploadInput)];
      const photos = [...(uploadInput.files || [])];
      if (!photos.length) return;
      if (!driveConnected) {
        await uploadPhoto(photos[0], row, uploadButton);
        uploadInput.value = "";
        return;
      }
      if (photos.length === 1 || uploadInput.classList.contains("dr-evupload-camera-input")) {
        const succeeded = await uploadPhoto(photos[0], row, uploadButton);
        if (succeeded) {
          showToast("Photo uploaded. Save the Daily Record to keep it.", "success", 5000);
        }
      } else {
        const targetRows = photos.map((photo, index) =>
          index === 0 ? row : addEvidenceRow()
        );
        let uploadedCount = 0;
        for (let index = 0; index < photos.length; index += 1) {
          const targetRow = targetRows[index];
          if (await uploadPhoto(
            photos[index],
            targetRow,
            targetRow.querySelector(".dr-evupload-gallery")
          )) uploadedCount += 1;
        }
        if (uploadedCount === photos.length) {
          showToast(`${uploadedCount} photos uploaded. Save the Daily Record to keep them.`, "success", 6000);
        } else {
          showToast(`${uploadedCount} of ${photos.length} photos uploaded. Retry any failed photo rows, then save the Daily Record.`, "error", 7000);
        }
      }
      uploadInput.value = "";
    };
    uploadInputs.forEach(uploadInput => uploadInput.addEventListener("change", handlePhotoSelection));

    // Debounced URL input to avoid excessive extraction requests
    row.querySelector(".dr-evurl").oninput = () => {
      clearTimeout(previewDebounceTimer);
      previewDebounceTimer = setTimeout(updatePreview, PREVIEW_DEBOUNCE_MS);
    };
    row.querySelector(".dr-evtype").onchange = updatePreview;

    $("dr-evidenceRows").appendChild(row);
    updatePreview();
    return row;
  }

  async function initializeDailyReportsDrive() {
    const statusElement = $("dr-driveStatus");
    const connectButton = $("dr-connectDrive");
    if (!statusElement) return;
    try {
      const response = await fetch("/api/daily-reports/google-drive/status", {
        cache: "no-store",
        credentials: "same-origin"
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Google Drive status is unavailable.");
      if (payload.connected) {
        statusElement.textContent = payload.message || "Google Drive connected";
      } else if (payload.message === "Google Drive is connected for Daily Reports.") {
        statusElement.textContent = "OAuth returned, but no usable Drive connection was found. Connect Google Drive again.";
      } else if (payload.message) {
        statusElement.textContent = payload.message;
      } else {
        statusElement.textContent = payload.configured
          ? "Google Drive not connected"
          : "OAuth setup required: configure GOOGLE_OAUTH_CLIENT_SECRETS_FILE. Local authorization requires localhost; mobile or LAN access requires an HTTPS callback.";
      }
      if (connectButton) {
        connectButton.hidden = !payload.can_connect || Boolean(payload.connected);
        connectButton.disabled = !payload.configured;
        connectButton.title = payload.configured
          ? "Connect your Google Drive account"
          : "Administrator setup is required before Google OAuth can start";
      }
      driveConnected = Boolean(payload.connected);
      driveConfigured = Boolean(payload.configured);
      if (connectButton && !connectButton.dataset.bound) {
        connectButton.dataset.bound = "true";
        connectButton.addEventListener("click", async () => {
          connectButton.disabled = true;
          try {
            const csrfToken = document.body.dataset.dailyReportsCsrf || "";
            const connectResponse = await fetch("/api/daily-reports/google-drive/connect", {
              method: "POST",
              headers: { "X-CSRF-Token": csrfToken },
              cache: "no-store",
              credentials: "same-origin"
            });
            const connectPayload = await connectResponse.json();
            if (!connectResponse.ok) {
              throw new Error(connectPayload.error || "Unable to connect Google Drive.");
            }
            window.location.assign(connectPayload.authorization_url);
          } catch (error) {
            statusElement.textContent = error.message || "Unable to connect Google Drive.";
            connectButton.disabled = false;
          }
        });
      }
    } catch (error) {
      statusElement.textContent = error.message || "Google Drive status is unavailable.";
    }
  }

  function updatePreview(){
    const rows=[...document.querySelectorAll(".dr-evrow")];
    if(rows.length){
      const previews = rows.map(row => previewHtml(
        row.querySelector(".dr-evtype").value,
        row.querySelector(".dr-evurl").value,
        row.dataset.drivePreviewUrl || "",
        true
      ));
      
      $("dr-livePreview").innerHTML = previews.join("");
      observeGooglePhotosPreviews($("dr-livePreview"));
    } else {
      $("dr-livePreview").innerHTML = "Paste a YouTube, Google Drive, or Google Photos link above.";
    }
  }

  function openAddModal() {
    editingId = null;
    editingRecordUpdatedAt = null;
    $("dr-recordForm").reset();
    updateFloorCustomField();
    $("dr-tradeSelect").disabled = false;
    $("dr-tradeSelect").required = true;
    $("dr-tradeStatus").textContent = "";
    $("dr-weatherCondition").value = "";
    $("dr-weatherDescription").value = "";
    updateWeatherCustomField();
    renderWorkerCategories(getTradeCategories(getSelectedTrade()));
    updateWorkforceTotal();
    $("dr-date").value = selectedDate;
    $("dr-evidenceRows").innerHTML = "";
    addEvidenceRow();
    const modal = $("dr-modal");
    if (modal) {
      modal.classList.remove("dr-hidden");
      registerEscClose(modal, closeModal);
    }

    const submitButton = $("dr-recordForm").querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.querySelector("span").textContent = "Save Daily Record";
    }

    const title = $("dr-modal").querySelector("h2");
    if (title) {
      title.querySelector("span").textContent = "New Daily Record";
    }
  }

  function closeModal() {
    const modal = $("dr-modal");
    if (modal) {
      unregisterEscClose(modal);
      modal.classList.add("dr-hidden");
    }
  }

  function openDeleteConfirm(id) {
    const record = recordCache.get(id);
    if (!record) {
      showToast("Record not found.", "error", 5000);
      return;
    }

    deletingId = id;
    $("dr-deleteMeta").innerHTML = `
      <div class="dr-delete-title">Delete this daily record?</div>
      <div class="dr-delete-date">${escapeHtml(formatDate(record.date, ""))}</div>
      <div class="dr-delete-activity">${escapeHtml(record.activity || "Daily Site Record")}</div>
      <div class="dr-delete-note">This cannot be undone.</div>
    `;
    const deleteModal = $("dr-deleteModal");
    if (deleteModal) {
      deleteModal.classList.remove("dr-hidden");
      registerEscClose(deleteModal, cancelDelete);
    }
  }

  async function confirmDeleteRecord() {
    if (!deletingId) {
      return;
    }

    try {
      const response = await fetch(`/api/daily-records/${encodeURIComponent(deletingId)}`, {
        method: "DELETE",
        credentials: "same-origin",
        headers: {
          "Accept": "application/json",
          "X-CSRF-Token": document.body.dataset.dailyReportsCsrf || ""
        }
      });
      if (!response.ok) {
        let errorMessage = `Unable to delete the daily report (${response.status}).`;
        try {
          const errorResponse = await response.json();
          if (typeof errorResponse.error === "string" && errorResponse.error.trim()) {
            errorMessage = errorResponse.error;
          }
        } catch (error) {
          console.error("Unable to read the Daily Report deletion error response.", error);
        }
        throw new Error(errorMessage);
      }
      deletingId = null;
      const deleteModal = $("dr-deleteModal");
      if (deleteModal) {
        unregisterEscClose(deleteModal);
        deleteModal.classList.add("dr-hidden");
      }
      showToast("Record deleted successfully.", "success", 3000);
      loadCalendarReportDates();
    } catch (error) {
      console.error(error);
      showToast(
        error instanceof Error ? error.message : "Unable to delete the daily report. Please try again.",
        "error",
        5000
      );
    }
  }

  function cancelDelete() {
    deletingId = null;
    const deleteModal = $("dr-deleteModal");
    if (deleteModal) {
      unregisterEscClose(deleteModal);
      deleteModal.classList.add("dr-hidden");
    }
  }

  function editRecord(id) {
    const record = recordCache.get(id);
    if (!record) {
      showToast("Record not found.", "error", 5000);
      return;
    }

    editingId = id;
    editingRecordUpdatedAt = normalizeRecordVersion(record.updatedAt);
    $("dr-date").value = record.date || "";
    setFloorForEditing(record.floor);
    $("dr-activity").value = record.activity || "";

    setWorkerFieldsForRecord(record);

    const savedWeather = normalizeWeatherData({
      weatherCondition: record.weatherCondition,
      weatherDescription: record.weatherDescription
    });
    $("dr-weatherCondition").value = savedWeather.weatherCondition;
    $("dr-weatherDescription").value = savedWeather.weatherDescription;
    updateWeatherCustomField();

    $("dr-materials").value = record.materials || "";
    $("dr-notes").value = record.notes || "";
    $("dr-evidenceRows").innerHTML = "";

    const evidence = record.evidence || [];
    if (evidence.length) {
      evidence.forEach(item => {
        addEvidenceRow(item);
      });
    } else {
      addEvidenceRow();
    }

    const modal = $("dr-modal");
    if (modal) {
      modal.classList.remove("dr-hidden");
      registerEscClose(modal, closeModal);
    }

    const submitButton = $("dr-recordForm").querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.querySelector("span").textContent = "Update Daily Record";
    }

    const title = $("dr-modal").querySelector("h2");
    if (title) {
      title.querySelector("span").textContent = "Edit Daily Record";
    }
  }

  async function handleFormSubmit(e) {
    e.preventDefault();

    if (!db) {
      showToast("Daily Reports are temporarily unavailable. Please contact your administrator.", "error", 5000);
      return;
    }

    let isEditing;
    let responseData;
    try {
      const evidence = [...document.querySelectorAll(".dr-evrow")]
        .map(row => ({
          type: row.querySelector(".dr-evtype").value,
          title: row.querySelector(".dr-evtitle").value.trim(),
          url: row.querySelector(".dr-evurl").value.trim(),
          ...(row.dataset.driveFileId ? { fileId: row.dataset.driveFileId } : {}),
          ...(row.dataset.drivePreviewUrl ? { previewUrl: row.dataset.drivePreviewUrl } : {})
        }))
        .filter(x => x.url);

      const workforce = getWorkforceValues();
      const selectedTrade = getSelectedTrade();
      const existingRecord = editingId ? recordCache.get(editingId) : null;
      const weatherData = getWeatherDataFromForm();
      isEditing = Boolean(editingId);

      const recordData = {
        date: $("dr-date").value,
        floor: $("dr-floor").value === "OTHER"
          ? $("dr-floorCustom").value.trim()
          : $("dr-floor").value,
        activity: $("dr-activity").value,
        tradeId: selectedTrade?.id || existingRecord?.tradeId || "",
        tradeName: selectedTrade?.name || existingRecord?.tradeName || "",
        workerCounts: workforce.workerCounts,
        masonCount: workforce.masonCount,
        manMazdoorCount: workforce.manMazdoorCount,
        womanMazdoorCount: workforce.womanMazdoorCount,
        totalWorkers: workforce.totalWorkers,
        weatherCondition: weatherData.weatherCondition,
        weatherDescription: weatherData.weatherDescription,
        materials: $("dr-materials").value,
        notes: $("dr-notes").value,
        evidence: evidence,
        updatedAt: new Date().toISOString()
      };
      if (isEditing) {
        recordData.expectedUpdatedAt = editingRecordUpdatedAt;
      }

      const response = await fetch(
        isEditing ? `/api/daily-records/${encodeURIComponent(editingId)}` : "/api/daily-records",
        {
          method: isEditing ? "PATCH" : "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": document.body.dataset.dailyReportsCsrf || ""
          },
          body: JSON.stringify(recordData)
        }
      );
      try {
        responseData = await response.json();
      } catch (_) {
        responseData = {};
      }
      if (!response.ok) {
        throw new Error(responseData.error || "Unable to save the daily report.");
      }
    } catch (error) {
      console.error(error);
      showToast(error.message || "Unable to save the daily report. Please check your connection and try again.", "error", 5000);
      return;
    }

    if (isEditing && responseData.record?.updatedAt) {
      editingRecordUpdatedAt = normalizeRecordVersion(responseData.record.updatedAt);
    }

    try {
      if (
        typeof responseData.id !== "string" ||
        !responseData.id ||
        !responseData.record ||
        typeof responseData.record !== "object"
      ) {
        throw new Error("The saved Daily Record could not be displayed. Reload to view it.");
      }
      updateDisplayedDailyRecord(responseData.id, responseData.record);
    } catch (error) {
      console.error("Daily Report was saved, but the report card could not be updated.", error);
      showToast(
        "Daily Record was saved, but the report card could not be updated. Reload to view it.",
        "error",
        6000
      );
      return;
    }

    editingId = null;
    editingRecordUpdatedAt = null;
    const modal = $("dr-modal");
    if (modal) {
      unregisterEscClose(modal);
      modal.classList.add("dr-hidden");
    }
    e.target.reset();
    updateFloorCustomField();
    $("dr-tradeSelect").disabled = false;
    $("dr-tradeSelect").required = true;
    $("dr-tradeStatus").textContent = "";
    renderWorkerCategories(getTradeCategories(getSelectedTrade()));
    updateWorkforceTotal();
    $("dr-date").value = selectedDate;
    $("dr-evidenceRows").innerHTML = "";
    $("dr-livePreview").innerHTML = "Paste a YouTube, Google Drive, or Google Photos link above.";

    const submitButton = $("dr-recordForm").querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.querySelector("span").textContent = "Save Daily Record";
    }

    const title = $("dr-modal").querySelector("h2");
    if (title) {
      title.querySelector("span").textContent = "New Daily Record";
    }

    loadCalendarReportDates();
    showToast(
      isEditing ? "Daily Record updated successfully." : "Daily Record saved successfully.",
      "success",
      3000
    );
  }

  async function resolveEvidenceImageUrl(item) {
    if (!item || !item.url) {
      return null;
    }

    const url = String(item.url).trim();
    if (!url) {
      return null;
    }

    if (item.type === "Photo" && isGooglePhotosUrl(url)) {
      const directUrl = await getGooglePhotosDirectUrl(url);
      return directUrl || url;
    }

    if (item.type === "Photo" && /\.(png|jpe?g|gif|webp|bmp|svg)(\?.*)?$/i.test(url)) {
      return url;
    }

    const driveCandidates = getDriveImageCandidates(url);
    if (driveCandidates.length && item.type === "Photo") {
      return driveCandidates[0];
    }

    return url;
  }

  function isVideoEvidence(item) {
    return String(item?.type || "").toLowerCase() === "video";
  }

  function getVideoThumbnailCandidates(item) {
    if (!item || !item.url) {
      return [];
    }

    const url = String(item.url).trim();
    const savedThumbnail = item.thumbnailUrl || item.thumbnail || item.poster || item.previewUrl;
    const candidates = [];
    if (savedThumbnail) {
      candidates.push(String(savedThumbnail).trim());
    }

    const youtubeVideoId = youtubeId(url);
    if (youtubeVideoId) {
      candidates.push(`https://img.youtube.com/vi/${encodeURIComponent(youtubeVideoId)}/maxresdefault.jpg`);
      candidates.push(`https://img.youtube.com/vi/${encodeURIComponent(youtubeVideoId)}/hqdefault.jpg`);
    }

    const googleDriveId = driveId(url);
    if (googleDriveId) {
      candidates.push(`https://lh3.googleusercontent.com/d/${encodeURIComponent(googleDriveId)}`);
      candidates.push(`https://drive.google.com/thumbnail?id=${encodeURIComponent(googleDriveId)}&sz=w1000`);
    }

    return [...new Set(candidates.filter(Boolean))];
  }

  function getVideoThumbnailUrl(item) {
    return getVideoThumbnailCandidates(item)[0] || null;
  }

  async function generateVideoFrameThumbnail(url) {
    if (!url || youtubeId(url) || driveId(url) || isGooglePhotosUrl(url)) {
      return null;
    }

    return new Promise(resolve => {
      const video = document.createElement("video");
      const canvas = document.createElement("canvas");
      let settled = false;
      const finish = value => {
        if (settled) return;
        settled = true;
        video.removeAttribute("src");
        video.load();
        resolve(value);
      };
      const timeout = setTimeout(() => finish(null), 8000);

      video.muted = true;
      video.playsInline = true;
      video.preload = "metadata";
      video.crossOrigin = "anonymous";
      video.onloadeddata = () => {
        try {
          const width = Math.min(video.videoWidth || 640, 1280);
          const height = Math.max(1, Math.round(width * (video.videoHeight || 360) / (video.videoWidth || 640)));
          canvas.width = width;
          canvas.height = height;
          canvas.getContext("2d").drawImage(video, 0, 0, width, height);
          clearTimeout(timeout);
          finish(canvas.toDataURL("image/jpeg", 0.86));
        } catch (error) {
          clearTimeout(timeout);
          finish(null);
        }
      };
      video.onerror = () => {
        clearTimeout(timeout);
        finish(null);
      };
      video.src = url;
      video.load();
    });
  }

  function waitForPrintImage(img, source) {
    if (!img || !source) {
      return Promise.resolve(false);
    }

    return new Promise(resolve => {
      let settled = false;
      const finish = loaded => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        img.onload = null;
        img.onerror = null;
        resolve(loaded);
      };
      const timeout = setTimeout(() => finish(false), 10000);
      img.referrerPolicy = "no-referrer";
      img.onload = () => finish(true);
      img.onerror = () => finish(false);
      img.src = source;
      if (img.complete && img.naturalWidth > 0) {
        finish(true);
      }
    });
  }

  function showPrintEvidenceFallback(img, message) {
    if (!img) return;
    img.style.display = "none";
    const fallback = img.parentNode?.parentNode?.querySelector(".dr-print-evidence-fallback");
    if (fallback) {
      fallback.textContent = message;
      fallback.style.display = "block";
    }
  }

  function buildPrintMarkup(record) {
    const savedCounts = record.workerCounts && typeof record.workerCounts === "object"
      ? record.workerCounts
      : {
        "Masons": Number(record.masonCount ?? 0),
        "Man Mazdoor": Number(record.manMazdoorCount ?? 0),
        "Woman Mazdoor": Number(record.womanMazdoorCount ?? 0)
      };

    const workerEntries = Object.entries(savedCounts).filter(([_, count]) => Number(count || 0) > 0);
    const weatherData = normalizeWeatherData({
      weatherCondition: record.weatherCondition,
      weatherDescription: record.weatherDescription
    });
    const weatherIcon = WEATHER_ICONS[weatherData.weatherCondition] || "◌";
    const weatherLabel = weatherData.weatherCondition
      ? weatherData.weatherCondition === "Other" && weatherData.weatherDescription
        ? `${weatherIcon} ${weatherData.weatherCondition} — ${weatherData.weatherDescription}`
        : `${weatherIcon} ${weatherData.weatherCondition}`
      : "Not recorded";
    const tradeName = record.tradeName || record.tradeId || "Not specified";
    const totalWorkers = Number(record.totalWorkers ?? Object.values(savedCounts).reduce((sum, value) => sum + Number(value || 0), 0));
    const notes = record.notes || "";
    const materials = record.materials || "";
    const extraNotes = record.remarks || record.additionalItems || "";
    const evidence = Array.isArray(record.evidence) ? record.evidence : [];
    const postedBy = formatPostedByMarkup(record);
    const lastEdited = formatLastEditedMarkup(record);

    const workerRows = workerEntries.length
      ? workerEntries.map(([name, count]) => `<tr><td>${escapeHtml(name)}</td><td class="dr-print-number">${Number(count || 0)}</td></tr>`).join("")
      : `<tr><td colspan="2">No workforce data recorded.</td></tr>`;

    const buildEvidencePages = (items) => {
      const safeItems = Array.isArray(items) ? items.filter(Boolean) : [];

      if (!safeItems.length) {
        return `
          <section class="dr-print-evidence-page">
            <h3>VISUAL EVIDENCE</h3>
            <div class="dr-print-evidence-grid">
              <div class="dr-print-placeholder">No visual evidence attached to this report.</div>
            </div>
          </section>
        `;
      }

      const pageGroups = [];
      for (let index = 0; index < safeItems.length; index += 4) {
        const pageItems = safeItems.slice(index, index + 4);
        const pageMarkup = pageItems.map((item, itemIndex) => {
          const imgUrl = item.previewUrl || (item.url ? String(item.url).trim() : "");
          const label = item.title || item.type || "Evidence";
            const evidenceIndex = index + itemIndex;
            const videoLabel = isVideoEvidence(item) ? "VIDEO" : "";
            const driveCandidates = item.type === "Photo" ? getDriveImageCandidates(imgUrl) : [];
            const initialThumbnail = isVideoEvidence(item)
              ? getVideoThumbnailUrl(item)
              : (driveCandidates.length ? driveCandidates[0] : imgUrl);
          return `<figure class="dr-print-evidence-item">
            <div class="dr-print-evidence-thumb">
                <img src="${escapeHtml(initialThumbnail || "")}" referrerpolicy="no-referrer" data-evidence-index="${evidenceIndex}" data-evidence-type="${escapeHtml(item.type || "")}" alt="${escapeHtml(label)}" />
                <div class="dr-print-evidence-fallback" style="display:none;">${isVideoEvidence(item) ? "Video thumbnail unavailable" : "Image unavailable"}</div>
            </div>
              <figcaption>${escapeHtml(label)}${videoLabel ? ` <span class="dr-print-video-label">${videoLabel}</span>` : ""}</figcaption>
          </figure>`;
        }).join("");

        pageGroups.push(`
          <section class="dr-print-evidence-page">
            <h3>VISUAL EVIDENCE</h3>
            <div class="dr-print-evidence-grid">${pageMarkup}</div>
          </section>
        `);
      }

      return pageGroups.join("");
    };

    const evidencePagesMarkup = buildEvidencePages(evidence);

    return `
      <div class="dr-print-host">
        <header class="dr-print-header">
          <div>
            <div class="dr-print-brand">MANNAT MOON</div>
            <div class="dr-print-sub">Construction Documentation</div>
          </div>
          <div class="dr-print-header-right">
            <div class="dr-print-header-title">DAILY CONSTRUCTION REPORT</div>
            <div class="dr-print-meta">Report Date: ${escapeHtml(formatPrintDate(record.date))} | ${postedBy}</div>
            ${lastEdited ? `<div class="dr-print-meta">${lastEdited}</div>` : ""}
            <div class="dr-print-meta">Weather: ${escapeHtml(weatherLabel)}</div>
          </div>
        </header>

        <section class="dr-print-section">
          <div class="dr-print-summary-row">
            <div class="dr-print-summary-block">
              <div class="dr-print-summary-label">Work / Activity</div>
              <div class="dr-print-summary-value">${escapeHtml(record.activity || "Daily Site Record")}</div>
            </div>
          </div>

          <div class="dr-print-grid">
            <div class="dr-print-pair">
              <div class="dr-print-label">Trade</div>
              <div class="dr-print-value">${escapeHtml(tradeName)}</div>
            </div>
            <div class="dr-print-pair">
              <div class="dr-print-label">Location / Floor</div>
              <div class="dr-print-value">${escapeHtml(record.floor || "Location not specified")}</div>
            </div>
          </div>
        </section>

        <section class="dr-print-section">
          <h3>WORKFORCE SUMMARY</h3>
          <table class="dr-print-table">
            <thead>
              <tr>
                <th>Worker Category</th>
                <th class="dr-print-number">Count</th>
              </tr>
            </thead>
            <tbody>
              ${workerRows}
              <tr class="dr-print-total-row">
                <td><strong>Total Workers</strong></td>
                <td class="dr-print-number"><strong>${Number(totalWorkers || 0)}</strong></td>
              </tr>
            </tbody>
          </table>
        </section>

        <section class="dr-print-section">
          <h3>MATERIALS</h3>
          <div class="dr-print-block">${escapeHtml(materials || "No materials recorded.")}</div>
        </section>

        <section class="dr-print-section">
          <h3>WORK DONE TODAY</h3>
          <div class="dr-print-block">${escapeHtml(notes || "No work description recorded.")}</div>
        </section>

        ${extraNotes ? `<section class="dr-print-section"><h3>REMARKS / ADDITIONAL ITEMS</h3><div class="dr-print-block">${escapeHtml(extraNotes)}</div></section>` : ""}

        ${evidencePagesMarkup}
      </div>
    `;
  }

  async function printSingleRecord(id) {
    const record = recordCache.get(id);
    if (!record) {
      showToast("Record not found.", "error", 5000);
      return;
    }

    const printWindow = window.open('', '_blank', 'width=1200,height=900');
    if (!printWindow) {
      showToast("Please allow pop-ups to print this report.", "error", 5000);
      return;
    }

    const evidence = Array.isArray(record.evidence) ? record.evidence : [];
    const printHtml = buildPrintMarkup(record);

    printWindow.document.write(`<!DOCTYPE html>
      <html>
      <head>
        <meta charset="UTF-8" />
        <title>Daily Report</title>
        <style>
          @page { size: A4 portrait; margin: 15mm; }
          html, body { width: auto; margin: 0; padding: 0; background: #fff; color: #18212b; font-family: Arial, sans-serif; font-size: 10.5pt; }
          body { padding: 0; }
          * { box-sizing: border-box; }
          .dr-print-host { width: 100%; max-width: 100%; margin: 0; padding: 0; box-sizing: border-box; color: #18212b; }
          .dr-print-header { background: #E5E7EB; color: #1F2937; padding: 12px 14px; display: flex; justify-content: space-between; align-items: flex-start; gap: 18px; border: 1px solid #D1D5DB; }
          .dr-print-brand { font-size: 22pt; font-weight: 800; letter-spacing: 0.04em; color: #1F2937; }
          .dr-print-sub { font-size: 10pt; color: #4B5563; margin-top: 4px; }
          .dr-print-header-right { text-align: right; display: flex; flex-direction: column; gap: 4px; color: #1F2937; }
          .dr-print-header-title { font-size: 12pt; font-weight: 700; letter-spacing: 0.06em; color: #1F2937; }
          .dr-print-meta { font-size: 9.5pt; color: #4B5563; }
          .dr-user-attribution { display: inline-flex; align-items: center; gap: 4px; vertical-align: middle; }
          .dr-author-avatar { display: inline-flex; width: 24px; height: 24px; overflow: hidden; flex: 0 0 24px; border-radius: 50%; vertical-align: middle; }
          .animal-avatar-svg { display: block; width: 100%; height: 100%; }
          .dr-print-section { margin-top: 18px; page-break-inside: avoid; }
          .dr-print-section h3 { margin: 0 0 10px; font-size: 12.5pt; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: #1a2433; border-bottom: 1px solid #dfe6ee; padding-bottom: 6px; }
          .dr-print-summary-row { margin-bottom: 12px; }
          .dr-print-summary-block { background: #f8fafc; border: 1px solid #dfe6ee; border-radius: 8px; padding: 10px 12px; }
          .dr-print-summary-label { font-size: 9pt; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #667085; }
          .dr-print-summary-value { margin-top: 6px; font-size: 15pt; font-weight: 700; }
          .dr-print-grid { display: grid; grid-template-columns: repeat(2, minmax(180px, 1fr)); gap: 12px 18px; }
          .dr-print-pair { background: #fbfcfe; border: 1px solid #e2e7ec; border-radius: 8px; padding: 10px 12px; }
          .dr-print-label { font-size: 8.5pt; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #667085; }
          .dr-print-value { margin-top: 4px; font-size: 10.5pt; color: #18212b; white-space: pre-wrap; word-break: break-word; }
          .dr-print-table { width: 100%; border-collapse: collapse; margin-top: 8px; border: 1px solid #dfe6ee; }
          .dr-print-table th, .dr-print-table td { border: 1px solid #dfe6ee; padding: 8px 10px; text-align: left; font-size: 10pt; }
          .dr-print-table th { background: #f2f6fb; font-weight: 700; }
          .dr-print-number { text-align: right; }
          .dr-print-total-row td { background: #edf6ff; font-weight: 700; }
          .dr-print-block { background: #fff; border: 1px solid #e2e7ec; border-radius: 8px; padding: 12px; font-size: 10.5pt; white-space: pre-wrap; word-break: break-word; }
          .dr-print-placeholder { display: block; border: 1px dashed #cbd5e1; border-radius: 8px; background: #f8fafc; color: #667085; padding: 12px; font-size: 10pt; }
          .dr-print-evidence-page { break-before: page; page-break-before: always; margin-top: 18px; }
          .dr-print-evidence-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10mm; }
          .dr-print-evidence-item { margin: 0; border: 1px solid #e2e7ec; border-radius: 8px; background: #fff; overflow: hidden; break-inside: avoid; page-break-inside: avoid; }
          .dr-print-evidence-thumb { width: 100%; height: 55mm; background: #f8fafc; display: flex; align-items: center; justify-content: center; overflow: hidden; }
          .dr-print-evidence-thumb img { display: block; width: 100%; height: 55mm; object-fit: contain; border: 0; background: #f8fafc; }
          .dr-print-evidence-fallback { display: none; text-align: center; color: #667085; font-size: 10pt; padding: 26px 12px; }
          .dr-print-evidence-item figcaption { padding: 8px 10px; font-size: 9.5pt; color: #475467; background: #fff; border-top: 1px solid #e2e7ec; }
          .dr-print-video-label { display: inline-block; margin-left: 4px; color: #475467; font-size: 8pt; font-weight: 700; letter-spacing: 0.08em; }
          .dr-print-evidence-page:first-child { break-before: page; page-break-before: always; }
          @media print { body { -webkit-print-color-adjust: exact; print-color-adjust: exact; } }
          @media (max-width: 700px) { .dr-print-grid, .dr-print-evidence-grid { grid-template-columns: 1fr; } .dr-print-header { flex-direction: column; } .dr-print-header-right { text-align: left; } }
        </style>
      </head>
      <body>${printHtml}</body>
      </html>`);
    printWindow.document.close();

    const evidenceElements = [...printWindow.document.querySelectorAll('.dr-print-evidence-item img')];
    const imagePromises = evidenceElements.map(async img => {
      const index = Number(img.dataset.evidenceIndex);
      const item = evidence[index];
      if (!item || !item.url) {
        showPrintEvidenceFallback(img, isVideoEvidence(item) ? "Video thumbnail unavailable" : "Image unavailable");
        return;
      }

      if (isVideoEvidence(item)) {
        const thumbnailCandidates = getVideoThumbnailCandidates(item);
        for (const thumbnailUrl of thumbnailCandidates) {
          if (await waitForPrintImage(img, thumbnailUrl)) {
            return;
          }
        }

        const generatedThumbnail = await generateVideoFrameThumbnail(String(item.url).trim());
        if (generatedThumbnail && await waitForPrintImage(img, generatedThumbnail)) {
          return;
        }

        showPrintEvidenceFallback(img, "Video thumbnail unavailable");
        return;
      }

      const driveCandidates = item.type === "Photo" ? getDriveImageCandidates(item.url) : [];
      if (driveCandidates.length) {
        for (const candidate of driveCandidates) {
          if (await waitForPrintImage(img, candidate)) return;
        }
      } else {
        const directUrl = await resolveEvidenceImageUrl(item);
        if (directUrl && await waitForPrintImage(img, directUrl)) return;
      }
      showPrintEvidenceFallback(img, "Image unavailable");
    });

    try {
      await Promise.all(imagePromises);
      setTimeout(() => {
        printWindow.focus();
        printWindow.print();
      }, 300);
    } catch (error) {
      console.error("Print image load failed:", error);
      setTimeout(() => {
        printWindow.focus();
        printWindow.print();
      }, 200);
    }
  }

  async function printSelectedDate() {
    const records = [...recordCache.values()]
      .filter(record => record?.date === selectedDate);

    const printWindow = window.open("", "_blank", "width=1200,height=900");
    if (!printWindow) {
      showToast("Please allow pop-ups to print this Daily Page.", "error", 5000);
      return;
    }

    const recordsMarkup = records.length
      ? records.map(record => buildPrintMarkup(record)).join("")
      : '<div class="dr-print-host"><section class="dr-print-section"><h3>Daily Reports</h3><div class="dr-print-placeholder">No Daily Reports for this date.</div></section></div>';

    printWindow.document.write(`<!DOCTYPE html>
      <html>
      <head>
        <meta charset="UTF-8" />
        <title>Mannat Moon Daily Reports - ${escapeHtml(selectedDate)}</title>
        <style>
          @page { size: A4 portrait; margin: 15mm; }
          html, body { width: auto; margin: 0; padding: 0; background: #fff; color: #18212b; font-family: Arial, sans-serif; font-size: 10.5pt; }
          * { box-sizing: border-box; }
          .dr-day-print-heading { margin-bottom: 16px; padding-bottom: 10px; border-bottom: 2px solid #d1d5db; }
          .dr-day-print-brand { font-size: 22pt; font-weight: 800; letter-spacing: .04em; color: #1f2937; }
          .dr-day-print-sub { margin-top: 4px; color: #4b5563; font-size: 10pt; }
          .dr-day-print-title { margin-top: 7px; font-size: 13pt; font-weight: 700; }
          .dr-day-print-date { margin-top: 5px; font-size: 10pt; color: #475467; }
          .dr-print-host { width: 100%; max-width: 100%; margin: 0; padding: 0; box-sizing: border-box; color: #18212b; break-after: page; page-break-after: always; }
          .dr-print-host:last-child { break-after: auto; page-break-after: auto; }
          .dr-print-header { background: #e5e7eb; color: #1f2937; padding: 12px 14px; display: flex; justify-content: space-between; align-items: flex-start; gap: 18px; border: 1px solid #d1d5db; }
          .dr-print-brand { font-size: 22pt; font-weight: 800; letter-spacing: .04em; color: #1f2937; }
          .dr-print-sub { font-size: 10pt; color: #4b5563; margin-top: 4px; }
          .dr-print-header-right { text-align: right; display: flex; flex-direction: column; gap: 4px; color: #1f2937; }
          .dr-print-header-title { font-size: 12pt; font-weight: 700; letter-spacing: .06em; color: #1f2937; }
          .dr-print-meta { font-size: 9.5pt; color: #4b5563; }
          .dr-user-attribution { display: inline-flex; align-items: center; gap: 4px; vertical-align: middle; }
          .dr-author-avatar { display: inline-flex; width: 24px; height: 24px; overflow: hidden; flex: 0 0 24px; border-radius: 50%; vertical-align: middle; }
          .animal-avatar-svg { display: block; width: 100%; height: 100%; }
          .dr-print-section { margin-top: 18px; page-break-inside: avoid; }
          .dr-print-section h3 { margin: 0 0 10px; font-size: 12.5pt; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; color: #1a2433; border-bottom: 1px solid #dfe6ee; padding-bottom: 6px; }
          .dr-print-summary-row { margin-bottom: 12px; }
          .dr-print-summary-block { background: #f8fafc; border: 1px solid #dfe6ee; border-radius: 8px; padding: 10px 12px; }
          .dr-print-summary-label { font-size: 9pt; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: #667085; }
          .dr-print-summary-value { margin-top: 6px; font-size: 15pt; font-weight: 700; }
          .dr-print-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 18px; }
          .dr-print-pair { background: #fbfcfe; border: 1px solid #e2e7ec; border-radius: 8px; padding: 10px 12px; }
          .dr-print-label { font-size: 8.5pt; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: #667085; }
          .dr-print-value { margin-top: 4px; font-size: 10.5pt; color: #18212b; white-space: pre-wrap; word-break: break-word; }
          .dr-print-table { width: 100%; border-collapse: collapse; margin-top: 8px; border: 1px solid #dfe6ee; }
          .dr-print-table th, .dr-print-table td { border: 1px solid #dfe6ee; padding: 8px 10px; text-align: left; font-size: 10pt; }
          .dr-print-table th { background: #f2f6fb; font-weight: 700; }
          .dr-print-number { text-align: right; }
          .dr-print-total-row td { background: #edf6ff; font-weight: 700; }
          .dr-print-block { background: #fff; border: 1px solid #e2e7ec; border-radius: 8px; padding: 12px; font-size: 10.5pt; white-space: pre-wrap; word-break: break-word; }
          .dr-print-placeholder { display: block; border: 1px dashed #cbd5e1; border-radius: 8px; background: #f8fafc; color: #667085; padding: 12px; font-size: 10pt; }
          .dr-print-evidence-page { break-before: page; page-break-before: always; margin-top: 18px; }
          .dr-print-evidence-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10mm; }
          .dr-print-evidence-item { margin: 0; border: 1px solid #e2e7ec; border-radius: 8px; background: #fff; overflow: hidden; break-inside: avoid; page-break-inside: avoid; }
          .dr-print-evidence-thumb { width: 100%; height: 55mm; background: #f8fafc; display: flex; align-items: center; justify-content: center; overflow: hidden; }
          .dr-print-evidence-thumb img { display: block; width: 100%; height: 55mm; object-fit: contain; border: 0; background: #f8fafc; }
          .dr-print-evidence-fallback { display: none; text-align: center; color: #667085; font-size: 10pt; padding: 26px 12px; }
          .dr-print-evidence-item figcaption { padding: 8px 10px; font-size: 9.5pt; color: #475467; background: #fff; border-top: 1px solid #e2e7ec; }
          .dr-print-video-label { display: inline-block; margin-left: 4px; color: #475467; font-size: 8pt; font-weight: 700; letter-spacing: .08em; }
          @media print { body { -webkit-print-color-adjust: exact; print-color-adjust: exact; } }
        </style>
      </head>
      <body>
        <header class="dr-day-print-heading">
          <div class="dr-day-print-brand">MANNAT MOON</div>
          <div class="dr-day-print-sub">Construction Documentation</div>
          <div class="dr-day-print-title">DAILY REPORTS</div>
          <div class="dr-day-print-date">Selected Date: ${escapeHtml(formatSelectedDate(selectedDate))}</div>
        </header>
        ${recordsMarkup}
      </body>
      </html>`);
    printWindow.document.close();

    const imagePromises = [];
    const hosts = [...printWindow.document.querySelectorAll(".dr-print-host")];

    hosts.forEach((host, recordIndex) => {
      const record = records[recordIndex];
      if (!record) return;
      const evidence = Array.isArray(record.evidence) ? record.evidence : [];
      const images = [...host.querySelectorAll(".dr-print-evidence-item img")];

      images.forEach((img, imageIndex) => {
        const item = evidence[imageIndex];
        imagePromises.push((async () => {
          if (!item?.url) {
            showPrintEvidenceFallback(img, "Image unavailable");
            return;
          }

          if (isVideoEvidence(item)) {
            for (const candidate of getVideoThumbnailCandidates(item)) {
              if (await waitForPrintImage(img, candidate)) return;
            }
            const generated = await generateVideoFrameThumbnail(String(item.url).trim());
            if (generated && await waitForPrintImage(img, generated)) return;
            showPrintEvidenceFallback(img, "Video thumbnail unavailable");
            return;
          }

          const driveCandidates = item.type === "Photo" ? getDriveImageCandidates(item.url) : [];
          if (driveCandidates.length) {
            for (const candidate of driveCandidates) {
              if (await waitForPrintImage(img, candidate)) return;
            }
          } else {
            const imageUrl = await resolveEvidenceImageUrl(item);
            if (imageUrl && await waitForPrintImage(img, imageUrl)) return;
          }
          showPrintEvidenceFallback(img, "Image unavailable");
        })());
      });
    });

    try {
      await Promise.all(imagePromises);
    } catch (error) {
      console.error("Daily Page print image loading failed:", error);
    }

    setTimeout(() => {
      printWindow.focus();
      printWindow.print();
    }, 350);
  }

  function renderDailyRecordCard(recordId, record) {
    const evidence = getUniqueDailyReportEvidence(record);
    const savedCounts = record.workerCounts && typeof record.workerCounts === "object"
      ? record.workerCounts
      : {
        "Masons": Number(record.masonCount ?? 0),
        "Man Mazdoor": Number(record.manMazdoorCount ?? 0),
        "Woman Mazdoor": Number(record.womanMazdoorCount ?? 0)
      };
    const totalWorkers = Number(record.totalWorkers ?? record.manpower ?? Object.values(savedCounts)
      .reduce((total, value) => total + Number(value || 0), 0));
    const tradeName = record.tradeName || record.tradeId || "Trade not specified";
    const postedBy = getPostedBy(record);
    const lastEdited = getLastEditedDetails(record);
    const workerSummary = Object.entries(savedCounts)
      .map(([name, count]) => `<div>${escapeHtml(name)}: ${Number(count || 0)}</div>`)
      .join("");
    return `<article class="dr-card" data-record-id="${escapeHtml(recordId)}">
      <div class="dr-date-row">
        <div class="dr-date">${escapeHtml(formatDate(record.date, ""))}</div>
        <div class="dr-author-details">
          <div class="dr-posted-by">Posted by: ${renderUserAttribution(postedBy.fullName, postedBy.userId, postedBy.username, "Posted by")}${postedBy.username ? ` (<span class="dr-posted-username">@${escapeHtml(postedBy.username)}</span>)` : ""}</div>
          ${lastEdited ? `<div class="dr-last-edited">Last edited by: ${renderUserAttribution(lastEdited.fullName, lastEdited.userId, lastEdited.username, "Last edited by")}${lastEdited.username ? ` (@${escapeHtml(lastEdited.username)})` : ""} | ${escapeHtml(lastEdited.editedAt)}</div>` : ""}
        </div>
        <div class="dr-weather-actions">
          ${getWeatherBadgeMarkup(record.weatherCondition, record.weatherDescription)}
          <button type="button" class="dr-print-btn" data-action="print" data-id="${escapeHtml(recordId)}">🖨 Print</button>
        </div>
      </div>
      <div class="dr-record-actions">
        <button type="button" class="dr-edit-btn" data-action="edit" data-id="${escapeHtml(recordId)}">Edit</button>
        <button type="button" class="dr-delete-btn" data-action="delete" data-id="${escapeHtml(recordId)}">Delete</button>
      </div>
      <h2>${escapeHtml(record.activity || "Daily Site Record")}</h2>
      <div class="dr-meta">
        <span class="dr-tag">Trade: ${escapeHtml(tradeName)}</span>
        <span class="dr-tag">${escapeHtml(record.floor || "Location not specified")}</span>
      </div>
      <div class="dr-workforce-card">
        ${workerSummary}
        <div>Total Workers: ${totalWorkers}</div>
      </div>
      ${record.materials ? `<h3>Materials</h3><div class="dr-pre">${escapeHtml(record.materials)}</div>` : ""}
      ${record.notes ? `<h3>Work Done Today</h3><div class="dr-pre">${escapeHtml(record.notes)}</div>` : ""}
      ${evidence.length ? `<h3>Evidence</h3><div class="dr-evidence">${evidence.map(item => `<div class="dr-ev" data-evidence-file-id="${escapeHtml(item.fileId || "")}"><div class="dr-evtitle">${escapeHtml(item.type)} — ${escapeHtml(item.title || "")}</div>${previewHtml(item.type, item.url, item.previewUrl)}<div class="dr-ev-actions"><a class="dr-open" target="_blank" href="${escapeHtml(item.url)}">Open original</a></div></div>`).join("")}</div>` : ""}
    </article>`;
  }

  function updateDisplayedDailyRecord(recordId, record) {
    const recordsElement = $("dr-records");
    const previousRecord = recordCache.get(recordId);
    record = {
      ...record,
      evidence: getUniqueDailyReportEvidence(record)
    };
    if (previousRecord?.createdByUserId) {
      record.createdByUserId = previousRecord.createdByUserId;
    }
    const existingCard = [...recordsElement.querySelectorAll(".dr-card[data-record-id]")]
      .find(card => card.dataset.recordId === recordId);
    if (record.date !== selectedDate) {
      existingCard?.remove();
      recordCache.delete(recordId);
    } else {
      recordCache.set(recordId, record);
      const cardMarkup = renderDailyRecordCard(recordId, record);
      if (existingCard) {
        existingCard.outerHTML = cardMarkup;
      } else {
        recordsElement.querySelector(".dr-empty-day")?.remove();
        recordsElement.insertAdjacentHTML("beforeend", cardMarkup);
      }
    }
    const visibleRecords = [...recordCache.values()]
      .filter(item => belongsToSelectedDate(item, selectedDate));
    $("dr-recordCount").textContent = visibleRecords.length;
    $("dr-evidenceCount").textContent = visibleRecords.reduce(
      (count, item) => count + getUniqueDailyReportEvidence(item).length,
      0
    );
    if (!visibleRecords.length) {
      recordsElement.innerHTML = '<div class="dr-card dr-empty-day"><h2>No Daily Reports for this date</h2></div>';
    }
    const updatedCard = [...recordsElement.querySelectorAll(".dr-card[data-record-id]")]
      .find(card => card.dataset.recordId === recordId);
    updatedCard?.querySelectorAll(".dr-ev-img").forEach(image => {
      image.loading = "eager";
    });
    observeGooglePhotosPreviews(recordsElement);
  }

  function getUniqueDailyReportEvidence(record) {
    const seenFileIds = new Set();
    return (Array.isArray(record?.evidence) ? record.evidence : []).filter(item => {
      if (!item || typeof item !== "object" || typeof item.fileId !== "string" || !item.fileId) {
        return true;
      }
      if (seenFileIds.has(item.fileId)) return false;
      seenFileIds.add(item.fileId);
      return true;
    });
  }

  function stopRecordsSubscription() {
    recordsListenerId += 1;
    recordsRenderId += 1;
    recordsUnsubscribe?.();
    recordsUnsubscribe = null;
    recordsSubscriptionDate = null;
  }

  function subscribeToRecords() {
    if (!db) {
      stopRecordsSubscription();
      $("dr-recordCount").textContent = "—";
      $("dr-evidenceCount").textContent = "—";
      setDataStatus(describeFirebaseError(firebaseBootstrapError), true);
      $("dr-records").innerHTML = '<div class="dr-card"><b>Daily Reports are unavailable.</b><p>Please contact your administrator for help.</p></div>';
      return;
    }

    if (recordsSubscriptionDate === selectedDate && recordsUnsubscribe) return;
    stopRecordsSubscription();
    const requestedDate = selectedDate;
    const listenerId = recordsListenerId;
    recordsSubscriptionDate = requestedDate;
    let hasReceivedSnapshot = false;
    let renderPending = false;
    setDataStatus("Loading Daily Reports...");

    recordsUnsubscribe = onSnapshot(
      query(collection(db, "daily_records"), where("date", "==", requestedDate)),
      { includeMetadataChanges: true },
      async snapshot => {
        if (listenerId !== recordsListenerId || selectedDate !== requestedDate) return;
        const changes = snapshot.docChanges({ includeMetadataChanges: false });
        if (hasReceivedSnapshot && changes.length === 0) {
          setDataStatus(snapshot.metadata.fromCache
            ? "Showing cached Daily Reports while reconnecting..."
            : "Daily Reports are up to date.");
          return;
        }

        const renderFullSnapshot = !hasReceivedSnapshot || renderPending;
        hasReceivedSnapshot = true;
        const renderId = ++recordsRenderId;
        const dailyRecords = snapshot.docs
          .map(recordDoc => ({ recordDoc, record: recordDoc.data() }))
          .filter(({ record }) => belongsToSelectedDate(record, requestedDate));
        const avatarRecords = renderFullSnapshot
          ? dailyRecords.map(({ record }) => record)
          : changes
            .filter(change => change.type !== "removed")
            .map(change => change.doc.data());
        renderPending = true;
        await loadAuthorAvatars(avatarRecords);
        if (
          listenerId !== recordsListenerId
          || renderId !== recordsRenderId
          || selectedDate !== requestedDate
        ) return;

        if (renderFullSnapshot) {
          recordCache.clear();
          const html = dailyRecords.map(({ recordDoc, record }) => {
            recordCache.set(recordDoc.id, record);
            return renderDailyRecordCard(recordDoc.id, record);
          }).join("");
          $("dr-records").innerHTML = html
            || '<div class="dr-card dr-empty-day"><h2>No Daily Reports for this date</h2></div>';
        } else {
          changes.forEach(change => {
            if (change.type === "removed") {
              recordCache.delete(change.doc.id);
              [...$("dr-records").querySelectorAll(".dr-card[data-record-id]")]
                .find(card => card.dataset.recordId === change.doc.id)
                ?.remove();
            } else {
              updateDisplayedDailyRecord(change.doc.id, change.doc.data());
            }
          });
          const visibleRecords = [...recordCache.values()]
            .filter(record => belongsToSelectedDate(record, requestedDate));
          if (!visibleRecords.length) {
            $("dr-records").innerHTML = '<div class="dr-card dr-empty-day"><h2>No Daily Reports for this date</h2></div>';
          }
          $("dr-recordCount").textContent = String(visibleRecords.length);
          $("dr-evidenceCount").textContent = String(visibleRecords.reduce(
            (count, record) => count + getUniqueDailyReportEvidence(record).length,
            0
          ));
        }
        renderPending = false;
        setDataStatus(snapshot.metadata.fromCache
          ? `Showing ${dailyRecords.length} cached Daily Report${dailyRecords.length === 1 ? "" : "s"} while reconnecting...`
          : dailyRecords.length
            ? `Loaded ${dailyRecords.length} Daily Report${dailyRecords.length === 1 ? "" : "s"} for this date.`
            : "Data loaded successfully. No Daily Reports exist for this date.");
        observeGooglePhotosPreviews($("dr-records"));
      },
      error => {
        if (listenerId !== recordsListenerId || selectedDate !== requestedDate) return;
        recordsRenderId += 1;
        recordCache.clear();
        $("dr-recordCount").textContent = "—";
        $("dr-evidenceCount").textContent = "—";
        setDataStatus(describeFirebaseError(error), true);
        $("dr-records").innerHTML = '<div class="dr-card dr-empty-day"><h2>Unable to load Daily Reports</h2><p>Check the connection and try again.</p></div>';
        console.error("Daily Reports Firestore listener failed", {
          operation: "daily_records query by selected date",
          code: error?.code || "unknown",
          message: error?.message || String(error),
          httpStatus: error?.customData?.httpStatus ?? error?.status ?? "unavailable"
        });
        showToast("Unable to load Daily Reports. Check the connection and try again.", "error", 5000);
      }
    );
  }

  // Initialize event listeners
  function init() {
    initializeDailyReportsDrive();
    updateDateUrl(true);
    window.addEventListener("popstate", () => {
      const dateFromUrl = getDateFromUrl();
      const date = isValidDateValue(dateFromUrl) ? dateFromUrl : getDateInputValue(new Date());
      setSelectedDate(date, { updateUrl: false });
      updateDateUrl(true);
    });

    // Button listeners
    $("dr-records").addEventListener("click", activateYouTubePreview);
    $("dr-records").addEventListener("click", handleEvidenceImageClick);
    $("dr-livePreview").addEventListener("click", activateYouTubePreview);
    $("dr-records").addEventListener("click", retryGooglePhotosPreview);
    $("dr-livePreview").addEventListener("click", retryGooglePhotosPreview);
    $("dr-addBtn").onclick = openAddModal;
    $("dr-prevDay").onclick = () => moveSelectedDate(-1);
    $("dr-nextDay").onclick = () => moveSelectedDate(1);
    $("dr-todayBtn").onclick = () => {
      setSelectedDate(getDateInputValue(new Date()));
      closeCalendar();
    };
    $("dr-calendarToggle").onclick = () => setCalendarOpen($("dr-calendar").hidden);
    $("dr-prevMonth").onclick = () => moveCalendarMonth(-1);
    $("dr-nextMonth").onclick = () => moveCalendarMonth(1);
    $("dr-calendarDays").addEventListener("click", event => {
      const dayButton = event.target.closest("button[data-date]");
      if (dayButton) {
        setSelectedDate(dayButton.dataset.date);
        closeCalendar();
      }
    });
    document.addEventListener("click", event => {
      const calendarControl = document.querySelector(".dr-calendar-control");
      if (calendarControl && !calendarControl.contains(event.target)) closeCalendar();
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape") closeCalendar();
    });
    $("dr-printDayBtn").onclick = printSelectedDate;
    $("dr-exportPdfBtn").onclick = printSelectedDate;
    $("dr-closeBtn").onclick = closeModal;
    $("dr-addEvidence").onclick = () => addEvidenceRow();
    $("dr-records").addEventListener("click", event => {
      const button = event.target.closest("button[data-action]");
      if (!button || !$("dr-records").contains(button)) return;
      if (button.dataset.action === "edit") {
        editRecord(button.dataset.id);
      } else if (button.dataset.action === "delete") {
        openDeleteConfirm(button.dataset.id);
      } else if (button.dataset.action === "print") {
        printSingleRecord(button.dataset.id);
      }
    });
    $("dr-tradeSelect").onchange = () => {
      $("dr-tradeSelect").required = true;
      $("dr-tradeStatus").textContent = "";
      renderWorkerCategories(getTradeCategories(getSelectedTrade()));
      updateWorkforceTotal();
    };
    $("dr-weatherCondition").onchange = updateWeatherCustomField;
    $("dr-floor").onchange = updateFloorCustomField;
    $("dr-cancelDeleteBtn").onclick = cancelDelete;
    $("dr-confirmDeleteBtn").onclick = confirmDeleteRecord;
    $("dr-recordForm").onsubmit = handleFormSubmit;

    // Load Firebase trade definitions before records so category fields are dynamic.
    renderCalendar();
    if (firebaseBootstrapError) {
      subscribeToRecords();
      return;
    }
    loadCalendarReportDates();
    loadTrades().then(async () => {
      await subscribeToTradeRevisions();
      subscribeToRecords();
    }).catch(error => {
      console.error("Daily Reports trades Firestore read failed", {
        operation: "trades collection query",
        code: error?.code || "unknown",
        message: error?.message || String(error),
        httpStatus: error?.customData?.httpStatus ?? error?.status ?? "unavailable"
      });
      const tradeSelect = $("dr-tradeSelect");
      tradeSelect.innerHTML = '<option value="">Unable to load trades.</option>';
      tradeSelect.disabled = true;
      $("dr-tradeStatus").textContent = `Trade options could not be loaded: ${error?.message || "Flask API request failed."}`;
      showToast("Unable to load trades. Please check your connection and try again.", "error", 5000);
      subscribeToRecords();
    });
    window.addEventListener("pagehide", () => {
      stopRecordsSubscription();
      tradeRevisionUnsubscribe?.();
      tradeRevisionUnsubscribe = null;
    }, { once: true });
  }

  // Public API
  return {
    init
  };

})();

// Initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', DailyReports.init);
} else {
  DailyReports.init();
}
