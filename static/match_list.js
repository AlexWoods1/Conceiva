(() => {
  const deck = document.querySelector("[data-deck]");
  if (!deck) return;
  const cards = [...deck.querySelectorAll("[data-card]")];
  if (!cards.length) return;

  const page = deck.closest(".deck-page");
  const toast = page.querySelector("[data-toast]");
  const modal = page.querySelector("[data-match-modal]");
  let toastTimer = null;

  function showToast(message, tone) {
    toast.textContent = message;
    toast.dataset.tone = tone || "";
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toast.hidden = true), 2600);
  }

  function showMatch(card) {
    const photo = card.querySelector("[data-photo] img");
    const img = modal.querySelector("[data-match-photo]");
    img.src = photo.src;
    img.alt = photo.alt;
    modal.querySelector("[data-match-text]").textContent =
      `${card.dataset.code} is on your shortlist. Book a counselor when you're ready to talk it through.`;
    modal.hidden = false;
    modal.querySelector("[data-match-close]").focus();
  }

  function closeMatch() {
    modal.hidden = true;
  }

  modal.querySelector("[data-match-close]").addEventListener("click", closeMatch);
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeMatch();
  });

  function setShortlistCount(delta) {
    page.querySelectorAll("[data-shortlist-count]").forEach((el) => {
      el.textContent = String(Number(el.textContent) + delta);
    });
  }

  async function shortlist(card) {
    const form = card.querySelector("[data-like-form]");
    const code = card.dataset.code;
    if (card.dataset.shortlisted === "true") {
      showToast(`${code} is already on your shortlist.`);
      return true;
    }
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const type = response.headers.get("content-type") || "";
      if (!type.includes("application/json")) {
        window.location.href = response.url;
        return false;
      }
      const data = await response.json();
      if (!data.ok) {
        showToast(data.message || data.detail || "Could not shortlist.", "error");
        return false;
      }
      const wasNew = data.added !== false;
      card.dataset.shortlisted = "true";
      card.querySelector(".action-like").classList.add("is-on");
      if (wasNew) setShortlistCount(1);
      if (wasNew) showMatch(card);
      else showToast(`${code} is already on your shortlist.`);
      return true;
    } catch (_) {
      showToast("Could not reach the server. Try again.", "error");
      return false;
    }
  }

  function stepPhoto(card, step) {
    const photos = [...card.querySelectorAll("[data-photo]")];
    const bars = [...card.querySelectorAll(".photo-bars span")];
    if (photos.length < 2) return;
    const current = photos.findIndex((p) => p.classList.contains("is-active"));
    const next = (current + step + photos.length) % photos.length;
    photos.forEach((p, i) => p.classList.toggle("is-active", i === next));
    bars.forEach((b, i) => b.classList.toggle("is-active", i === next));
  }

  cards.forEach((card) => {
    card.querySelectorAll("[data-photo-step]").forEach((button) => {
      button.addEventListener("click", () => stepPhoto(card, Number(button.dataset.photoStep)));
    });
    card.querySelector("[data-like-form]").addEventListener("submit", (event) => {
      event.preventDefault();
      shortlist(card);
    });
  });

  document.addEventListener("keydown", (event) => {
    if (modal.hidden) return;
    if (event.key === "Escape" || event.key === "Enter") {
      event.preventDefault();
      closeMatch();
    }
  });
})();
