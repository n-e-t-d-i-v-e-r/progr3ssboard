#!/bin/bash
# Generiert docs/screenshots/board.png aus dem Default-Board.
# Nimmt frische DB an — wenn leer, vorher seed-demo.sh laufen lassen.

cd "$(dirname "$0")/.."
mkdir -p docs/screenshots
PORT="${1:-8767}"

# Plattform-spezifischer Chrome-Pfad
CHROME=""
for p in \
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  "/Applications/Chromium.app/Contents/MacOS/Chromium" \
  "$(command -v google-chrome 2>/dev/null)" \
  "$(command -v chromium 2>/dev/null)"
do
  if [ -n "$p" ] && [ -x "$p" ]; then CHROME="$p"; break; fi
done
[ -z "$CHROME" ] && { echo "× kein Chrome/Chromium gefunden"; exit 1; }

# Server-Check
if ! curl -sf -o /dev/null "http://localhost:$PORT/"; then
  echo "× kein Server auf :$PORT — erst 'python3 progr3ssboard.py --serve --port $PORT' starten"
  exit 1
fi

"$CHROME" --headless --disable-gpu --hide-scrollbars \
  --window-size=2000,1100 \
  --screenshot=docs/screenshots/board.png \
  "http://localhost:$PORT/" 2>&1 | grep -E "bytes|error" | head -3
echo "✓ docs/screenshots/board.png — $(ls -lh docs/screenshots/board.png | awk '{print $5}')"
