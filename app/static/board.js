// BOARD: columns of cards (drag between them) and a freeform canvas of sticky notes with arrows.
let boardView = "cols", board = {columns: [], cards: []}, canvasItems = [], busy = false, linkFrom = null, editingCard = null;
const CARD_COLORS = ["", "yellow", "green", "blue", "pink", "purple"];
const NOTE_COLORS = ["", "green", "blue", "pink", "purple", "white"];
let noteColor = "";

function setBoardView(v){
  boardView = v;
  $$("#board-view button").forEach(b => b.setAttribute("aria-selected", b.dataset.v === v));
  $("#kanban").hidden = v !== "cols"; $("#col-add").hidden = v !== "cols";
  $("#canvas-wrap").hidden = v !== "canvas"; $("#canvas-tools").hidden = v !== "canvas";
  loadBoard();
}
$$("#board-view button").forEach(b => b.onclick = () => setBoardView(b.dataset.v));
scopeListeners.board = () => loadBoard();

async function loadBoard(force){
  if (busy && !force) return;
  try {
    if (boardView === "cols"){ board = await api.get(`/api/board?scope=${scopeOf("board")}`); renderKanban(); }
    else { canvasItems = (await api.get(`/api/canvas?scope=${scopeOf("board")}`)).items; renderCanvas(); }
  } catch(e){ fail(e); }
}

// ---------- columns ----------
function renderKanban(){
  const shared = scopeOf("board") === "faction";
  $("#kanban").innerHTML = board.columns.map(col => {
    const cards = board.cards.filter(c => c.col === col.key);
    return `<div class="kcol glass" data-col="${esc(col.key)}">
      <div class="kcol-h"><b>${esc(col.title)}</b><span class="count">${cards.length}</span>
        <button class="icon-btn" title="Rename or remove column" onclick="colMenu('${esc(col.key)}')">⋯</button></div>
      <div class="kcards" data-col="${esc(col.key)}">${cards.map(c => `
        <div class="kcard ${c.color ? "c-" + esc(c.color) : ""}" draggable="true" data-id="${c.id}" tabindex="0">
          <div>${esc(c.title)}</div>${c.body ? `<div class="b">${esc(c.body)}</div>` : ""}
          ${shared && c.author_name ? `<div class="by">${esc(c.author_name)}</div>` : ""}</div>`).join("")}</div>
      <button class="kadd" onclick="quickCard('${esc(col.key)}')">+ Add a card</button>
    </div>`; }).join("");

  $$("#kanban .kcard").forEach(el => {
    el.onclick = () => openCard(+el.dataset.id);
    el.onkeydown = e => { if (e.key === "Enter") openCard(+el.dataset.id); };
    el.ondragstart = e => { busy = true; el.classList.add("dragging"); e.dataTransfer.setData("text/plain", el.dataset.id); e.dataTransfer.effectAllowed = "move"; };
    el.ondragend = () => { el.classList.remove("dragging"); busy = false; };
  });
  $$("#kanban .kcards").forEach(zone => {
    zone.ondragover = e => { e.preventDefault(); zone.classList.add("over"); };
    zone.ondragleave = () => zone.classList.remove("over");
    zone.ondrop = async e => {
      e.preventDefault(); zone.classList.remove("over");
      const id = +e.dataTransfer.getData("text/plain"), card = board.cards.find(c => c.id === id);
      if (!card) return;
      // Place it above whichever card the pointer is over (or at the end).
      const siblings = [...zone.querySelectorAll(".kcard:not(.dragging)")];
      const after = siblings.find(s => e.clientY < s.getBoundingClientRect().top + s.offsetHeight / 2);
      const list = board.cards.filter(c => c.col === zone.dataset.col && c.id !== id).sort((a, b) => a.position - b.position);
      const idx = after ? list.findIndex(c => c.id === +after.dataset.id) : list.length;
      const before = list[idx - 1]?.position ?? 0, next = list[idx]?.position;
      const position = next == null ? before + 1 : (before + next) / 2;
      Object.assign(card, {col: zone.dataset.col, position});
      renderKanban(); busy = false;
      try { await api.put(`/api/board/cards/${id}`, {scope: scopeOf("board"), col: card.col, title: card.title, body: card.body || "", color: card.color || "", position}); }
      catch(err){ fail(err); loadBoard(); }
    };
  });
}

async function quickCard(col){
  const title = prompt("Card title");
  if (!title || !title.trim()) return;
  try { await api.post("/api/board/cards", {scope: scopeOf("board"), col, title: title.trim()}); loadBoard(); } catch(e){ fail(e); }
}

function openCard(id){
  const c = board.cards.find(x => x.id === id); if (!c) return;
  editingCard = c;
  $("#card-title").value = c.title; $("#card-body").value = c.body || "";
  $("#card-col").innerHTML = board.columns.map(col => `<option value="${esc(col.key)}" ${col.key === c.col ? "selected" : ""}>${esc(col.title)}</option>`).join("");
  $("#card-colors").innerHTML = CARD_COLORS.map(k => `<button type="button" class="chip ${k ? "c-" + k : ""}" aria-pressed="${(c.color || "") === k}" data-c="${k}">${k || "None"}</button>`).join("");
  $$("#card-colors .chip").forEach(b => b.onclick = () => $$("#card-colors .chip").forEach(x => x.setAttribute("aria-pressed", x === b)));
  $("#card-dialog").showModal();
}
$("#card-dialog").addEventListener("close", async () => {
  const c = editingCard, v = $("#card-dialog").returnValue; editingCard = null;
  if (!c) return;
  try {
    if (v === "delete"){ if (confirm("Delete this card?")){ await api.del(`/api/board/cards/${c.id}`); } }
    else if (v === "save"){
      const color = $("#card-colors [aria-pressed=true]")?.dataset.c || "";
      const col = $("#card-col").value, moved = col !== c.col;
      const position = moved ? Math.max(0, ...board.cards.filter(x => x.col === col).map(x => x.position)) + 1 : c.position;
      await api.put(`/api/board/cards/${c.id}`, {scope: scopeOf("board"), title: $("#card-title").value.trim() || c.title, body: $("#card-body").value, color, col, position});
    }
    loadBoard();
  } catch(e){ fail(e); }
});

async function saveColumns(cols){ try { await api.put("/api/board/columns", {scope: scopeOf("board"), columns: cols}); loadBoard(); } catch(e){ fail(e); } }
$("#col-add").onclick = () => {
  const title = prompt("New column name"); if (!title || !title.trim()) return;
  saveColumns([...board.columns, {key: title.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-") + "-" + Date.now().toString(36).slice(-3), title: title.trim()}]);
};
function colMenu(key){
  const col = board.columns.find(c => c.key === key); if (!col) return;
  const name = prompt(`Rename "${col.title}" (leave empty to remove the column)`, col.title);
  if (name === null) return;
  if (!name.trim()){
    if (board.cards.some(c => c.col === key)) return toast("Move or delete its cards first.");
    return saveColumns(board.columns.filter(c => c.key !== key));
  }
  saveColumns(board.columns.map(c => c.key === key ? {...c, title: name.trim()} : c));
}

// ---------- canvas ----------
function renderCanvas(){
  const cv = $("#canvas");
  cv.querySelectorAll(".sticky").forEach(n => n.remove());
  const notes = canvasItems.filter(i => i.kind === "note");
  notes.forEach(n => {
    const el = document.createElement("div");
    el.className = `sticky ${n.color ? "s-" + n.color : ""}`;
    el.dataset.id = n.id;
    Object.assign(el.style, {left: n.x + "px", top: n.y + "px", width: n.w + "px", height: n.h + "px"});
    el.innerHTML = `<div class="grip"><button title="Colour" data-a="color">●</button><button title="Delete" data-a="del">×</button></div>
      <textarea aria-label="Note">${esc(n.text)}</textarea><div class="rs" title="Resize"></div>`;
    if (linkFrom === n.id) el.classList.add("picked");
    cv.appendChild(el);
    wireSticky(el, n);
  });
  drawArrows();
}

function drawArrows(){
  const byId = Object.fromEntries(canvasItems.filter(i => i.kind === "note").map(n => [n.id, n]));
  const center = n => [n.x + n.w / 2, n.y + n.h / 2];
  // Shorten each line so the arrowhead lands on the edge of the target note.
  const edge = (from, to) => { const [x1, y1] = center(from), [x2, y2] = center(to);
    const dx = x2 - x1, dy = y2 - y1, tx = Math.abs(dx) ? (to.w / 2 + 6) / Math.abs(dx) : Infinity, ty = Math.abs(dy) ? (to.h / 2 + 6) / Math.abs(dy) : Infinity;
    const t = Math.min(tx, ty, 1); return [x1, y1, x2 - dx * t, y2 - dy * t]; };
  const lines = canvasItems.filter(i => i.kind === "arrow" && byId[i.from_id] && byId[i.to_id]).map(a => {
    const [x1, y1, x2, y2] = edge(byId[a.from_id], byId[a.to_id]);
    return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" marker-end="url(#ah)" data-id="${a.id}"><title>Click to remove</title></line>`; }).join("");
  $("#cv-svg").innerHTML = `<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" fill="var(--muted)"/></marker></defs>${lines}`;
  $$("#cv-svg line").forEach(l => l.onclick = async () => { if (!confirm("Remove this arrow?")) return;
    try { await api.del(`/api/canvas/${l.dataset.id}`); loadBoard(); } catch(e){ fail(e); } });
}

function saveNote(n){ return api.put(`/api/canvas/${n.id}`, {scope: scopeOf("board"), kind: "note", x: n.x, y: n.y, w: n.w, h: n.h, text: n.text, color: n.color || ""}).catch(fail); }

function wireSticky(el, n){
  const ta = el.querySelector("textarea");
  let t;
  ta.onfocus = () => { busy = true; };
  ta.oninput = () => { n.text = ta.value; clearTimeout(t); t = setTimeout(() => saveNote(n), 600); };
  ta.onblur = () => { busy = false; clearTimeout(t); saveNote(n); };
  const linking = () => $("#cv-link").getAttribute("aria-pressed") === "true";
  // While connecting, a tap picks the note instead of starting to type in it.
  el.addEventListener("pointerdown", e => { if (linking() && !e.target.closest("button")){ e.preventDefault(); document.activeElement?.blur(); } }, true);
  el.onclick = async e => {
    if (!linking() || e.target.closest("button")) return;
    if (!linkFrom){ linkFrom = n.id; el.classList.add("picked"); $("#cv-hint").textContent = "Now click the note it leads to."; return; }
    if (linkFrom === n.id) return;
    try { await api.post("/api/canvas", {scope: scopeOf("board"), kind: "arrow", from_id: linkFrom, to_id: n.id}); } catch(err){ fail(err); }
    linkFrom = null; busy = false; $("#cv-hint").textContent = "Click a note, then the note it leads to."; loadBoard(true);
  };
  el.querySelector("[data-a=del]").onclick = async () => { if (!confirm("Delete this note?")) return;
    try { await api.del(`/api/canvas/${n.id}`); loadBoard(); } catch(e){ fail(e); } };
  el.querySelector("[data-a=color]").onclick = () => {
    n.color = NOTE_COLORS[(NOTE_COLORS.indexOf(n.color || "") + 1) % NOTE_COLORS.length];
    el.className = `sticky ${n.color ? "s-" + n.color : ""}`; saveNote(n);
  };
  // Drag by the top strip, resize from the corner. Pointer events work for mouse and touch.
  const drag = (handle, onMove) => handle.addEventListener("pointerdown", e => {
    if (e.target.closest("button")) return;
    e.preventDefault(); busy = true; handle.setPointerCapture(e.pointerId);
    const sx = e.clientX, sy = e.clientY, start = {...n};
    const move = ev => { onMove(ev.clientX - sx, ev.clientY - sy, start); drawArrows(); };
    const up = () => { handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", up); busy = false; saveNote(n); };
    handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", up);
  });
  drag(el.querySelector(".grip"), (dx, dy, s) => { n.x = Math.max(0, Math.min(2200, s.x + dx)); n.y = Math.max(0, Math.min(1500, s.y + dy)); el.style.left = n.x + "px"; el.style.top = n.y + "px"; });
  drag(el.querySelector(".rs"), (dx, dy, s) => { n.w = Math.max(120, Math.min(600, s.w + dx)); n.h = Math.max(90, Math.min(600, s.h + dy)); el.style.width = n.w + "px"; el.style.height = n.h + "px"; });
}

$("#cv-add").onclick = async () => {
  const wrap = $("#canvas-wrap");
  const x = wrap.scrollLeft + 40 + Math.random() * 60, y = wrap.scrollTop + 40 + Math.random() * 60;
  try { await api.post("/api/canvas", {scope: scopeOf("board"), kind: "note", x, y, w: 200, h: 140, text: "", color: noteColor}); await loadBoard();
        const last = [...$$("#canvas .sticky")].pop(); last?.querySelector("textarea").focus(); } catch(e){ fail(e); }
};
$("#cv-link").onclick = () => {
  const on = $("#cv-link").getAttribute("aria-pressed") !== "true";
  $("#cv-link").setAttribute("aria-pressed", on); $("#cv-link").classList.toggle("quiet", !on);
  linkFrom = null; $$("#canvas .sticky.picked").forEach(s => s.classList.remove("picked"));
  $("#cv-hint").textContent = on ? "Click a note, then the note it leads to." : "";
};
