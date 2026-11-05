(function () {
    "use strict";

    var LOGO_SRC = "/static/img/logo.png";

    function renderLogo(container) {
        var title = container.dataset.title || "抖店 ERP";
        var img = document.createElement("img");
        img.src = LOGO_SRC;
        img.alt = "logo";
        img.className = "logo-img";
        var text = document.createElement("span");
        text.className = "logo-text";
        text.textContent = title;
        container.appendChild(img);
        container.appendChild(text);
    }

    document.querySelectorAll(".logo[data-logo]").forEach(renderLogo);

    function initResponsiveNav() {
        var layout = document.querySelector(".layout");
        if (!layout) return;
        var sidebar = layout.querySelector(".sidebar");
        var topbar = layout.querySelector(".topbar");
        if (!sidebar || !topbar) return;

        var backdrop = document.createElement("div");
        backdrop.className = "nav-backdrop";
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "nav-toggle";
        btn.setAttribute("aria-label", "切换导航菜单");
        btn.innerHTML =
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
            '<line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/>' +
            '<line x1="3" y1="18" x2="21" y2="18"/></svg>';
        topbar.insertBefore(btn, topbar.firstChild);
        layout.appendChild(backdrop);

        function closeNav() { layout.classList.remove("sidebar-open"); }
        btn.addEventListener("click", function (event) {
            event.stopPropagation();
            layout.classList.toggle("sidebar-open");
        });
        backdrop.addEventListener("click", closeNav);
        sidebar.addEventListener("click", function (event) {
            if (event.target.closest(".nav-item")) closeNav();
        });
        window.addEventListener("resize", function () {
            if (window.innerWidth > 768) closeNav();
        });
    }

    initResponsiveNav();
})();
