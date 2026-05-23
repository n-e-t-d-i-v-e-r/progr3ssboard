#!/usr/bin/env python3
"""
record-gif.py — generiert docs/screenshots/board-demo.gif via playwright.

Sequenz:
  1. Server muss auf 8767 laufen (z.B. `python3 progr3ssboard.py --serve --port 8767`)
  2. Skript seedet 6 generische Demo-Tickets in ein leeres Default-Board
  3. Chromium-Browser öffnet das Board, scripted Sequence (~10s):
     drag B-2 von NEU → IN ARBEIT → Modal öffnen → schließen
  4. playwright zeichnet alles als WebM-Video auf
  5. ffmpeg konvertiert WebM → GIF (palettegen-Trick für gute Qualität)
  6. Output: docs/screenshots/board-demo.gif

Usage (im venv):
  ~/projects/progr3ssboard/.venv/bin/python scripts/record-gif.py
"""
import json, subprocess, sys, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8767
BASE = f"http://localhost:{PORT}"
OUT_DIR = ROOT / "docs/screenshots"
TMP_DIR = ROOT / ".gif-tmp"
TMP_DIR.mkdir(exist_ok=True)

# ─── Vorbereitung: Server live + leere DB ─────────────────────────────────────

def _api(method, path, body=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or b"null")

def reset_and_seed():
    """Existing DB löschen + 6 Demo-Tickets seeden, dann Server-Reload erzwingen."""
    # DB wegputzen indem wir das default-board-Verzeichnis löschen geht nur OHNE Server
    # → wir rufen stattdessen alle Tickets ab und löschen sie via API
    try:
        existing = _api("GET", "/api/tickets")
        for t in existing:
            _api("DELETE", f"/api/tickets/{t['id']}")
        print(f"  cleared {len(existing)} existing tickets")
    except Exception as e:
        print(f"  ⚠ cleanup failed ({e}) — server up?")
        sys.exit(1)

    seeds = [
        ("open",     "BUG",     "api",      "Login redirect loops after token expiry"),
        ("open",     "FEATURE", "feature",  "Dark mode toggle in user settings"),
        ("open",     "BUG",     "api",      "Rate limit returns 200 instead of 429"),
        ("progress", "FEATURE", "backend",  "Replace requests with httpx for async support"),
        ("parked",   "FEATURE", "perf",     "Profile dashboard render with 10k items"),
        ("closed",   "BUG",     "security", "XSS in markdown rendering"),
    ]
    for status, ttype, tag, title in seeds:
        _api("POST", "/api/tickets", {
            "status": status, "type": ttype, "tag": tag, "prio": "P2", "title": title,
        })
    print(f"  seeded {len(seeds)} tickets")

# ─── Mouse-Cursor-Overlay (playwright zeichnet keinen) ────────────────────────

CURSOR_SCRIPT = r"""
() => {
  const c = document.createElement('div');
  c.id = '__demo_cursor';
  c.style.cssText = `
    position: fixed; left: -100px; top: -100px;
    width: 18px; height: 18px;
    border-radius: 50%;
    background: rgba(255, 153, 51, 0.92);
    box-shadow: 0 0 0 3px rgba(255, 153, 51, 0.25), 0 1px 6px rgba(0,0,0,0.5);
    pointer-events: none; z-index: 99999;
    transform: translate(-50%, -50%);
    transition: transform 0.08s linear;
  `;
  document.body.appendChild(c);
  document.addEventListener('mousemove', e => {
    c.style.left = e.clientX + 'px';
    c.style.top  = e.clientY + 'px';
  }, true);
  document.addEventListener('mousedown', () => c.style.transform = 'translate(-50%,-50%) scale(0.7)', true);
  document.addEventListener('mouseup',   () => c.style.transform = 'translate(-50%,-50%) scale(1)',   true);
}
"""

# ─── Hauptsequenz ─────────────────────────────────────────────────────────────

def main():
    print(f"=== Reset + Seed (Server: {BASE}) ===")
    reset_and_seed()

    print(f"\n=== Recording WebM via playwright ===")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 900},
            record_video_dir=str(TMP_DIR),
            record_video_size={"width": 1600, "height": 900},
        )
        page = ctx.new_page()
        page.goto(BASE, wait_until="networkidle")
        page.evaluate(CURSOR_SCRIPT)
        page.wait_for_selector(".card[data-id='B-2']", timeout=5000)

        # Schritt 1 — Initial-Pose
        time.sleep(0.7)
        page.mouse.move(800, 100, steps=15)
        time.sleep(0.3)

        # Schritt 2 — Drag B-2 (FEATURE "Dark mode") von NEU → IN ARBEIT
        card = page.locator(".card[data-id='B-2']")
        src_box = card.bounding_box()
        col_in_arbeit = page.locator(".column[data-col='in_arbeit']")
        dst_box = col_in_arbeit.bounding_box()

        sx = src_box["x"] + src_box["width"] / 2
        sy = src_box["y"] + src_box["height"] / 2
        dx = dst_box["x"] + dst_box["width"] / 2
        dy = dst_box["y"] + 180

        page.mouse.move(sx, sy, steps=20)
        time.sleep(0.4)
        page.mouse.down()
        time.sleep(0.2)
        # 3-Schritt-Bewegung damit der Drop-Indicator-Effekt sichtbar wird
        page.mouse.move(sx + 200, sy, steps=10); time.sleep(0.15)
        page.mouse.move((sx + dx) / 2, (sy + dy) / 2, steps=10); time.sleep(0.15)
        page.mouse.move(dx, dy, steps=15); time.sleep(0.35)
        page.mouse.up()
        # nach dem Drop reloaded der Code die Seite → kurz neu auf cursor warten
        page.wait_for_load_state("networkidle")
        page.evaluate(CURSOR_SCRIPT)
        time.sleep(1.0)

        # Schritt 3 — Modal öffnen (Klick auf B-2 jetzt in IN ARBEIT)
        page.wait_for_selector(".card[data-id='B-2']", timeout=5000)
        card = page.locator(".card[data-id='B-2']")
        cb = card.bounding_box()
        cx, cy = cb["x"] + cb["width"] / 2, cb["y"] + cb["height"] / 2
        page.mouse.move(cx, cy, steps=15)
        time.sleep(0.3)
        page.mouse.click(cx, cy)
        time.sleep(1.6)  # Modal-Inhalt lesen

        # Schritt 4 — Modal mit ESC schließen
        page.keyboard.press("Escape")
        time.sleep(0.8)

        ctx.close()
        browser.close()

    # Finde das aufgenommene Video
    videos = sorted(TMP_DIR.glob("*.webm"))
    if not videos:
        print("✗ kein WebM-Video gefunden"); sys.exit(1)
    video = videos[-1]
    print(f"  WebM: {video.name} ({video.stat().st_size // 1024} KB)")

    # ─── WebM → GIF via ffmpeg (palettegen-Trick) ────────────────────────────
    print(f"\n=== WebM → GIF (ffmpeg palettegen) ===")
    palette = TMP_DIR / "palette.png"
    gif_out = OUT_DIR / "board-demo.gif"

    # Step a) palette generieren
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video),
        "-vf", "fps=12,scale=1200:-1:flags=lanczos,palettegen=max_colors=128",
        str(palette),
    ], check=True, capture_output=True)

    # Step b) GIF mit Palette rendern
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video), "-i", str(palette),
        "-lavfi", "fps=12,scale=1200:-1:flags=lanczos[v];[v][1:v]paletteuse=dither=sierra2_4a",
        str(gif_out),
    ], check=True, capture_output=True)

    print(f"  GIF:  {gif_out.relative_to(ROOT)} ({gif_out.stat().st_size // 1024} KB)")

    # Cleanup tmp (Video + palette behalten falls Tuning nötig — löschen via `rm -rf .gif-tmp`)
    print(f"\n✓ done. tmp files in .gif-tmp/ (manuell aufräumen)")

if __name__ == "__main__":
    main()
