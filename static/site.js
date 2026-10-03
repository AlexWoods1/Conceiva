(() => {
  const toggle = document.querySelector("[data-menu-toggle]");
  const menu = document.querySelector("[data-menu]");
  if (toggle && menu) {
    toggle.addEventListener("click", () => {
      const open = menu.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", String(open));
    });
  }

  // * Testimonial and member strips scroll by one card with the arrow buttons.
  document.querySelectorAll("[data-carousel]").forEach((carousel) => {
    const track = carousel.querySelector("[data-carousel-track]");
    carousel.querySelectorAll("[data-carousel-step]").forEach((button) => {
      button.addEventListener("click", () => {
        const card = track.firstElementChild;
        const width = card ? card.getBoundingClientRect().width + 16 : 300;
        track.scrollBy({ left: Number(button.dataset.carouselStep) * width, behavior: "smooth" });
      });
    });
  });
})();
