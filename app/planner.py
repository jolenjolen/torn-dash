"""Journal, checklists, reminders, people, links and boards.

Every record lives in a scope: 'me' (private to the player) or 'faction' (shared with every
member of the player's faction who uses this dashboard). Faction membership comes from the
player's own Torn profile, so leaving a faction removes access automatically.
"""
import json
import re
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import analytics, db, timers, users
from .auth import current_player
from .players import client as player_client
from .torn import TornError

router = APIRouter(prefix="/api")
Scope = Literal["me", "faction"]
LEADERS = {"leader", "co-leader"}


# ---------------- scope helpers ----------------
def _faction(pid):
    profile, _ = db.uget(pid, "profile")
    f = (profile or {}).get("faction") or {}
    return f.get("faction_id") or 0, (f.get("position") or "").lower()


def owner_for(pid, scope: str) -> int:
    if scope == "me":
        return pid
    fid, _ = _faction(pid)
    if not fid:
        raise HTTPException(403, "Join a faction to use its shared space.")
    return fid


def _can_delete(pid, row):
    if row["scope"] == "me":
        return True
    _, position = _faction(pid)
    return row.get("author") == pid or position in LEADERS


def _get_owned(table, item_id, pid):
    row = db.one(f"SELECT * FROM {table} WHERE id=?", (item_id,))
    if not row:
        raise HTTPException(404, "Not found.")
    if row["owner"] != owner_for(pid, row["scope"]):
        raise HTTPException(404, "Not found.")
    return row


def _now():
    return int(time.time())


def _clean_tags(tags):
    out = []
    for t in tags or []:
        t = str(t).strip().lower()[:24]
        if t and t not in out:
            out.append(t)
    return out[:12]


def _jrow(r, *fields):
    for f in fields:
        r[f] = json.loads(r[f]) if r.get(f) else ([] if f == "tags" else None)
    return r


@router.get("/spaces")
def spaces(pid: int = Depends(current_player)):
    fid, position = _faction(pid)
    profile, _ = db.uget(pid, "profile")
    return {"me": pid, "faction": fid or None, "position": position,
            "faction_name": ((profile or {}).get("faction") or {}).get("faction_name")}


# ---------------- journal ----------------
class Note(BaseModel):
    scope: Scope = "me"
    title: str = Field(default="", max_length=160)
    body: str = Field(default="", max_length=20000)
    tags: list[str] = Field(default_factory=list)
    pinned: bool = False


@router.get("/notes")
def list_notes(scope: Scope = "me", q: str = "", tag: str = "", pid: int = Depends(current_player)):
    owner = owner_for(pid, scope)
    sql = "SELECT * FROM notes WHERE scope=? AND owner=?"
    params = [scope, owner]
    if q:
        sql += " AND (title LIKE ? OR body LIKE ?)"
        params += [f"%{q[:60]}%"] * 2
    if tag:
        sql += " AND tags LIKE ?"
        params.append(f'%"{tag.lower()[:24]}"%')
    rows = [_jrow(r, "tags") for r in db.query(sql + " ORDER BY pinned DESC, updated DESC LIMIT 300", params)]
    all_tags = {}
    for r in db.query("SELECT tags FROM notes WHERE scope=? AND owner=?", (scope, owner)):
        for t in json.loads(r["tags"] or "[]"):
            all_tags[t] = all_tags.get(t, 0) + 1
    names = _names({r["author"] for r in rows})
    for r in rows:
        r["author_name"] = names.get(r["author"])
    return {"notes": rows, "tags": sorted(all_tags.items(), key=lambda x: -x[1])}


@router.post("/notes")
def add_note(n: Note, pid: int = Depends(current_player)):
    if not (n.title.strip() or n.body.strip()):
        raise HTTPException(400, "Write something first.")
    now = _now()
    cur = db.execute("INSERT INTO notes (scope, owner, author, title, body, tags, pinned, created, updated) "
                     "VALUES (?,?,?,?,?,?,?,?,?)",
                     (n.scope, owner_for(pid, n.scope), pid, n.title.strip(), n.body, json.dumps(_clean_tags(n.tags)),
                      int(n.pinned), now, now))
    return {"id": cur.lastrowid}


@router.put("/notes/{note_id}")
def edit_note(note_id: int, n: Note, pid: int = Depends(current_player)):
    _get_owned("notes", note_id, pid)
    db.execute("UPDATE notes SET title=?, body=?, tags=?, pinned=?, updated=? WHERE id=?",
               (n.title.strip(), n.body, json.dumps(_clean_tags(n.tags)), int(n.pinned), _now(), note_id))
    return {"ok": True}


@router.delete("/notes/{note_id}")
def delete_note(note_id: int, pid: int = Depends(current_player)):
    row = _get_owned("notes", note_id, pid)
    if not _can_delete(pid, row):
        raise HTTPException(403, "Only the author or a faction leader can delete this.")
    db.execute("DELETE FROM notes WHERE id=?", (note_id,))
    return {"ok": True}


# ---------------- checklists ----------------
# Conditions the dashboard can tick off by itself from the player's Torn data.
AUTO = {
    "level":    ("Reach level {n}", lambda d, n: (d["profile"].get("level") or 0) >= n),
    "stats":    ("Battle stats total {n:,}", lambda d, n: (d["stats"].get("total") or 0) >= n),
    "networth": ("Net worth ${n:,}", lambda d, n: (d["money"].get("daily_networth") or 0) >= n),
    "cash":     ("Have ${n:,} to spend", lambda d, n: d["spendable"] >= n),
    "faction":  ("Join a faction", lambda d, n: bool((d["profile"].get("faction") or {}).get("faction_id"))),
    "courses":  ("Finish {n} education course(s)", lambda d, n: len(d["education"].get("complete") or []) >= n),
    "item":     ("Own {item}", lambda d, n: n.lower() in d["items"]),
}


def _player_data(pid):
    g = lambda k: db.uget(pid, k)[0] or {}
    money = g("money")
    items = {r["name"].lower() for r in db.query("SELECT name FROM inventory WHERE player_id=?", (pid,)) if r["name"]}
    eq = g("equipment")
    items |= {x.get("name", "").lower() for x in (eq.get("equipment") or []) + (eq.get("clothing") or [])}
    return {"profile": g("profile"), "stats": g("battlestats"), "money": money, "education": g("education"),
            "spendable": analytics.spendable(money), "items": items}


def _auto_done(auto, data):
    if not auto or auto.get("type") not in AUTO:
        return None
    _, test = AUTO[auto["type"]]
    try:
        return bool(test(data, auto.get("value")))
    except (TypeError, ValueError):
        return None


TEMPLATES = {
    "new-player": ("Getting started", [
        ("Start an education course", {"type": "courses", "value": 1}),
        ("Join a faction", {"type": "faction", "value": 1}),
        ("Reach level 5", {"type": "level", "value": 5}),
        ("Reach level 15", {"type": "level", "value": 15}),
        ("Keep a course running at all times", None),
        ("Bank spare cash so it can't be mugged", None),
    ]),
    "halloween": ("Halloween prep", [
        ("Reach level 5 (needed for the basket)", {"type": "level", "value": 5}),
        ("Own one scary clothing item (masks, Witch's Hat…)", None),
        ("Own a scary weapon (Axe, Cleaver, Chainsaw…)", None),
        ("Buy the basket at Sally's Sweet Shop when it opens ($35)", None),
        ("Save energy for the event; check Torn time", None),
        ("Don't exchange treats in your first year", None),
    ]),
}


class Checklist(BaseModel):
    scope: Scope = "me"
    title: str = Field(min_length=1, max_length=120)
    template: str | None = None


class CheckItem(BaseModel):
    text: str = Field(default="", max_length=200)
    auto: dict | None = None
    done: bool | None = None


def _check_item_text(text, auto):
    if text.strip() or not auto:
        return text.strip()
    label, _ = AUTO.get(auto.get("type"), ("", None))
    v = auto.get("value")
    return label.format(n=v if isinstance(v, int) else 0, item=v)


@router.get("/checklists")
def list_checklists(scope: Scope = "me", pid: int = Depends(current_player)):
    owner = owner_for(pid, scope)
    data = _player_data(pid) if scope == "me" else None
    lists = db.query("SELECT * FROM checklists WHERE scope=? AND owner=? ORDER BY position, id", (scope, owner))
    for cl in lists:
        cl["items"] = db.query("SELECT * FROM checklist_items WHERE checklist_id=? ORDER BY position, id", (cl["id"],))
        for it in cl["items"]:
            it["auto"] = json.loads(it["auto"]) if it["auto"] else None
            if data and it["auto"]:
                auto_done = _auto_done(it["auto"], data)
                if auto_done and not it["done"]:
                    db.execute("UPDATE checklist_items SET done=1, done_at=? WHERE id=?", (_now(), it["id"]))
                    it["done"], it["done_at"] = 1, _now()
                it["auto_state"] = auto_done
    return {"checklists": lists,
            "auto_types": {k: v[0] for k, v in AUTO.items()},
            "templates": {k: v[0] for k, v in TEMPLATES.items()}}


@router.post("/checklists")
def add_checklist(c: Checklist, pid: int = Depends(current_player)):
    now = _now()
    cur = db.execute("INSERT INTO checklists (scope, owner, author, title, created, updated) VALUES (?,?,?,?,?,?)",
                     (c.scope, owner_for(pid, c.scope), pid, c.title.strip(), now, now))
    cid = cur.lastrowid
    if c.template in TEMPLATES:
        for pos, (text, auto) in enumerate(TEMPLATES[c.template][1]):
            db.execute("INSERT INTO checklist_items (checklist_id, text, auto, position) VALUES (?,?,?,?)",
                       (cid, text, json.dumps(auto) if auto and c.scope == "me" else None, pos))
    return {"id": cid}


def _own_checklist(cid, pid):
    return _get_owned("checklists", cid, pid)


@router.put("/checklists/{cid}")
def rename_checklist(cid: int, c: Checklist, pid: int = Depends(current_player)):
    _own_checklist(cid, pid)
    db.execute("UPDATE checklists SET title=?, updated=? WHERE id=?", (c.title.strip(), _now(), cid))
    return {"ok": True}


@router.delete("/checklists/{cid}")
def delete_checklist(cid: int, pid: int = Depends(current_player)):
    row = _own_checklist(cid, pid)
    if not _can_delete(pid, row):
        raise HTTPException(403, "Only the author or a faction leader can delete this.")
    db.execute("DELETE FROM checklist_items WHERE checklist_id=?", (cid,))
    db.execute("DELETE FROM checklists WHERE id=?", (cid,))
    return {"ok": True}


@router.post("/checklists/{cid}/items")
def add_check_item(cid: int, it: CheckItem, pid: int = Depends(current_player)):
    row = _own_checklist(cid, pid)
    auto = it.auto if (it.auto and it.auto.get("type") in AUTO and row["scope"] == "me") else None
    text = _check_item_text(it.text, auto)
    if not text:
        raise HTTPException(400, "Write the item first.")
    pos = (db.one("SELECT MAX(position) AS p FROM checklist_items WHERE checklist_id=?", (cid,)) or {}).get("p") or 0
    db.execute("INSERT INTO checklist_items (checklist_id, text, auto, position) VALUES (?,?,?,?)",
               (cid, text, json.dumps(auto) if auto else None, pos + 1))
    return {"ok": True}


@router.put("/checklist-items/{iid}")
def edit_check_item(iid: int, it: CheckItem, pid: int = Depends(current_player)):
    row = db.one("SELECT * FROM checklist_items WHERE id=?", (iid,))
    if not row:
        raise HTTPException(404, "Not found.")
    _own_checklist(row["checklist_id"], pid)
    if it.done is not None:
        db.execute("UPDATE checklist_items SET done=?, done_at=? WHERE id=?",
                   (int(it.done), _now() if it.done else None, iid))
    if it.text.strip():
        db.execute("UPDATE checklist_items SET text=? WHERE id=?", (it.text.strip(), iid))
    return {"ok": True}


@router.delete("/checklist-items/{iid}")
def delete_check_item(iid: int, pid: int = Depends(current_player)):
    row = db.one("SELECT * FROM checklist_items WHERE id=?", (iid,))
    if not row:
        raise HTTPException(404, "Not found.")
    _own_checklist(row["checklist_id"], pid)
    db.execute("DELETE FROM checklist_items WHERE id=?", (iid,))
    return {"ok": True}


# ---------------- timeline + reminders ----------------
class Reminder(BaseModel):
    scope: Scope = "me"
    title: str = Field(min_length=1, max_length=160)
    note: str | None = Field(default=None, max_length=1000)
    link: str | None = Field(default=None, max_length=500)
    due: int
    repeat: Literal["none", "daily", "weekly", "monthly", "every"] = "none"
    every_days: int | None = Field(default=None, ge=1, le=365)


@router.get("/timeline")
def timeline(pid: int = Depends(current_player)):
    return {"items": timers.build(pid), "kinds": {k: v[0] for k, v in timers.KINDS.items()},
            "server_time": _now()}


@router.post("/reminders")
def add_reminder(r: Reminder, pid: int = Depends(current_player)):
    if r.link and not r.link.startswith(("https://", "http://")):
        raise HTTPException(400, "Links must start with https://")
    cur = db.execute("INSERT INTO reminders (scope, owner, author, title, note, link, due, repeat, every_days, done, created) "
                     "VALUES (?,?,?,?,?,?,?,?,?,0,?)",
                     (r.scope, owner_for(pid, r.scope), pid, r.title.strip(), r.note, r.link, r.due,
                      r.repeat, r.every_days, _now()))
    return {"id": cur.lastrowid}


@router.post("/reminders/{rid}/done")
def finish_reminder(rid: int, pid: int = Depends(current_player)):
    row = _get_owned("reminders", rid, pid)
    nxt = timers.advance_reminder(row)
    if nxt:
        db.execute("UPDATE reminders SET due=? WHERE id=?", (nxt, rid))
    else:
        db.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,))
    return {"ok": True, "next": nxt}


@router.delete("/reminders/{rid}")
def delete_reminder(rid: int, pid: int = Depends(current_player)):
    row = _get_owned("reminders", rid, pid)
    if not _can_delete(pid, row):
        raise HTTPException(403, "Only the author or a faction leader can delete this.")
    db.execute("DELETE FROM reminders WHERE id=?", (rid,))
    return {"ok": True}


class Hide(BaseModel):
    key: str = Field(max_length=200)
    hidden: bool = True
    due: int | None = None


@router.post("/timeline/hide")
def hide_item(h: Hide, pid: int = Depends(current_player)):
    hidden, _ = db.uget(pid, "timeline_hidden")
    entry = f"{h.key}@{h.due}" if h.due else h.key
    hidden = [k for k in (hidden or []) if k.partition("@")[0] != h.key]
    if h.hidden:
        hidden.append(entry)
    db.uput(pid, "timeline_hidden", hidden[-300:])
    return {"ok": True}


# ---------------- people ----------------
class Person(BaseModel):
    scope: Scope = "me"
    target_id: int = Field(gt=0)
    tags: list[str] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=2000)


@router.get("/people")
def list_people(pid: int = Depends(current_player)):
    """Everyone tagged in the player's own space and their faction's space."""
    fid, _ = _faction(pid)
    rows = db.query("""SELECT * FROM people WHERE (scope='me' AND owner=?) OR (scope='faction' AND owner=? AND owner<>0)
                       ORDER BY updated DESC""", (pid, fid))
    return {"people": [_jrow(r, "tags") for r in rows]}


@router.put("/people")
async def save_person(p: Person, pid: int = Depends(current_player)):
    owner = owner_for(pid, p.scope)
    existing = db.one("SELECT name, level FROM people WHERE scope=? AND owner=? AND target_id=?",
                      (p.scope, owner, p.target_id))
    name, level = (existing or {}).get("name"), (existing or {}).get("level")
    if not name:
        t = player_client(pid)
        if t:
            try:
                info = await t.player_basic(p.target_id)
                name, level = info.get("name"), info.get("level")
            except TornError:
                raise HTTPException(404, "Torn doesn't know a player with that id.")
    db.execute("INSERT OR REPLACE INTO people VALUES (?,?,?,?,?,?,?,?,?)",
               (p.scope, owner, p.target_id, name, level, json.dumps(_clean_tags(p.tags)), p.note, pid, _now()))
    return {"ok": True, "name": name}


@router.delete("/people/{scope}/{target_id}")
def delete_person(scope: Scope, target_id: int, pid: int = Depends(current_player)):
    db.execute("DELETE FROM people WHERE scope=? AND owner=? AND target_id=?", (scope, owner_for(pid, scope), target_id))
    return {"ok": True}


# ---------------- links ----------------
class Link(BaseModel):
    scope: Scope = "me"
    title: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=8, max_length=500)
    category: str = Field(default="", max_length=40)


@router.get("/links")
def list_links(pid: int = Depends(current_player)):
    fid, _ = _faction(pid)
    return {"links": db.query("""SELECT * FROM links WHERE (scope='me' AND owner=?) OR (scope='faction' AND owner=? AND owner<>0)
                                 ORDER BY category, title""", (pid, fid))}


@router.post("/links")
def add_link(link: Link, pid: int = Depends(current_player)):
    if not link.url.startswith(("https://", "http://", "discord://")):
        raise HTTPException(400, "Links must start with https://")
    db.execute("INSERT INTO links (scope, owner, author, title, url, category, created) VALUES (?,?,?,?,?,?,?)",
               (link.scope, owner_for(pid, link.scope), pid, link.title.strip(), link.url.strip(),
                link.category.strip().lower(), _now()))
    return {"ok": True}


@router.delete("/links/{lid}")
def delete_link(lid: int, pid: int = Depends(current_player)):
    row = _get_owned("links", lid, pid)
    if not _can_delete(pid, row):
        raise HTTPException(403, "Only the author or a faction leader can delete this.")
    db.execute("DELETE FROM links WHERE id=?", (lid,))
    return {"ok": True}


# ---------------- board: columns ----------------
DEFAULT_COLS = [{"key": "ideas", "title": "Ideas"}, {"key": "doing", "title": "Doing"}, {"key": "done", "title": "Done"}]


def _cols_key(scope, owner):
    return f"board_cols:{scope}:{owner}"


class Card(BaseModel):
    scope: Scope = "me"
    col: str = Field(default="ideas", max_length=40, pattern=r"^[a-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=4000)
    color: str = Field(default="", max_length=20)
    position: float | None = None


class Columns(BaseModel):
    scope: Scope = "me"
    columns: list[dict] = Field(max_length=12)


@router.get("/board")
def get_board(scope: Scope = "me", pid: int = Depends(current_player)):
    owner = owner_for(pid, scope)
    cols, _ = db.get(_cols_key(scope, owner))
    cards = db.query("SELECT * FROM board_cards WHERE scope=? AND owner=? ORDER BY position, id", (scope, owner))
    names = _names({c["author"] for c in cards})
    for c in cards:
        c["author_name"] = names.get(c["author"])
    return {"columns": cols or DEFAULT_COLS, "cards": cards}


@router.put("/board/columns")
def set_columns(c: Columns, pid: int = Depends(current_player)):
    owner = owner_for(pid, c.scope)
    clean = []
    for col in c.columns:
        key = re.sub(r"[^a-z0-9_-]", "", str(col.get("key") or "").lower())[:40]
        title = str(col.get("title") or "").strip()[:40]
        if key and title and key not in {x["key"] for x in clean}:
            clean.append({"key": key, "title": title})
    if not clean:
        raise HTTPException(400, "Keep at least one column.")
    db.put(_cols_key(c.scope, owner), clean)
    return {"ok": True}


@router.post("/board/cards")
def add_card(c: Card, pid: int = Depends(current_player)):
    owner = owner_for(pid, c.scope)
    pos = c.position
    if pos is None:
        pos = ((db.one("SELECT MAX(position) AS p FROM board_cards WHERE scope=? AND owner=? AND col=?",
                       (c.scope, owner, c.col)) or {}).get("p") or 0) + 1
    now = _now()
    cur = db.execute("INSERT INTO board_cards (scope, owner, author, col, title, body, color, position, created, updated) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)", (c.scope, owner, pid, c.col, c.title.strip(), c.body, c.color, pos, now, now))
    return {"id": cur.lastrowid}


@router.put("/board/cards/{cid}")
def edit_card(cid: int, c: Card, pid: int = Depends(current_player)):
    row = _get_owned("board_cards", cid, pid)
    db.execute("UPDATE board_cards SET col=?, title=?, body=?, color=?, position=?, updated=? WHERE id=?",
               (c.col, c.title.strip(), c.body, c.color, c.position if c.position is not None else row["position"],
                _now(), cid))
    return {"ok": True}


@router.delete("/board/cards/{cid}")
def delete_card(cid: int, pid: int = Depends(current_player)):
    row = _get_owned("board_cards", cid, pid)
    if not _can_delete(pid, row):
        raise HTTPException(403, "Only the author or a faction leader can delete this.")
    db.execute("DELETE FROM board_cards WHERE id=?", (cid,))
    return {"ok": True}


# ---------------- board: canvas ----------------
class CanvasItem(BaseModel):
    scope: Scope = "me"
    kind: Literal["note", "arrow"] = "note"
    x: float = 0
    y: float = 0
    w: float = 200
    h: float = 140
    text: str = Field(default="", max_length=2000)
    color: str = Field(default="", max_length=20)
    from_id: int | None = None
    to_id: int | None = None


@router.get("/canvas")
def get_canvas(scope: Scope = "me", pid: int = Depends(current_player)):
    owner = owner_for(pid, scope)
    return {"items": db.query("SELECT * FROM canvas_items WHERE scope=? AND owner=? ORDER BY id", (scope, owner))}


@router.post("/canvas")
def add_canvas(c: CanvasItem, pid: int = Depends(current_player)):
    owner = owner_for(pid, c.scope)
    if c.kind == "arrow":
        ends = db.query("SELECT id FROM canvas_items WHERE id IN (?,?) AND scope=? AND owner=? AND kind='note'",
                        (c.from_id, c.to_id, c.scope, owner))
        if len(ends) != 2 or c.from_id == c.to_id:
            raise HTTPException(400, "Connect two different notes.")
        if db.one("SELECT 1 FROM canvas_items WHERE kind='arrow' AND scope=? AND owner=? AND from_id=? AND to_id=?",
                  (c.scope, owner, c.from_id, c.to_id)):
            raise HTTPException(400, "Those notes are already connected.")
    cur = db.execute("INSERT INTO canvas_items (scope, owner, author, kind, x, y, w, h, text, color, from_id, to_id, updated) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (c.scope, owner, pid, c.kind, c.x, c.y, max(80, min(c.w, 800)), max(60, min(c.h, 800)),
                      c.text, c.color, c.from_id, c.to_id, _now()))
    return {"id": cur.lastrowid}


@router.put("/canvas/{iid}")
def edit_canvas(iid: int, c: CanvasItem, pid: int = Depends(current_player)):
    _get_owned("canvas_items", iid, pid)
    db.execute("UPDATE canvas_items SET x=?, y=?, w=?, h=?, text=?, color=?, updated=? WHERE id=?",
               (c.x, c.y, max(80, min(c.w, 800)), max(60, min(c.h, 800)), c.text, c.color, _now(), iid))
    return {"ok": True}


@router.delete("/canvas/{iid}")
def delete_canvas(iid: int, pid: int = Depends(current_player)):
    row = _get_owned("canvas_items", iid, pid)
    if not _can_delete(pid, row):
        raise HTTPException(403, "Only the author or a faction leader can delete this.")
    db.execute("DELETE FROM canvas_items WHERE id=? OR from_id=? OR to_id=?", (iid, iid, iid))
    return {"ok": True}


def _names(ids):
    ids = [i for i in ids if i]
    if not ids:
        return {}
    rows = db.query(f"SELECT player_id, name FROM users WHERE player_id IN ({','.join('?' * len(ids))})", tuple(ids))
    return {r["player_id"]: r["name"] for r in rows}
