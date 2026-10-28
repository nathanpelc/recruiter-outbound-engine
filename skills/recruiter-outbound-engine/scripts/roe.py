#!/usr/bin/env python3
"""Recruiter Outbound Engine CLI.

  setup            save API keys (prompts for any not given)
  check            test every key and print what each tool can see
  preview          run a campaign's job signal once and show matching companies
  run              daily pipeline: jobs -> ICP filter -> decision maker -> email -> Google Sheet
  schedule         print (or install) the cron line that runs `run` daily
  read-leads       load leads from a CSV, a Google Sheet link, or a sheet tab into JSON
  write-tab        append JSON records to a sheet tab (e.g. reviewed drafts)
  campaigns        list Instantly campaigns
  push             upload rendered leads to an Instantly campaign (needs --yes)

All output meant for Claude is JSON on stdout; progress goes to stderr.
"""
import argparse
import csv
import datetime as dt
import getpass
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402

LEAD_COLUMNS = [
    "date_found", "status", "company_name", "company_linkedin_url", "company_domain",
    "job_title", "job_url", "job_location", "job_posted", "open_matching_roles", "job_summary",
    "first_name", "last_name", "full_name", "title", "linkedin_url", "email", "person_location",
    "dm_tier", "campaign",
]


def out(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def err(msg):
    print(msg, file=sys.stderr, flush=True)


def need(cfg, key, label):
    if not cfg.get(key):
        raise SystemExit(f"No {label} saved yet. Run: roe.py setup")
    return cfg[key]


# ---------------------------------------------------------------- setup / check

def cmd_setup(a):
    cfg = lib.load_config()
    if a.stdin:
        cfg.update({k: v for k, v in json.load(sys.stdin).items() if v})
    fields = [("blitz_api_key", "Blitz API key", a.blitz_key),
              ("instantly_api_key", "Instantly API v2 key", a.instantly_key),
              ("google_service_account", "Path to Google service-account JSON", a.google_sa)]
    for key, label, given in fields:
        if given:
            cfg[key] = given
        elif not cfg.get(key) and sys.stdin.isatty():
            secret = key.endswith("_key")
            val = (getpass.getpass if secret else input)(f"{label}: ").strip()
            if val:
                cfg[key] = val
    if cfg.get("google_service_account"):
        cfg["google_service_account"] = str(Path(cfg["google_service_account"]).expanduser().resolve())
    lib.save_config(cfg)
    out({"saved_to": str(lib.CONFIG_PATH),
         "have": {k: bool(cfg.get(k)) for k, _, _ in fields}})


def cmd_check(a):
    cfg = lib.load_config()
    report = {}
    try:
        info = lib.Blitz(need(cfg, "blitz_api_key", "Blitz key")).key_info()
        report["blitz"] = {"ok": True, "details": info}
    except (lib.ApiError, SystemExit) as e:
        report["blitz"] = {"ok": False, "error": str(e)}
    try:
        camps = lib.Instantly(need(cfg, "instantly_api_key", "Instantly key")).list_campaigns()
        report["instantly"] = {"ok": True, "campaigns": len(camps)}
    except (lib.ApiError, SystemExit) as e:
        report["instantly"] = {"ok": False, "error": str(e)}
    try:
        sh = lib.Sheets(need(cfg, "google_service_account", "Google service account"))
        report["google_sheets"] = {"ok": True, "share_sheets_with": sh.email}
        if a.sheet:
            meta = sh.meta(lib.parse_sheet_id(a.sheet))
            report["google_sheets"]["sheet_title"] = meta["properties"]["title"]
    except (lib.ApiError, SystemExit, Exception) as e:
        report["google_sheets"] = {"ok": False, "error": str(e)}
    out(report)


# ---------------------------------------------------------------- lead engine

def _signal_query(camp, lookback_days=None):
    sig = camp["signal"]
    job = json.loads(json.dumps(sig.get("job", {})))
    company = json.loads(json.dumps(sig.get("company", {})))
    job["date_posted"] = {"last_days": int(lookback_days or sig.get("lookback_days", 1))}
    # Agencies posting the same roles are competitors, not clients.
    company.setdefault("is_agency", False)
    return job, company


def _excluded(camp):
    return {x.strip().lower().rstrip("/") for x in camp.get("exclude_companies", [])}


def collect_companies(blitz, camp, lookback_days=None, max_pages=None, seen=None):
    """Page through job postings and group them by company (most recent first)."""
    job, company = _signal_query(camp, lookback_days)
    limits = camp.get("limits", {})
    max_pages = max_pages or limits.get("max_job_pages", 5)
    excluded, seen = _excluded(camp), seen or {}
    companies, cursor, total = {}, None, None
    for page in range(max_pages):
        r = blitz.search_jobs(job, company, max_results=50, cursor=cursor)
        total = r.get("total_results", total)
        for j in r.get("results", []):
            url = (j.get("company_linkedin_url") or "").rstrip("/")
            name = (j.get("company_name") or "").strip()
            if not url or url.lower() in excluded or name.lower() in excluded or url in seen:
                continue
            c = companies.setdefault(url, {"company_name": name, "company_linkedin_url": url, "jobs": []})
            c["jobs"].append(j)
        cursor = r.get("cursor")
        err(f"jobs page {page + 1}: {len(r.get('results', []))} postings, {len(companies)} new companies")
        if not cursor:
            break
    return list(companies.values()), total


def _current_title(person, company_url):
    for e in person.get("experiences", []):
        if e.get("job_is_current") and (e.get("company_linkedin_url") or "").rstrip("/") == company_url:
            return e.get("job_title"), e.get("company_domain")
    return person.get("headline"), None


def _fmt_loc(loc):
    loc = loc or {}
    return ", ".join(x for x in [loc.get("city"), loc.get("state_code"), loc.get("country_code")] if x)


def build_rows(blitz, camp, company, today):
    dm = camp["decision_maker"]
    job = sorted(company["jobs"], key=lambda j: j.get("date_posted") or "", reverse=True)[0]
    r = blitz.decision_makers(company["company_linkedin_url"], dm["cascade"], dm.get("max_per_company", 1))
    rows = []
    for hit in r.get("results", []):
        p = hit.get("person", {})
        title, domain = _current_title(p, company["company_linkedin_url"])
        email = ""
        if p.get("linkedin_url"):
            e = blitz.work_email(p["linkedin_url"])
            email = e.get("email") if e.get("found") else ""
        if dm.get("require_email", True) and not email:
            continue
        rows.append({
            "date_found": today, "status": "new",
            "company_name": company["company_name"],
            "company_linkedin_url": company["company_linkedin_url"],
            "company_domain": domain or (email.split("@")[1] if email else ""),
            "job_title": job.get("title"), "job_url": job.get("url"),
            "job_location": _fmt_loc(job.get("location")), "job_posted": (job.get("date_posted") or "")[:10],
            "open_matching_roles": len(company["jobs"]), "job_summary": job.get("ai_summary"),
            "first_name": p.get("first_name"), "last_name": p.get("last_name"), "full_name": p.get("full_name"),
            "title": title, "linkedin_url": p.get("linkedin_url"), "email": email,
            "person_location": _fmt_loc(p.get("location")), "dm_tier": hit.get("icp"),
            "campaign": camp["slug"],
        })
    return rows


def cmd_preview(a):
    cfg = lib.load_config()
    camp = lib.load_campaign(a.campaign)
    blitz = lib.Blitz(need(cfg, "blitz_api_key", "Blitz key"))
    companies, total = collect_companies(blitz, camp, lookback_days=a.lookback_days, max_pages=1)
    sample = companies[:a.limit]
    result = {
        "total_matching_postings": total,
        "lookback_days": a.lookback_days or camp["signal"].get("lookback_days", 1),
        "companies_on_first_page": len(companies),
        "sample": [{"company": c["company_name"], "linkedin": c["company_linkedin_url"],
                    "roles": [j.get("title") for j in c["jobs"]],
                    "location": _fmt_loc(c["jobs"][0].get("location")),
                    "summary": c["jobs"][0].get("ai_summary")} for c in sample],
    }
    if a.with_contacts:
        today = dt.date.today().isoformat()
        result["sample_leads"] = [row for c in sample[:a.with_contacts] for row in build_rows(blitz, camp, c, today)]
    out(result)


def cmd_run(a):
    cfg = lib.load_config()
    camp = lib.load_campaign(a.campaign)
    blitz = lib.Blitz(need(cfg, "blitz_api_key", "Blitz key"))
    seen = lib.load_seen(a.campaign)
    recontact = int(camp.get("limits", {}).get("recontact_after_days", 90))
    cutoff = (dt.date.today() - dt.timedelta(days=recontact)).isoformat()
    seen = {k: v for k, v in seen.items() if v.get("date", "") >= cutoff}

    companies, total = collect_companies(blitz, camp, lookback_days=a.lookback_days, seen=seen)
    max_companies = a.max_companies or camp.get("limits", {}).get("max_companies_per_run", 50)
    today = dt.date.today().isoformat()
    rows, no_contact, errors = [], 0, []
    for c in companies[:max_companies]:
        try:
            new = build_rows(blitz, camp, c, today)
        except lib.ApiError as e:
            errors.append({"company": c["company_name"], "error": str(e)[:300]})
            continue
        rows.extend(new)
        no_contact += 0 if new else 1
        # Mark checked companies too, so tomorrow's run doesn't pay to look them up again.
        seen[c["company_linkedin_url"]] = {"date": today, "leads": len(new)}
        err(f"{c['company_name']}: {len(new)} lead(s)")

    summary = {"date": today, "postings_matched": total, "new_companies": len(companies),
               "companies_processed": min(len(companies), max_companies), "leads": len(rows),
               "companies_without_contact": no_contact, "errors": errors[:10]}
    if a.dry_run:
        summary["rows"] = rows
        out(summary)
        return
    sheet = camp.get("sheet", {})
    if rows:
        sh = lib.Sheets(need(cfg, "google_service_account", "Google service account"))
        sh.append_dicts(sheet["id"], sheet.get("tab", "Leads"), rows, LEAD_COLUMNS)
    lib.save_seen(a.campaign, seen)
    lib.log_run(a.campaign, json.dumps({k: v for k, v in summary.items() if k != "errors"}))
    out(summary)


def cmd_schedule(a):
    camp = lib.load_campaign(a.campaign)
    hh, mm = (a.time or camp.get("schedule", {}).get("time", "07:00")).split(":")
    script = Path(__file__).resolve()
    log = lib.HOME / "logs" / f"{a.campaign}.cron.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    line = (f"{int(mm)} {int(hh)} * * * {shlex.quote(sys.executable)} {shlex.quote(str(script))} "
            f"run --campaign {shlex.quote(a.campaign)} >> {shlex.quote(str(log))} 2>&1  # roe:{a.campaign}")
    if not a.install:
        out({"cron_line": line, "note": "Times are this computer's local time. The computer must be awake."})
        return
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    kept = [l for l in current.splitlines() if not l.endswith(f"# roe:{a.campaign}")]
    subprocess.run(["crontab", "-"], input="\n".join(kept + [line]) + "\n", text=True, check=True)
    out({"installed": line})


# ---------------------------------------------------------------- copy engine

def cmd_read_leads(a):
    src = a.source
    if os.path.exists(os.path.expanduser(src)):
        with open(os.path.expanduser(src), newline="", encoding="utf-8-sig") as f:
            records = list(csv.DictReader(f))
    else:
        cfg = lib.load_config()
        if cfg.get("google_service_account"):
            sh = lib.Sheets(cfg["google_service_account"])
            sid = lib.parse_sheet_id(src)
            tabs = sh.tab_titles(sid)
            gid = lib.parse_gid(src)
            tab = a.tab or (tabs.get(gid) if gid is not None else None) or next(iter(tabs.values()))
            records = sh.read_dicts(sid, tab)
        else:
            records = lib.read_public_sheet_csv(src)
    if a.only_with_email:
        records = [r for r in records if (r.get("email") or r.get("Email") or "").strip()]
    if a.out:
        Path(a.out).write_text(json.dumps(records, indent=2, ensure_ascii=False))
    out({"count": len(records), "columns": list(records[0].keys()) if records else [],
         "first_3": records[:3], "saved_to": a.out})


def cmd_write_tab(a):
    cfg = lib.load_config()
    records = json.loads(Path(a.file).read_text())
    headers = []
    for r in records:
        headers += [k for k in r if k not in headers]
    sh = lib.Sheets(need(cfg, "google_service_account", "Google service account"))
    sh.append_dicts(lib.parse_sheet_id(a.sheet), a.tab, records, headers)
    out({"appended": len(records), "tab": a.tab})


def cmd_campaigns(a):
    cfg = lib.load_config()
    camps = lib.Instantly(need(cfg, "instantly_api_key", "Instantly key")).list_campaigns()
    status = {0: "draft", 1: "active", 2: "paused", 3: "completed"}
    out([{"id": c["id"], "name": c.get("name"), "status": status.get(c.get("status"), c.get("status"))}
         for c in camps])


STANDARD = {"email", "first_name", "last_name", "company_name", "job_title", "website", "phone", "personalization"}


def cmd_push(a):
    records = json.loads(Path(a.file).read_text())
    leads, skipped = [], 0
    for r in records:
        if not (r.get("email") or "").strip():
            skipped += 1
            continue
        lead = {k: r[k] for k in STANDARD if r.get(k)}
        custom = dict(r.get("custom_variables") or {})
        custom.update({k: v for k, v in r.items() if k not in STANDARD and k != "custom_variables" and v not in (None, "")})
        if custom:
            lead["custom_variables"] = {k: (v if isinstance(v, (str, int, float, bool)) else json.dumps(v))
                                        for k, v in custom.items()}
        leads.append(lead)
    if not a.yes:
        out({"would_upload": len(leads), "skipped_no_email": skipped, "campaign_id": a.campaign_id,
             "example": leads[:1], "note": "Re-run with --yes once the user has approved the upload."})
        return
    cfg = lib.load_config()
    res = lib.Instantly(need(cfg, "instantly_api_key", "Instantly key")).add_leads(
        a.campaign_id, leads, skip_if_in_workspace=not a.allow_duplicates)
    out({"uploaded_batches": len(res), "sent": len(leads), "skipped_no_email": skipped, "responses": res})


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)

    x = s.add_parser("setup")
    x.add_argument("--blitz-key")
    x.add_argument("--instantly-key")
    x.add_argument("--google-sa", help="path to the service-account JSON file")
    x.add_argument("--stdin", action="store_true", help="read a JSON object of keys from stdin")
    x.set_defaults(f=cmd_setup)

    x = s.add_parser("check")
    x.add_argument("--sheet", help="sheet URL/ID to test access to")
    x.set_defaults(f=cmd_check)

    x = s.add_parser("preview")
    x.add_argument("--campaign", required=True)
    x.add_argument("--limit", type=int, default=15)
    x.add_argument("--lookback-days", type=int, help="widen the window for the preview (default: campaign setting)")
    x.add_argument("--with-contacts", type=int, default=0, help="also find decision makers for the first N companies")
    x.set_defaults(f=cmd_preview)

    x = s.add_parser("run")
    x.add_argument("--campaign", required=True)
    x.add_argument("--max-companies", type=int)
    x.add_argument("--lookback-days", type=int)
    x.add_argument("--dry-run", action="store_true", help="don't write to the sheet or mark companies seen")
    x.set_defaults(f=cmd_run)

    x = s.add_parser("schedule")
    x.add_argument("--campaign", required=True)
    x.add_argument("--time", help="HH:MM, local time")
    x.add_argument("--install", action="store_true")
    x.set_defaults(f=cmd_schedule)

    x = s.add_parser("read-leads")
    x.add_argument("--source", required=True, help="CSV path or Google Sheet URL/ID")
    x.add_argument("--tab")
    x.add_argument("--only-with-email", action="store_true")
    x.add_argument("--out")
    x.set_defaults(f=cmd_read_leads)

    x = s.add_parser("write-tab")
    x.add_argument("--sheet", required=True)
    x.add_argument("--tab", required=True)
    x.add_argument("--file", required=True)
    x.set_defaults(f=cmd_write_tab)

    x = s.add_parser("campaigns")
    x.set_defaults(f=cmd_campaigns)

    x = s.add_parser("push")
    x.add_argument("--campaign-id", required=True)
    x.add_argument("--file", required=True, help="JSON list of rendered leads")
    x.add_argument("--yes", action="store_true")
    x.add_argument("--allow-duplicates", action="store_true", help="don't skip leads already in the workspace")
    x.set_defaults(f=cmd_push)

    a = p.parse_args()
    try:
        a.f(a)
    except lib.ApiError as e:
        out({"error": str(e), "status": e.status})
        sys.exit(1)


if __name__ == "__main__":
    main()
