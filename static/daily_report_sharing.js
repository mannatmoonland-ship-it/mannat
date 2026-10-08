(() => {
  const shareButton = document.getElementById("shareDailyReportBtn");
  const downloadButton = document.getElementById("downloadDailyReportImageBtn");
  if (!shareButton && !downloadButton) return;

  const status = document.getElementById("shareStatus");
  function currentDate() {
    const queryDate = new URL(window.location.href).searchParams.get("date");
    if (/^\d{4}-\d{2}-\d{2}$/.test(queryDate || "")) return queryDate;
    const dateLabel = document.getElementById("dr-selectedDateLabel")?.textContent.trim();
    const match = dateLabel?.match(/(\d{1,2})\/(\d{1,2})\/(\d{4})/);
    if (match) return `${match[3]}-${match[2].padStart(2, "0")}-${match[1].padStart(2, "0")}`;
    return new Date().toISOString().slice(0, 10);
  }
  function imageBlob(date) {
    const [, year, month, day] = date.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    const displayDate = `${day}/${month}/${year}`;
    const canvas = document.createElement("canvas");
    canvas.width = 1200;
    canvas.height = 630;
    const ctx = canvas.getContext("2d");
    const gradient = ctx.createLinearGradient(0, 0, 1200, 630);
    gradient.addColorStop(0, "#0f172a");
    gradient.addColorStop(1, "#1e293b");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, 1200, 630);
    ctx.strokeStyle = "#facc15";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.roundRect(48, 48, 1104, 534, 30);
    ctx.stroke();
    ctx.textAlign = "center";
    ctx.fillStyle = "#f8fafc";
    ctx.font = "700 58px Arial, sans-serif";
    ctx.fillText("MANNAT MOON", 600, 190);
    ctx.fillStyle = "#cbd5e1";
    ctx.font = "600 27px Arial, sans-serif";
    ctx.fillText("CONSTRUCTION", 600, 232);
    ctx.fillStyle = "#facc15";
    ctx.font = "700 40px Arial, sans-serif";
    ctx.fillText("DAILY REPORT", 600, 328);
    ctx.fillStyle = "#f8fafc";
    ctx.font = "700 72px Arial, sans-serif";
    ctx.fillText(displayDate, 600, 424);
    ctx.fillStyle = "#cbd5e1";
    ctx.font = "400 30px Arial, sans-serif";
    ctx.fillText("Daily Construction Activity", 600, 500);
    return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("Image creation failed")), "image/png"));
  }
  async function imageFile(date) {
    return new File([await imageBlob(date)], `daily-report-share-${date}.png`, { type: "image/png" });
  }
  async function downloadImage() {
    const file = await imageFile(currentDate());
    const objectUrl = URL.createObjectURL(file);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = file.name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  }

  downloadButton?.addEventListener("click", async () => {
    try { await downloadImage(); if (status) status.textContent = "Daily Report image downloaded."; }
    catch (error) { console.error("Unable to create Daily Report image", error); if (status) status.textContent = "Unable to create the Daily Report image."; }
  });
  shareButton?.addEventListener("click", async () => {
    const date = currentDate();
    const [, year, month, day] = date.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    const displayDate = `${day}/${month}/${year}`;
    const shareUrl = new URL(`/daily-reports?date=${encodeURIComponent(date)}`, window.location.origin).href;
    const shareText = `Daily Report – ${displayDate}\n\n${shareUrl}`;
    try {
      if (navigator.share && navigator.canShare) {
        const file = await imageFile(date);
        if (navigator.canShare({ files: [file] })) {
          await navigator.share({ title: "Mannat Moon Daily Report", text: shareText, files: [file] });
          if (status) status.textContent = "Daily Report image and link shared.";
          return;
        }
      }
      if (navigator.share) {
        await navigator.share({ title: "Mannat Moon Daily Report", text: shareText, url: shareUrl });
        if (status) status.textContent = "Daily Report link shared.";
        return;
      }
      await navigator.clipboard.writeText(shareUrl);
      if (status) status.textContent = "Daily Report link copied. You can paste it into WhatsApp.";
    } catch (error) {
      if (error?.name === "AbortError") return;
      console.error("Unable to share Daily Report", error);
      try {
        await navigator.clipboard.writeText(shareUrl);
        if (status) status.textContent = "Daily Report link copied. You can paste it into WhatsApp.";
      } catch {
        if (status) status.textContent = "Sharing and clipboard are unavailable. Use Download Image and copy the URL from the address bar.";
      }
    }
  });
})();
