(function () {
    "use strict";

    var body = document.getElementById("account-tbody");
    var withdrawShop = null;

    var fundTimer = null;
    var fundDone = null;
    var fundItems = [];
    var fundSort = { key: "", dir: 1 };

    function showFundMask(text) {
        document.getElementById("fund-mask-text").textContent = text || "正在拉取…";
        document.getElementById("fund-mask").classList.remove("hidden");
        var layout = document.querySelector(".layout");
        if (layout) layout.setAttribute("inert", "");
    }

    function hideFundMask() {
        document.getElementById("fund-mask").classList.add("hidden");
        var layout = document.querySelector(".layout");
        if (layout) layout.removeAttribute("inert");
    }

    function paintProgress(state) {
        var total = state.total || 0;
        var done = state.done || 0;
        var bar = document.getElementById("fund-bar");
        if (state.running) {
            showFundMask(total ? ("正在拉取 " + done + " / " + total + " 家") : "正在拉取…");
            bar.style.width = total ? Math.min(100, Math.round(done * 100 / total)) + "%" : "0";
            return;
        }
        hideFundMask();
        bar.style.width = "0";
    }

    function watchFunds(onDone) {
        if (onDone) fundDone = onDone;
        if (fundTimer) return;
        fundTimer = setInterval(function () {
            Dash.request("/funds/progress").then(function (state) {
                paintProgress(state);
                if (state.running) return;
                clearInterval(fundTimer);
                fundTimer = null;
                var done = fundDone;
                fundDone = null;
                if (done) done();
            }).catch(function () {});
        }, 1000);
    }

    function loadAccounts() {
        Dash.request("/funds/accounts").then(paint).catch(function (err) {
            body.innerHTML = '<tr><td colspan="14" class="empty">' + Dash.escapeHtml(err.message) + "</td></tr>";
        });
        Dash.request("/funds/progress").then(function (state) {
            paintProgress(state);
            if (state.running) {
                watchFunds(function () {
                    loadAccounts();
                    document.getElementById("btn-refresh-accounts").disabled = false;
                });
            }
        }).catch(function () {});
    }

    function sortNumber(value) {
        if (value == null || value === "") return null;
        var number = Number(value);
        return isNaN(number) ? null : number;
    }

    function visibleFunds() {
        var keyword = document.getElementById("fund-search").value.trim().toLowerCase();
        var rows = fundItems.filter(function (row) {
            if (!keyword) return true;
            return (row.name || "").toLowerCase().indexOf(keyword) >= 0 || String(row.shop_id || "").indexOf(keyword) >= 0;
        });
        if (!fundSort.key) return rows;
        var key = fundSort.key;
        var dir = fundSort.dir;
        rows.sort(function (left, right) {
            var a = sortNumber(left[key]);
            var b = sortNumber(right[key]);
            if (a == null && b == null) return 0;
            if (a == null) return 1;
            if (b == null) return -1;
            return (a - b) * dir;
        });
        return rows;
    }

    function markSort() {
        document.querySelectorAll("#fund-table th.sortable").forEach(function (cell) {
            cell.classList.remove("sort-asc", "sort-desc");
            if (cell.getAttribute("data-sort") === fundSort.key) {
                cell.classList.add(fundSort.dir > 0 ? "sort-asc" : "sort-desc");
            }
        });
    }

    function paint(data) {
        if (data) fundItems = data.items || [];
        var items = visibleFunds();
        if (!fundItems.length) {
            body.innerHTML = '<tr><td colspan="14" class="empty">还没有账户数据。先在店铺列表添加账号，再点拉取最新。</td></tr>';
            return;
        }
        if (!items.length) {
            body.innerHTML = '<tr><td colspan="14" class="empty">没有符合搜索的店铺</td></tr>';
            return;
        }
        body.innerHTML = items.map(function (row) {
            var bank = row.bank_name ? row.bank_name + (row.bank_tail ? " (" + row.bank_tail + ")" : "") : "—";
            var action = row.withdraw_balance != null && !row.sync_error
                ? '<button class="btn" type="button" data-withdraw="' + row.store_pk + '" data-bank="' + Dash.escapeHtml(bank) + '">提现</button>'
                : Dash.escapeHtml(row.sync_error || "");
            var legal = row.legal_person || "";
            if (row.charge_mobile) legal = legal ? legal + " " + row.charge_mobile : row.charge_mobile;
            return "<tr><td>" + Dash.escapeHtml(row.name) + "</td><td>" + Dash.escapeHtml(legal) +
                "</td><td>" + (row.login_status === "online" ? '<span class="tag tag-ok">在线</span>' : '<span class="tag tag-bad">掉线</span>') +
                "</td><td>" + Dash.yuan(row.balance) + "</td><td>" + Dash.yuan(row.withdraw_balance) +
                "</td><td>" + Dash.yuan(row.reserve_balance) + "</td><td>" + Dash.yuan(row.frozen_balance) +
                "</td><td>" + Dash.yuan(row.pending_settle) + "</td><td>" + (row.settle_day ? Dash.escapeHtml(row.settle_day) + "天" : "—") +
                "</td><td>" + Dash.yuan(row.qianchuan_balance) + "</td><td>" + Dash.yuan(row.deposit_base) +
                "</td><td>" + Dash.yuan(row.deposit_experience) + "</td><td>" + Dash.escapeHtml(bank) +
                "</td><td>" + action + "</td></tr>";
        }).join("");
    }

    document.getElementById("fund-search").addEventListener("input", function () {
        paint(null);
    });

    document.querySelector("#fund-table thead").addEventListener("click", function (event) {
        var cell = event.target.closest("th[data-sort]");
        if (!cell) return;
        var key = cell.getAttribute("data-sort");
        fundSort.dir = fundSort.key === key ? fundSort.dir * -1 : -1;
        fundSort.key = key;
        markSort();
        paint(null);
    });

    document.getElementById("btn-refresh-accounts").addEventListener("click", function () {
        var button = document.getElementById("btn-refresh-accounts");
        button.disabled = true;
        showFundMask("正在向抖店拉取…");
        Dash.request("/funds/accounts/refresh", { method: "POST" }).then(function (state) {
            paintProgress(state);
            if (!state.running && !(state.total > 0)) {
                hideFundMask();
                button.disabled = false;
                Dash.showToast("还没有可以拉取的店铺凭证", "error");
                return;
            }
            watchFunds(function () {
                loadAccounts();
                button.disabled = false;
                Dash.showToast("账户中心已更新", "success");
            });
        }).catch(function (err) {
            hideFundMask();
            Dash.showToast(err.message, "error");
            button.disabled = false;
        });
    });

    body.addEventListener("click", function (event) {
        var button = event.target.closest("[data-withdraw]");
        if (!button) return;
        withdrawShop = button.dataset.withdraw;
        document.getElementById("withdraw-bank").textContent = "提现至 " + (button.dataset.bank || "");
        document.getElementById("withdraw-yuan").value = "";
        document.getElementById("withdraw-code").value = "";
        document.getElementById("withdraw-remark").value = "";
        document.getElementById("withdraw-tip").textContent = "默认部分提现，验证码发到店铺管理人手机。";
        document.getElementById("withdraw-mask").classList.remove("hidden");
    });

    document.getElementById("close-withdraw").addEventListener("click", function () {
        document.getElementById("withdraw-mask").classList.add("hidden");
    });

    document.getElementById("send-code").addEventListener("click", function () {
        Dash.request("/funds/accounts/" + withdrawShop + "/withdraw/code", { method: "POST" })
            .then(function (data) {
                document.getElementById("withdraw-tip").textContent = data.message || "验证码已发送";
            })
            .catch(function (err) {
                document.getElementById("withdraw-tip").textContent = err.message;
            });
    });

    document.getElementById("withdraw-form").addEventListener("submit", function (event) {
        event.preventDefault();
        var yuanValue = Number(document.getElementById("withdraw-yuan").value);
        Dash.request("/funds/accounts/" + withdrawShop + "/withdraw", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                amount_fen: Math.round(yuanValue * 100),
                code: document.getElementById("withdraw-code").value.trim(),
                remark: document.getElementById("withdraw-remark").value.trim()
            })
        }).then(function () {
            document.getElementById("withdraw-mask").classList.add("hidden");
            Dash.showToast("已提交部分提现", "success");
            loadAccounts();
        }).catch(function (err) {
            document.getElementById("withdraw-tip").textContent = err.message;
        });
    });

    window.Dash.loadAccounts = loadAccounts;
})();
