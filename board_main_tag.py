"""board_main_tag.py — shared schema for the `main:` tag, pill colors and BACKLOG section mapping.

Imported by progr3ssboard.py and backlog-html.py. Single source of truth for:
- 10 main categories (`main:` tag) with pill color + icon
- BACKLOG section hierarchy (7 areas + a Bugs block at the bottom)
- Parser for `main: <category>, <secondary>, <more>...` tag strings
- Fallback inference from title/type when no `main:` tag is present

Generic software-project taxonomy — domain-neutral, applies to any board project.
Reference: P3B-10 (Multi-Project Board-Refactor, public v0.2.0).
"""

# 10 main categories: key -> (hex_color, emoji, display_label)
MAIN_TAG_PILL_COLOR = {
    "feature":      ("#3498db", "🔵", "Feature"),
    "bug":          ("#e74c3c", "🔴", "Bug"),
    "hotfix":       ("#c0392b", "🟥", "Hotfix"),
    "refactor":     ("#e67e22", "🟠", "Refactor"),
    "ui":           ("#f1c40f", "🟡", "UI / UX"),
    "architecture": ("#8e44ad", "🟣", "Architecture"),
    "infra":        ("#7f8c8d", "⚫", "Infra"),
    "docs":         ("#a04000", "🟤", "Docs"),
    "perf":         ("#16a085", "🟢", "Performance"),
    "general":      ("#bdc3c7", "⚪", "General"),
}

# BACKLOG sub-sections in hierarchy order (top-down); Bugs block last.
# (section_key, display_label, description)
BACKLOG_SECTIONS = [
    ("architecture", "🏛 Architecture", "Cross-cutting structural work and system design"),
    ("features",     "✨ Features",      "New capabilities and enhancements"),
    ("ui",           "🎨 UI / UX",       "Interface, styling and interaction"),
    ("infra",        "🛠 Infra",         "Tooling, build, CI/CD and deployment"),
    ("docs",         "📚 Docs",          "Documentation, guides and references"),
    ("perf",         "⚡ Performance",   "Optimization, profiling and scaling"),
    ("general",      "🌀 General",       "Maintenance and uncategorized work"),
    ("bugs",         "🐛 Bugs (open)",   "All bug and hotfix tickets, regardless of area"),
]


def parse_main_tag(tag_string):
    """Parses '<main:cat>, <secondary>, <more>...' -> (main, secondary, rest_list).

    Tolerant of:
    - 'main: cat, sec, ...'
    - 'main:cat,sec,...'
    - lower-/uppercase
    - missing main: prefix -> (None, first, rest)
    """
    if not tag_string:
        return (None, None, [])
    parts = [p.strip() for p in tag_string.split(',') if p.strip()]
    if not parts:
        return (None, None, [])
    main = None
    if parts[0].lower().startswith('main:'):
        main = parts[0].split(':', 1)[1].strip().lower()
        parts = parts[1:]
    secondary = parts[0] if parts else None
    rest = parts[1:] if len(parts) > 1 else []
    return (main, secondary, rest)


def infer_main_tag(title, ticket_type, existing_tag_string):
    """Fallback inference when no explicit main: tag is set.

    Order:
    1. Explicit main: prefix in tag_string
    2. ticket_type mapping (BUG/HOTFIX/FEATURE/INFRA/...)
    3. Title keywords (refactor/docs/perf/ui/ci...)
    4. Default 'general'
    """
    m, _, _ = parse_main_tag(existing_tag_string)
    if m and m in MAIN_TAG_PILL_COLOR:
        return m

    # Type-based
    t = (ticket_type or '').upper()
    if t == 'BUG':      return 'bug'
    if t == 'HOTFIX':   return 'hotfix'
    if t == 'INFRA':    return 'infra'
    if t in ('FEATURE', 'SPEC', 'FUTURE', 'REBUILD'): return 'feature'

    # Title-based
    tu = (title or '').upper()
    if tu.startswith('HOTFIX'): return 'hotfix'
    if any(kw in tu for kw in ('REFACTOR', 'CLEANUP', 'MIGRATE', 'RENAME')):
        return 'refactor'
    if any(kw in tu for kw in ('DOC', 'README', 'GUIDE', 'CHANGELOG')):
        return 'docs'
    if any(kw in tu for kw in ('PERF', 'PERFORMANCE', 'SLOW', 'OPTIMIZE', 'LATENCY', 'CACHE', 'MEMORY LEAK')):
        return 'perf'
    if any(kw in tu for kw in ('UI', 'UX', 'STYLE', 'CSS', 'LAYOUT', 'RESPONSIVE', 'DARK MODE', 'THEME')):
        return 'ui'
    if any(kw in tu for kw in ('INFRA', ' CI', 'CI/', 'CD', 'DEPLOY', 'PIPELINE', 'DOCKER', 'BUILD')):
        return 'infra'
    if any(kw in tu for kw in ('ARCHITECT', 'SCHEMA', 'DESIGN', 'DATA MODEL')):
        return 'architecture'
    return 'general'


def assign_backlog_section(main_tag, secondary_tag, title):
    """Maps (main_tag, secondary_tag, title) onto a BACKLOG section key.

    The Bugs block wins for bug/hotfix regardless of area.
    """
    if main_tag in ('bug', 'hotfix'):     return 'bugs'
    if main_tag in ('architecture', 'refactor'): return 'architecture'
    if main_tag == 'feature':             return 'features'
    if main_tag == 'ui':                  return 'ui'
    if main_tag == 'infra':               return 'infra'
    if main_tag == 'docs':                return 'docs'
    if main_tag == 'perf':                return 'perf'
    return 'general'


def main_tag_pill_html(main_tag):
    """HTML snippet for the pill at the card header."""
    color, icon, label = MAIN_TAG_PILL_COLOR.get(main_tag, MAIN_TAG_PILL_COLOR['general'])
    return f'<span class="main-pill" style="background:{color};color:#fff;font-size:10px;padding:2px 8px;border-radius:10px;font-weight:700;letter-spacing:0.3px;">{icon} {label}</span>'


def main_tag_border_color(main_tag):
    color, _, _ = MAIN_TAG_PILL_COLOR.get(main_tag, MAIN_TAG_PILL_COLOR['general'])
    return color
