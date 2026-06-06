#!/bin/bash
# Seeds 11 realistische Demo-Tickets ins lokale 'default'-Board (idempotent: clear-first).
# Zeigt alle Status-Spalten, Tests-Balken, Iterationen, Epic-Hierarchie und Links —
# damit die OSS-Showcase-Instanz die Features auf einen Blick demonstriert.
# Nimmt an: Server läuft bereits auf $PORT (default 8767). Server liest die DB live.
# Verwendet von /p3b:run, /p3b:screenshot und vom Bitpartikel-Onboard (Auto-Seed wenn leer).

PORT="${1:-8767}"
BASE="http://localhost:$PORT"

if ! curl -sf -o /dev/null "$BASE/"; then
  echo "× Server unter $BASE nicht erreichbar — erst 'python3 progr3ssboard.py --serve --port $PORT' starten"
  exit 1
fi

python3 - <<'PY'
import sys, importlib.util
sys.path.insert(0, '.')
spec = importlib.util.spec_from_file_location("bdb", "./board-db.py")
bdb = importlib.util.module_from_spec(spec); spec.loader.exec_module(bdb)
from datetime import date, timedelta
today = date.today()

def ago(d):  # ISO-Datum vor d Tagen
    return (today - timedelta(days=d)).isoformat()

def body(tid, title, prio, sections):
    out = [f"## {tid} — {title} ({today.isoformat()}) — {prio}\n"]
    for i, (name, txt) in enumerate(sections, 1):
        out.append(f"\n### §{i} {name}\n{txt.strip()}\n")
    return "".join(out)

# id, type, status, tag, prio, title, tests_total, tests_passed, iter_count, parent, sections
TICKETS = [
    ("B-1","BUG","open","api","P1","Login redirect loops after token expiry",0,0,0,None,[
        ("Symptom","Nach Ablauf des Access-Tokens landet der User in einer Endlos-Schleife `/login → /dashboard → /login`. Betrifft nur Sessions älter als 1 h."),
        ("Repro","1. Einloggen, 60 min warten (oder `exp` manuell vorziehen).\n2. Geschützten Link öffnen.\n3. Browser pingpongt zwischen Login und Dashboard."),
        ("Hypothese","Refresh-Token wird VOR dem Redirect gelesen, aber der 401-Interceptor feuert den Redirect erneut, bevor `/auth/refresh` zurückkommt — Race zwischen Guard und Interceptor."),
    ]),
    ("B-2","FEATURE","progress","feature","P2","Dark mode toggle in user settings",5,3,2,None,[
        ("Ziel","Theme-Umschalter (Hell/Dunkel/System) in den Settings; Wahl persistiert pro User und respektiert `prefers-color-scheme` als Default."),
        ("Umfang","CSS-Custom-Properties als Theme-Tokens · Toggle-Komponente · Persistenz in `user_prefs` · SSR-flashfreies Initial-Theme via Inline-Script."),
        ("Stand","Tokens + Toggle stehen, Persistenz verdrahtet. Offen: FOUC beim ersten Paint, E2E auf Safari."),
    ]),
    ("B-3","BUG","open","api","P2","Rate limit returns 200 instead of 429",0,0,0,None,[
        ("Symptom","Über das Limit hinaus liefert `/api/*` weiter `200 OK` statt `429` — Clients merken die Begrenzung nicht und retryen nicht korrekt."),
        ("Befund","Das Limiter-Middleware-Ergebnis wird geloggt, aber der Early-Return fehlt — der Handler läuft trotzdem durch."),
        ("Akzeptanz","Bei Überschreitung: Status 429 + Header `Retry-After` + JSON-Body `{error:\"rate_limited\"}`."),
    ]),
    ("B-4","FEATURE","progress","backend","P2","Replace requests with httpx for async support",8,6,1,"B-9",[
        ("Ziel","Synchrones `requests` durch `httpx.AsyncClient` ersetzen, damit Outbound-Calls den Event-Loop nicht blockieren."),
        ("Umfang","Connection-Pooling über einen geteilten Client · Timeouts/Retries zentralisieren · Test-Doubles via `httpx.MockTransport`."),
        ("Stand","Client-Wrapper + 6/8 Call-Sites migriert. Offen: Streaming-Downloads, Retry-Backoff-Policy."),
    ]),
    ("B-5","FEATURE","parked","perf","P3","Profile dashboard render with 10k items",0,0,0,None,[
        ("Ziel","Dashboard-Liste muss 10 000 Einträge flüssig rendern (aktuell >2 s Jank ab ~2 000)."),
        ("Optionen","Virtualisiertes Scrolling (windowing) vs. server-seitige Pagination + Cursor."),
        ("Warum geparkt","Wartet auf Produkt-Entscheid, ob die volle Liste überhaupt ein Anforderungsfall ist."),
    ]),
    ("B-6","BUG","closed","security","P1","XSS in markdown rendering",6,6,1,None,[
        ("Symptom","User-Markdown mit `<img src=x onerror=...>` führte im Kommentar-Render Skript aus (stored XSS)."),
        ("Fix","Sanitizer (Allowlist) NACH der Markdown-zu-HTML-Wandlung; `javascript:`-URLs und Event-Handler entfernt; Regressions-Test mit 12 Payloads."),
        ("Closure","Deployed + verifiziert; Payload-Suite grün (6/6). Kein Reopen seit Release."),
    ]),
    ("B-7","FEATURE","deployed","infra","P3","Set up GitHub Actions CI pipeline",4,4,1,None,[
        ("Ziel","CI auf push/PR: pytest + ruff, Status-Badges im README."),
        ("Stand","Deployed; Workflow grün (4/4 Jobs). Wartet auf Prod-Promotion / Branch-Protection-Rollout."),
    ]),
    ("B-8","BUG","reopened","api","P2","CSV export drops UTF-8 BOM",3,1,2,None,[
        ("Symptom","Excel öffnet den CSV-Export mit zerschossenen Umlauten — fehlendes BOM."),
        ("Verlauf","Iter-1 setzte BOM → schloss das Ticket. Reopened: bei großen Exports (Streaming >50 MB) fehlt das BOM erneut."),
        ("Nächster Schritt","BOM einmalig im Stream-Header schreiben, nicht pro Chunk."),
    ]),
    ("B-9","FEATURE","progress","backend","P2","EPIC: Async I/O migration",0,0,0,None,[
        ("Ziel","Alle blockierenden I/O-Pfade auf async umstellen — Voraussetzung für höhere Concurrency ohne Thread-Pool-Aufblähung."),
        ("Kinder","Untertasks gruppieren die Migration pro Subsystem (HTTP-Client zuerst, dann DB-Layer)."),
    ]),
    ("B-10","BUG","change-request","backend","P2","DB layer holds connection across await",0,0,0,"B-9",[
        ("Befund","Im neuen async-Pfad wird eine DB-Connection über ein `await` gehalten → Pool-Starvation unter Last."),
        ("Change-Request","Review verlangt: Connection per Request scopen (Context-Manager), nicht über Suspend-Punkte tragen."),
    ]),
    ("B-11","BUG","deployed","database","P3","Slow query on user_activity index",5,5,1,None,[
        ("Symptom","p95 der Activity-Abfrage bei 2,1 s — fehlender Covering-Index."),
        ("Fix","Index auf `(user_id, ts)` ergänzt; p95 fällt auf 80 ms. Tests 5/5."),
        ("Stand","Deployed in Staging, frisch (3 Tage) → im TESTING-Fenster."),
    ]),
]

# ticket_id, iter_num, deploy_age (Tage; None = nicht deployed), kind, label, deployed
ITERS = [
    ("B-2", 1, 2,    "fix",   "Toggle + Tokens", 1),
    ("B-2", 2, None, "fix",   "FOUC-Fix (in Arbeit)", 0),
    ("B-4", 1, 5,    "fix",   "Client-Wrapper + 6 Call-Sites", 1),
    ("B-6", 1, 12,   "fix",   "Sanitizer-Allowlist", 1),
    ("B-7", 1, 10,   "fix",   "CI-Workflow + Badges", 1),
    ("B-8", 1, 6,    "fix",   "BOM im Header", 1),
    ("B-8", 2, None, "recur", "Regression im Streaming-Pfad", 0),
    ("B-11",1, 3,    "fix",   "Covering-Index (user_id, ts)", 1),
]

# src, dst, type
LINKS = [
    ("B-1",  "B-3", "relates-to"),
    ("B-10", "B-4", "blocks"),
]

conn = bdb.connect(project='default')
conn.execute("PRAGMA foreign_keys=ON")
conn.execute("DELETE FROM iterations")
conn.execute("DELETE FROM links")
conn.execute("DELETE FROM tickets")

# Pass 1: Tickets ohne parent_id (FK-sicher, Reihenfolge egal)
for (tid,typ,st,tag,prio,title,tt,tp,ic,parent,secs) in TICKETS:
    conn.execute("""INSERT INTO tickets
        (id,type,status,tag,prio,title,content_md,created_date,last_reset,
         parent_id,tests_total,tests_passed,iter_count,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tid,typ,st,tag,prio,title,body(tid,title,prio,secs),today.isoformat(),today.isoformat(),
         None,tt,tp,ic,today.isoformat()+"T12:00:00"))
# Pass 2: parent_id nachtragen
for (tid,typ,st,tag,prio,title,tt,tp,ic,parent,secs) in TICKETS:
    if parent:
        conn.execute("UPDATE tickets SET parent_id=? WHERE id=?", (parent, tid))

for (tk,num,age,kind,label,dep) in ITERS:
    dd = ago(age) if age is not None else None
    conn.execute("INSERT INTO iterations (ticket_id,iter_num,deploy_date,kind,label,deployed) VALUES (?,?,?,?,?,?)",
                 (tk,num,dd,kind,label,dep))
for (s,d,t) in LINKS:
    conn.execute("INSERT INTO links (src,dst,type) VALUES (?,?,?)", (s,d,t))

conn.commit()
nt = conn.execute("SELECT count(*) FROM tickets").fetchone()[0]
ni = conn.execute("SELECT count(*) FROM iterations").fetchone()[0]
nl = conn.execute("SELECT count(*) FROM links").fetchone()[0]
conn.close()
print(f"  + {nt} tickets ·  {ni} iterations ·  {nl} links")
PY

echo ""
echo "✓ Demo-Board 'default' geseedet (alle Status-Spalten · Tests · Iterationen · Epic-Hierarchie · Links)"
