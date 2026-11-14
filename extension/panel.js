(function () {
  function readCache() {
    try {
      return {
        running: sessionStorage.getItem("dy-erp-cred-shield") === "1",
        detail: sessionStorage.getItem("dy-erp-cred-detail") || "",
        progress: JSON.parse(sessionStorage.getItem("dy-erp-cred-progress") || "null"),
      };
    } catch (err) {
      return { running: false, detail: "", progress: null };
    }
  }

  function ensureVeil() {
    const parent = document.documentElement || document.body;
    if (!parent) return;
    let veil = document.getElementById("dy-erp-cred-veil");
    if (!veil) {
      veil = document.createElement("div");
      veil.id = "dy-erp-cred-veil";
      parent.appendChild(veil);
    }
    veil.style.setProperty("position", "fixed", "important");
    veil.style.setProperty("left", "0", "important");
    veil.style.setProperty("top", "0", "important");
    veil.style.setProperty("right", "0", "important");
    veil.style.setProperty("bottom", "0", "important");
    veil.style.setProperty("width", "100vw", "important");
    veil.style.setProperty("height", "100vh", "important");
    veil.style.setProperty("margin", "0", "important");
    veil.style.setProperty("background", "rgba(90, 90, 90, 0.55)", "important");
    const through = !!window.__dyErpClickThrough;
    veil.style.setProperty("z-index", "2147483646", "important");
    veil.style.setProperty("pointer-events", through ? "none" : "auto", "important");
    veil.style.setProperty("cursor", "not-allowed", "important");
    const panel = document.getElementById("dy-erp-cred-panel");
    if (panel) panel.style.setProperty("pointer-events", "auto", "important");
    if (!veil.dataset.bound) {
      veil.dataset.bound = "1";
      ["pointerdown", "pointerup", "click", "auxclick", "contextmenu", "wheel"].forEach(function (name) {
        veil.addEventListener(name, function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
        });
      });
    }
  }

  function removeVeil() {
    const veil = document.getElementById("dy-erp-cred-veil");
    if (veil) veil.remove();
    try {
      sessionStorage.removeItem("dy-erp-cred-shield");
      sessionStorage.removeItem("dy-erp-cred-detail");
      sessionStorage.removeItem("dy-erp-cred-progress");
    } catch (err) {
      /* 清不掉就等这次获取结束再关 */
    }
  }

  const cached = readCache();
  if (cached.running) ensureVeil();

  function boot(initialDetail) {
    const live = document.getElementById("dy-erp-cred-panel");
    if (window.__dyErpPanel && live) {
      window.__dyErpPanel.show();
      if (initialDetail) window.__dyErpPanel.showStatus(initialDetail);
      return;
    }
    window.__dyErpPanel = null;
    if (live) live.remove();
    const stale = document.getElementById("dy-erp-cred-panel");
    if (stale) stale.remove();

    const box = document.createElement("div");
    box.id = "dy-erp-cred-panel";
    box.style.setProperty("position", "fixed", "important");
    box.style.setProperty("z-index", "2147483647", "important");
    box.style.setProperty("width", "280px", "important");
    box.style.setProperty("margin", "0", "important");
    box.style.setProperty("transform", "none", "important");
    box.style.setProperty("visibility", "hidden", "important");
    const root = box.attachShadow({ mode: "open" });
    root.innerHTML = [
      "<style>",
      ".bar { cursor: move; padding: 8px 32px 8px 12px; background: #1677ff; color: #fff;",
      "font: 13px/20px 'Microsoft YaHei', sans-serif; border-radius: 8px 8px 0 0; }",
      ".x { position: absolute; right: 6px; top: 4px; width: 24px; height: 24px; border: 0;",
      "background: transparent; color: #fff; font: 18px/24px sans-serif; cursor: pointer; }",
      ".body { padding: 12px; background: #fff; border: 1px solid #d0d3d6; border-top: 0;",
      "border-radius: 0 0 8px 8px; box-shadow: 0 8px 24px rgba(0,0,0,.18);",
      "font: 13px/1.4 'Microsoft YaHei', sans-serif; color: #1f2329; }",
      "label { display: block; margin: 8px 0 4px; color: #646a73; }",
      "input { width: 100%; box-sizing: border-box; padding: 6px 8px; }",
      "button.go { width: 100%; margin-top: 12px; padding: 8px; border: 0; border-radius: 4px;",
      "background: #1677ff; color: #fff; cursor: pointer; }",
      "button.go:disabled { background: #b7c3d0; }",
      "button.stop { width: 100%; margin-top: 8px; padding: 8px; border: 0; border-radius: 4px;",
      "background: #d4380d; color: #fff; cursor: pointer; }",
      "button.stop:disabled { background: #b7c3d0; }",
      ".track { height: 8px; margin-top: 12px; background: #e8eaed; border-radius: 4px; overflow: hidden; }",
      ".fill { height: 100%; width: 0; background: #1677ff; }",
      ".fill.indeterminate { width: 35%; animation: dy-erp-slide 1.1s ease-in-out infinite; }",
      "@keyframes dy-erp-slide { from { margin-left: 0; } to { margin-left: 65%; } }",
      ".count { margin: 6px 0 0; color: #1f2329; }",
      "p { min-height: 18px; margin: 8px 0 0; color: #646a73; word-break: break-all; }",
      "a { color: #1677ff; cursor: pointer; }",
      "</style>",
      "<div class='bar'>获取凭证</div>",
      "<button class='x' type='button' title='关闭'>×</button>",
      "<div class='body'>",
      "<div class='login'>",
      "<label>账号</label><input class='user' autocomplete='username'>",
      "<label>密码</label><input class='pass' type='password' autocomplete='current-password'>",
      "</div>",
      "<p class='logged' hidden>已登录 <a class='switch'>换账号</a></p>",
      "<label>归属账号</label><input class='owner' autocomplete='off'>",
      "<button class='go' type='button'>获取凭证</button>",
      "<button class='stop' type='button' hidden>停止</button>",
      "<div class='track' hidden><div class='fill'></div></div>",
      "<p class='count' hidden></p>",
      "<p class='status'></p>",
      "</div>",
    ].join("");

    const bar = root.querySelector(".bar");
    const closer = root.querySelector(".x");
    const loginBox = root.querySelector(".login");
    const logged = root.querySelector(".logged");
    const switchLink = root.querySelector(".switch");
    const usernameInput = root.querySelector(".user");
    const passwordInput = root.querySelector(".pass");
    const ownerInput = root.querySelector(".owner");
    const button = root.querySelector(".go");
    const stop = root.querySelector(".stop");
    const track = root.querySelector(".track");
    const fill = root.querySelector(".fill");
    const count = root.querySelector(".count");
    const status = root.querySelector(".status");
    (document.documentElement || document.body).appendChild(box);

    function showLogin(needLogin) {
      loginBox.hidden = !needLogin;
      logged.hidden = needLogin;
    }

    function show(text) {
      status.textContent = text || "";
    }

    function apply(detail, progress, running) {
      show(detail || "");
      const total = progress && Number(progress.total);
      const current = progress && Number(progress.current);
      if (running) {
        try {
          sessionStorage.setItem("dy-erp-cred-shield", "1");
          sessionStorage.setItem("dy-erp-cred-detail", detail || "");
          sessionStorage.setItem("dy-erp-cred-progress", JSON.stringify(progress || null));
        } catch (err) {
          /* 记不住就靠后台在跳页前再写一次 */
        }
        ensureVeil();
        closer.hidden = true;
        switchLink.hidden = true;
        ownerInput.disabled = true;
        button.disabled = true;
        stop.hidden = false;
        track.hidden = false;
        if (total > 0) {
          fill.classList.remove("indeterminate");
          const pct = Math.max(4, Math.min(100, Math.round((current || 0) / total * 100)));
          fill.style.width = pct + "%";
          count.hidden = false;
          count.textContent = (current || 0) + " / " + total;
        } else {
          fill.classList.add("indeterminate");
          fill.style.width = "";
          count.hidden = true;
          count.textContent = "";
        }
      } else {
        removeVeil();
        closer.hidden = false;
        switchLink.hidden = false;
        ownerInput.disabled = false;
        button.disabled = false;
        stop.hidden = true;
        stop.disabled = false;
        track.hidden = true;
        count.hidden = true;
        fill.classList.remove("indeterminate");
      }
    }

    function moveTo(viewLeft, viewTop) {
      const maxW = Math.max(160, window.innerWidth);
      box.style.setProperty("width", Math.min(280, maxW) + "px", "important");
      const rect = box.getBoundingClientRect();
      const maxL = Math.max(0, window.innerWidth - rect.width);
      const maxT = Math.max(0, window.innerHeight - rect.height);
      const nextL = Math.min(maxL, Math.max(0, viewLeft));
      const nextT = Math.min(maxT, Math.max(0, viewTop));
      const curL = parseFloat(box.style.left) || 0;
      const curT = parseFloat(box.style.top) || 0;
      box.style.setProperty("left", curL + nextL - rect.left + "px", "important");
      box.style.setProperty("top", curT + nextT - rect.top + "px", "important");
      box.style.setProperty("right", "auto", "important");
      box.style.setProperty("bottom", "auto", "important");
    }

    function savedPos() {
      try {
        return JSON.parse(sessionStorage.getItem("dy-erp-cred-pos") || "null");
      } catch (err) {
        return null;
      }
    }

    function rememberPos() {
      const rect = box.getBoundingClientRect();
      try {
        sessionStorage.setItem("dy-erp-cred-pos", JSON.stringify({ left: rect.left, top: rect.top }));
      } catch (err) {
        /* 记不住位置就下次还放在页面左上角 */
      }
    }

    const saved = savedPos();
    box.style.setProperty("left", "12px", "important");
    box.style.setProperty("top", "72px", "important");
    moveTo(saved && Number.isFinite(saved.left) ? saved.left : 12, saved && Number.isFinite(saved.top) ? saved.top : 72);
    box.style.setProperty("visibility", "visible", "important");

    let drag = null;
    bar.addEventListener("pointerdown", function (ev) {
      if (ev.button !== 0) return;
      const rect = box.getBoundingClientRect();
      drag = { x: ev.clientX, y: ev.clientY, left: rect.left, top: rect.top };
      bar.setPointerCapture(ev.pointerId);
      ev.preventDefault();
    });
    bar.addEventListener("pointermove", function (ev) {
      if (!drag) return;
      moveTo(drag.left + ev.clientX - drag.x, drag.top + ev.clientY - drag.y);
    });
    function endDrag() {
      if (!drag) return;
      drag = null;
      rememberPos();
    }
    bar.addEventListener("pointerup", endDrag);
    bar.addEventListener("pointercancel", endDrag);
    window.addEventListener("resize", function () {
      if (box.style.display === "none") return;
      const rect = box.getBoundingClientRect();
      moveTo(rect.left, rect.top);
    });

    closer.addEventListener("click", function () {
      if (closer.hidden) return;
      chrome.storage.session.set({ showPanel: false, jobRunning: false });
      removeVeil();
      box.remove();
      window.__dyErpPanel = null;
    });

    window.__dyErpPanel = {
      show: function () {
        box.style.setProperty("display", "block", "important");
        const again = savedPos();
        const rect = box.getBoundingClientRect();
        const left = rect.width ? rect.left : (again && again.left) || 12;
        const top = rect.height ? rect.top : (again && again.top) || 72;
        moveTo(left, top);
      },
      showStatus: show,
      apply: apply,
    };
    if (initialDetail) show(initialDetail);

    function tokenAlive(token) {
      const parts = String(token || "").split("|");
      if (parts.length !== 4) return false;
      const expires = Number(parts[2]);
      return Number.isFinite(expires) && expires > Date.now() / 1000 + 30;
    }

    try {
      const kept = sessionStorage.getItem("dy-erp-cred-owner") || "";
      if (kept) ownerInput.value = kept;
    } catch (err) {
      /* 这一页记不住就等本地存储 */
    }
    chrome.storage.local.get(["username", "token", "ownerAccount"], function (saved) {
      if (saved && saved.username) usernameInput.value = saved.username;
      if (saved && saved.ownerAccount && !ownerInput.value) ownerInput.value = saved.ownerAccount;
      showLogin(!(saved && tokenAlive(saved.token)));
    });

    switchLink.addEventListener("click", function () {
      if (switchLink.hidden) return;
      chrome.storage.local.remove("token", function () {
        showLogin(true);
        show("");
      });
    });

    function askHalt(ev) {
      if (stop.hidden || stop.disabled) return;
      if (ev) {
        ev.preventDefault();
        ev.stopPropagation();
      }
      stop.disabled = true;
      show("正在停止");
      chrome.runtime.sendMessage({ type: "stop" }, function () {
        void chrome.runtime.lastError;
      });
    }
    stop.addEventListener("pointerdown", askHalt);
    stop.addEventListener("click", askHalt);

    button.addEventListener("click", function () {
      const owner = ownerInput.value.trim().slice(0, 100);
      if (!owner) {
        show("归属账号不能为空");
        return;
      }
      ownerInput.value = owner;
      try {
        sessionStorage.setItem("dy-erp-cred-owner", owner);
      } catch (err) {
        /* 记不住也先用这次输入的 */
      }
      chrome.storage.local.set({ ownerAccount: owner });
      button.disabled = true;
      apply("正在获取当前店铺", null, true);
      let port;
      try {
        port = chrome.runtime.connect({ name: "capture" });
      } catch (err) {
        apply("没有连上，再点一次获取凭证", null, false);
        return;
      }
      try {
        let heard = false;
        port.onMessage.addListener(function (msg) {
          heard = true;
          try {
            if (!msg) return;
            if (msg.needLogin) showLogin(true);
            else if (msg.done) showLogin(false);
            if (msg.needLogin !== true) passwordInput.value = "";
            if (msg.detail) show(msg.detail);
            if (msg.done) {
              apply(msg.detail || "", null, false);
              port.disconnect();
            }
          } catch (err) {
            apply("没有连上，再点一次获取凭证", null, false);
          }
        });
        port.onDisconnect.addListener(function () {
          try {
            void chrome.runtime.lastError;
          } catch (err) {
            /* 页面跳走时这条连接会断，后台还在接着获取 */
          }
          if (!heard && button.disabled) {
            apply("没有连上，再点一次获取凭证", null, false);
          }
        });
        port.postMessage({
          type: "begin",
          username: usernameInput.value.trim(),
          password: passwordInput.value,
          account: owner,
        });
      } catch (err) {
        apply("没有连上，再点一次获取凭证", null, false);
      }
    });
  }

  function pull() {
    chrome.storage.session.get(["showPanel", "jobDetail", "jobProgress", "jobRunning"], function (saved) {
      const running = !!(saved && saved.jobRunning);
      const showPanel = !!(saved && saved.showPanel);
      if (!running && !showPanel) return;
      const detail = (saved && saved.jobDetail) || "";
      if (!document.getElementById("dy-erp-cred-panel")) boot(detail);
      if (window.__dyErpPanel && window.__dyErpPanel.apply) {
        window.__dyErpPanel.apply(detail, (saved && saved.jobProgress) || null, running);
      }
    });
  }

  if (!window.__dyErpPanelWatch) {
    window.__dyErpPanelWatch = true;
    chrome.storage.session.onChanged.addListener(function (changes) {
      if (!changes.jobDetail && !changes.jobProgress && !changes.jobRunning && !changes.showPanel) return;
      pull();
    });
  }

  if (window.__dyErpForcePanel) {
    window.__dyErpForcePanel = false;
    boot("");
    pull();
    return;
  }

  if (cached.running) {
    boot(cached.detail);
    if (window.__dyErpPanel && window.__dyErpPanel.apply) {
      window.__dyErpPanel.apply(cached.detail, cached.progress, true);
    }
  }
  pull();
})();
