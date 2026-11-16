/* 违规与预警：筛选、表格、拉取进度。独立于店铺列表和账户中心。 */
(function () {
    "use strict";

    var body = document.getElementById("vio-tbody");
    var items = [];
    var sortDir = -1; // 违规时间默认倒序（最新在前）
    var vioTimer = null;
    var vioDone = null;
    var loadedOnce = false;
    var scheduleTimer = null;
    var lastUpdateSeen = "";
    var seq = 0; // 查询序号，避免连续切换时旧结果盖掉新结果
    var focusPk = "";
    var focusIds = "";
    var pinnedIds = "";

    function el(id) {
        return document.getElementById(id);
    }

    function nextUpdateText(state) {
        if (state.next_update) return state.next_update;
        var match = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/.exec(state.last_update || "");
        if (!match) return "";
        var at = new Date(
            Number(match[1]), Number(match[2]) - 1, Number(match[3]),
            Number(match[4]), Number(match[5]), Number(match[6])
        );
        if (isNaN(at.getTime())) return "";
        at.setMinutes(at.getMinutes() + (state.interval_minutes || 10));
        function pad(value) { return String(value).padStart(2, "0"); }
        return at.getFullYear() + "-" + pad(at.getMonth() + 1) + "-" + pad(at.getDate())
            + " " + pad(at.getHours()) + ":" + pad(at.getMinutes());
    }

    // 「数据10分钟更新一次，上一次更新 …，下次更新 …」
    function paintSchedule(state) {
        if (!state) return;
        var minutes = state.interval_minutes || 10;
        var text = "数据" + minutes + "分钟更新一次";
        if (state.last_update) text += "，上一次更新 " + state.last_update;
        var next = nextUpdateText(state);
        if (next) text += "，下次更新 " + next;
        el("vio-schedule").textContent = text;
        if (state.last_update) lastUpdateSeen = state.last_update;
    }

    function followRunning() {
        if (vioTimer) return;
        el("btn-refresh-violations").disabled = true;
        watch(function (state) {
            el("btn-refresh-violations").disabled = false;
            paintSchedule(state);
            loadOptions();
            query();
        });
    }

    // 开着这个页面就跟着进度走。预热或定时更新一开始，进度条马上出来。
    function pollSchedule() {
        if (scheduleTimer) return;
        scheduleTimer = setInterval(function () {
            Dash.request("/violations/progress").then(function (state) {
                var before = lastUpdateSeen;
                paintSchedule(state);
                if (state.running) {
                    paintProgress(state);
                    followRunning();
                    return;
                }
                if (state.last_update && state.last_update !== before) {
                    loadOptions();
                    query();
                }
            }).catch(function () {});
        }, 2000);
    }

    function showMask(text) {
        el("violation-mask-text").textContent = text || "正在拉取…";
        el("violation-mask").classList.remove("hidden");
        var layout = document.querySelector(".layout");
        if (layout) layout.setAttribute("inert", "");
    }

    function hideMask() {
        el("violation-mask").classList.add("hidden");
        var layout = document.querySelector(".layout");
        if (layout) layout.removeAttribute("inert");
    }

    function violationsVisible() {
        var view = document.getElementById("view-violations");
        return !!(view && view.classList.contains("active"));
    }

    function paintProgress(state) {
        var total = state.total || 0;
        var done = state.done || 0;
        var bar = el("violation-bar");
        if (state.running && violationsVisible()) {
            var warming = state.trigger === "schedule";
            el("violation-mask-title").textContent = warming ? "正在预热违规数据" : "正在拉取违规数据";
            showMask(total
                ? (warming ? "正在预热 " : "正在拉取 ") + done + " / " + total + " 家"
                : "正在预热…");
            bar.style.width = total ? Math.min(100, Math.round(done * 100 / total)) + "%" : "18%";
            return;
        }
        hideMask();
        bar.style.width = "0";
    }

    function watch(onDone) {
        if (vioTimer) return;
        if (onDone) vioDone = onDone;
        vioTimer = setInterval(function () {
            Dash.request("/violations/progress").then(function (state) {
                paintProgress(state);
                if (state.running) return;
                clearInterval(vioTimer);
                vioTimer = null;
                var done = vioDone;
                vioDone = null;
                if (done) done(state);
            }).catch(function () {});
        }, 1000);
    }

    function fmtDate(date) {
        var y = date.getFullYear();
        var m = String(date.getMonth() + 1).padStart(2, "0");
        var d = String(date.getDate()).padStart(2, "0");
        return y + "-" + m + "-" + d;
    }

    function defaultDates() {
        // 时间下拉：今天 / 近7天 / 近30天 / 自定义（自定义时不动用户选的日期）
        var mode = el("vio-range").value;
        if (mode === "custom") return;
        var end = new Date();
        var start = new Date();
        if (mode === "today") {
            start = end;
        } else {
            var days = parseInt(mode, 10) || 30;
            start.setDate(start.getDate() - days);
        }
        el("vio-start").value = fmtDate(start);
        el("vio-end").value = fmtDate(end);
    }

    // 下拉选项：既可以是字符串，也可以是 {value, count}
    // 带条数的项显示成「名称（12 条）」，当前条件下没有记录的显示「名称（无）」
    function fillSelect(select, values) {
        var current = select.value;
        while (select.options.length > 1) select.remove(1);
        var hasCurrent = false;
        (values || []).forEach(function (item) {
            var value = typeof item === "string" ? item : item.value;
            var count = typeof item === "string" ? null : (item.count || 0);
            if (!value) return;
            if (value === current) hasCurrent = true;
            var option = document.createElement("option");
            option.value = value;
            option.textContent = count === null
                ? value
                : value + (count > 0 ? "（" + count + " 条）" : "（无）");
            if (count === 0) option.className = "is-empty";
            select.appendChild(option);
        });
        // 当前选中的值在新条件下就算没有数据，也保留在下拉里，避免选择被悄悄清空
        if (current) {
            if (!hasCurrent) {
                var keep = document.createElement("option");
                keep.value = current;
                keep.textContent = current + "（无）";
                select.appendChild(keep);
            }
            select.value = current;
        }
    }

    // ---- 店铺多选（手工实现的下拉 + 勾选，原生 select 放不了复选框）----
    function shopBoxes() {
        return Array.prototype.slice.call(el("vio-shop-list").querySelectorAll("input[type=checkbox]"));
    }

    function checkedShopIds() {
        return shopBoxes().filter(function (box) { return box.checked; }).map(function (box) {
            return box.value;
        });
    }

    function syncShopUI() {
        var boxes = shopBoxes();
        var checked = boxes.filter(function (box) { return box.checked; });
        var label = el("vio-shop-label");
        if (!boxes.length || !checked.length) {
            label.textContent = "请选择店铺";
        } else if (checked.length === boxes.length) {
            label.textContent = "全部店铺（" + checked.length + " 家）";
        } else if (checked.length === 1) {
            label.textContent = checked[0].getAttribute("data-name") || "已选 1 家店铺";
        } else {
            label.textContent = "已选 " + checked.length + " 家店铺";
        }
        el("vio-shop-all").checked = boxes.length > 0 && checked.length === boxes.length;
        el("vio-shop-btn").classList.toggle("has-value", checked.length > 0);
    }

    function closeShopPanel() {
        el("vio-shop-panel").classList.add("hidden");
        el("vio-shop-btn").setAttribute("aria-expanded", "false");
    }

    function loadShops() {
        Dash.request("/violations/shops").then(function (data) {
            var list = data.items || [];
            var box = el("vio-shop-list");
            if (!list.length) {
                box.innerHTML = '<p class="vio-ms-empty">暂无店铺</p>';
                syncShopUI();
                return;
            }
            box.innerHTML = list.map(function (row) {
                var name = row.name || row.shop_id;
                return '<label class="vio-ms-item">'
                    + '<input type="checkbox" value="' + Dash.escapeHtml(String(row.store_pk)) + '"'
                    + ' data-name="' + Dash.escapeHtml(name) + '">'
                    + '<span>' + Dash.escapeHtml(name) + '</span>'
                    + '</label>';
            }).join("");
            syncShopUI();
            if (focusPk) applyFocus();
        }).catch(function () {});
    }

    function applyFocus() {
        if (!focusPk) return;
        var boxes = shopBoxes();
        if (!boxes.length) return;
        var target = focusPk;
        focusPk = "";
        pinnedIds = focusIds;
        focusIds = "";
        ["vio-type", "vio-penalty", "vio-status"].forEach(function (id) {
            if (el(id)) el(id).value = "";
        });
        boxes.forEach(function (box) { box.checked = box.value === target; });
        syncShopUI();
        query(false);
    }

    function clearPinned() {
        pinnedIds = "";
    }

    // 当前筛选条件 → 查询参数（查询和下拉选项共用同一套条件，保证两边一致）
    function filterParams() {
        var params = new URLSearchParams();
        var ids = checkedShopIds();
        if (ids.length) params.set("store_pks", ids.join(","));
        else params.set("store_pk", "0");
        params.set("account", el("vio-account").value || "");
        params.set("violation_type", el("vio-type").value || "");
        params.set("penalty", el("vio-penalty").value || "");
        params.set("status", el("vio-status").value || "");
        params.set("due", el("vio-due").value || "");
        if (pinnedIds) params.set("violation_ids", pinnedIds);
        params.set("start_date", el("vio-start").value || "");
        params.set("end_date", el("vio-end").value || "");
        return params;
    }

    // 下拉选项随手选一次就重新算一遍：只留下当前条件下确实有数据的项
    function refreshOptions() {
        var params = filterParams();
        var mySeq = seq;
        Dash.request("/violations/options?" + params.toString()).then(function (data) {
            if (mySeq !== seq) return;
            // 抖音账号：目前只有一个，以后加了账号这里会自动多出选项
            fillSelect(el("vio-account"), data.accounts || []);
            // 违规类型、处罚方式跟着条件联动；状态下拉由 paint() 按当前结果生成
            fillSelect(el("vio-type"), data.types || []);
            fillSelect(el("vio-penalty"), data.penalties || []);
        }).catch(function () {});
    }

    function loadOptions() {
        refreshOptions();
    }

    function query(silent) {
        var params = filterParams();
        var mySeq = ++seq;
        if (!silent) body.innerHTML = '<tr><td colspan="8" class="empty"><span class="vio-spinner"></span>加载中…</td></tr>';
        Dash.request("/violations?" + params.toString()).then(function (data) {
            if (mySeq !== seq) return; // 已经有更新的查询了，丢掉旧结果
            if (data.pulling) {
                // 这个时间范围超出了近 30 天的定时缓存，后台正在向抖店拉取，
                // 跟着进度走，拉完会自动再查一次
                body.innerHTML = '<tr><td colspan="8" class="empty">正在向抖店拉取这个时间范围的数据，拉完自动显示…</td></tr>';
                followRunning();
                return;
            }
            items = data.items || [];
            paint();
            refreshOptions();
        }).catch(function (err) {
            if (mySeq !== seq) return;
            body.innerHTML = '<tr><td colspan="8" class="empty">' + Dash.escapeHtml(err.message) + "</td></tr>";
        });
        el("btn-query-violations").classList.remove("is-dirty");
    }

    // 下拉一变就自动查，不用再点「查询」（日期框除外）
    var autoTimer = null;

    function autoApply() {
        if (autoTimer) clearTimeout(autoTimer);
        autoTimer = setTimeout(function () {
            autoTimer = null;
            query(true);
        }, 180);
    }

    // 违规时间：日期一行、时分一行，比一长串挤在一起好读
    function timeCell(value) {
        var text = (value || "").trim();
        if (!text) return '<td class="vio-time"><span class="vio-muted">—</span></td>';
        var parts = text.split(" ");
        return '<td class="vio-time">'
            + '<span class="vio-date">' + Dash.escapeHtml(parts[0]) + "</span>"
            + (parts[1] ? '<span class="vio-clock">' + Dash.escapeHtml(parts[1]) + "</span>" : "")
            + "</td>";
    }

    // 带「N天/N个自然日」的处罚，取最大的天数（一段处罚里可能有多个数字）
    function penaltyMaxDays(item) {
        var matches = String(item || "").match(/\d+\s*(?:个\s*自\s*然\s*日|天)/g);
        if (!matches) return 0;
        var days = 0;
        matches.forEach(function (m) {
            var digits = /^\d+/.exec(m);
            var n = digits ? parseInt(digits[0], 10) : 0;
            if (n > days) days = n;
        });
        return days;
    }

    // 处罚到期时间 = 违规时间 + 处罚天数（多种处罚取最晚的到期日）
    function dueDateText(row) {
        var text = (row.penalty || row.penalty_category || "").trim();
        if (!text || !row.violated_at) return "";
        var parts = String(row.violated_at).split(" ")[0].split("-");
        if (parts.length !== 3) return "";
        var at = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
        if (isNaN(at.getTime())) return "";
        var chips = text.split(/[;；、]/);
        var maxDays = 0;
        chips.forEach(function (item) {
            var days = penaltyMaxDays(item);
            if (days > maxDays) maxDays = days;
        });
        if (!maxDays) return "";
        at.setDate(at.getDate() + maxDays);
        return fmtDate(at);
    }

    function penaltyCell(row) {
        var text = (row.penalty || row.penalty_category || "").trim();
        if (!text) return '<td class="vio-penalty"><span class="vio-muted">—</span></td>';
        var chips = text.split(/[;；]/).map(function (item) { return item.trim(); }).filter(function (item) { return item; });
        return '<td class="vio-penalty">' + chips.map(function (item) {
            return '<span class="vio-chip">' + Dash.escapeHtml(item) + "</span>";
        }).join("") + "</td>";
    }

    // 到期状态：处罚还在生效 → "active"，已到期/解除 → "passed"，没有天数 → ""
    // 优先用后端算好的 due_date（到期筛选也在后端做），没有再按处罚文本现算
    function dueState(row) {
        var due = row.due_date || dueDateText(row);
        if (!due) return "";
        var parts = due.split("-");
        var at = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
        if (isNaN(at.getTime())) return "";
        var today = new Date();
        today.setHours(0, 0, 0, 0);
        return at.getTime() < today.getTime() ? "passed" : "active";
    }

    // 处罚到期时间单独一列：小于今天（处罚已到期/解除）标绿色，大于等于今天（生效中）标红色
    function dueCell(row) {
        var due = row.due_date || dueDateText(row);
        if (!due) return '<td class="vio-due"><span class="vio-muted">—</span></td>';
        var state = dueState(row);
        var tone = state === "passed" ? " vio-due-passed" : state === "active" ? " vio-due-active" : "";
        return '<td class="vio-due' + tone + '">' + Dash.escapeHtml(due) + "</td>";
    }

    function statusCell(status) {
        var text = (status || "").trim();
        if (!text) return '<td class="vio-status"><span class="vio-muted">—</span></td>';
        var tone = "vio-tag-muted";
        if (text.indexOf("待") === 0) tone = "vio-tag-warn";
        else if (text.indexOf("撤销") < 0) tone = "vio-tag-ok";
        return '<td class="vio-status"><span class="vio-tag ' + tone + '">' + Dash.escapeHtml(text) + "</span></td>";
    }

    function paint() {
        var rows = items.slice();
        rows.sort(function (left, right) {
            var a = left.violated_at || "";
            var b = right.violated_at || "";
            return a < b ? sortDir : a > b ? -sortDir : 0;
        });
        var revoked = 0;
        rows.forEach(function (row) {
            if ((row.status || "").indexOf("撤销") >= 0) revoked += 1;
        });
        el("vio-count").innerHTML = "当前 <b class=\"vio-count-num\">" + rows.length + "</b> 条"
            + (revoked ? "，<b class=\"vio-count-num\">" + revoked + "</b> 条违规已撤销" : "")
            + "。";
        // 状态下拉不写死：当前结果里出现过的状态就是选项（页面有什么就有什么）
        var statusSet = [];
        rows.forEach(function (row) {
            var value = (row.status || "").trim();
            if (value && statusSet.indexOf(value) < 0) statusSet.push(value);
        });
        statusSet.sort();
        fillSelect(el("vio-status"), statusSet);
        if (!rows.length) {
            body.innerHTML = '<tr><td colspan="8" class="empty">没有查到违规记录。抖店后台这批店铺在当前时间范围内没有违规，可换个时间范围或点「拉取最新数据」重新核对。</td></tr>';
            return;
        }
        body.innerHTML = rows.map(function (row) {
            return "<tr>"
                + "<td class=\"vio-shop\">" + Dash.escapeHtml(row.shop_name || row.shop_id) + "</td>"
                + timeCell(row.violated_at)
                + "<td class=\"vio-id\">" + Dash.escapeHtml(row.violation_id || "—") + "</td>"
                + "<td class=\"vio-type\" title=\"" + Dash.escapeHtml(row.violation_type_raw || row.violation_type || "") + "\">"
                + Dash.escapeHtml(row.violation_type || "—") + "</td>"
                + penaltyCell(row)
                + dueCell(row)
                + "<td class=\"vio-desc\" title=\"" + Dash.escapeHtml(row.description_full || row.description || "") + "\">"
                + Dash.escapeHtml(row.description || "—") + "</td>"
                + statusCell(row.status)
                + "</tr>";
        }).join("");
        markSort();
    }

    function markSort() {
        var head = document.querySelector("#vio-table th.sortable");
        if (!head) return;
        head.classList.remove("sort-asc", "sort-desc");
        head.classList.add(sortDir > 0 ? "sort-asc" : "sort-desc");
    }

    function refresh() {
        var button = el("btn-refresh-violations");
        button.disabled = true;
        Dash.request("/violations/refresh", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                start_date: el("vio-start").value || "",
                end_date: el("vio-end").value || "",
            }),
        }).then(function () {
            watch(function (state) {
                button.disabled = false;
                paintSchedule(state);
                loadOptions();
                query();
                var failed = (state && state.failed) || 0;
                var message = (state && state.message) || "拉取完成";
                Dash.showToast(message, failed ? "error" : "success");
            });
        }).catch(function (err) {
            button.disabled = false;
            Dash.showToast(err.message || "拉取失败", "error");
        });
    }

    function load() {
        if (!loadedOnce) {
            loadedOnce = true;
            defaultDates();
            loadShops();
            loadOptions();
            Dash.request("/violations/progress").then(function (state) {
                paintSchedule(state);
                paintProgress(state);
                if (state.running) followRunning();
            }).catch(function () {});
            pollSchedule();
        }
        if (focusPk && shopBoxes().length) applyFocus();
        else if (!focusPk) query();
    }

    // 店铺下拉：点按钮开合，点页面别处收起
    el("vio-shop-btn").addEventListener("click", function (event) {
        event.stopPropagation();
        var panel = el("vio-shop-panel");
        var willOpen = panel.classList.contains("hidden");
        panel.classList.toggle("hidden", !willOpen);
        el("vio-shop-btn").setAttribute("aria-expanded", willOpen ? "true" : "false");
    });
    el("vio-shop-panel").addEventListener("click", function (event) {
        event.stopPropagation();
    });
    document.addEventListener("click", closeShopPanel);

    // 全部勾选：一次选中/取消所有店铺
    el("vio-shop-all").addEventListener("change", function () {
        var checked = el("vio-shop-all").checked;
        shopBoxes().forEach(function (box) { box.checked = checked; });
        syncShopUI();
        clearPinned();
        autoApply();
    });
    el("vio-shop-list").addEventListener("change", function (event) {
        if (event.target && event.target.type === "checkbox") {
            syncShopUI();
            clearPinned();
            autoApply();
        }
    });

    el("btn-query-violations").addEventListener("click", function () {
        clearPinned();
        query(false);
    });
    el("btn-refresh-violations").addEventListener("click", refresh);
    // 四个下拉：选完立刻按新条件刷新表格和后面的选项
    ["vio-account", "vio-type", "vio-penalty", "vio-status"].forEach(function (id) {
        el(id).addEventListener("change", function () {
            clearPinned();
            autoApply();
        });
    });
    // 处罚到期筛选在后端做，可能要等一下：显示转圈提示，免得用户以为卡死了
    el("vio-due").addEventListener("change", function () {
        clearPinned();
        query(false);
    });
    // 时间下拉一改就把起止日期跟着换掉，然后自动查
    el("vio-range").addEventListener("change", function () {
        defaultDates();
        clearPinned();
        autoApply();
    });
    // 日期框按你的要求保持手动：改完点「查询」。按钮会亮一下提醒还没查
    ["vio-start", "vio-end"].forEach(function (id) {
        el(id).addEventListener("change", function () {
            clearPinned();
            el("btn-query-violations").classList.add("is-dirty");
        });
    });
    document.querySelector("#vio-table th.sortable").addEventListener("click", function () {
        sortDir = -sortDir;
        paint();
    });

    window.Vio = {
        load: load,
        openShop: function (storePk, ticketIds) {
            focusPk = String(storePk || "");
            focusIds = ticketIds == null ? "" : String(ticketIds);
            Dash.showView("violations");
        },
    };
})();
