// TODAY: compact tiles of everything the API knows, and one timeline of what's coming up.
let today = {personal: null, timeline: [], lists: [], notes: []};

const icon = (d, prefix) => Object.values(d.icons_travel?.icons || {}).find(t => t.startsWith(prefix));
const iconDetail = (d, prefix) => { const t = icon(d, prefix); if (!t) return null;
  const parts = textOnly(t).split(" - "); return parts.length > 2 ? parts.slice(1, -1).join(" - ") : parts[1]; };

function greet(p){
  const h = new Date().getHours();
  const part = h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  $("#hello").textContent = p?.name ? `${part}, ${p.name}` : part;
  const f = p?.faction?.faction_name && p.faction.faction_name !== "None" ? ` · ${p.faction.faction_name}` : "";
  $("#hello-sub").textContent = p?.name ? `Level ${p.level} · ${p.status?.description || "Okay"}${f}` : "Waiting for your first update from Torn.";
}

const BAR_COLOR = {energy: "var(--energy)", nerve: "var(--nerve)", happy: "var(--happy)", life: "var(--life)"};
const BAR_NAME = {energy: "Energy", nerve: "Nerve", happy: "Happy", life: "Life"};

function tile(key, label, value, sub, extra = "", cls = ""){
  return `<button class="tile ${cls}" data-tile="${key}"><span class="k">${esc(label)}</span><span class="v">${value}</span>` +
         `${sub ? `<span class="s">${sub}</span>` : ""}${extra}</button>`;
}

function renderTiles(d){
  const p = d.profile || {}, b = d.bars || {}, m = d.money || {}, cd = d.cooldowns || {}, ed = d.education || {};
  const out = [];

  const mini = ["energy", "nerve", "happy", "life"].filter(k => b[k]).map(k => { const x = b[k];
    return `<span>${BAR_NAME[k]}</span><span class="meter"><span style="width:${Math.min(100, x.current / x.maximum * 100)}%;background:${BAR_COLOR[k]}"></span></span><span class="num">${x.current}/${x.maximum}</span>`; }).join("");
  const full = ["energy", "nerve"].filter(k => b[k] && b[k].current >= b[k].maximum);
  out.push(tile("bars", "Bars", full.length ? `${full.map(k => BAR_NAME[k]).join(" & ")} full` : `${b.energy?.current ?? "—"} energy`,
    null, `<span class="mini">${mini}</span>${full.length ? '<span class="flag" title="Wasting regen"></span>' : ""}`, "wide"));

  out.push(tile("money", "Cash", short(m.wallet), `Spendable ${short(d.spendable)} · worth ${short(m.daily_networth)}`,
    (m.wallet || 0) > 5000 ? '<span class="flag" title="Mug risk"></span>' : ""));

  const st = p.status || {};
  const travel = d.icons_travel?.travel || {};
  const statusV = travel.time_left ? `Flying to ${esc(travel.destination)}` : esc(st.state === "Okay" ? "Okay" : st.description || st.state || "—");
  const icons = activeIcons(d);
  out.push(tile("status", "Status", statusV, icons.length ? esc(icons.slice(0, 2).map(i => i.label).join(" · ")) : "Nothing else going on"));

  const cu = d.cooldowns_until || {};
  const running = CD_KINDS.filter(k => cu[k]?.until > nowS()).sort((a, b) => cu[a].until - cu[b].until);
  out.push(tile("cooldowns", "Cooldowns",
    running.length ? `${CD_NAME[running[0]]} <span data-until="${cu[running[0]].until}">${durShort(cu[running[0]].until - nowS())}</span>` : "All clear",
    running.length > 1 ? running.slice(1).map(k => `${CD_NAME[k]} <span data-until="${cu[k].until}">${durShort(cu[k].until - nowS())}</span>`).join(" · ")
      : running.length ? `${CD_KINDS.filter(k => !running.includes(k)).map(k => CD_NAME[k]).join(" and ")} clear` : "Drug, booster and medical ready"));

  const course = iconDetail(d, "Education");
  out.push(tile("course", "Course", ed.current ? durShort(ed.current.until - nowS()) + " left" : "None running",
    ed.current ? esc((course || "").replace(/^Currently completing the /, "").replace(/ course$/, "")) : "Start one: it's free progress",
    ed.current ? "" : '<span class="flag"></span>'));

  const rented = (d.properties || []).find(x => x.rented_by?.id === p.player_id);
  const owned = (d.properties || []).find(x => x.owner?.id === p.player_id);
  const prop = rented || owned;
  out.push(tile("property", "Home", esc(prop?.property?.name || p.property || "—"),
    rented ? `Rent: ${rented.rental_period_remaining} day${rented.rental_period_remaining === 1 ? "" : "s"} left · ${short(rented.cost_per_day)}/day`
           : prop ? `${num(prop.happy)} max happy` : ""));

  const f = p.faction || {};
  const recruit = iconDetail(d, "Faction");
  out.push(tile("faction", "Faction", f.faction_id ? esc(f.faction_name) : "None yet",
    f.faction_id ? esc(f.position || recruit || "") : "Joining one gives perks and protection"));

  out.push(tile("stats", "Battle stats", num(d.battlestats?.total),
    d.battlestats?.strength ? `Str ${num(d.battlestats.strength.value)} · Def ${num(d.battlestats.defense.value)}` : ""));

  const job = iconDetail(d, "Company");
  out.push(tile("job", "Job", esc(job ? job.split(" of ")[0] : p.job?.position || "—"), esc(job ? job.split(" of ").slice(1).join(" of ") : p.job?.job || "")));

  const attackedToday = (d.attacks || []).some(a => a.direction === "in" && nowS() - a.started < 86400);
  const last = (d.attacks || [])[0];
  out.push(tile("fights", "Fights", last ? (last.direction === "out" ? `You ${esc((last.result || "").toLowerCase())}` : `${esc(last.attacker_name)} hit you`) : "None yet",
    `${(d.attackers || []).length} attacker${(d.attackers || []).length === 1 ? "" : "s"} this month`, attackedToday ? '<span class="flag"></span>' : ""));

  const ev = (d.events || [])[0];
  out.push(tile("events", "Latest event", ev ? ago(ev.ts) : "—", ev ? esc(textOnly(ev.text)).slice(0, 80) : ""));

  const rf = d.refills || {};
  out.push(tile("points", "Points", num(m.points), rf.energy === false ? "Energy refill unused today" : rf.energy ? "Energy refill used today" : ""));

  const ms = d.missions || {};
  const affordable = (ms.rewards || []).filter(r => (r.cost || 0) <= (ms.credits || 0)).length;
  out.push(tile("missions", "Mission credits", num(ms.credits ?? 0), affordable ? `${affordable} reward${affordable === 1 ? "" : "s"} you can buy` : "Nothing affordable yet"));

  out.push(tile("items", "Items", short(d.inventory_total), "At today's market value"));

  $("#tiles").innerHTML = out.join("");
  $$("#tiles .tile").forEach(t => t.onclick = () => openTile(t.dataset.tile));
}

const CD_KINDS = ["drug", "booster", "medical"];
const CD_NAME = {drug: "Drug", booster: "Booster", medical: "Medical"};

// Profile icons that aren't covered by another tile: "You have items up for sale", etc.
function activeIcons(d){
  const skip = /^(education|bank investment|faction|company|drug cooldown|booster cooldown|medical cooldown|newbie|male|female|donator|subscriber|level|hospital|jail|travel)/i;
  return Object.values(d.icons_travel?.icons || {}).map(t => textOnly(String(t)).split(" - "))
    .filter(p => !skip.test(p[0])).map(p => ({label: p.length > 1 ? p.slice(1).join(" - ") : p[0], title: p[0]}));
}

// Keeps countdowns ticking between refreshes.
setInterval(() => $$("[data-until]").forEach(el => { const s = +el.dataset.until - nowS();
  el.textContent = s > 0 ? durShort(s) : "done"; }), 30000);

function openTile(key){
  const d = today.personal || {}, p = d.profile || {}, m = d.money || {}, b = d.bars || {}, cd = d.cooldowns || {};
  const rows = list => list.map(([l, v, k]) => `<div class="row"><div class="main title">${l}</div><div class="val">${v}</div>${k ? info(k) : ""}</div>${k ? infoRow(k) : ""}`).join("");
  const T = {
    bars: () => ["Bars", `<div class="group">${["energy", "nerve", "happy", "life"].filter(k => b[k]).map(k => { const x = b[k];
      return `<div class="row"><div class="main"><div style="display:flex;justify-content:space-between"><span class="title">${BAR_NAME[k]}</span><span>${x.current} of ${x.maximum}</span></div>
      <div class="meter"><span style="width:${Math.min(100, x.current / x.maximum * 100)}%;background:${BAR_COLOR[k]}"></span></div>
      <div class="detail">${x.current >= x.maximum ? "Full" : x.full_time ? "Full in " + dur(x.full_time) : ""}</div></div>${info(k)}</div>${infoRow(k)}`; }).join("")}</div>`],
    money: () => ["Money", `<div class="group">${rows([["Cash in hand", money(m.wallet)], ["Vault", money(m.vault)], ["Company funds", money(m.company)],
      ["Cayman bank", money(m.cayman_bank)], ["Spendable", money(d.spendable), "spendable"], ["Net worth", money(m.daily_networth), "networth"], ["Points", num(m.points), "points"]])}</div>`],
    status: () => ["Status", `<div class="group">${rows([["Status", esc(p.status?.description || "Okay")],
      ["Travel", d.icons_travel?.travel?.time_left ? `${esc(d.icons_travel.travel.destination)}, lands in ${dur(d.icons_travel.travel.time_left)}` : "In Torn"],
      ["Life", b.life ? `${b.life.current} of ${b.life.maximum}` : "—", "life"]])}</div>
      <h4>On your profile</h4><div class="group">${group(Object.values(d.icons_travel?.icons || {}).map(t => { const p = textOnly(String(t)).split(" - ");
        return `<div class="row"><div class="main"><div class="title">${esc(p[0])}</div>${p.length > 1 ? `<div class="detail">${esc(p.slice(1).join(" · "))}</div>` : ""}</div></div>`; }), "Nothing to show.")}</div>`],
    cooldowns: () => { const cu = d.cooldowns_until || {};
      return ["Cooldowns", `<div class="group">${CD_KINDS.map(k => { const c = cu[k], on = c?.until > nowS();
        return `<div class="row"><div class="main"><div class="title">${CD_NAME[k]}</div><div class="detail">${on ? `${c.detail ? esc(c.detail) + " · " : ""}ends ${when(c.until)}` : "Ready to use"}</div></div>
          <div class="val">${on ? `<span data-until="${c.until}">${durShort(c.until - nowS())}</span>` : "Clear"}</div>${k === "drug" ? info("cooldown") : ""}</div>${k === "drug" ? infoRow("cooldown") : ""}`; }).join("")}</div>
        <p class="small-muted" style="margin:12px 4px">Each one also shows on your timeline. Turn on notifications for them in Settings to get a ping when they end.</p>`]; },
    course: () => ["Education", `<div class="group">${rows([["Current course", esc((iconDetail(d, "Education") || "None").replace(/^Currently completing the /, ""))],
      ["Time left", d.education?.current ? dur(d.education.current.until - nowS()) : "—", "education"], ["Courses finished", num((d.education?.complete || []).length)]])}</div>
      <p class="small-muted" style="margin:12px 4px"><a href="https://www.torn.com/page.php?sid=education" target="_blank" rel="noopener">Open education in Torn</a></p>`],
    property: () => ["Home", group((d.properties || []).map(x => `<div class="row"><div class="main"><div class="title">${esc(x.property?.name)}</div>
      <div class="detail">${x.rented_by?.id === p.player_id ? `Rented from ${person(x.owner?.id, x.owner?.name)} · ${x.rental_period_remaining} of ${x.rental_period} days left · ${money(x.cost_per_day)}/day` : esc(x.status || "")}
      · ${num(x.happy)} max happy${x.modifications?.length ? " · " + esc(x.modifications.join(", ")) : ""}</div></div></div>`), "No properties found.")],
    faction: () => { showTab("faction"); return null; },
    stats: () => ["Battle stats", `<div class="group">${rows(["strength", "defense", "speed", "dexterity"].map(k => [k[0].toUpperCase() + k.slice(1), num(d.battlestats?.[k]?.value)]).concat([["Total", num(d.battlestats?.total), "stats"]]))}</div>`],
    job: () => ["Job", `<div class="group">${rows([["Position", esc(iconDetail(d, "Company") || p.job?.position || "—")]])}</div>`],
    fights: () => ["Fights", `<h4>Who's attacked you (30 days)</h4><div class="group">${group((d.attackers || []).map(r => `<div class="row"><div class="main"><div class="title">${person(r.id, r.name)}</div><div class="detail">Level ${r.level ?? "?"} · last ${ago(r.last)}</div></div><div class="val">${r.times}×</div></div>`), "No one recently.")}</div>
      <h4>Recent fights</h4><div class="group">${fightRows(d.attacks || [])}</div>`],
    events: () => ["Events", `<div class="group">${group((d.events || []).map(r => `<div class="row"><div class="main"><div class="title" style="font-weight:400">${esc(textOnly(r.text))}</div><div class="detail">${ago(r.ts)}</div></div></div>`), "No events yet.")}</div>`],
    points: () => ["Points & refills", `<div class="group">${rows([["Points", num(m.points), "points"], ["Energy refill", d.refills?.energy ? "Used today" : "Available"], ["Nerve refill", d.refills?.nerve ? "Used today" : "Available"], ["Token refill", d.refills?.token ? "Used today" : "Available"]])}</div>`],
    missions: () => ["Mission rewards", `<p class="small-muted" style="margin:0 4px 10px">You have ${num(d.missions?.credits ?? 0)} credits.</p><div class="group">${group((d.missions?.rewards || []).map(r => `<div class="row"><div class="main"><div class="title">${esc(r.details?.name || r.type)} ×${num(r.amount)}</div><div class="detail">${num(r.cost)} credits · expires ${when(r.expires_at)}</div></div>${(r.cost || 0) <= (d.missions?.credits || 0) ? '<span class="pill deal">Affordable</span>' : ""}</div>`), "No rewards on offer.")}</div>`],
    items: () => { showTab("market"); return null; },
  };
  const r = (T[key] || (() => null))();
  if (r) openDetail(r[0], r[1]);
}

const RESULT_OUT = {Mugged: "You mugged %", Hospitalized: "You put % in hospital", Attacked: "You beat %", Lost: "You lost to %",
  Stalemate: "You drew with %", Escape: "% escaped from you", Assist: "You helped attack %", Timeout: "Your attack on % timed out"};
const RESULT_IN = {Mugged: "% mugged you", Hospitalized: "% put you in hospital", Attacked: "% beat you", Lost: "You beat % (they attacked)",
  Stalemate: "% drew with you", Escape: "You escaped from %", Timeout: "%'s attack on you timed out"};
function fightRows(attacks){
  return group(attacks.slice(0, 20).map(r => {
    const out = r.direction === "out";
    const tpl = (out ? RESULT_OUT : RESULT_IN)[r.result] || (out ? `You attacked % (${r.result})` : `% attacked you (${r.result})`);
    const line = esc(tpl).replace("%", out ? person(r.defender_id, r.defender_name) : person(r.attacker_id, r.attacker_name));
    return `<div class="row"><span class="dot ${out ? "low" : "high"}"></span><div class="main"><div class="title">${line}</div><div class="detail">${ago(r.started)}</div></div></div>`;
  }), "No fights yet.");
}

function renderTimeline(items){
  const real = items.filter(i => i.kind !== "tip");
  $("#timeline").innerHTML = group(real.slice(0, 60).map(i => {
    const tag = i.kind === "reminder" ? `<span class="kind ${i.scope === "faction" ? "faction" : "reminder"}">${i.scope === "faction" ? "faction" : "yours"}</span>`
      : i.kind === "event" ? '<span class="kind event">event</span>' : "";
    const title = i.url ? `<a href="${esc(i.url)}" target="_blank" rel="noopener" style="color:inherit">${esc(i.title)}</a>` : esc(i.title);
    const acts = i.kind === "reminder"
      ? `<button class="icon-btn" title="Done" onclick="remDone(${i.id})">✓</button><button class="icon-btn" title="Delete" onclick="remDel(${i.id})">×</button>`
      : `<button class="icon-btn" title="Hide this" data-hide="${esc(i.key)}" data-due="${i.due || 0}">×</button>`;
    return `<div class="tl ${i.now ? "now" : ""}"><div class="when">${i.ongoing ? "On now" : when(i.due)}</div>
      <div class="what"><div class="t">${title}${tag}</div>${i.detail ? `<div class="d">${linkify(i.detail)}${i.repeat && i.repeat !== "none" ? ` · repeats ${esc(i.repeat)}` : ""}</div>` : ""}</div>
      <div class="acts">${acts}</div></div>`;
  }), "Nothing coming up. Timers appear here as Torn reports them.");
}

function renderHero(items){
  const first = items.find(i => i.kind === "tip") || items.find(i => i.now && i.kind !== "event") || items.find(i => i.due);
  $("#next").textContent = first ? (first.kind === "tip" ? first.title : `${first.title}${first.now ? "" : " · " + when(first.due)}`) : "You're all set.";
  $("#next-why").textContent = first?.detail || "";
}

function renderTodaySide(){
  const lists = today.lists.filter(l => l.items.length);
  $("#today-lists").innerHTML = lists.length ? `<div class="panel-h"><h3>Checklists</h3><button class="linkish" onclick="showTab('notes');setNotesView('lists')">All</button></div>` +
    lists.slice(0, 2).map(l => { const done = l.items.filter(i => i.done).length;
      return `<div style="padding:4px 18px 10px"><div style="display:flex;justify-content:space-between;font-weight:500"><span>${esc(l.title)}</span><span class="small-muted">${done}/${l.items.length}</span></div>
        <div class="progress" style="margin-top:6px"><span style="width:${done / l.items.length * 100}%"></span></div></div>` +
        l.items.filter(i => !i.done).slice(0, 3).map(i => `<label class="cl-item"><input type="checkbox" onchange="checkItem(${i.id}, this.checked, true)" ${i.auto_state != null ? "disabled" : ""}><span class="txt">${esc(i.text)}</span>${i.auto ? '<span class="auto">auto</span>' : ""}</label>`).join(""); }).join("")
    : `<div class="panel-h"><h3>Checklists</h3></div><div class="empty">Make a checklist in Notes. There are starter templates too. <button class="linkish" onclick="showTab('notes');setNotesView('lists')">Open</button></div>`;
  const notes = today.notes.slice(0, 3);
  $("#today-notes").innerHTML = `<div class="panel-h"><h3>Notes</h3><button class="linkish" onclick="showTab('notes');setNotesView('journal')">All</button></div>` +
    (notes.length ? notes.map(n => `<div class="tl" style="cursor:pointer" onclick="showTab('notes');setNotesView('journal')"><div class="what"><div class="t">${esc(n.title || n.body.slice(0, 60))}</div><div class="d">${ago(n.updated)}${n.tags.length ? " · " + n.tags.map(esc).join(", ") : ""}</div></div></div>`).join("")
      : `<div class="empty">Write down what you learn. It adds up.</div>`);
}

async function loadToday(){
  const [p, tl, cl, nt] = await Promise.all([api.get("/api/personal"), api.get("/api/timeline"),
    api.get("/api/checklists"), api.get("/api/notes")]);
  today = {personal: p, timeline: tl.items, lists: cl.checklists, notes: nt.notes};
  greet(p.profile);
  renderTiles(p); renderTimeline(tl.items); renderHero(tl.items); renderTodaySide();
  const s = $("#sync");
  s.className = "sync" + (state.me && !state.me.key_ok ? " err" : "");
  s.textContent = state.me && !state.me.key_ok ? "Torn stopped accepting your key. Replace it in Settings." : `Updated ${ago(p.updated)}`;
}

// --- timeline actions ---
async function remDone(id){ try { const r = await api.post(`/api/reminders/${id}/done`); toast(r.next ? `Next one: ${when(r.next)}` : "Done"); loadToday(); } catch(e){ fail(e); } }
async function remDel(id){ if (!confirm("Delete this reminder?")) return; try { await api.del(`/api/reminders/${id}`); loadToday(); } catch(e){ fail(e); } }
document.addEventListener("click", e => { const b = e.target.closest("[data-hide]");
  if (b) tlHide(b.dataset.hide, +b.dataset.due); });
async function tlHide(key, due){ try { await api.post("/api/timeline/hide", {key, due: due || null}); loadToday(); } catch(e){ fail(e); } }

// --- reminder dialog ---
const toLocalInput = ts => { const d = new Date(ts * 1000); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); };
function openReminder(){
  $("#rem-form").reset();
  $("#rem-due").value = toLocalInput(nowS() + 86400);
  $("#rem-every").hidden = true;
  $("#rem-scope-wrap").hidden = !state.spaces.faction;
  renderScopes();
  $("#rem-dialog").showModal();
}
$("#add-rem").onclick = openReminder;
$$("#rem-quick .chip").forEach(c => c.onclick = () => { $("#rem-due").value = toLocalInput(nowS() + +c.dataset.s); });
$("#rem-repeat").onchange = () => { $("#rem-every").hidden = $("#rem-repeat").value !== "every"; };
$("#rem-dialog").addEventListener("close", async () => {
  if ($("#rem-dialog").returnValue !== "save") return;
  const extra = $("#rem-note").value.trim();
  const body = {title: $("#rem-title").value.trim(), due: Math.floor(new Date($("#rem-due").value).getTime() / 1000),
    repeat: $("#rem-repeat").value, every_days: parseInt($("#rem-every").value) || null, scope: scopeOf("rem")};
  if (/^https?:\/\//.test(extra)) body.link = extra; else if (extra) body.note = extra;
  if (!body.title || !body.due) return toast("Add what and when.");
  try { await api.post("/api/reminders", body); toast("Reminder saved"); loadToday(); } catch(e){ fail(e); }
});
