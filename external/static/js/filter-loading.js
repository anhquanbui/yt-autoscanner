document.addEventListener("DOMContentLoaded", function () {
  // Filter form and loading overlay
  const form = document.getElementById("filters-form");
  const overlay = document.getElementById("loading-overlay");
  const applyBtn = document.getElementById("apply-filters-btn");

  if (!form || !overlay) return;

  // Show loading overlay and disable button on submit
  form.addEventListener("submit", function () {
    overlay.classList.remove("hidden");

    if (applyBtn) {
      applyBtn.disabled = true;
      applyBtn.classList.add("opacity-60", "cursor-not-allowed");
    }
  });
});
