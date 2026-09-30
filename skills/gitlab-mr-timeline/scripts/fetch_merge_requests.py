#!/usr/bin/env python3
"""Fetch merged GitLab merge requests for one or more groups (all authors by default).

Outputs (in --out-dir):
  merge_requests.json   every merged MR, sorted by merged_at, with project/group/author/size info
  digest/YYYY-MM.md     compact per-month listing used as input for writing summaries
  projects/<path>.md    README + docs/ of every project touched, plus MR stats per project

Auth: GITLAB_TOKEN env var (token with read_api scope), falling back to
`glab config get token --host <host>`.

Examples:
  fetch_merge_requests.py --group acme/payments --group acme/platform
  fetch_merge_requests.py --group acme/payments --author me --since 2024-01-01
  fetch_merge_requests.py --group acme --author alice --author bob --out-dir ./team-timeline
"""

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

GRAPHQL_BATCH = 25
DESCRIPTION_SNIPPET = 300
SIZE_BUCKETS = [(10, "XS"), (50, "S"), (250, "M"), (1000, "L")]
DOC_EXTS = (".md", ".markdown", ".adoc", ".asciidoc", ".rst", ".txt")
DOC_DIRS = ("docs", "doc", "documentation")
DOCS_MAX_FILES = 25
DOCS_MAX_FILE_BYTES = 40_000
DOCS_MAX_TOTAL_BYTES = 150_000

_log_lock = threading.Lock()


def log(msg):
    with _log_lock:
        print(msg, file=sys.stderr, flush=True)


def ssl_context():
    # python.org builds on macOS ship without a CA bundle unless
    # "Install Certificates.command" was run; fall back to certifi or the system bundle.
    paths = ssl.get_default_verify_paths()
    if os.environ.get("SSL_CERT_FILE") or (paths.openssl_cafile and os.path.exists(paths.openssl_cafile)):
        return ssl.create_default_context()
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    for cafile in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
        if os.path.exists(cafile):
            return ssl.create_default_context(cafile=cafile)
    return ssl.create_default_context()


# --------------------------------------------------------------------------- HTTP


class GitLab:
    def __init__(self, host, token):
        self.api = f"https://{host}/api/v4"
        self.graphql_url = f"https://{host}/api/graphql"
        # Bearer works for both personal access tokens and glab OAuth tokens.
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self.ssl = ssl_context()

    def _request(self, url, data=None, raw=False):
        headers = dict(self.headers)
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        for attempt in range(7):
            req = urllib.request.Request(url, data=body, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=60, context=self.ssl) as resp:
                    text = resp.read().decode(errors="replace")
                    return (text if raw else json.loads(text)), resp.headers
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    wait = float(e.headers.get("Retry-After") or 2 ** attempt)
                    log(f"  HTTP {e.code}, retrying in {wait:.0f}s")
                    time.sleep(wait)
                    continue
                detail = e.read().decode(errors="replace")[:300]
                raise RuntimeError(f"HTTP {e.code} for {url.split('?')[0]}: {detail}") from None
            except urllib.error.URLError as e:
                if isinstance(e.reason, ssl.SSLCertVerificationError):
                    raise RuntimeError(f"TLS verification failed: {e.reason}. Set SSL_CERT_FILE to a CA bundle.") from None
                wait = 2 ** attempt
                log(f"  network error ({e.reason}), retrying in {wait}s")
                time.sleep(wait)
        raise RuntimeError(f"giving up on {url.split('?')[0]}")

    def _url(self, path, params):
        return f"{self.api}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")

    def get(self, path, params=None):
        return self._request(self._url(path, params))[0]

    def get_raw(self, path, params=None):
        return self._request(self._url(path, params), raw=True)[0]

    def paginate(self, path, params):
        url = self._url(path, params)
        while url:
            data, headers = self._request(url)
            yield from data
            url = next_link(headers.get("Link", ""))

    def graphql(self, query):
        return self._request(self.graphql_url, {"query": query})[0]


def next_link(link_header):
    for part in link_header.split(","):
        m = re.match(r'\s*<([^>]+)>;\s*rel="next"', part)
        if m:
            return m.group(1)
    return None


def resolve_token(host):
    token = os.environ.get("GITLAB_TOKEN", "").strip()
    if token:
        return token
    try:
        out = subprocess.run(["glab", "config", "get", "token", "--host", host],
                             capture_output=True, text=True, timeout=10)
        token = out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        token = ""
    if not token:
        sys.exit("No token: export GITLAB_TOKEN=<token with read_api> or run `glab auth login`.")
    return token


def run_parallel(items, fn, workers, label):
    """Run fn(item) concurrently; yields (item, result|None) and logs progress."""
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                result = future.result()
            except RuntimeError as e:
                log(f"  {label} {item}: {e}")
                result = None
            done += 1
            if done % 25 == 0 or done == len(items):
                log(f"  {done}/{len(items)} {label}")
            yield item, result


# --------------------------------------------------------------------------- cache


def load_cache(path, refresh):
    if refresh or not path.exists():
        return {}
    return json.loads(path.read_text())


def save_cache(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False))


# --------------------------------------------------------------------------- fetching


def fetch_mrs(gl, groups, authors, since, until):
    """All merged MRs in the groups (incl. subgroups and archived projects), optionally per author."""
    mrs = {}
    for group in groups:
        for author in authors or [None]:
            params = {
                "state": "merged",
                "scope": "all",
                "include_subgroups": "true",
                "non_archived": "false",  # the default silently drops MRs from archived projects
                "order_by": "created_at",
                "sort": "asc",
                "per_page": 100,
            }
            if author:
                params["author_username"] = author
            if since:
                params["merged_after"] = since
            if until:
                params["merged_before"] = until
            count = 0
            for mr in gl.paginate(f"/groups/{urllib.parse.quote(group, safe='')}/merge_requests", params):
                mr["_source_group"] = group
                mrs.setdefault(mr["id"], mr)
                count += 1
                if count % 1000 == 0:
                    log(f"  {group}{' @' + author if author else ''}: {count} MRs so far")
            log(f"{group}{' @' + author if author else ''}: {count} merged MRs")
    return list(mrs.values())


def fetch_projects(gl, project_ids, cache, cache_path, workers):
    missing = [pid for pid in project_ids if "default_branch" not in cache.get(str(pid), {})]
    log(f"Projects: {len(project_ids)} total, {len(missing)} to fetch")

    def fetch(pid):
        p = gl.get(f"/projects/{pid}")
        return {
            "id": p["id"],
            "name": p["name"],
            "path": p["path_with_namespace"],
            "namespace": p["namespace"]["full_path"],
            "description": p.get("description") or "",
            "topics": p.get("topics") or [],
            "archived": p.get("archived", False),
            "web_url": p["web_url"],
            "default_branch": p.get("default_branch"),
            "empty_repo": p.get("empty_repo", False),
            "created_at": p.get("created_at"),
            "last_activity_at": p.get("last_activity_at"),
        }

    for i, (pid, result) in enumerate(run_parallel(missing, fetch, workers, "projects"), 1):
        if result:
            cache[str(pid)] = result
        if i % 50 == 0:
            save_cache(cache_path, cache)
    save_cache(cache_path, cache)
    return cache


def fetch_diff_stats(gl, mrs, cache, cache_path, workers):
    todo = [mr for mr in mrs if str(mr["id"]) not in cache]
    log(f"Diff stats: {len(mrs)} MRs, {len(todo)} to fetch")
    batches = [todo[i:i + GRAPHQL_BATCH] for i in range(0, len(todo), GRAPHQL_BATCH)]

    def fetch(batch_index):
        batch = batches[batch_index]
        fields = [
            f"m{i}: project(fullPath: {json.dumps(mr['references']['full'].rsplit('!', 1)[0])}) "
            f"{{ mergeRequest(iid: {json.dumps(str(mr['iid']))}) "
            f"{{ diffStatsSummary {{ additions deletions fileCount }} }} }}"
            for i, mr in enumerate(batch)
        ]
        try:
            data = gl.graphql("query { " + " ".join(fields) + " }").get("data") or {}
        except RuntimeError as e:
            log(f"  graphql batch failed ({e}); falling back to REST")
            data = {}
        result = {}
        for i, mr in enumerate(batch):
            stats = ((data.get(f"m{i}") or {}).get("mergeRequest") or {}).get("diffStatsSummary")
            if stats:
                result[str(mr["id"])] = {"additions": stats["additions"], "deletions": stats["deletions"],
                                         "files_changed": stats["fileCount"]}
            else:
                result[str(mr["id"])] = rest_changes_count(gl, mr)
        return result

    for i, (_, result) in enumerate(run_parallel(range(len(batches)), fetch, workers, "diff-stat batches"), 1):
        cache.update(result or {})
        if i % 20 == 0:
            save_cache(cache_path, cache)
    save_cache(cache_path, cache)
    return cache


def rest_changes_count(gl, mr):
    try:
        detail = gl.get(f"/projects/{mr['project_id']}/merge_requests/{mr['iid']}")
        files = detail.get("changes_count")
        files = int(str(files).rstrip("+")) if files else None
    except (RuntimeError, ValueError):
        files = None
    return {"additions": None, "deletions": None, "files_changed": files}


def fill_missing_merged_at(gl, mrs, cache, cache_path, workers):
    """Very old MRs can be merged without merged_at: use the merge/squash commit date, else updated_at."""
    missing = [mr for mr in mrs if not mr.get("merged_at")]
    if not missing:
        return
    todo = [mr for mr in missing if str(mr["id"]) not in cache]
    log(f"Missing merged_at: {len(missing)} MRs ({len(todo)} to resolve from merge commits)")

    def resolve(mr):
        for sha in (mr.get("merge_commit_sha"), mr.get("squash_commit_sha")):
            if not sha:
                continue
            try:
                committed = gl.get(f"/projects/{mr['project_id']}/repository/commits/{sha}")["committed_date"]
                date = parse_ts(committed).astimezone(timezone.utc).isoformat(timespec="milliseconds")
                return {"merged_at": date.replace("+00:00", "Z"), "source": "merge_commit"}
            except RuntimeError:
                continue
        return {"merged_at": mr["updated_at"], "source": "updated_at"}

    by_id = {id(mr): mr for mr in todo}
    for key, result in run_parallel([id(mr) for mr in todo], lambda k: resolve(by_id[k]), workers, "merge dates"):
        cache[str(by_id[key]["id"])] = result or {"merged_at": by_id[key]["updated_at"], "source": "updated_at"}
    save_cache(cache_path, cache)
    for mr in missing:
        entry = cache[str(mr["id"])]
        mr["merged_at"] = entry["merged_at"]
        mr["_merged_at_source"] = entry["source"]


# --------------------------------------------------------------------------- normalising


def parse_ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def size_bucket(additions, deletions):
    if additions is None or deletions is None:
        return None
    lines = additions + deletions
    for limit, name in SIZE_BUCKETS:
        if lines < limit:
            return name
    return "XL"


def normalise(mr, project, stats):
    project_path = project.get("path") or mr["references"]["full"].rsplit("!", 1)[0]
    namespace = project.get("namespace") or project_path.rsplit("/", 1)[0]
    source_group = mr["_source_group"]
    created, merged = parse_ts(mr["created_at"]), parse_ts(mr["merged_at"])
    author = mr.get("author") or {}
    return {
        "id": mr["id"],
        "iid": mr["iid"],
        "reference": mr["references"]["full"],
        "title": mr["title"],
        "description": mr.get("description") or "",
        "web_url": mr["web_url"],
        "author": {"username": author.get("username"), "name": author.get("name")},
        "project": {
            "id": mr["project_id"],
            "name": project.get("name") or project_path.rsplit("/", 1)[-1],
            "path": project_path,
            "description": project.get("description", ""),
            "topics": project.get("topics", []),
            "archived": project.get("archived"),
        },
        "group": namespace,
        "source_group": source_group,
        "subgroup_path": namespace[len(source_group):].strip("/") if namespace.startswith(source_group) else namespace,
        "source_branch": mr["source_branch"],
        "target_branch": mr["target_branch"],
        "labels": mr.get("labels") or [],
        "milestone": (mr.get("milestone") or {}).get("title"),
        "created_at": mr["created_at"],
        "merged_at": mr["merged_at"],
        "merged_at_source": mr.get("_merged_at_source", "gitlab"),
        "days_open": round((merged - created).total_seconds() / 86400, 1) if created and merged else None,
        "merged_by": (mr.get("merged_by") or mr.get("merge_user") or {}).get("username"),
        "reviewers": [r["username"] for r in mr.get("reviewers") or []],
        "user_notes_count": mr.get("user_notes_count"),
        "additions": stats.get("additions"),
        "deletions": stats.get("deletions"),
        "files_changed": stats.get("files_changed"),
        "size_bucket": size_bucket(stats.get("additions"), stats.get("deletions")),
        "duplicate_of": None,
    }


def mark_duplicates(records):
    """Mirrored repos (or projects copied from a template with history) carry the same MR twice.

    Copies keep the iid and the exact created/merged timestamps; a change rolled out separately to
    several repos does not, so it is correctly left alone.
    """
    seen = {}
    for r in sorted(records, key=lambda r: (r["merged_at"], r["project"]["path"])):
        key = (r["iid"], r["title"], r["created_at"], r["merged_at"])
        if key in seen and seen[key]["project"]["id"] != r["project"]["id"]:
            r["duplicate_of"] = seen[key]["reference"]
        else:
            seen.setdefault(key, r)
    return sum(1 for r in records if r["duplicate_of"])


# --------------------------------------------------------------------------- digest


def snippet(text):
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s*(#+|[-*] \[[ xX]\]|[-*>])\s*", "", text, flags=re.M)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:DESCRIPTION_SNIPPET] + ("…" if len(text) > DESCRIPTION_SNIPPET else "")


def make_shortener(groups):
    """Strip the shared top-level namespace (e.g. 'acme/') from project paths to keep digests compact."""
    tops = {g.split("/")[0] for g in groups}
    prefix = f"{tops.pop()}/" if len(tops) == 1 else ""
    return lambda path: path[len(prefix):] if prefix and path.startswith(prefix) else path


def write_digests(records, digest_dir, short):
    digest_dir.mkdir(parents=True, exist_ok=True)
    for old in digest_dir.glob("????-??.md"):
        old.unlink()
    multi_author = len({r["author"]["username"] for r in records}) > 1
    by_month = defaultdict(list)
    for r in records:
        by_month[r["merged_at"][:7]].append(r)
    for month, items in sorted(by_month.items()):
        lines = [f"# {month} — {len(items)} merged MRs", ""]
        for r in items:
            if r["additions"] is not None:
                size = f"{r['size_bucket']} +{r['additions']}/-{r['deletions']} ({r['files_changed']}f)"
            else:
                size = f"? ({r['files_changed']}f)" if r["files_changed"] else "?"
            labels = f" [{', '.join(r['labels'])}]" if r["labels"] else ""
            who = f" | @{r['author']['username']}" if multi_author else ""
            dup = f" | DUPLICATE of {r['duplicate_of']}" if r["duplicate_of"] else ""
            lines.append(f"- {r['merged_at'][:10]} | {short(r['project']['path'])} | {size} | "
                         f"{r['title']}{labels} | {r['source_branch']}{who}{dup}")
            desc = snippet(r["description"])
            if desc and not r["duplicate_of"]:
                lines.append(f"  > {desc}")
        lines += ["", "## Projects"]
        lines += [f"- {short(p)}: {n}" for p, n in Counter(r["project"]["path"] for r in items).most_common()]
        if multi_author:
            lines += ["", "## Authors"]
            lines += [f"- @{a}: {n}" for a, n in Counter(r["author"]["username"] for r in items).most_common()]
        (digest_dir / f"{month}.md").write_text("\n".join(lines) + "\n")
    log(f"Wrote {len(by_month)} monthly digests to {digest_dir}")


# --------------------------------------------------------------------------- project docs


def readme_rank(name):
    lower = name.lower()
    for rank, ext in enumerate((".md", ".adoc", ".asciidoc", ".rst", ".markdown", ".txt")):
        if lower == f"readme{ext}":
            return rank
    return None


def fetch_project_docs(gl, project):
    """Return {"readme": {...} | None, "docs": [{path, content}], "truncated": bool}."""
    result = {"readme": None, "docs": [], "truncated": False}
    branch = project.get("default_branch")
    if project.get("empty_repo") or not branch:
        return result
    pid = project["id"]
    tree_params = {"ref": branch, "per_page": 100}
    root = list(gl.paginate(f"/projects/{pid}/repository/tree", tree_params))

    def read(path):
        content = gl.get_raw(f"/projects/{pid}/repository/files/{urllib.parse.quote(path, safe='')}/raw", {"ref": branch})
        if len(content) > DOCS_MAX_FILE_BYTES:
            content = content[:DOCS_MAX_FILE_BYTES] + "\n\n[… truncated …]\n"
            result["truncated"] = True
        return content

    readmes = sorted((readme_rank(e["name"]), e["path"]) for e in root
                     if e["type"] == "blob" and readme_rank(e["name"]) is not None)
    if readmes:
        result["readme"] = {"path": readmes[0][1], "content": read(readmes[0][1])}

    doc_files = []
    for entry in root:
        if entry["type"] == "tree" and entry["name"].lower() in DOC_DIRS:
            params = dict(tree_params, path=entry["path"], recursive="true")
            doc_files += [e["path"] for e in gl.paginate(f"/projects/{pid}/repository/tree", params)
                          if e["type"] == "blob" and e["path"].lower().endswith(DOC_EXTS)]
    # shallow files first, index/README pages before their siblings
    doc_files.sort(key=lambda p: (p.count("/"), not re.search(r"(^|/)(index|readme)\.", p.lower()), p.lower()))

    total = len(result["readme"]["content"]) if result["readme"] else 0
    for path in doc_files:
        if len(result["docs"]) >= DOCS_MAX_FILES or total >= DOCS_MAX_TOTAL_BYTES:
            result["truncated"] = True
            break
        try:
            content = read(path)
        except RuntimeError as e:
            log(f"  {project['path']}/{path}: {e}")
            continue
        result["docs"].append({"path": path, "content": content})
        total += len(content)
    return result


def fetch_all_docs(gl, projects, project_ids, cache_dir, refresh, workers):
    cache_dir.mkdir(parents=True, exist_ok=True)
    todo = [pid for pid in project_ids
            if str(pid) in projects and (refresh or not (cache_dir / f"{pid}.json").exists())]
    log(f"Project docs: {len(project_ids)} projects, {len(todo)} to fetch")
    for pid, data in run_parallel(todo, lambda pid: fetch_project_docs(gl, projects[str(pid)]), workers, "project docs"):
        if data is not None:
            (cache_dir / f"{pid}.json").write_text(json.dumps(data, ensure_ascii=False))
    return {pid: json.loads((cache_dir / f"{pid}.json").read_text())
            for pid in project_ids if (cache_dir / f"{pid}.json").exists()}


def demote_headings(markdown, levels):
    """Shift markdown headings down so embedded docs nest under the section heading."""
    out, fenced = [], False
    for line in markdown.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and re.match(r"#{1,6}\s", line):
            line = "#" * levels + line
        out.append(line)
    return "\n".join(out)


def embed(path, content, levels=2):
    lower = path.lower()
    if lower.endswith((".md", ".markdown")):
        return demote_headings(content, levels).strip()
    lang = "asciidoc" if lower.endswith((".adoc", ".asciidoc")) else "rst" if lower.endswith(".rst") else ""
    return f"````{lang}\n{content.strip()}\n````"


def write_project_docs(records, projects, docs, out_dir, short):
    out_dir.mkdir(parents=True, exist_ok=True)
    by_project = defaultdict(list)
    for r in records:
        by_project[r["project"]["id"]].append(r)
    for pid, mrs in by_project.items():
        project = projects.get(str(pid), {})
        path = mrs[0]["project"]["path"]
        doc = docs.get(pid, {})
        adds = sum(r["additions"] or 0 for r in mrs)
        dels = sum(r["deletions"] or 0 for r in mrs)
        labels = Counter(label for r in mrs for label in r["labels"]).most_common(8)
        authors = Counter(r["author"]["username"] for r in mrs).most_common(8)
        lines = [
            f"# {mrs[0]['project']['name']}",
            "",
            f"- **Path:** [{path}]({project.get('web_url', '')})",
            f"- **Description:** {project.get('description') or '—'}",
            f"- **Topics:** {', '.join(project.get('topics') or []) or '—'}",
            f"- **Archived:** {'yes' if project.get('archived') else 'no'}",
            f"- **Created:** {(project.get('created_at') or '')[:10]} · "
            f"**Last activity:** {(project.get('last_activity_at') or '')[:10]}",
            f"- **Merged MRs in scope:** {len(mrs)} ({mrs[0]['merged_at'][:10]} → {mrs[-1]['merged_at'][:10]}), "
            f"+{adds}/-{dels} lines",
            f"- **Top authors:** {', '.join(f'@{a} ({n})' for a, n in authors)}",
            f"- **Top labels:** {', '.join(f'{name} ({n})' for name, n in labels) or '—'}",
            "",
        ]
        readme = doc.get("readme")
        if readme:
            lines += [f"## README (`{readme['path']}`)", "", embed(readme["path"], readme["content"]), ""]
        else:
            lines += ["## README", "", "_No README found._", ""]
        if doc.get("docs"):
            lines += ["## Docs", ""]
            for d in doc["docs"]:
                lines += [f"### `{d['path']}`", "", embed(d["path"], d["content"], levels=3), ""]
        if doc.get("truncated"):
            lines += ["_Some documentation was truncated or skipped because of size limits._", ""]
        (out_dir / f"{short(path).replace('/', '__')}.md").write_text("\n".join(lines))
    log(f"Wrote {len(by_project)} project docs to {out_dir}")


# --------------------------------------------------------------------------- main


def split_values(values):
    return [v.strip() for value in values or [] for v in value.split(",") if v.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--group", action="append", required=True,
                        help="group full path; repeatable or comma-separated (subgroups are included)")
    parser.add_argument("--author", action="append",
                        help="only these author usernames; repeatable or comma-separated; 'me' = token owner. "
                             "Default: all authors")
    parser.add_argument("--since", help="only MRs merged after this date (YYYY-MM-DD)")
    parser.add_argument("--until", help="only MRs merged before this date (YYYY-MM-DD)")
    parser.add_argument("--host", default=os.environ.get("GITLAB_HOST", "gitlab.com"))
    parser.add_argument("--out-dir", type=Path, default=Path("mr-timeline"))
    parser.add_argument("--workers", type=int, default=8, help="parallel API requests (default 8)")
    parser.add_argument("--no-diff-stats", action="store_true", help="skip additions/deletions lookup")
    parser.add_argument("--no-docs", action="store_true", help="skip fetching README/docs per project")
    parser.add_argument("--refresh", action="store_true", help="ignore cached project/diff/docs data")
    args = parser.parse_args()
    groups = split_values(args.group)

    gl = GitLab(args.host, resolve_token(args.host))
    me = gl.get("/user")
    log(f"Authenticated as {me['username']} on {args.host}")
    authors = [me["username"] if a == "me" else a.lstrip("@") for a in split_values(args.author)]
    log(f"Groups: {', '.join(groups)} · Authors: {', '.join(authors) if authors else 'all'}")

    raw = fetch_mrs(gl, groups, authors, args.since, args.until)
    if not raw:
        sys.exit("No merged MRs found for the given filters.")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = args.out_dir / ".cache"
    merged_at_path, projects_path = cache_dir / "merged_at.json", cache_dir / "projects.json"
    diffs_path = cache_dir / "diffstats.json"

    fill_missing_merged_at(gl, raw, load_cache(merged_at_path, args.refresh), merged_at_path, args.workers)
    projects = fetch_projects(gl, sorted({mr["project_id"] for mr in raw}),
                              load_cache(projects_path, args.refresh), projects_path, args.workers)
    diff_stats = {}
    if not args.no_diff_stats:
        diff_stats = fetch_diff_stats(gl, raw, load_cache(diffs_path, args.refresh), diffs_path, args.workers)

    records = sorted(
        (normalise(mr, projects.get(str(mr["project_id"]), {}), diff_stats.get(str(mr["id"]), {})) for mr in raw),
        key=lambda r: r["merged_at"],
    )
    duplicates = mark_duplicates(records)

    output = {
        "meta": {
            "fetched_by": me["username"],
            "host": args.host,
            "groups": groups,
            "authors_filter": authors or None,
            "since": args.since,
            "until": args.until,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "total": len(records),
            "duplicates": duplicates,
            "projects": len({r["project"]["id"] for r in records}),
            "authors": len({r["author"]["username"] for r in records}),
            "first_merged_at": records[0]["merged_at"],
            "last_merged_at": records[-1]["merged_at"],
            "merged_at_sources": dict(Counter(r["merged_at_source"] for r in records)),
            "per_year": dict(sorted(Counter(r["merged_at"][:4] for r in records).items())),
            "top_authors": dict(Counter(r["author"]["username"] for r in records).most_common(25)),
        },
        "merge_requests": records,
    }
    out_file = args.out_dir / "merge_requests.json"
    out_file.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    log(f"Wrote {len(records)} MRs ({duplicates} flagged as duplicates) to {out_file}")

    short = make_shortener(groups)
    write_digests(records, args.out_dir / "digest", short)

    if not args.no_docs:
        project_ids = sorted({r["project"]["id"] for r in records})
        docs = fetch_all_docs(gl, projects, project_ids, cache_dir / "docs", args.refresh, args.workers)
        write_project_docs(records, projects, docs, args.out_dir / "projects", short)


if __name__ == "__main__":
    main()
