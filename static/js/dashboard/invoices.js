(function () {
    "use strict";

    var rows = [];
    var pollTimer = null;
    var sort = { key: "", dir: -1 };

    function countText(value) {
        return value === null || value === undefined || value === "" ? "—" : String(value);
    }

    function sortNumber(value) {
        if (value === null || value === undefined || value === "") return null;
        var number = Number(value);
        return isNaN(number) ? null : number;
    }

    function paintSummary(data) {
        document.getElementById("invoice-platform-pending").textContent = countText(data.platform_pending);
        document.getElementById("invoice-consumer-pending").textContent = countText(data.consumer_pending);
        document.getElementById("invoice-updated").textContent = data.updated_at ? ("更新于 " + data.updated_at) : "还没有拉取";
    }

    function filtered() {
        var keyword = (document.getElementById("invoice-search").value || "").trim().toLowerCase();
        var owner = document.getElementById("invoice-owner").value;
        var items = rows.filter(function (row) {
            if (owner && row.owner_account !== owner) return false;
            if (!keyword) return true;
            return String(row.name || "").toLowerCase().indexOf(keyword) >= 0
                || String(row.shop_id || "").indexOf(keyword) >= 0;
        });
        if (!sort.key) return items;
        var key = sort.key;
        var dir = sort.dir;
        items.sort(function (left, right) {
            var a = left.sync_error ? null : sortNumber(left[key]);
            var b = right.sync_error ? null : sortNumber(right[key]);
            if (a == null && b == null) return 0;
            if (a == null) return 1;
            if (b == null) return -1;
            return (a - b) * dir;
        });
        return items;
    }

    function markSort() {
        document.querySelectorAll("#invoice-table th.sortable").forEach(function (cell) {
            cell.classList.remove("sort-asc", "sort-desc");
            if (cell.getAttribute("data-sort") === sort.key) {
                cell.classList.add(sort.dir > 0 ? "sort-asc" : "sort-desc");
            }
        });
    }

    function paint() {
        var body = document.getElementById("invoice-tbody");
        var items = filtered();
        if (!items.length) {
            body.innerHTML = '<tr><td colspan="4" class="empty">没有符合条件的店铺</td></tr>';
            return;
        }
        body.innerHTML = items.map(function (row) {
            var shop = "<td>" + Dash.escapeHtml(row.name || "未命名店铺")
                + '<div class="center-home-id">' + Dash.escapeHtml(row.shop_id || "") + "</div></td>";
            var owner = "<td>" + Dash.escapeHtml(row.owner_account || "—") + "</td>";
            if (row.sync_error) {
                return "<tr>" + shop + owner + '<td colspan="2" class="center-error">' + Dash.escapeHtml(row.sync_error) + "</td></tr>";
            }
            return "<tr>" + shop + owner
                + "<td>" + countText(row.platform_pending) + "</td>"
                + "<td>" + countText(row.consumer_pending) + "</td></tr>";
        }).join("");
    }

    function fillOwners() {
        var select = document.getElementById("invoice-owner");
        var current = select.value;
        var owners = [];
        rows.forEach(function (row) {
            if (row.owner_account && owners.indexOf(row.owner_account) < 0) owners.push(row.owner_account);
        });
        owners.sort();
        select.innerHTML = '<option value="">全部归属账号</option>' + owners.map(function (owner) {
            return '<option value="' + Dash.escapeHtml(owner) + '">' + Dash.escapeHtml(owner) + "</option>";
        }).join("");
        select.value = owners.indexOf(current) >= 0 ? current : "";
    }

    function load() {
        return Dash.request("/invoices").then(function (data) {
            rows = data.items || [];
            paintSummary(data);
            fillOwners();
            paint();
        }).catch(function (err) {
            Dash.showToast(err.message || "发票数量没加载出来", "error");
        });
    }

    function poll() {
        Dash.request("/invoices/progress").then(function (state) {
            var button = document.getElementById("btn-refresh-invoices");
            if (state.running) {
                button.disabled = true;
                button.textContent = "拉取中 " + (state.done || 0) + "/" + (state.total || 0);
                pollTimer = setTimeout(poll, 1000);
                return;
            }
            button.disabled = false;
            button.textContent = "拉取最新";
            if (state.message) Dash.showToast(state.message, state.failed ? "error" : "success");
            load();
        }).catch(function () {
            document.getElementById("btn-refresh-invoices").disabled = false;
            document.getElementById("btn-refresh-invoices").textContent = "拉取最新";
        });
    }

    document.getElementById("btn-refresh-invoices").addEventListener("click", function () {
        var button = document.getElementById("btn-refresh-invoices");
        button.disabled = true;
        button.textContent = "拉取中";
        Dash.request("/invoices/refresh", { method: "POST" }).then(function () {
            poll();
        }).catch(function (err) {
            button.disabled = false;
            button.textContent = "拉取最新";
            Dash.showToast(err.message || "拉取没有开始", "error");
        });
    });
    document.getElementById("invoice-search").addEventListener("input", paint);
    document.getElementById("invoice-owner").addEventListener("change", paint);
    document.querySelector("#invoice-table thead").addEventListener("click", function (event) {
        var cell = event.target.closest("th[data-sort]");
        if (!cell) return;
        var key = cell.getAttribute("data-sort");
        sort.dir = sort.key === key ? sort.dir * -1 : -1;
        sort.key = key;
        markSort();
        paint();
    });

    window.Dash.loadInvoices = load;
})();
