#!/bin/bash
# Seeds 11 generische Demo-Tickets ins lokale Default-Board.
# Nimmt an: Server läuft bereits auf $PORT (default 8767).
# Verwendet von /p3b:run und /p3b:screenshot.

PORT="${1:-8767}"
BASE="http://localhost:$PORT"

if ! curl -sf -o /dev/null "$BASE/"; then
  echo "× Server unter $BASE nicht erreichbar — erst 'python3 progr3ssboard.py --serve --port $PORT' starten"
  exit 1
fi

seed() {
  curl -s -X POST "$BASE/api/tickets" \
    -H 'Content-Type: application/json' \
    -d "{\"status\":\"$1\",\"type\":\"$2\",\"tag\":\"$3\",\"prio\":\"P2\",\"title\":\"$4\"}" > /dev/null
  echo "  + [$1/$2/$3] $4"
}

seed open      BUG     bug      "Login redirect loops after token expiry"
seed open      FEATURE feature  "Dark mode toggle in user settings"
seed open      BUG     api      "Rate limit returns 200 instead of 429"
seed progress  FEATURE backend  "Replace requests with httpx for async support"
seed progress  BUG     ui       "Mobile breakpoint: sidebar overlaps content"
seed deployed  BUG     database "Slow query on user_activity index"
seed deployed  FEATURE infra    "Set up GitHub Actions CI pipeline"
seed parked    FEATURE perf     "Profile dashboard render with 10k items"
seed parked    SPEC    docs     "Onboarding guide for new contributors"
seed closed    BUG     security "XSS in markdown rendering (sanitize-html)"
seed closed    REBUILD refactor "Migrate from print-based to logging module"

# Iterationen für die zwei deployed-Tickets (B-6 → TESTING 3d, B-7 → READY 10d)
python3 - <<PY
import sys, importlib.util
sys.path.insert(0, '.')
spec = importlib.util.spec_from_file_location("bdb","./board-db.py")
bdb = importlib.util.module_from_spec(spec); spec.loader.exec_module(bdb)
conn = bdb.connect(project='default')
from datetime import date, timedelta
today = date.today()
data = [
    ('B-6', 1, (today - timedelta(days=3)).isoformat(), 'fix',
     'Added covering index on (user_id, ts) — drops p95 from 2.1s to 80ms'),
    ('B-7', 1, (today - timedelta(days=10)).isoformat(), 'fix',
     'GitHub Actions workflow: pytest + ruff on push/PR, badges in README'),
]
for tid, n, dd, kind, label in data:
    conn.execute("INSERT INTO iterations (ticket_id, iter_num, deploy_date, kind, label) VALUES (?,?,?,?,?)",
                 (tid, n, dd, kind, label))
    conn.execute("UPDATE tickets SET iter_count=? WHERE id=?", (n, tid))
conn.commit(); conn.close()
print('  + 2 iterations (B-6 testing/3d, B-7 ready/10d)')
PY

echo ""
echo "✓ 11 demo tickets + 2 iterations seeded into 'default' board"
