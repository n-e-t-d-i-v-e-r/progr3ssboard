#!/usr/bin/env python3
"""
board-db.py — SQLite-Schema + optionaler Markdown-Importer für bestehende Backlog-Dateien.

Shared module: progr3ssboard.py importiert die Helper-Funktionen.

Usage:
  python3 board-db.py migrate     # einmaliger Import (idempotent — kann re-run werden)
  python3 board-db.py reset       # DB löschen + neu importieren
  python3 board-db.py dump        # Tickets als JSON dumpen (Debug)
"""
import sqlite3, sys, re, json, argparse, subprocess
from pathlib import Path
from datetime import datetime

# Basis-Pfad: dort wo progr3ssboard.py liegt. Boards landen unter ./boards/<key>/.
BASE = Path(__file__).resolve().parent

# Multi-Project-Registry — Default-Eintrag ist „default". Weitere Boards werden
# zur Laufzeit über die UI (+ Board) angelegt und in boards/projects.json persistiert.
PROJECTS = {
    "default": {
        "name":          "Default Board",
        "source_md":     None,    # kein Markdown-Import — neues Board, frische DB
        "db":            BASE / "boards/default/board.db",
        "attachments":   BASE / "boards/default/attachments",
        "worktree_base": Path.home() / "progr3ssboard-worktrees/default",
        "id_pattern":    r"^B-\d+$",
        "id_regex":      r"B-\d+",
        "id_prefix":     "B",
    },
}
DEFAULT_PROJECT = "default"

# Custom-Projects via JSON — vom User zur Laufzeit über UI angelegt.
# Liegt unter boards/projects.json — git-ignoriert per default (.gitignore).
_CUSTOM_PROJECTS_FILE = BASE / "boards/projects.json"

def _load_custom_projects():
    """Liest persistierte Custom-Projects + merged in PROJECTS-Dict (in-place).
    Format: [{"key":"slug","name":"…","id_prefix":"B","source_md":null|"path"}].
    Hardcoded Einträge werden NICHT überschrieben (Schutz vor versehentlichem Shadow)."""
    if not _CUSTOM_PROJECTS_FILE.exists():
        return
    try:
        entries = json.loads(_CUSTOM_PROJECTS_FILE.read_text() or "[]")
    except Exception as e:
        print(f"WARN: projects.json unlesbar — {e}", file=sys.stderr); return
    for e in entries:
        key = e.get("key")
        if not key or key in PROJECTS: continue   # Hardcoded gewinnen
        prefix = e.get("id_prefix") or "B"
        base   = BASE / "boards" / key
        src_md = e.get("source_md")
        PROJECTS[key] = {
            "name":          e.get("name") or key,
            "source_md":     (BASE / src_md) if src_md else None,
            "db":            base / "board.db",
            "attachments":   base / "attachments",
            "worktree_base": Path.home() / "progr3ssboard-worktrees" / key,
            "id_pattern":    rf"^{re.escape(prefix)}-\d+$",
            "id_regex":      rf"{re.escape(prefix)}-\d+",
            "custom":        True,
            "id_prefix":     prefix,
        }

def add_custom_project(key, name, id_prefix="B", source_md=None):
    """Persistiert ein neues Projekt + legt leere DB an. Idempotent: gleicher key
    wird abgelehnt. Returnt das fertige cfg-Dict."""
    if not re.match(r"^[a-z][a-z0-9-]{1,40}$", key or ""):
        raise ValueError("key muss lowercase-slug sein (a-z, 0-9, '-'), 2-41 Zeichen")
    if key in PROJECTS:
        raise ValueError(f"projekt-key bereits vergeben: {key}")
    if not re.match(r"^[A-Z][A-Z0-9]{0,7}$", id_prefix or ""):
        raise ValueError("id_prefix muss 1-8 GROSSBUCHSTABEN/Ziffern sein (z.B. 'B', 'TASK', 'FOO')")
    _CUSTOM_PROJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    cur = []
    if _CUSTOM_PROJECTS_FILE.exists():
        try: cur = json.loads(_CUSTOM_PROJECTS_FILE.read_text() or "[]")
        except Exception: cur = []
    cur.append({"key": key, "name": name or key, "id_prefix": id_prefix, "source_md": source_md})
    _CUSTOM_PROJECTS_FILE.write_text(json.dumps(cur, indent=2, ensure_ascii=False))
    _load_custom_projects()        # re-merge → PROJECTS hat den neuen Eintrag
    migrate(project=key)           # leere DB + Schema anlegen
    return PROJECTS[key]

# Initial-Load beim Modul-Import
_load_custom_projects()

# Legacy-Aliases (board.db an alter Stelle bleibt bestehen falls jemand drauf zugreift)
BL   = PROJECTS[DEFAULT_PROJECT]["source_md"]
DB   = PROJECTS[DEFAULT_PROJECT]["db"]

def project_cfg(name=None):
    name = name or DEFAULT_PROJECT
    if name not in PROJECTS:
        raise ValueError(f"unknown project: {name} (verfügbar: {list(PROJECTS)})")
    return PROJECTS[name]

def date_today_iso():
    from datetime import date as _d
    return _d.today().isoformat()

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
  id           TEXT PRIMARY KEY,
  type         TEXT NOT NULL DEFAULT 'BUG',
  status       TEXT NOT NULL DEFAULT 'open',
  tag          TEXT,
  prio         TEXT,
  title        TEXT NOT NULL,
  content_md   TEXT,
  created_date TEXT,
  last_reset   TEXT,
  parent_id    TEXT,                    -- Phase 2: Hierarchie
  tests_total  INTEGER DEFAULT 0,
  tests_passed INTEGER DEFAULT 0,
  iter_count   INTEGER DEFAULT 0,
  worktree_path   TEXT,                 -- Phase 3: git worktree-Pfad
  worktree_branch TEXT,                 -- Phase 3: git worktree-Branch
  updated_at   TEXT NOT NULL,
  FOREIGN KEY (parent_id) REFERENCES tickets(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS attachments (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id   TEXT NOT NULL,
  filename    TEXT NOT NULL,
  mime        TEXT,
  size_bytes  INTEGER,
  uploaded_at TEXT NOT NULL,
  FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS iterations (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id  TEXT NOT NULL,
  iter_num   INTEGER NOT NULL,
  deploy_date TEXT,
  kind       TEXT,                       -- fix/revert/krücke/recur
  label      TEXT,
  deployed   INTEGER DEFAULT 0,
  UNIQUE(ticket_id, iter_num),
  FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS links (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  src        TEXT NOT NULL,
  dst        TEXT NOT NULL,
  type       TEXT NOT NULL,              -- blocks/blocked-by/relates-to/duplicates
  UNIQUE(src, dst, type),
  FOREIGN KEY (src) REFERENCES tickets(id) ON DELETE CASCADE,
  FOREIGN KEY (dst) REFERENCES tickets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS comments (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id  TEXT NOT NULL,
  body_md    TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS activity (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id  TEXT,
  action     TEXT NOT NULL,              -- create/update/delete/status_change/link/comment
  detail     TEXT,                       -- JSON
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);

CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_type ON tickets(type);
CREATE INDEX IF NOT EXISTS idx_tickets_tag ON tickets(tag);
CREATE INDEX IF NOT EXISTS idx_iter_ticket ON iterations(ticket_id);
CREATE INDEX IF NOT EXISTS idx_links_src ON links(src);
"""

def connect(project=None):
    cfg = project_cfg(project)
    db_path = cfg["db"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    existing = {r[1] for r in conn.execute("PRAGMA table_info(tickets)").fetchall()}
    for col, ddl in (("worktree_path", "TEXT"), ("worktree_branch", "TEXT"), ("sort_order", "INTEGER")):
        if col not in existing:
            conn.execute(f"ALTER TABLE tickets ADD COLUMN {col} {ddl}")
    # sort_order Defaults: pro Status nach id-Reihenfolge nummerieren (idempotent: nur wo NULL)
    has_unset = conn.execute("SELECT 1 FROM tickets WHERE sort_order IS NULL LIMIT 1").fetchone()
    if has_unset:
        cur = conn.execute("SELECT id,status FROM tickets WHERE sort_order IS NULL ORDER BY status, id")
        per_status = {}
        for r in cur:
            st = r['status'] or 'open'
            per_status.setdefault(st, 0)
            per_status[st] += 10
            conn.execute("UPDATE tickets SET sort_order=? WHERE id=?", (per_status[st], r['id']))
        conn.commit()
    return conn

# ─── Parsing (aus board-html.py wiederverwendet) ────────────────────────────────

def _ticket_type(title, phase):
    t = (title or '').upper()
    p = (phase or '').lower().strip()
    if t.startswith("HOTFIX"): return "HOTFIX"
    if "FEATURE-REBUILD" in t: return "REBUILD"
    if "FEATURE-REQUEST" in t: return "FEATURE"
    if t.startswith("FUTURE-WORK"): return "FUTURE"
    if t.startswith("INFRA"): return "INFRA"
    if p in ("spec", "design-capture", "design-cap"): return "SPEC"
    return "BUG"

def _strip_prefix(title):
    for prefix in ("FEATURE-REQUEST:", "FEATURE-REBUILD:", "Future-Work:",
                   "FUTURE-WORK:", "HOTFIX —", "HOTFIX:", "INFRA:"):
        if title.startswith(prefix):
            return title[len(prefix):].lstrip(" -—:")
    return title

def _parse_phase_status(content):
    """§1-Status aus dem §1-Block extrahieren (oder None)."""
    m = re.search(r"### §1[^\n]*\n(.+?)(?=\n###|\Z)", content, re.DOTALL)
    if not m: return None
    block = m.group(1)
    # `**Status:** xyz` oder `**xyz**` als ersten Marker
    s = re.search(r"\*\*(?:Status|Phase)[:\s]+\*\*\s*([^\n*]+)", block, re.I)
    if s: return s.group(1).strip().lower()
    s = re.search(r"\*\*([a-z\-]+)\*\*", block)
    if s: return s.group(1).strip().lower()
    return None

def _parse_iters(content):
    """[(num, deploy_date, kind, label, deployed?)] aus §13/§14."""
    out = []
    # §13 ### Iter-N (...) — Label
    for m in re.finditer(r"^### .*?Iter[-\s]?(\d+)\s*(?:\(([^)]+)\))?\s*[—\-]\s*(.+)$",
                         content, re.MULTILINE):
        num = int(m.group(1))
        ctx = m.group(2) or ""
        label = m.group(3).strip()
        dm = re.search(r"(\d{4})-(\d{2})-(\d{2})", ctx + " " + label)
        dt = dm.group(0) if dm else None
        deployed = ("DEPLOY" in ctx.upper() or "deploy" in label.lower() or "sandbox" in label.lower())
        kind = _iter_kind(ctx + " " + label)
        out.append((num, dt, kind, label[:300], int(deployed)))
    # §14/§15 - **Iter-N <ctx>:** label
    for m in re.finditer(r"^\s*[-*]\s*\*\*Iter[-\s]?(\d+)\s+([^*:]*?):\*\*\s*(.+)$",
                         content, re.MULTILINE):
        num = int(m.group(1))
        ctx = m.group(2).strip()
        label = m.group(3).strip()
        if any(o[0] == num for o in out): continue   # schon aus §13
        dm = re.search(r"(\d{4})-(\d{2})-(\d{2})", ctx + " " + label)
        dt = dm.group(0) if dm else None
        deployed = ("deploy" in label.lower() or "sandbox" in label.lower())
        kind = _iter_kind(ctx + " " + label)
        out.append((num, dt, kind, label[:300], int(deployed)))
    out.sort(key=lambda x: x[0])
    return out

def _iter_kind(s):
    s = s.lower()
    if "revert" in s or "regress" in s: return "revert"
    if "krücke" in s or "krucke" in s or "band-aid" in s: return "krücke"
    if "recur" in s: return "recur"
    return "fix"

def _extract_intrinsic_links_re(tid, content_md, id_re):
    """Intrinsische Links projekt-aware. id_re = compiled regex für gültige IDs."""
    out = set()
    if not content_md: return out
    pat = id_re.pattern
    s11 = re.search(r"###\s*§11[^\n]*\n(.*?)(?=\n###|\Z)", content_md, re.DOTALL)
    if s11:
        block = s11.group(1)
        for m in re.finditer(rf"(?:RESOLVED[-_ ]?BY|Resolved[-_ ]?By|resolved-by)[:\s]*({pat})", block):
            d = m.group(1)
            if d != tid: out.add((tid, d, "blocked-by"))
        for m in re.finditer(rf"(?:RESOLVES|Resolves|resolves)[:\s]*({pat})", block):
            d = m.group(1)
            if d != tid: out.add((tid, d, "blocks"))
    for m in re.finditer(rf"\[\[({pat})\]\]", content_md):
        d = m.group(1)
        if d != tid: out.add((tid, d, "relates-to"))
    for m in re.finditer(rf"\bRESOLVED[-_ ]BY[:\s]+({pat})\b", content_md):
        d = m.group(1)
        if d != tid: out.add((tid, d, "blocked-by"))
    return out

# Back-compat
def _extract_intrinsic_links(tid, content_md):
    return _extract_intrinsic_links_re(tid, content_md, re.compile(r"B-\d+"))

# ─── Worktree-Discovery ─────────────────────────────────────────────────────────

WORKTREE_BASE  = Path.home() / "progr3ssboard-worktrees"

def discover_worktrees(id_regex, repo_path=None):
    """Scan `git worktree list` → [(tid, path, branch)] mit projekt-spezifischem ID-Regex.
    Default: aktuelles cwd (kein hardcoded Repo-Pfad)."""
    matched = []
    repo = str(repo_path) if repo_path else "."
    try:
        out = subprocess.check_output(
            ["git","-C",repo,"worktree","list","--porcelain"],
            stderr=subprocess.DEVNULL).decode()
    except Exception:
        return matched
    cur = {}
    for line in out.splitlines() + [""]:
        if not line.strip():
            if cur.get('worktree'):
                path = cur['worktree']
                branch = (cur.get('branch') or '').replace('refs/heads/','')
                # Match: branch ticket/<TID>
                m = re.search(rf'^ticket/({id_regex})$', branch)
                tid = m.group(1) if m else None
                # Match: dir-name = <TID>
                if not tid:
                    m2 = re.match(rf'^({id_regex})$', Path(path).name)
                    tid = m2.group(1) if m2 else None
                if tid:
                    matched.append((tid, path, branch))
            cur = {}
        elif ' ' in line:
            k, v = line.split(' ', 1)
            cur[k] = v
        else:
            cur[line] = ''
    return matched

def import_worktrees(project=None):
    cfg = project_cfg(project)
    conn = connect(project); cur = conn.cursor()
    n = 0
    now = datetime.now().isoformat(timespec='seconds')
    for tid, path, branch in discover_worktrees(cfg["id_regex"]):
        if not cur.execute("SELECT 1 FROM tickets WHERE id=?", (tid,)).fetchone():
            continue
        cur.execute("UPDATE tickets SET worktree_path=?, worktree_branch=?, updated_at=? WHERE id=?",
                    (path, branch, now, tid))
        n += 1
    conn.commit(); conn.close()
    return n

def _split_tickets(text, id_regex):
    """{TID: full_section_text} — id_regex z.B. 'B-\\d+' oder '[A-Z]+-\\d+'"""
    out = {}
    ms = list(re.finditer(rf"^## ({id_regex})\b", text, re.MULTILINE))
    for i, m in enumerate(ms):
        tid = m.group(1)
        start = m.start()
        end = ms[i+1].start() if i+1 < len(ms) else len(text)
        out[tid] = text[start:end].rstrip()
    return out

def _header_meta(line, id_regex):
    """'## TID — Title (YYYY-MM-DD) — Prio' (Datum optional)."""
    m = re.match(rf"^## ({id_regex})\s+—\s+(.+?)(?:\s+\((\d{{4}}-\d{{2}}-\d{{2}})\))?\s*(?:—\s*([A-Z0-9-]+))?$", line)
    if not m: return None
    return m.group(1), m.group(2).strip(), (m.group(3) or ''), (m.group(4) or "")

def _counter_meta(content):
    """COUNTER-AUTO-Block parsen: Days-Free, Tests, Iter, Promo, Phase."""
    m = re.search(r"<!-- COUNTER-AUTO -->.*?Days-Free=(\d+)d.*?Tests=(\d+)/(\d+).*?Iter=([\w⚠]+).*?Phase=([\w:-]+)",
                  content)
    if not m: return {}
    iter_s = m.group(4)
    iter_n = int(re.search(r'\d+', iter_s).group()) if re.search(r'\d+', iter_s) else 0
    return {
        "days_free": int(m.group(1)),
        "tests_passed": int(m.group(2)),
        "tests_total": int(m.group(3)),
        "iter_count": iter_n,
        "phase": m.group(5).strip().rstrip(':'),
    }

# ─── Migration ──────────────────────────────────────────────────────────────────

def migrate(project=None):
    cfg = project_cfg(project)
    src = cfg["source_md"]
    id_re = cfg["id_regex"]
    # Custom-Projekte ohne source_md: leeres Board, nur Schema anlegen + zurück
    if src is None:
        conn = connect(project)
        conn.commit(); conn.close()
        print(f"✓ [{project}] leeres Custom-Board angelegt (kein Markdown-Import)")
        return
    if not src.exists():
        print(f"× Source-MD fehlt: {src}"); return
    text = src.read_text()
    conn = connect(project)
    cur = conn.cursor()
    sections = _split_tickets(text, id_re)
    now = datetime.now().isoformat(timespec='seconds')
    inserted = updated = 0
    for tid, content in sections.items():
        first = content.split('\n', 1)[0]
        meta = _header_meta(first, id_re)
        if not meta:
            continue
        _id, full_title, created, prio = meta
        if not created: created = date_today_iso()
        cmeta = _counter_meta(content)
        phase_from_s1 = _parse_phase_status(content)
        status = phase_from_s1 or cmeta.get("phase") or "open"
        # nur Lifecycle erlauben — andere → 'open'
        if status not in ("open","progress","deployed","reopened","parked","closed"):
            status = "open"
        ttype = _ticket_type(full_title, cmeta.get("phase",""))
        title = _strip_prefix(full_title)
        # Tag aus COUNTER-AUTO
        tag_m = re.search(r"Tag=`([^`]+)`", content)
        tag = tag_m.group(1) if tag_m else None
        # Letzte-Reset
        lr = re.search(r"Last-Reset=(\d{4}-\d{2}-\d{2})", content)
        last_reset = lr.group(1) if lr else None

        # Upsert
        exists = cur.execute("SELECT id FROM tickets WHERE id=?", (tid,)).fetchone()
        if exists:
            cur.execute("""UPDATE tickets SET type=?, status=?, tag=?, prio=?,
                title=?, content_md=?, created_date=?, last_reset=?,
                tests_total=?, tests_passed=?, iter_count=?, updated_at=?
                WHERE id=?""",
                (ttype, status, tag, prio, title, content, created, last_reset,
                 cmeta.get("tests_total",0), cmeta.get("tests_passed",0),
                 cmeta.get("iter_count",0), now, tid))
            updated += 1
        else:
            cur.execute("""INSERT INTO tickets
                (id, type, status, tag, prio, title, content_md, created_date,
                 last_reset, tests_total, tests_passed, iter_count, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (tid, ttype, status, tag, prio, title, content, created, last_reset,
                 cmeta.get("tests_total",0), cmeta.get("tests_passed",0),
                 cmeta.get("iter_count",0), now))
            inserted += 1
        # Iterations
        cur.execute("DELETE FROM iterations WHERE ticket_id=?", (tid,))
        for (num, dt, kind, label, dep) in _parse_iters(content):
            cur.execute("""INSERT INTO iterations
                (ticket_id, iter_num, deploy_date, kind, label, deployed)
                VALUES (?,?,?,?,?,?)""", (tid, num, dt, kind, label, dep))
    # Intrinsische Links (projekt-eigenes ID-Regex)
    intrinsic_re = re.compile(id_re)
    n_links = 0
    for tid, content in sections.items():
        for (s, d, ltype) in _extract_intrinsic_links_re(tid, content, intrinsic_re):
            if cur.execute("SELECT 1 FROM tickets WHERE id=?", (d,)).fetchone():
                cur.execute("INSERT OR IGNORE INTO links (src,dst,type) VALUES (?,?,?)", (s,d,ltype))
                if cur.rowcount: n_links += 1
    cur.execute("INSERT OR REPLACE INTO meta (key,value) VALUES ('last_migration',?)", (now,))
    conn.commit()
    n_tickets = cur.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    n_iters   = cur.execute("SELECT COUNT(*) FROM iterations").fetchone()[0]
    n_links_total = cur.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    conn.close()
    n_wt = import_worktrees(project)
    print(f"✓ [{project or DEFAULT_PROJECT}] Migration ok: {inserted} neu · {updated} aktualisiert · "
          f"{n_tickets} tickets · {n_iters} iters · {n_links} neue Links ({n_links_total} total) · "
          f"{n_wt} Worktrees zugeordnet")

def reset(project=None):
    cfg = project_cfg(project)
    if cfg["db"].exists():
        cfg["db"].unlink()
        print(f"× DB gelöscht: {cfg['db']}")
    migrate(project)

def dump(project=None):
    conn = connect(project)
    rows = [dict(r) for r in conn.execute("SELECT * FROM tickets ORDER BY id")]
    print(json.dumps(rows[:3], indent=2, ensure_ascii=False))
    print(f"... ({len(rows)} tickets total)")
    conn.close()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["migrate","reset","dump","worktrees","list-projects"], default="migrate", nargs="?")
    ap.add_argument("--project", default=None, help=f"Projekt (default {DEFAULT_PROJECT}; --project=all = alle migrieren)")
    args = ap.parse_args()
    if args.cmd == "list-projects":
        for k, v in PROJECTS.items():
            print(f"  {k:<14}  {v['name']:<30}  → {v['source_md']}")
    elif args.project == "all":
        for p in PROJECTS:
            {"migrate": migrate, "reset": reset, "dump": dump, "worktrees": import_worktrees}[args.cmd](p)
    else:
        if args.cmd == "worktrees":
            n = import_worktrees(args.project)
            print(f"✓ [{args.project or DEFAULT_PROJECT}] {n} Worktrees zugeordnet")
        else:
            {"migrate": migrate, "reset": reset, "dump": dump}[args.cmd](args.project)
