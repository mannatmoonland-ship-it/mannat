const DATE_ONLY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;
const DATE_TIME_PATTERN = /^(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})\s*(AM|PM)$/i;

export function localDateInputValue(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function parseDateOnly(value) {
  const match = DATE_ONLY_PATTERN.exec(value);
  if (!match) return null;
  const [, yearText, monthText, dayText] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const date = new Date(year, month - 1, day, 12);
  return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day
    ? date
    : null;
}

function toDate(value) {
  if (!value) return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value === "string") {
    const dateOnly = parseDateOnly(value);
    if (dateOnly) return dateOnly;
    const dateTime = DATE_TIME_PATTERN.exec(value);
    if (dateTime) {
      const [, year, month, day, hourText, minuteText, period] = dateTime;
      const hour = Number(hourText);
      const minute = Number(minuteText);
      if (hour < 1 || hour > 12 || minute > 59) return null;
      const date = new Date(Number(year), Number(month) - 1, Number(day), 12);
      if (date.getFullYear() !== Number(year) || date.getMonth() !== Number(month) - 1 || date.getDate() !== Number(day)) return null;
      date.setHours((hour % 12) + (period.toUpperCase() === "PM" ? 12 : 0), minute, 0, 0);
      return date;
    }
  }
  if (typeof value === "object") {
    if (typeof value.toDate === "function") {
      const date = value.toDate();
      return date instanceof Date && !Number.isNaN(date.getTime()) ? date : null;
    }
    if (Number.isFinite(value.seconds)) return new Date(value.seconds * 1000);
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function dateParts(date, options = {}) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    ...options
  }).formatToParts(date);
  const getPart = type => parts.find(part => part.type === type)?.value || "";
  return { day: getPart("day"), month: getPart("month"), year: getPart("year"), weekday: getPart("weekday") };
}

export function formatDate(value, fallback = "-") {
  const date = toDate(value);
  if (!date) return value ? String(value) : fallback;
  const { day, month, year } = dateParts(date);
  return `${day}/${month}/${year}`;
}

export function formatDateWithDay(value, fallback = "-") {
  const date = toDate(value);
  if (!date) return value ? String(value) : fallback;
  const { day, month, year, weekday } = dateParts(date, { weekday: "long" });
  return `${weekday}, ${day}/${month}/${year}`;
}

export function formatDateTime(value, fallback = "-") {
  const date = toDate(value);
  if (!date) return value ? String(value) : fallback;
  const { day, month, year } = dateParts(date);
  const timeParts = new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: true
  }).formatToParts(date);
  const getPart = type => timeParts.find(part => part.type === type)?.value || "";
  return `${day}/${month}/${year} ${getPart("hour")}:${getPart("minute")} ${getPart("dayPeriod").toUpperCase()}`;
}
