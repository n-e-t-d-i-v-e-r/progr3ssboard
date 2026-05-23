#!/bin/bash
# Pre-Push-Audit für progr3ssboard OSS-Repo.
# Exit 0 = bereit zum Push. Exit non-zero = blockierende Findings.
# Wichtig: scripts/ wird excluded weil dieses Skript selbst die Brand-Liste enthält
# (Self-Reference). Edits an scripts/ bitte vorher manuell auditieren.

set -e
cd "$(dirname "$0")/.."

EX="--exclude-dir=.git --exclude-dir=boards --exclude-dir=scripts --exclude-dir=.venv --exclude-dir=.gif-tmp"

# Brand-Liste in separater Variable damit grep sie nicht in sich selbst findet
BRANDS='jira|trello|atlassian|confluence|bitbucket|linear[ -]app|notion app|asana|monday\.com|clickup|gitlab|gitea|copilot|jetbrains|intellij|cursor-ide|cursor\.ai|cursor\.com|anysphere'
LEAKS='BACKLOG|boards|workspace'

echo "=== 1) Trademark-Scan ==="
hits=$(grep -rniE "$BRANDS" . $EX 2>/dev/null | wc -l | tr -d ' ')
if [ "$hits" -gt 0 ]; then
  echo "  ⚠ $hits Treffer:"; grep -rniE "$BRANDS" . $EX 2>/dev/null | head -10
  exit 1
fi
echo "  ✓ 0 Treffer"

echo ""
echo "=== 2) Personal/Project-Leak-Scan ==="
hits=$(grep -rniE "$LEAKS" . $EX 2>/dev/null | wc -l | tr -d ' ')
if [ "$hits" -gt 0 ]; then
  echo "  ⚠ $hits Treffer:"; grep -rniE "$LEAKS" . $EX 2>/dev/null
  exit 1
fi
echo "  ✓ 0 Treffer"

echo ""
echo "=== 3) Syntax-Smoke ==="
python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('p3','./progr3ssboard.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print('  ✓ progr3ssboard.py loads · PROJECTS:', list(m.PROJECTS))
"

echo ""
echo "=== 4) README via GitHub-Renderer ==="
if command -v gh >/dev/null; then
  rendered=$(jq -Rs '{text: .}' < README.md | gh api -X POST markdown --input - 2>/dev/null)
  hits=$(echo "$rendered" | grep -ciE "$BRANDS" || true)
  if [ "${hits:-0}" -gt 0 ]; then
    echo "  ⚠ $hits Treffer im gerenderten HTML"; exit 1
  fi
  echo "  ✓ rendered README clean"
else
  echo "  ◦ gh CLI fehlt — Render-Check übersprungen"
fi

echo ""
echo "✓ ALLE CHECKS GRÜN — bereit zum Push."
