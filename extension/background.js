/* 抖店账号导入：登录那套保持原样，这里只重写「获取凭证」这一段。
 *
 * 旧写法的问题：
 *   1. 店铺名必须带「严选」，换个店名就认不出来；
 *   2. ensureShopModal 每 450ms 重新点一次「切换组织/店铺」，菜单被点开又点关；
 *   3. 判断有没有进店靠 cookie 变化，切店时 cookie 未必变；
 *   4. 点按钮只发 mousedown/mouseup/click，React 有些节点需要 pointer 事件。
 *
 * 新写法：所有点击在一段页面内代码里一次做完（不在后台反复轮询），
 *         店铺优先用「店铺ID」定位，名字只做兜底。
 */

const API_BASE = "http://120.76.58.132:8082";
const STORE_DOMAIN = "jinritemai.com";

let job = { state: "idle", detail: "", progress: null };
let runId = 0;
let stopId = 0;

function askStop() {
  stopId = runId > 0 ? runId : -1;
}

function halt() {
  return stopId !== 0 && (stopId === runId || stopId === -1);
}
let panelTabId = 0;
let jobSeq = 0;

function sleep(ms) {
  return new Promise(function (resolve) {
    setTimeout(function () {
      try {
        chrome.runtime.getPlatformInfo(function () {
          resolve();
        });
      } catch (err) {
        resolve();
      }
    }, ms);
  });
}

/* ===================== 下面到 ensureToken 为止：原样保留 ===================== */

function tokenAlive(token) {
  const parts = String(token || "").split("|");
  if (parts.length !== 4) return false;
  const expires = Number(parts[2]);
  return Number.isFinite(expires) && expires > Date.now() / 1000 + 30;
}

async function readDashboardToken() {
  const allowed = await chrome.permissions.contains({ origins: [API_BASE + "/*"] });
  if (!allowed) return "";
  const tabs = await chrome.tabs.query({ url: API_BASE + "/*" });
  for (let i = 0; i < tabs.length; i += 1) {
    if (!tabs[i].id) continue;
    try {
      const [injected] = await chrome.scripting.executeScript({
        target: { tabId: tabs[i].id },
        func: function () {
          return localStorage.getItem("erp_access_token") || "";
        },
      });
      const token = injected && injected.result;
      if (tokenAlive(token)) return token;
    } catch (err) {
      /* 这个后台标签读不到，换下一个 */
    }
  }
  return "";
}

async function ensureToken(username, password) {
  const saved = await chrome.storage.local.get(["token", "username"]);
  if (tokenAlive(saved.token)) return { token: saved.token };
  const dash = await readDashboardToken();
  if (tokenAlive(dash)) {
    await chrome.storage.local.set({ token: dash });
    return { token: dash };
  }
  const name = String(username || "").trim();
  if (!name || !password) return { error: "请填写账号和密码", needLogin: true };
  const allowed = await chrome.permissions.contains({ origins: [API_BASE + "/*"] });
  if (!allowed) return { error: "需要允许访问后台", needLogin: true };
  let response;
  try {
    response = await fetch(API_BASE + "/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: name, password: password }),
    });
  } catch (err) {
    return { error: "登录失败", needLogin: true };
  }
  if (response.status === 401) return { error: "账号或密码错误", needLogin: true };
  if (!response.ok) return { error: "登录失败", needLogin: true };
  const data = await response.json();
  const token = data.access_token || "";
  if (!tokenAlive(token)) return { error: "请填写账号和密码", needLogin: true };
  await chrome.storage.local.set({ token: token, username: name });
  return { token: token };
}

/* ===================== 页面内执行：所有动作都在这一个函数里 ===================== */

async function pageRun(action, payload) {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const norm = (s) => String(s || "").replace(/[\u200b\u00a0\s]/g, "").replace(/／/g, "/");
  const text = () => String((document.body && document.body.innerText) || "");
  const skip = /^(店铺信息|切换组织\/店铺|退出|返回|管理|更多|查看|收起导航|请选择店铺|选择店铺|正常营业|已关店|已清退|营业中|主账号|子账号)$/;
  const bareShop = /^(正常营业|已关店|已清退|营业中|停业整顿|个体店|企业店|旗舰店|专卖店|专营店|官方店|个人店|抖店工作台|登录中)$/;
  const shopType = /(个体|企业|旗舰|专卖|专营|官方|个人)店/;
  const headerJunk = /通知中心|飞鸽|下载|更多|搜索|学习中心|服务市场|电商罗盘|精选联盟|巨量|获取凭证|快速发布|去发品|发图文|无货源/;
  const badgeBits = /(个体店|企业店|旗舰店|专卖店|专营店|官方店|个人店|正常营业|已关店|已清退|营业中|停业整顿|子账号|主账号|抖店工作台|登录中)/g;
  const nameLine = (value) => {
    const clean = norm(value);
    if (!clean || clean.length < 2 || clean.length > 32) return false;
    if (skip.test(clean) || bareShop.test(clean) || headerJunk.test(clean)) return false;
    if (!clean.replace(badgeBits, "")) return false;
    return /[\u4e00-\u9fa5A-Za-z]/.test(clean);
  };

  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none" && Number(cs.opacity) !== 0;
  };
  const visibleExact = (label) => {
    const key = norm(label);
    for (const el of document.querySelectorAll("div,span,p,h1,h2,h3,a,button")) {
      if (norm(el.innerText || "") !== key) continue;
      if (vis(el)) return el;
    }
    return null;
  };
  const hasPicker = () => {
    if (visibleExact("请选择店铺")) return true;
    for (const el of document.querySelectorAll("div,span,p,h1,h2,h3")) {
      const t = norm(el.innerText || "");
      if (t.indexOf("请选择店铺") < 0 || t.length > 12) continue;
      if (vis(el)) return true;
    }
    return false;
  };
  const hasCaptcha = () => /滑块|向右滑动|拖动滑块|按住左边按钮|安全验证/.test(text());
  const onLogin = () => /\/login/.test(location.pathname) && location.pathname.indexOf("/ffa/") < 0;
  const ready = () => !hasPicker() && !onLogin() && /店铺ID|经营数据|商品管理/.test(text());
  const headerBlock = () => {
    let block = null;
    let best = Infinity;
    for (const el of document.querySelectorAll("div,span,a,button")) {
      if (!vis(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.top > 130 || r.bottom < 0 || r.left < window.innerWidth * 0.5) continue;
      if (r.width < 40 || r.width > 420 || r.height > 96) continue;
      const blob = norm(el.innerText || "");
      if (!blob || blob.length > 48 || headerJunk.test(blob) || !shopType.test(blob)) continue;
      if (!nameIn(el)) continue;
      const area = r.width * r.height;
      if (area < best) {
        block = el;
        best = area;
      }
    }
    return block;
  };
  const nameIn = (root) => {
    if (!root) return null;
    let found = null;
    let best = Infinity;
    const nodes = [root].concat(Array.from(root.querySelectorAll ? root.querySelectorAll("div,span,a,button") : []));
    for (const el of nodes) {
      if (!vis(el)) continue;
      const blob = norm(el.innerText || "");
      if (!nameLine(blob)) continue;
      const r = el.getBoundingClientRect();
      const area = r.width * r.height;
      if (area < best) {
        found = el;
        best = area;
      }
    }
    return found;
  };
  const headerName = () => {
    const named = nameIn(headerBlock());
    return named ? norm(named.innerText || "") : "";
  };

  const press = async (el) => {
    const r = el.getBoundingClientRect();
    const box = {
      bubbles: true, cancelable: true, composed: true, view: window,
      clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0,
    };
    const seq = [
      ["pointerover", PointerEvent], ["pointerenter", PointerEvent],
      ["pointerdown", PointerEvent], ["mousedown", MouseEvent],
      ["pointerup", PointerEvent], ["mouseup", MouseEvent],
      ["click", MouseEvent],
    ];
    for (const pair of seq) {
      const C = typeof pair[1] === "function" ? pair[1] : MouseEvent;
      try {
        el.dispatchEvent(new C(pair[0], box));
      } catch (err) {
        el.dispatchEvent(new MouseEvent(pair[0], box));
      }
    }
    await wait(90);
  };

  // 从最里层往上打几层：React 的事件常挂在容器上
  const pressUp = async (el, layers) => {
    let node = el;
    const list = [];
    for (let i = 0; i < (layers || 3) && node; i += 1, node = node.parentElement) {
      if (vis(node)) list.push(node);
    }
    for (const item of list) {
      try {
        item.scrollIntoView({ block: "center" });
      } catch (err) {
        /* 滚不动也不影响点击 */
      }
      await press(item);
      if (action === "enter" && !hasPicker()) return; // 已经跳走了，别再补点
    }
  };

  const deepest = (want, maxLen) => {
    const key = norm(want);
    let best = null;
    let area = Infinity;
    for (const el of document.querySelectorAll("div,span,p,li,a,button")) {
      if (!vis(el)) continue;
      const t = norm(el.innerText || el.textContent);
      if (!t || t.indexOf(key) < 0) continue;
      if (maxLen && t.length > maxLen) continue;
      const r = el.getBoundingClientRect();
      const a = r.width * r.height;
      if (a < area) {
        best = el;
        area = a;
      }
    }
    return best;
  };

  const shopIdHere = () => {
    const fromUrl = location.href.match(/[?&]shop_?id=(\d{6,20})/i);
    if (fromUrl) return fromUrl[1];
    const fromText = text().replace(/\s+/g, "").match(/店铺ID[:：]?(\d{6,20})/);
    if (fromText) return fromText[1];
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      const value = localStorage.getItem(key) || "";
      if (/shop_?id/i.test(key) && /^\d{6,20}$/.test(value)) return value;
      const nested = value.match(/"shop_?id"\s*:\s*"?(\d{6,20})/i);
      if (nested) return nested[1];
    }
    return "";
  };

  const emailHere = () => {
    const mail = /[A-Za-z0-9._%+-]{3,64}@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}/g;
    const image = /\.(png|jpe?g|gif|webp|svg|ico|bmp|avif)$/i;
    for (let i = 0; i < localStorage.length; i += 1) {
      const value = localStorage.getItem(localStorage.key(i)) || "";
      const found = value.match(mail) || [];
      for (let n = 0; n < found.length; n += 1) {
        const hit = found[n];
        if (hit.indexOf("*") >= 0 || image.test(hit)) continue;
        return hit;
      }
    }
    return "";
  };

  const localAll = () => {
    const out = {};
    for (let i = 0; i < localStorage.length && i < 60; i += 1) {
      const key = localStorage.key(i);
      const value = localStorage.getItem(key) || "";
      if (!key || value.length > 4000) continue;
      out[key] = value;
    }
    return out;
  };

  if (action === "snapshot") {
    return {
      login: onLogin(),
      picker: hasPicker(),
      captcha: hasCaptcha(),
      ready: ready(),
      shop_id: shopIdHere(),
      header: headerName(),
      menu: !!visibleExact("退出"),
      can_switch: text().indexOf("切换组织") >= 0,
      account: emailHere(),
      local_storage: localAll(),
      ua: navigator.userAgent,
    };
  }

  const accountChip = () => {
    const named = nameIn(headerBlock());
    if (!named) return null;
    const link = named.closest && named.closest("a");
    const href = link ? (link.getAttribute("href") || "") : "";
    if (/login|account/.test(href)) return null;
    return named;
  };
  const switchEntry = () => {
    const key = "切换组织/店铺";
    let best = null;
    let area = Infinity;
    const consider = (el) => {
      let node = el;
      for (let i = 0; i < 5 && node; i += 1, node = node.parentElement) {
        if (!vis(node)) continue;
        const r = node.getBoundingClientRect();
        if (r.bottom < 8 || r.top > window.innerHeight - 8) continue;
        if (r.height > 80 || r.width > 520) continue;
        const a = r.width * r.height;
        if (a < area) {
          best = node;
          area = a;
        }
        break;
      }
    };
    const root = document.body || document.documentElement;
    if (root) {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        if (norm(walker.currentNode.nodeValue) !== key) continue;
        consider(walker.currentNode.parentElement);
      }
    }
    if (best) return best;
    for (const el of document.querySelectorAll("div,span,a,button,li,p")) {
      const own = el.children.length ? "" : norm(el.textContent || "");
      const t = own || norm(el.innerText || "");
      if (t.indexOf("切换组织") < 0 || t.indexOf("店铺") < 0) continue;
      if (t.length > key.length + 16) continue;
      consider(el);
    }
    return best;
  };

  const switchPoint = () => {
    const key = "切换组织/店铺";
    let best = null;
    let area = Infinity;
    const take = (rect) => {
      if (!rect || rect.width < 20 || rect.height < 10 || rect.height > 90) return;
      if (rect.bottom < 8 || rect.top > window.innerHeight - 8) return;
      const a = rect.width * rect.height;
      if (a >= area) return;
      best = rect;
      area = a;
    };
    const lineOk = (text) => {
      const lines = String(text || "").split(/\n/).map(norm).filter(Boolean);
      return lines.some((line) => line === key || (line.indexOf(key) === 0 && line.length <= key.length + 4));
    };
    const scan = (root) => {
      if (!root || !root.querySelectorAll) return;
      for (const el of root.querySelectorAll("div,span,a,button,li,p")) {
        if (!lineOk(el.innerText || el.textContent || "")) continue;
        take(el.getBoundingClientRect());
      }
    };
    const root = document.body || document.documentElement;
    scan(root);
    if (!best && root) {
      for (const el of root.querySelectorAll("*")) {
        if (!el.shadowRoot) continue;
        scan(el.shadowRoot);
        if (best) break;
      }
    }
    if (!best) return null;
    return {
      x: best.left + Math.min(Math.max(best.width * 0.35, 12), 72),
      y: best.top + best.height / 2,
    };
  };

  /* 右上角店名点一次，再点「切换组织/店铺」那一层，点开「请选择店铺」 */
  if (action === "picker") {
    if (onLogin()) return { status: "login" };
    if (hasPicker()) return { status: "picker" };
    const chip = accountChip();
    if (!chip) return { status: "no-header" };
    await press(chip);
    let entry = null;
    const opened = Date.now();
    while (Date.now() - opened < 2500) {
      entry = switchEntry();
      if (entry) break;
      await wait(200);
    }
    if (!entry) return { status: "no-switch" };
    await press(entry);
    const start = Date.now();
    while (Date.now() - start < 4000) {
      await wait(200);
      if (hasPicker()) return { status: "picker" };
      if (hasCaptcha()) return { status: "captcha" };
    }
    return { status: "no-modal" };
  }

  /* 读出选择页上的全部店铺 */
  if (action === "list") {
    if (!hasPicker()) return { status: "not-picker", shops: [] };
    const out = [];
    const seen = {};
    const push = (name, shopId) => {
      const clean = norm(name);
      if (!nameLine(clean)) return;
      const key = shopId || clean;
      if (seen[key]) return;
      seen[key] = 1;
      out.push({ name: clean, shop_id: shopId || "" });
    };
    const inPicker = (el) => {
      let node = el;
      for (let i = 0; i < 12 && node; i += 1, node = node.parentElement) {
        const blob = norm(node.innerText || "");
        if (blob.indexOf("请选择店铺") >= 0 && blob.length < 8000) return true;
      }
      return false;
    };
    const cardOf = (el) => {
      if (!inPicker(el)) return null;
      let card = el;
      for (let i = 0; i < 6 && card; i += 1) {
        const blob = norm(card.innerText || "");
        if (blob.length && blob.length <= 180 &&
            /(子账号|主账号)/.test(blob) && shopType.test(blob)) {
          return card;
        }
        card = card.parentElement;
      }
      return null;
    };
    const harvest = () => {
      // 名字是一个完整叶子节点的情况
      for (const el of document.querySelectorAll("div,span,p,li,a,button")) {
        if (el.children.length || !vis(el)) continue;
        const t = norm(el.innerText || "");
        if (!t || t.length < 2 || t.length > 32) continue;
        if (!cardOf(el)) continue;
        const card = cardOf(el);
        const idMatch = norm(card.innerText || "").match(/店铺ID[:：]?(\d{6,20})/);
        push(t, idMatch ? idMatch[1] : "");
      }
      // 名字被拆成多段的情况：取卡片首行
      for (const el of document.querySelectorAll("div")) {
        if (!vis(el)) continue;
        const blob = norm(el.innerText || "");
        if (!blob || blob.length > 180 || !cardOf(el)) continue;
        const lines = String(el.innerText || "").split("\n").map((s) => s.replace(/\s+/g, "")).filter(Boolean);
        const title = lines.map(norm).find((line) => nameLine(line)) || "";
        const idMatch = blob.match(/店铺ID[:：]?(\d{6,20})/);
        push(title, idMatch ? idMatch[1] : "");
      }
    };
    harvest();
    // 店铺多的时候要滚动才看得全
    for (const box of document.querySelectorAll("div")) {
      if (box.scrollHeight <= box.clientHeight + 20) continue;
      if (norm(box.innerText || "").indexOf("请选择店铺") < 0) continue;
      const step = Math.max((box.clientHeight || 100) - 60, 80);
      for (let top = 0; top <= box.scrollHeight; top += step) {
        box.scrollTop = top;
        await wait(120);
        harvest();
      }
      box.scrollTop = 0;
      await wait(150);
      harvest();
    }
    return { status: "ok", shops: out };
  }

  /* 点进某一家店铺 */
  if (action === "enter") {
    const name = String((payload && payload.name) || "");
    const shopId = String((payload && payload.shop_id) || "");
    const find = () => {
      if (name) {
        const byName = deepest(name, 40);
        if (byName) return byName;
      }
      if (shopId) {
        for (const el of document.querySelectorAll("div,span,p,li,a,button")) {
          if (el.children.length || !vis(el)) continue;
          let card = el;
          for (let i = 0; i < 5 && card; i += 1) {
            if (norm(card.innerText || "").indexOf(shopId) >= 0) return el;
            card = card.parentElement;
          }
        }
      }
      return null;
    };
    const target = find();
    if (!target) return { status: "notfound" };
    await pressUp(target, 3);
    const start = Date.now();
    while (Date.now() - start < 12000) {
      await wait(300);
      if (hasCaptcha()) return { status: "captcha" };
      if (!hasPicker()) return { status: "entered", shop_id: shopIdHere() };
    }
    return { status: "timeout" };
  }

  if (action === "point") {
    const kind = payload && payload.kind;
    const box = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return {
        x: r.left + r.width / 2,
        y: r.top + r.height / 2,
        text: norm(el.innerText || "").slice(0, 16),
      };
    };
    const under = (x, y) => {
      const stack = document.elementsFromPoint(x, y) || [];
      for (const hit of stack) {
        if (!hit || hit.id === "dy-erp-cred-veil" || hit.id === "dy-erp-cred-panel") continue;
        return hit;
      }
      return null;
    };
    const badHit = (x, y) => {
      const hit = under(x, y);
      if (!hit) return false;
      const label = norm(hit.innerText || hit.textContent || "").slice(0, 16);
      if (label === "退出") return true;
      if (hit.tagName !== "A") return false;
      const href = hit.getAttribute("href") || "";
      if (/logout/.test(href)) return true;
      return /login|account/.test(href) && label.indexOf("严选") < 0 && label.indexOf("切换组织") < 0;
    };
    if (kind === "switchOk") {
      const x = Number(payload && payload.x);
      const y = Number(payload && payload.y);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return { ok: false, text: "" };
      const caretText = () => {
        if (!document.caretRangeFromPoint) return "";
        const range = document.caretRangeFromPoint(x, y);
        const raw = range && range.startContainer && range.startContainer.nodeType === 3 ? range.startContainer.nodeValue : "";
        return norm(raw);
      };
      const painted = caretText();
      if (painted.indexOf("切换组织") >= 0) return { ok: true, text: "切换组织/店铺" };
      if (painted === "退出") return { ok: false, text: "退出" };
      const stack = document.elementsFromPoint(x, y) || [];
      for (const hit of stack) {
        if (!hit || hit.id === "dy-erp-cred-veil" || hit.id === "dy-erp-cred-panel") continue;
        let cur = hit;
        for (let i = 0; i < 5 && cur; i += 1) {
          const t = norm(cur.innerText || cur.textContent || "").slice(0, 24);
          if (t === "退出") return { ok: false, text: "退出" };
          if (t.indexOf("切换组织") >= 0 && t.length <= 16) return { ok: true, text: "切换组织/店铺" };
          cur = cur.parentElement;
        }
        const own = norm(hit.innerText || hit.textContent || "").slice(0, 16);
        if (painted.indexOf("体验") >= 0 || own.indexOf("体验") >= 0 || own.indexOf("详情") >= 0) {
          return { ok: false, text: (painted || own).slice(0, 16) };
        }
        return { ok: false, text: (painted || own).slice(0, 16) };
      }
      return { ok: false, text: painted.slice(0, 16) };
    }
    if (kind === "chip") {
      const el = accountChip();
      if (!el) return null;
      const r = el.getBoundingClientRect();
      const blob = norm(el.innerText || "");
      const mixed = /个体|企业|旗舰|专卖|专营|正常营业|已关店/.test(blob);
      let x = mixed ? r.left + Math.min(r.width * 0.68, r.width - 18) : r.left + r.width / 2;
      let y = mixed ? r.top + Math.min(14, r.height / 3) : r.top + r.height / 2;
      if (badHit(x, y)) {
        x = r.right - 10;
        y = r.top + Math.min(14, r.height / 3);
        if (badHit(x, y)) return null;
      }
      return { x: x, y: y, text: blob.slice(0, 16) };
    }
    if (kind === "switch") {
      const veil = document.getElementById("dy-erp-cred-veil");
      if (veil) veil.style.setProperty("pointer-events", "none", "important");
      const chip = accountChip();
      const cr = chip ? chip.getBoundingClientRect() : null;
      const x0 = cr ? Math.max(4, cr.right - 380) : Math.max(4, window.innerWidth - 420);
      const x1 = cr ? Math.min(window.innerWidth - 2, cr.right + 24) : window.innerWidth - 2;
      const y0 = cr ? Math.max(4, cr.bottom - 4) : 36;
      const y1 = Math.min(window.innerHeight - 4, y0 + 520);
      const rowText = (node) => norm(node && (node.innerText || node.textContent) || "").slice(0, 24);
      const accountLink = (node) => {
        const link = node && node.closest ? node.closest("a") : null;
        const href = link ? (link.getAttribute("href") || "") : "";
        return /logout|login\/account|\/w\/login/.test(href);
      };
      const fromNode = (node, x, y) => {
        if (!node || node.id === "dy-erp-cred-veil" || node.id === "dy-erp-cred-panel") return null;
        if (accountLink(node)) return null;
        let cur = node;
        for (let i = 0; i < 4 && cur; i += 1) {
          const own = rowText(cur);
          if (own === "退出") return null;
          if (own.indexOf("切换组织") >= 0 && own.length <= 16) return { x: x, y: y, text: "切换组织/店铺" };
          cur = cur.parentElement;
        }
        if (node.shadowRoot && node.shadowRoot.elementFromPoint) {
          const inner = node.shadowRoot.elementFromPoint(x, y);
          if (inner && inner !== node) return fromNode(inner, x, y);
        }
        if (node.tagName === "IFRAME" && node.contentDocument) {
          const ir = node.getBoundingClientRect();
          const inner = node.contentDocument.elementFromPoint(x - ir.left, y - ir.top);
          if (inner) return fromNode(inner, x, y);
        }
        return null;
      };
      const scanRow = () => {
        const matched = [];
        for (let y = y0; y < y1; y += 7) {
          for (let x = x0; x < x1; x += 18) {
            let found = false;
            const stack = document.elementsFromPoint(x, y) || [];
            for (const hit of stack) {
              if (!hit || hit.id === "dy-erp-cred-veil" || hit.id === "dy-erp-cred-panel") continue;
              if (fromNode(hit, x, y)) found = true;
              break;
            }
            if (!found && document.caretRangeFromPoint) {
              const range = document.caretRangeFromPoint(x, y);
              const raw = range && range.startContainer && range.startContainer.nodeType === 3 ? range.startContainer.nodeValue : "";
              if (norm(raw).indexOf("切换组织") >= 0) found = true;
            }
            if (found) matched.push({ x: x, y: y });
          }
        }
        if (!matched.length) return null;
        const bands = {};
        for (const p of matched) {
          const key = Math.round(p.y / 14) * 14;
          bands[key] = (bands[key] || 0) + 1;
        }
        let bestKey = 0;
        let bestN = 0;
        for (const key of Object.keys(bands)) {
          if (bands[key] > bestN) {
            bestN = bands[key];
            bestKey = Number(key);
          }
        }
        const band = matched.filter((p) => Math.abs(p.y - bestKey) <= 14);
        const xs = band.map((p) => p.x).sort((a, b) => a - b);
        const ys = band.map((p) => p.y).sort((a, b) => a - b);
        const mid = (arr) => arr[Math.floor((arr.length - 1) / 2)];
        const span = xs[xs.length - 1] - xs[0];
        const x = span > 80 ? xs[0] + 8 : xs[Math.floor((xs.length - 1) * 0.25)];
        return { x: x, y: mid(ys), text: "切换组织/店铺" };
      };
      const firstHit = scanRow();
      if (firstHit) {
        await wait(160);
        const secondHit = scanRow();
        return secondHit || firstHit;
      }
      const hit = switchPoint();
      if (hit) return { x: hit.x, y: hit.y, text: "切换组织/店铺" };
      const el = switchEntry();
      if (!el) {
        let n = 0;
        let sample = "";
        for (const node of document.querySelectorAll("div,span,a,button,li,p")) {
          const t = norm(node.innerText || node.textContent || "");
          if (t.indexOf("切换") < 0) continue;
          n += 1;
          if (sample) continue;
          const r = node.getBoundingClientRect();
          sample = t.slice(0, 18) + "@" + Math.round(r.top) + "," + Math.round(r.height);
        }
        const bodyHas = text().indexOf("切换组织") >= 0 ? 1 : 0;
        return { missing: "没看到切换 " + n + " body=" + bodyHas + " " + sample, hits: 0 };
      }
      const r = el.getBoundingClientRect();
      return {
        x: r.left + Math.min(Math.max(r.width * 0.35, 16), 90),
        y: r.top + r.height / 2,
        text: "切换组织/店铺",
      };
    }
    if (kind === "shop") {
      const name = norm(payload.name || "");
      const inList = (el) => {
        let card = el;
        for (let i = 0; i < 8 && card; i += 1) {
          const blob = norm(card.innerText || "");
          if (blob.indexOf("请选择店铺") >= 0 || blob.indexOf("子账号") >= 0) return true;
          card = card.parentElement;
        }
        return false;
      };
      const leaves = () => {
        const found = [];
        for (const el of document.querySelectorAll("div,span,p,li,a")) {
          if (norm(el.innerText || "") !== name || !inList(el)) continue;
          let deeper = false;
          for (const child of el.children) {
            if (norm(child.innerText || "") === name) deeper = true;
          }
          if (!deeper) found.push(el);
        }
        return found;
      };
      const shown = () => leaves().filter((el) => {
        if (!vis(el)) return false;
        const r = el.getBoundingClientRect();
        return r.top >= 120 && r.bottom <= window.innerHeight - 8;
      });
      let rows = shown();
      if (!rows.length) {
        for (const scroller of document.querySelectorAll("div")) {
          if (scroller.scrollHeight <= scroller.clientHeight + 20) continue;
          const blob = norm(scroller.innerText || "");
          if (blob.indexOf("请选择店铺") < 0 && blob.indexOf("子账号") < 0) continue;
          const step = Math.max((scroller.clientHeight || 100) - 40, 80);
          for (let top = 0; top <= scroller.scrollHeight && !rows.length; top += step) {
            scroller.scrollTop = top;
            await wait(80);
            rows = shown();
          }
        }
      }
      let best = null;
      let area = Infinity;
      for (const el of rows) {
        const r = el.getBoundingClientRect();
        if (r.bottom > window.innerHeight - 8) continue;
        const areaNow = r.width * r.height;
        if (areaNow < area) {
          best = el;
          area = areaNow;
        }
      }
      if (best) {
        best.scrollIntoView({ block: "center" });
        await wait(150);
      }
      return box(best);
    }
    return null;
  }

  return { status: "unknown" };
}

/* ===================== 后台编排 ===================== */

function call(tabId, action, payload) {
  return chrome.scripting
    .executeScript({
      target: { tabId: tabId },
      world: "MAIN",
      func: pageRun,
      args: [action, payload || null],
    })
    .then(function (rows) {
      return (rows && rows[0] && rows[0].result) || null;
    })
    .catch(function (err) {
      console.log("[dy] call fail " + action + " " + String((err && err.message) || err));
      return null;
    });
}

async function targetTab() {
  if (panelTabId) {
    try {
      const tab = await chrome.tabs.get(panelTabId);
      if (tab && tab.id && /jinritemai\.com/.test(tab.url || "")) return tab;
    } catch (err) {
      /* 面板所在的标签已经关了，往下找 */
    }
  }
  const tabs = await chrome.tabs.query({ url: "*://*.jinritemai.com/*" });
  const working = tabs.filter(function (tab) {
    return tab.id && !/\/login/.test(tab.url || "");
  });
  if (working.length) return working[0];
  return tabs.find(function (tab) { return tab.id; }) || null;
}

async function readCookies() {
  const cookies = await chrome.cookies.getAll({ domain: STORE_DOMAIN });
  if (cookies.length >= 6) return cookies;
  try {
    const extra = await chrome.cookies.getAll({ url: "https://fxg.jinritemai.com/" });
    const keys = {};
    cookies.forEach(function (item) { keys[item.domain + "|" + item.path + "|" + item.name] = 1; });
    extra.forEach(function (item) {
      const key = item.domain + "|" + item.path + "|" + item.name;
      if (!keys[key]) {
        keys[key] = 1;
        cookies.push(item);
      }
    });
  } catch (err) {
    /* 拿不到就当只有 domain 那份 */
  }
  return cookies;
}

function cookiePayload(cookies) {
  return cookies.map(function (item) {
    return {
      name: item.name,
      value: item.value,
      domain: item.domain,
      path: item.path,
      secure: item.secure,
      httpOnly: item.httpOnly,
      expirationDate: item.expirationDate || null,
      sameSite: item.sameSite || "",
    };
  });
}

async function upload(apiBase, token, snap, shopName, shopId, account) {
  const cookies = await readCookies();
  const response = await fetch(apiBase + "/api/v1/accounts/import", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + token,
    },
    body: JSON.stringify({
      account: String(account || "").trim().slice(0, 100),
      shops: [{ name: shopName, shop_id: shopId }],
      cookies: cookiePayload(cookies),
      local_storage: (snap && snap.local_storage) || {},
      ua: (snap && snap.ua) || "",
    }),
    signal: AbortSignal.timeout(8000),
  });
  if (response.status === 401) {
    await chrome.storage.local.remove("token");
    throw new Error("401");
  }
  if (!response.ok) throw new Error(String(response.status));
  return response.json();
}

async function findPickerTab() {
  const tabs = await chrome.tabs.query({ url: "*://*.jinritemai.com/*" });
  for (let i = 0; i < tabs.length; i += 1) {
    if (!tabs[i].id) continue;
    const snap = await call(tabs[i].id, "snapshot");
    if (snap && snap.picker) return tabs[i];
  }
  return null;
}

async function waitShopReady(tabId, expectName) {
  const want = String(expectName || "").replace(/\s+/g, "");
  const start = Date.now();
  let snap = null;
  while (Date.now() - start < 12000) {
    if (halt()) return snap;
    snap = await call(tabId, "snapshot");
    if (snap && snap.captcha) return snap;
    if (snap && !snap.picker && (snap.ready || snap.shop_id)) {
      const header = String((snap && snap.header) || "");
      if (!want || header.indexOf(want) >= 0 || (header.length >= 4 && want.indexOf(header) >= 0)) return snap;
    }
    await sleep(400);
  }
  return snap || (await call(tabId, "snapshot"));
}

async function ensureDebug(tabId) {
  try {
    await chrome.debugger.attach({ tabId: tabId }, "1.3");
  } catch (err) {
    const message = String((err && err.message) || err);
    if (message.toLowerCase().indexOf("already") < 0) throw err;
  }
}

async function veilPass(tabId, open) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tabId },
      injectImmediately: true,
      func: function (open) {
        window.__dyErpClickThrough = !!open;
        const veil = document.getElementById("dy-erp-cred-veil");
        const panel = document.getElementById("dy-erp-cred-panel");
        if (veil) veil.style.setProperty("pointer-events", open ? "none" : "auto", "important");
        if (panel) panel.style.setProperty("pointer-events", "auto", "important");
      },
      args: [!!open],
    });
  } catch (err) {
    /* 幕布还没挂上时，点击照旧打到页面上 */
  }
}

async function trustedClick(tabId, x, y) {
  const target = { tabId: tabId };
  const px = Math.round(Number(x));
  const py = Math.round(Number(y));
  if (!Number.isFinite(px) || !Number.isFinite(py)) return false;
  let last = null;
  await veilPass(tabId, true);
  try {
    for (let i = 0; i < 3; i += 1) {
      try {
        await ensureDebug(tabId);
        await veilPass(tabId, true);
        await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
          type: "mouseMoved", x: px, y: py, button: "none", pointerType: "mouse",
        });
        await sleep(80);
        await veilPass(tabId, true);
        await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
          type: "mousePressed", x: px, y: py, button: "left", buttons: 1, clickCount: 1, pointerType: "mouse",
        });
        await sleep(40);
        await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
          type: "mouseReleased", x: px, y: py, button: "left", buttons: 0, clickCount: 1, pointerType: "mouse",
        });
        return true;
      } catch (err) {
        last = err;
        await sleep(300);
      }
    }
  } finally {
    await veilPass(tabId, false);
  }
  if (last) throw last;
  return false;
}

async function pressEscape(tabId) {
  try {
    await ensureDebug(tabId);
    const target = { tabId: tabId };
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", {
      type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27,
    });
    await chrome.debugger.sendCommand(target, "Input.dispatchKeyEvent", {
      type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27,
    });
  } catch (err) {
    /* 没有菜单要关 */
  }
}

async function releaseDebug(tabId) {
  try {
    await chrome.debugger.detach({ tabId: tabId });
  } catch (err) {
    /* 没挂上调试就不用拆 */
  }
}

async function openShopList(tabId) {
  if (halt()) return "stopped";
  await ensureDebug(tabId);
  await sleep(800);
  const now = await call(tabId, "snapshot");
  if (now && now.login) return "login";
  if (now && now.picker) return "picker";
  if (now && now.captcha) return "captcha";
  let sawChip = false;
  let sawSwitch = false;
  const clickable = (entry) => !!(entry && Number.isFinite(entry.x) && Number.isFinite(entry.y));
  for (let attempt = 0; attempt < 4; attempt += 1) {
    if (halt()) return "stopped";
    let entry = await call(tabId, "point", { kind: "switch" });
    if (!clickable(entry)) {
      const snap = await call(tabId, "snapshot");
      if (snap && snap.menu) {
        const opened = Date.now();
        while (Date.now() - opened < 1200 && !clickable(entry)) {
          await sleep(150);
          const got = await call(tabId, "point", { kind: "switch" });
          if (clickable(got)) entry = got;
        }
      }
      if (!clickable(entry)) {
        await pressEscape(tabId);
        await sleep(200);
        const chip = await call(tabId, "point", { kind: "chip" });
        if (!clickable(chip)) {
          await sleep(400);
          continue;
        }
        sawChip = true;
        say("点店铺 " + (chip.text || "") + " " + Math.round(chip.x) + "," + Math.round(chip.y));
        await trustedClick(tabId, chip.x, chip.y);
        const afterChip = await call(tabId, "snapshot");
        if (afterChip && afterChip.login) return "login";
        const opened = Date.now();
        entry = null;
        while (Date.now() - opened < 2500) {
          const got = await call(tabId, "point", { kind: "switch" });
          if (clickable(got)) {
            entry = got;
            break;
          }
          await sleep(150);
        }
        if (!clickable(entry)) {
          const again = await call(tabId, "point", { kind: "switch" });
          if (again && again.missing) say(again.missing);
          await pressEscape(tabId);
          await sleep(200);
          continue;
        }
      }
    }
    if (entry.y < 90) {
      await pressEscape(tabId);
      await sleep(200);
      continue;
    }
    const chipNow = await call(tabId, "point", { kind: "chip" });
    if (halt()) return "stopped";
    const seeds = [entry.x];
    if (chipNow && Number.isFinite(chipNow.x)) seeds.push(chipNow.x - 140);
    const tried = {};
    let aim = null;
    for (let s = 0; s < seeds.length && !aim; s += 1) {
      for (let dx = 0; dx <= 64 && !aim; dx += 16) {
        const px = seeds[s] - dx;
        const key = Math.round(px) + ":" + Math.round(entry.y);
        if (tried[key]) continue;
        tried[key] = 1;
        const guard = await call(tabId, "point", { kind: "switchOk", x: px, y: entry.y });
        if (guard && guard.ok) aim = { x: px, y: entry.y };
      }
    }
    if (!aim) {
      say("没点到切换 " + Math.round(entry.x) + "," + Math.round(entry.y));
      await pressEscape(tabId);
      await sleep(200);
      continue;
    }
    sawSwitch = true;
    say("点切换 " + Math.round(aim.x) + "," + Math.round(aim.y));
    await trustedClick(tabId, aim.x, aim.y);
    await closeStray(tabId);
    try {
      const here = await chrome.tabs.get(tabId);
      const url = (here && here.url) || "";
      if (url.indexOf("experience-score") >= 0) {
        await chrome.tabs.update(tabId, { url: "https://fxg.jinritemai.com/ffa/mshop/homepage/index" });
        await sleep(1200);
        continue;
      }
    } catch (err) {
      /* 标签还在就继续等选择店铺 */
    }
    const start = Date.now();
    while (Date.now() - start < 1800) {
      if (halt()) return "stopped";
      await sleep(200);
      try {
        const here = await chrome.tabs.get(tabId);
        const url = (here && here.url) || "";
        if (url.indexOf("experience-score") >= 0) {
          await chrome.tabs.update(tabId, { url: "https://fxg.jinritemai.com/ffa/mshop/homepage/index" });
          await sleep(1200);
          break;
        }
      } catch (err) {
        /* 标签还在就继续等选择店铺 */
      }
      const page = await call(tabId, "snapshot");
      if (page && page.login) return "login";
      if (page && page.picker) return "picker";
      if (page && page.captcha) return "captcha";
    }
    await pressEscape(tabId);
    await sleep(250);
  }
  if (sawSwitch) return "no-modal";
  return sawChip ? "no-switch" : "no-header";
}

function paint(text) {
  if (!panelTabId) return;
  jobSeq += 1;
  const seq = jobSeq;
  const progress = job.progress || null;
  const running = job.state === "running";
  chrome.scripting.executeScript({
    target: { tabId: panelTabId },
    injectImmediately: true,
    func: function (message, progress, running, seq) {
      const seen = window.__dyErpPaintSeq || 0;
      if (seq < seen) return "stale";
      window.__dyErpPaintSeq = seq;
      try {
        if (running) {
          sessionStorage.setItem("dy-erp-cred-shield", "1");
          sessionStorage.setItem("dy-erp-cred-detail", message || "");
          sessionStorage.setItem("dy-erp-cred-progress", JSON.stringify(progress || null));
        } else {
          sessionStorage.removeItem("dy-erp-cred-shield");
          sessionStorage.removeItem("dy-erp-cred-detail");
          sessionStorage.removeItem("dy-erp-cred-progress");
        }
      } catch (err) {
        /* 记不住就等新页面再读一次会话 */
      }
      return document.getElementById("dy-erp-cred-panel") ? "there" : "missing";
    },
    args: [String(text || ""), progress, running, seq],
  }).then(function (rows) {
    const flag = rows && rows[0] && rows[0].result;
    if (flag === "stale") return "stale";
    if (flag === "there") return "ready";
    return chrome.scripting.executeScript({
      target: { tabId: panelTabId },
      files: ["panel.js"],
      injectImmediately: true,
    }).then(function () {
      return "ready";
    });
  }).then(function (flag) {
    if (flag !== "ready") return null;
    return chrome.scripting.executeScript({
      target: { tabId: panelTabId },
      injectImmediately: true,
      func: function (message, progress, running, seq) {
        const seen = window.__dyErpPaintSeq || 0;
        if (seq < seen) return;
        window.__dyErpPaintSeq = seq;
        if (window.__dyErpPanel && window.__dyErpPanel.apply) {
          window.__dyErpPanel.apply(message, progress, running);
        }
      },
      args: [String(text || ""), progress, running, seq],
    });
  }).catch(function () {
    /* 页面正在跳，下一页 document_start 会把面板和幕布再挂上 */
  });
}

function say(text, progress) {
  job.detail = text;
  if (progress) job.progress = progress;
  console.log("[dy] " + text);
  chrome.storage.session.set({
    jobDetail: text,
    jobProgress: job.progress || null,
    jobRunning: job.state === "running",
    showPanel: true,
  });
  paint(text);
}

function finish(detail) {
  job = { state: "done", detail: detail, progress: job.progress || null };
  chrome.storage.session.set({
    jobDetail: detail,
    jobProgress: job.progress,
    jobRunning: false,
    showPanel: true,
  });
  paint(detail);
  return detail;
}

async function uploadCurrentShop(tabId, apiBase, token, owner) {
  say("正在读取当前页面");
  const snap = await call(tabId, "snapshot");
  if (halt()) return finish("已停止");
  if (!snap) return finish("没有读到当前店铺");
  if (snap.login) return finish("请先在抖店登录");
  if (snap.captcha) return finish("出现验证码，请自己通过后再点一次");
  const name = String(snap.header || "").trim();
  const shopId = String(snap.shop_id || "").trim();
  if (!name && !shopId) return finish("没有读到当前店铺");
  try {
    const data = await upload(apiBase, token, snap, name, shopId, owner);
    const action = (data && data.action) || "";
    if (action === "skip") return finish((name || "当前店铺") + " 没对上店铺");
    const word = action === "create" ? "已新建" : "已更新";
    return finish("交上 1 家，" + (name || shopId) + " " + word);
  } catch (err) {
    const message = String((err && err.message) || "");
    if (message === "401") return finish("401 后台登录已过期，请重新登录");
    return finish((name || "当前店铺") + " 上传失败 " + message);
  }
}

async function captureAll(apiBase, token, account) {
  const owner = String(account || "").trim().slice(0, 100);
  if (!owner) return finish("归属账号不能为空");
  if (job.state === "running") return job.detail || "正在获取";
  runId += 1;
  if (stopId === -1) {
    stopId = 0;
    return finish("已停止");
  }
  job = { state: "running", detail: "正在找抖店页面", progress: null };
  chrome.storage.session.set({
    jobDetail: job.detail,
    jobProgress: null,
    jobRunning: true,
    showPanel: true,
  });
  paint(job.detail);
  let tabId = 0;
  try {
    const tab = await targetTab();
    if (!tab || !tab.id) return finish("请先打开抖店工作台再获取");

    tabId = tab.id;
    panelTabId = tabId;
    await chrome.tabs.update(tabId, { active: true });

    const first = await call(tabId, "snapshot");
    if (!first) return finish("读不到抖店页面，刷新后再点一次");
    if (first.login) return finish("请先在抖店登录");
    if (first.captcha) return finish("页面正在验证码，请通过后再点获取凭证");

    return uploadCurrentShop(tabId, apiBase, token, owner);
  } catch (err) {
    console.log("[dy] fail " + String((err && err.message) || err));
    return finish("获取失败");
  } finally {
    if (typeof tabId === "number") await releaseDebug(tabId);
  }
}

/* ===================== 面板与消息接线：原样保留 ===================== */

chrome.action.onClicked.addListener(async function (tab) {
  try {
    console.log("[dy] icon click");
    try {
      await chrome.permissions.request({ origins: [API_BASE + "/*"] });
    } catch (err) {
      /* 没允许也能先把面板放出来 */
    }
    let tabId = tab && tab.id;
    const url = (tab && tab.url) || "";
    if (url.indexOf("jinritemai.com") < 0) {
      const tabs = await chrome.tabs.query({ url: "*://*.jinritemai.com/*" });
      const home = tabs.find(function (item) { return item.id; });
      tabId = home && home.id;
    }
    if (!tabId) return;
    await chrome.storage.session.set({ showPanel: true });
    await chrome.scripting.executeScript({
      target: { tabId: tabId },
      func: function () { window.__dyErpForcePanel = true; },
    });
    await chrome.scripting.executeScript({
      target: { tabId: tabId },
      files: ["panel.js"],
    });
  } catch (err) {
    console.error("[dy] icon", err);
  }
});

chrome.tabs.onUpdated.addListener(function (id, info) {
  if (id !== panelTabId) return;
  if (info.status !== "loading" && info.status !== "complete") return;
  if (job.state !== "running" && job.state !== "done") return;
  paint(job.detail);
});

chrome.runtime.onConnect.addListener(function (port) {
  if (port.name !== "capture") return;
  let closed = false;
  function reply(payload) {
    if (closed) return;
    try {
      port.postMessage(payload);
    } catch (err) {
      closed = true;
    }
  }
  port.onDisconnect.addListener(function () {
    closed = true;
  });
  port.onMessage.addListener(function (message) {
    if (!message || message.type !== "begin") return;
    panelTabId = (port.sender && port.sender.tab && port.sender.tab.id) || panelTabId;
    ensureToken(message.username, message.password).then(function (got) {
      if (!got || !got.token) {
        reply({ detail: (got && got.error) || "请填写账号和密码", needLogin: true, done: true });
        return;
      }
      return captureAll(API_BASE, got.token, message.account).then(function (detail) {
        const needLogin = String(detail).indexOf("401") >= 0;
        if (needLogin) chrome.storage.local.remove("token");
        reply({ detail: detail, needLogin: needLogin, done: true });
      });
    }).catch(function () {
      reply({ detail: "获取失败", done: true });
    });
  });
});

function closeStray(keepId) {
  if (job.state !== "running") return Promise.resolve();
  return chrome.tabs.query({}).then(function (tabs) {
    (tabs || []).forEach(function (tab) {
      if (!tab || !tab.id || tab.id === keepId || tab.id === panelTabId) return;
      const url = tab.url || tab.pendingUrl || "";
      const openedByPanel = tab.openerTabId && tab.openerTabId === panelTabId;
      if (url.indexOf("experience-score") >= 0 || (openedByPanel && /jinritemai\.com/.test(url))) {
        chrome.tabs.remove(tab.id);
      }
    });
  }).catch(function () { /* 标签已经没了 */ });
}

function watchBorn(born) {
  let left = 6;
  const tick = function () {
    if (job.state !== "running" || left <= 0) return;
    left -= 1;
    chrome.tabs.get(born, function (live) {
      if (chrome.runtime.lastError || !live) return;
      const url = live.url || live.pendingUrl || "";
      const openedByPanel = live.openerTabId && live.openerTabId === panelTabId;
      if (url.indexOf("experience-score") >= 0 || (openedByPanel && /jinritemai\.com/.test(url))) {
        chrome.tabs.remove(born);
        return;
      }
      if (!url || url === "about:blank") setTimeout(tick, 400);
    });
  };
  setTimeout(tick, 300);
}

chrome.tabs.onCreated.addListener(function (tab) {
  if (job.state !== "running" || !tab || !tab.id || tab.id === panelTabId) return;
  watchBorn(tab.id);
});

chrome.storage.session.get(["jobRunning"], function (saved) {
  if (saved && saved.jobRunning && job.state !== "running") finish("已停止");
});

chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  if (message && message.type === "stop") {
    if (sender && sender.tab && sender.tab.id) panelTabId = sender.tab.id;
    console.log("[dy] stop");
    if (job.state === "running") askStop();
    else {
      stopId = 0;
      finish("已停止");
    }
    sendResponse({ ok: true });
    return false;
  }
  if (message.type === "status") {
    sendResponse({ state: job.state, detail: job.detail });
    return false;
  }
  if (message.type === "session") {
    chrome.storage.local.get(["username", "token"], function (saved) {
      sendResponse({
        loggedIn: tokenAlive(saved && saved.token),
        username: (saved && saved.username) || "",
      });
    });
    return true;
  }
  if (message.type === "switch") {
    chrome.storage.local.remove("token", function () {
      sendResponse({ ok: true });
    });
    return true;
  }
  if (message.type === "begin") {
    panelTabId = (sender && sender.tab && sender.tab.id) || panelTabId;
    ensureToken(message.username, message.password).then(function (got) {
      if (!got.token) {
        sendResponse({ detail: got.error || "请填写账号和密码", needLogin: true });
        return;
      }
      captureAll(API_BASE, got.token, message.account).then(function (detail) {
        const needLogin = String(detail).indexOf("401") >= 0;
        if (needLogin) chrome.storage.local.remove("token");
        sendResponse({ detail: detail, needLogin: needLogin });
      });
    });
    return true;
  }
  if (message.type !== "capture") return false;
  captureAll(message.apiBase || API_BASE, message.token, message.account).then(function (detail) {
    sendResponse({ detail: detail });
  });
  return true;
});
