// external/static/js/base.js

document.addEventListener("DOMContentLoaded", () => {
    const loader = document.getElementById("page-loading");
    if (!loader) return;

    // 1) Hide loading overlay after page has rendered
    setTimeout(() => {
        loader.classList.add("opacity-0", "pointer-events-none");
    }, 150); // small delay for smoother transition

    // 2) Show loading overlay when navigating via internal links
    const links = document.querySelectorAll("a[href]");
    links.forEach((link) => {
        const href = link.getAttribute("href") || "";

        // Skip anchors, new-tab links, and external navigation
        if (
            href.startsWith("#") ||
            link.target === "_blank"
        ) {
            return;
        }

        link.addEventListener("click", (e) => {
            // Ignore modified clicks (Ctrl / Cmd / Shift / Alt)
            if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) {
                return;
            }

            // Show loading overlay
            loader.classList.remove("pointer-events-none", "opacity-0");
            loader.classList.add("opacity-100");
        });
    });
});
