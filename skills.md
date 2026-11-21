# fleetflow-api: Skills Index

**Every session**: pick the skill(s) for the task from the table below. Load one only when its trigger matches; don't load skills "just in case". Project docs (CLAUDE.md, rules.md, architecture.md) always take precedence over any skill's advice.

Research date: 2026-11-21. Each skill's contents were inspected by reading its SKILL.md and hooks.

## Which skill for which task
| Task | Use | Don't use |
|---|---|---|
| Any coding task | `ponytail` (always on via hook, `full` mode): smallest complete solution, reuse first | — |
| Chat replies | `caveman` (always on via hook, default `caveman` mode) | Not for docs, commits or PR text (see CLAUDE.md) |
| Routine or mechanical task (rename, small fix) | `/ultracave` for terser replies | Never for design discussions, security findings, test reports |
| Writing or changing Django/DRF code | `fleetflow-backend-dev` (project skill) | — |
| Writing tests or test plans | `fleetflow-backend-testing` (project skill) + `superpowers:test-driven-development` for new business logic | TDD workflow for trivial edits |
| Bug with unclear cause | `superpowers:systematic-debugging` | For obvious one-line fixes |
| New module or substantial feature (Phase kickoff) | `superpowers:brainstorming` → `superpowers:writing-plans` | For tasks under ~1 hour |
| Before finishing a task | `superpowers:verification-before-completion` | — |
| Reviewing a diff for security/tenancy | `fleetflow-security-review` (project skill), built-in `/security-review` | — |
| Reviewing a diff for bloat | `ponytail-review` | — |
| Reviewing a diff for correctness | built-in `/code-review` | — |

Frontend skills (`frontend-design`, `impeccable`) are **disabled** in this repo's `.claude/settings.json`. The global user skill `ui-ux-pro-max` can't be disabled per project. Ignore it here.

## Installed / enabled for this repo
| Skill / plugin | Source | Version (commit) | License | Install method | Notes |
|---|---|---|---|---|---|
| ponytail | github `DietrichGebert/ponytail` | 5.3.3 (`96608e0`) | MIT | `.claude/settings.json` → `extraKnownMarketplaces` + `enabledPlugins` | Hooks: SessionStart mode injection; optional "band" ledger writes to `~/.config/ponytail/` (local only; webhook is empty by default, so nothing leaves the machine). Default mode via `PONYTAIL_DEFAULT_MODE` or `~/.config/ponytail/config.json` |
| caveman | github `JuliusBrussee/caveman` | 4.1.0 (`a192894`) | Apache-2.0 / MIT | same | SessionStart/SubagentStart node hook; no network calls found in `caveman-activate.js`. Modes: `caveman` (default, moderate), `ultracave` (strict), `megacave` (Classical Chinese, **not used**). Per-repo override: `.caveman.json` `{ "defaultMode": "manual" }` to opt out |
| superpowers | `claude-plugins-official` marketplace (→ `obra/superpowers`, pinned sha `22f0b46`) | 7.0.2 | MIT | `enabledPlugins` | SessionStart hook injects `using-superpowers` (~3 KB per session). It states that CLAUDE.md instructions take precedence over its skills, so **use its workflows only for the triggers above** |
| fleetflow-backend-dev | this repo `.claude/skills/` | — | project | committed with repo | Thin pointer into architecture/rules |
| fleetflow-backend-testing | this repo | — | project | committed | Pointer into architecture §26 |
| fleetflow-security-review | this repo | — | project | committed | FleetFlow-specific security checklist |

First run: when you open this repo, Claude Code asks you to trust it and install the declared marketplaces and plugins. Accept that prompt. Nothing is written to global `~/.claude/settings.json` by this repo.

## Evaluated and excluded
| Skill | Why excluded |
|---|---|
| `security-guidance` (official) | SessionStart installs an Agent SDK into a venv, and a Stop hook runs an LLM review on every turn: high token and latency cost. The built-in `/security-review` plus `fleetflow-security-review` cover this on demand. Revisit in Phase 11 |
| `pyright-lsp` (official) | Useful, but needs a `pyright-langserver` binary and real code. **PROPOSED to enable in Phase 0** once the project exists |
| `claude-seo` (user-global) | Irrelevant to an API; disabled in this repo's settings |
| `anthropics/skills` webapp-testing, mcp-builder etc. | Not relevant to the backend |
| caveman sub-skills (`caveman-compress`, `cavecrew`, `megacave`) | `caveman-compress` rewrites .md files. **Never run it on project docs** (it would degrade them). Others are unnecessary |
| Dedicated Django/DRF community skills | No well-maintained, inspected candidate found on 2026-11-21. The project skill plus architecture.md give better FleetFlow-specific guidance |

## Updating skills
1. In Claude Code: `/plugin` → Marketplaces → update `ponytail`, `caveman`, `claude-plugins-official` (or run `claude plugin marketplace update <name>`).
2. Before accepting a new version: read the changed `SKILL.md`/hooks (`git log -p` on the marketplace clone under `~/.claude/plugins/marketplaces/<name>`) and check for new hooks, network calls or permission changes.
3. Record the new version and commit in the table above and in memory.md §Dependencies (category: tooling).
