"""Shared clients and storage for the Recruiter Outbound Engine.

Everything the engine remembers lives in ~/.recruiter-outbound-engine (override
with ROE_HOME):
  config.json            API keys + Google service-account path (chmod 600)
  campaigns/<slug>.json  offer, ICP, signal filters, decision-maker cascade, sheet
  campaigns/<slug>.copy.json  email template + variable definitions
  state/<slug>.seen.json companies already processed (dedupe across days)
  logs/<slug>.log        one line per daily run
"""
import csv
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path(os.environ.get("ROE_HOME", Path.home() / ".recruiter-outbound-engine"))
CONFIG_PATH = HOME / "config.json"


# ---------------------------------------------------------------- storage

def _write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    os.chmod(path, 0o600)


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def save_config(cfg):
    _write_private(CONFIG_PATH, cfg)


def campaign_path(slug, suffix=""):
    return HOME / "campaigns" / f"{slug}{suffix}.json"


def load_campaign(slug):
    p = campaign_path(slug)
    if not p.exists():
        raise SystemExit(f"No campaign '{slug}'. Expected {p}")
    return json.loads(p.read_text())


def save_campaign(slug, data):
    _write_private(campaign_path(slug), data)


def load_seen(slug):
    p = HOME / "state" / f"{slug}.seen.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_seen(slug, seen):
    _write_private(HOME / "state" / f"{slug}.seen.json", seen)


def log_run(slug, line):
    p = HOME / "logs" / f"{slug}.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")


# ---------------------------------------------------------------- http

class ApiError(Exception):
    def __init__(self, status, body, url):
        super().__init__(f"HTTP {status} from {url}: {body[:500]}")
        self.status = status
        self.body = body


def http(method, url, headers=None, body=None, retries=4):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        hdrs.setdefault("Content-Type", "application/json")
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            # Rate limits and transient server errors are worth a backoff; anything
            # else (bad key, bad filter) will fail the same way again.
            if (e.code == 429 or e.code >= 500) and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise ApiError(e.code, text, url)
        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise


# ---------------------------------------------------------------- Blitz

class Blitz:
    BASE = "https://api.blitz-api.ai"

    def __init__(self, key):
        self.headers = {"x-api-key": key}

    def _post(self, path, body):
        return http("POST", self.BASE + path, self.headers, body)

    def key_info(self):
        return http("GET", self.BASE + "/v2/account/key-info", self.headers)

    def search_jobs(self, job, company, max_results=50, cursor=None):
        body = {"job": job, "company": company, "max_results": max_results}
        if cursor:
            body["cursor"] = cursor
        return self._post("/v2/jobs/search", body)

    def decision_makers(self, company_linkedin_url, cascade, max_results=1):
        return self._post("/v2/search/waterfall-icp-keyword", {
            "company_linkedin_url": company_linkedin_url,
            "cascade": cascade,
            "max_results": max_results,
        })

    def work_email(self, person_linkedin_url):
        return self._post("/v2/enrichment/email", {"person_linkedin_url": person_linkedin_url})


# ---------------------------------------------------------------- Instantly

class Instantly:
    BASE = "https://api.instantly.ai/api/v2"

    def __init__(self, key):
        self.headers = {"Authorization": f"Bearer {key}"}

    def list_campaigns(self):
        out, cursor = [], None
        while True:
            q = {"limit": 100}
            if cursor:
                q["starting_after"] = cursor
            r = http("GET", f"{self.BASE}/campaigns?{urllib.parse.urlencode(q)}", self.headers)
            out.extend(r.get("items", []))
            cursor = r.get("next_starting_after")
            if not cursor:
                return out

    def add_leads(self, campaign_id, leads, skip_if_in_workspace=True):
        results = []
        for i in range(0, len(leads), 1000):  # API max per request
            results.append(http("POST", f"{self.BASE}/leads/add", self.headers, {
                "campaign_id": campaign_id,
                "leads": leads[i:i + 1000],
                "skip_if_in_workspace": skip_if_in_workspace,
            }))
        return results


# ---------------------------------------------------------------- Google Sheets

def parse_sheet_id(url_or_id):
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url_or_id)
    return m.group(1) if m else url_or_id.strip()


def parse_gid(url):
    m = re.search(r"[#&?]gid=(\d+)", url)
    return int(m.group(1)) if m else None


class Sheets:
    """Sheets REST API authenticated as a Google service account.

    The user shares their sheet with the service account's email (Editor), which
    is the only way an unattended daily job can write to it without a browser.
    """
    BASE = "https://sheets.googleapis.com/v4/spreadsheets"

    def __init__(self, service_account_path):
        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request
        except ImportError:
            raise SystemExit("Missing dependency: run  pip3 install google-auth requests")
        self._Request = Request
        self.creds = service_account.Credentials.from_service_account_file(
            os.path.expanduser(service_account_path),
            scopes=["https://www.googleapis.com/auth/spreadsheets"])

    @property
    def email(self):
        return self.creds.service_account_email

    def _h(self):
        if not self.creds.valid:
            self.creds.refresh(self._Request())
        return {"Authorization": f"Bearer {self.creds.token}"}

    def meta(self, sheet_id):
        return http("GET", f"{self.BASE}/{sheet_id}?fields=properties.title,sheets.properties", self._h())

    def tab_titles(self, sheet_id):
        return {s["properties"]["sheetId"]: s["properties"]["title"] for s in self.meta(sheet_id)["sheets"]}

    def get_values(self, sheet_id, a1):
        r = http("GET", f"{self.BASE}/{sheet_id}/values/{urllib.parse.quote(a1)}", self._h())
        return r.get("values", [])

    def ensure_tab(self, sheet_id, tab, headers):
        if tab not in self.tab_titles(sheet_id).values():
            http("POST", f"{self.BASE}/{sheet_id}:batchUpdate", self._h(),
                 {"requests": [{"addSheet": {"properties": {"title": tab}}}]})
        existing = self.get_values(sheet_id, f"'{tab}'!1:1")
        if not existing:
            self.append_rows(sheet_id, tab, [headers])
            return headers
        # Keep the user's column order; add any new columns we need at the end.
        current = existing[0]
        missing = [h for h in headers if h not in current]
        if missing:
            current = current + missing
            a1 = urllib.parse.quote(f"'{tab}'!1:1")
            http("PUT", f"{self.BASE}/{sheet_id}/values/{a1}?valueInputOption=RAW",
                 self._h(), {"values": [current]})
        return current

    def append_rows(self, sheet_id, tab, rows):
        if not rows:
            return {}
        a1 = urllib.parse.quote(f"'{tab}'!A1")
        return http("POST", f"{self.BASE}/{sheet_id}/values/{a1}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS",
                    self._h(), {"values": rows})

    def append_dicts(self, sheet_id, tab, records, headers):
        cols = self.ensure_tab(sheet_id, tab, headers)
        return self.append_rows(sheet_id, tab, [[_cell(r.get(c)) for c in cols] for r in records])

    def read_dicts(self, sheet_id, tab):
        values = self.get_values(sheet_id, f"'{tab}'")
        if not values:
            return []
        head = values[0]
        return [dict(zip(head, row + [""] * (len(head) - len(row)))) for row in values[1:] if any(row)]


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return v


def read_public_sheet_csv(url):
    """Fallback for sheets shared as 'anyone with the link can view'."""
    gid = parse_gid(url)
    export = f"https://docs.google.com/spreadsheets/d/{parse_sheet_id(url)}/export?format=csv"
    if gid is not None:
        export += f"&gid={gid}"
    with urllib.request.urlopen(export, timeout=60) as r:
        text = r.read().decode("utf-8-sig")
    if text.lstrip().startswith("<"):
        raise SystemExit("That sheet isn't public. Share it with the service account email, or export a CSV.")
    return list(csv.DictReader(io.StringIO(text)))
