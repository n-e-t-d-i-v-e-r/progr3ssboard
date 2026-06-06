# Changelog

All notable changes to progr3ssboard are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/) and the project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-05-24

### Added
- **BACKLOG-Board** as a separate view (`backlog-html.py`): horizontal layout, seven topic areas + a "Bugs (open)" block at the bottom.
- **Top navigation toggle** `‹ BACKLOG · PROGR3SSBOARD ›` in both views.
- **`main:`-tag schema** (`board_main_tag.py`): ten universal main categories (`feature`, `bug`, `hotfix`, `refactor`, `ui`, `architecture`, `infra`, `docs`, `perf`, `general`) with consistent pill-colors per category. The first item in a ticket's tag string (e.g. `main: feature, ui, ...`) drives the card's border-color and the routing into the BACKLOG sub-sections.
- **New status `change-request`** in addition to `open/progress/deployed/reopened/parked/closed`. Tickets with `change-request` are accepted for the BACKLOG view alongside `open`.
- **Sub-route `/backlog`** integrated into the progr3ssboard HTTP server — single port, two views.
- **Inference fallback** for tickets without an explicit `main:`-prefix: `infer_main_tag(title, type, existing_tag)` derives a sensible category from title keywords + ticket type.
- **`--demo` instance flag**: shows a `DEMO` badge in the board header and `· DEMO` in the page title, to mark a public showcase instance (real boards stay unbadged).
- **Richer demo seed** (`scripts/seed-demo.sh`): re-seeds the default board (idempotent) with 11 realistic tickets — full Markdown bodies, test counts, iterations, an epic/sub-task hierarchy and links — covering all status columns.

### Changed
- **PROGR3SSBOARD column count: 6 → 5.** The leftmost `📥 NEU` column has been removed; tickets with `open` or `change-request` status now live exclusively in the BACKLOG view. The remaining columns are `🛠 IN ARBEIT · 🧪 TESTING · ✅ READY · ⏸ PARKED · 🗄 CLOSED`.
- **Tag schema universally** applied across all board projects.

### Migration notes
- Existing ticket tags are migrated automatically: the inferer prepends a `main:` prefix when none is present. Re-running the migration is idempotent.
- Tickets without a §12-Tags line in their source Markdown still appear correctly — the BACKLOG view falls back to title/type-based inference at render time.

### Reference
- Tracking ticket: [P3B-10](#) — Multi-Project Board-Refactor.

---

## [0.1.0] — Initial release

- SQLite-backed kanban with CRUD, attachments, iterations, worktree integration.
- Single 6-column kanban view (Backlog · In Arbeit · Testing · Ready · Parked · Closed).
- Multi-project support via project-folders under `boards/`.
