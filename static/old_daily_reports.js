import { initializeApp } from "https://www.gstatic.com/firebasejs/12.2.1/firebase-app.js";
import {
  getFirestore,
  collection,
  doc,
  getDocs,
  query,
  where
} from "https://www.gstatic.com/firebasejs/12.2.1/firebase-firestore.js";
import { registerEscClose, unregisterEscClose } from "./dialogs.js";
import { formatDate, formatDateTime, formatDateWithDay } from "./date_format.js";

const firebaseConfigResponse = await fetch("/api/firebase-web-config", { credentials: "same-origin" });
if (!firebaseConfigResponse.ok) {
  throw new Error("Unable to load the active Firebase Web App configuration.");
}
const firebaseConfig = await firebaseConfigResponse.json();

// =========================================================
// DAILY REPORTS NAMESPACE
// =========================================================

const DailyReports = (function() {

  const configured = !Object.values(firebaseConfig).some(v => v.startsWith("PASTE_"));
  let db = null;
  if (configured) {
    const app = initializeApp(firebaseConfig);
    db = getFirestore(app);
  }

  // Helper function to get elements
  const $ = id => document.getElementById(id);

  // State variables
  let editingId = null;
  let deletingId = null;
  let trades = [];
  let selectedDate = getDateInputValue(new Date());
  let calendarMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1, 12);
  let calendarReportDates = new Set();
  let calendarLoadId = 0;
  let recordsLoadId = 0;
  const recordCache = new Map();
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
  const PREVIEW_DEBOUNCE_MS = 800;
  
  // Track pending requests to cancel them when URL changes
  const pendingRequests = new Map();

  $("dr-date").value = selectedDate;
  $("dr-selectedDateLabel").textContent = formatSelectedDate(selectedDate);

  // Helper functions
  function getDateInputValue(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function formatSelectedDate(dateValue) {
    return formatDateWithDay(dateValue);
  }

  function setSelectedDate(dateValue) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dateValue || "")) {
      return;
    }

    const date = new Date(`${dateValue}T12:00:00`);
    if (Number.isNaN(date.getTime()) || getDateInputValue(date) !== dateValue) {
      return;
    }

    const previousMonth = `${calendarMonth.getFullYear()}-${calendarMonth.getMonth()}`;
    selectedDate = dateValue;
    calendarMonth = new Date(date.getFullYear(), date.getMonth(), 1, 12);
    $("dr-selectedDateLabel").textContent = formatSelectedDate(selectedDate);
    renderCalendar();
    if (previousMonth !== `${calendarMonth.getFullYear()}-${calendarMonth.getMonth()}`) {
      loadCalendarReportDates();
    }
    loadRecords();
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
      cells.push(`<button type="button" class="${classes.join(" ")}" data-date="${dateValue}" aria-label="${escapeHtml(label)}" aria-pressed="${dateValue === selectedDate}"><span>${day}</span>${hasReport ? '<i class="dr-calendar-report-dot" aria-hidden="true"></i>' : ""}</button>`);
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
        return `<label>${escapeHtml(name)}<input class="dr-worker-count" data-category="${escapeHtml(name)}" type="number" min="0" value="${clampNonNegative(value)}"></label>`;
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
    if (!dateValue) {
      return "Not specified";
    }

    const parsed = new Date(dateValue);
    if (Number.isNaN(parsed.getTime())) {
      return String(dateValue);
    }

    const day = String(parsed.getDate()).padStart(2, "0");
    const month = String(parsed.getMonth() + 1).padStart(2, "0");
    const year = parsed.getFullYear();
    return `${day}/${month}/${year}`;
  }

  function getPostedBy(record) {
    const fullName = String(record?.createdByName || "").trim() || "Unknown";
    const username = String(record?.createdByUsername || "").trim().replace(/^@+/, "");
    return { fullName, username };
  }

  function formatPostedBy(record) {
    const postedBy = getPostedBy(record);
    return `Posted by: ${postedBy.fullName}${postedBy.username ? ` (@${postedBy.username})` : ""}`;
  }

  function getLastEdited(record) {
    if (!record?.last_edited_at) {
      return "";
    }

    const fullName = String(record.last_edited_by_name || "").trim() || "Unknown";
    const username = String(record.last_edited_by_username || "").trim().replace(/^@+/, "");
    const editedAt = formatDateTime(record.last_edited_at);

    return `Last edited by: ${fullName}${username ? ` (@${username})` : ""} | ${editedAt}`;
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
    select.innerHTML = '<option value="">Select a trade</option>' + trades
      .filter(trade => getTradeCategories(trade).length)
      .sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")))
      .map(trade => `<option value="${escapeHtml(trade.id)}">${escapeHtml(trade.name || "Unnamed trade")}</option>`)
      .join("");
  }

  async function loadTrades() {
    if (!db) return;
    const snapshot = await getDocs(collection(db, "trades"));
    trades = snapshot.docs.map(item => ({ id: item.id, ...item.data() }));
    populateTrades();
    renderWorkerCategories(getTradeCategories(getSelectedTrade()));
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

  function isGooglePhotosUrl(url){
    try{
      const u = new URL(url);
      return u.hostname.includes("photos.app.goo.gl") || 
             u.hostname.includes("google.com") && u.pathname.includes("/photo/");
    }catch(e){
      return false;
    }
  }

  function getGooglePhotosDirectUrl(photosUrl, signal = null) {
    const fetchOptions = {};
    if (signal) {
      fetchOptions.signal = signal;
    }
    
    return fetch(`/extract-google-photos-url?url=${encodeURIComponent(photosUrl)}`, fetchOptions)
      .then(response => {
        if (response.ok) {
          return response.text();
        } else if (response.status === 500) {
          // Playwright error - return null to show fallback
          console.error("Google Photos extraction returned 500 error");
          return null;
        } else {
          console.error("Google Photos extraction returned status:", response.status);
          return null;
        }
      })
      .then(text => {
        // Validate the returned URL
        if (text && text.startsWith("http")) {
          // Fix protocol if malformed
          if (text.startsWith("https:/") && !text.startsWith("https://")) {
            text = text.replace("https:/", "https://", 1);
          }
          console.log("Google Photos direct URL obtained:", text.substring(0, 80) + "...");
          return text;
        }
        console.error("Invalid Google Photos URL returned:", text);
        return null;
      })
      .catch(e => {
        if (e.name === 'AbortError') {
          console.log("Google Photos extraction request cancelled");
          throw e; // Re-throw AbortError to handle it properly
        }
        console.error("Failed to extract Google Photos URL:", e);
        return null;
      });
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

  function previewHtml(type,url){
    const y = youtubeId(url);
    if(y) return youtubePreviewHtml(y);
    const d = driveId(url);
    if(d && type==="Photo") return `<iframe src="https://drive.google.com/file/d/${d}/preview"></iframe>`;
    if(d) return `<iframe src="https://drive.google.com/file/d/${d}/preview"></iframe>`;
    
    // Google Photos handling - return placeholder that will be updated asynchronously
    if(type==="Photo" && isGooglePhotosUrl(url)){
      return `<div class="dr-ev-preview-loading" data-photos-url="${escapeHtml(url)}">Loading Google Photos preview...</div>`;
    }
    
    // For other photos, try direct image display
    if(type==="Photo"){
      return `<img src="${escapeHtml(url)}" alt="Photo preview" class="dr-ev-img" onerror="this.style.display='none'; this.nextElementSibling.style.display='block';">
              <div class="dr-preview-error" style="display:none;">Photo preview unavailable</div>`;
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
      <button type="button" class="dr-remove">×</button>
    `;

    if (data.type) {
      row.querySelector(".dr-evtype").value = data.type;
    }

    row.querySelector(".dr-remove").onclick = () => {
      row.remove();
      updatePreview();
    };

    // Debounced URL input to avoid excessive extraction requests
    row.querySelector(".dr-evurl").oninput = () => {
      clearTimeout(previewDebounceTimer);
      previewDebounceTimer = setTimeout(updatePreview, PREVIEW_DEBOUNCE_MS);
    };
    row.querySelector(".dr-evtype").onchange = updatePreview;

    $("dr-evidenceRows").appendChild(row);
    updatePreview();
  }

  function updatePreview(){
    const rows=[...document.querySelectorAll(".dr-evrow")];
    if(rows.length){
      const previews = rows.map(row => previewHtml(
        row.querySelector(".dr-evtype").value,
        row.querySelector(".dr-evurl").value
      ));
      
      $("dr-livePreview").innerHTML = previews.join("");
      
      // Cancel any pending requests for this preview update
      pendingRequests.forEach((controller, key) => {
        controller.abort();
        pendingRequests.delete(key);
      });
      
      // Load Google Photos previews asynchronously
      document.querySelectorAll("#dr-livePreview .dr-ev-preview-loading").forEach(loadingEl => {
        const photosUrl = loadingEl.dataset.photosUrl;
        const requestId = `preview-${photosUrl}`;
        
        // Create AbortController for this request
        const controller = new AbortController();
        pendingRequests.set(requestId, controller);
        
        getGooglePhotosDirectUrl(photosUrl, controller.signal).then(directUrl => {
          // Check if request wasn't cancelled
          if (!controller.signal.aborted) {
            pendingRequests.delete(requestId);
            if(directUrl){
              loadingEl.innerHTML = `<img src="${escapeHtml(directUrl)}" alt="Photo preview" class="dr-ev-img" onerror="this.style.display='none'; this.nextElementSibling.style.display='block';">
              <div class="dr-preview-error" style="display:none;">Photo preview unavailable</div>`;
            } else {
              loadingEl.innerHTML = `<div class="dr-preview-error">Unable to load Google Photos preview</div>`;
            }
          }
        }).catch(err => {
          if (err.name !== 'AbortError') {
            console.error("Google Photos preview error:", err);
            pendingRequests.delete(requestId);
            if (!controller.signal.aborted) {
              loadingEl.innerHTML = `<div class="dr-preview-error">Unable to load Google Photos preview</div>`;
            }
          }
        });
      });
    } else {
      $("dr-livePreview").innerHTML = "Paste a YouTube, Google Drive, or Google Photos link above.";
    }
  }

  function openAddModal() {
    editingId = null;
    $("dr-recordForm").reset();
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
      submitButton.textContent = "Save Daily Record";
    }

    const title = $("dr-modal").querySelector("h2");
    if (title) {
      title.textContent = "New Daily Record";
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
        throw new Error(`Unable to delete the daily report (${response.status}).`);
      }
      deletingId = null;
      const deleteModal = $("dr-deleteModal");
      if (deleteModal) {
        unregisterEscClose(deleteModal);
        deleteModal.classList.add("dr-hidden");
      }
      showToast("Record deleted successfully.", "success", 3000);
      await loadRecords();
      loadCalendarReportDates();
    } catch (error) {
      console.error(error);
      showToast("Unable to delete the daily report. Please try again.", "error", 5000);
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
    $("dr-date").value = record.date || "";
    $("dr-floor").value = record.floor || "";
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
      submitButton.textContent = "Update Daily Record";
    }

    const title = $("dr-modal").querySelector("h2");
    if (title) {
      title.textContent = "Edit Daily Record";
    }
  }

  async function handleFormSubmit(e) {
    e.preventDefault();

    if (!db) {
      showToast("Daily Reports are temporarily unavailable. Please contact your administrator.", "error", 5000);
      return;
    }

    try {
      const evidence = [...document.querySelectorAll(".dr-evrow")]
        .map(row => ({
          type: row.querySelector(".dr-evtype").value,
          title: row.querySelector(".dr-evtitle").value.trim(),
          url: row.querySelector(".dr-evurl").value.trim()
        }))
        .filter(x => x.url);

      const workforce = getWorkforceValues();
      const selectedTrade = getSelectedTrade();
      const existingRecord = editingId ? recordCache.get(editingId) : null;
      const weatherData = getWeatherDataFromForm();
      const isEditing = Boolean(editingId);

      const recordData = {
        date: $("dr-date").value,
        floor: $("dr-floor").value,
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

      const response = await fetch(
        isEditing ? `/api/daily-records/${encodeURIComponent(editingId)}` : "/api/daily-records",
        {
          method: isEditing ? "PATCH" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(recordData)
        }
      );
      let responseData = {};
      try {
        responseData = await response.json();
      } catch (_) {
        // Keep a readable save error if the server did not return JSON.
      }
      if (!response.ok) {
        throw new Error(responseData.error || "Unable to save the daily report.");
      }

      if (isEditing) {
        showToast("Daily Record updated successfully.", "success", 3000);
      } else {
        showToast("Daily Record saved successfully.", "success", 3000);
      }

      editingId = null;
      const modal = $("dr-modal");
      if (modal) {
        unregisterEscClose(modal);
        modal.classList.add("dr-hidden");
      }
      e.target.reset();
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
        submitButton.textContent = "Save Daily Record";
      }

      const title = $("dr-modal").querySelector("h2");
      if (title) {
        title.textContent = "New Daily Record";
      }

      await loadRecords();
      loadCalendarReportDates();
    } catch (error) {
      console.error(error);
      showToast("Unable to save the daily report. Please check your connection and try again.", "error", 5000);
    }
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

    const driveId = driveId(url);
    if (driveId && item.type === "Photo") {
      return `https://drive.google.com/uc?export=view&id=${driveId}`;
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
    const postedBy = formatPostedBy(record);
    const lastEdited = getLastEdited(record);

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
          const imgUrl = item.url ? String(item.url).trim() : "";
          const label = item.title || item.type || "Evidence";
            const evidenceIndex = index + itemIndex;
            const videoLabel = isVideoEvidence(item) ? "VIDEO" : "";
            const initialThumbnail = isVideoEvidence(item) ? getVideoThumbnailUrl(item) : imgUrl;
          return `<figure class="dr-print-evidence-item">
            <div class="dr-print-evidence-thumb">
                <img src="${escapeHtml(initialThumbnail || "")}" data-evidence-index="${evidenceIndex}" data-evidence-type="${escapeHtml(item.type || "")}" alt="${escapeHtml(label)}" />
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
            <div class="dr-print-meta">Report Date: ${escapeHtml(formatPrintDate(record.date))} | ${escapeHtml(postedBy)}</div>
            ${lastEdited ? `<div class="dr-print-meta">${escapeHtml(lastEdited)}</div>` : ""}
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

      const directUrl = await resolveEvidenceImageUrl(item);
      if (directUrl && await waitForPrintImage(img, directUrl)) {
        return;
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

  function printSelectedDate() {
    const cards = [...$("dr-records").querySelectorAll(":scope > .dr-card")].filter(card => {
      const recordId = card.querySelector('[data-action="edit"]')?.dataset.id;
      return recordId && recordCache.get(recordId)?.date === selectedDate;
    });
    const recordsMarkup = cards.length
      ? cards.map(card => card.outerHTML).join("")
      : '<article class="dr-card dr-empty-day"><h2>No Daily Reports for this date</h2></article>';
    const printRoot = document.createElement("section");
    printRoot.id = "dr-day-print-root";
    printRoot.innerHTML = `
      <header>
        <div>
          <div class="brand">MANNAT MOON</div>
          <div class="sub">Construction Documentation</div>
          <div class="dr-print-report-title">DAILY REPORT</div>
        </div>
      </header>
      <main>
        <section class="dr-date-navigation">
          <div class="dr-selected-date"><strong>${escapeHtml(formatSelectedDate(selectedDate))}</strong></div>
        </section>
        <div class="dr-day-print-records">${recordsMarkup}</div>
      </main>
    `;

    let cleanedUp = false;
    const cleanup = () => {
      if (cleanedUp) return;
      cleanedUp = true;
      window.removeEventListener("afterprint", cleanup);
      printRoot.remove();
      document.body.classList.remove("dr-day-printing");
    };

    document.body.classList.add("dr-day-printing");
    document.body.append(printRoot);
    window.addEventListener("afterprint", cleanup, { once: true });
    window.print();
    if (!window.matchMedia("print").matches) {
      cleanup();
    }
  }

  async function loadRecords(){
    if(!db){
      $("dr-records").innerHTML=`<div class="dr-card"><b>Daily Reports are unavailable.</b><p>Please contact your administrator for help.</p></div>`;
      return;
    }

    const requestedDate = selectedDate;
    const requestId = ++recordsLoadId;
    let snap;
    try {
      snap = await getDocs(query(collection(db, "daily_records"), where("date", "==", requestedDate)));
    } catch (error) {
      if (requestId !== recordsLoadId) return;
      recordCache.clear();
      $("dr-recordCount").textContent = "0";
      $("dr-evidenceCount").textContent = "0";
      $("dr-records").innerHTML = '<div class="dr-card dr-empty-day"><h2>Unable to load Daily Reports</h2><p>Check the connection and try again.</p></div>';
      console.error("Daily Reports Firestore read failed", {
        operation: "daily_records query by selected date",
        code: error?.code || "unknown",
        message: error?.message || String(error),
        httpStatus: error?.customData?.httpStatus ?? error?.status ?? "unavailable"
      });
      showToast("Unable to load Daily Reports. Check the connection and try again.", "error", 5000);
      return;
    }
    if (requestId !== recordsLoadId) {
      return;
    }

    recordCache.clear();
    let html="", evidenceCount=0, recordCount=0;

    snap.forEach(recordDoc => {
      const r = recordDoc.data();
      if (!belongsToSelectedDate(r, requestedDate)) return;
      const ev = r.evidence || [];
      const savedCounts = r.workerCounts && typeof r.workerCounts === "object"
        ? r.workerCounts
        : {
          "Masons": Number(r.masonCount ?? 0),
          "Man Mazdoor": Number(r.manMazdoorCount ?? 0),
          "Woman Mazdoor": Number(r.womanMazdoorCount ?? 0)
        };
      const totalWorkers = Number(r.totalWorkers ?? r.manpower ?? Object.values(savedCounts).reduce((total, value) => total + Number(value || 0), 0));
      const weatherSummary = getWeatherSummary(r.weatherCondition, r.weatherDescription);
      const postedBy = getPostedBy(r);
      const lastEdited = getLastEdited(r);
      const workerSummary = Object.entries(savedCounts)
        .map(([name, count]) => `<div>${escapeHtml(name)}: ${Number(count || 0)}</div>`)
        .join("");

      recordCache.set(recordDoc.id, r);
      recordCount += 1;
      evidenceCount += ev.length;

      html += `<article class="dr-card">
        <div class="dr-date-row">
          <div class="dr-date">${escapeHtml(formatDate(r.date, ""))}</div>
          <div class="dr-author-details">
            <div class="dr-posted-by">Posted by: ${escapeHtml(postedBy.fullName)}${postedBy.username ? ` (<span class="dr-posted-username">@${escapeHtml(postedBy.username)}</span>)` : ""}</div>
            ${lastEdited ? `<div class="dr-last-edited">${escapeHtml(lastEdited)}</div>` : ""}
          </div>
          <div class="dr-weather-actions">
            ${getWeatherBadgeMarkup(r.weatherCondition, r.weatherDescription)}
            <button type="button" class="dr-print-btn" data-action="print" data-id="${escapeHtml(recordDoc.id)}">🖨 Print</button>
          </div>
        </div>
        <div class="dr-record-actions">
          <button type="button" class="dr-edit-btn" data-action="edit" data-id="${escapeHtml(recordDoc.id)}">Edit</button>
          <button type="button" class="dr-delete-btn" data-action="delete" data-id="${escapeHtml(recordDoc.id)}">Delete</button>
        </div>
        <h2>${escapeHtml(r.activity||"Daily Site Record")}</h2>
        <div class="dr-meta">
          <span class="dr-tag">${escapeHtml(r.floor||"Location not specified")}</span>
        </div>
        <div class="dr-workforce-card">
          ${workerSummary}
          <div>Total Workers: ${totalWorkers}</div>
        </div>
        ${r.materials?`<h3>Materials</h3><div class="dr-pre">${escapeHtml(r.materials)}</div>`:""}
        ${r.notes?`<h3>Work Done Today</h3><div class="dr-pre">${escapeHtml(r.notes)}</div>`:""}
        ${ev.length?`<h3>Evidence</h3><div class="dr-evidence">${ev.map(x=>`<div class="dr-ev"><div class="dr-evtitle">${escapeHtml(x.type)} — ${escapeHtml(x.title||"")}</div>${previewHtml(x.type,x.url)}<div class="dr-ev-actions"><a class="dr-open" target="_blank" href="${escapeHtml(x.url)}">Open original</a></div></div>`).join("")}</div>`:""}
      </article>`;
    });

    $("dr-records").innerHTML=html||`<div class="dr-card dr-empty-day"><h2>No Daily Reports for this date</h2></div>`;
    $("dr-recordCount").textContent=recordCount;
    $("dr-evidenceCount").textContent=evidenceCount;

    // Load async previews after HTML is rendered
    document.querySelectorAll(".dr-ev-preview-loading").forEach(loadingEl => {
      const photosUrl = loadingEl.dataset.photosUrl;
      getGooglePhotosDirectUrl(photosUrl).then(directUrl => {
        if(directUrl){
          loadingEl.innerHTML = `<img src="${escapeHtml(directUrl)}" alt="Photo preview" class="dr-ev-img" onerror="this.style.display='none'; this.nextElementSibling.style.display='block';">
          <div class="dr-preview-error" style="display:none;">Photo preview unavailable</div>`;
        } else {
          loadingEl.innerHTML = `<div class="dr-preview-error">Unable to load Google Photos preview - <a href="${escapeHtml(photosUrl)}" target="_blank">Open original</a></div>`;
        }
      }).catch(err => {
        console.error("Google Photos preview error in loadRecords:", err);
        loadingEl.innerHTML = `<div class="dr-preview-error">Unable to load Google Photos preview - <a href="${escapeHtml(photosUrl)}" target="_blank">Open original</a></div>`;
      });
    });
  }

  // Initialize event listeners
  function init() {
    // Button listeners
    $("dr-records").addEventListener("click", activateYouTubePreview);
    $("dr-livePreview").addEventListener("click", activateYouTubePreview);
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
    $("dr-cancelDeleteBtn").onclick = cancelDelete;
    $("dr-confirmDeleteBtn").onclick = confirmDeleteRecord;
    $("dr-recordForm").onsubmit = handleFormSubmit;

    // Load Firebase trade definitions before records so category fields are dynamic.
    renderCalendar();
    loadCalendarReportDates();
    loadTrades().then(loadRecords).catch(error => {
      console.error("Daily Reports trades Firestore read failed", {
        operation: "trades collection query",
        code: error?.code || "unknown",
        message: error?.message || String(error),
        httpStatus: error?.customData?.httpStatus ?? error?.status ?? "unavailable"
      });
      const tradeSelect = $("dr-tradeSelect");
      tradeSelect.innerHTML = '<option value="">Unable to load trades.</option>';
      tradeSelect.disabled = true;
      showToast("Unable to load trades. Please check your connection and try again.", "error", 5000);
      loadRecords();
    });
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
