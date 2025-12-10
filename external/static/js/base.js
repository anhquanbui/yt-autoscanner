// external/static/js/base.js

document.addEventListener("DOMContentLoaded", () => {
    const loader = document.getElementById("page-loading");
    if (!loader) return;

    // 1) Ẩn loading sau khi trang đã render xong
    setTimeout(() => {
        loader.classList.add("opacity-0", "pointer-events-none");
    }, 150); // thêm 150ms cho mượt

    // 2) Hiện loading khi user click link nội bộ (chuyển trang full page)
    const links = document.querySelectorAll("a[href]");
    links.forEach((link) => {
        const href = link.getAttribute("href") || "";

        // Bỏ qua anchor (#...) và mở tab mới, link external
        if (
            href.startsWith("#") ||
            link.target === "_blank"
        ) {
            return;
        }

        link.addEventListener("click", (e) => {
            // Nếu giữ Ctrl/Command để mở tab mới thì thôi
            if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) {
                return;
            }

            // Show loading overlay
            loader.classList.remove("pointer-events-none", "opacity-0");
            loader.classList.add("opacity-100");
        });
    });
});
