#!/usr/bin/env python3
"""
progr3ssboard.py — lokales Kanban-Board mit SQLite-Backend + CRUD.

Features Phase 1:
- 4-Spalten-Kanban (Backlog / In Arbeit / Testing / Merge-bereit)
- Click → Modal mit komplettem Ticket (boxed §-Layout)
- Edit-Form im Modal: Titel/Type/Tag/Status/Prio editierbar
- Drag-Drop zwischen Spalten → ändert Status
- "+ Neues Ticket"-Button
- Delete-Button im Modal

Usage:
  python3 progr3ssboard.py --serve            → localhost:8766
  python3 progr3ssboard.py --serve --port N   → custom

Datenquelle: board.db (siehe board-db.py migrate)
"""
import sys, re, json, html, argparse, sqlite3, subprocess, mimetypes, shutil
from pathlib import Path
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs, unquote

# DB-Layer aus board-db.py importieren
sys.path.insert(0, str(Path(__file__).resolve().parent))
import importlib.util
_spec = importlib.util.spec_from_file_location("board_db", Path(__file__).resolve().parent / "board-db.py")
_bdb  = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_bdb)
connect = _bdb.connect
PROJECTS = _bdb.PROJECTS
DEFAULT_PROJECT = _bdb.DEFAULT_PROJECT
project_cfg = _bdb.project_cfg

ALLOWED_STATUS = ["open", "progress", "deployed", "reopened", "parked", "closed"]
ALLOWED_TYPE   = ["BUG", "FEATURE", "HOTFIX", "REBUILD", "SPEC", "FUTURE", "INFRA"]
LINK_TYPES     = ["blocks", "blocked-by", "relates-to", "duplicates", "iteration-of", "parent-of", "child-of"]
# Types die als "Hauptticket" / Anforderung gelten (visuell prominent)
PARENT_TYPES   = {"FEATURE", "SPEC", "REBUILD", "FUTURE"}

REPO_ROOT      = Path(__file__).resolve().parents[2]
def _attach_dir(project):    return project_cfg(project)["attachments"]
def _worktree_base(project): return project_cfg(project)["worktree_base"]
def _id_pattern(project):    return project_cfg(project)["id_pattern"]
add_custom_project = _bdb.add_custom_project
TYPE_COLORS = {
    "BUG":      ("#e74c3c", "🐛"), "HOTFIX":  ("#c0392b", "🚨"),
    "FEATURE":  ("#3498db", "✨"), "REBUILD": ("#8e44ad", "🔨"),
    "SPEC":     ("#9b59b6", "📋"), "FUTURE":  ("#7f8c8d", "🔮"),
    "INFRA":    ("#16a085", "⚙"),
}
TAG_COLORS = {
    # Generische Palette — Tags die nicht hier gelistet sind, bekommen automatisch
    # eine deterministische Farbe per Hash (siehe tag_color() unten).
    "backend":     "#2980b9", "frontend":  "#9b59b6", "infra":     "#16a085",
    "ui":          "#e67e22", "api":       "#27ae60", "database":  "#8e44ad",
    "bug":         "#c0392b", "feature":   "#3498db", "refactor":  "#1abc9c",
    "docs":        "#7f8c8d", "test":      "#f1c40f", "release":   "#d35400",
    "security":    "#a93226", "perf":      "#117a65", "ux":        "#bf6516",
    "research":    "#566573",
}
def tag_color(t):
    if t in TAG_COLORS: return TAG_COLORS[t]
    h = sum(ord(c) for c in (t or "")) % 360
    return f"hsl({h},45%,35%)"

def ampel_from_days(days):
    try: d = int(days)
    except: return "grey"
    if d >= 7: return "green"
    if d >= 3: return "orange"
    return "red"

def iter_sandbox_days(d_str):
    if not d_str: return None
    try:
        return (date.today() - datetime.strptime(d_str, "%Y-%m-%d").date()).days
    except: return None

def _inline(s):
    s = html.escape(s)
    s = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', s)
    s = re.sub(r'`([^`]+)`', r'<code>\1</code>', s)
    s = re.sub(r'\[\[([^\]]+)\]\]', r'<span class="wikilink">\1</span>', s)
    return s

def md_to_html(md):
    out = ['<div class="ticket-render">']
    section_open = sub_open = in_list = False
    def close_lists():
        nonlocal in_list
        if in_list: out.append('</ul>'); in_list = False
    def close_sub():
        nonlocal sub_open
        if sub_open: out.append('</div>'); sub_open = False
    def close_sec():
        nonlocal section_open
        close_sub()
        if section_open: out.append('</div>'); section_open = False
    for line in (md or "").split('\n'):
        if line.startswith('## B-'):
            close_lists(); close_sec()
            out.append(f'<div class="t-main-header"><h2>{html.escape(line[3:].strip())}</h2></div>'); continue
        if line.startswith('### §'):
            close_lists(); close_sec()
            out.append(f'<div class="t-section"><h3>§{html.escape(line[5:].strip())}</h3>')
            section_open = True; continue
        if line.startswith('### Iter-') or line.startswith('### Aktiver Fix') or line.startswith('### Versuchs-Historie'):
            close_lists(); close_sub()
            out.append(f'<h4 class="t-iter">{html.escape(line[4:].strip())}</h4>'); continue
        if line.startswith('#### '):
            close_lists(); close_sub()
            out.append(f'<div class="t-sub"><h4>{html.escape(line[5:].strip())}</h4>')
            sub_open = True; continue
        st = line.lstrip()
        if st.startswith(('- ', '* ')):
            if not in_list: out.append('<ul>'); in_list = True
            out.append(f'<li>{_inline(st[2:])}</li>'); continue
        close_lists()
        if not st: continue
        if re.match(r'^\d+\.\s', st):
            out.append(f'<div class="checklist-item">{_inline(st)}</div>'); continue
        if st.startswith('> '):
            out.append(f'<blockquote>{_inline(st[2:])}</blockquote>'); continue
        if st.startswith('---'):
            out.append('<hr>'); continue
        out.append(f'<p>{_inline(line)}</p>')
    close_lists(); close_sec()
    out.append('</div>')
    return '\n'.join(out)

# ─── Data loading ──────────────────────────────────────────────────────────────

def load_tickets(project=None):
    conn = connect(project)
    rows = [dict(r) for r in conn.execute("SELECT * FROM tickets ORDER BY status, COALESCE(sort_order,99999), id")]
    iters_raw = conn.execute("SELECT * FROM iterations ORDER BY ticket_id, iter_num").fetchall()
    iters = {}
    for r in iters_raw:
        iters.setdefault(r['ticket_id'], []).append(dict(r))
    # Child-Counts per parent_id für Hierarchie-Pill
    child_counts = {}
    for r in conn.execute("SELECT parent_id, COUNT(*) AS n FROM tickets WHERE parent_id IS NOT NULL GROUP BY parent_id"):
        child_counts[r['parent_id']] = r['n']
    conn.close()
    today = date.today()
    for r in rows:
        r['ampel'] = ampel_from_days(_days_since(r.get('last_reset'), today))
        r['days_since_reset'] = _days_since(r.get('last_reset'), today)
        r['iters'] = iters.get(r['id'], [])
        r['child_count'] = child_counts.get(r['id'], 0)
        r['content_html'] = md_to_html(r.get('content_md') or "")
    return rows

def get_links(tid, project=None):
    """{outgoing: [(id, dst, type, dst_title)], incoming: [...]}"""
    conn = connect(project)
    out = []
    for r in conn.execute("""SELECT l.id, l.src, l.dst, l.type, t.title
                              FROM links l LEFT JOIN tickets t ON l.dst=t.id
                              WHERE l.src=? ORDER BY l.type""", (tid,)):
        out.append({"id": r['id'], "other": r['dst'], "type": r['type'], "other_title": r['title'] or ''})
    inc = []
    for r in conn.execute("""SELECT l.id, l.src, l.dst, l.type, t.title
                              FROM links l LEFT JOIN tickets t ON l.src=t.id
                              WHERE l.dst=? ORDER BY l.type""", (tid,)):
        inc.append({"id": r['id'], "other": r['src'], "type": r['type'], "other_title": r['title'] or ''})
    conn.close()
    return {"outgoing": out, "incoming": inc}

def create_link(src, dst, ltype, project=None):
    if ltype not in LINK_TYPES:
        raise ValueError(f"invalid link type: {ltype}")
    if src == dst:
        raise ValueError("self-link not allowed")
    now = datetime.now().isoformat(timespec='seconds')
    conn = connect(project)
    cur = conn.cursor()
    # Beide Tickets müssen existieren
    if not cur.execute("SELECT 1 FROM tickets WHERE id IN (?,?)", (src, dst)).fetchall():
        conn.close(); raise ValueError("ticket not found")
    cur.execute("INSERT OR IGNORE INTO links (src,dst,type) VALUES (?,?,?)", (src, dst, ltype))
    link_id = cur.lastrowid
    cur.execute("INSERT INTO activity (ticket_id,action,detail,created_at) VALUES (?,?,?,?)",
                (src, 'link', json.dumps({"to":dst,"type":ltype}), now))
    conn.commit(); conn.close()
    return link_id

def get_children(parent_id, project=None):
    """[{id, type, status, title, tag, ampel}] — direkte Children eines Tickets."""
    conn = connect(project)
    today = date.today()
    out = []
    for r in conn.execute("SELECT id,type,status,title,tag,last_reset,iter_count,tests_passed,tests_total FROM tickets WHERE parent_id=? ORDER BY id", (parent_id,)):
        d = dict(r)
        d['ampel'] = ampel_from_days(_days_since(d.get('last_reset'), today))
        out.append(d)
    conn.close()
    return out

def get_parent(child_id, project=None):
    conn = connect(project)
    row = conn.execute("""SELECT p.id, p.type, p.title, p.status, p.tag
                          FROM tickets c LEFT JOIN tickets p ON c.parent_id=p.id
                          WHERE c.id=? AND p.id IS NOT NULL""", (child_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

# ─── Attachments ───────────────────────────────────────────────────────────────

def list_attachments(tid, project=None):
    conn = connect(project)
    rows = [dict(r) for r in conn.execute("SELECT * FROM attachments WHERE ticket_id=? ORDER BY uploaded_at DESC", (tid,))]
    conn.close()
    return rows

def add_attachment(tid, filename, body_bytes, mime, project=None):
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', filename)[:120] or 'file'
    target_dir = _attach_dir(project) / tid
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe
    # Falls Filename schon existiert: Counter anhängen
    if target.exists():
        stem, ext = target.stem, target.suffix
        i = 1
        while (target_dir / f"{stem}_{i}{ext}").exists(): i += 1
        target = target_dir / f"{stem}_{i}{ext}"
        safe = target.name
    target.write_bytes(body_bytes)
    now = datetime.now().isoformat(timespec='seconds')
    conn = connect(project)
    cur = conn.cursor()
    cur.execute("INSERT INTO attachments (ticket_id,filename,mime,size_bytes,uploaded_at) VALUES (?,?,?,?,?)",
                (tid, safe, mime, len(body_bytes), now))
    aid = cur.lastrowid
    cur.execute("INSERT INTO activity (ticket_id,action,detail,created_at) VALUES (?,?,?,?)",
                (tid, 'attach', json.dumps({"file":safe,"size":len(body_bytes)}), now))
    conn.commit(); conn.close()
    return {"id": aid, "filename": safe, "size_bytes": len(body_bytes), "mime": mime}

def delete_attachment(aid, project=None):
    conn = connect(project)
    row = conn.execute("SELECT ticket_id,filename FROM attachments WHERE id=?", (aid,)).fetchone()
    if not row: conn.close(); return False
    fp = _attach_dir(project) / row['ticket_id'] / row['filename']
    if fp.exists(): fp.unlink()
    conn.execute("DELETE FROM attachments WHERE id=?", (aid,))
    conn.commit(); conn.close()
    return True

# ─── Worktree ──────────────────────────────────────────────────────────────────

def worktree_status(tid, project=None):
    """Status + branch + last-commit. None falls noch nicht angelegt."""
    conn = connect(project)
    row = conn.execute("SELECT worktree_path,worktree_branch FROM tickets WHERE id=?", (tid,)).fetchone()
    conn.close()
    if not row or not row['worktree_path']: return None
    wp = Path(row['worktree_path'])
    if not wp.exists():
        return {"path": str(wp), "branch": row['worktree_branch'], "exists": False, "error": "Pfad fehlt"}
    try:
        head = subprocess.check_output(["git","-C",str(wp),"log","-1","--format=%h %s"], stderr=subprocess.DEVNULL).decode().strip()
        dirty = subprocess.check_output(["git","-C",str(wp),"status","--porcelain"], stderr=subprocess.DEVNULL).decode().strip()
        return {"path": str(wp), "branch": row['worktree_branch'], "exists": True,
                "head": head, "dirty": bool(dirty), "dirty_lines": dirty.count('\n')+1 if dirty else 0}
    except Exception as e:
        return {"path": str(wp), "branch": row['worktree_branch'], "exists": True, "error": str(e)}

def create_worktree(tid, project=None):
    """git worktree add <WORKTREE_BASE>/<tid> -b ticket/<tid> (basierend auf main)."""
    wp = _worktree_base(project) / tid
    branch = f"ticket/{tid}"
    if wp.exists():
        raise ValueError(f"Worktree existiert schon: {wp}")
    _worktree_base(project).mkdir(parents=True, exist_ok=True)
    try:
        subprocess.check_output(
            ["git","-C",str(REPO_ROOT),"worktree","add",str(wp),"-b",branch,"main"],
            stderr=subprocess.STDOUT
        )
    except subprocess.CalledProcessError as e:
        raise ValueError(f"git worktree add failed: {e.output.decode()[:500]}")
    now = datetime.now().isoformat(timespec='seconds')
    conn = connect(project)
    conn.execute("UPDATE tickets SET worktree_path=?, worktree_branch=?, updated_at=? WHERE id=?",
                 (str(wp), branch, now, tid))
    conn.execute("INSERT INTO activity (ticket_id,action,detail,created_at) VALUES (?,?,?,?)",
                 (tid, 'worktree_create', json.dumps({"path":str(wp),"branch":branch}), now))
    conn.commit(); conn.close()
    return {"path": str(wp), "branch": branch}

def remove_worktree(tid, project=None, force=False):
    conn = connect(project)
    row = conn.execute("SELECT worktree_path,worktree_branch FROM tickets WHERE id=?", (tid,)).fetchone()
    if not row or not row['worktree_path']: conn.close(); return False
    wp = row['worktree_path']
    cmd = ["git","-C",str(REPO_ROOT),"worktree","remove",wp]
    if force: cmd.append("--force")
    try:
        subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        conn.close()
        raise ValueError(f"git worktree remove failed: {e.output.decode()[:500]}")
    now = datetime.now().isoformat(timespec='seconds')
    conn.execute("UPDATE tickets SET worktree_path=NULL, worktree_branch=NULL, updated_at=? WHERE id=?", (now, tid))
    conn.execute("INSERT INTO activity (ticket_id,action,detail,created_at) VALUES (?,?,?,?)",
                 (tid, 'worktree_remove', None, now))
    conn.commit(); conn.close()
    return True

def delete_link(link_id, project=None):
    now = datetime.now().isoformat(timespec='seconds')
    conn = connect(project)
    cur = conn.cursor()
    cur.execute("DELETE FROM links WHERE id=?", (link_id,))
    ok = cur.rowcount > 0
    cur.execute("INSERT INTO activity (ticket_id,action,detail,created_at) VALUES (?,?,?,?)",
                (None, 'unlink', json.dumps({"link_id":link_id}), now))
    conn.commit(); conn.close()
    return ok

def _days_since(d_str, today):
    if not d_str: return None
    try:
        return (today - datetime.strptime(d_str, "%Y-%m-%d").date()).days
    except: return None

def next_id(project=None):
    """Project-aware ID-Generator. Nimmt den Prefix aus pcfg['id_prefix'] (z.B. 'B',
    'TASK', 'FOO') und sucht die höchste existierende Nummer dafür + 1. Bei Boards
    mit mehreren Prefixen (z.B. 'B-' für Bugs und 'F-' für Features) wird der
    Prefix mit dem höchsten existierenden Counter genommen — neue Prefix-Typen
    müssen einmal manuell vergeben werden, danach läuft der Counter."""
    pcfg = project_cfg(project)
    default_first = f"{pcfg.get('id_prefix') or 'B'}-1"
    conn = connect(project)
    rows = [r['id'] for r in conn.execute("SELECT id FROM tickets")]
    conn.close()
    if not rows: return default_first
    # Pro Prefix (alles bis vor der letzten Zahl) max numerisches Suffix
    best = {}
    for tid in rows:
        m = re.match(r"^(.*?)(\d+)$", tid)
        if not m: continue
        prefix, num = m.group(1), int(m.group(2))
        if num > best.get(prefix, -1): best[prefix] = num
    if not best: return default_first
    # Heuristik: der Prefix mit höchstem Counter gewinnt (= aktivster Track).
    prefix = max(best, key=lambda p: best[p])
    return f"{prefix}{best[prefix]+1}"

# ─── CRUD ──────────────────────────────────────────────────────────────────────

def create_ticket(data, project=None):
    tid = data.get('id') or next_id(project=project)
    now = datetime.now().isoformat(timespec='seconds')
    today = date.today().isoformat()
    conn = connect(project)
    conn.execute("""INSERT INTO tickets
        (id,type,status,tag,prio,title,content_md,created_date,last_reset,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (tid,
         data.get('type','BUG') if data.get('type') in ALLOWED_TYPE else 'BUG',
         data.get('status','open') if data.get('status') in ALLOWED_STATUS else 'open',
         data.get('tag') or None,
         data.get('prio') or 'P2',
         data.get('title') or '(ohne Titel)',
         data.get('content_md') or f"## {tid} — {data.get('title','(ohne Titel)')} ({today}) — {data.get('prio','P2')}\n\n### §1 Status\n**open** — neu angelegt via Board.\n",
         today, today, now))
    conn.execute("INSERT INTO activity (ticket_id,action,detail,created_at) VALUES (?,?,?,?)",
                 (tid, 'create', json.dumps(data, ensure_ascii=False), now))
    conn.commit(); conn.close()
    return tid

def update_ticket(tid, data, project=None):
    now = datetime.now().isoformat(timespec='seconds')
    conn = connect(project)
    cur = conn.cursor()
    # Whitelisted fields
    fields = []
    vals = []
    for k in ('type','status','tag','prio','title','content_md','parent_id'):
        if k in data:
            v = data[k]
            if k == 'type' and v not in ALLOWED_TYPE: continue
            if k == 'status' and v not in ALLOWED_STATUS: continue
            fields.append(f"{k}=?"); vals.append(v)
    if not fields:
        conn.close(); return False
    fields.append("updated_at=?"); vals.append(now)
    vals.append(tid)
    cur.execute(f"UPDATE tickets SET {', '.join(fields)} WHERE id=?", vals)
    ok = cur.rowcount > 0
    cur.execute("INSERT INTO activity (ticket_id,action,detail,created_at) VALUES (?,?,?,?)",
                (tid, 'update', json.dumps(data, ensure_ascii=False), now))
    conn.commit(); conn.close()
    return ok

def reorder_ticket(tid, new_status, before_id=None, after_id=None, project=None):
    """Setzt Status + sort_order. before_id/after_id geben die Nachbar-Karte an
    (eine von beiden). sort_order wird re-numeriert (10er-Schritte) damit Insert
    immer Platz hat. Idempotent: leerer Spalten-Drop → ans Ende."""
    conn = connect(project); cur = conn.cursor()
    row = cur.execute("SELECT id, status FROM tickets WHERE id=?", (tid,)).fetchone()
    if not row: conn.close(); return False
    # Status setzen (auch reopened-Logik: closed → open beim Rausziehen)
    if row['status'] != new_status:
        # closed → was-anderes = reopened-Markierung im Activity-Log
        if row['status'] == 'closed' and new_status != 'closed':
            cur.execute("UPDATE tickets SET status='reopened', updated_at=? WHERE id=?",
                        (datetime.now().isoformat(timespec='seconds'), tid))
        else:
            cur.execute("UPDATE tickets SET status=?, updated_at=? WHERE id=?",
                        (new_status, datetime.now().isoformat(timespec='seconds'), tid))
    # sort_order re-compute innerhalb Ziel-Spalte
    effective_status = 'reopened' if (row['status']=='closed' and new_status!='closed') else new_status
    siblings = [r['id'] for r in cur.execute(
        "SELECT id FROM tickets WHERE status=? AND id<>? ORDER BY COALESCE(sort_order,99999), id",
        (effective_status, tid))]
    order = []
    inserted = False
    for sid in siblings:
        if sid == before_id and not inserted:
            order.append(tid); inserted = True
        order.append(sid)
        if sid == after_id and not inserted:
            order.append(tid); inserted = True
    if not inserted: order.append(tid)  # leere Spalte oder kein anchor → ans Ende
    for i, sid in enumerate(order):
        cur.execute("UPDATE tickets SET sort_order=? WHERE id=?", ((i+1)*10, sid))
    conn.commit(); conn.close()
    return True

def search_all_projects(q, limit=50):
    """Cross-Project-Suche über alle PROJECTS — id, title, content_md (LIKE).
    Returns [{project, id, title, status}]."""
    out = []
    if not q or not q.strip(): return out
    like = f"%{q.strip()}%"
    for pkey in PROJECTS:
        try:
            conn = connect(pkey)
            for r in conn.execute(
                "SELECT id, title, status FROM tickets "
                "WHERE id LIKE ? OR title LIKE ? OR content_md LIKE ? "
                "ORDER BY status, id LIMIT ?",
                (like, like, like, limit)
            ):
                out.append({"project": pkey, "id": r['id'], "title": r['title'] or '', "status": r['status']})
            conn.close()
        except Exception:
            continue
    return out[:limit]

def delete_ticket(tid, project=None):
    now = datetime.now().isoformat(timespec='seconds')
    conn = connect(project)
    cur = conn.cursor()
    cur.execute("DELETE FROM tickets WHERE id=?", (tid,))
    ok = cur.rowcount > 0
    cur.execute("INSERT INTO activity (ticket_id,action,detail,created_at) VALUES (?,?,?,?)",
                (tid, 'delete', None, now))
    conn.commit(); conn.close()
    return ok

# ─── HTML render ───────────────────────────────────────────────────────────────

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
body { background: #1a1d23; color: #e4e6eb; padding: 16px; min-height: 100vh; }
h1 { font-size: 22px; color: #ff9933; font-weight: 700; }
.subtitle { font-size: 12px; color: #8b95a8; margin: 4px 0 14px; }
.topbar { display: flex; gap: 10px; align-items: center; margin-bottom: 14px; }
.topbar button { background: #ff9933; color: #1a1d23; border: none; border-radius: 5px; padding: 7px 14px; font-size: 12px; font-weight: 700; cursor: pointer; }
.topbar button:hover { background: #ffaa55; }
.topbar button.secondary { background: #3a3f4a; color: #ccc; }
.board { display: flex; gap: 14px; overflow-x: auto; padding-bottom: 16px; align-items: flex-start; }
.column { background: #22262e; border-radius: 8px; padding: 12px; min-width: 320px; max-width: 380px; flex: 1; min-height: 200px; }
.column.drag-over { background: #2d3540; outline: 2px dashed #ff9933; }
.column-header { display: flex; justify-content: space-between; align-items: center; padding: 4px 6px 10px; border-bottom: 1px solid #3a3f4a; margin-bottom: 12px; }
.column-title { font-weight: 700; font-size: 13px; color: #fff; text-transform: uppercase; letter-spacing: 0.5px; }
.column-count { background: #3a3f4a; color: #aaa; padding: 2px 9px; border-radius: 10px; font-size: 11px; font-weight: 600; }
.card { background: #2d3139; border-radius: 6px; padding: 10px 12px; margin-bottom: 8px; border-left: 4px solid #555; box-shadow: 0 1px 3px rgba(0,0,0,0.4); transition: transform 0.1s; cursor: grab; }
.card:hover { transform: translateY(-1px); box-shadow: 0 2px 6px rgba(0,0,0,0.5); }
.card:active { cursor: grabbing; }
.card.dragging { opacity: 0.4; }
.card.red { border-left-color: #e74c3c; }
.card.orange { border-left-color: #f39c12; }
.card.green { border-left-color: #2ecc71; }
.card.grey { border-left-color: #6b7280; }
.card.parent-feature { border: 2px solid #ffd700; border-left-width: 4px; background: linear-gradient(135deg,#2d3139 0%,#3a3527 100%); box-shadow: 0 2px 8px rgba(255,215,0,0.15); }
.card.parent-feature .card-id { color: #ffd700; }
.card-row1 { display: flex; align-items: center; gap: 5px; margin-bottom: 5px; flex-wrap: wrap; }
.card-id { font-size: 11px; color: #8b95a8; font-weight: 700; }
.type-badge { padding: 1px 7px; border-radius: 8px; font-size: 9px; font-weight: 700; color: #fff; letter-spacing: 0.4px; text-transform: uppercase; }
.card-tag { padding: 1px 8px; border-radius: 8px; font-size: 10px; font-weight: 600; color: #fff; }
.card-warn { background: #c0392b; color: #fff; padding: 1px 6px; border-radius: 8px; font-size: 10px; font-weight: 700; margin-left: auto; }
.hier-pill { padding: 1px 6px; border-radius: 8px; font-size: 10px; font-weight: 700; }
.hier-pill.parent { background: #4a5568; color: #fff; }
.hier-pill.child  { background: #2c5282; color: #fff; }
.links-section { background: #1f2228; border-radius: 6px; padding: 10px 14px; margin-top: 10px; border: 1px solid #2a2e36; }
.links-section h4 { color: #ff9933; font-size: 11px; text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 8px; font-weight: 700; }
.link-row { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 12px; color: #ccc; }
.link-row .type { background: #3a3f4a; color: #ff9933; padding: 1px 7px; border-radius: 8px; font-size: 10px; font-weight: 700; }
.link-row .target { color: #5dade2; font-weight: 600; }
.link-row .titletxt { color: #888; font-size: 11px; flex: 1; }
.link-row button { background: transparent; color: #c0392b; border: none; cursor: pointer; font-size: 14px; padding: 2px 6px; }
.link-row button:hover { color: #e74c3c; }
.add-link-row { display: flex; gap: 6px; margin-top: 10px; padding-top: 10px; border-top: 1px dashed #3a3f4a; }
.add-link-row select, .add-link-row input { flex: 1; background: #25292f; color: #fff; border: 1px solid #3a3f4a; border-radius: 4px; padding: 4px 8px; font-size: 12px; }
.add-link-row button { background: #ff9933; color: #1a1d23; border: none; border-radius: 4px; padding: 4px 12px; font-size: 12px; font-weight: 700; cursor: pointer; }
/* Parent-Box + Children-Liste (Bug↔Feature) */
.parent-box { background: #3a3527; border: 1px solid #ffd700; border-radius: 6px; padding: 10px 14px; margin-bottom: 12px; }
.parent-box .lbl { color: #ffd700; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.4px; }
.parent-box a { color: #ffd700; font-weight: 700; cursor: pointer; text-decoration: none; }
.parent-box a:hover { text-decoration: underline; }
.children-box { background: #1f2228; border-left: 4px solid #2ecc71; border-radius: 6px; padding: 12px 14px; margin-top: 12px; }
.children-box h4 { color: #2ecc71; font-size: 11px; text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 8px; font-weight: 700; }
.child-row { display: flex; gap: 8px; align-items: center; padding: 4px 0; font-size: 12px; cursor: pointer; }
.child-row:hover { background: #25292f; }
.child-row .cid { font-weight: 700; color: #5dade2; min-width: 50px; }
.child-row .ctype { padding: 1px 6px; border-radius: 8px; font-size: 9px; font-weight: 700; color: #fff; }
.child-row .cstatus { color: #888; font-size: 10px; min-width: 70px; }
.child-row .ctitle { color: #ccc; flex: 1; }
.child-row .camp { width: 8px; height: 8px; border-radius: 50%; }
/* Attachments */
.attach-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; margin-top: 12px; }
.attach-tile { background: #25292f; border: 1px solid #3a3f4a; border-radius: 6px; overflow: hidden; position: relative; }
.attach-tile img { width: 100%; height: 100px; object-fit: cover; display: block; cursor: pointer; }
.attach-tile .att-meta { padding: 5px 8px; font-size: 10px; color: #888; }
.attach-tile .att-name { color: #ccc; font-weight: 600; word-break: break-all; }
.attach-tile button { position: absolute; top: 4px; right: 4px; background: rgba(192,57,43,0.85); color: #fff; border: none; border-radius: 50%; width: 22px; height: 22px; cursor: pointer; font-size: 11px; }
.dropzone { border: 2px dashed #3a3f4a; border-radius: 6px; padding: 24px; text-align: center; color: #888; margin-top: 12px; transition: all 0.2s; }
.dropzone.over { border-color: #ff9933; color: #ff9933; background: rgba(255,153,51,0.05); }
.dropzone input { display: none; }
.dropzone label { cursor: pointer; color: #5dade2; }
/* Worktree */
.worktree-box { background: #1f2228; border: 1px solid #2a2e36; border-radius: 6px; padding: 14px; }
.worktree-box .row { display: flex; gap: 10px; margin: 6px 0; font-size: 12px; }
.worktree-box .row .k { color: #888; min-width: 90px; }
.worktree-box .row .v { color: #ccc; font-family: ui-monospace, monospace; word-break: break-all; }
.worktree-box .row .v.dirty { color: #f39c12; }
.worktree-box .actions { margin-top: 12px; display: flex; gap: 8px; }
.worktree-box button { background: #ff9933; color: #1a1d23; border: none; border-radius: 4px; padding: 6px 14px; font-size: 12px; font-weight: 700; cursor: pointer; }
.worktree-box button.danger { background: #c0392b; color: #fff; }
.card-title { font-weight: 700; color: #ff9933; font-size: 13px; margin: 4px 0 6px; line-height: 1.35; }
.card-meta { display: flex; flex-wrap: wrap; gap: 10px; font-size: 11px; color: #8b95a8; margin: 4px 0; }
.iter-row { background: #1f2228; padding: 7px 9px; border-radius: 4px; margin-top: 8px; font-size: 11px; border: 1px solid #2a2e36; }
.iter-row .num { font-weight: 700; color: #fff; }
.iter-row .sandbox { display: inline-block; padding: 1px 7px; border-radius: 3px; margin: 0 4px; font-size: 10px; font-weight: 700; }
.sandbox.red { background: #c0392b; color: #fff; }
.sandbox.green { background: #27ae60; color: #fff; }
.iter-row .label { display: block; color: #aaa; margin-top: 4px; line-height: 1.3; font-style: italic; }

/* Modal */
.modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.75); z-index: 100; justify-content: center; align-items: center; padding: 20px; }
.modal.open { display: flex; }
.modal-content { background: #22262e; border-radius: 8px; width: 100%; max-width: 1100px; max-height: 92vh; display: flex; flex-direction: column; overflow: hidden; }
.modal-header { padding: 12px 18px; border-bottom: 1px solid #3a3f4a; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.mh-id { font-weight: 700; color: #fff; font-size: 14px; }
.modal-header button { background: #ff9933; color: #1a1d23; border: none; border-radius: 4px; padding: 5px 12px; font-size: 12px; font-weight: 700; cursor: pointer; }
.modal-header button:hover { background: #ffaa55; }
.modal-header button.danger { background: #c0392b; color: #fff; }
.modal-header button.danger:hover { background: #e74c3c; }
.modal-header button.close { background: transparent; color: #888; font-size: 22px; padding: 0 8px; }
.modal-header button.close:hover { color: #fff; }
.modal-body { padding: 16px 20px; overflow: auto; flex: 1; }
.toast { padding: 5px 10px; background: #27ae60; color: #fff; font-size: 11px; border-radius: 3px; display: none; }
.toast.show { display: inline-block; }
.toast.err { background: #c0392b; }

/* Edit form */
.tabs { display: flex; border-bottom: 1px solid #3a3f4a; margin-bottom: 12px; }
.tab { padding: 8px 14px; cursor: pointer; color: #888; font-size: 12px; font-weight: 600; border-bottom: 2px solid transparent; }
.tab.active { color: #ff9933; border-bottom-color: #ff9933; }
.tab-content { display: none; }
.tab-content.active { display: block; }
.form-row { margin-bottom: 12px; }
.form-row label { display: block; font-size: 11px; color: #8b95a8; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.4px; font-weight: 600; }
.form-row input, .form-row select, .form-row textarea {
  width: 100%; background: #1f2228; color: #fff; border: 1px solid #3a3f4a;
  border-radius: 4px; padding: 7px 10px; font-size: 13px; font-family: inherit;
}
.form-row textarea { min-height: 200px; font-family: ui-monospace, monospace; font-size: 12px; }

/* Render-Style */
.ticket-render { font-family: -apple-system, sans-serif; }
.ticket-render .t-main-header { padding-bottom: 10px; margin-bottom: 14px; border-bottom: 2px solid #ff9933; }
.ticket-render .t-main-header h2 { color: #ff9933; font-size: 15px; font-weight: 700; }
.ticket-render .t-section { background: #1f2228; border: 1px solid #3a3f4a; border-left: 4px solid #ff9933; border-radius: 6px; padding: 12px 16px; margin: 12px 0; }
.ticket-render .t-section h3 { color: #ff9933; font-size: 12px; font-weight: 700; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.5px; padding-bottom: 6px; border-bottom: 1px solid #2a2e36; }
.ticket-render .t-sub { background: #25292f; border-left: 3px solid #8b95a8; border-radius: 4px; padding: 8px 12px; margin: 8px 0; }
.ticket-render .t-sub h4 { color: #b8bfca; font-size: 11px; font-weight: 700; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.3px; }
.ticket-render h4.t-iter { color: #f39c12; font-size: 12px; font-weight: 700; margin: 10px 0 4px; padding: 4px 0; border-bottom: 1px dashed #f39c12; }
.ticket-render p { color: #ccc; font-size: 12px; line-height: 1.55; margin: 5px 0; }
.ticket-render ul { color: #ccc; font-size: 12px; line-height: 1.55; margin: 4px 0 8px 22px; list-style: disc; }
.ticket-render li { margin: 3px 0; }
.ticket-render code { background: #1a1d23; color: #ff9933; padding: 1px 5px; border-radius: 3px; font-family: ui-monospace, monospace; font-size: 11px; }
.ticket-render b { color: #fff; font-weight: 600; }
.ticket-render .checklist-item { color: #ccc; font-size: 12px; line-height: 1.55; margin: 4px 0; padding-left: 16px; }
.ticket-render blockquote { color: #aaa; font-style: italic; border-left: 3px solid #3a3f4a; padding: 4px 12px; margin: 6px 0; }
.ticket-render .wikilink { color: #5dade2; background: #1a3a52; padding: 1px 5px; border-radius: 3px; font-size: 11px; }
.ticket-render hr { border: none; border-top: 1px dashed #3a3f4a; margin: 12px 0; }
.footer { margin-top: 20px; padding-top: 10px; border-top: 1px solid #2a2e36; color: #6b7280; font-size: 11px; text-align: center; }
/* — Add-a-Card-Button — */
.add-card { color: #6b7280; background: transparent; border: none; padding: 8px 10px; font-size: 12px; cursor: pointer; text-align: left; border-radius: 4px; width: 100%; margin-top: 4px; transition: background 0.15s, color 0.15s; }
.add-card:hover { background: #2d3139; color: #ff9933; }
.add-card[disabled] { display: none; }
/* — Drop-Insertion-Indicator (vertikales DnD) — */
.card.drop-before { box-shadow: 0 -3px 0 0 #ff9933 inset, 0 1px 3px rgba(0,0,0,0.4); }
.card.drop-after  { box-shadow: 0  3px 0 0 #ff9933 inset, 0 1px 3px rgba(0,0,0,0.4); }
/* — Top-Bar: Switcher + Suche + Create-Board — */
.topbar select, .topbar input[type=text], .topbar input[type=search] {
  background: #2d3139; color: #e4e6eb; border: 1px solid #3a3f4a; border-radius: 5px;
  padding: 6px 10px; font-size: 12px; font-family: inherit;
}
.topbar select:focus, .topbar input:focus { outline: none; border-color: #ff9933; }
.topbar .search-wrap { position: relative; flex: 0 0 320px; }
.topbar .search-wrap input { width: 100%; }
.search-results { position: absolute; top: 100%; left: 0; right: 0; background: #22262e; border: 1px solid #3a3f4a; border-radius: 5px; margin-top: 4px; max-height: 320px; overflow-y: auto; z-index: 100; display: none; }
.search-results.show { display: block; }
.search-results a { display: block; padding: 7px 10px; color: #e4e6eb; text-decoration: none; font-size: 12px; border-bottom: 1px solid #2a2e36; }
.search-results a:hover { background: #2d3139; }
.search-results .sr-proj { color: #ff9933; font-size: 10px; text-transform: uppercase; margin-right: 6px; }
.search-results .sr-empty { padding: 10px; color: #6b7280; font-size: 11px; text-align: center; }
.column.closed { background: #1e2128; opacity: 0.85; }
.column.closed .column-title { color: #6b7280; }
/* — Create-Board-Modal — */
.cb-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.7); display: none; align-items: center; justify-content: center; z-index: 1500; }
.cb-overlay.show { display: flex; }
.cb-box { background: #22262e; border: 1px solid #3a3f4a; border-radius: 8px; padding: 18px; width: 420px; }
.cb-box h2 { color: #ff9933; font-size: 14px; margin-bottom: 12px; }
.cb-box label { display: block; color: #b8bfca; font-size: 11px; margin: 8px 0 3px; text-transform: uppercase; letter-spacing: 0.3px; }
.cb-box input { width: 100%; background: #1a1d23; color: #e4e6eb; border: 1px solid #3a3f4a; border-radius: 4px; padding: 7px 9px; font-size: 12px; }
.cb-box .cb-hint { color: #6b7280; font-size: 10px; margin-top: 3px; }
.cb-box .cb-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 14px; }
"""

def render_card(t):
    amp = t['ampel']
    tid = t['id']
    tag = html.escape(t.get('tag') or '?')
    tag_bg = tag_color(t.get('tag') or '?')
    ttype = t.get('type') or 'BUG'
    type_bg, type_icon = TYPE_COLORS.get(ttype, TYPE_COLORS['BUG'])
    title = html.escape(t.get('title') or '(ohne Titel)')
    days = t.get('days_since_reset')
    days_disp = f"{days}d" if days is not None else "—"
    it_n = t.get('iter_count', 0)
    warn = (it_n >= 5)
    warn_html = f'<span class="card-warn">⚠{it_n} Iter</span>' if warn else ''
    iter_count_str = f"{it_n}It" if it_n else "—"
    tests = f"{t.get('tests_passed',0)}/{t.get('tests_total',0)}"
    # Hierarchie-Pills
    hierarchy_pills = []
    if t.get('parent_id'):
        hierarchy_pills.append(f'<span class="hier-pill parent">↑ {t["parent_id"]}</span>')
    if t.get('child_count', 0):
        hierarchy_pills.append(f'<span class="hier-pill child">⤓ {t["child_count"]}</span>')
    hierarchy_html = "".join(hierarchy_pills)

    iter_html = ""
    if t['iters']:
        last = t['iters'][-1]
        sb = iter_sandbox_days(last.get('deploy_date'))
        if sb is None:
            sb_cls, sb_txt = "red", "—d / 7d"
        elif sb >= 7:
            sb_cls, sb_txt = "green", f"{sb}d / 7d ✓"
        else:
            sb_cls, sb_txt = "red", f"{sb}d / 7d"
        kind = last.get('kind') or 'fix'
        lbl = html.escape((last.get('label') or '')[:90])
        iter_html = f'''<div class="iter-row">
          <span class="num">Iter-{last["iter_num"]}</span> ·
          <span class="sandbox {sb_cls}">{sb_txt}</span> ·
          <span>{kind}</span>
          <span class="label">{lbl}</span>
        </div>'''
    parent_klass = " parent-feature" if ttype in PARENT_TYPES else ""
    crown = "👑 " if ttype in PARENT_TYPES else ""
    return f'''
    <div class="card {amp}{parent_klass}" draggable="true" data-id="{tid}" onclick="openTicket('{tid}')">
      <div class="card-row1">
        <span class="card-id">{crown}{tid}</span>
        <span class="type-badge" style="background:{type_bg}">{type_icon} {ttype}</span>
        <span class="card-tag" style="background:{tag_bg}">{tag}</span>
        {warn_html}
      </div>
      <div class="card-title">{title}</div>
      <div class="card-meta">
        <span>📅 {days_disp}</span>
        <span>🔄 {iter_count_str}</span>
        <span>🧪 {tests}</span>
        {hierarchy_html}
      </div>
      {iter_html}
    </div>'''

def render_page(project=None):
    project = project or DEFAULT_PROJECT
    if project not in PROJECTS: project = DEFAULT_PROJECT
    pname = PROJECTS[project]["name"]
    tickets = load_tickets(project=project)
    # 6-Spalten-Klassifikation
    # NEU (open, kein Iter), IN_ARBEIT (progress/reopened), TESTING (Iter <7d),
    # READY (Iter ≥7d), PARKED (parked), CLOSED (closed)
    cols = {"neu": [], "in_arbeit": [], "testing": [], "ready": [], "parked": [], "closed": []}
    for t in tickets:
        st = t.get('status', 'open')
        if st == "closed":
            cols["closed"].append(t)
        elif st == "parked":
            cols["parked"].append(t)
        elif t['iters']:
            sb = iter_sandbox_days(t['iters'][-1].get('deploy_date'))
            if sb is not None and sb >= 7:
                cols["ready"].append(t)
            else:
                cols["testing"].append(t)
        elif st in ("progress", "reopened", "deployed"):
            # deployed ohne Iter ist Edge — landet bei In-Arbeit (Tester muss Iter anlegen)
            cols["in_arbeit"].append(t)
        else:  # 'open' und alles Unbekannte → NEU
            cols["neu"].append(t)
    # Sortier-Reihenfolge: User-Sort (sort_order) → Ampel → Alter
    order = {"red":0,"orange":1,"green":2,"grey":3}
    for k in cols:
        cols[k].sort(key=lambda x: (x.get('sort_order') or 99999, order[x['ampel']], -(x.get('days_since_reset') or 0)))

    def col_html(key, title, items, status_when_dropped, can_add=True):
        cards = "\n".join(render_card(t) for t in items)
        empty = '' if cards else '<div style="color:#555;font-size:11px;padding:4px 6px">— leer —</div>'
        add_btn = (f'<button class="add-card" onclick="newTicket(\'{status_when_dropped}\')">+ Karte hinzufügen</button>'
                   if can_add else '')
        klass = "column closed" if key == "closed" else "column"
        return f'''<div class="{klass}" data-col="{key}" data-status="{status_when_dropped}" ondragover="onColDragOver(event)" ondragleave="onColDragLeave(event)" ondrop="onColDrop(event)">
          <div class="column-header">
            <span class="column-title">{title}</span>
            <span class="column-count">{len(items)}</span>
          </div>
          {cards}{empty}
          {add_btn}
        </div>'''

    n = len(tickets)
    n_red = sum(1 for t in tickets if t['ampel']=='red')
    n_yel = sum(1 for t in tickets if t['ampel']=='orange')
    n_grn = sum(1 for t in tickets if t['ampel']=='green')

    # Project-Switcher Options
    proj_opts = "\n".join(
        f'<option value="{k}"{ " selected" if k==project else "" }>{html.escape(v["name"])}</option>'
        for k, v in PROJECTS.items()
    )

    return f'''<!DOCTYPE html>
<html lang="de"><head>
<meta charset="utf-8">
<title>📋 {html.escape(pname)} · Board</title>
<style>{CSS}</style>
</head><body>
<h1>📋 {html.escape(pname)} — {date.today().isoformat()}</h1>
<div class="subtitle">{n} Tickets · 🔴{n_red} 🟠{n_yel} 🟢{n_grn} · SQLite · Multi-Project</div>
<div class="topbar">
  <select id="proj-switch" onchange="switchProject(this.value)" title="Projekt-Board wechseln">
    {proj_opts}
  </select>
  <button class="secondary" onclick="openCreateBoard()" title="Neues Projekt-Board anlegen">+ Board</button>
  <div class="search-wrap">
    <input id="global-search" type="search" placeholder="🔍 Suche über alle Boards (ID, Titel, Inhalt)…" oninput="onGlobalSearch(this.value)" onblur="setTimeout(()=>hideSearchResults(),200)" onfocus="if(this.value)onGlobalSearch(this.value)">
    <div id="search-results" class="search-results"></div>
  </div>
  <button class="secondary" onclick="location.reload()">🔄 Reload</button>
  <span style="color:#666;font-size:11px;margin-left:auto">Drag horizontal = Status · Drag vertikal = Sortieren · „+ Karte" pro Spalte · Closed → drag raus = wieder eröffnen</span>
</div>
<div class="board">
  {col_html("neu",       "📥 NEU",                 cols["neu"],       "open",     True)}
  {col_html("in_arbeit", "🛠 IN ARBEIT",            cols["in_arbeit"], "progress", True)}
  {col_html("testing",   "🧪 TESTING (Sandbox <7d)", cols["testing"],   "deployed", True)}
  {col_html("ready",     "✅ READY (≥7d ✓)",        cols["ready"],     "deployed", True)}
  {col_html("parked",    "⏸ PARKED",                cols["parked"],    "parked",   True)}
  {col_html("closed",    "🗄 CLOSED",               cols["closed"],    "closed",   False)}
</div>

<!-- Create-Board Modal -->
<div id="cb-overlay" class="cb-overlay" onclick="if(event.target===this)closeCreateBoard()">
  <div class="cb-box">
    <h2>+ Neues Projekt-Board</h2>
    <label>Slug (URL-Key)</label>
    <input id="cb-key" type="text" placeholder="z.B. mein-projekt" autocomplete="off">
    <div class="cb-hint">lowercase a-z 0-9 '-', 2-41 Zeichen — bestimmt ?project=&lt;slug&gt;</div>
    <label>Anzeigename</label>
    <input id="cb-name" type="text" placeholder="z.B. Mein Projekt-Board">
    <label>Ticket-ID-Prefix</label>
    <input id="cb-prefix" type="text" value="B" placeholder="B, TASK, FOO …">
    <div class="cb-hint">GROSSBUCHSTABEN/Ziffern, 1-8 Zeichen — Ticket-IDs werden {{prefix}}-1, {{prefix}}-2 …</div>
    <div class="cb-actions">
      <button class="secondary" onclick="closeCreateBoard()">Abbrechen</button>
      <button onclick="createBoard()">Board anlegen</button>
    </div>
  </div>
</div>

<!-- MODAL -->
<div id="modal" class="modal" onclick="closeModal(event)">
  <div class="modal-content" onclick="event.stopPropagation()">
    <div class="modal-header">
      <span class="mh-id" id="mh-id"></span>
      <span style="color:#888;font-size:11px">|</span>
      <button onclick="switchTab('view')" id="tab-view-btn" class="tab-btn active">Ansicht</button>
      <button onclick="switchTab('edit')" id="tab-edit-btn" class="tab-btn">Editieren</button>
      <button onclick="switchTab('links')" id="tab-links-btn" class="tab-btn">🔗 Links</button>
      <button onclick="switchTab('attach')" id="tab-attach-btn" class="tab-btn">📎 Bilder</button>
      <button onclick="switchTab('wt')" id="tab-wt-btn" class="tab-btn">🌳 Worktree</button>
      <span class="toast" id="mh-toast" style="margin-left:auto"></span>
      <button class="danger" onclick="deleteTicket()" title="Ticket löschen">🗑 Löschen</button>
      <button class="close" onclick="closeModal()" title="Schließen (ESC)">×</button>
    </div>
    <div class="modal-body">
      <div id="tab-view" class="tab-content active">
        <div id="mh-parent"></div>
        <div id="mh-body"></div>
        <div id="mh-children"></div>
      </div>
      <div id="tab-edit" class="tab-content">
        <div class="form-row"><label>Titel</label><input id="f-title" type="text"></div>
        <div style="display:flex;gap:10px">
          <div class="form-row" style="flex:1"><label>Type</label><select id="f-type"></select></div>
          <div class="form-row" style="flex:1"><label>Status</label><select id="f-status"></select></div>
          <div class="form-row" style="flex:1"><label>Prio</label><select id="f-prio"><option>P0</option><option>P1</option><option>P2</option></select></div>
        </div>
        <div style="display:flex;gap:10px">
          <div class="form-row" style="flex:1"><label>Tag (Cluster)</label><input id="f-tag" type="text" placeholder="z.B. backend, frontend, infra"></div>
          <div class="form-row" style="flex:1"><label>Parent (Hierarchie)</label><select id="f-parent"></select></div>
        </div>
        <div class="form-row"><label>Content (Markdown — §1–§15)</label><textarea id="f-content"></textarea></div>
        <div style="display:flex;justify-content:flex-end;gap:8px"><button onclick="saveTicket()">💾 Speichern</button></div>
      </div>
      <div id="tab-links" class="tab-content">
        <div class="links-section">
          <h4>Outgoing (dieses Ticket → …)</h4>
          <div id="links-out"></div>
        </div>
        <div class="links-section">
          <h4>Incoming (… → dieses Ticket)</h4>
          <div id="links-in"></div>
        </div>
        <div class="links-section">
          <h4>+ Link hinzufügen</h4>
          <div class="add-link-row">
            <select id="new-link-type"></select>
            <input id="new-link-target" list="ticket-suggestions" placeholder="Ziel: tippen oder aus Liste …">
            <datalist id="ticket-suggestions"></datalist>
            <button onclick="addLink()">Hinzufügen</button>
          </div>
        </div>
      </div>
      <div id="tab-attach" class="tab-content">
        <div id="attach-grid" class="attach-grid"></div>
        <div id="dropzone" class="dropzone" ondragover="onAttachOver(event)" ondragleave="onAttachLeave(event)" ondrop="onAttachDrop(event)">
          Bilder/Dateien hier hineinziehen oder
          <label for="attach-input">durchsuchen</label>
          <input id="attach-input" type="file" multiple accept="image/*,application/pdf,text/*" onchange="onAttachPick(event)">
        </div>
      </div>
      <div id="tab-wt" class="tab-content">
        <div id="wt-content"></div>
      </div>
    </div>
  </div>
</div>

<div class="footer">progr3ssboard · SQLite: <code>board.db</code> · optionaler Markdown-Importer: <code>python3 board-db.py migrate</code></div>

<script>
const ALLOWED_STATUS = {json.dumps(ALLOWED_STATUS)};
const ALLOWED_TYPE = {json.dumps(ALLOWED_TYPE)};
const LINK_TYPES = {json.dumps(LINK_TYPES)};
let currentId = null;
let currentData = null;
let allTicketIds = [];   // für Parent-Dropdown

async function openTicket(id) {{
  const res = await fetch(apiUrl('/api/tickets/' + id));
  if (!res.ok) {{ alert('Ticket nicht gefunden: '+id); return; }}
  currentData = await res.json();
  currentId = id;
  document.getElementById('mh-id').textContent = id;
  document.getElementById('mh-body').innerHTML = currentData.content_html || '<p>(leer)</p>';
  document.getElementById('f-title').value = currentData.title || '';
  document.getElementById('f-content').value = currentData.content_md || '';
  document.getElementById('f-tag').value = currentData.tag || '';
  fillSelect('f-type', ALLOWED_TYPE, currentData.type);
  fillSelect('f-status', ALLOWED_STATUS, currentData.status);
  document.getElementById('f-prio').value = currentData.prio || 'P2';
  // Parent-Dropdown (alle Tickets außer sich selbst) + Datalist für Link-Target
  if (allTicketIds.length === 0) {{
    const lst = await fetch(apiUrl('/api/tickets')).then(r => r.json());
    allTicketIds = lst.map(t => ({{id: t.id, title: t.title}}));
  }}
  const parentSel = document.getElementById('f-parent');
  parentSel.innerHTML = '<option value="">(kein Parent)</option>';
  allTicketIds.filter(t => t.id !== id).forEach(t => {{
    const o = document.createElement('option');
    o.value = t.id; o.textContent = t.id + ' — ' + (t.title || '').substring(0,60);
    if (t.id === currentData.parent_id) o.selected = true;
    parentSel.appendChild(o);
  }});
  // Datalist: alle Tickets als Vorschläge fürs Link-Target (Input bleibt frei tippbar)
  const dl = document.getElementById('ticket-suggestions');
  dl.innerHTML = '';
  allTicketIds.filter(t => t.id !== id).forEach(t => {{
    const o = document.createElement('option');
    o.value = t.id;
    o.textContent = (t.title || '').substring(0,80);
    dl.appendChild(o);
  }});
  // Links-Tab vorbereiten
  fillSelect('new-link-type', LINK_TYPES, 'relates-to');
  loadLinks();
  // Parent-Feature + Children-Liste in 'Ansicht'
  loadHierarchy();
  // Attachments
  loadAttachments();
  // Worktree
  loadWorktree();
  switchTab('view');
  document.getElementById('modal').classList.add('open');
}}

async function loadHierarchy() {{
  if (!currentId) return;
  const res = await fetch(apiUrl('/api/tickets/' + currentId + '/hierarchy'));
  if (!res.ok) return;
  const data = await res.json();
  const pe = document.getElementById('mh-parent');
  if (data.parent) {{
    pe.innerHTML = `<div class="parent-box">
      <div class="lbl">↑ Gehört zu Anforderung/Feature</div>
      <div style="margin-top:6px"><a onclick="openTicket('${{data.parent.id}}')">${{data.parent.id}} — ${{(data.parent.title||'').substring(0,80)}}</a>
      <span style="color:#888;font-size:11px"> · ${{data.parent.type}} · ${{data.parent.status}}</span></div>
    </div>`;
  }} else pe.innerHTML = '';
  const ce = document.getElementById('mh-children');
  if (data.children && data.children.length) {{
    ce.innerHTML = `<div class="children-box">
      <h4>⤓ Linked Bugs / Subtasks (${{data.children.length}})</h4>
      ${{data.children.map(c => `
        <div class="child-row" onclick="openTicket('${{c.id}}')">
          <span class="camp" style="background:${{ {{'red':'#e74c3c','orange':'#f39c12','green':'#2ecc71','grey':'#6b7280'}}[c.ampel] || '#666' }}"></span>
          <span class="cid">${{c.id}}</span>
          <span class="ctype" style="background:${{TYPE_COLORS_MAP[c.type] || '#666'}}">${{c.type}}</span>
          <span class="cstatus">${{c.status}}</span>
          <span class="ctitle">${{(c.title||'').substring(0,80)}}</span>
        </div>`).join('')}}
    </div>`;
  }} else ce.innerHTML = '';
}}

async function loadAttachments() {{
  if (!currentId) return;
  const res = await fetch(apiUrl('/api/tickets/' + currentId + '/attachments'));
  if (!res.ok) return;
  const list = await res.json();
  const grid = document.getElementById('attach-grid');
  grid.innerHTML = list.map(a => {{
    const isImg = (a.mime || '').startsWith('image/');
    const url = '/api/tickets/' + currentId + '/attachments/' + encodeURIComponent(a.filename);
    const inner = isImg
      ? `<img src="${{url}}" onclick="window.open('${{url}}','_blank')">`
      : `<div style="height:100px;display:flex;align-items:center;justify-content:center;color:#888">📄 ${{a.mime||'file'}}</div>`;
    return `<div class="attach-tile">
      ${{inner}}
      <button onclick="deleteAttach(${{a.id}})" title="Löschen">✕</button>
      <div class="att-meta">
        <div class="att-name"><a href="${{url}}" target="_blank" style="color:#5dade2;text-decoration:none">${{a.filename}}</a></div>
        ${{Math.round((a.size_bytes||0)/1024)}} KB
      </div>
    </div>`;
  }}).join('') || '<div style="color:#666;font-size:11px;padding:8px">(keine Anhänge)</div>';
}}

async function uploadAttach(file) {{
  if (!currentId) return;
  const res = await fetch(apiUrl('/api/tickets/' + currentId + '/attachments'), {{
    method: 'POST',
    headers: {{
      'Content-Type': file.type || 'application/octet-stream',
      'X-Filename': encodeURIComponent(file.name),
    }},
    body: file,
  }});
  if (res.ok) loadAttachments();
  else alert('Upload-Fehler ' + res.status);
}}

async function deleteAttach(aid) {{
  if (!confirm('Anhang löschen?')) return;
  const res = await fetch(apiUrl('/api/attachments/' + aid), {{method:'DELETE'}});
  if (res.ok) loadAttachments();
}}

function onAttachOver(e)  {{ e.preventDefault(); document.getElementById('dropzone').classList.add('over'); }}
function onAttachLeave(e) {{ e.currentTarget.classList.remove('over'); }}
async function onAttachDrop(e) {{
  e.preventDefault(); document.getElementById('dropzone').classList.remove('over');
  for (const f of e.dataTransfer.files) await uploadAttach(f);
}}
async function onAttachPick(e) {{
  for (const f of e.target.files) await uploadAttach(f);
  e.target.value = '';
}}

async function loadWorktree() {{
  if (!currentId) return;
  const res = await fetch(apiUrl('/api/tickets/' + currentId + '/worktree'));
  if (!res.ok) return;
  const data = await res.json();
  const el = document.getElementById('wt-content');
  if (!data || data.exists === undefined) {{
    el.innerHTML = `<div class="worktree-box">
      <div style="color:#888;margin-bottom:10px">Noch kein Worktree für dieses Ticket angelegt.</div>
      <div class="actions"><button onclick="createWT()">🌳 Worktree erstellen</button></div>
      <div style="color:#666;font-size:11px;margin-top:8px">Erstellt: <code>~/progr3ssboard-worktrees/${{currentId}}</code> · Branch <code>ticket/${{currentId}}</code> (von <code>main</code>)</div>
    </div>`;
    return;
  }}
  if (!data.exists) {{
    el.innerHTML = `<div class="worktree-box">
      <div style="color:#c0392b">Worktree-Pfad fehlt: ${{data.path}}</div>
      <div class="actions"><button class="danger" onclick="removeWT(true)">Eintrag bereinigen</button></div>
    </div>`;
    return;
  }}
  el.innerHTML = `<div class="worktree-box">
    <div class="row"><span class="k">Pfad:</span><span class="v">${{data.path}}</span></div>
    <div class="row"><span class="k">Branch:</span><span class="v">${{data.branch}}</span></div>
    <div class="row"><span class="k">HEAD:</span><span class="v">${{data.head || '(unbekannt)'}}</span></div>
    <div class="row"><span class="k">Dirty:</span><span class="v ${{data.dirty?'dirty':''}}">${{data.dirty?(data.dirty_lines+' geänderte Dateien'):'sauber'}}</span></div>
    <div class="actions">
      <button onclick="navigator.clipboard.writeText('${{data.path}}').then(()=>alert('Pfad kopiert'))">📋 Pfad kopieren</button>
      <button class="danger" onclick="removeWT(false)">🗑 Worktree entfernen</button>
    </div>
  </div>`;
}}

async function createWT() {{
  const res = await fetch(apiUrl('/api/tickets/' + currentId + '/worktree'), {{method:'POST'}});
  if (res.ok) loadWorktree();
  else {{ const e = await res.json().catch(()=>({{}})); alert('Fehler: ' + (e.error||res.status)); }}
}}

async function removeWT(force) {{
  if (!confirm('Worktree wirklich entfernen?' + (force?' (force)':''))) return;
  const url = '/api/tickets/' + currentId + '/worktree' + (force?'?force=1':'');
  const res = await fetch(url, {{method:'DELETE'}});
  if (res.ok) loadWorktree();
  else {{ const e = await res.json().catch(()=>({{}})); alert('Fehler: ' + (e.error||res.status)); }}
}}

const TYPE_COLORS_MAP = {json.dumps({k: v[0] for k, v in TYPE_COLORS.items()})};

async function loadLinks() {{
  if (!currentId) return;
  const res = await fetch(apiUrl('/api/links/' + currentId));
  if (!res.ok) return;
  const data = await res.json();
  const renderRow = (l) => `<div class="link-row">
    <span class="type">${{l.type}}</span>
    <span class="target">${{l.other}}</span>
    <span class="titletxt">${{(l.other_title || '').substring(0,80)}}</span>
    <button onclick="removeLink(${{l.id}})" title="Link löschen">✕</button>
  </div>`;
  document.getElementById('links-out').innerHTML =
    data.outgoing.length ? data.outgoing.map(renderRow).join('') : '<div style="color:#666;font-size:11px">(keine)</div>';
  document.getElementById('links-in').innerHTML =
    data.incoming.length ? data.incoming.map(renderRow).join('') : '<div style="color:#666;font-size:11px">(keine)</div>';
}}

async function addLink() {{
  if (!currentId) return;
  const dst  = document.getElementById('new-link-target').value.trim().toUpperCase();
  const type = document.getElementById('new-link-type').value;
  if (!dst.match(/^B-\\d+$/)) {{ alert('Ziel-ID muss B-N sein'); return; }}
  const res = await fetch(apiUrl('/api/links'), {{
    method: 'POST', headers: {{'Content-Type':'application/json'}},
    body: JSON.stringify({{src: currentId, dst: dst, type: type}})
  }});
  if (res.ok) {{ document.getElementById('new-link-target').value=''; loadLinks(); }}
  else {{ const err = await res.json().catch(()=>({{}})); alert('Fehler: ' + (err.error || res.status)); }}
}}

async function removeLink(linkId) {{
  if (!confirm('Link löschen?')) return;
  const res = await fetch(apiUrl('/api/links/' + linkId), {{method: 'DELETE'}});
  if (res.ok) loadLinks();
}}

function fillSelect(id, options, current) {{
  const sel = document.getElementById(id);
  sel.innerHTML = '';
  options.forEach(o => {{
    const el = document.createElement('option');
    el.value = o; el.textContent = o;
    if (o === current) el.selected = true;
    sel.appendChild(el);
  }});
}}

function switchTab(name) {{
  document.querySelectorAll('.tab-content').forEach(e => e.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(e => e.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.getElementById('tab-' + name + '-btn').classList.add('active');
}}

function closeModal(ev) {{
  if (ev && ev.target.id !== 'modal' && ev.type === 'click') return;
  document.getElementById('modal').classList.remove('open');
  currentId = null; currentData = null;
}}

async function saveTicket() {{
  if (!currentId) return;
  const data = {{
    title: document.getElementById('f-title').value,
    type:  document.getElementById('f-type').value,
    status:document.getElementById('f-status').value,
    prio:  document.getElementById('f-prio').value,
    tag:   document.getElementById('f-tag').value || null,
    parent_id: document.getElementById('f-parent').value || null,
    content_md: document.getElementById('f-content').value,
  }};
  const res = await fetch(apiUrl('/api/tickets/' + currentId), {{
    method: 'PUT', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify(data),
  }});
  showToast(res.ok ? '✓ gespeichert' : '✗ Fehler ' + res.status, res.ok);
  if (res.ok) setTimeout(() => location.reload(), 700);
}}

async function deleteTicket() {{
  if (!currentId) return;
  if (!confirm('Ticket ' + currentId + ' wirklich löschen?')) return;
  const res = await fetch(apiUrl('/api/tickets/' + currentId), {{method: 'DELETE'}});
  showToast(res.ok ? '✓ gelöscht' : '✗ Fehler', res.ok);
  if (res.ok) setTimeout(() => location.reload(), 700);
}}

async function newTicket(status) {{
  status = status || 'open';
  const title = prompt('Titel des neuen Tickets (Status: ' + status + '):');
  if (!title) return;
  const res = await fetch(apiUrl('/api/tickets'), {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{title: title, type: 'BUG', status: status, prio: 'P2'}}),
  }});
  if (res.ok) location.reload();
  else alert('Fehler ' + res.status);
}}

// ─── Project + URL ──
function currentProject() {{
  const u = new URL(location.href);
  return u.searchParams.get('project') || '';
}}
function apiUrl(path) {{
  const p = currentProject();
  if (!p) return path;
  return path + (path.includes('?') ? '&' : '?') + 'project=' + encodeURIComponent(p);
}}
function switchProject(slug) {{
  const u = new URL(location.href);
  if (slug) u.searchParams.set('project', slug); else u.searchParams.delete('project');
  location.href = u.toString();
}}

// ─── Create-Board-Modal ──
function openCreateBoard() {{ document.getElementById('cb-overlay').classList.add('show'); document.getElementById('cb-key').focus(); }}
function closeCreateBoard() {{ document.getElementById('cb-overlay').classList.remove('show'); }}
async function createBoard() {{
  const key    = document.getElementById('cb-key').value.trim();
  const name   = document.getElementById('cb-name').value.trim();
  const prefix = document.getElementById('cb-prefix').value.trim().toUpperCase();
  if (!key || !name) {{ alert('Slug und Name sind Pflicht'); return; }}
  const res = await fetch('/api/projects', {{
    method: 'POST', headers: {{'Content-Type':'application/json'}},
    body: JSON.stringify({{key: key, name: name, id_prefix: prefix || 'B'}}),
  }});
  if (res.ok) {{
    const u = new URL(location.href);
    u.searchParams.set('project', key);
    location.href = u.toString();
  }} else {{
    const j = await res.json().catch(()=>({{error:'unknown'}}));
    alert('Anlage fehlgeschlagen: ' + (j.error || res.status));
  }}
}}

// ─── Globale Suche (cross-project) ──
let searchDeb = null;
function hideSearchResults() {{ document.getElementById('search-results').classList.remove('show'); }}
function onGlobalSearch(q) {{
  clearTimeout(searchDeb);
  const box = document.getElementById('search-results');
  if (!q || q.length < 2) {{ box.classList.remove('show'); return; }}
  searchDeb = setTimeout(async () => {{
    const res = await fetch('/api/search?q=' + encodeURIComponent(q));
    const rows = await res.json();
    if (!rows.length) {{ box.innerHTML = '<div class="sr-empty">keine Treffer</div>'; box.classList.add('show'); return; }}
    box.innerHTML = rows.slice(0,25).map(r =>
      '<a href="?project=' + encodeURIComponent(r.project) + '#' + r.id + '">'
      + '<span class="sr-proj">' + r.project + '</span>'
      + '<b>' + r.id + '</b> · ' + r.title.replace(/[<>&]/g, c => ({{'<':'&lt;','>':'&gt;','&':'&amp;'}}[c]))
      + ' <span style="color:#888">(' + r.status + ')</span></a>'
    ).join('');
    box.classList.add('show');
  }}, 200);
}}

function showToast(msg, ok) {{
  const t = document.getElementById('mh-toast');
  t.textContent = msg;
  t.className = 'toast show' + (ok ? '' : ' err');
  setTimeout(() => t.classList.remove('show'), 1500);
}}

// ─── Drag-Drop (horizontal=Status, vertikal=Sort) ──
let dragged = null;
function clearDropMarks() {{
  document.querySelectorAll('.card.drop-before,.card.drop-after').forEach(el => el.classList.remove('drop-before','drop-after'));
  document.querySelectorAll('.column.drag-over').forEach(el => el.classList.remove('drag-over'));
}}
document.addEventListener('dragstart', e => {{
  if (e.target.classList && e.target.classList.contains('card')) {{
    dragged = e.target; e.target.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
  }}
}});
document.addEventListener('dragend', e => {{
  if (dragged) dragged.classList.remove('dragging');
  dragged = null; clearDropMarks();
}});
function _cardAt(col, y) {{
  // Finde die Karte unter Maus (oder vor/nach welcher Karte eingefügt werden soll)
  const cards = [...col.querySelectorAll('.card:not(.dragging)')];
  for (const c of cards) {{
    const r = c.getBoundingClientRect();
    const mid = r.top + r.height / 2;
    if (y < mid) return {{card: c, position: 'before'}};
  }}
  if (cards.length) return {{card: cards[cards.length-1], position: 'after'}};
  return null;
}}
function onColDragOver(e) {{
  e.preventDefault();
  if (!dragged) return;
  const col = e.currentTarget;
  clearDropMarks();
  col.classList.add('drag-over');
  const hit = _cardAt(col, e.clientY);
  if (hit) hit.card.classList.add('drop-' + hit.position);
}}
function onColDragLeave(e) {{
  // Nur leeren wenn Maus die Spalte WIRKLICH verlässt (nicht beim Hover über Child)
  if (e.currentTarget.contains(e.relatedTarget)) return;
  e.currentTarget.classList.remove('drag-over');
}}
async function onColDrop(e) {{
  e.preventDefault();
  if (!dragged) return;
  const col = e.currentTarget;
  const tid = dragged.dataset.id;
  const newStatus = col.dataset.status;
  const hit = _cardAt(col, e.clientY);
  const before_id = hit && hit.position === 'before' ? hit.card.dataset.id : null;
  const after_id  = hit && hit.position === 'after'  ? hit.card.dataset.id : null;
  clearDropMarks();
  const res = await fetch(apiUrl('/api/tickets/' + tid + '/reorder'), {{
    method: 'PUT', headers: {{'Content-Type':'application/json'}},
    body: JSON.stringify({{status: newStatus, before_id: before_id, after_id: after_id}}),
  }});
  if (res.ok) location.reload();
  else {{
    const j = await res.json().catch(()=>({{error:'unknown'}}));
    alert('Drop fehlgeschlagen: ' + (j.error || res.status));
  }}
}}

document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeModal(); }});
</script>
</body></html>'''

# ─── HTTP Server ───────────────────────────────────────────────────────────────

class H(BaseHTTPRequestHandler):
    def _project(self):
        q = parse_qs(urlparse(self.path).query)
        p = (q.get('project') or [DEFAULT_PROJECT])[0]
        return p if p in PROJECTS else DEFAULT_PROJECT

    def _send(self, code, body, ctype="text/html"):
        # WICHTIG: erst encoden, dann Bytes-Länge messen (UTF-8/Emoji-safe)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, data):
        self._send(code, json.dumps(data, ensure_ascii=False), "application/json")

    def _read_body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        project = self._project()
        u = urlparse(self.path)
        try:
            if u.path == "/":
                return self._send(200, render_page(project))
            if u.path == "/api/tickets":
                conn = connect(project)
                rows = [dict(r) for r in conn.execute("SELECT id,type,status,tag,prio,title,iter_count FROM tickets ORDER BY id")]
                conn.close()
                return self._json(200, rows)
            m = re.match(r"^/api/tickets/([A-Z][A-Z0-9-]*-\d+)$", u.path)
            if m:
                tid = m.group(1)
                conn = connect(project)
                row = conn.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
                if not row:
                    conn.close(); return self._json(404, {"error":"not found"})
                d = dict(row)
                d['content_html'] = md_to_html(d.get('content_md') or '')
                d['iterations'] = [dict(r) for r in conn.execute("SELECT * FROM iterations WHERE ticket_id=? ORDER BY iter_num", (tid,))]
                conn.close()
                return self._json(200, d)
            # Links GET
            m = re.match(r"^/api/links/([A-Z][A-Z0-9-]*-\d+)$", u.path)
            if m:
                return self._json(200, get_links(m.group(1), project=project))
            # Hierarchy (parent + children)
            m = re.match(r"^/api/tickets/([A-Z][A-Z0-9-]*-\d+)/hierarchy$", u.path)
            if m:
                tid = m.group(1)
                return self._json(200, {"parent": get_parent(tid, project=project), "children": get_children(tid, project=project)})
            # Attachments list
            m = re.match(r"^/api/tickets/([A-Z][A-Z0-9-]*-\d+)/attachments$", u.path)
            if m:
                return self._json(200, list_attachments(m.group(1), project=project))
            # Attachment file (binary)
            m = re.match(r"^/api/tickets/([A-Z][A-Z0-9-]*-\d+)/attachments/(.+)$", u.path)
            if m:
                tid = m.group(1); fname = unquote(m.group(2))
                # sicher: nur basename, kein ..
                safe = re.sub(r'[^A-Za-z0-9._-]', '_', fname)[:120]
                fp = _attach_dir(project) / tid / safe
                if not fp.exists():
                    return self._json(404, {"error":"not found"})
                mime, _ = mimetypes.guess_type(safe)
                return self._send(200, fp.read_bytes(), mime or 'application/octet-stream')
            # Worktree status
            m = re.match(r"^/api/tickets/([A-Z][A-Z0-9-]*-\d+)/worktree$", u.path)
            if m:
                return self._json(200, worktree_status(m.group(1), project=project) or {})
            # Cross-Project-Suche
            if u.path == "/api/search":
                q = parse_qs(u.query).get('q', [''])[0]
                return self._json(200, search_all_projects(q))
            # Projekt-Liste (für externe Tools / Future-UI)
            if u.path == "/api/projects":
                return self._json(200, [
                    {"key": k, "name": v["name"], "custom": bool(v.get("custom"))}
                    for k, v in PROJECTS.items()
                ])
            self._json(404, {"error":"not found"})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def do_POST(self):
        project = self._project()
        u = urlparse(self.path)
        try:
            if u.path == "/api/tickets":
                data = self._read_body()
                tid = create_ticket(data, project=project)
                return self._json(201, {"id": tid})
            if u.path == "/api/links":
                data = self._read_body()
                try:
                    link_id = create_link(data.get('src'), data.get('dst'), data.get('type'), project=project)
                    return self._json(201, {"id": link_id})
                except ValueError as ve:
                    return self._json(400, {"error": str(ve)})
            # Attachment upload (raw body + X-Filename)
            m = re.match(r"^/api/tickets/([A-Z][A-Z0-9-]*-\d+)/attachments$", u.path)
            if m:
                tid = m.group(1)
                fname = unquote(self.headers.get('X-Filename', 'file.bin'))
                mime  = self.headers.get('Content-Type', 'application/octet-stream')
                n = int(self.headers.get('Content-Length', 0))
                if n <= 0 or n > 20*1024*1024:
                    return self._json(400, {"error":"empty or too large (>20MB)"})
                body = self.rfile.read(n)
                res = add_attachment(tid, fname, body, mime, project=project)
                return self._json(201, res)
            # Worktree create
            m = re.match(r"^/api/tickets/([A-Z][A-Z0-9-]*-\d+)/worktree$", u.path)
            if m:
                tid = m.group(1)
                try:
                    return self._json(201, create_worktree(tid, project=project))
                except ValueError as ve:
                    return self._json(400, {"error": str(ve)})
            # Custom-Projekt anlegen
            if u.path == "/api/projects":
                data = self._read_body()
                try:
                    cfg = add_custom_project(
                        key=(data.get('key') or '').strip().lower(),
                        name=(data.get('name') or '').strip(),
                        id_prefix=(data.get('id_prefix') or 'B').strip().upper(),
                    )
                    return self._json(201, {"key": data.get('key'), "name": cfg["name"]})
                except ValueError as ve:
                    return self._json(400, {"error": str(ve)})
            self._json(404, {"error":"not found"})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def do_PUT(self):
        project = self._project()
        u = urlparse(self.path)
        try:
            m = re.match(r"^/api/tickets/([A-Z][A-Z0-9-]*-\d+)$", u.path)
            if m:
                tid = m.group(1)
                data = self._read_body()
                ok = update_ticket(tid, data, project=project)
                return self._json(200 if ok else 400, {"ok": ok})
            # Reorder (status + sort_order)
            m = re.match(r"^/api/tickets/([A-Z][A-Z0-9-]*-\d+)/reorder$", u.path)
            if m:
                tid = m.group(1)
                data = self._read_body()
                ok = reorder_ticket(tid,
                                    new_status=data.get('status'),
                                    before_id=data.get('before_id'),
                                    after_id=data.get('after_id'),
                                    project=project)
                return self._json(200 if ok else 404, {"ok": ok})
            self._json(404, {"error":"not found"})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def do_DELETE(self):
        project = self._project()
        u = urlparse(self.path)
        try:
            m = re.match(r"^/api/tickets/([A-Z][A-Z0-9-]*-\d+)$", u.path)
            if m:
                tid = m.group(1)
                ok = delete_ticket(tid, project=project)
                return self._json(200 if ok else 404, {"ok": ok})
            m = re.match(r"^/api/links/(\d+)$", u.path)
            if m:
                ok = delete_link(int(m.group(1)), project=project)
                return self._json(200 if ok else 404, {"ok": ok})
            # Attachment delete
            m = re.match(r"^/api/attachments/(\d+)$", u.path)
            if m:
                ok = delete_attachment(int(m.group(1)), project=project)
                return self._json(200 if ok else 404, {"ok": ok})
            # Worktree remove
            m = re.match(r"^/api/tickets/([A-Z][A-Z0-9-]*-\d+)/worktree$", u.path)
            if m:
                tid = m.group(1)
                q = parse_qs(u.query)
                force = bool(q.get('force'))
                try:
                    ok = remove_worktree(tid, force=force, project=project)
                    return self._json(200 if ok else 404, {"ok": ok})
                except ValueError as ve:
                    return self._json(400, {"error": str(ve)})
            self._json(404, {"error":"not found"})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def log_message(self, fmt, *args): pass

def _local_urls(port):
    """Sammle alle Adressen unter denen das Board erreichbar ist."""
    import socket
    urls = [f"http://localhost:{port}"]
    # LAN-IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]; s.close()
        if not lan_ip.startswith("127."):
            urls.append(f"http://{lan_ip}:{port}")
    except Exception: pass
    # Tailscale
    try:
        ts = subprocess.check_output(["tailscale","ip","-4"], stderr=subprocess.DEVNULL, timeout=2).decode().strip().split('\n')[0]
        if ts: urls.append(f"http://{ts}:{port}  (Tailscale — von anderen Geräten im Tailnet)")
    except Exception: pass
    return urls

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--host", default="0.0.0.0", help="Bind-Adresse (default 0.0.0.0 = LAN/Tailscale erreichbar; 127.0.0.1 = nur lokal)")
    args = ap.parse_args()
    if args.serve:
        print(f"📋 progr3ssboard live · bind={args.host}:{args.port}  (Strg+C zum Stoppen)")
        for u in _local_urls(args.port):
            print(f"   → {u}")
        try:
            HTTPServer((args.host, args.port), H).serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        print("Usage: python3 progr3ssboard.py --serve")

if __name__ == "__main__":
    main()
