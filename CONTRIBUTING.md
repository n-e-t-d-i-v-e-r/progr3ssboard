# Contributing to progr3ssboard

Everyone is welcome — bug reports, feature ideas, code, docs, screenshots,
critique of weird design choices. The maintainer is a one-person operation; the
project will stay small on purpose, but contributions are read and answered.

## Quick paths

| You want to … | Do this |
|---|---|
| Report a bug | Open a [GitHub Issue](https://github.com/n-e-t-d-i-v-e-r/progr3ssboard/issues/new) — see template below |
| Request a feature | Open an Issue with the prefix `[idea]` in the title, or start a [Discussion](https://github.com/n-e-t-d-i-v-e-r/progr3ssboard/discussions) |
| Ask a how-do-I question | Discussions, not Issues |
| Submit a fix or feature | Fork → branch (`fix/<short-desc>` or `feat/<short-desc>`) → PR against `main` |
| Submit a docs/typo fix | Same as code — small PRs against `main` are welcome and merged fast |

## Design constraints (please respect these)

The project is opinionated. PRs that conflict with these usually won't merge —
not because the work isn't valuable, but because the constraint defines what
progr3ssboard *is*:

- **Single file for the server** — `progr3ssboard.py` stays one Python file.
  Splitting into modules requires explicit discussion.
- **Zero runtime dependencies** — Python stdlib only. No `requirements.txt`,
  no `pip install`, no bundled JS framework. The whole point is `git clone &&
  python3 progr3ssboard.py --serve`.
- **SQLite as the only storage** — no Postgres adapter, no Redis cache, no
  ORM. If you need scale beyond SQLite, you're past the tool's use case.
- **Backward-compatible schema** — adding columns via idempotent `ALTER TABLE`
  is fine; renaming/dropping needs a migration path that doesn't break
  existing users' `boards/*/board.db` files.
- **Local-first, no cloud** — no telemetry, no analytics, no auto-update
  pings. The tool talks to localhost (or LAN if you choose) and that's it.

## Bug report template

When opening an Issue, please include:

```
**What happened:**  (one or two sentences)
**What you expected:**  (one or two sentences)
**Steps to reproduce:**
  1.
  2.
  3.
**Environment:**
  - OS:               (macOS 14.5 / Ubuntu 22.04 / etc.)
  - Python version:   (python3 --version)
  - Browser:          (Firefox 125 / Safari 17 / etc.)
  - progr3ssboard commit:  (git rev-parse --short HEAD)
**Logs (if any):**
  paste relevant output from the server terminal
```

## Feature request template

```
**The problem:**  (what are you trying to do, that's awkward today?)
**A solution that would work for you:**  (no need to design it, just sketch)
**Alternatives you considered:**  (optional)
**Would this break the design constraints above?**  (be honest)
```

## Code submissions (PRs)

- Branch from `main`, name it `fix/<short>` or `feat/<short>`.
- Keep PRs focused — one logical change per PR. Unrelated refactors split off.
- For UI/CSS changes: include a before/after screenshot in the PR description.
- Don't bump versions or edit `LICENSE` — the maintainer handles release-level
  stuff.
- The `scripts/check.sh` pre-push audit should still pass — it scans for
  inadvertent trademark mentions and other things that shouldn't leak into a
  public repo. Run it before pushing your branch.

## Code of conduct

Be civil. Disagreement is fine and useful; personal attacks are not. The
maintainer reserves the right to lock or remove threads that become hostile.
There's no formal code-of-conduct document yet — if this project grows enough
to need one, we'll adopt the [Contributor Covenant](https://www.contributor-covenant.org/).

## Licensing

By contributing, you agree your contributions are licensed under the same
[MIT License](LICENSE) as the rest of the project.

## Questions

If you're not sure where to start, open a Discussion thread and ask. There are
no dumb questions; there are only un-asked ones.
