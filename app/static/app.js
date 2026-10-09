// Navigation, sign-in, faction page, settings, notifications and the refresh loop.
let pollers = [], currentTab = "today";
const LOADERS = {
  today: () => loadToday(),
  notes: () => loadNotes(),
  board: () => loadBoard(),
  people: () => loadPeople(),
  faction: () => loadFaction(),
  market: () => loadMarket(),
};

function showTab(tab){
  currentTab = tab;
  $$("[data-tab]").forEach(b => b.setAttribute("aria-selected", b.dataset.tab === tab));
  $$("section.tab").forEach(s => s.classList.toggle("on", s.id === tab));
  try { history.replaceState(null, "", tab === "today" ? location.pathname : "#" + tab); } catch(e){}
  window.scrollTo({top: 0});
  return Promise.resolve(LOADERS[tab]()).catch(fail);
}
$$("[data-tab]").forEach(b => b.onclick = () => showTab(b.dataset.tab));

// ---------- faction ----------
async function loadFaction(){
  const [d, links] = await Promise.all([api.get("/api/faction"), api.get("/api/links")]);
  const f = d.faction;
  if (!f){ $("#faction-body").innerHTML = `
    <div class="page-head"><h1>Faction</h1></div>
    <div class="hero glass" style="margin-top:24px"><small>Faction</small><div class="say">You're not in a faction yet.</div>
    <div class="sub">Once you join one, this page shows respect, chain timers, who's online, plus a shared board, notes and links for your faction.</div></div>`; return; }
    const c = f.chain || {};
    const flinks = links.links.filter(l => l.scope === "faction");
    const meId = state.me?.player_id;
    const people = (rows, extra, empty = "No one right now.") => group(rows.map(r => `<div class="row"><div class="main"><div class="title">${person(r.id, r.name)}${r.id === meId ? ' <span class="small-muted">(you)</span>' : ""}</div>
    <div class="detail">Level ${r.level} · ${esc(r.status)}</div></div>${extra ? `<div class="val detail">${extra(r)}</div>` : ""}</div>`), empty);
    const lastSeen = r => r.last ? (nowS() - r.last < 60 ? "just now" : ago(r.last)) : "";
    const idle = f.idle || [];
    infoId = 2000;
    $("#faction-body").innerHTML = `
    <div class="page-head"><h1>${esc(f.name)}${f.tag ? ` <span class="small-muted" style="font-size:24px">[${esc(f.tag)}]</span>` : ""}</h1>
    <p>${num(f.member_count)} members · ${num(f.respect)} respect</p></div>
    <div class="today">
    <div>
    <div class="hero glass"><small>Right now</small>
    <div class="say">${f.alerts.length ? esc(f.alerts[0]) : `${f.online.length} of ${f.member_count} members are online.`}</div>
    <div class="sub">${c.current ? `The chain is at ${num(c.current)} hits.` : "No chain running."}</div></div>
    <div class="tiles">
    <button class="tile" onclick="showTab('board');state.scope.board='faction';renderScopes();loadBoard()"><span class="k">Shared board</span><span class="v">Plan together</span><span class="s">Columns and a canvas everyone in the faction can edit</span></button>
    <button class="tile" onclick="state.scope.notes='faction';renderScopes();showTab('notes');setNotesView('journal')"><span class="k">Shared notes</span><span class="v">Faction journal</span><span class="s">Plans, rules and what you've learned</span></button>
    <button class="tile" onclick="openReminder();state.scope.rem='faction';renderScopes()"><span class="k">Faction reminder</span><span class="v">Remind everyone</span><span class="s">Shows on every member's timeline</span></button>
    <button class="tile" onclick="state.scope.person='faction';renderScopes();showTab('people')"><span class="k">Shared tags</span><span class="v">Tag players</span><span class="s">Allies, targets and traders for the whole faction</span></button>
    </div>
    <h2>Overview</h2><div class="group">
    <div class="row"><div class="main title">Respect</div><div class="val">${num(f.respect)}</div>${info("respect")}</div>${infoRow("respect")}
    <div class="row"><div class="main title">Chain</div><div class="val">${num(c.current || 0)} hits${c.timeout ? ` · ${c.timeout}s left` : ""}</div>${info("chain")}</div>${infoRow("chain")}
    <div class="row"><div class="main title">Best chain ever</div><div class="val">${num(f.best_chain)}</div></div></div>
    <div class="cols"><div><h2>In hospital</h2><div class="group">${people(f.hospital, r => r.until ? `out in ${dur(r.until - nowS())}` : "")}</div></div>
    <div><h2>Travelling</h2><div class="group">${people(f.travelling)}</div></div></div>
    </div>
    <aside class="side">
    <div class="panel glass"><div class="panel-h"><h3>Online now</h3><span class="small-muted">${f.online.length}</span></div>${people(f.online, lastSeen)}
    ${idle.length ? `<div class="panel-h"><h3>Idle</h3><span class="small-muted">${idle.length}</span></div>${people(idle, lastSeen)}` : ""}
    <p class="small-muted" style="margin:6px 18px 14px">As Torn reports it${d.updated ? `, updated ${ago(d.updated)}` : ""}. Torn only counts time spent in the game, so having this dashboard open doesn't show you as online.</p></div>
    <div class="panel glass"><div class="panel-h"><h3>Faction links</h3><button class="linkish" onclick="state.scope.notes='faction';renderScopes();showTab('notes');setNotesView('links')">Edit</button></div>
    ${flinks.length ? flinks.map(l => `<div class="link-row"><div class="main"><a href="${esc(l.url)}" target="_blank" rel="noopener" class="title">${esc(l.title)}</a></div></div>`).join("")
    : '<div class="empty">Add the faction Discord and guides so everyone can find them.</div>'}</div>
    </aside>
    </div>`;
}

// ---------- theme ----------
const mq = matchMedia("(prefers-color-scheme: dark)");
function applyTheme(t){
  try { localStorage.setItem("td-theme", t); } catch(e){}
  const dark = t === "auto" ? mq.matches : t === "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  $('meta[name="theme-color"]').content = dark ? "#000000" : "#F5F5F7";
  $$("#theme-seg button").forEach(b => b.setAttribute("aria-selected", b.dataset.theme === t));
}
const savedTheme = () => { try { return localStorage.getItem("td-theme") || "light"; } catch(e){ return "light"; } };
mq.addEventListener("change", () => { if ((state.me?.theme || savedTheme()) === "auto") applyTheme("auto"); });
applyTheme(savedTheme());

// ---------- sign-in ----------
async function showLogin(){
  pollers.forEach(clearInterval); pollers = []; state.me = null;
  $("#app").hidden = true; $("#login").hidden = false;
  $$("dialog[open]").forEach(d => d.close());
  try {
    const p = await (await fetch("/api/public")).json();
    const m = p.market || {}, c = p.community || {};
    const parts = [];
    if (c.players) parts.push(`${num(c.players)} player${c.players === 1 ? "" : "s"} use this dashboard, ${num(c.active_today)} today`);
    if (m.days) parts.push(`price history for ${num(m.catalogue_items || 0)} items over ${m.days} day${m.days === 1 ? "" : "s"}`);
    $("#login-stat").textContent = parts.length ? parts.join(" · ").replace(/^./, x => x.toUpperCase()) + "." : "";
    if (!p.signups_open) $("#login-stat").textContent += " This server is invite-only.";
  } catch(e){}
  setTimeout(() => $("#login-key").focus(), 50);
}

function setAvatar(){
  const a = $("#avatar"), initial = (state.me?.name || "?").slice(0, 1).toUpperCase();
  if (state.me?.profile_image){
    const img = new Image(); img.alt = ""; img.referrerPolicy = "no-referrer";
    img.onerror = () => { a.textContent = initial; };
    img.src = state.me.profile_image; a.replaceChildren(img);
  } else a.textContent = initial;
}

async function showApp(){
  setAvatar(); applyTheme(state.me.theme || "light");
  splash.step(`Welcome back, ${state.me.name || "player"}`, 25);
  state.spaces = await api.get("/api/spaces").catch(() => ({faction: null}));
  renderScopes();
  splash.step("Opening your notes and tags", 40);
  await loadPeopleMap();
  const start = (location.hash || "").slice(1);
  splash.step("Getting your latest numbers from Torn", 55);
  $("#login").hidden = true; $("#app").hidden = false;
  await showTab(LOADERS[start] ? start : "today");
  splash.done();
  renderCommunity();
  pollers.forEach(clearInterval);
  pollers = [
    setInterval(() => { if (!document.hidden && ["today", "faction"].includes(currentTab)) LOADERS[currentTab]().catch(() => {}); }, 30000),
    setInterval(() => { if (!document.hidden && currentTab === "board") loadBoard(); }, 20000),
    setInterval(renderCommunity, 120000),
  ];
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
}

async function boot(){
  splash.step("Signing you in", 12);
  try { state.me = await getJSON("/api/me"); await showApp(); }
  catch(e){ if (!(e instanceof AuthError)) showLogin(); }
  splash.done();
}

$("#login-form").onsubmit = async e => {
  e.preventDefault();
  const btn = $("#login-btn"), err = $("#login-err");
  err.hidden = true; btn.disabled = true; btn.textContent = "Checking your key with Torn…";
  try {
    const r = await fetch("/api/login", {method: "POST", headers: {"Content-Type": "application/json"},
                          body: JSON.stringify({torn_key: $("#login-key").value.trim()})});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || "Sign-in failed.");
    $("#login-key").value = "";
    splash.show();
    await boot();
  } catch(ex){ err.textContent = ex.message; err.hidden = false; }
  btn.disabled = false; btn.textContent = "Sign in";
};

async function renderCommunity(){
  try {
    const p = await (await fetch("/api/public")).json();
    const c = p.community || {}, m = p.market || {};
    const plural = (n, w) => `<b>${num(n)}</b> ${w}${n === 1 ? "" : "s"}`;
    $("#community").innerHTML = [
      `<span class="live">${plural(c.active_now || 0, "player")} on now</span>`,
      `<span>${plural(c.active_today, "player")} today</span>`,
      `<span>${plural(c.active_week, "player")} this week</span>`,
      `<span>${plural(c.players, "player")} in total${c.new_week ? `, ${num(c.new_week)} new this week` : ""}</span>`,
      m.days ? `<span>${plural(m.catalogue_items || 0, "item")} tracked for <b>${num(m.days)}</b> day${m.days === 1 ? "" : "s"}</span>` : "",
    ].join("");
  } catch(e){}
}

// ---------- settings ----------
function openSettings(){
  const me = state.me; if (!me) return;
  $("#set-who").textContent = `Signed in as ${me.name} [${me.player_id}]${me.is_owner ? " · you run this server" : ""}`;
  const g = me.gemini;
  $("#set-gemini").value = "";
  $("#set-gemini").placeholder = g.has_own_key ? "Saved. Paste a new key to replace it" : "Paste a key from Google AI Studio";
  $("#set-gemini-status").innerHTML = g.source === "own" ? `Using your own key · up to ${g.daily_limit || "unlimited"} requests a day. <button class="linkish" id="set-gemini-remove">Remove key</button>`
  : g.source === "server" ? "Using the server's key, shared with you by whoever runs this dashboard."
  : "Off. Add a key to get buy and sell advice.";
  const rm = $("#set-gemini-remove");
  if (rm) rm.onclick = () => saveSettings({gemini_key: ""}, "Key removed.");
  $("#set-torn-status").textContent = me.key_ok ? "Your key is working." : "Torn stopped accepting your key. Paste a new one.";
  $("#set-err").hidden = true;
  applyTheme(me.theme || "light");
  $("#owner-box").hidden = !me.is_owner;
  if (me.is_owner) loadPlayers();
  renderPush();
  if (!$("#settings").open) $("#settings").showModal();
}
async function loadPlayers(){
  try {
    const list = await api.get("/api/admin/players");
    $("#owner-players").innerHTML = list.map(p => `<div class="row"><div class="main">
    <div class="title">${person(p.player_id, p.name)} <span class="detail" style="display:inline">level ${p.level ?? "?"}</span></div>
    <div class="detail">Joined ${ago(p.created)} · last seen ${ago(p.last_seen)}${p.has_advisor ? " · advisor on" : ""}${p.key_ok ? "" : " · key rejected"}</div></div></div>`).join("")
    || '<div class="empty">No one yet.</div>';
  } catch(e){}
}
async function saveSettings(body, okMsg){
  const err = $("#set-err");
  try { await api.put("/api/settings", body); state.me = await api.get("/api/me");
    openSettings(); err.hidden = false; err.className = "ok-text"; err.textContent = okMsg || "Saved."; }
    catch(e){ if (e instanceof AuthError) return; err.hidden = false; err.className = "err-text"; err.textContent = e.message; }
}
$("#avatar").onclick = openSettings;
$("#set-close").onclick = () => $("#settings").close();
$$("#theme-seg button").forEach(b => b.onclick = () => { applyTheme(b.dataset.theme); saveSettings({theme: b.dataset.theme}, "Appearance saved."); });
$("#set-gemini-save").onclick = () => { const k = $("#set-gemini").value.trim(); if (k) saveSettings({gemini_key: k}, "Gemini key saved. The advisor is on."); };
$("#set-torn-save").onclick = async () => {
  const err = $("#set-err"), k = $("#set-torn").value.trim(); if (!k) return;
  try { await api.put("/api/settings/torn_key", {torn_key: k}); $("#set-torn").value = "";
  state.me = await api.get("/api/me"); openSettings(); err.hidden = false; err.className = "ok-text"; err.textContent = "Key replaced."; }
  catch(e){ if (e instanceof AuthError) return; err.hidden = false; err.className = "err-text"; err.textContent = e.message; }
};
$("#set-logout").onclick = async () => { await fetch("/api/logout", {method: "POST"}); showLogin(); };
$("#set-delete").onclick = async () => {
  if (!confirm("Delete your key and all your personal data from this server? Your Torn account isn't affected, and anything you added to your faction's space stays with the faction.")) return;
  await fetch("/api/account", {method: "DELETE"}); showLogin();
};

// ---------- notifications ----------
const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent);
const standalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone;
const b64ToBytes = s => { const p = "=".repeat((4 - s.length % 4) % 4), raw = atob((s + p).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map(c => c.charCodeAt(0))); };

  async function currentSub(){
    if (!pushSupported()) return null;
    const reg = await navigator.serviceWorker.getRegistration("/");
    return reg ? reg.pushManager.getSubscription() : null;
  }

  async function renderPush(){
    const stateEl = $("#push-state"), btn = $("#push-toggle"), help = $("#push-help");
    let prefs;
    try { prefs = await api.get("/api/notify/prefs"); } catch(e){ return; }
    if (!prefs.available){ stateEl.textContent = "Not available on this server."; btn.hidden = true; help.textContent = "The server owner needs to install pywebpush."; $("#push-kinds").innerHTML = ""; return; }
    if (!pushSupported()){
      stateEl.textContent = "This browser can't receive notifications."; btn.hidden = true;
      help.textContent = isIOS() && !standalone() ? "On iPhone, tap Share → Add to Home Screen, then open the dashboard from your home screen and turn notifications on there." : "";
      return;
    }
    const sub = await currentSub();
    const on = !!sub && Notification.permission === "granted";
    btn.hidden = Notification.permission === "denied" && !on;
    stateEl.textContent = on ? `On for this device${prefs.devices > 1 ? ` (and ${prefs.devices - 1} other)` : ""}.` : Notification.permission === "denied" ? "Blocked in your browser settings." : "Off on this device.";
    btn.textContent = on ? "Turn off" : "Turn on";
    btn.onclick = on ? pushOff : pushOn;
    help.innerHTML = on ? `<button class="linkish" id="push-test">Send a test notification</button>` :
    "Get rent, course, travel and reminder alerts even with the dashboard closed. While on, your data is checked every 10 minutes in the background.";
    if (Notification.permission === "denied" && !on) help.textContent = "Allow notifications for this site in your browser's settings, then come back here.";
    const t = $("#push-test"); if (t) t.onclick = async () => { try { await api.post("/api/push/test"); toast("Sent. It should appear in a moment."); } catch(e){ fail(e); } };
    $("#push-kinds").innerHTML = Object.entries(prefs.labels).map(([k, label]) => `
    <label class="row" style="cursor:pointer"><div class="main">${esc(label)}</div>
    <input type="checkbox" data-k="${k}" ${prefs.prefs[k] ? "checked" : ""} style="width:20px;height:20px;accent-color:var(--action)"></label>`).join("");
    $$("#push-kinds input").forEach(i => i.onchange = async () => {
      const all = Object.fromEntries($$("#push-kinds input").map(x => [x.dataset.k, x.checked]));
      try { await api.put("/api/notify/prefs", {prefs: all}); } catch(e){ fail(e); }
    });
  }

  async function pushOn(){
    try {
      if (await Notification.requestPermission() !== "granted") return renderPush();
      const reg = await navigator.serviceWorker.register("/sw.js");
      await navigator.serviceWorker.ready;
      const {key} = await api.get("/api/push/key");
      const sub = await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: b64ToBytes(key)});
      await api.post("/api/push/subscribe", sub.toJSON());
      toast("Notifications are on");
    } catch(e){ fail(e.message ? e : new Error("Couldn't turn on notifications here.")); }
    renderPush();
  }
  async function pushOff(){
    try { const sub = await currentSub(); if (sub){ await api.post("/api/push/unsubscribe", sub.toJSON()); await sub.unsubscribe(); } toast("Notifications are off"); }
    catch(e){ fail(e); }
    renderPush();
  }

  boot();
