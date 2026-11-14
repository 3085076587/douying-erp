(function () {
    "use strict";

    var bell = document.getElementById("notif-bell");
    var badge = document.getElementById("notif-badge");
    if (!bell || !badge) return;

    function refresh() {
        Dash.request("/stores").then(function (data) {
            var items = data.items || [];
            var offline = items.filter(function (row) {
                return (row.login_status || "") !== "online";
            }).length;
            bell.classList.remove("hidden");
            if (offline > 0) {
                badge.textContent = offline > 99 ? "99+" : String(offline);
                badge.classList.remove("hidden");
                bell.title = "有 " + offline + " 家店铺掉线，点击查看";
            } else {
                badge.classList.add("hidden");
                bell.title = "没有掉线店铺";
            }
        }).catch(function () {
            bell.classList.add("hidden");
        });
    }

    bell.addEventListener("click", function () {
        Dash.showView("stores");
        var select = document.getElementById("store-login");
        if (select) {
            select.value = "offline";
            select.dispatchEvent(new Event("change"));
        }
    });

    refresh();
    setInterval(refresh, 60000);
})();
