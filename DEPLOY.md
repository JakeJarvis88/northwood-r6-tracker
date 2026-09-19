# Getting this online, free, for the team

**No subscriptions anywhere in this stack.** GitHub, Streamlit Community Cloud and
Google Sheets are free accounts with no paid tier required. The only thing that
can ever cost money is the screenshot reader, and it's pay-as-you-go per request —
prepaid credit, no monthly fee, no minimum, nothing auto-renewing. Send no
screenshots for a month and you're billed nothing. A whole season runs about $1
against the $5 of free credit a new account starts with.

Two ways to run it. Pick one.

---

## Option A — Local only (10 minutes, simplest)

You run the app on your PC; teammates read the Google Sheet.

```bash
pip install -r requirements.txt
streamlit run app.py
```

Teammates get the Sheet link (view-only). They see every dashboard table, updated
each time you import. They can't import matches themselves.

Good if you're the only one who'll ever do imports. Skip to **Step 2** below for
the Sheets part, ignore the rest.

---

## Option B — Hosted on Streamlit Community Cloud (45 minutes, everyone gets a link)

Teammates open a URL, log in with a shared password, and browse. Give trusted
people the editor password and they can import matches too.

### Step 1 — GitHub repo (free)

1. Create a GitHub account if you don't have one.
2. Make a **new repository** — public is fine and simpler; the `.gitignore`
   already excludes secrets and the local database.
3. Upload the project folder (drag-and-drop works in GitHub's web UI:
   *Add file → Upload files*).

**Never commit** `google_creds.json` or `.streamlit/secrets.toml`. They're
gitignored, but check before pushing.

### Step 2 — Google Sheet as durable storage (free)

Free hosting has no permanent disk — the container wipes on every restart. So the
Sheet is where your data actually lives, and the app rebuilds from it on boot.

1. [console.cloud.google.com](https://console.cloud.google.com) → new project.
2. **APIs & Services → Library** → enable **Google Sheets API** and **Google
   Drive API**.
3. **IAM & Admin → Service Accounts** → create one → **Keys → Add key → JSON** →
   download it.
4. Create a Google Sheet. Click **Share**, paste the service account's
   `...@....iam.gserviceaccount.com` address, give it **Editor**.
5. Share the same Sheet **view-only** with your teammates (or "anyone with the
   link can view").

### Step 3 — Deploy (free)

1. [share.streamlit.io](https://share.streamlit.io) → sign in with GitHub →
   **New app** → pick your repo, main file `app.py`.
2. Before deploying, open **Advanced settings → Secrets** and paste this,
   filled in (template also at `.streamlit/secrets.toml.example`):

```toml
editor_password = "something-only-you-know"
viewer_password = "something-for-the-team"
ANTHROPIC_API_KEY = "sk-ant-..."
gs_sheet = "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit"

[gcp_service_account]
# paste the ENTIRE contents of the downloaded JSON key here, as TOML keys
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\nMII...\n-----END PRIVATE KEY-----\n"
client_email = "something@something.iam.gserviceaccount.com"
client_id = "..."
token_uri = "https://oauth2.googleapis.com/token"
```

3. Deploy. First boot has no data — see Step 4.

### Step 4 — Load your existing data

On your own machine, with the app running locally and Sheets configured under
**Manage → Settings**, hit **Manage → Data → 🔄 Sync to Google Sheets now**. That
writes both the readable report tabs and the `_db_` backup tabs.

Then restart the cloud app (Streamlit Cloud → **Reboot**). It finds the backup
and restores everything on boot.

From then on, every confirmed import pushes back automatically — the Sheet stays
the source of truth and the cloud container is disposable.

### Step 5 — Send it out

Give teammates:
- the app URL + **viewer password** → dashboards, filters, scouting, ratings
- or the **editor password** → they can import matches too
- the Sheet link if they'd rather just read a spreadsheet

---

## The API key (the only thing that can cost money)

The screenshot reader calls Anthropic's API. Everything else — hosting, storage,
sharing, all the analysis — is free, and the app works with no key at all if you
type the numbers yourself.

- Get a key at [console.claude.com](https://console.claude.com). New accounts get
  **$5 in free credits**.
- **Pay-as-you-go only.** You buy credit up front; there is no subscription, no
  monthly minimum, and nothing renews on its own. Idle months cost $0.
- Default model is **Sonnet**, about two cents per scoreboard — roughly **$1 for a
  40-map season**, comfortably inside the free credit.
- Leave **auto-reload off** in the console billing settings so nothing can ever
  charge you without you choosing to top up, and set a spend limit if you want a
  hard ceiling on top of that.
- Haiku is available in the model picker if you ever want to halve it further.
- One key in the app's secrets serves everyone — teammates never need their own.

---

## Things worth knowing

**One importer at a time.** Saving writes the whole database back to the Sheet, so
two people importing simultaneously means the second overwrites the first. Fine
for a six-person team; just don't both import right after a match.

**The `_db_` tabs are machine-written.** Don't hand-edit them — they're overwritten
on every sync. Edit data in the app; read the normal report tabs.

**Your API key never reaches the Sheet.** It's filtered out of the backup
deliberately.

**Back up before anything drastic**: Manage → Data → *Download siege.db backup*.

**Sleeping apps.** A free Streamlit app sleeps after a week of no visits and takes
~30 seconds to wake. Data survives — it's in the Sheet.
