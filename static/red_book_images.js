(() => {
  function candidates(url) {
    try {
      const parsed = new URL(url);
      if (parsed.protocol !== "https:" || parsed.hostname !== "drive.google.com") return [];
      const idMatch = parsed.pathname.match(/\/d\/([A-Za-z0-9_-]{1,200})(?:\/|$)/);
      const id = idMatch ? idMatch[1] : parsed.searchParams.get("id");
      if (!id || !/^[A-Za-z0-9_-]{1,200}$/.test(id)) return [];
      const encoded = encodeURIComponent(id);
      return [
        `https://lh3.googleusercontent.com/d/${encoded}=w1600`,
        `https://lh3.googleusercontent.com/d/${encoded}`,
        `https://drive.google.com/thumbnail?id=${encoded}&sz=w1600`,
        `https://drive.google.com/uc?export=view&id=${encoded}`
      ];
    } catch (error) {
      return [];
    }
  }

  function appendImage(content, imageUrl) {
    if (!imageUrl) return;
    const imageSources = candidates(imageUrl);
    if (!imageSources.length) return;

    const link = document.createElement("a");
    link.className = "red-book-image-link";
    link.href = imageUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.setAttribute("aria-label", "Open image in Google Drive");

    const image = document.createElement("img");
    image.className = "red-book-image";
    image.alt = "Image attached to this Red Book item";
    image.loading = "lazy";
    const unavailable = document.createElement("span");
    unavailable.className = "red-book-image-error";
    unavailable.textContent = "Image unavailable. Check Google Drive sharing settings.";
    unavailable.hidden = true;
    let candidateIndex = 0;
    image.addEventListener("error", () => {
      candidateIndex += 1;
      if (candidateIndex < imageSources.length) {
        image.src = imageSources[candidateIndex];
      } else {
        image.hidden = true;
        unavailable.hidden = false;
      }
    });
    image.src = imageSources[candidateIndex];
    link.append(image);
    content.append(link, unavailable);
  }

  function appendImages(content, imageUrls) {
    const urls = Array.isArray(imageUrls) ? imageUrls : [];
    urls.forEach(imageUrl => appendImage(content, imageUrl));
  }

  window.MannatRedBookImages = { appendImage, appendImages, candidates };
})();
