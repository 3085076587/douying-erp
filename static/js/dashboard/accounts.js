(function () {
    "use strict";

    var goodsBody = document.getElementById("account-tbody-goods");
    var depositBody = document.getElementById("account-tbody-deposit");
    var withdrawShop = null;
    var withdrawBank = "";
    var withdrawCaps = { goods: "", base: "", experience: "" };

    var fundTimer = null;
    var fundDone = null;
    var fundItems = [];
    var fundTab = "goods";
    var fundSort = {
        goods: { key: "", dir: 1 },
        deposit: { key: "", dir: 1 }
    };
    var GOODS_SUM = [
        ["总资产", "total_asset"],
        ["账户余额", "balance"],
        ["可提现", "withdraw_balance"],
        ["售后预留", "reserve_balance"],
        ["已冻结", "frozen_balance"],
        ["待结算", "pending_settle"],
        ["千川可投", "qianchuan_balance"]
    ];
    var DEPOSIT_SUM = [
        ["基础保证金", "deposit_base"],
        ["基础应缴", "deposit_base_payable"],
        ["基础保证金代缴", "deposit_base_due"],
        ["基础可提现", "deposit_base_withdraw"],
        ["体验保证金", "deposit_experience"],
        ["体验应缴", "deposit_experience_payable"],
        ["体验保证金代缴", "deposit_experience_due"],
        ["体验可提现", "deposit_experience_withdraw"]
    ];

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
            var html = '<tr><td colspan="13" class="empty">' + Dash.escapeHtml(err.message) + "</td></tr>";
            goodsBody.innerHTML = html;
            depositBody.innerHTML = '<tr><td colspan="12" class="empty">' + Dash.escapeHtml(err.message) + "</td></tr>";
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

    function totalAsset(row) {
        if (row.balance == null && row.pending_settle == null) return null;
        return (Number(row.balance) || 0) + (Number(row.pending_settle) || 0);
    }

    function dueFen(payable, balance) {
        if (payable == null || payable === "" || balance == null || balance === "") return null;
        var left = Number(payable);
        var right = Number(balance);
        if (isNaN(left) || isNaN(right)) return null;
        return left - right;
    }

    function matchedFunds() {
        var keyword = document.getElementById("fund-search").value.trim().toLowerCase();
        return fundItems.filter(function (row) {
            row.total_asset = totalAsset(row);
            row.deposit_base_due = dueFen(row.deposit_base_payable, row.deposit_base);
            row.deposit_experience_due = dueFen(row.deposit_experience_payable, row.deposit_experience);
            if (!keyword) return true;
            return (row.name || "").toLowerCase().indexOf(keyword) >= 0 || String(row.shop_id || "").indexOf(keyword) >= 0;
        });
    }

    function orderedFunds(tab) {
        var rows = matchedFunds();
        var sort = fundSort[tab];
        if (!sort || !sort.key) return rows;
        var key = sort.key;
        var dir = sort.dir;
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

    function sumFen(items, key) {
        var any = false;
        var total = 0;
        items.forEach(function (row) {
            var number = sortNumber(row[key]);
            if (number == null) return;
            any = true;
            total += number;
        });
        return any ? total : null;
    }

    function markSort() {
        var sort = fundSort[fundTab];
        var table = document.getElementById(fundTab === "goods" ? "fund-table-goods" : "fund-table-deposit");
        table.querySelectorAll("th.sortable").forEach(function (cell) {
            cell.classList.remove("sort-asc", "sort-desc");
            if (cell.getAttribute("data-sort") === sort.key) {
                cell.classList.add(sort.dir > 0 ? "sort-asc" : "sort-desc");
            }
        });
    }

    function paintSummary(items) {
        var fields = fundTab === "goods" ? GOODS_SUM : DEPOSIT_SUM;
        document.getElementById("fund-summary").innerHTML = fields.map(function (field) {
            return '<span class="center-summary-item"><span class="label">' + field[0] + "</span><b>"
                + Dash.yuan(sumFen(items, field[1])) + "</b></span>";
        }).join("");
    }

    function shopCells(row) {
        var legal = row.legal_person || "";
        if (row.charge_mobile) legal = legal ? legal + " " + row.charge_mobile : row.charge_mobile;
        var login = row.login_status === "online"
            ? '<span class="tag tag-ok">在线</span>'
            : '<span class="tag tag-bad">掉线</span>';
        return "<td>" + Dash.escapeHtml(row.name) + "</td><td>" + Dash.escapeHtml(legal) + "</td><td>" + login + "</td>";
    }

    function withdrawCell(row) {
        var bank = row.bank_name ? row.bank_name + (row.bank_tail ? " (" + row.bank_tail + ")" : "") : "—";
        var goods = row.withdraw_balance == null ? "" : String(row.withdraw_balance);
        var base = row.deposit_base_withdraw == null ? "" : String(row.deposit_base_withdraw);
        var experience = row.deposit_experience_withdraw == null ? "" : String(row.deposit_experience_withdraw);
        if (!row.sync_error && (goods !== "" || base !== "" || experience !== "")) {
            return '<button class="btn" type="button" data-withdraw="' + row.store_pk +
                '" data-bank="' + Dash.escapeHtml(bank) +
                '" data-goods="' + goods + '" data-base="' + base + '" data-experience="' + experience + '">提现</button>';
        }
        return Dash.escapeHtml(row.sync_error || "");
    }

    function paint(data) {
        if (data) fundItems = data.items || [];
        var goods = orderedFunds("goods");
        var deposit = orderedFunds("deposit");
        paintSummary(fundTab === "goods" ? goods : deposit);
        if (!fundItems.length) {
            goodsBody.innerHTML = '<tr><td colspan="13" class="empty">还没有账户数据。请先用插件添加店铺，再点拉取最新。</td></tr>';
            depositBody.innerHTML = '<tr><td colspan="12" class="empty">还没有账户数据。请先用插件添加店铺，再点拉取最新。</td></tr>';
            return;
        }
        goodsBody.innerHTML = goods.length ? goods.map(function (row) {
            var bank = row.bank_name ? row.bank_name + (row.bank_tail ? " (" + row.bank_tail + ")" : "") : "—";
            return "<tr>" + shopCells(row) +
                "<td>" + Dash.yuan(row.total_asset) + "</td><td>" + Dash.yuan(row.balance) +
                "</td><td>" + Dash.yuan(row.withdraw_balance) + "</td><td>" + Dash.yuan(row.reserve_balance) +
                "</td><td>" + Dash.yuan(row.frozen_balance) + "</td><td>" + Dash.yuan(row.pending_settle) +
                "</td><td>" + (row.settle_day ? Dash.escapeHtml(row.settle_day) + "天" : "—") +
                "</td><td>" + Dash.yuan(row.qianchuan_balance) + "</td><td>" + Dash.escapeHtml(bank) +
                "</td><td>" + withdrawCell(row) + "</td></tr>";
        }).join("") : '<tr><td colspan="13" class="empty">没有符合搜索的店铺</td></tr>';
        depositBody.innerHTML = deposit.length ? deposit.map(function (row) {
            return "<tr>" + shopCells(row) +
                "<td>" + Dash.yuan(row.deposit_base) + "</td><td>" + Dash.yuan(row.deposit_base_payable) +
                "</td><td>" + Dash.yuan(row.deposit_base_due) + "</td><td>" + Dash.yuan(row.deposit_base_withdraw) +
                "</td><td>" + Dash.yuan(row.deposit_experience) + "</td><td>" + Dash.yuan(row.deposit_experience_payable) +
                "</td><td>" + Dash.yuan(row.deposit_experience_due) +
                "</td><td>" + Dash.yuan(row.deposit_experience_withdraw) +
                "</td><td>" + withdrawCell(row) + "</td></tr>";
        }).join("") : '<tr><td colspan="12" class="empty">没有符合搜索的店铺</td></tr>';
        markSort();
    }

    function csvCell(value) {
        var text = String(value == null ? "" : value);
        if (/^[=+\-@]/.test(text)) text = "'" + text;
        if (/[",\r\n]/.test(text)) return '"' + text.replace(/"/g, '""') + '"';
        return text;
    }

    function yuanCsv(fen) {
        if (fen == null || fen === "") return "";
        var number = Number(fen);
        if (isNaN(number)) return "";
        return (number / 100).toFixed(2);
    }

    function exportAccounts() {
        var items = orderedFunds(fundTab);
        if (!items.length) {
            Dash.showToast(fundItems.length ? "没有符合搜索的店铺" : "还没有账户数据", "error");
            return;
        }
        var goodsTab = fundTab === "goods";
        var header = goodsTab
            ? ["店铺", "法人", "登录", "总资产", "账户余额", "可提现", "售后预留", "已冻结", "待结算", "账期", "千川可投", "银行卡"]
            : ["店铺", "法人", "登录", "基础保证金", "基础应缴", "基础保证金代缴", "基础可提现", "体验保证金", "体验应缴", "体验保证金代缴", "体验可提现"];
        var lines = [header.map(csvCell).join(",")];
        items.forEach(function (row) {
            var legal = row.legal_person || "";
            if (row.charge_mobile) legal = legal ? legal + " " + row.charge_mobile : row.charge_mobile;
            var bank = row.bank_name ? row.bank_name + (row.bank_tail ? " (" + row.bank_tail + ")" : "") : "";
            var cells = goodsTab
                ? [
                    row.name || "", legal, row.login_status === "online" ? "在线" : "掉线",
                    yuanCsv(row.total_asset), yuanCsv(row.balance), yuanCsv(row.withdraw_balance),
                    yuanCsv(row.reserve_balance), yuanCsv(row.frozen_balance), yuanCsv(row.pending_settle),
                    row.settle_day || "", yuanCsv(row.qianchuan_balance), bank
                ]
                : [
                    row.name || "", legal, row.login_status === "online" ? "在线" : "掉线",
                    yuanCsv(row.deposit_base), yuanCsv(row.deposit_base_payable), yuanCsv(row.deposit_base_due),
                    yuanCsv(row.deposit_base_withdraw),
                    yuanCsv(row.deposit_experience), yuanCsv(row.deposit_experience_payable),
                    yuanCsv(row.deposit_experience_due), yuanCsv(row.deposit_experience_withdraw)
                ];
            lines.push(cells.map(csvCell).join(","));
        });
        var now = new Date();
        var pad = function (n) { return n < 10 ? "0" + n : String(n); };
        var stamp = now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate());
        var blob = new Blob(["\uFEFF" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
        var url = URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = url;
        link.download = (fundTab === "goods" ? "货款账户-" : "保证金-") + stamp + ".csv";
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
    }

    document.getElementById("btn-export-accounts").addEventListener("click", exportAccounts);

    document.getElementById("fund-search").addEventListener("input", function () {
        paint(null);
    });

    document.querySelectorAll("[data-fund-tab]").forEach(function (button) {
        button.addEventListener("click", function () {
            fundTab = button.getAttribute("data-fund-tab");
            document.querySelectorAll("[data-fund-tab]").forEach(function (item) {
                item.classList.toggle("active", item === button);
            });
            document.getElementById("fund-pane-goods").classList.toggle("hidden", fundTab !== "goods");
            document.getElementById("fund-pane-deposit").classList.toggle("hidden", fundTab !== "deposit");
            paint(null);
        });
    });

    document.getElementById("view-fund-account").addEventListener("click", function (event) {
        var cell = event.target.closest("th[data-sort]");
        if (!cell) return;
        var sort = fundSort[fundTab];
        var key = cell.getAttribute("data-sort");
        sort.dir = sort.key === key ? sort.dir * -1 : -1;
        sort.key = key;
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

    function paintWithdrawSource() {
        var source = document.getElementById("withdraw-source").value;
        var deposit = source !== "goods";
        document.getElementById("withdraw-code-label").classList.toggle("hidden", deposit);
        document.getElementById("withdraw-remark-label").classList.toggle("hidden", deposit);
        document.getElementById("send-code").classList.toggle("hidden", deposit);
        document.getElementById("withdraw-card-label").classList.toggle("hidden", !deposit);
        document.getElementById("withdraw-code").required = !deposit;
        document.getElementById("withdraw-card").required = deposit;
        document.getElementById("withdraw-mode").textContent = deposit
            ? "提现方式：退回可提现部分，1-3个工作日到账"
            : "提现方式：部分提现";
        var cap = withdrawCaps[source];
        document.getElementById("withdraw-cap").textContent = cap === "" ? "" : "最多可提现 " + Dash.yuan(cap);
        document.getElementById("withdraw-tip").textContent = deposit
            ? "退到填写的收款银行卡。每周每类保证金通常只能提现一次。"
            : "默认部分提现，验证码发到店铺管理人手机。";
    }

    document.getElementById("view-fund-account").addEventListener("click", function (event) {
        var button = event.target.closest("[data-withdraw]");
        if (!button) return;
        withdrawShop = button.dataset.withdraw;
        withdrawBank = button.dataset.bank || "";
        withdrawCaps = {
            goods: button.dataset.goods || "",
            base: button.dataset.base || "",
            experience: button.dataset.experience || ""
        };
        var source = fundTab === "deposit" ? "base" : "goods";
        document.getElementById("withdraw-source").value = source;
        document.getElementById("withdraw-bank").textContent = source === "base"
            ? "基础保证金可提现部分退回银行卡"
            : "货款提现至 " + withdrawBank;
        document.getElementById("withdraw-yuan").value = "";
        document.getElementById("withdraw-code").value = "";
        document.getElementById("withdraw-remark").value = "";
        document.getElementById("withdraw-card").value = "";
        paintWithdrawSource();
        document.getElementById("withdraw-mask").classList.remove("hidden");
    });

    document.getElementById("withdraw-source").addEventListener("change", function () {
        var source = document.getElementById("withdraw-source").value;
        var bank = document.getElementById("withdraw-bank");
        if (source === "goods") bank.textContent = "货款提现至 " + withdrawBank;
        else if (source === "base") bank.textContent = "基础保证金可提现部分退回银行卡";
        else bank.textContent = "体验保证金可提现部分退回银行卡";
        paintWithdrawSource();
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
        var source = document.getElementById("withdraw-source").value;
        var yuanValue = Number(document.getElementById("withdraw-yuan").value);
        var payload = {
            source: source,
            amount_fen: Math.round(yuanValue * 100)
        };
        if (source === "goods") {
            payload.code = document.getElementById("withdraw-code").value.trim();
            payload.remark = document.getElementById("withdraw-remark").value.trim();
        } else {
            payload.account_number = document.getElementById("withdraw-card").value.replace(/\s+/g, "");
        }
        Dash.request("/funds/accounts/" + withdrawShop + "/withdraw", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        }).then(function (data) {
            document.getElementById("withdraw-mask").classList.add("hidden");
            Dash.showToast(data.message || "已提交提现", "success");
            loadAccounts();
        }).catch(function (err) {
            document.getElementById("withdraw-tip").textContent = err.message;
        });
    });

    window.Dash.loadAccounts = loadAccounts;
})();
