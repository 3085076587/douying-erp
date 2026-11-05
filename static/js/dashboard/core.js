(function () {
    "use strict";

    var API_BASE = "/api/v1";
    var TOKEN_KEY = "erp_access_token";
    var NAME_KEY = "erp_display_name";
    var TITLES = { stores: "店铺列表", "fund-account": "账户中心" };

    function token() { return localStorage.getItem(TOKEN_KEY); }
    if (!token()) {
        location.replace("/");
        return;
    }

    var name = localStorage.getItem(NAME_KEY) || "admin";
    document.getElementById("top-username").textContent = name;
    document.getElementById("top-avatar").textContent = name.slice(0, 1).toUpperCase();

    function toLogin() {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(NAME_KEY);
        location.href = "/";
    }

    var toastEl = document.getElementById("toast");

    function showToast(message, type) {
        toastEl.textContent = message;
        toastEl.className = "toast " + (type || "error");
        clearTimeout(showToast._timer);
        showToast._timer = setTimeout(function () { toastEl.classList.add("hidden"); }, 3000);
    }

    function request(path, options) {
        options = options || {};
        options.headers = Object.assign({ Authorization: "Bearer " + token() }, options.headers || {});
        return fetch(API_BASE + path, options).then(function (resp) {
            if (resp.status === 401) {
                showToast("登录已过期，请重新登录", "error");
                setTimeout(toLogin, 800);
                throw new Error("未认证");
            }
            return resp.json().then(function (data) {
                if (!resp.ok) {
                    var detail = data && data.detail;
                    if (Array.isArray(detail)) detail = detail[0].msg;
                    throw new Error(detail || "请求失败");
                }
                return data;
            });
        });
    }

    function escapeHtml(value) {
        return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
        });
    }

    function yuan(fen) {
        if (fen === null || fen === undefined || fen === "") return "—";
        return "¥" + (Number(fen) / 100).toFixed(2);
    }

    function showView(name) {
        document.querySelectorAll(".nav-item").forEach(function (item) {
            item.classList.toggle("active", item.dataset.view === name);
        });
        document.querySelectorAll(".view").forEach(function (view) {
            view.classList.toggle("active", view.id === "view-" + name);
        });
        document.getElementById("breadcrumb").textContent = TITLES[name] || "";
        document.getElementById("withdraw-mask").classList.add("hidden");
        if (name === "fund-account" && window.Dash.loadAccounts) window.Dash.loadAccounts();
    }

    document.querySelectorAll(".nav-item").forEach(function (item) {
        item.addEventListener("click", function () { showView(item.dataset.view); });
    });

    document.getElementById("btn-logout").addEventListener("click", toLogin);

    window.Dash = { request: request, showToast: showToast, escapeHtml: escapeHtml, yuan: yuan, showView: showView };
})();
