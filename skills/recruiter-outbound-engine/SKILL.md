---
name: recruiter-outbound-engine
description: Recruiter Outbound Engine. End-to-end BD for recruitment agencies on Blitz API, Google Sheets and Instantly. Stage 1 (lead engine) collects API keys, interviews the recruiter on offer and ICP, builds a daily Blitz job-posting signal for the roles they place, finds the hiring manager plus verified email at each matching company, and appends them to their Google Sheet on a schedule. Stage 2 (copy engine) reads a lead sheet or CSV, drafts an email template, has the recruiter define variables and how to research them, tests on 10 leads for feedback, then uploads approved leads to Instantly. Use this whenever a recruiter or agency owner mentions the recruiter outbound engine, Blitz API, job-posting or hiring signals, daily lead lists of companies hiring, finding hiring managers, writing outbound or cold email copy for recruitment BD, personalising emails from a lead sheet, or pushing leads into Instantly, even if they don't name the skill.
compatibility: Python 3.9+, `pip3 install google-auth requests`. Needs a Blitz API key, an Instantly API v2 key, and a Google Cloud service-account JSON key.
---

# Recruiter Outbound Engine

A recruitment agency's best BD signal is a company that has just posted a job the agency could fill. This skill turns that signal into a daily lead list, then turns the list into personalised outbound in Instantly.

There are two stages. Work out which one the user wants:

| User says | Go to |
|---|---|
| First run, "set up", "find leads", "companies hiring", "daily list", "change my ICP/signal" | **Stage 1: Lead engine** → read `references/lead-engine.md` |
| "Write copy", "here's my sheet/CSV", "draft emails", "upload to Instantly" | **Stage 2: Copy engine** → read `references/copy-engine.md` |
| Unsure | Ask which stage, in one line. Recommend Stage 1 if no campaign exists yet. |

Both stages start with the same step 0.

## Step 0: Keys (every run, takes seconds after the first)

All state lives in `~/.recruiter-outbound-engine/` (see the header of `scripts/lib.py` for the layout). The CLI is `scripts/roe.py`; run it as `python3 <skill-dir>/scripts/roe.py <command>`. Every command prints JSON.

1. Run `roe.py check`. If all three tools report `ok`, move on without bothering the user.
2. If any key is missing or failing, ask for **only the missing ones**, and explain where to get each:
   - **Blitz API key**: Blitz dashboard → API keys.
   - **Instantly API key**: Instantly → Settings → Integrations → API keys. It must be a **v2** key (v1 keys stopped working in January 2026). Scopes: campaigns read, leads create.
   - **Google Sheets**: a plain Google API key can't write to a sheet, so the engine uses a *service account*. This works unattended at 7am with nobody logged in. Walk them through it: Google Cloud Console → create a project → enable "Google Sheets API" → IAM → Service accounts → create one → Keys → Add key → JSON. That downloads a file. Ask for the file's path, not its contents.
3. Save the keys. The safest way is for the user to run `python3 <skill-dir>/scripts/roe.py setup` in their own terminal: it prompts with hidden input, so the keys never go through the chat. If they would rather paste keys into the chat, save them with `roe.py setup --blitz-key ... --instantly-key ... --google-sa ...`. Never repeat a key back in your replies.
4. Run `roe.py check` again. It prints `share_sheets_with`, the service account's email. The user must share their Google Sheet with that email as **Editor**, or nothing can be written. Tell them this plainly, because it's the step people miss.
5. If `google-auth` is missing, run `pip3 install google-auth requests`.

## Ground rules for both stages

- **Show before spending.** Blitz charges per record. Preview a signal before turning on a daily run, and use `--dry-run` or small limits while tuning.
- **Nothing reaches a prospect without a clear yes.** Uploading to Instantly can start emails going out if the campaign is active. Always show the final count and the campaign name, and get an explicit "yes, upload" before `push --yes`. Recommend uploading into a paused or draft campaign.
- **Never invent facts about a lead.** Personalisation that's wrong is worse than none. If a variable can't be found from a real source, use the user's fallback text or flag the lead.
- **Remember decisions.** Campaign config, the template and the variable definitions are saved as JSON so the next run doesn't repeat the interview. On later runs, read them and confirm with "same as last time?" instead of asking again.

## When finished

Recap in plain language what now exists: the campaign config, the sheet it writes to, when it runs, how many leads so far, and what was uploaded to which Instantly campaign. Then point to the next stage. After Stage 1, that's "send me the sheet when you want copy written." After Stage 2, it's "check the campaign in Instantly and activate it when ready."
