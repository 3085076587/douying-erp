/* 账单赔付：店铺合计和流水详情都读数据库。 */
(function () {
    "use strict";

    var loaded = false;
    var body = document.getElementById("comp-tbody");

    function el(id) {
        return document.getElementById(id);
    }

    function pad(value) {
        return String(value).padStart(2, "0");
    }

    function dayText(date) {
        return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate());
    }

    function applyRange() {
        var range = el("comp-range").value;
        var end = new Date();
        var start = new Date();
        if (range === "7") start.setDate(end.getDate() - 6);
        else if (range === "30") start.setDate(end.getDate() - 29);
        else if (range === "custom") return;
        el("comp-start").value = dayText(start);
        el("comp-end").value = dayText(end);
    }

    function money(fen) {
        var yuan = (Number(fen) / 100).toFixed(2);
        if (yuan.charAt(0) === "-") return "-¥" + yuan.slice(1);
        return "¥" + yuan;
    }

    function moneyCell(fen) {
        var tone = Number(fen) < 0 ? "comp-out" : "";
        return '<span class="' + tone + '">' + Dash.escapeHtml(money(fen)) + "</span>";
    }

    function query() {
        var params = new URLSearchParams();
        if (el("comp-shop").value) params.set("store_pk", el("comp-shop").value);
        if (el("comp-start").value) params.set("start_date", el("comp-start").value);
        if (el("comp-end").value) params.set("end_date", el("comp-end").value);
        body.innerHTML = '<tr><td colspan="3" class="empty">加载中</td></tr>';
        Dash.request("/compensates?" + params.toString()).then(function (data) {
            var items = (data && data.items) || [];
            el("comp-sum").textContent = "当前 " + (data.shop_count || items.length) + " 家，赔付合计 "
                + money(data.total_fen || 0) + "。";
            if (!items.length) {
                body.innerHTML = '<tr><td colspan="3" class="empty">这个时间里没有赔付记录。</td></tr>';
                return;
            }
            body.innerHTML = items.map(function (row) {
                return "<tr><td>" + Dash.escapeHtml(row.shop_name || "—") + "</td><td>"
                    + moneyCell(row.amount_fen) + '</td><td><button type="button" class="comp-detail-btn" data-comp-shop="'
                    + Dash.escapeHtml(row.store_pk) + '" data-comp-name="'
                    + Dash.escapeHtml(row.shop_name || "") + '">详情</button></td></tr>';
            }).join("");
        }).catch(function (err) {
            body.innerHTML = '<tr><td colspan="3" class="empty">' + Dash.escapeHtml(err.message || "加载失败") + "</td></tr>";
        });
    }

    function cell(value) {
        return "<td>" + Dash.escapeHtml(value || "—") + "</td>";
    }

    function openDetail(storePk, name) {
        var params = new URLSearchParams();
        params.set("store_pk", storePk);
        if (el("comp-start").value) params.set("start_date", el("comp-start").value);
        if (el("comp-end").value) params.set("end_date", el("comp-end").value);
        el("comp-detail-title").textContent = (name || "店铺") + " 赔付详情";
        el("comp-detail-note").textContent = "正在读取…";
        el("comp-detail-tbody").innerHTML = '<tr><td colspan="12" class="empty">正在读取…</td></tr>';
        el("comp-detail-mask").classList.remove("hidden");
        Dash.request("/compensates/detail?" + params.toString()).then(function (data) {
            var items = (data && data.items) || [];
            el("comp-detail-note").textContent = "共 " + items.length + " 条，合计 " + money(data.amount_fen || 0);
            if (!items.length) {
                el("comp-detail-tbody").innerHTML = '<tr><td colspan="12" class="empty">这个时间里没有赔付记录。</td></tr>';
                return;
            }
            el("comp-detail-tbody").innerHTML = items.map(function (row) {
                return "<tr>"
                    + cell(row.finished_at)
                    + cell(row.compensate_id)
                    + cell(row.scene)
                    + cell(row.recipient)
                    + cell(row.direction)
                    + "<td>" + moneyCell(row.amount_fen) + "</td>"
                    + cell(row.compensator)
                    + cell(row.account_name)
                    + cell(row.reason)
                    + cell(row.shop_order_id)
                    + cell(row.order_id)
                    + cell(row.aftersale_id)
                    + "</tr>";
            }).join("");
        }).catch(function (err) {
            el("comp-detail-note").textContent = (err && err.message) || "没有读到赔付详情";
            el("comp-detail-tbody").innerHTML = "";
        });
    }

    function loadShops() {
        return Dash.request("/compensates/shops").then(function (data) {
            var select = el("comp-shop");
            var current = select.value;
            var items = (data && data.items) || [];
            select.innerHTML = '<option value="">全部店铺</option>' + items.map(function (shop) {
                return '<option value="' + Dash.escapeHtml(shop.store_pk) + '">' + Dash.escapeHtml(shop.name || shop.store_pk) + "</option>";
            }).join("");
            select.value = current;
        }).catch(function () {});
    }

    function load() {
        if (!loaded) {
            loaded = true;
            applyRange();
            loadShops().then(query);
            return;
        }
        query();
    }

    el("comp-range").addEventListener("change", function () {
        applyRange();
        query();
    });
    function exportFile() {
        var params = new URLSearchParams();
        if (el("comp-shop").value) params.set("store_pk", el("comp-shop").value);
        if (el("comp-start").value) params.set("start_date", el("comp-start").value);
        if (el("comp-end").value) params.set("end_date", el("comp-end").value);
        var button = el("btn-export-compensates");
        button.disabled = true;
        fetch("/api/v1/compensates/export?" + params.toString(), {
            headers: { Authorization: "Bearer " + (localStorage.getItem("erp_access_token") || "") },
        }).then(function (resp) {
            if (resp.status === 401) {
                Dash.showToast("登录已过期，请重新登录", "error");
                return;
            }
            if (!resp.ok) {
                Dash.showToast("导出失败", "error");
                return;
            }
            var name = "账单赔付.xlsx";
            var matched = /filename\*=UTF-8''([^;]+)/i.exec(resp.headers.get("Content-Disposition") || "");
            if (matched) {
                try { name = decodeURIComponent(matched[1]); } catch (err) {}
            }
            return resp.blob().then(function (blob) {
                var url = URL.createObjectURL(blob);
                var link = document.createElement("a");
                link.href = url;
                link.download = name;
                document.body.appendChild(link);
                link.click();
                link.remove();
                URL.revokeObjectURL(url);
            });
        }).catch(function () {
            Dash.showToast("导出失败", "error");
        }).then(function () {
            button.disabled = false;
        });
    }

    el("btn-query-compensates").addEventListener("click", query);
    el("btn-export-compensates").addEventListener("click", exportFile);
    el("comp-shop").addEventListener("change", query);
    body.addEventListener("click", function (event) {
        var button = event.target.closest ? event.target.closest(".comp-detail-btn") : null;
        if (!button) return;
        openDetail(button.getAttribute("data-comp-shop"), button.getAttribute("data-comp-name"));
    });
    el("comp-detail-close").addEventListener("click", function () {
        el("comp-detail-mask").classList.add("hidden");
    });
    el("comp-detail-mask").addEventListener("click", function (event) {
        if (event.target === el("comp-detail-mask")) el("comp-detail-mask").classList.add("hidden");
    });

    window.Dash.loadCompensates = load;
})();
