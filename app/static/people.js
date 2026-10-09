// PEOPLE: players you've tagged, privately or for your whole faction.
let peopleList = [], peopleTag = "";
scopeListeners.person = () => {};

async function loadPeopleMap(){
  try {
    peopleList = (await api.get("/api/people")).people;
    state.people = {};
    // Your own tags win over faction tags when both exist.
    [...peopleList].sort((a, b) => (a.scope === "me") - (b.scope === "me")).forEach(p => {
      state.people[p.target_id] = {...p, tags: [...new Set([...(state.people[p.target_id]?.tags || []), ...p.tags])]};
    });
  } catch(e){ fail(e); }
}

async function loadPeople(){
  await loadPeopleMap();
  const q = $("#people-q").value.trim().toLowerCase();
  const counts = {};
  peopleList.forEach(p => p.tags.forEach(t => counts[t] = (counts[t] || 0) + 1));
  $("#people-tags").innerHTML = Object.entries(counts).sort((a, b) => b[1] - a[1])
    .map(([t, n]) => `<button class="chip" aria-pressed="${t === peopleTag}" data-t="${esc(t)}">${esc(t)} <span class="small-muted">${n}</span></button>`).join("");
  $$("#people-tags .chip").forEach(c => c.onclick = () => { peopleTag = peopleTag === c.dataset.t ? "" : c.dataset.t; loadPeople(); });
  const shown = peopleList.filter(p => (!peopleTag || p.tags.includes(peopleTag)) &&
    (!q || (p.name || "").toLowerCase().includes(q) || (p.note || "").toLowerCase().includes(q) || String(p.target_id).includes(q)));
  $("#people-list").innerHTML = group(shown.map(p => `
    <div class="person-row"><span class="av">${esc((p.name || "?")[0].toUpperCase())}</span>
      <div class="main"><div class="title"><a href="https://www.torn.com/profiles.php?XID=${p.target_id}" target="_blank" rel="noopener">${esc(p.name || p.target_id)}</a>
        <span class="small-muted">[${p.target_id}]${p.level ? " · level " + p.level : ""}${p.scope === "faction" ? " · faction" : ""}</span></div>
        <div>${p.tags.map(tagChip).join("")}</div>${p.note ? `<div class="note-t">${linkify(p.note)}</div>` : ""}</div>
      <button class="icon-btn" title="Edit" onclick="editPerson('${p.scope}', ${p.target_id})">✎</button>
      <button class="icon-btn" title="Remove" onclick="delPerson('${p.scope}', ${p.target_id})">×</button>
    </div>`), peopleList.length ? "No one matches." : "Tag a player by pasting their id or profile link above.");
}
$("#people-q").oninput = () => loadPeople();

function editPerson(scope, id){
  const p = peopleList.find(x => x.scope === scope && x.target_id === id); if (!p) return;
  $("#person-id").value = id; $("#person-tags").value = p.tags.join(", "); $("#person-note").value = p.note || "";
  state.scope.person = scope; renderScopes();
  $("#person-form").scrollIntoView({behavior: "smooth", block: "center"});
}
async function delPerson(scope, id){ if (!confirm("Remove this player's tags?")) return;
  try { await api.del(`/api/people/${scope}/${id}`); loadPeople(); } catch(e){ fail(e); } }

$("#person-form").onsubmit = async e => {
  e.preventDefault();
  const raw = $("#person-id").value.trim();
  const id = parseInt((raw.match(/XID=(\d+)/i) || raw.match(/\[(\d+)\]/) || raw.match(/(\d{1,9})/) || [])[1]);
  const err = $("#person-err");
  if (!id){ err.textContent = "Paste a player id like 2636703 or their profile link."; err.hidden = false; return; }
  err.hidden = true; $("#person-save").disabled = true;
  try {
    const r = await api.put("/api/people", {scope: scopeOf("person"), target_id: id, tags: splitTags($("#person-tags").value), note: $("#person-note").value.trim() || null});
    $("#person-form").reset(); toast(`Saved ${r.name || id}`); loadPeople();
  } catch(ex){ if (!(ex instanceof AuthError)){ err.textContent = ex.message; err.hidden = false; } }
  $("#person-save").disabled = false;
};
