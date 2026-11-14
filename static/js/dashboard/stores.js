(function () {
    "use strict";

    var body = document.getElementById("store-tbody");
    var storeItems = [];

    function statusTag(status) {
        return status === "online"
            ? '<span class="tag tag-ok">在线</span>'
            : '<span class="tag tag-bad">掉线</span>';
    }

    function fillSelect(id, values, blank) {
        var select = document.getElementById(id);
        var current = select.value;
        var options = ['<option value="">' + blank + "</option>"];
        values.forEach(function (value) {
            options.push('<option value="' + Dash.escapeHtml(value) + '">' + Dash.escapeHtml(value) + "</option>");
        });
        select.innerHTML = options.join("");
        select.value = values.indexOf(current) >= 0 ? current : "";
    }

    function loginLeft(stamp) {
        var matched = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/.exec(stamp || "");
        if (!matched) return { text: "还没有记录", cls: "" };
        var start = new Date(+matched[1], +matched[2] - 1, +matched[3], +matched[4], +matched[5], +matched[6]).getTime();
        var left = start + 48 * 60 * 60 * 1000 - Date.now();
        if (left <= 0) return { text: "建议重新登录", cls: "tag-wait" };
        var totalMin = Math.floor(left / 60000);
        if (totalMin < 1) return { text: "剩余不到 1 分", cls: "tag-wait" };
        var hours = Math.floor(totalMin / 60);
        var mins = totalMin % 60;
        var text = hours > 0 ? ("剩余 " + hours + "小时" + mins + "分") : ("剩余 " + mins + "分");
        return { text: text, cls: hours < 6 ? "tag-wait" : "tag-ok" };
    }

    function paintStores() {
        var keyword = document.getElementById("store-search").value.trim().toLowerCase();
        var owner = document.getElementById("store-owner").value;
        var login = document.getElementById("store-login").value;
        var rows = storeItems.filter(function (row) {
            if (owner && (row.owner_account || "") !== owner) return false;
            if (login && (row.login_status || "") !== login) return false;
            if (!keyword) return true;
            return (row.name || "").toLowerCase().indexOf(keyword) >= 0 || String(row.shop_id || "").indexOf(keyword) >= 0;
        });
        if (!storeItems.length) {
            body.innerHTML = '<tr><td colspan="6" class="empty">还没有店铺。请使用插件添加。</td></tr>';
            return;
        }
        if (!rows.length) {
            body.innerHTML = '<tr><td colspan="6" class="empty">没有符合筛选的店铺</td></tr>';
            return;
        }
        body.innerHTML = rows.map(function (row) {
            var left = loginLeft(row.credential_at);
            var mark = left.cls ? '<span class="tag ' + left.cls + '">' + Dash.escapeHtml(left.text) + "</span>" : Dash.escapeHtml(left.text);
            return "<tr><td>" + Dash.escapeHtml(row.name) + "</td><td>" + Dash.escapeHtml(row.shop_id) +
                "</td><td>" + Dash.escapeHtml(row.owner_account || "") +
                "</td><td>" + mark +
                "</td><td>" + statusTag(row.login_status) +
                "</td><td><button class=\"btn-row store-delete\" type=\"button\" data-id=\"" + row.id + "\">删除</button></td></tr>";
        }).join("");
    }

    function loadStores() {
        Dash.request("/stores").then(function (data) {
            storeItems = data.items || [];
            var owners = [];
            storeItems.forEach(function (row) {
                if (row.owner_account && owners.indexOf(row.owner_account) < 0) owners.push(row.owner_account);
            });
            fillSelect("store-owner", owners, "全部归属账号");
            paintStores();
        }).catch(function (err) {
            body.innerHTML = '<tr><td colspan="6" class="empty">' + Dash.escapeHtml(err.message) + "</td></tr>";
        });
    }

    setInterval(paintStores, 30000);

    ["store-search", "store-owner", "store-login"].forEach(function (id) {
        document.getElementById(id).addEventListener("input", paintStores);
        document.getElementById(id).addEventListener("change", paintStores);
    });

    var deleting = false;
    body.addEventListener("click", function (event) {
        var button = event.target.closest(".store-delete");
        if (!button || deleting) return;
        var id = Number(button.getAttribute("data-id"));
        var row = null;
        storeItems.forEach(function (item) {
            if (item.id === id) row = item;
        });
        if (!row) return;
        var name = row.name || row.shop_id || "这家店";
        if (!window.confirm("删除「" + name + "」？店铺、违规记录和账户记录会一起去掉。")) return;
        deleting = true;
        button.disabled = true;
        Dash.request("/stores/" + id, { method: "DELETE" }).then(function () {
            storeItems = storeItems.filter(function (item) { return item.id !== id; });
            paintStores();
            Dash.showToast("已删除「" + name + "」", "success");
        }).catch(function (err) {
            Dash.showToast(err.message || "删除失败", "error");
            button.disabled = false;
        }).then(function () {
            deleting = false;
        });
    });

    var loginTimer = null;
    var enterTimer = null;
    var captchaHold = false;
    var enterBase = null;
    var enterNote = "";
    var enterBeat = 0;
    var lastState = null;
    var frameTimer = null;
    var framePulls = 0;
    var framePullAt = 0;

    function hideLogin(reload) {
        if (loginTimer) {
            clearInterval(loginTimer);
            loginTimer = null;
        }
        document.getElementById("login-mask").classList.add("hidden");
        document.getElementById("login-mask").classList.remove("working");
        document.getElementById("login-frame").src = "about:blank";
        if (reload) loadStores();
    }

    function stopEnter(reload) {
        if (enterTimer) {
            clearInterval(enterTimer);
            enterTimer = null;
        }
        if (frameTimer) {
            clearInterval(frameTimer);
            frameTimer = null;
        }
        document.getElementById("enter-mask").classList.add("hidden");
        hideLogin(reload);
        if (window.Dash.loadAccounts) window.Dash.loadAccounts();
    }

    function gainedCount(state) {
        if (enterBase === null) enterBase = state.done || 0;
        return Math.max(0, (state.done || 0) - enterBase);
    }

    function paintEnter(state) {
        lastState = state;
        var total = state.total || 0;
        var gained = gainedCount(state);
        var text = document.getElementById("enter-text");
        var bar = document.getElementById("enter-bar");
        var line = total ? ("已拿到凭证 " + (state.done || 0) + " / " + total + " 家") : "准备进店…";
        if (enterNote) line += "，" + enterNote;
        text.textContent = line;
        bar.style.width = total ? Math.min(100, Math.round((state.done || 0) * 100 / total)) + "%" : "0";
        return total > 0 && (enterBase + gained) >= total;
    }

    function finishEnter(state) {
        var total = state.total || 0;
        var gained = gainedCount(state);
        var finished = total > 0 && (enterBase + gained) >= total;
        stopEnter(true);
        Dash.showToast("已拿到凭证 " + (state.done || 0) + " / " + total + " 家", finished ? "success" : "error");
    }

    function watchEnter() {
        if (enterTimer) return;
        var last = -1;
        enterTimer = setInterval(function () {
            Dash.request("/stores/enter-progress").then(function (state) {
                var finished = paintEnter(state);
                if (captchaHold) {
                    document.getElementById("enter-text").textContent = "请先手动完成滑块验证";
                    enterBeat = Date.now();
                    return;
                }
                if ((state.done || 0) !== last) {
                    enterBeat = Date.now();
                    framePulls = 0;
                    postPending();
                }
                last = state.done || 0;
                if (finished || Date.now() - enterBeat > 60000) finishEnter(state);
            }).catch(function () {});
        }, 1000);
    }

    function postPending() {
        var frame = document.getElementById("login-frame");
        Dash.request("/stores").then(function (data) {
            var names = (data.items || []).filter(function (row) {
                return !row.shop_cookie;
            }).map(function (row) {
                return row.name;
            }).filter(Boolean);
            try {
                if (frame.contentWindow) {
                    frame.contentWindow.postMessage({ type: "dy-names", names: names }, location.origin);
                }
            } catch (err) {}
        }).catch(function () {});
    }

    function pullFrame(frame) {
        var now = Date.now();
        if (now - framePullAt < 4000) return;
        framePullAt = now;
        framePulls += 1;
        enterNote = "回到选店页";
        if (framePulls < 6) enterBeat = now;
        if (lastState) paintEnter(lastState);
        frame.src = "/login/common?channel=zhaoshang&n=" + now;
    }

    function watchFrame() {
        if (frameTimer) return;
        frameTimer = setInterval(function () {
            if (!enterTimer || captchaHold) {
                if (!enterTimer) {
                    clearInterval(frameTimer);
                    frameTimer = null;
                }
                return;
            }
            var frame = document.getElementById("login-frame");
            if (!frame) return;
            var href = "";
            try {
                href = frame.contentWindow.location.href;
            } catch (err) {
                pullFrame(frame);
                return;
            }
            if (href.indexOf("/dyp/buyin.jinritemai.com") >= 0 && href.indexOf("callback") < 0) {
                pullFrame(frame);
                return;
            }
            if (href.indexOf("/login/common") >= 0 && framePullAt && Date.now() - framePullAt < 8000) {
                try {
                    frame.contentWindow.postMessage({ type: "dy-reset" }, location.origin);
                } catch (err) {}
                framePullAt = 0;
                postPending();
            }
        }, 1000);
    }

    function startEnter() {
        if (loginTimer) {
            clearInterval(loginTimer);
            loginTimer = null;
        }
        enterBase = null;
        enterNote = "正在点选店铺";
        enterBeat = Date.now();
        framePulls = 0;
        document.getElementById("login-mask").classList.add("working");
        document.getElementById("enter-mask").classList.remove("hidden");
        var frame = document.getElementById("login-frame");
        try { frame.focus(); if (frame.contentWindow) frame.contentWindow.focus(); } catch (err) {}
        postPending();
        watchEnter();
        watchFrame();
    }

    function watchLogin(baseline) {
        if (loginTimer) clearInterval(loginTimer);
        loginTimer = setInterval(function () {
            if (document.getElementById("login-mask").classList.contains("hidden")) {
                hideLogin(false);
                return;
            }
            Dash.request("/stores/login-sync").then(function (state) {
                if ((state.mark || 0) === baseline || !(state.total > 0)) return;
                startEnter();
            }).catch(function () {});
        }, 1000);
    }

    window.addEventListener("message", function (event) {
        if (!event.data || event.origin !== location.origin) return;
        if (event.data.type === "dy-captcha") {
            if (!enterTimer) return;
            captchaHold = true;
            document.getElementById("enter-mask").classList.add("hidden");
            document.getElementById("login-mask").classList.remove("hidden", "working");
            document.getElementById("login-watch").textContent = "出现滑块验证，请手动拖动完成。完成后会继续读取，不要关这个窗口。";
            return;
        }
        if (event.data.type === "dy-captcha-done") {
            if (!captchaHold) return;
            captchaHold = false;
            document.getElementById("login-watch").textContent = "登录成功后这个窗口会关掉，接着自动读取每家店的登录凭证。";
            document.getElementById("login-mask").classList.add("working");
            document.getElementById("enter-mask").classList.remove("hidden");
            return;
        }
        if (event.data.type === "dy-status") {
            enterNote = event.data.name ? ("正在进入 " + event.data.name) : "正在翻店铺列表";
            enterBeat = Date.now();
            if (lastState) paintEnter(lastState);
            return;
        }
        if (event.data.type === "dy-picker") {
            enterBeat = Date.now();
            if (!enterTimer) startEnter();
            return;
        }
        if (event.data.type === "dy-enter-done") {
            Dash.request("/stores/enter-progress").then(function (state) {
                paintEnter(state);
                finishEnter(state);
            }).catch(function () {});
        }
    });

    document.getElementById("close-login").addEventListener("click", function () {
        hideLogin(true);
    });

    loadStores();
    window.Dash.loadStores = loadStores;
})();
