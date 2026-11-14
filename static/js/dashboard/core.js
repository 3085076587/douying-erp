(function () {
    "use strict";

    var API_BASE = "/api/v1";
    var TOKEN_KEY = "erp_access_token";
    var NAME_KEY = "erp_display_name";
    var TITLES = { stores: "店铺列表", "fund-account": "账户中心", invoices: "发票管理", violations: "违规与预警", center: "数据中心", members: "成员管理" };
    var SECTION_OF = { stores: "stores", violations: "violation", center: "center", "fund-account": "fund", invoices: "fund" };
    var accessReady = false;
    var accessSections = [];
    var canManageMembers = false;

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

    // 把响应文本转成对象。有些代理会在 JSON 前面加几个字符，
    // 这里会退一步从第一个 { 或 [ 开始再解析一次，实在不行才报错。
    function parseJson(text) {
        var raw = String(text == null ? "" : text);
        if (!raw.trim()) return null;
        try {
            return JSON.parse(raw);
        } catch (err) {
            var start = raw.search(/[{[]/);
            if (start > 0) {
                try {
                    return JSON.parse(raw.slice(start));
                } catch (err2) {}
            }
            var snippet = raw.trim().slice(0, 80).replace(/\s+/g, " ");
            throw new Error("服务器返回了无法识别的内容：" + snippet);
        }
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
            return resp.text().then(function (text) {
                var data;
                try {
                    data = parseJson(text);
                } catch (err) {
                    if (!resp.ok) throw new Error("请求失败（" + resp.status + "），服务器没返回正常内容");
                    throw err;
                }
                if (!resp.ok) {
                    var detail = data && data.detail;
                    if (Array.isArray(detail)) detail = detail[0] && detail[0].msg;
                    throw new Error(detail || "请求失败（" + resp.status + "）");
                }
                return data || {};
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

    function viewAllowed(name) {
        if (name === "members") return canManageMembers;
        if (!accessReady) return name !== "members";
        var section = SECTION_OF[name];
        if (!section) return true;
        return accessSections.indexOf(section) >= 0;
    }

    function firstAllowed() {
        var order = ["stores", "violations", "center", "fund-account", "invoices"];
        for (var i = 0; i < order.length; i++) {
            if (viewAllowed(order[i])) return order[i];
        }
        return "stores";
    }

    function applyAccess(user) {
        if (!user || !Array.isArray(user.sections)) return;
        accessReady = true;
        accessSections = user.sections;
        canManageMembers = !!user.can_manage_members;
        document.querySelectorAll("[data-section]").forEach(function (el) {
            el.classList.toggle("hidden", accessSections.indexOf(el.getAttribute("data-section")) < 0);
        });
        var shopOn = ["stores", "violation", "center"].some(function (key) {
            return accessSections.indexOf(key) >= 0;
        });
        document.getElementById("nav-shop-title").classList.toggle("hidden", !shopOn);
        document.getElementById("nav-fund-title").classList.toggle("hidden", accessSections.indexOf("fund") < 0);
        document.getElementById("nav-members").classList.toggle("hidden", !canManageMembers);
        var nameText = user.display_name || user.username || name;
        document.getElementById("top-username").textContent = nameText;
        document.getElementById("top-avatar").textContent = nameText.slice(0, 1).toUpperCase();
        var active = document.querySelector(".nav-item.active");
        var current = active ? active.dataset.view : "stores";
        if (!viewAllowed(current)) showView(firstAllowed());
    }

    function showView(name) {
        if (!viewAllowed(name)) name = firstAllowed();
        document.querySelectorAll(".nav-item").forEach(function (item) {
            item.classList.toggle("active", item.dataset.view === name);
        });
        document.querySelectorAll(".view").forEach(function (view) {
            view.classList.toggle("active", view.id === "view-" + name);
        });
        document.getElementById("breadcrumb").textContent = TITLES[name] || "";
        document.getElementById("withdraw-mask").classList.add("hidden");
        if (name === "fund-account" && window.Dash.loadAccounts) window.Dash.loadAccounts();
        if (name === "invoices" && window.Dash.loadInvoices) window.Dash.loadInvoices();
        if (name === "violations" && window.Vio && window.Vio.load) window.Vio.load();
        if (name === "center" && window.Dash.loadCenter) window.Dash.loadCenter();
        if (name === "members" && window.Dash.pages && window.Dash.pages.members) window.Dash.pages.members();
    }

    document.querySelectorAll(".nav-item").forEach(function (item) {
        item.addEventListener("click", function () { showView(item.dataset.view); });
    });

    document.getElementById("btn-logout").addEventListener("click", toLogin);

    window.Dash = { request: request, showToast: showToast, escapeHtml: escapeHtml, yuan: yuan, showView: showView, pages: {} };
    request("/auth/me").then(applyAccess).catch(function () {});
})();
