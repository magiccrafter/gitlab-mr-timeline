#!/usr/bin/env python3
"""Numbers and indexes for writing the timeline summaries from merge_requests.json.

Subcommands (all read <out-dir>/merge_requests.json; duplicates are excluded unless --with-duplicates):
  summary                 per-year table: MRs, projects, authors, lines, L/XL, busiest month, top projects/authors
  year YYYY               per-month table for one year, plus top projects and authors
  largest [-n N]          the N largest MRs by lines changed (with links)
  project-index           writes <out-dir>/project_index.md: one compact entry per project (desc, topics, README gist)
  initiatives RULES.json  per-initiative stats, representative MRs and a primary-area breakdown

RULES.json format:
  {"initiatives": [
     {"name": "Payments Ledger", "area": "Core platform",
      "projects": ["ledger-api", "ledger-e2e-tests"],      # project names or path suffixes
      "keywords": "ledger|double.entry",                   # optional regex on title + branch
      "authors": ["alice"]},                               # optional author filter
     ...]}
  An initiative matches an MR if (projects OR keywords) match, and authors (if given) match.
  "area" is used for the non-overlapping breakdown: each MR counts once, for the first
  initiative (in file order) that has an area and matches it.
"""

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


def lines_of(r):
    return (r["additions"] or 0) + (r["deletions"] or 0)


def load(out_dir, with_duplicates):
    data = json.loads((out_dir / "merge_requests.json").read_text())
    records = data["merge_requests"]
    if not with_duplicates:
        records = [r for r in records if not r.get("duplicate_of")]
    return data["meta"], records


def fmt_top(counter, n=5, prefix=""):
    return ", ".join(f"{prefix}{k} ({v})" for k, v in counter.most_common(n))


def cmd_summary(meta, records, args):
    print(f"Groups: {', '.join(meta['groups'])} · authors filter: {meta.get('authors_filter') or 'all'}")
    print(f"Total: {len(records)} MRs (excl. {meta.get('duplicates', 0)} duplicates) · "
          f"{len({r['project']['id'] for r in records})} projects · "
          f"{len({r['author']['username'] for r in records})} authors · "
          f"{records[0]['merged_at'][:10]} → {records[-1]['merged_at'][:10]}")
    print(f"merged_at sources: {meta.get('merged_at_sources')}  (updated_at = approximate date)")
    missing = sum(1 for r in records if r["additions"] is None)
    if missing:
        print(f"MRs without diff stats: {missing}")
    print()
    print("| Year | MRs | Projects | Authors | Lines | L/XL | Busiest month | Top projects | Top authors |")
    print("|---|---:|---:|---:|---:|---:|---|---|---|")
    by_year = defaultdict(list)
    for r in records:
        by_year[r["merged_at"][:4]].append(r)
    for year, rs in sorted(by_year.items()):
        month, n = Counter(r["merged_at"][:7] for r in rs).most_common(1)[0]
        print(f"| {year} | {len(rs)} | {len({r['project']['id'] for r in rs})} | "
              f"{len({r['author']['username'] for r in rs})} | {sum(map(lines_of, rs)):,} | "
              f"{sum(1 for r in rs if r['size_bucket'] in ('L', 'XL'))} | {month} ({n}) | "
              f"{fmt_top(Counter(r['project']['name'] for r in rs), 4)} | "
              f"{fmt_top(Counter(r['author']['username'] for r in rs), 4, '@')} |")


def cmd_year(meta, records, args):
    rs = [r for r in records if r["merged_at"].startswith(args.year)]
    if not rs:
        raise SystemExit(f"No MRs in {args.year}")
    print(f"{args.year}: {len(rs)} MRs · {len({r['project']['id'] for r in rs})} projects · "
          f"{len({r['author']['username'] for r in rs})} authors · {sum(map(lines_of, rs)):,} lines")
    print("\n| Month | MRs | Projects | Lines | L/XL | Top projects | Top authors |")
    print("|---|---:|---:|---:|---:|---|---|")
    by_month = defaultdict(list)
    for r in rs:
        by_month[r["merged_at"][:7]].append(r)
    for m in [f"{args.year}-{i:02d}" for i in range(1, 13)]:
        ms = by_month.get(m, [])
        print(f"| {m} | {len(ms)} | {len({r['project']['id'] for r in ms})} | {sum(map(lines_of, ms)):,} | "
              f"{sum(1 for r in ms if r['size_bucket'] in ('L', 'XL'))} | "
              f"{fmt_top(Counter(r['project']['name'] for r in ms), 3)} | "
              f"{fmt_top(Counter(r['author']['username'] for r in ms), 3, '@')} |")
    print(f"\nTop projects: {fmt_top(Counter(r['project']['name'] for r in rs), 10)}")
    print(f"Top authors: {fmt_top(Counter(r['author']['username'] for r in rs), 10, '@')}")


def cmd_largest(meta, records, args):
    for r in sorted(records, key=lambda r: -lines_of(r))[:args.n]:
        print(f"- {r['merged_at'][:10]} | {r['project']['name']} | {lines_of(r):,} lines | "
              f"@{r['author']['username']} | [{r['title'][:90]}]({r['web_url']})")


def clean_text(text):
    text = re.sub(r"<!--.*?-->|```.*?```|\[!\[.*?\)\]\(.*?\)|!\[[^\]]*\]\([^)]*\)", " ", text, flags=re.S)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s*(=+|#+|[-*>|]|:\w+:.*|include::.*|image::.*)\s*", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


def cmd_project_index(meta, records, args):
    projects = json.loads((args.out_dir / ".cache" / "projects.json").read_text())
    docs_dir = args.out_dir / ".cache" / "docs"
    by_project = defaultdict(list)
    for r in records:
        by_project[r["project"]["id"]].append(r)
    out = [f"# Project index — {len(by_project)} projects (ordered by first merged MR)", ""]
    for pid, mrs in sorted(by_project.items(), key=lambda kv: kv[1][0]["merged_at"]):
        p = projects.get(str(pid), {})
        doc_file = docs_dir / f"{pid}.json"
        doc = json.loads(doc_file.read_text()) if doc_file.exists() else {}
        readme = clean_text((doc.get("readme") or {}).get("content", ""))[:450]
        authors = fmt_top(Counter(r["author"]["username"] for r in mrs), 3, "@")
        out.append(f"## {p.get('path', mrs[0]['project']['path'])} — {len(mrs)} MRs, "
                   f"{mrs[0]['merged_at'][:7]}→{mrs[-1]['merged_at'][:7]}, ~{sum(map(lines_of, mrs)):,} lines, "
                   f"docs:{len(doc.get('docs') or [])}{' ARCHIVED' if p.get('archived') else ''}")
        if len({r['author']['username'] for r in records}) > 1:
            out.append(f"authors: {authors}")
        if p.get("description"):
            out.append(f"desc: {p['description'][:200]}")
        if p.get("topics"):
            out.append(f"topics: {', '.join(p['topics'])}")
        if readme:
            out.append(f"readme: {readme}")
        out.append("")
    path = args.out_dir / "project_index.md"
    path.write_text("\n".join(out))
    print(f"Wrote {path} ({path.stat().st_size:,} bytes)")


def matcher(rule):
    names = rule.get("projects") or []
    pattern = re.compile(rule["keywords"], re.I) if rule.get("keywords") else None
    authors = set(rule.get("authors") or [])

    def match(r):
        path = r["project"]["path"]
        hit = any(path == n or path.endswith("/" + n) for n in names)
        if not hit and pattern:
            hit = bool(pattern.search(f"{r['title']} {r['source_branch']}"))
        return hit and (not authors or r["author"]["username"] in authors)
    return match


def cmd_initiatives(meta, records, args):
    rules = json.loads(Path(args.rules).read_text())["initiatives"]
    multi_author = len({r["author"]["username"] for r in records}) > 1
    covered = set()
    for rule in rules:
        rs = [r for r in records if matcher(rule)(r)]
        covered |= {r["id"] for r in rs}
        if not rs:
            print(f"## {rule['name']}: no matching MRs\n")
            continue
        print(f"## {rule['name']}: {len(rs)} MRs | {rs[0]['merged_at'][:7]} → {rs[-1]['merged_at'][:7]} | "
              f"~{sum(map(lines_of, rs)):,} lines | L/XL {sum(1 for r in rs if r['size_bucket'] in ('L', 'XL'))}")
        print(f"   per year: {dict(sorted(Counter(r['merged_at'][:4] for r in rs).items()))}")
        print(f"   projects: {fmt_top(Counter(r['project']['name'] for r in rs), 8)}")
        if multi_author:
            print(f"   authors: {fmt_top(Counter(r['author']['username'] for r in rs), 8, '@')}")
        print("   largest:")
        for r in sorted(sorted(rs, key=lambda r: -lines_of(r))[:args.n], key=lambda r: r["merged_at"]):
            print(f"   - {r['merged_at'][:7]} [{r['title'][:90]}]({r['web_url']}) ({lines_of(r):,} lines)")
        print()

    rest = [r for r in records if r["id"] not in covered]
    print(f"Uncategorised: {len(rest)} MRs — {fmt_top(Counter(r['project']['name'] for r in rest), 15)}\n")

    areas = [(rule["area"], matcher(rule)) for rule in rules if rule.get("area")]
    if areas:
        primary, primary_lines, per_year = Counter(), Counter(), defaultdict(Counter)
        for r in records:
            area = next((a for a, m in areas if m(r)), "Other")
            primary[area] += 1
            primary_lines[area] += lines_of(r)
            per_year[r["merged_at"][:4]][area] += 1
        print("| Area | MRs | Share | Lines |")
        print("|---|---:|---:|---:|")
        for area, n in primary.most_common():
            print(f"| {area} | {n} | {n * 100 / len(records):.0f}% | {primary_lines[area]:,} |")
        print()
        for year, counts in sorted(per_year.items()):
            total = sum(counts.values())
            print(f"{year}: " + ", ".join(f"{a} {n * 100 / total:.0f}%" for a, n in counts.most_common()))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=Path("mr-timeline"))
    parser.add_argument("--with-duplicates", action="store_true", help="keep MRs flagged as mirrored duplicates")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("summary")
    sub.add_parser("year").add_argument("year")
    sub.add_parser("largest").add_argument("-n", type=int, default=25)
    sub.add_parser("project-index")
    p = sub.add_parser("initiatives")
    p.add_argument("rules")
    p.add_argument("-n", type=int, default=6, help="representative MRs per initiative")
    args = parser.parse_args()
    meta, records = load(args.out_dir, args.with_duplicates)
    {"summary": cmd_summary, "year": cmd_year, "largest": cmd_largest,
     "project-index": cmd_project_index, "initiatives": cmd_initiatives}[args.cmd](meta, records, args)


if __name__ == "__main__":
    main()
