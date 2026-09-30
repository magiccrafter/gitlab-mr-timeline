# gitlab-mr-timeline

A Claude Code skill that turns merged GitLab merge requests into a written history of the work. The scope can be every author in one or more groups, or just the authors you name. It produces:

- `merge_requests.json`: every merged MR, with author, project, group, dates, labels and change size
- `monthly_highlights_YYYY.md`: 12 month sections per year
- `yearly_highlights.md`: one section per year, with an overview table
- `key_initiatives_highlights.md`: major initiatives, categorised, with a "where the energy went" breakdown

The fetching and number-crunching are done by scripts. Claude writes the narrative, following the skill's workflow and writing guide.

## Layout

```
skills/gitlab-mr-timeline/
  SKILL.md                      workflow Claude follows
  references/writing-guide.md   templates + style for the three highlight files
  scripts/fetch_merge_requests.py
  scripts/stats.py
```

## Install

Make the skill available to Claude Code by symlinking it into your personal skills folder:

```sh
mkdir -p ~/.claude/skills
ln -s "$PWD/skills/gitlab-mr-timeline" ~/.claude/skills/gitlab-mr-timeline
```

Or, to use it in a single project only, symlink it into that project's `.claude/skills/` instead.

## Use

Ask Claude things like:

- "Build a timeline of everything merged in `acme/payments` and `acme/platform` since 2020."
- "Make a career timeline of my MRs in `acme/payments`." (This filters to you, with `--author me`.)
- "Summarise what alice and bob shipped in `acme` last year."

### Sample prompt (team-wide, all authors)

```text
Use the gitlab-mr-timeline skill to build the full history of all merged merge requests
in XXX/YYY and XXX/ZZZ, for ALL authors (no author filter), merged from
2015-01-01 until today.

Scope and setup:
- Groups: XXX/YYY, XXX/ZZZ (include subgroups and archived projects)
- Authors: everyone
- Date range: --since 2015-01-01 (no --until)
- Host: gitlab.com
- Output folder: ~/ws/private/gitlab-mr-timeline/output/XXX-2015-today
- My GITLAB_TOKEN is exported in this shell. If it isn't visible to you, give me the exact
  command and I'll run it myself.

Process:
1. Smoke-test with --since 2026-09-01 first, then run the full fetch in the background.
   Tell me the totals (MRs, projects, authors, first/last merge date) before writing.
2. Write in the third person, crediting people as @username. For each month, cover the
   main themes and who drove them rather than listing individual MRs.
3. This is a team-wide scope, so years may be large. You may use subagents to work on
   different years in parallel, as long as each one follows the skill's writing guide.
4. Before writing key_initiatives_highlights.md, show me the proposed initiative list
   (names, spans, MR counts, main contributors) so I can rename, merge or split them.

Deliverables in the output folder:
- merge_requests.json
- monthly_highlights_YYYY.md for every year from 2015 onwards
- yearly_highlights.md
- key_initiatives_highlights.md, ending with "where the energy went" broken down by area,
  era and top contributors
```

## Scripts on their own

Both scripts use only the Python standard library (3.9+). You need a GitLab token with `read_api` scope, either in `GITLAB_TOKEN` or via `glab auth login`.

```sh
# All authors in two groups
python3 skills/gitlab-mr-timeline/scripts/fetch_merge_requests.py \
  --group acme/payments --group acme/platform --out-dir ./mr-timeline

# Only some authors ("me" = token owner), merged in 2025
python3 skills/gitlab-mr-timeline/scripts/fetch_merge_requests.py \
  --group acme --author me,alice --since 2025-01-01 --until 2026-01-01 --out-dir ./mr-2025

# Numbers for writing
python3 skills/gitlab-mr-timeline/scripts/stats.py --out-dir ./mr-timeline summary
python3 skills/gitlab-mr-timeline/scripts/stats.py --out-dir ./mr-timeline year 2024
python3 skills/gitlab-mr-timeline/scripts/stats.py --out-dir ./mr-timeline project-index
python3 skills/gitlab-mr-timeline/scripts/stats.py --out-dir ./mr-timeline largest -n 20
python3 skills/gitlab-mr-timeline/scripts/stats.py --out-dir ./mr-timeline initiatives initiatives.json
```

### `fetch_merge_requests.py` options

| Flag | Default | Description |
|---|---|---|
| `--group PATH` | required | Group full path; repeatable or comma-separated. Subgroups and archived projects are included. |
| `--author USER` | all authors | Filter to these usernames; repeatable or comma-separated. `me` means the token owner. |
| `--since` / `--until` | none | Filter on merge date (`YYYY-MM-DD`). |
| `--host` | `$GITLAB_HOST` or `gitlab.com` | GitLab host. |
| `--out-dir` | `./mr-timeline` | Output folder. Use one folder per scope. |
| `--workers` | 8 | Number of parallel API requests. |
| `--no-diff-stats` | off | Skip the additions/deletions lookup. |
| `--no-docs` | off | Skip fetching project READMEs and docs. |
| `--refresh` | off | Ignore the caches. |

### Output folder

```
merge_requests.json      MRs + meta (totals, per-year, top authors, duplicates, merged_at sources)
digest/YYYY-MM.md        one line per MR + description snippet + per-project/author counts
projects/<path>.md       README + docs/ of each project, with MR stats
project_index.md         (stats.py project-index) compact overview of all projects
.cache/                  projects, diff stats, merge dates, docs — makes re-runs incremental
```

**Data quirks the scripts handle:**
- **Missing merge dates:** very old MRs can lack `merged_at`. The script falls back to the merge-commit date, or `updated_at` as a last resort, and records which in `merged_at_source`.
- **Mirrored repos:** these can hold the same MR twice. Copies get `duplicate_of`, and `stats.py` excludes them by default.
- **TLS on macOS:** python.org builds ship without a CA bundle. The script falls back to `certifi`, or to `/etc/ssl/cert.pem`.
