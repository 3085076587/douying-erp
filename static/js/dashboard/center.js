(function () {
    "use strict";

    var timer = null;
    var bound = false;
    var shops = [];
    var latest = null;
    var homeData = null;
    var shopData = null;
    var shopTimer = null;
    var productData = null;
    var productTimer = null;
    var tab = "overview";
    var shopSort = { key: "pay_amount", dir: "desc" };
    var productSort = { key: "pay_amount", dir: "desc" };
    var scheduleTimer = null;
    var scheduleSeen = "";
    var scheduleState = null;
    var dailySeen = null;

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

    function fillPresetDates() {
        var mode = preset();
        var custom = mode === "custom";
        var start = el("center-start");
        var end = el("center-end");
        start.disabled = !custom;
        end.disabled = !custom;
        if (custom) return;
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
        }
        start.value = fmt(from);
        end.value = fmt(to);
    }

    function rangeQuery() {
        var path = "/center/overview?preset=" + encodeURIComponent(preset());
        if (preset() !== "custom") return path;
        var start = el("center-start").value;
        var end = el("center-end").value;
        if (!start || !end) return "";
        return path + "&start=" + encodeURIComponent(start) + "&end=" + encodeURIComponent(end);
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
        return name + (start && end ? "（" + start + " 至 " + end + "）" : "");
    }

    function paintShellFilters() {
        var shopText = "当前筛选：" + accountLabel() + " · " + shopLabel() + " · " + timeLabel();
        el("center-shops-filter").textContent = shopText;
        el("center-products-filter").textContent = "当前筛选：" + accountLabel() + " · " + shopLabel();
    }

    function productQuery() {
        return "/center/products?preset=7";
    }

    function paintSchedule(state) {
        if (state) scheduleState = state;
        var node = el("center-schedule");
        if (!node) return;
        var current = scheduleState;
        var text = "";
        if ((tab === "shops" || tab === "products") && current && current.next_update) {
            text = "每" + (current.interval_minutes || 10) + "分钟更新一次，下次更新 " + current.next_update;
        }
        node.textContent = text;
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
                    if (tab === "overview") query();
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

    function shopsOfAccount() {
        var account = el("center-account").value;
        return shops.filter(function (row) {
            return !account || (row.owner_account || "") === account;
        });
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
        var allowed = {};
        shopsOfAccount().forEach(function (row) { allowed[String(row.shop_id)] = true; });
        return (items || []).filter(function (item) {
            var id = String(item.shop_id || "");
            if (shopId && id !== shopId) return false;
            if (account && shops.length && !allowed[id]) return false;
            return true;
        });
    }

    function productKeyword() {
        var input = el("center-product-q");
        return input ? input.value.trim().toLowerCase() : "";
    }

    function matchProductSearch(items) {
        var keyword = productKeyword();
        if (!keyword) return items;
        return items.filter(function (item) {
            var name = String(item.product_name || "").toLowerCase();
            var id = String(item.product_id || "").toLowerCase();
            return name.indexOf(keyword) >= 0 || id.indexOf(keyword) >= 0;
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
            list.innerHTML = '<p class="empty">没有符合当前账号和店铺的数据</p>';
            return;
        }
        list.innerHTML = items.map(function (item) {
            var head = '<h3>' + Dash.escapeHtml(item.name || "未命名店铺")
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

    function paintHome() {
        if (!homeData) return;
        var body = el("center-home-tbody");
        var items = visibleItems(homeData.items || []);
        if (!(homeData.items && homeData.items.length)) {
            body.innerHTML = '<tr><td colspan="9" class="empty">还没有店铺</td></tr>';
            return;
        }
        var homeCached = !!homeData.updated_at || (homeData.items || []).some(function (item) {
            return item.pending_pay || item.pending_ship || item.abnormal_package
                || item.pending_aftersale || item.service_ticket || item.risk_point
                || item.pending_violation || item.sync_error;
        });
        if (!homeCached) {
            body.innerHTML = '<tr><td colspan="9" class="empty">正在准备，启动后会先更新一次</td></tr>';
            return;
        }
        if (!items.length) {
            body.innerHTML = '<tr><td colspan="9" class="empty">没有符合当前账号和店铺的数据</td></tr>';
            return;
        }
        body.innerHTML = items.map(function (item) {
            var name = "<td>" + Dash.escapeHtml(item.name || "未命名店铺")
                + '<div class="center-home-id">' + Dash.escapeHtml(item.shop_id || "") + "</div></td>";
            var login = "<td>" + loginTag(item.login_status) + "</td>";
            if (item.sync_error) {
                return "<tr>" + name + login + '<td colspan="7" class="center-error">' + Dash.escapeHtml(item.sync_error) + "</td></tr>";
            }
            var hasTickets = Array.isArray(item.pending_violation_ids);
            var ticketIds = hasTickets ? (item.pending_violation_ids.join(",") || "-") : "";
            var violation = '<td><button type="button" class="center-jump" data-vio-shop="'
                + Dash.escapeHtml(String(item.store_pk)) + '"'
                + (hasTickets ? ' data-vio-ids="' + Dash.escapeHtml(ticketIds) + '"' : "")
                + ">" + Dash.escapeHtml(homeNumber(item.pending_violation)) + "</button></td>";
            return "<tr>" + name + login
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
            el("center-home-tbody").innerHTML = '<tr><td colspan="9" class="empty">'
                + Dash.escapeHtml(err.message || "加载失败") + "</td></tr>";
        });
    }

    function metricCell(value) {
        return '<td><div class="center-shop-value">' + Dash.escapeHtml(value || "—") + "</div></td>";
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
            var state = table === "shops" ? shopSort : productSort;
            var mark = state.key === key ? (state.dir === "asc" ? " ↑" : " ↓") : "";
            btn.textContent = btn.getAttribute("data-sort-label") + mark;
        });
    }

    function paintHeadSummary(items) {
        var node = el("center-summary");
        if (!node) return;
        if (tab !== "shops" && tab !== "products") {
            node.innerHTML = "";
            return;
        }
        var fields = [
            ["成交金额", formatMoney(sumField(items || [], "pay_amount"))],
            ["成交订单数", formatCount(sumField(items || [], "order_count"))],
            ["成交退款金额（支付时间）", formatMoney(sumField(items || [], "refund_amount"))]
        ];
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
            body.innerHTML = '<tr><td colspan="6" class="empty">还没有店铺</td></tr>';
            return;
        }
        if (!items.length) {
            body.innerHTML = '<tr><td colspan="6" class="empty">没有符合当前账号和店铺的数据</td></tr>';
            return;
        }
        body.innerHTML = items.map(function (item) {
            var name = "<td>" + Dash.escapeHtml(item.name || "未命名店铺")
                + '<div class="center-home-id">' + Dash.escapeHtml(item.shop_id || "") + "</div></td>";
            var login = "<td>" + loginTag(item.login_status) + "</td>";
            if (item.sync_error) {
                return "<tr>" + name + login + '<td colspan="4" class="center-error">' + Dash.escapeHtml(item.sync_error) + "</td></tr>";
            }
            return "<tr>" + name + login
                + metricCell(item.pay_amount)
                + metricCell(item.order_count)
                + metricCell(item.refund_rate)
                + metricCell(item.refund_amount)
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
            ? ("正在拉取经营概况 " + done + " / " + total + " 家")
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
        var scoped = visibleItems(productData.items || []);
        var items = sortedCopy(matchProductSearch(scoped), productSort);
        paintSortMarks();
        paintHeadSummary(items);
        if (!(productData.items && productData.items.length)) {
            body.innerHTML = '<tr><td colspan="7" class="empty">还没有商品</td></tr>';
            return;
        }
        if (!scoped.length) {
            body.innerHTML = '<tr><td colspan="7" class="empty">没有符合当前账号和店铺的数据</td></tr>';
            return;
        }
        if (!items.length) {
            body.innerHTML = '<tr><td colspan="7" class="empty">没有符合搜索的商品</td></tr>';
            return;
        }
        body.innerHTML = items.map(function (item) {
            var product = '<td class="center-product-name">' + Dash.escapeHtml(item.product_name || "—");
            if (item.product_id) {
                product += '<div class="center-home-id">ID ' + Dash.escapeHtml(item.product_id) + "</div>";
            }
            product += "</td>";
            var shop = "<td>" + Dash.escapeHtml(item.name || "未命名店铺")
                + '<div class="center-home-id">' + Dash.escapeHtml(item.shop_id || "") + "</div></td>";
            if (item.sync_error) {
                return "<tr>" + product + shop + '<td colspan="5" class="center-error">' + Dash.escapeHtml(item.sync_error) + "</td></tr>";
            }
            return "<tr>" + product + shop
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
            ? ("正在拉取商品明细 " + done + " / " + total + " 家")
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
        Dash.request(productQuery()).then(function (data) {
            productData = data;
            paintSchedule(data);
            paintProducts();
        }).catch(function (err) {
            Dash.showToast(err.message || "加载失败", "error");
        });
    }

    function refreshProducts() {
        paintShellFilters();
        el("center-products-tbody").innerHTML = "";
        Dash.request("/center/products/refresh", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ preset: "7", start: "", end: "" }),
        }).then(function (state) {
            paintProductProgress(state);
            if (productTimer) return;
            watchProducts();
        }).catch(function (err) {
            Dash.showToast(err.message || "拉取失败", "error");
        });
    }

    function ensureProducts() {
        if (productData && productData.preset === "7" && (productData.items || []).length) {
            paintProducts();
            return;
        }
        Dash.request(productQuery()).then(function (data) {
            productData = data;
            paintSchedule(data);
            if ((data.items || []).length) {
                paintProducts();
                return;
            }
            el("center-products-tbody").innerHTML = '<tr><td colspan="7" class="empty">正在准备，启动后会先更新一次</td></tr>';
        }).catch(function () {
            el("center-products-tbody").innerHTML = '<tr><td colspan="7" class="empty">正在准备，启动后会先更新一次</td></tr>';
        });
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
            el("center-shops-tbody").innerHTML = '<tr><td colspan="6" class="empty">正在准备，启动后会先更新一次</td></tr>';
        }).catch(function () {
            el("center-shops-tbody").innerHTML = '<tr><td colspan="6" class="empty">正在准备，启动后会先更新一次</td></tr>';
        });
    }

    function showTab(name) {
        tab = name || "overview";
        document.querySelectorAll("[data-center-tab]").forEach(function (btn) {
            btn.classList.toggle("active", btn.getAttribute("data-center-tab") === tab);
        });
        document.querySelectorAll("[data-center-pane]").forEach(function (pane) {
            pane.classList.toggle("hidden", pane.getAttribute("data-center-pane") !== tab);
        });
        el("view-center").classList.toggle("center-no-time", tab === "products");
        el("view-center").classList.toggle("center-has-summary", tab === "shops" || tab === "products");
        paintSchedule();
        paintShellFilters();
        if (tab === "overview") ensureOverview();
        if (tab === "shops") ensureShops();
        if (tab === "products") ensureProducts();
    }

    function ensureOverview() {
        if (latest && (latest.items || []).length) {
            paint(latest);
            return;
        }
        var path = rangeQuery();
        if (!path) return;
        Dash.request(path).then(function (data) {
            if ((data.items || []).length) {
                paint(data);
                return;
            }
            el("center-list").innerHTML = '<p class="empty">正在准备，启动后会先更新一次</p>';
        }).catch(function () {
            el("center-list").innerHTML = '<p class="empty">正在准备，启动后会先更新一次</p>';
        });
    }

    function loadShops() {
        return Dash.request("/stores").then(function (data) {
            shops = data.items || [];
            fillAccounts();
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
            fillPresetDates();
            paintShellFilters();
            latest = null;
            shopData = null;
            if (tab === "overview") ensureOverview();
            if (tab === "shops") ensureShops();
        });
        el("center-start").addEventListener("change", function () {
            paintShellFilters();
            if (preset() !== "custom") return;
            latest = null;
            shopData = null;
            if (tab === "overview") refresh();
            if (tab === "shops") refreshShops();
        });
        el("center-end").addEventListener("change", function () {
            paintShellFilters();
            if (preset() !== "custom") return;
            latest = null;
            shopData = null;
            if (tab === "overview") refresh();
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
            if (tab === "overview") refresh();
            else if (tab === "shops") refreshShops();
            else if (tab === "products") refreshProducts();
            else paintHome();
        });
        el("btn-center-query").addEventListener("click", function () {
            if (tab === "overview") refresh();
            else if (tab === "shops") refreshShops();
            else if (tab === "products") refreshProducts();
            else paintHome();
        });
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
        document.querySelectorAll(".center-sort").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var table = btn.getAttribute("data-sort-for");
                var key = btn.getAttribute("data-sort-key");
                var state = table === "shops" ? shopSort : productSort;
                if (state.key === key) state.dir = state.dir === "desc" ? "asc" : "desc";
                else {
                    state.key = key;
                    state.dir = "desc";
                }
                if (table === "shops") paintShops();
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
