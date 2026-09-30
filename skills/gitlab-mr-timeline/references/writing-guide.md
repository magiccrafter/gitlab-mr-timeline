# Writing guide for the timeline files

## Contents
1. Principles
2. `monthly_highlights_YYYY.md` template
3. `yearly_highlights.md` template
4. `key_initiatives_highlights.md` template
5. Category tags

---

## 1. Principles

- **Themes over lists.** A month with 40 MRs should read as 3–6 themes, not 40 bullets. Group MRs by project or goal, and name the outcome ("idempotency for payment operations"), not the ticket.
- **Name the big ones.** Mark large changes with their size bucket from the digest, e.g. "(XL: 96 files)" or "(L)". They signal effort to the reader.
- **Keep ticket keys when they add traceability** (e.g. `ZEN-122`), but never as the only description.
- **Explain what a project is** the first time it appears in a year: "casino-access-manager, the service deciding who may play what". The project index and `projects/*.md` tell you what each one does.
- **Bold the headline.** Each month opens with a one-line bold headline when there's a clear theme. Use **New project:** when a service is bootstrapped. Readers skim headlines to follow the arc.
- **Numbers come from the stats script,** never from estimates. Put data caveats in a short "Data note" or "Counting note" blockquote, not scattered through the prose.
- **Voice:**
  - First person when the scope is the user's own MRs.
  - Third person with `@username` credits for teams.
  - For teams, attribute themes to their main contributors ("led by @alice").
- **Be honest about gaps.** An empty month gets one line: "No merged MRs." or "No merged MRs (before start)." Don't pad it.

## 2. `monthly_highlights_YYYY.md`

```markdown
# Monthly Highlights — 2021

**289 merged MRs · 33 projects**   ← for teams add "· 14 authors"

<2–4 sentence overview of the year's arc: the main themes and what was new.>

> Data note: <only if needed: approximate dates, duplicates, missing diff stats>

## January 2021 — 24 MRs
**New project: GUID Generator API; platform library grows.**
- Delivered the first complete deployable **GUID Generator API** (XL, +1.2k lines): Netty runtime, pod-name worker IDs, smoke tests.
- **gaming-etcd** (new): etcd cluster skeleton for GUID worker coordination.
- <more themed bullets…>

Projects: guid-generator-api, gaming-etcd, gaming-core, …   ← for teams: "Contributors: @alice (12), @bob (7)"

## February 2021 — 39 MRs
…
(all 12 months, in order; months before the first or after the last MR say so in one line)
```

Aim for 3–8 bullets for a busy month, and 1–2 for a quiet one.

## 3. `yearly_highlights.md`

```markdown
# Yearly Highlights — <first month> to <last month>

**<total> merged MRs · <projects> projects · <years> years** in `<groups>`.

| Year | MRs | Projects | Lines changed¹ | Headline |
|---|---:|---:|---:|---|
| 2015 | 24 | 2 | n/a | Reality Check: responsible gambling for the licence |
…

¹ Additions + deletions. <footnotes for duplicates or inflated years>

**How the focus shifted:** <era 1> → <era 2> → … (one short paragraph)

---

## 2018 — <a short title capturing the year>
*321 MRs · 20 projects · busiest month Nov (57)*

<1–2 sentence framing>
- **<Theme or project>:** what was delivered and why it mattered.
- …
```

Keep each year to 5–10 bullets. The monthly files hold the detail; this file is the story.

## 4. `key_initiatives_highlights.md`

```markdown
# Key Initiatives — <span>

<one paragraph: how initiatives were identified; ★ marks keyword-based, overlapping ones; lines = rough effort>

**Categories:** 🧱 Greenfield · 💰 Core money movement · 🛡️ Regulatory / player protection · 🔌 Integrations / B2B · 🏗️ Platform & infra · 🔁 Migration / modernisation · 🧰 Developer experience · 📈 Observability, performance & cost · 🤖 AI-assisted engineering

## Overview
| # | Initiative | Categories | Span | MRs | ~Lines |
|---|---|---|---|---:|---:|
| 1 | Wallets v2 Platform | 🧱💰🔁 | 2021 → 2026 | 455 | 220k |
…

---

## 1. Wallets v2 Platform 🧱💰🔁
**Span:** … · **Effort:** 455 MRs (~20% of total), ~220k lines, 83 L/XL · **Projects:** …
**What it is:** <what problem it solved and why it mattered, 1–3 sentences>
**Milestones:**
- **Nov 2023:** …
- **2024:** …
**Representative MRs:** [short label](url) · [short label](url) · …

(…one section per initiative, ordered by impact/effort…)

## Where the energy went
| Area | MRs | Share | ~Lines |
…
**By era:** <how the mix shifted, using stats.py per-year area shares>

**What the data suggests <I gravitate to | the team gravitates to>:** 3–5 bullets backed by evidence, e.g. greenfield services bootstrapped, recurring problem types, ownership of big migrations.
```

Order initiatives by impact and effort. Put cross-cutting keyword initiatives (★) after the product and platform ones, and say they overlap.

## 5. Category tags

Pick one to three per initiative. Invent a new tag only when none of these fit, and add it to the legend.

| Tag | Use for |
|---|---|
| 🧱 Greenfield | new service/platform bootstrapped |
| 💰 Core money movement | transactions, wallets, ledgers, payments, balances |
| 🛡️ Regulatory / player protection | compliance, responsible gambling, restrictions, access control |
| 🔌 Integrations / B2B | third-party providers, partner APIs, aggregation |
| 🏗️ Platform & infra | shared libraries, Kubernetes, networking, databases |
| 🔁 Migration / modernisation | framework/runtime/cloud migrations |
| 🧰 Developer experience | CI/CD, templates, docs, internal tools |
| 📈 Observability, performance & cost | metrics, tracing, logging, tuning, cost cuts |
| 🤖 AI-assisted engineering | agent skills, AI tooling |
