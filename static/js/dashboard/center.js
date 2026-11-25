(function () {
    "use strict";

    var timer = null;
    var bound = false;
    var shops = [];
    var latest = null;
    var homeData = null;
    var homeTimer = null;
    var homePretend = null;
    var shopData = null;
    var shopTimer = null;
    var productData = null;
    var productTimer = null;
    var tab = "overview";
    var shopSort = { key: "pay_amount", dir: "desc" };
    var productSort = { key: "pay_amount", dir: "desc" };
    var homeSort = { key: "", dir: "desc" };
    var scheduleTimer = null;
    var scheduleSeen = "";
    var scheduleState = null;
    var dailySeen = null;
    var shellPreset = "realtime";
    var shellStart = "";
    var shellEnd = "";
    var productPresetValue = "7";
    var productStartValue = "";
    var productEndValue = "";
    var dealData = null;
    var dealMetric = {};
    var dealSpan = 7;
    var dealAccount = "";
    var dealChartSeq = 0;
    var DEAL_METRICS = [
        { key: "pay_gmv", label: "成交金额", money: true },
        { key: "user_pay_gmv", label: "用户支付金额", money: true },
        { key: "pay_order_cnt", label: "成交订单数", money: false },
        { key: "pay_uv", label: "成交人数", money: false },
        { key: "refund_pay_gmv", label: "退款金额（支付时间）", money: true },
        { key: "refund_pay_ratio", label: "退款率（支付时间）", money: false, rate: true },
        { key: "refund_pay_cnt", label: "退款订单数（支付时间）", money: false }
    ];
    var SHELL_OPTIONS = [
        ["realtime", "实时"],
        ["1", "近1天"],
        ["7", "近7天"],
        ["30", "近30天"],
        ["month", "本月"],
        ["custom", "自定义"]
    ];
    var PRODUCT_OPTIONS = [
        ["realtime", "实时"],
        ["1", "近1天"],
        ["7", "近7天"],
        ["30", "近30天"],
        ["month", "本月"],
        ["custom", "自定义"]
    ];

    function el(id) {
        return document.getElementById(id);
    }

    function preset() {
        return el("center-preset").value;
    }

    function fmt(day) {
        var y = day.getFullYear();
        var m = String(day.getMonth() + 1).padStart(2, "0");
        var d = String(day.getDate()).padStart(2, "0");
        return y + "-" + m + "-" + d;
    }

    function shift(days) {
        var day = new Date();
        day.setHours(12, 0, 0, 0);
        day.setDate(day.getDate() + days);
        return day;
    }

    function parseIso(value) {
        var parts = (value || "").split("-");
        if (parts.length < 3) return shift(-1);
        return new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]), 12, 0, 0, 0);
    }

    function paintRangeNote() {
        var note = el("center-range-note");
        if (!note) return;
        if (tab === "products") {
            note.textContent = "实时是首页商品列表今天的商品。近1天、近7天、近30天、本月是另一套商品列表，本月从1号加到昨天。自定义选开始和结束，最多 90 天。";
            return;
        }
        note.textContent = "默认实时。近 1 天、近 7 天、近 30 天、本月截止到昨天。自定义只能选今天和前 90 天。";
    }

    function presetSpan(mode) {
        var from = shift(0);
        var to = shift(0);
        if (mode === "1") {
            from = shift(-1);
            to = shift(-1);
        } else if (mode === "7") {
            to = shift(-1);
            from = shift(-7);
        } else if (mode === "30") {
            to = shift(-1);
            from = shift(-30);
        } else if (mode === "month") {
            to = shift(-1);
            from = new Date(to.getFullYear(), to.getMonth(), 1, 12, 0, 0, 0);
        }
        return { from: from, to: to };
    }

    function fillProductDates(mode, start, end, resetPick) {
        var custom = mode === "custom";
        start.disabled = !custom;
        end.disabled = !custom;
        if (custom) {
            if (resetPick) {
                start.value = "";
                end.value = "";
            }
            return;
        }
        var span = presetSpan(mode);
        start.value = fmt(span.from);
        end.value = fmt(span.to);
    }

    function fillPresetDates(resetPick) {
        var mode = preset();
        var start = el("center-start");
        var end = el("center-end");
        if (tab === "products") {
            fillProductDates(mode, start, end, !!resetPick);
            paintRangeNote();
            return;
        }
        var custom = mode === "custom";
        start.disabled = !custom;
        end.disabled = !custom;
        paintRangeNote();
        if (custom) return;
        var span = presetSpan(mode);
        start.value = fmt(span.from);
        end.value = fmt(span.to);
    }

    function rangeQuery() {
        var path = "/center/overview?preset=" + encodeURIComponent(preset());
        if (preset() !== "custom") return path;
        var start = el("center-start").value;
        var end = el("center-end").value;
        if (!start || !end) return "";
        return path + "&start=" + encodeURIComponent(start) + "&end=" + encodeURIComponent(end);
    }

    function chargeLabel(id) {
        var select = el(id || "center-charge");
        if (!select) return "全部负责人";
        return select.options[select.selectedIndex] ? select.options[select.selectedIndex].text : "全部负责人";
    }

    function stateLabel() {
        var select = el("center-state");
        if (!select) return "全部状态";
        return select.options[select.selectedIndex] ? select.options[select.selectedIndex].text : "全部状态";
    }

    function matchesState(label, wanted) {
        if (!wanted) return true;
        return (label || "正常") === wanted;
    }

    function accountLabel() {
        var select = el("center-account");
        return select.options[select.selectedIndex] ? select.options[select.selectedIndex].text : "全部账号";
    }

    function shopLabel() {
        var select = el("center-shop");
        return select.options[select.selectedIndex] ? select.options[select.selectedIndex].text : "全部店铺";
    }

    function timeLabel() {
        var select = el("center-preset");
        var name = select.options[select.selectedIndex] ? select.options[select.selectedIndex].text : "";
        var start = el("center-start").value;
        var end = el("center-end").value;
        if (tab === "products" && productData && productData._query === productQuery() && productData.start) {
            start = productData.start;
            end = productData.end || productData.start;
        }
        if (!start) return name;
        if (!end || start === end) return name + "（" + start + "）";
        return name + "（" + start + " 至 " + end + "）";
    }

    function paintShellFilters() {
        var shopText = "当前筛选：" + accountLabel() + " · " + chargeLabel() + " · " + stateLabel() + " · " + shopLabel() + " · " + timeLabel();
        el("center-shops-filter").textContent = shopText;
        var productText = "当前筛选：" + accountLabel() + " · " + chargeLabel() + " · " + stateLabel() + " · " + shopLabel() + " · " + timeLabel();
        el("center-products-filter").textContent = productText;
    }

    function productQuery() {
        var mode = preset();
        var path = "/center/products?preset=" + encodeURIComponent(mode);
        var start = el("center-start").value;
        var end = el("center-end").value;
        if (mode === "custom") {
            if (!start || !end) return "";
            return path + "&start=" + encodeURIComponent(start) + "&end=" + encodeURIComponent(end);
        }
        return path;
    }

    function productEmptyText(data) {
        if (productIsLive(data)) return data && data.updated_at ? "今天还没有商品" : "实时商品还没有，点查询拉取";
        if (data && data.updated_at) return "这个时间没有商品";
        return "这个时间还没有入库的商品";
    }

    function paintSchedule(state) {
        if (state) scheduleState = state;
        var node = el("center-schedule");
        if (!node) return;
        var current = scheduleState;
        var text = "";
        if (tab === "products" && current && current.next_update) {
            text = "实时数据下次更新 " + current.next_update;
        } else if (tab === "shops" && current && current.next_update) {
            text = "每" + (current.interval_minutes || 10) + "分钟更新一次，下次更新 " + current.next_update;
        }
        node.textContent = text;
        node.classList.toggle("hidden", !text);
    }

    function watchSchedule() {
        if (scheduleTimer) return;
        function tick() {
            scheduleTimer = null;
            if (!centerVisible()) {
                scheduleTimer = setTimeout(tick, 5000);
                return;
            }
            Dash.request("/center/schedule").then(function (state) {
                paintSchedule(state);
                var dailyStamp = state.daily_stamp || "";
                if (dailySeen === null) dailySeen = dailyStamp;
                else if (dailyStamp !== dailySeen) {
                    dailySeen = dailyStamp;
                    if (tab === "overview") loadDeals();
                    if (tab === "home") loadHome();
                }
                if (state.last_update && state.last_update !== scheduleSeen) {
                    scheduleSeen = state.last_update;
                    if (tab === "shops") queryShops();
                    if (tab === "products") queryProducts();
                }
                scheduleTimer = setTimeout(tick, 5000);
            }).catch(function () {
                scheduleTimer = setTimeout(tick, 5000);
            });
        }
        tick();
    }

    function matchesCharge(person, wanted) {
        var name = (person || "").trim();
        if (!wanted) return true;
        if (wanted === "未分配") return !name;
        return name === wanted;
    }

    function scopedShops() {
        var account = el("center-account").value;
        var charge = el("center-charge") ? el("center-charge").value : "";
        return shops.filter(function (row) {
            if (account && (row.owner_account || "") !== account) return false;
            return matchesCharge(row.charge_name, charge);
        });
    }

    function shopsOfAccount() {
        var state = el("center-state") ? el("center-state").value : "";
        return scopedShops().filter(function (row) {
            if (!state || !row.shop_state) return true;
            return matchesState(row.shop_state, state);
        });
    }

    function fillCharges(selectId, rows) {
        var select = el(selectId);
        if (!select) return;
        var current = select.value;
        var names = [];
        var hasEmpty = false;
        (rows || []).forEach(function (row) {
            var person = (row.charge_name || "").trim();
            if (!person) hasEmpty = true;
            else if (names.indexOf(person) < 0) names.push(person);
        });
        if (hasEmpty) names.push("未分配");
        var options = ['<option value="">全部负责人</option>'];
        names.forEach(function (name) {
            options.push('<option value="' + Dash.escapeHtml(name) + '">' + Dash.escapeHtml(name) + "</option>");
        });
        select.innerHTML = options.join("");
        select.value = names.indexOf(current) >= 0 ? current : "";
    }

    function fillAccounts() {
        var select = el("center-account");
        var current = select.value;
        var owners = [];
        shops.forEach(function (row) {
            if (row.owner_account && owners.indexOf(row.owner_account) < 0) owners.push(row.owner_account);
        });
        var options = ['<option value="">全部账号</option>'];
        owners.forEach(function (owner) {
            options.push('<option value="' + Dash.escapeHtml(owner) + '">' + Dash.escapeHtml(owner) + "</option>");
        });
        select.innerHTML = options.join("");
        select.value = owners.indexOf(current) >= 0 ? current : "";
    }

    function fillShops() {
        var select = el("center-shop");
        var current = select.value;
        var rows = shopsOfAccount();
        var options = ['<option value="">全部店铺</option>'];
        rows.forEach(function (row) {
            options.push('<option value="' + Dash.escapeHtml(row.shop_id) + '">' + Dash.escapeHtml(row.name || row.shop_id) + "</option>");
        });
        select.innerHTML = options.join("");
        var still = rows.some(function (row) { return String(row.shop_id) === current; });
        select.value = still ? current : "";
    }

    function visibleItems(items) {
        var account = el("center-account").value;
        var shopId = el("center-shop").value;
        var charge = el("center-charge") ? el("center-charge").value : "";
        var state = el("center-state") ? el("center-state").value : "";
        var allowed = {};
        scopedShops().forEach(function (row) { allowed[String(row.shop_id)] = true; });
        return (items || []).filter(function (item) {
            var id = String(item.shop_id || "");
            if (shopId && id !== shopId) return false;
            if ((account || charge) && shops.length && !allowed[id]) return false;
            if (!shops.length && !matchesCharge(item.charge_name, charge)) return false;
            if (!matchesState(item.shop_state, state)) return false;
            return true;
        });
    }

    function productKeyword() {
        var input = el("center-product-q");
        return input ? input.value.trim().toLowerCase() : "";
    }

    function productIsLive(data) {
        return !!data && (data.layout === "shops" || data.preset === "realtime");
    }

    function matchProductSearch(items) {
        var keyword = productKeyword();
        if (!keyword) return items;
        var live = productIsLive(productData);
        return items.filter(function (item) {
            var name = String(item.product_name || "").toLowerCase();
            var id = String(item.product_id || "").toLowerCase();
            if (name.indexOf(keyword) >= 0 || id.indexOf(keyword) >= 0) return true;
            if (!live) return false;
            var shopName = String(item.name || "").toLowerCase();
            var shopId = String(item.shop_id || "").toLowerCase();
            return shopName.indexOf(keyword) >= 0 || shopId.indexOf(keyword) >= 0;
        });
    }

    function ratioClass(text) {
        if (!text) return "";
        if (text.charAt(0) === "-") return "down";
        if (text.charAt(0) === "+") return "up";
        return "";
    }

    function paint(data) {
        latest = data || latest;
        paintShellFilters();
        if (preset() !== "custom" && latest && latest.start && latest.end) {
            el("center-start").value = latest.start;
            el("center-end").value = latest.end;
        }
        var list = el("center-list");
        var items = visibleItems((latest && latest.items) || []);
        if (!(latest && latest.items && latest.items.length)) {
            list.innerHTML = '<p class="empty">还没有店铺</p>';
            return;
        }
        if (!items.length) {
            list.innerHTML = '<p class="empty">没有符合当前筛选的数据</p>';
            return;
        }
        list.innerHTML = items.map(function (item) {
            var head = '<h3>' + Dash.escapeHtml(item.name || "未命名店铺")
                + '<span class="shop-id">' + Dash.escapeHtml(item.charge_name || "未分配") + "</span>"
                + '<span class="shop-id">' + Dash.escapeHtml(item.shop_id || "") + '</span></h3>';
            if (item.sync_error) {
                return '<article class="center-shop">' + head + '<p class="center-error">' + Dash.escapeHtml(item.sync_error) + '</p></article>';
            }
            var cards = (item.metrics || []).map(function (metric) {
                var sub = [metric.ratio_text, metric.extra_text].filter(Boolean).join(" · ");
                return '<div class="center-metric"><div class="label">' + Dash.escapeHtml(metric.label)
                    + '</div><div class="value">' + Dash.escapeHtml(metric.value_text)
                    + '</div><div class="sub ' + ratioClass(metric.ratio_text) + '">' + Dash.escapeHtml(sub) + '</div></div>';
            }).join("");
            return '<article class="center-shop">' + head + '<div class="center-metrics">' + cards + '</div></article>';
        }).join("");
    }

    function query() {
        var path = rangeQuery();
        if (!path) return;
        Dash.request(path).then(paint).catch(function (err) {
            Dash.showToast(err.message || "加载失败", "error");
        });
    }

    function centerVisible() {
        var view = el("view-center");
        return !!(view && view.classList.contains("active"));
    }

    function paintProgress(state) {
        var box = el("center-progress");
        var running = !!(state && state.running && centerVisible() && tab === "overview");
        el("btn-refresh-center").disabled = running;
        el("btn-center-query").disabled = running;
        if (!running) {
            box.classList.add("hidden");
            el("center-bar").style.width = "0";
            return;
        }
        box.classList.remove("hidden");
        var total = state.total || 0;
        var done = state.done || 0;
        el("center-bar").style.width = (total ? Math.round(done / total * 100) : 8) + "%";
        el("center-progress-text").textContent = total
            ? ("正在拉取收支概况 " + done + " / " + total + " 家")
            : "正在拉取…";
    }

    function watch() {
        if (!centerVisible()) {
            timer = null;
            el("center-progress").classList.add("hidden");
            el("btn-refresh-center").disabled = false;
            el("btn-center-query").disabled = false;
            return;
        }
        Dash.request("/center/overview/progress").then(function (state) {
            paintProgress(state);
            if (state.running) {
                timer = setTimeout(watch, 2000);
                return;
            }
            timer = null;
            if (state.failed && tab === "overview") Dash.showToast(state.message || "有店铺没有拉到", "error");
            query();
        }).catch(function () {
            timer = setTimeout(watch, 2000);
        });
    }

    function refresh() {
        fillPresetDates();
        paintShellFilters();
        var body = { preset: preset(), start: "", end: "" };
        if (body.preset === "custom") {
            body.start = el("center-start").value;
            body.end = el("center-end").value;
            if (!body.start || !body.end) {
                Dash.showToast("请选择开始和结束日期", "error");
                return;
            }
        }
        el("center-list").innerHTML = "";
        Dash.request("/center/overview/refresh", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        }).then(function (state) {
            paintProgress(state);
            if (timer) return;
            watch();
        }).catch(function (err) {
            Dash.showToast(err.message || "拉取失败", "error");
        });
    }

    function loginTag(status) {
        return status === "online"
            ? '<span class="tag tag-ok">在线</span>'
            : '<span class="tag tag-bad">掉线</span>';
    }

    function homeNumber(value) {
        return value === "" || value == null ? "—" : String(value);
    }

    function shopStateCell(item) {
        var label = item.shop_state || "正常";
        var due = item.shop_state_due || "";
        var tone = label === "已清退" || label === "停业整顿中" ? "bad" : (label === "限单中" ? "warn" : "ok");
        var html = '<td class="shop-state"><div class="shop-state-name ' + tone + '">' + Dash.escapeHtml(label) + "</div>";
        if (label !== "正常" && due) {
            html += '<div class="shop-state-due">到期时间 ' + Dash.escapeHtml(due) + "</div>";
        }
        return html + "</td>";
    }

    function paintHome() {
        if (!homeData) return;
        var body = el("center-home-tbody");
        var items = sortedCopy(visibleItems(homeData.items || []), homeSort);
        paintSortMarks();
        paintHeadSummary(items);
        if (!(homeData.items && homeData.items.length)) {
            body.innerHTML = '<tr><td colspan="11" class="empty">还没有店铺</td></tr>';
            return;
        }
        var homeCached = !!homeData.updated_at || (homeData.items || []).some(function (item) {
            return item.pending_pay || item.pending_ship || item.abnormal_package
                || item.pending_aftersale || item.service_ticket || item.risk_point
                || item.pending_violation || item.sync_error;
        });
        if (!homeCached) {
            body.innerHTML = '<tr><td colspan="11" class="empty">正在准备，启动后会先更新一次</td></tr>';
            return;
        }
        if (!items.length) {
            body.innerHTML = '<tr><td colspan="11" class="empty">没有符合当前筛选的数据</td></tr>';
            return;
        }
        body.innerHTML = items.map(function (item) {
            var name = "<td>" + Dash.escapeHtml(item.name || "未命名店铺")
                + '<div class="center-home-id">' + Dash.escapeHtml(item.shop_id || "") + "</div></td>";
            var charge = "<td>" + Dash.escapeHtml(item.charge_name || "—") + "</td>";
            var state = shopStateCell(item);
            var login = "<td>" + loginTag(item.login_status) + "</td>";
            if (item.sync_error) {
                return "<tr>" + name + charge + state + login + '<td colspan="7" class="center-error">' + Dash.escapeHtml(item.sync_error) + "</td></tr>";
            }
            var violation = '<td><button type="button" class="center-jump" data-vio-shop="'
                + Dash.escapeHtml(String(item.store_pk)) + '">'
                + Dash.escapeHtml(homeNumber(item.pending_violation)) + "</button></td>";
            return "<tr>" + name + charge + state + login
                + "<td>" + Dash.escapeHtml(homeNumber(item.pending_pay)) + "</td>"
                + "<td>" + Dash.escapeHtml(homeNumber(item.pending_ship)) + "</td>"
                + "<td>" + Dash.escapeHtml(homeNumber(item.abnormal_package)) + "</td>"
                + "<td>" + Dash.escapeHtml(homeNumber(item.pending_aftersale)) + "</td>"
                + "<td>" + Dash.escapeHtml(homeNumber(item.service_ticket)) + "</td>"
                + "<td>" + Dash.escapeHtml(homeNumber(item.risk_point)) + "</td>"
                + violation + "</tr>";
        }).join("");
    }

    function loadHome() {
        Dash.request("/center/home").then(function (data) {
            homeData = data;
            paintHome();
        }).catch(function (err) {
            el("center-home-tbody").innerHTML = '<tr><td colspan="11" class="empty">'
                + Dash.escapeHtml(err.message || "加载失败") + "</td></tr>";
        });
    }

    function stopHomePretend() {
        if (!homePretend) return;
        clearTimeout(homePretend);
        homePretend = null;
    }

    function paintHomeProgress(state) {
        var box = el("center-home-progress");
        var running = !!(state && state.running && centerVisible() && tab === "home");
        if (tab === "home" && !homePretend) {
            el("btn-refresh-center").disabled = running;
            el("btn-center-query").disabled = running;
        }
        if (!running) {
            if (!homePretend) {
                box.classList.add("hidden");
                el("center-home-bar").style.width = "0";
            }
            return;
        }
        box.classList.remove("hidden");
        var total = state.total || 0;
        var done = state.done || 0;
        el("center-home-bar").style.width = (total ? Math.min(100, Math.round(done / total * 100)) : 8) + "%";
        el("center-home-progress-text").textContent = total
            ? ("正在拉取店铺首页 " + done + " / " + total + " 家")
            : "正在拉取店铺首页…";
    }

    function watchHome() {
        if (!centerVisible() || tab !== "home") {
            homeTimer = null;
            if (!homePretend) {
                el("center-home-progress").classList.add("hidden");
                el("center-home-bar").style.width = "0";
            }
            if (!centerVisible() && !homePretend) {
                el("btn-refresh-center").disabled = false;
                el("btn-center-query").disabled = false;
            }
            return;
        }
        Dash.request("/center/home/progress").then(function (state) {
            paintHomeProgress(state);
            if (state.running) {
                homeTimer = setTimeout(watchHome, 1000);
                return;
            }
            homeTimer = null;
            if (state.failed && tab === "home") Dash.showToast(state.message || "有店铺没有拉到", "error");
            loadHome();
        }).catch(function () {
            homeTimer = setTimeout(watchHome, 2000);
        });
    }

    function pretendHome() {
        stopHomePretend();
        var box = el("center-home-progress");
        var bar = el("center-home-bar");
        var steps = [16, 41, 68, 88, 100];
        var index = 0;
        box.classList.remove("hidden");
        el("center-home-progress-text").textContent = "正在拉取店铺首页…";
        el("btn-refresh-center").disabled = true;
        el("btn-center-query").disabled = true;
        function finish() {
            homePretend = null;
            box.classList.add("hidden");
            bar.style.width = "0";
            el("btn-refresh-center").disabled = false;
            el("btn-center-query").disabled = false;
            if (centerVisible() && tab === "home") loadHome();
        }
        function tick() {
            if (!centerVisible() || tab !== "home") {
                finish();
                return;
            }
            bar.style.width = steps[index] + "%";
            index += 1;
            if (index < steps.length) {
                homePretend = setTimeout(tick, 420);
                return;
            }
            homePretend = setTimeout(finish, 280);
        }
        tick();
    }

    function refreshHome() {
        var button = el("btn-refresh-center");
        if (button.disabled || homePretend || homeTimer) return;
        button.disabled = true;
        el("btn-center-query").disabled = true;
        Dash.request("/center/home/refresh", { method: "POST" }).then(function (state) {
            if (state.fresh) {
                pretendHome();
                return;
            }
            paintHomeProgress(state);
            if (homeTimer) return;
            watchHome();
        }).catch(function (err) {
            button.disabled = false;
            el("btn-center-query").disabled = false;
            Dash.showToast(err.message || "拉取失败", "error");
        });
    }

    function resumeHome() {
        if (homePretend || homeTimer || tab !== "home") return;
        Dash.request("/center/home/progress").then(function (state) {
            if (tab !== "home" || homePretend) return;
            if (!state.running) return;
            paintHomeProgress(state);
            watchHome();
        }).catch(function () {});
    }

    var LIVE_METRIC_KEYS = [
        "user_pay_amount",
        "show_user_count",
        "pay_amount",
        "order_count",
        "ad_cost",
        "refund_amount",
        "refund_order_count",
        "refund_rate",
        "pay_user_count",
        "avg_pay",
        "stock_count",
        "net_pay_amount",
        "net_order_count",
        "refund_rate_1h",
        "refund_amount_1h",
        "refund_order_count_1h"
    ];

    function metricCell(value) {
        return '<td><div class="center-shop-value">' + Dash.escapeHtml(value || "—") + "</div></td>";
    }

    function liveMetricCells(item) {
        return LIVE_METRIC_KEYS.map(function (key) {
            return metricCell(item[key]);
        }).join("");
    }

    function rateText(part, whole) {
        if (!whole) return "—";
        var rate = (part / whole) * 100;
        if (Math.abs(rate - Math.round(rate)) < 0.05) return Math.round(rate) + "%";
        return rate.toFixed(2) + "%";
    }

    function liveCell(item) {
        var shopId = String(item.store_pk || "").replace(/\D/g, "");
        return '<button type="button" class="center-live-btn" data-live-shop="' + shopId
            + '" data-live-name="' + Dash.escapeHtml(item.name || "") + '">明细</button>';
    }

    function paintLive(data) {
        var body = el("live-tbody");
        var items = (data && data.items) || [];
        var note = el("live-note");
        if (!items.length) {
            body.innerHTML = '<tr><td colspan="5" class="empty">'
                + Dash.escapeHtml((data && data.error) || "这家店没有商品品质退货明细") + "</td></tr>";
            note.textContent = "";
            return;
        }
        note.textContent = data.error || "";
        body.innerHTML = items.map(function (item) {
            return "<tr><td class=\"center-product-name\">" + Dash.escapeHtml(item.product_name || "未命名商品")
                + '<div class="center-home-id">ID ' + Dash.escapeHtml(item.product_id || "") + "</div></td>"
                + "<td>" + Dash.escapeHtml(item.category || "—") + "</td>"
                + "<td>" + Dash.escapeHtml(item.order_count || "—") + "</td>"
                + "<td>" + Dash.escapeHtml(item.contribute_rate || "—") + "</td>"
                + "<td>" + Dash.escapeHtml(item.reasons || "—") + "</td></tr>";
        }).join("");
    }

    function closeLive() {
        el("live-mask").classList.add("hidden");
    }

    function openLive(storePk, shopName) {
        var mask = el("live-mask");
        el("live-title").textContent = (shopName || "店铺") + " 商品品质退货率";
        el("live-note").textContent = "正在获取…";
        el("live-tbody").innerHTML = '<tr><td colspan="5" class="empty">正在获取…</td></tr>';
        mask.classList.remove("hidden");
        Dash.request("/center/shops/" + storePk + "/quality-returns").then(function (data) {
            if (mask.classList.contains("hidden")) return;
            paintLive(data);
        }).catch(function (err) {
            if (mask.classList.contains("hidden")) return;
            el("live-note").textContent = "";
            el("live-tbody").innerHTML = '<tr><td colspan="5" class="empty">'
                + Dash.escapeHtml(err.message || "获取失败") + "</td></tr>";
        });
    }

    function scoreCell(value, change) {
        var sub = "";
        if (change) {
            var kind = change.indexOf("↑") >= 0 ? " up" : (change.indexOf("↓") >= 0 ? " down" : "");
            sub = '<div class="center-score-change' + kind + '">' + Dash.escapeHtml(change) + "</div>";
        }
        return '<td><div class="center-shop-value">' + Dash.escapeHtml(value || "—") + "</div>" + sub + "</td>";
    }

    function metricNumber(text) {
        if (text === undefined || text === null) return null;
        var cleaned = String(text).replace(/[¥,%\s,]/g, "");
        if (!cleaned || cleaned === "—" || cleaned === "-") return null;
        var number = Number(cleaned);
        return isNaN(number) ? null : number;
    }

    function formatMoney(number) {
        var sign = number < 0 ? "-" : "";
        var parts = Math.abs(number).toFixed(2).split(".");
        parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
        return sign + "¥" + parts[0] + "." + parts[1];
    }

    function formatCount(number) {
        return String(Math.round(number)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    }

    function sumField(items, key) {
        var total = 0;
        items.forEach(function (item) {
            if (item.sync_error) return;
            var number = metricNumber(item[key]);
            if (number === null) return;
            total += number;
        });
        return Math.round(total * 100) / 100;
    }

    function sortedCopy(items, state) {
        var list = items.slice();
        if (!state.key) return list;
        var dir = state.dir === "asc" ? 1 : -1;
        var textKey = state.key === "name" || state.key === "product_name" || state.key === "login_status";
        list.sort(function (a, b) {
            if (textKey) {
                var left = String(a[state.key] || "");
                var right = String(b[state.key] || "");
                return left.localeCompare(right, "zh") * dir;
            }
            var leftNumber = metricNumber(a[state.key]);
            var rightNumber = metricNumber(b[state.key]);
            if (leftNumber === null && rightNumber === null) return 0;
            if (leftNumber === null) return 1;
            if (rightNumber === null) return -1;
            return (leftNumber - rightNumber) * dir;
        });
        return list;
    }

    function paintSortMarks() {
        document.querySelectorAll(".center-sort").forEach(function (btn) {
            var table = btn.getAttribute("data-sort-for");
            var key = btn.getAttribute("data-sort-key");
            var state = table === "shops" ? shopSort : table === "home" ? homeSort : productSort;
            var mark = state.key === key ? (state.dir === "asc" ? " ↑" : " ↓") : "";
            btn.textContent = btn.getAttribute("data-sort-label") + mark;
        });
    }

    function paintHeadSummary(items) {
        var node = el("center-summary");
        if (!node) return;
        var fields;
        if (tab === "home") {
            fields = [
                ["待支付", formatCount(sumField(items || [], "pending_pay"))],
                ["待发货", formatCount(sumField(items || [], "pending_ship"))],
                ["异常包裹", formatCount(sumField(items || [], "abnormal_package"))],
                ["待处理售后", formatCount(sumField(items || [], "pending_aftersale"))],
                ["服务工单", formatCount(sumField(items || [], "service_ticket"))],
                ["待整改风险点", formatCount(sumField(items || [], "risk_point"))],
                ["待处理违规", formatCount(sumField(items || [], "pending_violation"))]
            ];
        } else if (tab === "products" && productIsLive(productData)) {
            var pay = sumField(items || [], "pay_amount");
            var userPay = sumField(items || [], "user_pay_amount");
            var buyers = sumField(items || [], "pay_user_count");
            var refund = sumField(items || [], "refund_amount");
            fields = [
                ["用户支付金额", formatMoney(userPay)],
                ["曝光人数", formatCount(sumField(items || [], "show_user_count"))],
                ["成交金额", formatMoney(pay)],
                ["成交订单数", formatCount(sumField(items || [], "order_count"))],
                ["投放消耗", formatMoney(sumField(items || [], "ad_cost"))],
                ["退款金额", formatMoney(refund)],
                ["退款订单数", formatCount(sumField(items || [], "refund_order_count"))],
                ["退款率", rateText(refund, pay)],
                ["成交人数", formatCount(buyers)],
                ["客单价", buyers ? formatMoney(userPay / buyers) : "—"],
                ["实时可售库存", formatCount(sumField(items || [], "stock_count"))],
                ["净成交金额", formatMoney(sumField(items || [], "net_pay_amount"))],
                ["净成交订单", formatCount(sumField(items || [], "net_order_count"))],
                ["1小时退款金额", formatMoney(sumField(items || [], "refund_amount_1h"))],
                ["1小时退款订单数", formatCount(sumField(items || [], "refund_order_count_1h"))]
            ];
        } else if (tab === "shops" || tab === "products") {
            fields = [
                ["成交金额", formatMoney(sumField(items || [], "pay_amount"))],
                ["成交订单数", formatCount(sumField(items || [], "order_count"))],
                ["成交退款金额（支付时间）", formatMoney(sumField(items || [], "refund_amount"))]
            ];
        } else {
            node.innerHTML = "";
            return;
        }
        node.innerHTML = fields.map(function (field) {
            return '<span class="center-summary-item"><span class="label">' + field[0] + "</span><b>"
                + Dash.escapeHtml(field[1]) + "</b></span>";
        }).join("");
    }

    function paintShops() {
        if (!shopData) return;
        var body = el("center-shops-tbody");
        var items = sortedCopy(visibleItems(shopData.items || []), shopSort);
        paintSortMarks();
        paintHeadSummary(items);
        if (!(shopData.items && shopData.items.length)) {
            body.innerHTML = '<tr><td colspan="19" class="empty">还没有店铺</td></tr>';
            return;
        }
        if (!items.length) {
            body.innerHTML = '<tr><td colspan="19" class="empty">没有符合当前筛选的数据</td></tr>';
            return;
        }
        body.innerHTML = items.map(function (item) {
            var name = "<td>" + Dash.escapeHtml(item.name || "未命名店铺")
                + '<div class="center-home-id">' + Dash.escapeHtml(item.shop_id || "") + "</div></td>";
            var charge = "<td>" + Dash.escapeHtml(item.charge_name || "—") + "</td>";
            var state = shopStateCell(item);
            var scores = metricCell(item.experience_score)
                + scoreCell(item.goods_score, item.goods_score_change)
                + scoreCell(item.logistics_score, item.logistics_score_change)
                + scoreCell(item.service_score, item.service_score_change)
                + scoreCell(item.bonus_score, item.bonus_score_change)
                + scoreCell(item.deduct_score, item.deduct_score_change);
            var login = "<td>" + loginTag(item.login_status) + "</td>";
            var rates = metricCell(item.good_rate_30d)
                + metricCell(item.mid_rate_30d)
                + metricCell(item.bad_rate_30d);
            var quality = '<td><div class="center-shop-value">' + Dash.escapeHtml(item.quality_return_rate || "—") + "</div></td>";
            var detail = "<td>" + liveCell(item) + "</td>";
            if (item.sync_error) {
                return "<tr>" + name + charge + state + scores + login
                    + '<td colspan="4" class="center-error">' + Dash.escapeHtml(item.sync_error) + "</td>"
                    + quality
                    + detail
                    + rates + "</tr>";
            }
            return "<tr>" + name + charge + state + scores + login
                + metricCell(item.pay_amount)
                + metricCell(item.order_count)
                + metricCell(item.refund_rate)
                + metricCell(item.refund_amount)
                + quality
                + detail
                + rates
                + "</tr>";
        }).join("");
    }

    function paintShopProgress(state) {
        var box = el("center-shops-progress");
        var running = !!(state && state.running && centerVisible() && tab === "shops");
        if (tab === "shops") {
            el("btn-refresh-center").disabled = running;
            el("btn-center-query").disabled = running;
        }
        if (!running) {
            box.classList.add("hidden");
            el("center-shops-bar").style.width = "0";
            return;
        }
        box.classList.remove("hidden");
        var total = state.total || 0;
        var done = state.done || 0;
        el("center-shops-bar").style.width = (total ? Math.round(done / total * 100) : 8) + "%";
        el("center-shops-progress-text").textContent = total
            ? ("正在拉取店铺明细 " + done + " / " + total + " 家")
            : "正在拉取…";
    }

    function watchShops() {
        if (!centerVisible() || tab !== "shops") {
            shopTimer = null;
            el("center-shops-progress").classList.add("hidden");
            if (tab !== "overview" && tab !== "products") {
                el("btn-refresh-center").disabled = false;
                el("btn-center-query").disabled = false;
            }
            return;
        }
        Dash.request("/center/shops/progress").then(function (state) {
            paintShopProgress(state);
            if (state.running) {
                shopTimer = setTimeout(watchShops, 2000);
                return;
            }
            shopTimer = null;
            if (state.failed) Dash.showToast(state.message || "有店铺没有拉到", "error");
            queryShops();
        }).catch(function () {
            shopTimer = setTimeout(watchShops, 2000);
        });
    }

    function queryShops() {
        var path = rangeQuery().replace("/center/overview", "/center/shops");
        if (!path) return;
        Dash.request(path).then(function (data) {
            shopData = data;
            paintSchedule(data);
            paintShops();
        }).catch(function (err) {
            Dash.showToast(err.message || "加载失败", "error");
        });
    }

    function refreshShops() {
        fillPresetDates();
        paintShellFilters();
        var body = { preset: preset(), start: "", end: "" };
        if (body.preset === "custom") {
            body.start = el("center-start").value;
            body.end = el("center-end").value;
            if (!body.start || !body.end) {
                Dash.showToast("请选择开始和结束日期", "error");
                return;
            }
        }
        el("center-shops-tbody").innerHTML = "";
        Dash.request("/center/shops/refresh", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        }).then(function (state) {
            paintShopProgress(state);
            if (shopTimer) return;
            watchShops();
        }).catch(function (err) {
            Dash.showToast(err.message || "拉取失败", "error");
        });
    }

    function sameSpan(data) {
        if (!data || data.preset !== preset()) return false;
        if (preset() !== "custom") return true;
        return data.start === el("center-start").value && data.end === el("center-end").value;
    }

    function paintProducts() {
        if (!productData) return;
        var body = el("center-products-tbody");
        var live = productIsLive(productData);
        var itemHead = el("center-products-head-items");
        var liveHead = el("center-products-head-live");
        if (itemHead) itemHead.classList.toggle("hidden", live);
        if (liveHead) liveHead.classList.toggle("hidden", !live);
        if (body && body.closest("table")) body.closest("table").classList.toggle("center-live-table", live);
        var search = el("center-product-q");
        if (search) search.placeholder = live ? "商品名称、ID或店铺" : "商品名称或ID";
        var body = el("center-products-tbody");
        var span = live ? 20 : 9;
        var scoped = visibleItems(productData.items || []);
        var items = sortedCopy(matchProductSearch(scoped), productSort);
        paintSortMarks();
        paintHeadSummary(items);
        if (!(productData.items && productData.items.length)) {
            body.innerHTML = '<tr><td colspan="' + span + '" class="empty">' + Dash.escapeHtml(productEmptyText(productData)) + "</td></tr>";
            return;
        }
        if (!scoped.length) {
            body.innerHTML = '<tr><td colspan="' + span + '" class="empty">没有符合当前筛选的数据</td></tr>';
            return;
        }
        if (!items.length) {
            body.innerHTML = '<tr><td colspan="' + span + '" class="empty">没有符合搜索的商品</td></tr>';
            return;
        }
        if (live) {
            var stale = (productData.items || []).every(function (item) {
                return !item.product_id && !item.product_name && !item.sync_error;
            });
            if (stale) {
                body.innerHTML = '<tr><td colspan="' + span + '" class="empty">当前缓存还是店铺汇总，点查询重新拉取商品</td></tr>';
                return;
            }
            body.innerHTML = items.map(function (item) {
                var product = '<td class="center-product-name">' + Dash.escapeHtml(item.product_name || "—");
                if (item.product_id) {
                    product += '<div class="center-home-id">ID ' + Dash.escapeHtml(item.product_id) + "</div>";
                }
                product += "</td>";
                var shop = '<td class="center-shop-name">' + Dash.escapeHtml(item.name || "未命名店铺")
                    + '<div class="center-home-id">' + Dash.escapeHtml(item.shop_id || "") + "</div></td>";
                var charge = "<td>" + Dash.escapeHtml(item.charge_name || "—") + "</td>";
                var state = shopStateCell(item);
                if (item.sync_error) {
                    return "<tr>" + product + shop + charge + state + '<td colspan="16" class="center-error">' + Dash.escapeHtml(item.sync_error) + "</td></tr>";
                }
                return "<tr>" + product + shop + charge + state + liveMetricCells(item) + "</tr>";
            }).join("");
            return;
        }
        body.innerHTML = items.map(function (item) {
            var product = '<td class="center-product-name">' + Dash.escapeHtml(item.product_name || "—");
            if (item.product_id) {
                product += '<div class="center-home-id">ID ' + Dash.escapeHtml(item.product_id) + "</div>";
            }
            product += "</td>";
            var shop = '<td class="center-shop-name">' + Dash.escapeHtml(item.name || "未命名店铺")
                + '<div class="center-home-id">' + Dash.escapeHtml(item.shop_id || "") + "</div></td>";
            var charge = "<td>" + Dash.escapeHtml(item.charge_name || "—") + "</td>";
            var state = shopStateCell(item);
            if (item.sync_error) {
                var errorText = item.sync_error.indexOf("频繁") >= 0 ? "暂无商品数据" : item.sync_error;
                return "<tr>" + product + shop + charge + state + '<td colspan="5" class="center-error">' + Dash.escapeHtml(errorText) + "</td></tr>";
            }
            return "<tr>" + product + shop + charge + state
                + '<td><div class="center-shop-value">' + Dash.escapeHtml(item.pay_amount || "—") + "</div></td>"
                + '<td><div class="center-shop-value">' + Dash.escapeHtml(item.order_count || "—") + "</div></td>"
                + '<td><div class="center-shop-value">' + Dash.escapeHtml(item.refund_rate || "—") + "</div></td>"
                + '<td><div class="center-shop-value">' + Dash.escapeHtml(item.refund_amount || "—") + "</div></td>"
                + '<td><div class="center-shop-value">' + Dash.escapeHtml(item.good_rate || "—") + "</div></td>"
                + "</tr>";
        }).join("");
    }

    function paintProductProgress(state) {
        var box = el("center-products-progress");
        var running = !!(state && state.running && centerVisible() && tab === "products");
        if (tab === "products") {
            el("btn-refresh-center").disabled = running;
            el("btn-center-query").disabled = running;
        }
        if (!running) {
            box.classList.add("hidden");
            el("center-products-bar").style.width = "0";
            return;
        }
        box.classList.remove("hidden");
        var total = state.total || 0;
        var done = state.done || 0;
        el("center-products-bar").style.width = (total ? Math.round(done / total * 100) : 8) + "%";
        el("center-products-progress-text").textContent = total
            ? ((preset() === "realtime" ? "正在拉取实时商品 " : "正在拉取商品明细 ") + done + " / " + total + " 家")
            : "正在拉取…";
    }

    function watchProducts() {
        if (!centerVisible() || tab !== "products") {
            productTimer = null;
            el("center-products-progress").classList.add("hidden");
            if (tab !== "overview" && tab !== "shops") {
                el("btn-refresh-center").disabled = false;
                el("btn-center-query").disabled = false;
            }
            return;
        }
        Dash.request("/center/products/progress").then(function (state) {
            paintProductProgress(state);
            if (state.running) {
                productTimer = setTimeout(watchProducts, 2000);
                return;
            }
            productTimer = null;
            if (state.failed) Dash.showToast(state.message || "有店铺没有拉到", "error");
            queryProducts();
        }).catch(function () {
            productTimer = setTimeout(watchProducts, 2000);
        });
    }

    function queryProducts() {
        var query = productQuery();
        Dash.request(query).then(function (data) {
            data._query = query;
            productData = data;
            paintSchedule(data);
            paintProducts();
            paintShellFilters();
        }).catch(function (err) {
            Dash.showToast(err.message || "加载失败", "error");
        });
    }

    function refreshProducts() {
        var mode = preset();
        if (mode !== "realtime") {
            productData = null;
            ensureProducts();
            return;
        }
        var body = { preset: mode, start: "", end: "" };
        if (mode === "custom") {
            body.start = el("center-start").value;
            body.end = el("center-end").value;
            if (!body.start || !body.end) {
                Dash.showToast("请选择开始和结束日期", "error");
                return;
            }
        }
        paintShellFilters();
        el("center-products-tbody").innerHTML = "";
        Dash.request("/center/products/refresh", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        }).then(function (state) {
            paintProductProgress(state);
            if (productTimer) return;
            watchProducts();
        }).catch(function (err) {
            Dash.showToast(err.message || "拉取失败", "error");
        });
    }

    function ensureProducts() {
        var query = productQuery();
        if (!query) {
            el("center-products-tbody").innerHTML = '<tr><td colspan="9" class="empty">请选择开始和结束日期</td></tr>';
            return;
        }
        if (productData && productData._query === query) {
            paintProducts();
            return;
        }
        Dash.request(query).then(function (data) {
            data._query = query;
            productData = data;
            paintSchedule(data);
            paintProducts();
            paintShellFilters();
        }).catch(function (err) {
            el("center-products-tbody").innerHTML = '<tr><td colspan="9" class="empty">' + Dash.escapeHtml((err && err.message) || "加载失败") + "</td></tr>";
        });
    }

    function rememberToolbar() {
        if (tab === "products") {
            productPresetValue = preset();
            productStartValue = el("center-start").value;
            productEndValue = el("center-end").value;
            return;
        }
        shellPreset = preset();
        shellStart = el("center-start").value;
        shellEnd = el("center-end").value;
    }

    function setPresetOptions(kind) {
        var select = el("center-preset");
        var options = kind === "products" ? PRODUCT_OPTIONS : SHELL_OPTIONS;
        var want = kind === "products" ? productPresetValue : shellPreset;
        select.innerHTML = options.map(function (pair) {
            return '<option value="' + pair[0] + '">' + pair[1] + "</option>";
        }).join("");
        var known = options.some(function (pair) { return pair[0] === want; });
        select.value = known ? want : (kind === "products" ? "7" : "realtime");
    }

    function onProductDateChange() {
        var mode = preset();
        paintShellFilters();
        if (mode !== "custom") return;
        if (!el("center-start").value || !el("center-end").value) return;
        productData = null;
        ensureProducts();
    }

    function ensureShops() {
        if (sameSpan(shopData) && (shopData.items || []).length) {
            paintShops();
            return;
        }
        var path = rangeQuery();
        if (!path) return;
        Dash.request(path.replace("/center/overview", "/center/shops")).then(function (data) {
            shopData = data;
            paintSchedule(data);
            if ((data.items || []).length) {
                paintShops();
                return;
            }
            el("center-shops-tbody").innerHTML = '<tr><td colspan="19" class="empty">正在准备，启动后会先更新一次</td></tr>';
        }).catch(function () {
            el("center-shops-tbody").innerHTML = '<tr><td colspan="19" class="empty">正在准备，启动后会先更新一次</td></tr>';
        });
    }

    function showTab(name) {
        rememberToolbar();
        if (tab === "home" && name !== "home") {
            stopHomePretend();
            if (homeTimer) {
                clearTimeout(homeTimer);
                homeTimer = null;
            }
            el("center-home-progress").classList.add("hidden");
            el("center-home-bar").style.width = "0";
            el("btn-refresh-center").disabled = false;
            el("btn-center-query").disabled = false;
        }
        tab = name || "overview";
        document.querySelectorAll("[data-center-tab]").forEach(function (btn) {
            btn.classList.toggle("active", btn.getAttribute("data-center-tab") === tab);
        });
        document.querySelectorAll("[data-center-pane]").forEach(function (pane) {
            pane.classList.toggle("hidden", pane.getAttribute("data-center-pane") !== tab);
        });
        if (tab === "products") {
            setPresetOptions("products");
            if (productStartValue) el("center-start").value = productStartValue;
            if (productEndValue) el("center-end").value = productEndValue;
        } else {
            setPresetOptions("shell");
            if (shellStart) el("center-start").value = shellStart;
            if (shellEnd) el("center-end").value = shellEnd;
        }
        fillPresetDates(false);
        el("view-center").classList.toggle("center-on-overview", tab === "overview");
        el("view-center").classList.toggle("center-has-summary", tab === "home" || tab === "shops" || tab === "products");
        paintSchedule();
        paintShellFilters();
        if (tab === "overview") ensureOverview();
        if (tab === "home") {
            if (homeData) paintHome();
            resumeHome();
        }
        if (tab === "shops") ensureShops();
        if (tab === "products") ensureProducts();
    }

    function dealDayText(offset) {
        var day = new Date();
        day.setHours(12, 0, 0, 0);
        day.setDate(day.getDate() - offset);
        var month = day.getMonth() + 1;
        var date = day.getDate();
        return day.getFullYear() + "-" + (month < 10 ? "0" : "") + month + "-" + (date < 10 ? "0" : "") + date;
    }

    function markDealSpan() {
        document.querySelectorAll("[data-deal-span]").forEach(function (btn) {
            btn.classList.toggle("btn-primary", btn.getAttribute("data-deal-span") === String(dealSpan));
        });
    }

    function setDealRange(days) {
        dealSpan = days;
        var end = el("deal-end");
        var start = el("deal-start");
        if (!end || !start) return;
        var yesterday = dealDayText(1);
        end.max = yesterday;
        start.max = yesterday;
        end.value = yesterday;
        start.value = dealDayText(days);
        markDealSpan();
    }

    function syncDealSpan() {
        var start = el("deal-start").value;
        var end = el("deal-end").value;
        var yesterday = dealDayText(1);
        dealSpan = 0;
        if (end === yesterday && start === dealDayText(7)) dealSpan = 7;
        if (end === yesterday && start === dealDayText(30)) dealSpan = 30;
        markDealSpan();
    }

    function dealMoney(fen, axis) {
        var yuan = (Number(fen) || 0) / 100;
        var sign = yuan < 0 ? "-" : "";
        var abs = Math.abs(yuan);
        var text = axis && (abs >= 100 || Math.abs(abs - Math.round(abs)) < 0.001)
            ? String(Math.round(abs))
            : abs.toFixed(2);
        var parts = text.split(".");
        parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
        return sign + "¥" + parts.join(".");
    }

    function dealCount(value) {
        return String(Math.round(Number(value) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    }

    function dealShown(value, meta) {
        if (meta && meta.rate) {
            return ((Number(value) || 0) / 100).toFixed(2) + "%";
        }
        return meta && meta.money ? dealMoney(value, false) : dealCount(value);
    }

    function cardText(shops, meta) {
        return dealShown(periodTotal(shops, meta.key), meta);
    }

    function metricMeta(key) {
        for (var i = 0; i < DEAL_METRICS.length; i++) {
            if (DEAL_METRICS[i].key === key) return DEAL_METRICS[i];
        }
        return DEAL_METRICS[0];
    }

    function selectedDealShops(group) {
        var wanted = el("deal-charge") ? el("deal-charge").value : "";
        var state = el("center-state") ? el("center-state").value : "";
        return ((group && group.shops) || []).filter(function (shop) {
            return matchesCharge(shop.charge_name, wanted) && matchesState(shop.shop_state, state);
        });
    }

    function dayCount() {
        return ((dealData && dealData.days) || []).length;
    }

    function seriesOf(shops, key) {
        var count = dayCount();
        var totals = [];
        for (var index = 0; index < count; index++) {
            if (key === "refund_pay_ratio") {
                var refund = 0;
                var pay = 0;
                shops.forEach(function (shop) {
                    refund += Number((shop.refund_pay_gmv || [])[index]) || 0;
                    pay += Number((shop.pay_gmv || [])[index]) || 0;
                });
                totals.push(pay ? Math.round(refund / pay * 10000) : 0);
            } else {
                var total = 0;
                shops.forEach(function (shop) {
                    total += Number((shop[key] || [])[index]) || 0;
                });
                totals.push(total);
            }
        }
        return totals;
    }

    function periodTotal(shops, key) {
        if (key === "refund_pay_ratio") {
            var refund = 0;
            var pay = 0;
            shops.forEach(function (shop) {
                (shop.refund_pay_gmv || []).forEach(function (value) { refund += Number(value) || 0; });
                (shop.pay_gmv || []).forEach(function (value) { pay += Number(value) || 0; });
            });
            return pay ? Math.round(refund / pay * 10000) : 0;
        }
        var total = 0;
        shops.forEach(function (shop) {
            (shop[key] || []).forEach(function (value) { total += Number(value) || 0; });
        });
        return total;
    }

    function dealMode(meta) {
        if (meta && meta.rate) return "rate";
        return meta && meta.money ? "money" : "count";
    }

    function dealAxis(maxValue, mode) {
        var scaled = mode === "count" ? 1 : 100;
        var raw = Math.max(0, maxValue) / scaled;
        if (raw <= 0) raw = 1;
        var rough = raw / 4;
        var pow = Math.pow(10, Math.floor(Math.log10(rough)));
        var stepUnit = rough / pow;
        var nice = stepUnit <= 1 ? 1 : stepUnit <= 2 ? 2 : stepUnit <= 2.5 ? 2.5 : stepUnit <= 5 ? 5 : 10;
        var step = nice * pow;
        var top = Math.ceil((raw / step) - 1e-9) * step;
        if (top < raw) top += step;
        var ticks = [];
        var cursor = 0;
        while (cursor <= top + step * 0.01 && ticks.length < 8) {
            ticks.push(mode === "count" ? cursor : Math.round(cursor * scaled));
            cursor += step;
        }
        return { ticks: ticks, max: mode === "count" ? top : top * scaled };
    }

    function dealCurve(points) {
        if (!points.length) return "";
        if (points.length === 1) return "M " + points[0].x + " " + points[0].y;
        var path = "M " + points[0].x + " " + points[0].y;
        for (var index = 0; index < points.length - 1; index += 1) {
            var current = points[index];
            var next = points[index + 1];
            var previous = points[index - 1] || current;
            var after = points[index + 2] || next;
            var cp1x = current.x + (next.x - previous.x) / 6;
            var cp1y = current.y + (next.y - previous.y) / 6;
            var cp2x = next.x - (after.x - current.x) / 6;
            var cp2y = next.y - (after.y - current.y) / 6;
            path += " C " + cp1x.toFixed(1) + " " + cp1y.toFixed(1) + ", " + cp2x.toFixed(1) + " " + cp2y.toFixed(1) + ", " + next.x + " " + next.y;
        }
        return path;
    }

    function dealTick(value, mode) {
        if (mode === "money") return dealMoney(value, true);
        if (mode === "rate") return ((Number(value) || 0) / 100).toFixed(2) + "%";
        return dealCount(value);
    }

    function dealChart(labels, values, meta) {
        dealChartSeq += 1;
        var width = 760;
        var height = 180;
        var left = 104;
        var right = 20;
        var top = 12;
        var bottom = 28;
        var mode = dealMode(meta);
        var metricLabel = meta && meta.label;
        var nums = values.map(function (value) { return Number(value) || 0; });
        if (!nums.length) return "";
        var peak = 0;
        nums.forEach(function (value) { if (value > peak) peak = value; });
        var axis = dealAxis(peak, mode);
        var max = axis.max || (mode === "count" ? 1 : 100);
        var innerW = width - left - right;
        var innerH = height - top - bottom;
        var baseline = top + innerH;
        var step = nums.length > 1 ? innerW / (nums.length - 1) : 0;
        var points = nums.map(function (value, index) {
            return {
                x: (left + (nums.length === 1 ? innerW / 2 : step * index)).toFixed(1),
                y: (top + innerH - (value / max) * innerH).toFixed(1)
            };
        });
        var numeric = points.map(function (point) { return { x: Number(point.x), y: Number(point.y) }; });
        var line = dealCurve(numeric);
        var area = line + " L " + numeric[numeric.length - 1].x + " " + baseline + " L " + numeric[0].x + " " + baseline + " Z";
        var fillId = "deal-fill-" + dealChartSeq;
        var grid = axis.ticks.map(function (value) {
            var y = top + innerH - (value / max) * innerH;
            var shown = dealTick(value, mode);
            return '<line x1="' + left + '" y1="' + y.toFixed(1) + '" x2="' + (width - right) + '" y2="' + y.toFixed(1) + '" stroke="#eef2f6"/>' +
                '<text x="' + (left - 8) + '" y="' + (y + 4).toFixed(1) + '" text-anchor="end" font-size="12" fill="#94a3b8">' + Dash.escapeHtml(shown) + "</text>";
        }).join("");
        var labelEvery = labels.length > 10 ? Math.ceil(labels.length / 7) : 1;
        var xText = labels.map(function (label, index) {
            if (index % labelEvery !== 0 && index !== labels.length - 1) return "";
            var shown = String(label).replace(/-/g, "/");
            if (labels.length > 10) shown = shown.slice(5);
            return '<text x="' + numeric[index].x + '" y="' + (height - 8) + '" text-anchor="middle" font-size="12" fill="#94a3b8">' + Dash.escapeHtml(shown) + "</text>";
        }).join("");
        var hits = numeric.map(function (point, index) {
            var slot = nums.length > 1 ? step : innerW;
            var x = point.x - slot / 2;
            var dateText = String(labels[index] || "").replace(/-/g, "/");
            return '<g class="deal-point">' +
                '<rect class="deal-hit" data-i="' + index + '" x="' + x.toFixed(1) + '" y="' + top + '" width="' + Math.max(slot, 8).toFixed(1) + '" height="' + innerH + '" fill="transparent"' +
                ' data-date="' + Dash.escapeHtml(dateText) + '" data-name="' + Dash.escapeHtml(metricLabel || "") + '" data-value="' + Dash.escapeHtml(dealShown(nums[index], meta)) + '"' +
                ' data-x="' + point.x + '" data-y="' + point.y + '"></rect>' +
                '<circle class="deal-dot" data-i="' + index + '" cx="' + point.x + '" cy="' + point.y + '" r="4.5" fill="#fff" stroke="#2ec9be" stroke-width="2"></circle>' +
                "</g>";
        }).join("");
        return '<div class="deal-chart-wrap"><div class="deal-legend"><span class="deal-tip-dot"></span>' + Dash.escapeHtml(metricLabel || "") + '</div><svg class="deal-chart" style="aspect-ratio:' + width + " / " + height + '" viewBox="0 0 ' + width + " " + height + '" role="img">' +
            "<defs><linearGradient id=\"" + fillId + '" x1="0" y1="0" x2="0" y2="1">' +
            '<stop offset="0%" stop-color="#7ee8dc" stop-opacity="0.55"/>' +
            '<stop offset="100%" stop-color="#7ee8dc" stop-opacity="0.02"/>' +
            "</linearGradient></defs>" +
            grid +
            '<path d="' + area + '" fill="url(#' + fillId + ')" stroke="none"></path>' +
            '<path d="' + line + '" fill="none" stroke="#2ec9be" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"></path>' +
            '<line class="deal-guide" x1="-10" y1="' + top + '" x2="-10" y2="' + baseline + '" stroke="#dbe4ee" stroke-dasharray="3 3"></line>' +
            xText + hits + "</svg>" +
            '<div class="deal-tip" hidden></div></div>';
    }

    function hideDealTip(wrap) {
        if (!wrap) return;
        var tip = wrap.querySelector(".deal-tip");
        if (tip) tip.hidden = true;
        wrap.querySelectorAll(".deal-dot").forEach(function (dot) { dot.classList.remove("is-on"); });
        var guide = wrap.querySelector(".deal-guide");
        if (guide) {
            guide.setAttribute("x1", "-10");
            guide.setAttribute("x2", "-10");
        }
    }

    function showDealTip(wrap, hit) {
        var tip = wrap.querySelector(".deal-tip");
        var svg = wrap.querySelector("svg");
        if (!tip || !svg) return;
        tip.innerHTML = '<div class="deal-tip-date">' + Dash.escapeHtml(hit.getAttribute("data-date") || "") + "</div>" +
            '<div class="deal-tip-row"><span class="deal-tip-dot"></span><span>' + Dash.escapeHtml(hit.getAttribute("data-name") || "") +
            '</span><span class="deal-tip-value">' + Dash.escapeHtml(hit.getAttribute("data-value") || "") + "</span></div>";
        tip.hidden = false;
        var index = hit.getAttribute("data-i");
        wrap.querySelectorAll(".deal-dot").forEach(function (dot) {
            dot.classList.toggle("is-on", dot.getAttribute("data-i") === index);
        });
        var guide = wrap.querySelector(".deal-guide");
        if (guide) {
            guide.setAttribute("x1", hit.getAttribute("data-x"));
            guide.setAttribute("x2", hit.getAttribute("data-x"));
        }
        var box = svg.getBoundingClientRect();
        var view = svg.viewBox.baseVal;
        var px = (Number(hit.getAttribute("data-x")) / view.width) * box.width;
        var py = (Number(hit.getAttribute("data-y")) / view.height) * box.height;
        var width = tip.offsetWidth || 148;
        tip.style.left = Math.max(8, Math.min(px - width / 2, box.width - width - 8)) + "px";
        tip.style.top = Math.max(8, py - tip.offsetHeight - 12) + "px";
    }

    function drawDealChart(index) {
        var group = (dealData.groups || [])[index];
        var block = document.querySelector('[data-deal-index="' + index + '"]');
        if (!group || !block) return;
        var key = dealMetric[group.account] || "pay_gmv";
        var meta = metricMeta(key);
        var slot = block.querySelector(".deal-chart-slot");
        slot.innerHTML = dealChart(dealData.days || [], seriesOf(selectedDealShops(group), key), meta);
    }

    function shopTitle(shops) {
        var names = [];
        (shops || []).forEach(function (shop) {
            var name = shop.name || "";
            if (!name) return;
            var who = shop.charge_name ? ("（" + shop.charge_name + "）") : "（未分配）";
            var label = name + who;
            if (names.indexOf(label) < 0) names.push(label);
        });
        return names.join("、") || "未命名店铺";
    }

    function updateDealBlock(index) {
        var group = (dealData.groups || [])[index];
        var block = document.querySelector('[data-deal-index="' + index + '"]');
        if (!group || !block) return;
        var shops = selectedDealShops(group);
        var title = block.querySelector(".deal-block-title");
        if (title) title.textContent = shopTitle(shops);
        var current = dealMetric[group.account] || "pay_gmv";
        block.querySelectorAll("[data-deal-metric]").forEach(function (card) {
            var key = card.getAttribute("data-deal-metric");
            card.classList.toggle("is-on", key === current);
            var value = card.querySelector(".deal-card-value");
            if (value) value.textContent = cardText(shops, metricMeta(key));
        });
        drawDealChart(index);
    }

    var dealPickIndex = 0;

    function dealAccountLabel(account) {
        var groups = (dealData && dealData.groups) || [];
        for (var i = 0; i < groups.length; i++) {
            if (groups[i].account === account) return shopTitle(groups[i].shops);
        }
        return "";
    }

    function syncDealPickInput() {
        var input = el("deal-shop-q");
        if (!input) return;
        input.value = dealAccount ? dealAccountLabel(dealAccount) : "";
    }

    function dealPickChoices(query) {
        var text = (query || "").trim().toLowerCase();
        var items = [{ account: "", label: "全部店铺" }];
        ((dealData && dealData.groups) || []).forEach(function (group) {
            var rows = selectedDealShops(group);
            if (!rows.length) return;
            items.push({ account: group.account || "", label: shopTitle(rows) });
        });
        if (!text) return items;
        return items.filter(function (item) {
            return item.account && item.label.toLowerCase().indexOf(text) >= 0;
        });
    }

    function closeDealPick() {
        var menu = el("deal-pick-menu");
        if (menu) menu.classList.add("hidden");
    }

    function openDealPick(query) {
        var menu = el("deal-pick-menu");
        if (!menu) return;
        var items = dealPickChoices(query);
        dealPickIndex = 0;
        if (!items.length) {
            menu.innerHTML = '<p class="deal-pick-empty">没有匹配的店铺</p>';
        } else {
            menu.innerHTML = items.map(function (item, index) {
                var on = item.account === (dealAccount || "") ? " is-on" : "";
                return '<button type="button" class="deal-pick-item' + on + '" data-pick-account="' + Dash.escapeHtml(item.account) + '" data-pick-i="' + index + '">' + Dash.escapeHtml(item.label) + "</button>";
            }).join("");
        }
        menu.classList.remove("hidden");
    }

    function chooseDealAccount(account) {
        var groups = (dealData && dealData.groups) || [];
        var still = !account || groups.some(function (group) { return group.account === account; });
        dealAccount = still ? (account || "") : "";
        syncDealPickInput();
        closeDealPick();
        if (dealData) paintDeals();
    }

    function fillDealAccounts() {
        var groups = (dealData && dealData.groups) || [];
        var still = groups.some(function (group) { return group.account === dealAccount; });
        if (!still) dealAccount = "";
        syncDealPickInput();
        closeDealPick();
    }

    function visibleDealGroups() {
        var groups = (dealData && dealData.groups) || [];
        return groups.map(function (group, index) {
            return { group: group, index: index };
        }).filter(function (item) {
            if (dealAccount && item.group.account !== dealAccount) return false;
            return selectedDealShops(item.group).length > 0;
        });
    }

    function paintDeals() {
        var board = el("deal-board");
        var shown = visibleDealGroups();
        if (!shown.length) {
            var narrowed = (el("deal-charge") && el("deal-charge").value) || (el("center-state") && el("center-state").value);
            var empty = narrowed
                ? "没有符合当前筛选的店铺"
                : "还没有可以按抖音号汇总的店铺";
            board.innerHTML = '<p class="empty">' + empty + "</p>";
            return;
        }
        board.innerHTML = shown.map(function (item) {
            var group = item.group;
            var index = item.index;
            var shops = selectedDealShops(group);
            var current = dealMetric[group.account] || "pay_gmv";
            var cards = DEAL_METRICS.map(function (meta) {
                return '<button type="button" class="deal-card' + (meta.key === current ? " is-on" : "") + '" data-deal-metric="' + meta.key + '">' +
                    '<span class="deal-card-label">' + Dash.escapeHtml(meta.label) + "</span>" +
                    '<span class="deal-card-value">' + Dash.escapeHtml(cardText(shops, meta)) + "</span></button>";
            }).join("");
            return '<section class="deal-block" data-deal-index="' + index + '">' +
                '<div class="deal-head"><h3 class="deal-block-title">' + Dash.escapeHtml(shopTitle(shops)) + "</h3></div>" +
                '<div class="deal-cards">' + cards + "</div>" +
                '<div class="deal-chart-slot"></div></section>';
        }).join("");
        shown.forEach(function (item) { drawDealChart(item.index); });
    }

    function loadDeals() {
        var startInput = el("deal-start");
        var endInput = el("deal-end");
        if (!startInput || !endInput) return;
        if (!startInput.value || !endInput.value) setDealRange(dealSpan || 7);
        var yesterday = dealDayText(1);
        startInput.max = yesterday;
        endInput.max = yesterday;
        var start = startInput.value;
        var end = endInput.value;
        if (end > yesterday) {
            Dash.showToast("结束日期不能选今天", "error");
            return;
        }
        var span = Math.round((Date.parse(end) - Date.parse(start)) / 86400000) + 1;
        if (!(span >= 1)) {
            Dash.showToast("开始日期不能晚于结束日期", "error");
            return;
        }
        if (span > 93) {
            Dash.showToast("一次最多选择 93 天", "error");
            return;
        }
        var board = el("deal-board");
        board.innerHTML = '<p class="empty">正在读取…</p>';
        Dash.request("/center/deals?start=" + encodeURIComponent(start) + "&end=" + encodeURIComponent(end)).then(function (data) {
            dealData = data || { groups: [], days: [] };
            if (data && data.start) startInput.value = data.start;
            if (data && data.end) endInput.value = data.end;
            syncDealSpan();
            fillDealAccounts();
            var dealShops = [];
            ((dealData && dealData.groups) || []).forEach(function (group) {
                (group.shops || []).forEach(function (shop) { dealShops.push(shop); });
            });
            fillCharges("deal-charge", dealShops);
            paintDeals();
        }).catch(function (err) {
            board.innerHTML = '<p class="empty">' + Dash.escapeHtml((err && err.message) || "加载失败") + "</p>";
        });
    }

    function ensureOverview() {
        loadDeals();
    }

    function loadShops() {
        return Dash.request("/stores").then(function (data) {
            shops = data.items || [];
            fillAccounts();
            fillCharges("center-charge", shops);
            fillShops();
            if (latest) paint(latest);
            else paintShellFilters();
            if (homeData) paintHome();
            if (shopData) paintShops();
            if (productData) paintProducts();
        }).catch(function () {
            paintShellFilters();
        });
    }

    function bind() {
        el("center-preset").addEventListener("change", function () {
            fillPresetDates(true);
            paintShellFilters();
            if (tab === "products") {
                productData = null;
                ensureProducts();
                return;
            }
            latest = null;
            shopData = null;
            if (tab === "overview") ensureOverview();
            if (tab === "shops") ensureShops();
            if (tab === "shops") ensureShops();
        });
        el("center-start").addEventListener("change", function () {
            if (tab === "products") {
                onProductDateChange();
                return;
            }
            paintShellFilters();
            if (preset() !== "custom") return;
            latest = null;
            shopData = null;
            if (tab === "shops") refreshShops();
        });
        el("center-end").addEventListener("change", function () {
            if (tab === "products") {
                onProductDateChange();
                return;
            }
            paintShellFilters();
            if (preset() !== "custom") return;
            latest = null;
            shopData = null;
            if (tab === "overview") refresh();
            if (tab === "shops") refreshShops();
            if (tab === "shops") refreshShops();
        });
        el("center-account").addEventListener("change", function () {
            fillShops();
            paintShellFilters();
            if (latest) paint(latest);
            paintHome();
            if (shopData) paintShops();
            if (productData) paintProducts();
        });
        el("center-charge").addEventListener("change", function () {
            fillShops();
            paintShellFilters();
            if (latest) paint(latest);
            paintHome();
            if (shopData) paintShops();
            if (productData) paintProducts();
        });
        el("center-state").addEventListener("change", function () {
            fillShops();
            paintShellFilters();
            if (latest) paint(latest);
            paintHome();
            if (shopData) paintShops();
            if (productData) paintProducts();
            if (dealData) paintDeals();
        });
        el("deal-charge").addEventListener("change", function () {
            if (dealAccount) {
                var groups = (dealData && dealData.groups) || [];
                var still = groups.some(function (group) {
                    return group.account === dealAccount && selectedDealShops(group).length > 0;
                });
                if (!still) dealAccount = "";
                syncDealPickInput();
                closeDealPick();
            }
            if (dealData) paintDeals();
        });
        el("center-shop").addEventListener("change", function () {
            paintShellFilters();
            if (latest) paint(latest);
            paintHome();
            if (shopData) paintShops();
            if (productData) paintProducts();
        });
        el("center-product-q").addEventListener("input", function () {
            if (productData) paintProducts();
        });
        el("btn-refresh-center").addEventListener("click", function () {
            if (tab === "overview") return;
            if (tab === "home") refreshHome();
            else if (tab === "shops") refreshShops();
            else if (tab === "products") refreshProducts();
        });
        el("btn-center-query").addEventListener("click", function () {
            if (tab === "overview") return;
            if (tab === "shops") refreshShops();
        });
        el("center-shops-tbody").addEventListener("click", function (event) {
            var button = event.target.closest("[data-live-shop]");
            if (!button) return;
            var storePk = button.getAttribute("data-live-shop") || "";
            if (!storePk) return;
            openLive(storePk, button.getAttribute("data-live-name"));
        });
        el("live-close").addEventListener("click", closeLive);
        el("center-pane-home").addEventListener("click", function (event) {
            var button = event.target.closest("[data-vio-shop]");
            if (!button || !window.Vio || !window.Vio.openShop) return;
            window.Vio.openShop(button.getAttribute("data-vio-shop"), button.getAttribute("data-vio-ids"));
        });
        document.querySelectorAll("[data-center-tab]").forEach(function (btn) {
            btn.addEventListener("click", function () {
                showTab(btn.getAttribute("data-center-tab"));
            });
        });
        el("deal-shop-q").addEventListener("focus", function () {
            el("deal-shop-q").select();
            openDealPick("");
        });
        el("deal-shop-q").addEventListener("input", function () {
            openDealPick(el("deal-shop-q").value);
        });
        el("deal-shop-q").addEventListener("keydown", function (event) {
            var menu = el("deal-pick-menu");
            var buttons = menu ? menu.querySelectorAll(".deal-pick-item") : [];
            if (event.key === "Escape") {
                syncDealPickInput();
                closeDealPick();
                return;
            }
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                if (!buttons.length || menu.classList.contains("hidden")) openDealPick(el("deal-shop-q").value);
                buttons = el("deal-pick-menu").querySelectorAll(".deal-pick-item");
                if (!buttons.length) return;
                dealPickIndex += event.key === "ArrowDown" ? 1 : -1;
                if (dealPickIndex < 0) dealPickIndex = buttons.length - 1;
                if (dealPickIndex >= buttons.length) dealPickIndex = 0;
                buttons.forEach(function (btn, index) { btn.classList.toggle("is-hot", index === dealPickIndex); });
                buttons[dealPickIndex].scrollIntoView({ block: "nearest" });
                return;
            }
            if (event.key === "Enter") {
                event.preventDefault();
                if (!buttons.length || menu.classList.contains("hidden")) {
                    chooseDealAccount("");
                    return;
                }
                var hot = buttons[dealPickIndex] || buttons[0];
                chooseDealAccount(hot.getAttribute("data-pick-account") || "");
            }
        });
        el("deal-pick").addEventListener("mousedown", function (event) {
            var button = event.target.closest(".deal-pick-item");
            if (!button) return;
            event.preventDefault();
            chooseDealAccount(button.getAttribute("data-pick-account") || "");
        });
        document.addEventListener("mousedown", function (event) {
            var pick = el("deal-pick");
            if (!pick || pick.contains(event.target)) return;
            syncDealPickInput();
            closeDealPick();
        });
        document.querySelectorAll("[data-deal-span]").forEach(function (btn) {
            btn.addEventListener("click", function () {
                setDealRange(Number(btn.getAttribute("data-deal-span")) || 7);
                loadDeals();
            });
        });
        el("btn-deal-query").addEventListener("click", loadDeals);
        el("deal-start").addEventListener("change", syncDealSpan);
        el("deal-end").addEventListener("change", syncDealSpan);
        el("deal-board").addEventListener("click", function (event) {
            var card = event.target.closest("[data-deal-metric]");
            if (card) {
                var block = card.closest("[data-deal-index]");
                var index = Number(block.getAttribute("data-deal-index"));
                var group = (dealData.groups || [])[index];
                if (!group) return;
                dealMetric[group.account] = card.getAttribute("data-deal-metric");
                updateDealBlock(index);
            }
        });
        el("deal-board").addEventListener("mousemove", function (event) {
            var hit = event.target.closest ? event.target.closest(".deal-hit") : null;
            var wrap = hit && hit.closest ? hit.closest(".deal-chart-wrap") : null;
            el("deal-board").querySelectorAll(".deal-chart-wrap").forEach(function (item) {
                if (item !== wrap) hideDealTip(item);
            });
            if (wrap && hit) showDealTip(wrap, hit);
        });
        el("deal-board").addEventListener("mouseleave", function () {
            el("deal-board").querySelectorAll(".deal-chart-wrap").forEach(hideDealTip);
        });
        document.querySelectorAll(".center-sort").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var table = btn.getAttribute("data-sort-for");
                var key = btn.getAttribute("data-sort-key");
                var state = table === "shops" ? shopSort : table === "home" ? homeSort : productSort;
                if (state.key === key) state.dir = state.dir === "desc" ? "asc" : "desc";
                else {
                    state.key = key;
                    state.dir = "desc";
                }
                if (table === "shops") paintShops();
                else if (table === "home") paintHome();
                else paintProducts();
            });
        });
        bound = true;
    }

    window.Dash.loadCenter = function () {
        if (!bound) bind();
        showTab(tab);
        loadShops();
        loadHome();
        watchSchedule();
    };
})();
