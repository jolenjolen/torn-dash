// MARKET: advisor, what you own, price history, watchlist and items worth watching.
async function loadMarket(){
  try { renderTrade(await api.get("/api/trade")); renderLogging(); } catch(e){ fail(e); }
}

// ================= TRADE =================
let trade = null;
function renderTrade(t){
  trade = t;
  // advisor
  const a = t.advice;
  if (!t.ai_enabled){
    $("#advice-say").textContent = "Turn on the advisor by adding your Gemini key.";
    $("#advice-sub").innerHTML = 'It\'s optional and stays private to you. <button class="linkish" onclick="openSettings()">Open Settings</button>';
    $("#advice-btn").disabled = true; $("#ask-btn").disabled = true;
  } else if (a && !a.error){
    $("#advice-say").textContent = a.summary || "Here's what the advisor thinks.";
    $("#advice-sub").innerHTML = `Updated ${ago(a.generated_at)} · based on ${a.data_coverage?.daily_snapshots ?? 0} day(s) of price data. ${info("advisor")}${infoRow("advisor")}`;
    renderRecs(a);
  } else if (a?.error){
    $("#advice-say").textContent = "The advisor couldn't answer last time.";
    $("#advice-sub").textContent = a.error;
  } else {
    $("#advice-sub").textContent = "It looks at what you own, your cash, price history and upcoming events.";
  }

  // inventory
  const inv = t.inventory || {items: []};
  $("#inv-lede").textContent = inv.items.length
    ? `Worth about ${money(inv.total_value)} in total. Updated ${ago(t.inventory_updated)}.`
    : "Your items show up here after the first inventory check (it runs hourly).";
  $("#inventory").innerHTML = group(inv.items.map(it => `
    <div class="row"><div class="main"><div class="title"><button class="linkish" style="font-size:inherit;font-weight:500" onclick="showHistory(${it.item_id})">${esc(it.name)}</button>${it.amount > 1 ? ` <span class="detail" style="display:inline">× ${it.amount}</span>` : ""}</div>
      <div class="detail">${esc(it.type)}${it.equipped ? " · equipped" : ""}${!it.tradeable ? " · can't be sold" : ""}${trendWords(it.price_chg_30d, "price") ? ` · ${trendWords(it.price_chg_30d, "Price")} in 30 days` : ""}</div></div>
      <div class="val"><div>${it.tradeable ? short(it.value) : '<span class="detail">no market value</span>'}</div>${it.in_sell_season ? '<span class="pill sell">Sell window</span>' : ""}</div></div>`),
    "Nothing here yet.");

  // watchlist
  $("#watchlist").innerHTML = group((t.watchlist || []).map(w => {
    const signal = w.deal ? `<span class="pill deal">${esc(w.deal)}</span>`
      : w.season === "buy" ? '<span class="pill buy">Buy window</span>'
      : w.season === "sell" ? '<span class="pill sell">Sell window</span>' : "";
    const season = (w.buy_label || w.sell_label) ? `Buy ${w.buy_label || "—"} · sell ${w.sell_label || "—"}` : "No buy or sell months set";
    return `<div class="row"><div class="main">
        <div class="title"><a href="${w.market_url}" target="_blank" rel="noopener">${esc(w.name)}</a>${w.source === "auto" ? ' <span class="pill">added for you</span>' : ""}</div>
        <div class="detail">${w.lowest ? `Cheapest now ${money(w.lowest)}` : "Price not checked yet"}${w.median_7d ? ` · usual ${money(w.median_7d)}` : ""}${w.target_price ? ` · alert at ${money(w.target_price)}` : ""}</div>
        <div class="detail">${esc(season)}${w.note ? ` · ${esc(w.note)}` : ""}</div>
        <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">${signal}
          <button class="btn quiet small" onclick="showHistory(${w.item_id})">History</button>
          <button class="btn quiet small" onclick="openSeason(${w.item_id})">Set months</button>
          <button class="btn quiet small" onclick="unwatch(${w.item_id})">Remove</button></div>
      </div><div class="val">${spark(w.history)}</div></div>`;
  }), "Nothing on your watchlist yet.");

  // candidates
  $("#cand-lede").innerHTML = `Items that no shop sells, ranked by shrinking supply. ${t.snapshots < 8
    ? `The dashboard has ${t.snapshots} day(s) of history so far, and trends get reliable after about a week.` : ""} ${info("candidate")}${infoRow("candidate")}`;
  renderCandidates();
}

function renderCandidates(){
  if (!trade) return;
  const only = $("#afford").checked;
  const list = (trade.candidates || []).filter(c => !only || c.affordable);
  $("#candidates").innerHTML = group(list.slice(0, 30).map(c => `
    <div class="row"><div class="main">
      <div class="title"><button class="linkish" style="font-size:inherit;font-weight:500" onclick="showHistory(${c.id})">${esc(c.name)}</button> ${c.event ? '<span class="pill warn">event item</span>' : ""}</div>
      <div class="detail">${esc(c.type)}${c.consumable ? " · gets used up" : ""} · ${num(c.circulation)} exist</div>
      <div class="detail">${trends(trendWords(c.chg_30d ?? c.chg_7d, "Supply"), trendWords(c.price_chg_30d, "price"))}</div></div>
      <div class="val"><div>${short(c.market_value)}</div>
        ${c.affordable === false ? '<div class="detail">over budget</div>' : ""}
        <button class="btn quiet small" style="margin-top:6px" onclick="watch(${c.id})">Watch</button></div></div>`),
    only ? `Nothing on this list fits your budget of ${money(trade.spendable)} yet.` : "Candidates appear after the first daily item check.");
}
$("#afford").onchange = renderCandidates;

function renderRecs(a){
  const recs = a.recommendations || [];
  $("#recs-wrap").hidden = !recs.length;
  $("#recs").innerHTML = recs.map(r => `
    <div class="row" data-more role="button" tabindex="0" aria-expanded="false">
      <span class="pill ${esc(r.action)}">${esc((r.action || "").replace(/^./, c => c.toUpperCase()))}</span>
      <div class="main"><div class="title">${esc(r.item)}</div><div class="detail">${esc(r.reason)}</div></div>
      <div class="val detail">${esc(r.when || "")}<br>${esc(r.confidence || "")} confidence</div></div>
    <div class="more"><strong>Based on</strong><ul class="ev">${(r.evidence || []).map(e => `<li>${esc(e)}</li>`).join("")}</ul></div>`).join("");
  const w = a.watch_out || [];
  $("#watchout").innerHTML = w.length ? `<h2>Keep in mind</h2><div class="group">${w.map(x =>
    `<div class="row"><span class="dot med"></span><div class="main">${esc(x)}</div></div>`).join("")}</div>` : "";
}

$("#advice-btn").onclick = async () => {
  const b = $("#advice-btn"); b.disabled = true; b.textContent = "Thinking…";
  try { const a = await getJSON("/api/ai/advice", {method: "POST"});
    if (a.error){ $("#advice-say").textContent = "The advisor couldn't answer."; $("#advice-sub").textContent = a.error; }
    else { trade.advice = a; renderTrade(trade); }
  } catch(e){ $("#advice-sub").textContent = "Couldn't reach the dashboard server."; }
  b.disabled = false; b.textContent = "Get advice";
};

$("#ask-form").onsubmit = async e => {
  e.preventDefault();
  const q = $("#ask-q").value.trim(); if (q.length < 3) return;
  const out = $("#ask-out"); out.hidden = false; out.textContent = "Thinking…"; $("#ask-btn").disabled = true;
  try { out.textContent = (await getJSON("/api/ai/ask", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({question: q})})).answer; }
  catch(err){ out.textContent = "Couldn't reach the dashboard server."; }
  $("#ask-btn").disabled = false;
};

// watchlist actions
let searchMap = {};
$("#w-search").addEventListener("input", async e => {
  const q = e.target.value.trim(); if (q.length < 2) return;
  const res = await getJSON(`/api/items/search?q=${encodeURIComponent(q)}`).catch(() => []);
  searchMap = Object.fromEntries(res.map(r => [r.name, r.item_id]));
  $("#w-opts").innerHTML = res.map(r => `<option value="${esc(r.name)}">`).join("");
});
const showErr = msg => { const e = $("#w-err"); e.textContent = msg || ""; e.hidden = !msg; };
async function watch(id){
  try { await getJSON("/api/trade/watchlist", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({item_id: id})});
    showErr(""); loadMarket(); }
  catch(e){ showErr(e.message); }
}
$("#w-add").onclick = () => { const id = searchMap[$("#w-search").value];
  if (!id) return showErr("Choose an item from the suggestions as you type."); watch(id); $("#w-search").value = ""; };
async function unwatch(id){ await fetch(`/api/trade/watchlist/${id}`, {method: "DELETE"}); loadMarket(); }

// ================= PRICE HISTORY =================
let histItem = null, histDays = 30;
const fmtDate = ts => new Date(ts * 1000).toLocaleDateString(undefined, {day: "numeric", month: "short"});

function chartSVG(value, lowest){
  const all = [...value, ...lowest];
  if (all.length < 2) return "";
  const W = Math.max(300, ($("#hist-body").clientWidth || 640) - 36), H = W < 520 ? 190 : 230;
  const L = 58, R = 12, T = 12, B = 26;
  const xs = all.map(p => p[0]), ys = all.map(p => p[1]);
  let x0 = Math.min(...xs), x1 = Math.max(...xs); if (x1 === x0) x1 = x0 + 1;
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = (y1 - y0) * 0.08 || y1 * 0.05 || 1; y0 = Math.max(0, y0 - pad); y1 += pad;
  const X = t => L + (t - x0) / (x1 - x0) * (W - L - R);
  const Y = v => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
  const line = (pts, color) => pts.length < 2 ? (pts.length ? `<circle cx="${X(pts[0][0])}" cy="${Y(pts[0][1])}" r="3.5" fill="${color}"/>` : "")
    : `<polyline fill="none" stroke="${color}" stroke-width="2.25" stroke-linejoin="round" stroke-linecap="round" points="${pts.map(p => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ")}"/>`;
  const ticks = [y0, (y0 + y1) / 2, y1];
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Price chart">
    ${ticks.map(v => `<line class="grid" x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/><text class="axis" x="${L - 8}" y="${Y(v) + 4}" text-anchor="end">${short(v)}</text>`).join("")}
    <text class="axis" x="${L}" y="${H - 6}">${fmtDate(x0)}</text>
    <text class="axis" x="${W - R}" y="${H - 6}" text-anchor="end">${fmtDate(x1)}</text>
    ${line(lowest, "var(--good)")}${line(value, "var(--action)")}</svg>`;
}

async function showHistory(id, scroll = true){
  histItem = id;
  const body = $("#hist-body");
  body.innerHTML = '<div class="empty">Loading…</div>';
  if (scroll) $("#hist-h").scrollIntoView({behavior: "smooth", block: "start"});
  let h;
  try { h = await getJSON(`/api/items/${id}/history?days=${histDays}`); }
  catch(e){ body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  $("#h-search").value = h.item.name;
  const vs = h.value_stats, ls = h.lowest_stats, cs = h.circulation_stats;
  const supply = h.item.city_buy_price > 0 ? `Sold in a city shop for ${money(h.item.city_buy_price)}, so supply is endless.`
    : h.item.abroad ? "Sold in a foreign shop, so supply is endless." : "Not sold in any shop.";
  const change = st => st && st.change_pct != null && st.points > 1
    ? `<span class="${st.change_pct < 0 ? "down" : "up"}">${st.change_pct > 0 ? "+" : ""}${st.change_pct.toFixed(1)}%</span> since ${fmtDate(st.since)}` : "Not enough points yet";
  const chart = chartSVG(h.value, h.lowest);
  body.innerHTML = `
    ${chart ? `<div class="chart">${chart}</div>
      <div class="legend"><span class="lv">Market value (Torn's estimate)</span><span class="lw">Cheapest listing on the item market</span></div>`
      : `<div class="empty">Only ${(vs?.points || 0) + (ls?.points || 0)} price point(s) so far. The chart appears as the log fills in, usually within a few hours.</div>`}
    <div class="row"><div class="main"><div class="title">${esc(h.item.name)}</div><div class="detail">${esc(h.item.type || "")} · ${supply}</div></div></div>
    <div class="row"><div class="main"><div class="title">Market value</div><div class="detail">${change(vs)}${vs ? ` · low ${short(vs.min)} · high ${short(vs.max)}` : ""}</div></div>
      <div class="val">${vs ? money(vs.last) : "—"}</div>${info("market")}</div>${infoRow("market")}
    <div class="row"><div class="main"><div class="title">Cheapest listing</div><div class="detail">${change(ls)}${ls ? ` · ${ls.points} checks` : " · not checked yet"}</div></div>
      <div class="val">${ls ? money(ls.last) : "—"}</div></div>
    <div class="row"><div class="main"><div class="title">How many exist</div><div class="detail">${change(cs)}</div></div>
      <div class="val">${cs ? num(cs.last) : "—"}</div>${info("circulation")}</div>${infoRow("circulation")}`;
}

let histMap = {};
$("#h-search").addEventListener("input", async e => {
  const v = e.target.value.trim();
  if (histMap[v]) return showHistory(histMap[v], false);
  if (v.length < 2) return;
  const res = await getJSON(`/api/items/search?q=${encodeURIComponent(v)}`).catch(() => []);
  histMap = Object.fromEntries(res.map(r => [r.name, r.item_id]));
  $("#h-opts").innerHTML = res.map(r => `<option value="${esc(r.name)}">`).join("");
  if (histMap[v]) showHistory(histMap[v], false);
});
document.querySelectorAll("#h-range button").forEach(b => b.onclick = () => {
  document.querySelectorAll("#h-range button").forEach(x => x.setAttribute("aria-selected", x === b));
  histDays = +b.dataset.days;
  if (histItem) showHistory(histItem, false);
});

async function renderLogging(){
  try {
    const l = await getJSON("/api/logging");
    const sw = l.sweep || {};
    const parts = [];
    if (l.catalogue_items) parts.push(`Market values for all ${num(l.catalogue_items)} items are logged every hour`);
    if (sw.total) parts.push(`listings checked for ${num(sw.total)} items, now on ${num(sw.position)} of ${num(sw.total)}${sw.passes ? ` (${sw.passes} full rounds done)` : ""}`);
    $("#log-lede").textContent = parts.length ? parts.join("; ") + `. ${num((l.value_points || 0) + (l.listing_points || 0))} prices recorded so far.`
      : "Price logging starts once the item catalogue has loaded (a minute or two after the dashboard starts).";
  } catch(e){}
}

// season editor
let editing = null;
function monthButtons(el, chosen){
  el.innerHTML = MONTHS.map((m, i) => `<button type="button" aria-pressed="${chosen.includes(i+1)}" data-m="${i+1}">${m}</button>`).join("");
  el.querySelectorAll("button").forEach(b => b.onclick = () => b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") !== "true"));
}
const picked = el => [...el.querySelectorAll("button[aria-pressed=true]")].map(b => +b.dataset.m);
function openSeason(id){
  const w = (trade?.watchlist || []).find(x => x.item_id === id); if (!w) return;
  editing = id;
  $("#season-title").textContent = w.name;
  monthButtons($("#buy-months"), w.buy_months || []);
  monthButtons($("#sell-months"), w.sell_months || []);
  $("#s-target").value = w.target_price || ""; $("#s-note").value = w.note || "";
  $("#season").showModal();
}
$("#season").addEventListener("close", async () => {
  if ($("#season").returnValue !== "save" || editing == null) return;
  await fetch(`/api/trade/watchlist/${editing}/season`, {method: "PUT", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({buy_months: picked($("#buy-months")), sell_months: picked($("#sell-months")),
      note: $("#s-note").value, target_price: parseInt($("#s-target").value) || null})});
  editing = null; loadMarket();
});

