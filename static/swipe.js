(() => {
  const deck = document.querySelector("[data-deck]");
  if (!deck) return;
  const cards = [...deck.querySelectorAll("[data-card]")];
  if (!cards.length) return;

  const page = deck.closest(".deck-page");
  const empty = deck.querySelector("[data-deck-empty]");
  const footer = page.querySelector("[data-deck-footer]");
  const undoButton = page.querySelector("[data-undo]");
  const counter = page.querySelector("[data-counter]");
  const toast = page.querySelector("[data-toast]");
  const toggle = page.querySelector("[data-view-toggle]");
  const modal = page.querySelector("[data-match-modal]");
  const SWIPE_THRESHOLD = 110;
  const VIEW_KEY = "donor-match-view";

  let index = 0;
  const history = [];
  let toastTimer = null;

  page.querySelectorAll("[data-action]").forEach((el) => (el.hidden = false));
  toggle.hidden = false;
  footer.hidden = false;

  function stacked() {
    return deck.classList.contains("is-stacked");
  }

  function setView(view) {
    deck.classList.toggle("is-stacked", view === "cards");
    footer.hidden = view !== "cards";
    toggle.querySelectorAll("button").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.view === view));
    });
    try {
      localStorage.setItem(VIEW_KEY, view);
    } catch (_) {}
    layout();
  }

  function layout() {
    cards.forEach((card, i) => {
      const pos = i - index;
      card.style.setProperty("--pos", String(Math.max(pos, 0)));
      card.style.zIndex = String(cards.length - i);
      card.classList.toggle("is-top", pos === 0);
      card.classList.toggle("is-hidden-deep", pos > 2);
      if (pos >= 0) {
        card.classList.remove("is-gone-left", "is-gone-right");
        card.style.transform = "";
      }
      card.setAttribute("aria-hidden", stacked() && pos !== 0 ? "true" : "false");
    });
    const done = index >= cards.length;
    empty.hidden = !(stacked() && done);
    undoButton.disabled = history.length === 0;
    counter.textContent = done ? `${cards.length} of ${cards.length} seen` : `${index + 1} of ${cards.length}`;
  }

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

  async function swipe(direction) {
    if (index >= cards.length) return;
    const card = cards[index];
    card.classList.remove("is-expanded");
    if (direction === "right") {
      card.classList.add("is-gone-right");
      const ok = await shortlist(card);
      if (!ok) {
        card.classList.remove("is-gone-right");
        card.style.transform = "";
        return;
      }
    } else {
      card.classList.add("is-gone-left");
    }
    history.push(index);
    index += 1;
    layout();
  }

  function undo() {
    if (!history.length) return;
    index = history.pop();
    layout();
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

  // * Drag the top card. A short tap falls through to the photo-nav click.
  cards.forEach((card) => {
    const area = card.querySelector("[data-photos]");
    let startX = 0;
    let startY = 0;
    let dx = 0;
    let pressed = false;
    let dragging = false;
    let suppressClick = false;

    area.addEventListener("pointerdown", (event) => {
      if (!stacked() || !card.classList.contains("is-top") || event.button !== 0) return;
      startX = event.clientX;
      startY = event.clientY;
      dx = 0;
      pressed = true;
      dragging = false;
    });

    area.addEventListener("pointermove", (event) => {
      if (!pressed) return;
      dx = event.clientX - startX;
      const dy = event.clientY - startY;
      if (!dragging && Math.abs(dx) > 8 && Math.abs(dx) > Math.abs(dy)) {
        dragging = true;
        area.setPointerCapture(event.pointerId);
        card.classList.add("is-dragging");
      }
      if (!dragging) return;
      card.style.transform = `translate(${dx}px, ${dy * 0.2}px) rotate(${dx / 18}deg)`;
      const strength = Math.min(Math.abs(dx) / SWIPE_THRESHOLD, 1);
      card.style.setProperty("--like", dx > 0 ? String(strength) : "0");
      card.style.setProperty("--nope", dx < 0 ? String(strength) : "0");
    });

    const release = (event) => {
      if (!pressed) return;
      pressed = false;
      if (area.hasPointerCapture(event.pointerId)) area.releasePointerCapture(event.pointerId);
      card.classList.remove("is-dragging");
      card.style.setProperty("--like", "0");
      card.style.setProperty("--nope", "0");
      if (!dragging) return;
      suppressClick = true;
      if (Math.abs(dx) > SWIPE_THRESHOLD) {
        swipe(dx > 0 ? "right" : "left");
      } else {
        card.style.transform = "";
      }
    };
    area.addEventListener("pointerup", release);
    area.addEventListener("pointercancel", release);

    area.addEventListener(
      "click",
      (event) => {
        if (suppressClick) {
          event.stopPropagation();
          event.preventDefault();
          suppressClick = false;
        }
      },
      true
    );

    card.querySelectorAll("[data-photo-step]").forEach((button) => {
      button.addEventListener("click", () => stepPhoto(card, Number(button.dataset.photoStep)));
    });

    card.querySelector('[data-action="pass"]').addEventListener("click", () => {
      if (stacked()) swipe("left");
      else card.classList.add("is-dismissed");
    });
    card.querySelector('[data-action="info"]').addEventListener("click", () => {
      card.classList.toggle("is-expanded");
    });
    card.querySelector("[data-like-form]").addEventListener("submit", (event) => {
      event.preventDefault();
      if (stacked() && card.classList.contains("is-top")) swipe("right");
      else shortlist(card);
    });
  });

  undoButton.addEventListener("click", undo);
  deck.querySelector("[data-restart]").addEventListener("click", () => {
    index = 0;
    history.length = 0;
    layout();
  });
  toggle.querySelectorAll("button").forEach((b) => {
    b.addEventListener("click", () => setView(b.dataset.view));
  });

  document.addEventListener("keydown", (event) => {
    if (!modal.hidden) {
      if (event.key === "Escape" || event.key === "Enter") {
        event.preventDefault();
        closeMatch();
      }
      return;
    }
    if (!stacked() || event.target.closest("input, select, textarea")) return;
    if (event.key === "ArrowLeft") swipe("left");
    else if (event.key === "ArrowRight") swipe("right");
    else if (event.key === "ArrowUp" && cards[index]) {
      event.preventDefault();
      cards[index].classList.toggle("is-expanded");
    } else if (event.key === " " && cards[index]) {
      event.preventDefault();
      stepPhoto(cards[index], 1);
    } else if (event.key === "Backspace") undo();
  });

  let saved = "cards";
  try {
    saved = localStorage.getItem(VIEW_KEY) || "cards";
  } catch (_) {}
  setView(saved === "list" ? "list" : "cards");
})();
