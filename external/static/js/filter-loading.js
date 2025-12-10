document.addEventListener("DOMContentLoaded", function () {
  const form = document.getElementById("filters-form");
  const overlay = document.getElementById("loading-overlay");
  const applyBtn = document.getElementById("apply-filters-btn");

  if (!form || !overlay) return;

  form.addEventListener("submit", function () {
    overlay.classList.remove("hidden");

    if (applyBtn) {
      applyBtn.disabled = true;
      applyBtn.classList.add("opacity-60", "cursor-not-allowed");
    }
  });
});
