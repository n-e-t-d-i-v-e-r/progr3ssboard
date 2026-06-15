#!/usr/bin/env python3
"""backlog-html.py — BACKLOG-Katalog (multi-project, horizontal, Architektur-Hierarchie).

Teil von P3B-10 (Multi-Project Board-Refactor v0.2.0).

Datenquelle: boards/<project>/board.db (SQLite-CRUD)
Layout: horizontal-Sektionen (7 Bereiche + Bug-Block zuunderst)
Filter: status ∈ {open, change-request}; aktive Tickets (progress/deployed/parked) gehen ins PROGR3SSBOARD

Usage:
  python3 backlog-html.py                       → schreibt backlog.html (static)
  python3 backlog-html.py --serve                → localhost:8767, regeneriert per Request
  python3 backlog-html.py --serve --port N      → custom port
  python3 backlog-html.py --project NAME          → Projekt auswählen (default: default)

Top-Nav: ‹ BACKLOG · PROGR3SSBOARD ›  + Project-Switcher
"""
import sys, os, json, html, argparse, sqlite3, importlib.util
from pathlib import Path
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# Module-Imports
HERE = Path(__file__).resolve().parent

# Layout-robust: REPO = das naechste Verzeichnis aufwaerts, das ein 'boards/' enthaelt.
# Funktioniert in JEDEM Deployment-Layout (OSS: backlog-html.py im Repo-Root neben boards/;
# Hub-Deploy: scripts/backlog-html.py mit boards/ eine Ebene hoeher) — kein fragiles parent-Counting,
# das beim Re-Deploy in einen anderen Verzeichnis-Layout still bricht.
def _resolve_repo(start):
    p = start
    while True:
        if (p / "boards").is_dir():
            return p
        if p == p.parent:        # Filesystem-Root erreicht, nichts gefunden
            return start         # Fallback: Repo-Root-Layout (boards/ als Geschwister)
        p = p.parent
REPO = _resolve_repo(HERE)

# board_main_tag.py (SSoT für main:-Schema)
sys.path.insert(0, str(HERE))
from board_main_tag import (
    MAIN_TAG_PILL_COLOR, BACKLOG_SECTIONS,
    parse_main_tag, infer_main_tag, assign_backlog_section,
    main_tag_pill_html, main_tag_border_color,
)

# board-db.py (CRUD-Layer)
_spec = importlib.util.spec_from_file_location("board_db", HERE / "board-db.py")
_bdb = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_bdb)

# --- Project Config ----------------------------------------------------------
BOARDS_DIR = REPO / "boards"

def list_projects():
    """List available board projects (= sub-dirs of BOARDS_DIR with board.db)."""
    if not BOARDS_DIR.exists(): return []
    return sorted([p.name for p in BOARDS_DIR.iterdir() if p.is_dir() and (p / "board.db").exists()])

def db_path(project):
    return BOARDS_DIR / project / "board.db"

# --- Data Load ---------------------------------------------------------------

# BACKLOG-relevante Stati
BACKLOG_STATUS = {"open", "change-request"}

def load_backlog_tickets(project):
    """Liest BACKLOG-relevante Tickets aus board.db.

    Cluster-Filter:
    - status='change-request' → IMMER sichtbar (User-Spec: unbearbeiteter change-request = BACKLOG)
    - status='open' + Sekundär-Tag deckt sich mit aktivem Cluster (progress/deployed/reopened)
      → AUSGEBLENDET (Ticket ist effektiv Iteration eines aktiven Cluster-Members)
    - status='open' + solo (kein aktiver Cluster) → sichtbar
    """
    db = db_path(project)
    if not db.exists(): return []
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        # Alle Tickets laden für Cluster-Analyse
        all_rows = c.execute("""
            SELECT id, type, status, tag, prio, title, content_md, created_date,
                   last_reset, iter_count, tests_total, tests_passed, parent_id, updated_at
            FROM tickets
            ORDER BY id
        """).fetchall()
    all_tickets = [dict(r) for r in all_rows]

    # Aktive Cluster identifizieren
    active_secondary_tags = set()
    for t in all_tickets:
        if t['status'] in ('progress', 'deployed', 'reopened'):
            _, sec, _ = parse_main_tag(t.get('tag'))
            if sec:
                active_secondary_tags.add(sec)

    # Filter anwenden
    result = []
    for t in all_tickets:
        if t['status'] not in BACKLOG_STATUS:
            continue
        if t['status'] == 'change-request':
            result.append(t)  # immer sichtbar
            continue
        # status == 'open': Cluster-Filter
        _, sec, _ = parse_main_tag(t.get('tag'))
        if sec and sec in active_secondary_tags:
            continue  # Iteration eines aktiven Clusters, ausblenden
        result.append(t)
    return result

def load_iterations(project):
    """Returns dict ticket_id → list of iteration-dicts."""
    db = db_path(project)
    if not db.exists(): return {}
    out = {}
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        for r in c.execute("SELECT * FROM iterations ORDER BY ticket_id, iter_num"):
            d = dict(r)
            out.setdefault(d['ticket_id'], []).append(d)
    return out

def enrich_ticket(t, all_iters=None):
    """Adds main_tag, secondary_tag, section_key, days_open, iters."""
    main, secondary, _rest = parse_main_tag(t.get('tag'))
    if not main:
        main = infer_main_tag(t.get('title'), t.get('type'), t.get('tag'))
    t['main_tag'] = main
    t['secondary_tag'] = secondary or '—'
    t['section_key'] = assign_backlog_section(main, secondary, t.get('title'))
    t['iters'] = (all_iters or {}).get(t['id'], [])
    created = t.get('created_date') or t.get('last_reset')
    try:
        d = date.fromisoformat(created)
        t['days_open'] = (date.today() - d).days
    except Exception:
        t['days_open'] = 0
    return t

def group_by_section(tickets):
    """Gruppiert nach section_key, in BACKLOG_SECTIONS-Reihenfolge."""
    grouped = {key: [] for key, _, _ in BACKLOG_SECTIONS}
    for t in tickets:
        grouped.setdefault(t['section_key'], []).append(t)
    # innerhalb: sortiere nach days_open desc (älter oben), dann ID
    for k in grouped:
        grouped[k].sort(key=lambda x: (-x.get('days_open', 0), x.get('id', '')))
    return grouped

# --- Rendering ---------------------------------------------------------------

CSS = """
body { background: #1a1d24; color: #e0e0e0; font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; margin: 0; padding: 0; }
.topnav { background: #2c303a; padding: 12px 20px; border-bottom: 2px solid #3a3f4a; display: flex; align-items: center; gap: 20px; position: sticky; top: 0; z-index: 100; }
.brand { font-weight: 700; font-size: 16px; color: #fff; }
.brand-sub { color: #888; font-weight: 400; font-size: 13px; }
.topnav-spacer { flex: 1; }
.nav-link { padding: 6px 14px; border-radius: 6px; text-decoration: none; color: #aaa; font-size: 13px; font-weight: 600; }
.nav-link.active { background: #3498db; color: #fff; }
.nav-link:hover:not(.active) { background: #3a3f4a; color: #fff; }
.project-select { background: #1a1d24; color: #fff; border: 1px solid #3a3f4a; padding: 5px 10px; border-radius: 5px; font-size: 13px; }
.stats { color: #888; font-size: 12px; }
.container { padding: 20px 30px; max-width: 1400px; margin: 0 auto; }
.section { margin-bottom: 28px; }
.section-header { display: flex; align-items: baseline; gap: 12px; padding: 8px 4px; border-bottom: 2px solid #3a3f4a; margin-bottom: 12px; }
.section-title { font-size: 16px; font-weight: 700; color: #fff; }
.section-count { background: #3a3f4a; color: #aaa; padding: 2px 10px; border-radius: 10px; font-size: 11px; font-weight: 600; }
.section-desc { color: #777; font-size: 12px; flex: 1; }
.cards { display: flex; flex-wrap: wrap; gap: 10px; }
.card { background: #22262e; border-left: 4px solid #888; border-radius: 6px; padding: 10px 12px; min-width: 280px; max-width: 360px; flex: 1 1 280px; cursor: pointer; transition: transform 0.1s, box-shadow 0.1s; }
.card:hover { transform: translateY(-1px); box-shadow: 0 3px 8px rgba(0,0,0,0.35); }
.card-row1 { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; flex-wrap: wrap; }
.card-id { font-weight: 700; font-size: 12px; color: #ddd; min-width: 50px; }
.card-secondary-pill { background: #3a3f4a; color: #bbb; padding: 1px 7px; border-radius: 8px; font-size: 10px; }
.card-title { font-size: 13px; color: #eee; line-height: 1.35; margin-bottom: 6px; }
.card-meta { color: #888; font-size: 11px; display: flex; gap: 12px; flex-wrap: wrap; }
.empty { color: #555; font-size: 12px; padding: 16px 8px; font-style: italic; }
.card-iter-badge { background: #f1c40f; color: #000; padding: 1px 7px; border-radius: 8px; font-size: 10px; font-weight: 700; cursor: help; }
.bug-analysis-section { background: rgba(52,152,219,0.08); border-left: 3px solid #3498db; padding: 12px 14px; margin-bottom: 18px; border-radius: 4px; }
.iter-list-section { background: rgba(39,174,96,0.08); border-left: 3px solid #27ae60; padding: 12px 14px; border-radius: 4px; }
.iter-tabs { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.iter-tab-btn { background: #2c303a; color: #ddd; border: 1px solid #3a3f4a; padding: 5px 12px; border-radius: 4px; font-size: 11px; cursor: pointer; font-weight: 600; text-align: left; }
.iter-tab-btn:hover { background: #3a3f4a; color: #fff; }
.iter-tab-btn.active { background: #27ae60; color: #fff; border-color: #27ae60; }
.iter-content-area { margin-top: 12px; background: #1a1d24; padding: 14px; border-radius: 5px; border: 1px solid #2a2e36; line-height: 1.5; }
.modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 200; align-items: center; justify-content: center; }
.modal-overlay.active { display: flex; }
.modal { background: #22262e; border-radius: 8px; max-width: 900px; max-height: 85vh; width: 95%; display: flex; flex-direction: column; overflow: hidden; }
.modal-header { padding: 14px 20px; border-bottom: 1px solid #3a3f4a; display: flex; justify-content: space-between; align-items: center; }
.modal-body { padding: 16px 24px; overflow-y: auto; line-height: 1.5; color: #ccc; }
.modal-close { background: none; border: none; color: #aaa; font-size: 22px; cursor: pointer; }
.modal-body h1, .modal-body h2, .modal-body h3 { color: #fff; margin-top: 1em; }
.modal-body code { background: #1a1d24; padding: 2px 5px; border-radius: 3px; font-size: 12px; }
.modal-body pre { background: #1a1d24; padding: 10px; border-radius: 5px; overflow-x: auto; }
.footer { text-align: center; padding: 20px; color: #555; font-size: 11px; }
"""

def render_card(t):
    color, icon, label = MAIN_TAG_PILL_COLOR.get(t['main_tag'], MAIN_TAG_PILL_COLOR['general'])
    sec = html.escape(t['secondary_tag'] or '—')
    title = html.escape(t['title'] or '')
    tid = html.escape(t['id'])
    days = t.get('days_open', 0)
    iter_n = t.get('iter_count', 0)
    tests = f"{t.get('tests_passed', 0)}/{t.get('tests_total', 0)}"
    status = html.escape(t.get('status', '') or '')

    iter_badge = ""
    if t.get('iters'):
        n = len(t['iters'])
        iter_badge = f'<span class="card-iter-badge" title="{n} Iterations — klick für Detail im Modal">🔄 {n}</span>'

    return f"""
    <div class="card" style="border-left-color: {color}" onclick="openTicket('{tid}')">
      <div class="card-row1">
        <span class="card-id">{tid}</span>
        <span class="main-pill" style="background:{color};color:#fff;font-size:10px;padding:2px 8px;border-radius:10px;font-weight:700">{icon} {label}</span>
        <span class="card-secondary-pill">{sec}</span>
      </div>
      <div class="card-title">{title}</div>
      <div class="card-meta">
        <span>📅 {days}d</span>
        {iter_badge}
        <span>🧪 {tests}</span>
        <span>📍 {status}</span>
      </div>
    </div>
    """

def render_section(key, label, desc, tickets):
    if not tickets:
        cards_html = '<div class="empty">(keine Tickets)</div>'
    else:
        cards_html = "".join(render_card(t) for t in tickets)
    return f"""
    <div class="section" data-section="{key}">
      <div class="section-header">
        <span class="section-title">{html.escape(label)}</span>
        <span class="section-count">{len(tickets)}</span>
        <span class="section-desc">{html.escape(desc)}</span>
      </div>
      <div class="cards">{cards_html}</div>
    </div>
    """

def render_topnav(current_project, projects, total_count):
    options = "".join(
        f'<option value="{p}"{" selected" if p==current_project else ""}>{p}</option>'
        for p in projects
    )
    return f"""
    <div class="topnav">
      <span class="brand">progr3ssboard <span class="brand-sub">◷ BACKLOG</span></span>
      <select class="project-select" onchange="switchProject(this.value)">
        {options}
      </select>
      <span class="stats">{total_count} offen</span>
      <span class="topnav-spacer"></span>
      <a class="nav-link active" href="/backlog?project={current_project}">📋 BACKLOG</a>
      <a class="nav-link" href="/?project={current_project}">🛠 PROGR3SSBOARD</a>
    </div>
    """

def render_page(current_project, projects):
    tickets_raw = load_backlog_tickets(current_project)
    iters_by_tid = load_iterations(current_project)
    tickets = [enrich_ticket(t, iters_by_tid) for t in tickets_raw]
    grouped = group_by_section(tickets)

    sections_html = "\n".join(
        render_section(key, label, desc, grouped.get(key, []))
        for key, label, desc in BACKLOG_SECTIONS
    )

    # Embed ticket-Contents als JSON für Modal-Anzeige
    tickets_for_js = {
        t['id']: {
            'title': t.get('title') or '',
            'content_md': t.get('content_md') or '',
            'tag': t.get('tag') or '',
            'type': t.get('type') or '',
            'status': t.get('status') or '',
        }
        for t in tickets
    }
    js_tickets = json.dumps(tickets_for_js, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>progr3ssboard ◷ BACKLOG · {html.escape(current_project)}</title>
  <style>{CSS}</style>
</head>
<body>
  {render_topnav(current_project, projects, len(tickets))}
  <div class="container">
    {sections_html}
  </div>
  <div class="modal-overlay" id="modal-overlay" onclick="closeIfBackdrop(event)">
    <div class="modal">
      <div class="modal-header">
        <h3 id="modal-title">—</h3>
        <button class="modal-close" onclick="closeModal()">✕</button>
      </div>
      <div class="modal-body" id="modal-body"></div>
    </div>
  </div>
  <div class="footer">P3B-10 v0.2.0 · BACKLOG-Board · Datenquelle: boards/{html.escape(current_project)}/board.db</div>
<script>
const TICKETS = {js_tickets};
function _renderMd(md) {{
  if (!md) return '<p>(leer)</p>';
  let h = md.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  h = h.replace(/^### (.+)$/gm, '<h4>$1</h4>');
  h = h.replace(/^## (.+)$/gm, '<h3>$1</h3>');
  h = h.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
  h = h.replace(/`([^`]+)`/g, '<code>$1</code>');
  h = h.replace(/\\n/g, '<br>');
  return h;
}}
function _splitBugContent(md) {{
  if (!md) return {{analysis: '', iters: []}};
  const re = /^### (?:§\\d+[^\\n]*?)?Iter[\\-\\s]*(\\d+)[^\\n]*$/gm;
  const matches = [...md.matchAll(re)];
  if (!matches.length) return {{analysis: md, iters: []}};
  const analysis = md.slice(0, matches[0].index);
  const iters = matches.map((m, i) => ({{
    num: parseInt(m[1], 10),
    header: m[0].replace(/^###\\s*/, '').trim(),
    content: md.slice(m.index, (i+1 < matches.length) ? matches[i+1].index : md.length)
  }}));
  return {{analysis, iters}};
}}
window.__currentIters = [];
function showIter(num) {{
  const it = window.__currentIters.find(x => x.num === Number(num));
  if (!it) return;
  const area = document.getElementById('iter-content-area');
  area.style.display = 'block';
  area.innerHTML = _renderMd(it.content);
  document.querySelectorAll('.iter-tab-btn').forEach(b => b.classList.remove('active'));
  const btn = document.querySelector('.iter-tab-btn[data-iter="' + num + '"]');
  if (btn) btn.classList.add('active');
  area.scrollIntoView({{behavior:'smooth', block:'nearest'}});
}}
function openTicket(id) {{
  const t = TICKETS[id];
  if (!t) return;
  document.getElementById('modal-title').textContent = id + ' — ' + t.title;
  const {{analysis, iters}} = _splitBugContent(t.content_md || '');
  window.__currentIters = iters;
  const analysisHtml = _renderMd(analysis);
  const iterListHtml = iters.length ? (
    '<div class="iter-list-section">' +
    '<h3 style="color:#27ae60;margin-top:0">🔄 Iterations (' + iters.length + ')</h3>' +
    '<div class="iter-tabs">' +
    iters.map(it => '<button class="iter-tab-btn" data-iter="' + it.num + '" onclick="showIter(' + it.num + ')">' + it.header.substring(0,90).replace(/[<>&]/g, c => ({{'<':'&lt;','>':'&gt;','&':'&amp;'}})[c]) + '</button>').join('') +
    '</div>' +
    '<div id="iter-content-area" class="iter-content-area" style="display:none"></div>' +
    '</div>'
  ) : '<div style="color:#888;font-size:12px;font-style:italic;margin-top:14px">— Keine Iterations bisher (Iter-0/Analyse-Phase) —</div>';
  document.getElementById('modal-body').innerHTML =
    '<div class="bug-analysis-section">' +
    '<h3 style="color:#3498db;margin-top:0">📋 Bug-Analyse (Iter-0)</h3>' +
    analysisHtml +
    '</div>' +
    iterListHtml;
  document.getElementById('modal-overlay').classList.add('active');
}}
function closeModal() {{ document.getElementById('modal-overlay').classList.remove('active'); }}
function closeIfBackdrop(e) {{ if (e.target.id === 'modal-overlay') closeModal(); }}
function switchProject(p) {{
  const url = new URL(window.location);
  url.searchParams.set('project', p);
  window.location = url;
}}
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeModal(); }});
</script>
</body>
</html>"""

# --- HTTP Server -------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        view = qs.get('view', ['backlog'])[0]
        project = qs.get('project', [self.server.default_project])[0]

        if view == 'progress':
            # Forward to progr3ssboard
            self.send_response(302)
            self.send_header('Location', f'/progr3ssboard?project={project}')
            self.end_headers()
            return

        projects = list_projects()
        if project not in projects:
            project = projects[0] if projects else _bdb.DEFAULT_PROJECT
        try:
            html_out = render_page(project, projects)
        except Exception as e:
            html_out = f"<pre>Error: {e}</pre>"
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html_out.encode('utf-8'))

    def log_message(self, *a, **kw): pass

def serve(project, port):
    server = HTTPServer(('localhost', port), Handler)
    server.default_project = project
    print(f"backlog-html.py served on http://localhost:{port}/?project={project}")
    print("Ctrl+C to stop.")
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nStopped.")

# --- Main --------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--serve', action='store_true')
    ap.add_argument('--port', type=int, default=8767)
    ap.add_argument('--project', default=_bdb.DEFAULT_PROJECT)
    ap.add_argument('--out', default=str(REPO / 'backlog.html'))
    args = ap.parse_args()

    projects = list_projects()
    if args.project not in projects:
        print(f"Unknown project '{args.project}'. Available: {', '.join(projects)}")
        sys.exit(1)

    if args.serve:
        serve(args.project, args.port)
    else:
        html_out = render_page(args.project, projects)
        Path(args.out).write_text(html_out, encoding='utf-8')
        print(f"Wrote {args.out}")

if __name__ == '__main__':
    main()
