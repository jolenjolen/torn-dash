// NOTES: journal, checklists and links, each in your own or your faction's space.
let notesView = "journal", noteTag = "", editingNote = null, autoTypes = {}, notesById = {};

function setNotesView(v){
  notesView = v;
  $$("#notes-view button").forEach(b => b.setAttribute("aria-selected", b.dataset.v === v));
  $("#notes-journal").hidden = v !== "journal";
  $("#notes-lists").hidden = v !== "lists";
  $("#notes-links").hidden = v !== "links";
  loadNotes();
}
$$("#notes-view button").forEach(b => b.onclick = () => setNotesView(b.dataset.v));
scopeListeners.notes = () => { noteTag = ""; loadNotes(); };

async function loadNotes(){
  try {
    if (notesView === "journal") await loadJournal();
    else if (notesView === "lists") await loadLists();
    else await loadLinks();
  } catch(e){ fail(e); }
}

// ---------- journal ----------
async function loadJournal(){
  const q = $("#note-q").value.trim();
  const d = await api.get(`/api/notes?scope=${scopeOf("notes")}&q=${encodeURIComponent(q)}&tag=${encodeURIComponent(noteTag)}`);
  $("#note-tags-filter").innerHTML = d.tags.map(([t, n]) => `<button class="chip" aria-pressed="${t === noteTag}" data-t="${esc(t)}">${esc(t)} <span class="small-muted">${n}</span></button>`).join("");
  $$("#note-tags-filter .chip").forEach(c => c.onclick = () => { noteTag = noteTag === c.dataset.t ? "" : c.dataset.t; loadJournal(); });
  notesById = Object.fromEntries(d.notes.map(n => [n.id, n]));
  $("#note-list").innerHTML = d.notes.length ? d.notes.map(n => `
    <article class="note glass ${n.pinned ? "pinned" : ""}" style="border-radius:var(--radius)">
      ${n.title ? `<h4>${esc(n.title)}</h4>` : ""}
      <div class="meta">${dateLabel(n.created)}${n.updated - n.created > 60 ? ` · edited ${ago(n.updated)}` : ""}${scopeOf("notes") === "faction" && n.author_name ? ` · ${esc(n.author_name)}` : ""}${n.pinned ? " · pinned" : ""}</div>
      <div class="body" onclick="this.classList.toggle('open')">${linkify(n.body)}</div>
      <div class="foot"><div class="chips">${n.tags.map(tagChip).join("")}</div>
        <button class="icon-btn" title="${n.pinned ? "Unpin" : "Pin"}" onclick="pinNote(${n.id})">★</button>
        <button class="icon-btn" title="Edit" onclick="editNote(${n.id})">✎</button>
        <button class="icon-btn" title="Delete" onclick="delNote(${n.id})">×</button></div>
    </article>`).join("")
    : `<div class="empty">${q || noteTag ? "No notes match." : "No notes yet. Your first one could be what you learned today."}</div>`;
}
let qTimer;
$("#note-q").oninput = () => { clearTimeout(qTimer); qTimer = setTimeout(loadJournal, 250); };

function editNote(id){
  const n = notesById[id]; if (!n) return;
  editingNote = n.id;
  $("#note-title").value = n.title || ""; $("#note-body").value = n.body || ""; $("#note-tags").value = n.tags.join(", ");
  $("#note-save").textContent = "Save changes";
  $("#note-form").scrollIntoView({behavior: "smooth", block: "center"}); $("#note-body").focus();
}
async function pinNote(id){ const n = notesById[id]; try { await api.put(`/api/notes/${n.id}`, {...n, scope: scopeOf("notes"), pinned: !n.pinned}); loadJournal(); } catch(e){ fail(e); } }
async function delNote(id){ if (!confirm("Delete this note?")) return; try { await api.del(`/api/notes/${id}`); loadJournal(); } catch(e){ fail(e); } }
$("#note-form").onsubmit = async e => {
  e.preventDefault();
  const body = {scope: scopeOf("notes"), title: $("#note-title").value.trim(), body: $("#note-body").value, tags: splitTags($("#note-tags").value)};
  try {
    if (editingNote) await api.put(`/api/notes/${editingNote}`, body); else await api.post("/api/notes", body);
    editingNote = null; $("#note-form").reset(); $("#note-save").textContent = "Save note"; toast("Saved"); loadJournal();
  } catch(e){ fail(e); }
};

// ---------- checklists ----------
async function loadLists(){
  const d = await api.get(`/api/checklists?scope=${scopeOf("notes")}`);
  autoTypes = d.auto_types;
  const sel = $("#cl-template");
  if (sel.options.length === 1) Object.entries(d.templates).forEach(([k, v]) => sel.add(new Option(`Start from: ${v}`, k)));
  const personal = scopeOf("notes") === "me";
  $("#lists").innerHTML = d.checklists.length ? d.checklists.map(cl => {
    const done = cl.items.filter(i => i.done).length;
    return `<div class="group">
      <div class="panel-h"><h3>${esc(cl.title)}</h3><span class="small-muted">${done}/${cl.items.length}</span>
        <button class="icon-btn" title="Delete list" onclick="delList(${cl.id})">×</button></div>
      <div style="padding:0 18px 10px"><div class="progress"><span style="width:${cl.items.length ? done / cl.items.length * 100 : 0}%"></span></div></div>
      ${cl.items.map(i => `<div class="cl-item ${i.done ? "done" : ""}">
        <input type="checkbox" ${i.done ? "checked" : ""} ${i.auto && i.auto_state ? "disabled" : ""} onchange="checkItem(${i.id}, this.checked)" aria-label="${esc(i.text)}">
        <span class="txt">${esc(i.text)}</span>${i.auto ? `<span class="auto" title="Ticks itself from your Torn data">auto</span>` : ""}
        <button class="icon-btn" title="Remove" onclick="delItem(${i.id})">×</button></div>`).join("")}
      <form class="cl-add" onsubmit="addItem(event, ${cl.id})">
        <input type="text" placeholder="Add an item" maxlength="200">
        ${personal ? `<select title="Tick automatically when…"><option value="">Manual</option>${Object.entries(autoTypes).map(([k, v]) => `<option value="${k}">Auto: ${esc(v.replace(/\{[^}]*\}/g, "…"))}</option>`).join("")}</select>` : ""}
        <button class="btn small">Add</button></form>
    </div>`; }).join("")
    : `<div class="empty">No checklists yet. Pick a starter template above, or make your own.</div>`;
}
$("#cl-form").onsubmit = async e => {
  e.preventDefault();
  const tpl = $("#cl-template").value;
  const title = $("#cl-title").value.trim() || ($("#cl-template").selectedOptions[0]?.text || "").replace("Start from: ", "");
  if (!title) return toast("Name the checklist.");
  try { await api.post("/api/checklists", {scope: scopeOf("notes"), title, template: tpl || null}); $("#cl-form").reset(); loadLists(); } catch(e){ fail(e); }
};
async function addItem(e, cid){
  e.preventDefault();
  const f = e.target, text = f.querySelector("input").value.trim(), type = f.querySelector("select")?.value;
  let auto = null;
  if (type){
    let value = 1;
    if (["level", "stats", "networth", "cash", "courses"].includes(type)){ value = parseInt(text.replace(/[^\d]/g, "")); if (!value) return toast("Type the number for this goal, e.g. 15"); }
    if (type === "item"){ value = text; if (!value) return toast("Type the item name"); }
    auto = {type, value};
  }
  try { await api.post(`/api/checklists/${cid}/items`, {text: type ? "" : text, auto}); loadLists(); } catch(err){ fail(err); }
}
async function checkItem(id, done, fromToday){ try { await api.put(`/api/checklist-items/${id}`, {done}); fromToday ? loadToday() : loadLists(); } catch(e){ fail(e); } }
async function delItem(id){ try { await api.del(`/api/checklist-items/${id}`); loadLists(); } catch(e){ fail(e); } }
async function delList(id){ if (!confirm("Delete this checklist?")) return; try { await api.del(`/api/checklists/${id}`); loadLists(); } catch(e){ fail(e); } }

// ---------- links ----------
async function loadLinks(){
  const d = await api.get("/api/links");
  const scope = scopeOf("notes");
  const mine = d.links.filter(l => l.scope === scope);
  const groups = {};
  mine.forEach(l => (groups[l.category || "other"] ||= []).push(l));
  $("#link-list").innerHTML = mine.length ? Object.entries(groups).map(([cat, ls]) => `
    <h2 style="font-size:18px;margin:22px 4px 8px">${esc(cat[0].toUpperCase() + cat.slice(1))}</h2>
    <div class="group">${ls.map(l => { let host = ""; try { host = new URL(l.url).hostname.replace(/^www\./, ""); } catch(e){}
      return `<div class="link-row"><span class="fav">${esc((host || l.title)[0].toUpperCase())}</span>
        <div class="main"><a href="${esc(l.url)}" target="_blank" rel="noopener" class="title">${esc(l.title)}</a><div class="u">${esc(host || l.url)}</div></div>
        <button class="icon-btn" title="Remove" onclick="delLink(${l.id})">×</button></div>`; }).join("")}</div>`).join("")
    : `<div class="empty">Keep your faction Discord, guides and forum threads here.</div>`;
}
$("#link-form").onsubmit = async e => {
  e.preventDefault();
  let url = $("#link-url").value.trim();
  if (url && !/^[a-z]+:\/\//.test(url)) url = "https://" + url;
  try { await api.post("/api/links", {scope: scopeOf("notes"), title: $("#link-title").value.trim() || url, url, category: $("#link-cat").value.trim()});
        $("#link-form").reset(); loadLinks(); } catch(err){ fail(err); }
};
async function delLink(id){ try { await api.del(`/api/links/${id}`); loadLinks(); } catch(e){ fail(e); } }
