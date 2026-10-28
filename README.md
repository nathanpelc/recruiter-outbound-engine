# Recruiter Outbound Engine

A Claude skill that runs outbound business development for a recruitment agency, from finding companies that are hiring to loading personalised emails into Instantly.

**Stage 1: Lead engine.** Claude asks about your offer and ideal client (ICP), turns the roles you place into a daily job-posting signal on [Blitz API](https://blitz-api.ai), finds the hiring manager and their verified work email at each matching company, and adds them to your Google Sheet every day at a time you choose.

**Stage 2: Copy engine.** Give Claude a lead sheet or CSV. It drafts an email template, you edit it and decide what each variable is and where it comes from, Claude tests it on 10 leads for your feedback, and once you approve it uploads the leads with their personalised variables to an Instantly campaign.

```
Blitz job postings → ICP filter → decision maker → work email → Google Sheet (daily)
Google Sheet / CSV → template → variables → 10-lead test → your approval → Instantly
```

## What you need

| Tool | What to get |
|---|---|
| [Blitz API](https://blitz-api.ai) | An API key (Dashboard → API keys) |
| [Instantly](https://instantly.ai) | An **API v2** key (Settings → Integrations → API keys) with campaign read and lead create access |
| Google Sheets | A Google Cloud **service account** JSON key file. Claude walks you through creating one on the first run. |
| Python | 3.9 or newer, plus `pip3 install -r requirements.txt` |

The skill asks for these on the first run and saves them to `~/.recruiter-outbound-engine/config.json` on your computer, readable only by you. They are never stored in this repository.

## Install

### Claude Code (recommended)

```bash
claude plugin marketplace add nathanpelc/recruiter-outbound-engine
```

```bash
claude plugin install recruiter-outbound-engine@recruiter-outbound-engine
```

You can also run these inside Claude Code as `/plugin marketplace add nathanpelc/recruiter-outbound-engine`, then `/plugin install recruiter-outbound-engine@recruiter-outbound-engine`.

### Claude Code, manual copy

```bash
git clone https://github.com/nathanpelc/recruiter-outbound-engine.git
```

```bash
cp -r recruiter-outbound-engine/skills/recruiter-outbound-engine ~/.claude/skills/
```

No git? Unzip `dist/recruiter-outbound-engine.zip` into `~/.claude/skills/` instead.

### Claude.ai / Claude desktop app

Download [`recruiter-outbound-engine.skill`](https://github.com/nathanpelc/recruiter-outbound-engine/raw/main/dist/recruiter-outbound-engine.skill) from the `dist/` folder, then upload it under **Settings → Capabilities → Skills**. The daily schedule needs a computer that can run Python, so Claude Code is the better fit for Stage 1.

Then install the Python dependencies once:

```bash
pip3 install google-auth requests
```

## Use

Just ask Claude, for example:

- "Set up the recruiter outbound engine."
- "Find me companies hiring backend engineers in the UK every morning."
- "Here's my lead sheet, write the copy and get it into Instantly: https://docs.google.com/spreadsheets/d/..."

On the first run Claude:

1. Asks for your three keys and tests them.
2. Interviews you on your offer, ICP and who to contact.
3. Shows a preview of the job signal (real companies hiring right now) and refines it until you confirm.
4. Shows sample decision makers, runs the first batch into your sheet, and schedules the daily run.

Later runs reuse your saved settings.

### Good to know

- **Share your Google Sheet** with the service-account email the skill shows you, as Editor. Without this, nothing can be written.
- **Blitz charges per record.** The skill previews before spending, and never looks up the same company twice within 90 days.
- **Nothing is uploaded to Instantly without your explicit yes.** Upload into a paused or draft campaign, then activate it in Instantly once you've checked the sequence.
- **The daily run** is a plain Python script on a cron schedule. It doesn't need Claude open, but the computer has to be awake.

## What's inside

```
skills/recruiter-outbound-engine/
├── SKILL.md                    # entry point: key setup + which stage to run
├── references/
│   ├── lead-engine.md          # Stage 1: ICP interview, Blitz signal, scheduling
│   ├── copy-engine.md          # Stage 2: template, variables, 10-lead test, upload
│   └── blitz-industries.txt    # exact industry labels Blitz accepts
└── scripts/
    ├── roe.py                  # CLI: setup, check, preview, run, schedule, read-leads, push…
    └── lib.py                  # Blitz, Instantly and Google Sheets clients
```

You can also run the CLI yourself:

```bash
python3 skills/recruiter-outbound-engine/scripts/roe.py --help
```

## Saved data

Everything the engine remembers lives in `~/.recruiter-outbound-engine/` (you can change the location with `ROE_HOME`):

| Path | Contents |
|---|---|
| `config.json` | API keys and the service-account path (chmod 600) |
| `campaigns/<slug>.json` | Offer, ICP, signal filters, decision-maker tiers, sheet, schedule |
| `campaigns/<slug>.copy.json` | Email template and variable rules |
| `state/<slug>.seen.json` | Companies already processed |
| `logs/` | One line per daily run |

## License

MIT
