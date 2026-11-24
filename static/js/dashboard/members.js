/* 成员管理。上面是登录账号，下面是店铺负责人，两套分开。 */
(function () {
    "use strict";
    var D = window.Dash;
    if (!D) return;
    var request = D.request;
    var showToast = D.showToast;
    var escapeHtml = D.escapeHtml;

    var ACCESS_KEYS = [
        ["stores", "店铺列表"],
        ["violation", "违规与预警"],
        ["center", "数据总览"],
        ["fund", "资金结算"],
    ];

    function loadAccess() {
        var body = document.getElementById("access-tbody");
        if (!body) return;
        request("/account-access").then(function (data) {
            var items = (data.items || []).filter(function (user) {
                return user.username !== "admin";
            });
            if (!items.length) {
                body.innerHTML = '<tr><td colspan="7" class="empty">没有成员</td></tr>';
                return;
            }
            body.innerHTML = items.map(function (user) {
                var boxes = ACCESS_KEYS.map(function (pair) {
                    var checked = user.sections.indexOf(pair[0]) >= 0 ? " checked" : "";
                    return '<td><input type="checkbox" data-access-key="' + pair[0] + '"' + checked + "></td>";
                }).join("");
                return '<tr data-access-user="' + user.id + '">' +
                    "<td>" + escapeHtml(user.username) + "</td>" +
                    "<td>" + escapeHtml(user.display_name || "-") + "</td>" +
                    boxes +
                    '<td><button type="button" class="btn btn-primary" data-access-save="' + user.id + '">保存</button></td>' +
                    "</tr>";
            }).join("");
        }).catch(function (err) {
            body.innerHTML = '<tr><td colspan="7" class="empty">加载失败：' + escapeHtml(err.message) + "</td></tr>";
        });
    }

    var shopChoices = [];
    var chargeMask = document.getElementById("charge-mask");
    var chargeForm = document.getElementById("charge-form");
    var chargeId = 0;

    function shopText(person) {
        var names = (person.shops || []).map(function (shop) { return shop.name; }).filter(Boolean);
        return names.length ? names.join("、") : "未分配";
    }

    function loadCharges() {
        var body = document.getElementById("charge-tbody");
        if (!body) return;
        request("/shop-charges").then(function (data) {
            shopChoices = data.shops || [];
            var items = data.items || [];
            if (!items.length) {
                body.innerHTML = '<tr><td colspan="3" class="empty">还没有负责人</td></tr>';
                return;
            }
            body.innerHTML = items.map(function (person) {
                return "<tr><td>" + escapeHtml(person.name) + "</td><td>" + escapeHtml(shopText(person)) +
                    '</td><td><button type="button" class="btn" data-charge="' + person.id +
                    '" data-charge-name="' + escapeHtml(person.name) + '">分配</button></td></tr>';
            }).join("");
        }).catch(function (err) {
            body.innerHTML = '<tr><td colspan="3" class="empty">加载失败：' + escapeHtml(err.message) + "</td></tr>";
        });
    }

    function closeCharge() {
        chargeMask.classList.add("hidden");
        chargeId = 0;
    }

    function openCharge(id, name) {
        chargeId = id;
        document.getElementById("charge-title").textContent = "分配负责店铺 · " + name;
        var list = document.getElementById("charge-list");
        if (!shopChoices.length) {
            list.innerHTML = '<p class="form-hint">还没有店铺</p>';
        } else {
            list.innerHTML = shopChoices.map(function (shop) {
                var checked = shop.charge_id === id ? " checked" : "";
                var note = "";
                if (shop.charge_id && shop.charge_id !== id && shop.charge_name) {
                    note = '<span class="charge-owner">当前 ' + escapeHtml(shop.charge_name) + "</span>";
                }
                return '<label class="charge-item"><input type="checkbox" data-charge-shop="' + shop.id + '"' + checked + ">" +
                    "<span>" + escapeHtml(shop.name || "未命名店铺") + "</span>" + note + "</label>";
            }).join("");
        }
        chargeMask.classList.remove("hidden");
    }

    document.getElementById("charge-add").addEventListener("submit", function (event) {
        event.preventDefault();
        var input = document.getElementById("charge-name");
        var name = input.value.trim();
        if (!name) {
            showToast("请输入姓名", "error");
            return;
        }
        request("/shop-charges", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: name }),
        }).then(function () {
            input.value = "";
            showToast("已添加负责人", "success");
            loadCharges();
        }).catch(function (err) {
            showToast(err.message, "error");
        });
    });

    document.getElementById("charge-cancel").addEventListener("click", closeCharge);
    chargeMask.addEventListener("click", function (event) {
        if (event.target === chargeMask) closeCharge();
    });
    chargeForm.addEventListener("submit", function (event) {
        event.preventDefault();
        if (!chargeId) return;
        var shopIds = [];
        chargeForm.querySelectorAll("[data-charge-shop]").forEach(function (box) {
            if (box.checked) shopIds.push(Number(box.getAttribute("data-charge-shop")));
        });
        var saveBtn = document.getElementById("charge-save");
        saveBtn.disabled = true;
        request("/shop-charges/" + chargeId + "/shops", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ shop_ids: shopIds }),
        }).then(function () {
            showToast("负责店铺已保存", "success");
            closeCharge();
            loadCharges();
        }).catch(function (err) {
            showToast(err.message, "error");
        }).finally(function () {
            saveBtn.disabled = false;
        });
    });
    document.getElementById("charge-tbody").addEventListener("click", function (event) {
        var button = event.target.closest("[data-charge]");
        if (!button) return;
        openCharge(Number(button.getAttribute("data-charge")), button.getAttribute("data-charge-name") || "");
    });

    var modal = document.getElementById("member-mask");
    var form = document.getElementById("member-form");

    function openModal() {
        form.reset();
        document.querySelectorAll("#member-form .field-error").forEach(function (el) { el.textContent = ""; });
        modal.classList.remove("hidden");
        document.getElementById("f-account").focus();
    }

    function closeModal() {
        modal.classList.add("hidden");
    }

    document.getElementById("btn-add-member").addEventListener("click", openModal);
    document.getElementById("modal-cancel").addEventListener("click", closeModal);
    modal.addEventListener("click", function (event) {
        if (event.target === modal) closeModal();
    });

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        var username = document.getElementById("f-account").value.trim();
        var displayName = document.getElementById("f-name").value.trim();
        var accountError = document.getElementById("e-account");
        var nameError = document.getElementById("e-name");
        accountError.textContent = /^[A-Za-z0-9_]{2,50}$/.test(username) ? "" : "账号为2-50位字母、数字或下划线";
        nameError.textContent = displayName ? "" : "请输入姓名";
        if (accountError.textContent || nameError.textContent) return;
        var submitBtn = document.getElementById("modal-submit");
        submitBtn.disabled = true;
        request("/account-access", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: username, display_name: displayName }),
        }).then(function () {
            showToast("已添加，初始密码 admin123", "success");
            closeModal();
            loadAccess();
        }).catch(function (err) {
            showToast(err.message, "error");
        }).finally(function () {
            submitBtn.disabled = false;
        });
    });

    document.getElementById("access-tbody").addEventListener("click", function (event) {
        var button = event.target.closest("[data-access-save]");
        if (!button) return;
        var row = button.closest("tr");
        var sections = [];
        row.querySelectorAll("[data-access-key]").forEach(function (box) {
            if (box.checked) sections.push(box.getAttribute("data-access-key"));
        });
        button.disabled = true;
        request("/account-access/" + button.getAttribute("data-access-save"), {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sections: sections }),
        }).then(function () {
            showToast("已保存", "success");
        }).catch(function (err) {
            showToast(err.message, "error");
        }).finally(function () {
            button.disabled = false;
        });
    });

    D.pages.members = function () {
        loadAccess();
        loadCharges();
    };
})();
