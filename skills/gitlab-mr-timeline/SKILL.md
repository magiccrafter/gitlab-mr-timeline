---
name: gitlab-mr-timeline
description: Build a work timeline from GitLab merged merge requests. It fetches every merged MR in one or more GitLab groups (all authors, or only selected authors), then writes monthly, yearly and key-initiative highlight summaries in Markdown. Use this whenever someone wants to summarise, review or tell the story of engineering work done in GitLab. Typical asks: a career or tenure timeline, a performance review or promotion packet, "what did I/we ship this year", a team or department retrospective, contribution history for a group, brag documents, or "where did my energy go". Use it even when they only mention merge requests, MRs, GitLab groups or contribution history without saying "timeline".
---

# GitLab MR Timeline

This skill turns merged merge requests into a readable history of the work. It runs in two phases:

1. **Data (deterministic):** `scripts/fetch_merge_requests.py` fetches the MRs and writes JSON, monthly digests and per-project docs.
2. **Narrative (you):** read the digests year by year and write the highlight files. `scripts/stats.py` supplies the numbers so they're computed, not guessed.

Scripts live next to this file. Refer to them as `<skill-dir>/scripts/...` and run them with `python3`. They use only the standard library, and Python 3.9+ is enough.

## 1. Pin down the scope

Before fetching, confirm these with the user. Only ask about the ones they haven't already answered.

- **Groups:** full group paths, e.g. `acme/payments`. Subgroups are always included.
- **Authors:** the default is **all authors** in the groups. Filter only when asked, e.g. "just me", "only alice and bob". `--author me` means the token owner.
- **Date range** (optional): `--since` / `--until` on merge date.
- **GitLab host:** default `gitlab.com`; use `--host` or `GITLAB_HOST` for self-managed instances.
- **Output folder:** default `./mr-timeline`. Use a fresh folder per distinct scope. Caches are shared within a folder, and the digests describe exactly one scope.
- **Voice:**
  - If the scope is the user's own MRs, write in the first person ("I built…").
  - For a team or group, write in the third person and credit people by `@username`.

The output files are:
- `merge_requests.json`
- `monthly_highlights_YYYY.md`: one per year, with 12 month sections
- `yearly_highlights.md`
- `key_initiatives_highlights.md`

## 2. Fetch the data

The token needs `read_api` scope. The script reads `GITLAB_TOKEN`, or falls back to `glab config get token`.

Check whether a token is available in your shell first. Often it isn't, because the user exported it only in their own terminal. In that case, give them the exact command to run themselves, either in their terminal or with the `! <command>` prefix.

Do a quick smoke test on a narrow date range before the full run. It surfaces auth, TLS or permission problems in seconds rather than after a 20-minute fetch:

```bash
python3 <skill-dir>/scripts/fetch_merge_requests.py --group acme/payments --group acme/platform \
  [--author me | --author alice,bob] --since 2025-06-01 --out-dir ./mr-timeline
```

Then run it again without the narrowed `--since` for the full history. Re-runs are incremental: project metadata, diff stats, merge dates and docs are cached in `<out-dir>/.cache/`. On large groups, give it a long timeout or run it in the background. Useful flags:
- `--workers N`: parallelism, default 8.
- `--no-docs` or `--no-diff-stats`: faster runs with less signal.
- `--refresh`: ignore the caches.

**What it produces:**
- `merge_requests.json`: every MR with title, description, author, project, group, labels, dates, reviewers and additions/deletions/files. A `meta` block holds totals.
- `digest/YYYY-MM.md`: one line per MR (`date | project | size | title [labels] | branch | @author`), a description snippet, then per-project and per-author counts. **This is your main reading material.**
- `projects/<path>.md`: each project's README and `docs/` folder, used to understand what a service actually does.

**Known data quirks** (the script handles them; your job is to mention them in the output):
- **Missing merge dates:** some very old MRs have no `merged_at`. The script uses the merge-commit date, or `updated_at` as a last resort, and records which in `merged_at_source`. Dates sourced from `updated_at` are approximate, so say so in that year's file.
- **Mirrored or forked repos:** these can contain the same MR twice. The script flags copies with `duplicate_of`, and `stats.py` excludes them by default.
- **Oversized line counts:** generated files, lockfiles and UI bundles can inflate lines changed. Treat "lines" as a rough effort signal, and footnote the outliers.

## 3. Get oriented

```bash
python3 <skill-dir>/scripts/stats.py --out-dir ./mr-timeline summary
python3 <skill-dir>/scripts/stats.py --out-dir ./mr-timeline project-index
```

1. **Read `summary`:** check the totals with the user, e.g. "2,315 MRs from 2015-10 to 2026-09, does that match what you expect?" A surprisingly late first date usually means projects were moved or deleted, or the scope is wrong.
2. **Read `project_index.md`:** a compact overview of every project. It's how you learn the domain before reading any month.
3. **Look at digest sizes** (`wc -c digest/*.md`) and plan how to split the reading.

## 4. Write the monthly files, one year at a time

For each year, oldest first:

1. Run `stats.py year YYYY` for the month-level numbers.
2. Read that year's digests.
   - Concatenate them if they fit comfortably, roughly under 60 KB.
   - Otherwise read month by month.
   - For very large team-wide years, read the per-project and per-author tallies first, then skim the MR lines for themes.
3. Open `projects/<path>.md` whenever a project's purpose isn't clear from its name and the index.
4. **Write `monthly_highlights_YYYY.md` immediately**, before moving to the next year. See `references/writing-guide.md` for the template and style.
5. Append 3–6 lines to `<out-dir>/.notes.md`:
   - the year's themes
   - new projects and services
   - peak months
   - candidate initiatives with their spans

**Why write as you go:** long timelines don't fit in context at once, and earlier material may get summarised away. Files on disk plus `.notes.md` are what the yearly and initiative files get built from. Recollection isn't reliable for that.

**Team-wide scopes** (many authors, thousands of MRs a year):
- Summarise themes and deliveries per month, not individual MRs.
- Name the main contributors for each theme.
- If a year is too big even month by month, say so and cover the main themes rather than skipping months silently.

If the user has said they're fine with subagents, years can be split across them in parallel. Give each subagent this file, the writing guide and its year. Otherwise, work sequentially.

## 5. Write `yearly_highlights.md`

Build it from the monthly files, `.notes.md` and `stats.py summary`. Take every number from the stats output, not from memory. The file has:
- an overview table
- a short "how the focus shifted" arc
- one section per year

Template: `references/writing-guide.md`.

## 6. Write `key_initiatives_highlights.md`

An initiative is a larger or higher-effort body of work, usually spanning months or years and several projects. Initiatives may overlap in time.

1. **Draft a rules file** (`<out-dir>/initiatives.json`) from `.notes.md` and the project index. Each initiative has:
   - a name
   - an `area`, used for the "where the energy went" breakdown
   - `projects`
   - optionally `keywords`, a regex for cross-cutting themes such as framework upgrades or infrastructure migrations
   - optionally `authors`

   The format is in the `stats.py` docstring.
2. **Run** `stats.py initiatives <out-dir>/initiatives.json`. Iterate until "Uncategorised" is small and every initiative's numbers look plausible.
3. **Show the user the proposed initiative list** (names, spans, MR counts) before writing prose. How work is grouped and named is their call, and it's much cheaper to fix now.
4. **Write the file** with an overview table, then one section per initiative. Each section has:
   - categories
   - span and effort
   - projects
   - what it is and why it mattered
   - milestones
   - representative MR links from the stats output

   End with the energy breakdown by area and era, and patterns the data supports. Template: `references/writing-guide.md`.

## 7. Wrap up

Tell the user where the files are. Also mention:
- the data caveats: approximate dates, duplicates, inflated line counts, anything outside the fetched groups
- that the narrative is your reading of MR titles, descriptions and READMEs, so they may want to adjust the wording

Offer to regroup the initiatives or change the voice. If the user wants to share the result, offer to publish the files as a page.
