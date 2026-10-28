# Stage 2: Copy engine

Goal: turn a list of leads into personalised emails the recruiter has approved, and load them into an Instantly campaign.

```
read leads → draft template → user edits + defines variables → test on 10 leads
   → feedback loop → render all leads → user approves → push to Instantly
```

## 1. Read the leads

The user gives a Google Sheet link, a sheet ID, or a CSV path (or uploads a CSV into the chat; save it to a file first).

```
roe.py read-leads --source "<url or path>" [--tab Leads] --only-with-email --out <workdir>/leads.json
```

The script reads private sheets through the service account (the sheet must be shared with it) and public sheets through CSV export. It prints the row count, the column names and three sample rows. Tell the user how many leads came in and which columns you'll be able to use.

If the sheet came from Stage 1, the columns are already known (see `lead-engine.md`), and the campaign file has the offer and ICP: read `~/.recruiter-outbound-engine/campaigns/<slug>.json`, using the slug in the sheet's `campaign` column. If it's an outside list, ask briefly what the offer is, unless the conversation already says.

Skip rows whose `status` is already `drafted` or `uploaded`, unless the user asks to redo them.

## 2. Draft a first template

Write one email (subject + body) the user can react to. A good first draft for recruitment BD:

- **Under ~90 words.** Hiring managers skim on a phone.
- **Opens on their signal**, not on you. They posted a role, and the email says so specifically: role title, maybe how long it's been open, maybe how many similar roles.
- **One line of relevance or proof**: a similar placement, the candidate pool, time-to-shortlist.
- **A soft, specific ask**: "worth sending over 2-3 profiles?" works better than "book a 30-min call".
- **No fake familiarity**: no "hope you're well", no "I came across your profile".

Use `{{double_braces}}` for every variable. Use Instantly's built-in names where they exist (`{{firstName}}`, `{{companyName}}`) and snake_case for custom ones (`{{role_hook}}`). Present it like:

```
Subject: {{job_title}} hire at {{companyName}}

Hi {{firstName}},

Saw {{companyName}} is hiring a {{job_title}}. {{role_hook}}

We've placed {{proof_point}}, and I have {{candidate_angle}}.

Worth me sending over 2-3 anonymised profiles?

{{sender_name}}
```

Then ask the user to edit it freely: wording, structure, their sign-off. Also ask for any follow-up steps they want in the sequence.

## 3. Define the variables with the user

For each `{{variable}}` in their edited template, agree on three things and write them in a small table back to the user:

| Variable | Source | Instruction / mapping | Fallback |
|---|---|---|---|
| `firstName` | column `first_name` | direct | "there" |
| `job_title` | column `job_title` | tidy it: remove location, IDs, and "(m/f/d)" | skip lead |
| `role_hook` | AI from `job_summary` + `job_url` | one sentence on what makes this role hard to fill, in plain words, no flattery | "Roles like this are tough to fill right now." |
| `proof_point` | static | "3 senior backend engineers for London fintechs this year" | — |

Source types:
- **column**: copied from a sheet column, with optional light cleanup.
- **static**: the same text for every lead (sender name, proof point).
- **ai**: you research and write it per lead. Agree which sources you may use: sheet columns (`job_summary`, `open_matching_roles`, `title`), the job posting URL, the company's website (`company_domain`), or a web search. Agree on a length and tone rule.

The fallback matters. It decides what happens when the research comes up empty: use a safe generic line, or skip the lead entirely. Never fill the gap with a guess.

Save the result with Write to `~/.recruiter-outbound-engine/campaigns/<slug>.copy.json`:

```json
{
  "subject": "...", "body": "...", "follow_ups": [{"delay_days": 3, "body": "..."}],
  "variables": {
    "role_hook": {"source": "ai", "inputs": ["job_summary", "job_url"],
                  "instruction": "...", "max_words": 25, "fallback": "..."},
    "firstName": {"source": "column", "column": "first_name", "fallback": "there"}
  },
  "sender_name": "..."
}
```

Use the Stage 1 campaign slug when there is one. For an outside list, ask for a short name.

## 4. Test on 10 leads

Pick 10 leads that vary: different companies, titles and seniority, and some with thin data. An easy sample hides problems. For each one, fill every variable using its agreed source, then render the full email.

Show the results as a compact review. For each lead, show the name, title, company and role, then the rendered subject and body, with the AI-written parts **bolded** so the user can judge them quickly. Under each one, note which sources you used and whether a fallback fired.

Ask for feedback on specifics: tone, length, whether the hooks feel true, whether any lead should have been skipped. Change the template, the variable instructions or the fallbacks to match, save the copy file again, and re-run the same 10 so the user can compare. Repeat until they say it's good. Usually one or two rounds.

## 5. Render everything

Run the approved setup over all remaining leads. For lists over ~50, work in batches of about 25. If subagents are available, hand each batch to one, along with the copy file and the variable rules, so they run in parallel. Then merge the results and spot-check a few from each batch.

Write the rendered leads to `<workdir>/rendered.json` as a list:

```json
[{
  "email": "jane@acme.com", "first_name": "Jane", "last_name": "Doe",
  "company_name": "Acme", "job_title": "VP Engineering", "website": "acme.com",
  "custom_variables": {
    "role_title": "Senior Backend Engineer", "role_hook": "...", "proof_point": "...",
    "subject_line": "...", "email_body": "..."
  }
}]
```

Notes:
- Instantly's `job_title` field is the **lead's** job title. Put the advertised role in a custom variable (e.g. `role_title`).
- Include both the individual variables and the fully rendered `subject_line` / `email_body`. The user can then set their Instantly step to `{{subject_line}}` / `{{email_body}}`, or rebuild the template there from the variables. Tell them both options.
- Keep a list of leads you skipped (with the reason), and report it.

Optionally, save the drafts to the sheet so there's a record: `roe.py write-tab --sheet <url> --tab "Drafts" --file <workdir>/rendered_flat.json`. Use flat records here, with the variables as columns.

## 6. Upload to Instantly (only with a clear yes)

1. `roe.py campaigns` lists their Instantly campaigns. Ask which one, or ask them to create one in Instantly first. Leads can't be added to a campaign that doesn't exist. Point out if the chosen campaign is **active**, because new leads may start receiving emails right away. A draft or paused campaign is safer.
2. Dry run: `roe.py push --campaign-id <id> --file <workdir>/rendered.json`. This uploads nothing. It prints the count and an example lead.
3. Tell the user exactly what's about to happen, e.g. "Upload 143 leads to 'UK Backend Eng – Oct' (paused); 6 skipped with no email". Wait for a yes.
4. `roe.py push --campaign-id <id> --file <workdir>/rendered.json --yes`. Leads already in their Instantly workspace are skipped by default, so a lead isn't emailed twice by two campaigns. `--allow-duplicates` turns that off; only use it if the user asks.
5. Report what Instantly returned. If the leads came from a Stage 1 sheet, tell the user they may want to set `status` to `uploaded` for those rows.
6. Remind them to check the sequence in Instantly uses the variables, and to activate the campaign when ready.
