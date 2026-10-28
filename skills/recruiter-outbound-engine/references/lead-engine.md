# Stage 1: Lead engine

Goal: a saved campaign that runs every day. It finds companies that just posted roles the agency fills and fit the agency's ICP, finds the right person to email at each one, and appends them to the user's Google Sheet.

```
Blitz /v2/jobs/search  →  group by company, drop agencies/excluded/already-seen
   →  Blitz /v2/search/waterfall-icp-keyword (best decision maker, tiered)
   →  Blitz /v2/enrichment/email (verified work email)
   →  Google Sheet "Leads" tab (one row per contact)
```

## 1. Understand the offer and ICP

Have a conversation, not a form. Ask in two or three small batches and build on their answers. You need:

**The offer**
- What roles do they place? Get the actual job titles a client would post, e.g. "Senior Backend Engineer" or "Payroll Manager", not just "tech" or "finance". Ask for variants and seniority.
- Perm, contract or both? Typical fee or rate? What makes them different (niche, speed, a candidate pool, guarantees)? This feeds the copy later.
- Any proof: placements at notable companies, time-to-hire stats?

**The ICP: who the client is**
- Industries or sectors (e.g. SaaS, fintech, healthcare).
- Company size by headcount. Very large companies often have in-house talent teams. Very small ones may not pay agency fees.
- Geography: where the *role* is, and whether HQ matters.
- Remote, hybrid or on-site roles?
- Exclusions: current clients, competitors, blacklisted names. Also roles they don't touch, e.g. "no interns" or "no sales roles at engineering firms".

**Who to contact**
- Who usually signs off on an agency for these roles? The hiring manager (e.g. VP Engineering for engineers), talent or HR leadership, or the founder at small companies? Most agencies want the hiring manager first and talent second.
- Titles to avoid (assistants, coordinators, recruiters at large companies, etc.).
- How many contacts per company (1 is the default, 2 allows a parallel thread).

**Logistics**
- The Google Sheet URL to write to, and the tab name (default `Leads`).
- What time each day to run, and their timezone.
- Rough daily volume: how many new companies per day they can actually work (default 50).

Summarise the answers back in a short block and get a yes before building anything.

## 2. Build the signal

Turn the answers into Blitz filters. The job-search body has two parts:

```json
"signal": {
  "lookback_days": 1,
  "job": {
    "title": {"include": ["Backend Engineer", "Software Engineer", "Platform Engineer"],
              "exclude": ["intern", "graduate", "contract"]},
    "seniority": {"include": ["2-5", "5-10"]},
    "employment_type": {"include": ["FULL_TIME"]},
    "work_arrangement": {"include": ["Hybrid", "On-site", "Remote OK"]},
    "location": {"country_code": {"include": ["GB"]}}
  },
  "company": {
    "is_agency": false,
    "industry": {"include": ["Software Development", "Financial Services"]},
    "employee_count": {"min": 20, "max": 1000},
    "hq": {"country_code": {"include": ["GB"]}},
    "keywords": {"exclude": ["staffing", "recruitment"]}
  }
}
```

How the filters behave, and why they matter:
- `job.title.include` values are OR'd keyword matches, and every word of a value must appear in the title. Keep them short ("Backend Engineer", not "Senior Backend Software Engineer II"), or you'll miss most postings. Wrap a value in `[brackets]` for an exact title match.
- `job.ai_keywords` searches title, description and skills. It's useful for niche skill-based roles ("Kubernetes", "IFRS 17") where titles vary. It's broader, so preview it.
- `seniority` is years-of-experience bands: `0-2`, `2-5`, `5-10`, `10+`.
- `employment_type`: `FULL_TIME`, `PART_TIME`, `CONTRACTOR`, `TEMPORARY`, `INTERN`, ...
- `work_arrangement`: `On-site`, `Hybrid`, `Remote OK`, `Remote Solely`.
- `location` is where the job is. `company.hq` is where the company is based. Use whichever the user actually means.
- `company.industry` values must match Blitz's list exactly. Look them up in `references/blitz-industries.txt` with grep, and never guess a label.
- `company.size` (`1-10` … `10001+`) or `company.employee_count` `{min, max}` set headcount.
- `is_agency: false` is on by default in the script, because other agencies posting the same roles are competitors, not clients.
- `lookback_days`: use 1 for a daily run, so each run sees only yesterday's postings. The script also remembers every company it has processed, so a company isn't looked up twice within `recontact_after_days` (default 90).

### Decision-maker cascade

The waterfall endpoint tries each tier in order until it fills `max_per_company`. Build tiers from most to least preferred:

```json
"decision_maker": {
  "max_per_company": 1,
  "require_email": true,
  "cascade": [
    {"include_title": ["VP Engineering", "Head of Engineering", "Director of Engineering", "CTO"],
     "exclude_title": ["assistant", "intern"], "location": {"country_code": ["GB"]}},
    {"include_title": ["Engineering Manager", "Head of Platform"], "exclude_title": ["assistant"]},
    {"include_title": ["Head of Talent", "Talent Acquisition Manager", "Head of People"],
     "exclude_title": ["coordinator", "assistant"]},
    {"include_title": ["Founder", "Co-Founder", "CEO"]}
  ]
}
```

Title phrases match whole words in any order, with no synonyms, so include common variants ("Head of Engineering", "Engineering Director"). Up to 10 tiers. `location` per tier takes `country_code`, `continent` or `city` lists (max 10 values each). With `require_email: true`, contacts without a verified email are dropped. Leave it on: Instantly needs an email.

If the agency places several unrelated role families (e.g. engineers *and* finance), create one campaign per family, because each needs its own hiring-manager cascade. Give each its own slug, and the same sheet tab is fine.

### Save it

Write the campaign file with the Write tool to `~/.recruiter-outbound-engine/campaigns/<slug>.json`:

```json
{
  "slug": "uk-backend-eng",
  "name": "UK Backend Engineering",
  "offer": "Perm backend engineers for UK scale-ups, 18% fee, 2-week shortlist guarantee...",
  "icp_summary": "UK HQ SaaS/fintech, 20-1000 staff, hiring mid/senior backend engineers",
  "signal": { ... },
  "decision_maker": { ... },
  "exclude_companies": ["https://www.linkedin.com/company/current-client", "Competitor Ltd"],
  "limits": {"max_companies_per_run": 50, "max_job_pages": 5, "recontact_after_days": 90},
  "sheet": {"id": "<sheet id or url>", "tab": "Leads"},
  "schedule": {"time": "07:00", "timezone": "Europe/London"}
}
```

The `offer` and `icp_summary` are reused by the copy engine, so write them with some detail.

## 3. Present the signal and get confirmation

Run `roe.py preview --campaign <slug> --lookback-days 7 --limit 15`. Use a week of data so there is enough to judge. Show the user:
- How many matching postings there are, and a rough per-day figure (total ÷ 7).
- A short table: company, roles they're hiring, location, and a one-line note on what the company does (from the job summary).
- Your own read: does this look like their ICP? Point out companies that look wrong, such as agencies that slipped through, giant enterprises, or the wrong sector.

Ask them to confirm, or say what to change. Tweak the filters, save, and preview again until they're happy. Typical fixes: add title excludes, narrow the industries, adjust headcount, or switch from `title` to `ai_keywords` for niche skills.

Then show real contacts: `roe.py preview --campaign <slug> --lookback-days 7 --limit 5 --with-contacts 5`. This costs a few Blitz records. Show who it found and their titles, and check that these are the people the user wants to email. Adjust the cascade if not.

## 4. First run and schedule

1. Ask the user to share the sheet with the service-account email (from `roe.py check`). Then check access with `roe.py check --sheet <url>`.
2. First real run: `roe.py run --campaign <slug> --lookback-days 7`. A week of backlog gives them leads to work today. It creates the tab and headers if they're missing, and keeps the existing column order if the tab already exists.
3. Report the summary: companies found, leads written, companies where no contact or email was found.
4. Schedule it at the user's chosen time:
   - If a scheduled-task tool is available in this session (e.g. a "scheduled tasks" or cron tool), you can use it to run `python3 <skill-dir>/scripts/roe.py run --campaign <slug>` daily.
   - Otherwise use cron: `roe.py schedule --campaign <slug> --time 07:00` prints the line. Show it, explain that it runs on this computer at local time while the machine is awake, and only add `--install` after they say yes. Runs log to `~/.recruiter-outbound-engine/logs/`.
   - The daily run is a plain Python script. It needs neither Claude nor a browser, so it keeps working after this conversation ends.

## Sheet columns

`date_found, status, company_name, company_linkedin_url, company_domain, job_title, job_url, job_location, job_posted, open_matching_roles, job_summary, first_name, last_name, full_name, title, linkedin_url, email, person_location, dm_tier, campaign`

`status` starts as `new`. The copy engine marks rows it has drafted, and the user can use the column for their own pipeline. `open_matching_roles` > 1 is a strong signal ("hiring 3 backend engineers"), and the copy engine can use it. `dm_tier` is the cascade tier the contact came from (1 = most preferred).

## Troubleshooting

- `422` from Blitz: a filter value is invalid. Usually an industry label that isn't in the list, or more than 10 location values in a cascade tier. The error body names the field.
- Zero results: run the preview with `--lookback-days 30`. If it's still empty, loosen titles or industries one at a time to find the over-tight filter.
- Many companies without a contact: the cascade is too narrow. Add a talent tier and a founder tier.
- Sheets `403`: the sheet isn't shared with the service-account email, or the Sheets API isn't enabled in their Google Cloud project.
