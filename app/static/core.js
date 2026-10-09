// Shared helpers used by every section.
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const money = n => n == null ? "—" : "$" + Math.round(Number(n)).toLocaleString();
const short = n => { if (n == null) return "—"; n = Number(n);
  if (Math.abs(n) >= 1e9) return "$" + (n/1e9).toFixed(1) + "B";
  if (Math.abs(n) >= 1e6) return "$" + (n/1e6).toFixed(1) + "M";
  if (Math.abs(n) >= 1e4) return "$" + Math.round(n/1e3) + "k";
  return money(n); };
  const num = n => n == null ? "—" : Number(n).toLocaleString();
  const nowS = () => Math.floor(Date.now() / 1000);
  const ago = ts => { if (!ts) return "never"; const s = nowS() - ts;
    if (s < 60) return "just now"; if (s < 3600) return Math.floor(s/60) + " min ago";
    if (s < 86400) return Math.floor(s/3600) + " h ago"; return Math.floor(s/86400) + " days ago"; };
    const dur = s => { s = Math.max(0, s|0); const d = Math.floor(s/86400), h = Math.floor(s%86400/3600), m = Math.floor(s%3600/60);
      return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`; };
      const durShort = s => { s = Math.max(0, s|0); const d = Math.floor(s/86400), h = Math.floor(s%86400/3600), m = Math.floor(s%3600/60);
        return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`; };
        const when = ts => { if (!ts) return ""; const s = ts - nowS();
          if (s <= 0) return "Now"; if (s < 3600) return `${Math.ceil(s/60)}m`; if (s < 86400) return `${Math.floor(s/3600)}h ${Math.floor(s%3600/60)}m`;
          if (s < 7 * 86400) return new Date(ts * 1000).toLocaleDateString(undefined, {weekday: "short"}) + " " +
            new Date(ts * 1000).toLocaleTimeString(undefined, {hour: "numeric", minute: "2-digit"}).replace(/\s?([AP])M/i, (m, x) => x.toLowerCase() + "m");
            return new Date(ts * 1000).toLocaleDateString(undefined, {day: "numeric", month: "short"}); };
            const dateLabel = ts => new Date(ts * 1000).toLocaleDateString(undefined, {day: "numeric", month: "short", year: "numeric"});
            const textOnly = html => { const d = document.createElement("div"); d.innerHTML = html; return d.textContent; };
            const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
            const trendWords = (p, noun) => p == null ? null
            : Math.abs(p) < 0.5 ? `${noun} steady`
            : `${noun} ${p < 0 ? "down" : "up"} ${Math.abs(p).toFixed(1)}%`;
            const trends = (...parts) => { const got = parts.filter(Boolean);
              return got.length ? got.join(" · ") : "Trends appear after a few days of data"; };
              const linkify = s => esc(s).replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
              const splitTags = s => (s || "").split(",").map(t => t.trim().toLowerCase()).filter(Boolean);
              const tagChip = t => `<span class="tag t-${esc(t.replace(/[^a-z0-9-]/g, ""))}">${esc(t)}</span>`;

              // Splash screen shown while the first page loads.
              const splash = (() => {
                let shown = Date.now(), pct = 4, creep = null, cap = null;
                const set = p => { pct = Math.max(pct, p); const f = document.getElementById("sp-fill"); if (f) f.style.width = pct + "%"; };
                return {
                  show(){ const s = document.getElementById("splash"); if (!s) return;
                    shown = Date.now(); pct = 0; set(4); s.hidden = false; s.classList.remove("out");
                    clearTimeout(cap); cap = setTimeout(() => splash.done(), 25000); },
                              step(text, p){
                                const el = document.getElementById("sp-step");
                                if (el && el.textContent !== text){ el.classList.add("swap"); setTimeout(() => { el.textContent = text; el.classList.remove("swap"); }, 180); }
                                set(p);
                                // Slow parts (Torn's API) keep the bar moving so it never looks stuck.
                                clearInterval(creep); creep = setInterval(() => set(Math.min(pct + (92 - pct) * .06, 92)), 400);
                              },
                              async done(){
                                clearInterval(creep); clearTimeout(cap); set(100);
                                const s = document.getElementById("splash"); if (!s || s.hidden) return;
                                await new Promise(r => setTimeout(r, Math.max(350, 700 - (Date.now() - shown))));
                                s.classList.add("out"); setTimeout(() => { s.hidden = true; }, 600);
                              },
                };
              })();

              // Shared state
              const state = {me: null, spaces: {faction: null}, people: {}, scope: {}};

              // A player's name, linked to Torn, with any tags you've given them.
              function person(id, name){
                if (!id) return esc(name || "Someone");
                const p = state.people[id];
                const tags = p ? p.tags.slice(0, 2).map(t => `<span class="ptag">${esc(t)}</span>`).join("") : "";
                return `<a href="https://www.torn.com/profiles.php?XID=${id}" target="_blank" rel="noopener">${esc(name || p?.name || id)}</a>${tags}`;
              }

              // Plain-language explanations behind every ⓘ.
              const GLOSSARY = {
                energy: "Energy is spent at the gym and on attacks (25 per attack). It refills 5 every 15 minutes, up to your maximum.",
                nerve: "Nerve is spent on crimes. It refills 1 every 5 minutes.",
                happy: "Happiness makes gym training more effective. Training uses some of it up.",
                life: "Life is your health in a fight. If it reaches zero, you go to hospital for a while.",
                networth: "Everything you own added up: cash, items, property and investments.",
                spendable: "Cash you can spend right now: your wallet plus your vault.",
                points: "A premium currency, mostly used for energy and nerve refills. You can buy them from other players.",
                stats: "Strength, defence, speed and dexterity added together. They decide who wins fights. Other players can't see yours.",
                education: "Courses take days to finish and give permanent bonuses. Keeping one running is free progress.",
                circulation: "How many of this item exist in the whole game. If it's falling, people are using it up faster than new ones appear.",
                market: "Market value is Torn's estimate of what the item sells for, based on recent sales.",
                candidate: "An item that's tradeable and that no shop sells, here or abroad. With limited supply, the price can rise as people use it up.",
                chain: "A chain is a run of attacks by faction members, each within 5 minutes of the last. Longer chains earn the faction more respect.",
                respect: "Respect is a faction's score. It's earned mostly from attacks and wars, and pays for faction upgrades.",
                advisor: "The advisor sends the numbers this dashboard has collected to Google Gemini and asks for suggestions. It's told to use only that data, but it can still be wrong. Check before you spend.",
                cooldown: "After using a drug, booster or medical item you have to wait before using another of the same kind.",
              };
              let infoId = 0;
              const info = key => `<button class="i" aria-expanded="false" aria-controls="gl${++infoId}" title="What's this?">i</button>`;
              const infoRow = key => `<div class="more" id="gl${infoId}">${esc(GLOSSARY[key])}</div>`;

              document.addEventListener("click", e => {
                const b = e.target.closest(".i, .row[data-more]");
                if (!b) return;
                const panel = b.classList.contains("i") ? document.getElementById(b.getAttribute("aria-controls")) : b.nextElementSibling;
                if (!panel || !panel.classList.contains("more")) return;
                const open = !panel.classList.contains("open");
                panel.classList.toggle("open", open);
                b.setAttribute("aria-expanded", open);
              });
              document.addEventListener("keydown", e => {
                if ((e.key === "Enter" || e.key === " ") && e.target.matches(".row[data-more], .tile")){ e.preventDefault(); e.target.click(); }
              });

              class AuthError extends Error {}
              const getJSON = async (u, opt) => { const r = await fetch(u, opt); const j = await r.json().catch(() => ({}));
              if (r.status === 401){ showLogin(); throw new AuthError(j.detail || "Signed out"); }
              if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : "Something went wrong."); return j; };
              const sendJSON = (u, method, body) => getJSON(u, {method, headers: {"Content-Type": "application/json"}, body: body === undefined ? undefined : JSON.stringify(body)});
              const api = {
                get: u => getJSON(u),
                post: (u, b) => sendJSON(u, "POST", b ?? {}),
                put: (u, b) => sendJSON(u, "PUT", b),
                del: u => sendJSON(u, "DELETE"),
              };
              const group = (rows, empty) => rows.length ? rows.join("") : `<div class="empty">${empty}</div>`;

              function spark(points, w = 88, h = 26){
                if (!points || points.length < 2) return "";
                const ys = points.map(p => p[1]), lo = Math.min(...ys), hi = Math.max(...ys);
                const d = points.map((p,i) => `${(i/(points.length-1)*w).toFixed(1)},${(h-2-(hi===lo?(h-4)/2:(p[1]-lo)/(hi-lo)*(h-4))).toFixed(1)}`).join(" ");
                return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline points="${d}" fill="none" stroke="var(--action)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
              }

              // "Mine / Faction" switches. onChange(name) fires when a switch changes.
              const scopeListeners = {};
              function scopeOf(name){ return state.scope[name] || "me"; }
              function renderScopes(){
                const fid = state.spaces.faction;
                $$("[data-scope-for]").forEach(el => {
                  const name = el.dataset.scopeFor;
                  if (!fid){ state.scope[name] = "me"; el.hidden = true; return; }
                  el.hidden = false;
                  const cur = scopeOf(name);
                  el.innerHTML = `<button type="button" data-s="me" aria-selected="${cur === "me"}">Mine</button>` +
                  `<button type="button" data-s="faction" aria-selected="${cur === "faction"}">${esc(state.spaces.faction_name || "Faction")}</button>`;
                  el.querySelectorAll("button").forEach(b => b.onclick = () => {
                    state.scope[name] = b.dataset.s; renderScopes(); (scopeListeners[name] || (() => {}))();
                  });
                });
              }

              // Opens the side sheet with a title and HTML content.
              function openDetail(title, html){
                infoId = 1000;
                $("#detail-title").textContent = title;
                $("#detail-body").innerHTML = html;
                if (!$("#detail").open) $("#detail").showModal();
              }

              function toast(msg){
                let t = $("#toast");
                if (!t){ t = document.createElement("div"); t.id = "toast"; t.setAttribute("role", "status");
                  t.style.cssText = "position:fixed;left:50%;bottom:90px;transform:translateX(-50%);background:var(--glass-strong);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid var(--hair);padding:10px 18px;border-radius:999px;box-shadow:var(--shadow);z-index:99;font-size:15px";
                  document.body.appendChild(t); }
                  t.textContent = msg; t.hidden = false;
                  clearTimeout(t._h); t._h = setTimeout(() => t.hidden = true, 2600);
              }
              const fail = e => { if (!(e instanceof AuthError)) toast(e.message); };
