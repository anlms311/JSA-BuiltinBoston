#!/usr/bin/env python3
"""
builtin_jobs.py  (v2 — two-stage, detail-page flagging)

Stage 1: scrape a Built In jobs-search URL for job cards (title/company/url/id/...).
Stage 2: title-filter (scientist / associate director), then fetch each surviving
         job's DETAIL page and flag it against focus keywords loaded from
         company_config.yaml -- matched against the FULL description, not just the
         card summary. Confidence tiering mirrors the company detector:
         2+ keyword hits OR 2+ focus areas = high; exactly 1 hit = low.

Why detail-page matching: many target roles have a generic title
("Senior Scientist, Analytical Development") but a description saturated with
CRISPR / gene-editing / CNS language. Card-level text misses those; the detail
page catches them.

Usage:
    python builtin_jobs.py \
        --url "https://www.builtinboston.com/jobs/biotech/pharmaceutical?search=director&city=Boston&state=Massachusetts&country=USA&allLocations=true" \
        --pages auto \
        --config company_config.yaml \
        --out jobs.json --csv jobs.csv

    # skip the detail fetch (faster, card-summary matching only):
    python builtin_jobs.py --url "..." --no-detail --out jobs.json

Title filter defaults to: scientist, associate director  (override with --title-include).
Plain "director" / "senior director" are NOT matched unless you add them.

Etiquette: Built In's Terms restrict automated collection. --delay applies to
BOTH list pages and detail fetches. Personal job-search use.
"""

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass, asdict, field
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import datetime as _dt
import os
import smtplib
from email.mime.text import MIMEText

import requests
import yaml
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (personal-job-search; contact: mike) BuiltInJobs/2.0"
HEADERS = {"User-Agent": UA, "Accept": "text/html"}
DEFAULT_DELAY = 2.0
TIMEOUT = 20
DEFAULT_TITLE_INCLUDE = ["scientist", "director"]


@dataclass
class Job:
    job_id: str
    title: str
    company: str
    url: str
    company_url: str = ""
    location: str = ""
    salary: str = ""
    seniority: str = ""
    posted: str = ""
    remote: str = ""
    industries: str = ""
    summary: str = ""
    apply_url: str = ""
    category: str = ""              # CNS | Gene | Both | ""
    cns_hits: list = field(default_factory=list)
    gene_hits: list = field(default_factory=list)


def _set_page(url: str, page: int) -> str:
    parts = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    q["page"] = str(page)
    return urlunparse(parts._replace(query=urlencode(q)))


def _host(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def detect_page_count(soup: BeautifulSoup) -> int:
    pages = [1]
    for a in soup.select('a[href*="page="]'):
        m = re.search(r"[?&]page=(\d+)", a.get("href", ""))
        if m:
            pages.append(int(m.group(1)))
    return max(pages)


def parse_cards(html: str, host: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    jobs, seen = [], set()
    for h in soup.find_all(["h2", "h3"]):
        a = h.find("a", href=re.compile(r"/job/"))
        if not a:
            continue
        m = re.search(r"/job/[a-z0-9\-]+/(\d+)", a.get("href", ""))
        if not m:
            continue
        job_id = m.group(1)
        if job_id in seen:
            continue
        seen.add(job_id)
        title = a.get_text(strip=True)
        href = a.get("href", "")
        url = href if href.startswith("http") else host + href
        card, company, company_url = h, "", ""
        for _ in range(6):
            card = card.parent
            if card is None:
                break
            comps = card.find_all("a", href=re.compile(r"/company/[a-z0-9\-]+$"))
            if comps:
                named = next((c for c in comps if c.get_text(strip=True)), comps[0])
                company = named.get_text(strip=True)
                churl = named.get("href", "")
                company_url = churl if churl.startswith("http") else host + churl
                break
        lines = [ln.strip() for ln in (card.get_text("\n", strip=True) if card else "").split("\n") if ln.strip()]
        salary = next((ln for ln in lines if re.search(r"\d+K\b|\$\d", ln)), "")
        seniority = next((ln for ln in lines if ln in
                          ("Senior level", "Mid level", "Entry level", "Expert/Leader", "Internship")), "")
        posted = next((ln for ln in lines if re.search(
            r"\b(Yesterday|Today|Days? Ago|Hours? Ago|Reposted)\b", ln)), "")
        remote = next((ln for ln in lines if ln in
                       ("Hybrid", "Remote", "In-Office", "In-Office or Remote", "Remote or Hybrid")), "")
        location = next((ln for ln in lines if re.search(
            r",\s*[A-Z]{2},\s*USA|Locations?$|United States|Massachusetts, USA", ln)
            and "Annually" not in ln), "")
        industries = next((ln for ln in lines if " \u2022 " in ln), "")
        meta = {salary, seniority, posted, remote, location, industries, title, company}
        cands = [ln for ln in lines if ln not in meta and len(ln) > 60 and not ln.startswith("Top Skills")]
        summary = max(cands, key=len) if cands else ""
        jobs.append(Job(job_id, title, company, url, company_url,
                        location, salary, seniority, posted, remote, industries, summary))
    return jobs


def scrape_list(url, pages, delay, session) -> list:
    host = _host(url)
    all_jobs, seen = [], set()
    r = session.get(_set_page(url, 1), headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    if pages == "auto":
        pages = detect_page_count(soup)
        print(f"detected {pages} pages", file=sys.stderr)
    pages = int(pages)
    for j in parse_cards(r.text, host):
        if j.job_id not in seen:
            seen.add(j.job_id); all_jobs.append(j)
    print(f"[list 1] {len(all_jobs)} jobs", file=sys.stderr)
    for page in range(2, pages + 1):
        time.sleep(delay)
        try:
            r = session.get(_set_page(url, page), headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"[list {page}] failed: {e}", file=sys.stderr)
            continue
        new = 0
        for j in parse_cards(r.text, host):
            if j.job_id not in seen:
                seen.add(j.job_id); all_jobs.append(j); new += 1
        print(f"[list {page}] +{new} ({len(all_jobs)} total)", file=sys.stderr)
    return all_jobs


def title_ok(title: str, include, exclude) -> bool:
    t = title.lower()
    if include and not any(k.lower() in t for k in include):
        return False
    if exclude and any(k.lower() in t for k in exclude):
        return False
    return True


def load_focus(config_path: str) -> dict:
    cfg = yaml.safe_load(open(config_path))
    focus = cfg.get("focus_keywords", {})
    return {area: [k.lower() for k in kws] for area, kws in focus.items()}


def fetch_detail(url: str, session):
    try:
        r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  detail fetch failed {url}: {e}", file=sys.stderr)
        return "", ""
    soup = BeautifulSoup(r.text, "html.parser")
    apply_url = ""
    for a in soup.find_all("a"):
        if a.get_text(strip=True).lower() in ("apply", "apply now") and a.get("href"):
            apply_url = a["href"]
            break
    full = soup.get_text("\n", strip=True)
    lo = 0
    m = re.search(r"Job Description:|Company Overview|Job Summary", full)
    if m:
        lo = m.start()
    hi = len(full)
    for marker in ("Read Full Description", "\nHQ\n", "Similar Jobs",
                   "What you need to know about the Boston Tech Scene"):
        i = full.find(marker, lo)
        if i != -1:
            hi = min(hi, i)
    return full[lo:hi], apply_url


def _match_kw(kw: str, low: str) -> bool:
    parts = re.split(r"[\s\-]+", kw)
    pat = r"[\s\-]+".join(re.escape(p) for p in parts)
    return re.search(r"(?<![a-z])" + pat + r"(?![a-z])", low) is not None


def classify(text: str, focus: dict):
    """
    Collapse focus areas into two buckets and return a category:
      - CNS  : neuroscience/CNS terms only
      - Gene : gene therapy OR gene editing terms only
      - Both : hits in CNS *and* Gene
      - ""   : no hits
    Returns (category, cns_hits, gene_hits).
    """
    low = text.lower()
    cns_kws = focus.get("neuroscience", [])
    gene_kws = focus.get("gene_therapy", []) + focus.get("gene_editing", [])

    cns_hits = sorted({kw for kw in cns_kws if _match_kw(kw, low)})
    gene_hits = sorted({kw for kw in gene_kws if _match_kw(kw, low)})

    if cns_hits and gene_hits:
        cat = "Both"
    elif cns_hits:
        cat = "CNS"
    elif gene_hits:
        cat = "Gene"
    else:
        cat = ""
    return cat, cns_hits, gene_hits


def load_seen(path: str) -> set:
    try:
        return set(json.load(open(path)))
    except (FileNotFoundError, ValueError):
        return set()


def save_seen(path: str, ids: set):
    json.dump(sorted(ids), open(path, "w"), indent=0)


ARCHIVE_FIELDS = [
    "first_seen", "job_id", "category", "title", "company", "location",
    "salary", "seniority", "remote", "posted", "cns_hits", "gene_hits",
    "url", "apply_url", "company_url", "industries", "summary",
]


def append_archive(path: str, jobs: list):
    """
    Append new job rows to a permanent CSV archive, deduped on job_id. Existing
    rows are never rewritten, so a job's first_seen date is preserved. Returns
    the number of rows added.
    """
    existing_ids = set()
    file_exists = os.path.exists(path)
    if file_exists:
        try:
            with open(path, newline="") as f:
                for row in csv.DictReader(f):
                    if row.get("job_id"):
                        existing_ids.add(row["job_id"])
        except (OSError, csv.Error):
            pass

    today = _dt.date.today().isoformat()
    added = 0
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ARCHIVE_FIELDS, extrasaction="ignore")
        if not file_exists or os.path.getsize(path) == 0:
            w.writeheader()
        for j in jobs:
            if j.job_id in existing_ids:
                continue
            row = asdict(j)
            row["first_seen"] = today
            row["cns_hits"] = ",".join(row.get("cns_hits") or [])
            row["gene_hits"] = ",".join(row.get("gene_hits") or [])
            w.writerow(row)
            existing_ids.add(j.job_id)
            added += 1
    return added


def build_email_html(jobs: list) -> str:
    """Group jobs under Both / Gene / CNS headers as an HTML digest."""
    order = ["Both", "Gene", "CNS"]
    label = {"Both": "CNS + Gene (top targets)", "Gene": "Gene therapy / editing", "CNS": "CNS / neuro"}
    parts = ["<h2>New Built In matches</h2>"]
    any_rows = False
    for cat in order:
        rows = [j for j in jobs if j.category == cat]
        if not rows:
            continue
        any_rows = True
        parts.append(f"<h3>{label[cat]} ({len(rows)})</h3><ul>")
        for j in rows:
            hits = []
            if j.cns_hits:
                hits.append("CNS: " + ", ".join(j.cns_hits))
            if j.gene_hits:
                hits.append("Gene: " + ", ".join(j.gene_hits))
            apply = f' &middot; <a href="{j.apply_url}">apply</a>' if j.apply_url else ""
            meta = " &middot; ".join(x for x in (j.company, j.location, j.salary) if x)
            parts.append(
                f'<li><a href="{j.url}"><b>{j.title}</b></a> &mdash; {meta}'
                f'<br><a href="{j.url}">view posting</a>{apply}'
                f'<br><small>ID: {j.job_id} | {" | ".join(hits)}</small></li>'
            )
        parts.append("</ul>")
    if not any_rows:
        return ""
    return "\n".join(parts)


def send_email(html: str, subject: str, addr_from: str, addr_to: str, app_password: str):
    msg = MIMEText(html, "html")
    msg["Subject"] = subject
    msg["From"] = addr_from
    msg["To"] = addr_to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(addr_from, app_password)
        s.sendmail(addr_from, [addr_to], msg.as_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--pages", default="auto")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    ap.add_argument("--config", default="company_config.yaml")
    ap.add_argument("--title-include", nargs="*", default=DEFAULT_TITLE_INCLUDE)
    ap.add_argument("--title-exclude", nargs="*", default=None)
    ap.add_argument("--no-detail", action="store_true")
    ap.add_argument("--focus-only", action="store_true",
                    help="keep only jobs categorized CNS / Gene / Both")
    ap.add_argument("--seen-file", default=None,
                    help="JSON file of already-reported job_ids; only NEW jobs "
                         "are reported and the file is updated after the run")
    ap.add_argument("--out", default="builtin_jobs.json")
    ap.add_argument("--csv", default=None,
                    help="snapshot CSV of THIS run only (overwritten each run)")
    ap.add_argument("--archive", default=None,
                    help="permanent CSV; new jobs are APPENDED (deduped on job_id), with a first_seen date. Grows over time.")
    ap.add_argument("--email-to", default=None,
                    help="if set, emails the digest here via Gmail SMTP "
                         "(needs --email-from and GMAIL_APP_PASSWORD env var)")
    ap.add_argument("--email-from", default=None)
    ap.add_argument("--email-empty", action="store_true",
                    help="send the email even when there are no new hits")
    args = ap.parse_args()

    session = requests.Session()
    focus = load_focus(args.config)
    print(f"loaded focus areas: {', '.join(focus)}", file=sys.stderr)

    seen = load_seen(args.seen_file) if args.seen_file else set()
    if args.seen_file:
        print(f"seen-file: {len(seen)} job_ids already reported", file=sys.stderr)

    jobs = scrape_list(args.url, args.pages, args.delay, session)
    print(f"\nscraped {len(jobs)} jobs", file=sys.stderr)

    kept = [j for j in jobs if title_ok(j.title, args.title_include, args.title_exclude)]
    print(f"title filter ({'/'.join(args.title_include)}): {len(jobs)} -> {len(kept)}", file=sys.stderr)

    # daily-report mode: drop jobs already reported BEFORE the costly detail fetch
    all_scraped_ids = {j.job_id for j in kept}
    if args.seen_file:
        before = len(kept)
        kept = [j for j in kept if j.job_id not in seen]
        print(f"new-only: {before} -> {len(kept)} (skipped {before-len(kept)} already seen)", file=sys.stderr)

    for i, j in enumerate(kept, 1):
        if args.no_detail:
            text = f"{j.title}\n{j.industries}\n{j.summary}"
        else:
            time.sleep(args.delay)
            body, apply_url = fetch_detail(j.url, session)
            j.apply_url = apply_url
            text = body or f"{j.title}\n{j.industries}\n{j.summary}"
        j.category, j.cns_hits, j.gene_hits = classify(text, focus)
        tag = j.category or "-"
        print(f"[{i}/{len(kept)}] {tag:<5} {j.company} - {j.title[:45]}", file=sys.stderr)

    if args.focus_only:
        before = len(kept)
        kept = [j for j in kept if j.category]
        print(f"focus-only: {before} -> {len(kept)}", file=sys.stderr)

    # sort: Both first, then Gene, then CNS, then uncategorized
    order = {"Both": 0, "Gene": 1, "CNS": 2, "": 3}
    kept.sort(key=lambda j: order.get(j.category, 3))

    json.dump([asdict(j) for j in kept], open(args.out, "w"), indent=2)
    print(f"\nwrote {len(kept)} jobs -> {args.out}")
    for cat in ("Both", "Gene", "CNS"):
        n = sum(1 for j in kept if j.category == cat)
        print(f"  {cat}: {n}")

    if args.csv:
        fields = list(asdict(kept[0]).keys()) if kept else ["job_id"]
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for j in kept:
                row = asdict(j)
                row["cns_hits"] = ",".join(row["cns_hits"])
                row["gene_hits"] = ",".join(row["gene_hits"])
                w.writerow(row)
        print(f"wrote {args.csv}")

    # update seen-file: add every title-passing job_id seen this run, so tomorrow
    # they're all treated as already-reported (report = only brand-new postings)
    if args.seen_file:
        save_seen(args.seen_file, seen | all_scraped_ids)
        print(f"updated {args.seen_file} ({len(seen | all_scraped_ids)} ids)", file=sys.stderr)

    if args.archive:
        added = append_archive(args.archive, kept)
        print(f"archive {args.archive}: +{added} new row(s)", file=sys.stderr)

    if args.email_to and args.email_from:
        pw = os.environ.get("GMAIL_APP_PASSWORD", "")
        if not pw:
            print("GMAIL_APP_PASSWORD not set; skipping email", file=sys.stderr)
        else:
            reportable = [j for j in kept if j.category]
            html = build_email_html(reportable)
            if not html and not args.email_empty:
                print("no new hits; email not sent (use --email-empty to override)", file=sys.stderr)
            else:
                if not html:
                    html = "<p>No new Built In matches today.</p>"
                subject = f"Built In: {len(reportable)} new match(es)" if reportable else "Built In: no new matches"
                send_email(html, subject, args.email_from, args.email_to, pw)
                print(f"emailed digest to {args.email_to}", file=sys.stderr)


if __name__ == "__main__":
    main()
