# Running it without installing anything

Python never has to touch your PC. Streamlit's servers run it; you use a browser.

Everything below happens on two websites: **github.com** and **share.streamlit.io**.
Both are free accounts. Nothing to install, no command line.

---

## Step 1 — Put the code on GitHub (10 min)

1. Sign up at [github.com](https://github.com) (free).
2. Click **+** (top right) → **New repository**.
   - Name: `northwood-r6-tracker`
   - **Private** is fine — Streamlit can read your private repos.
   - Tick **Add a README file** so the repo isn't empty.
   - **Create repository**.
3. Unzip the tracker folder on your PC if you haven't.
4. In the repo, click **Add file → Upload files**.
5. Open the unzipped folder, select **everything inside it** (Ctrl+A), and drag it
   into the browser window.
   - Folders (`ui`, `siegestats`, `.streamlit`) come along automatically.
   - **Do not** upload `google_creds.json` if you ever create one.
6. Scroll down, click **Commit changes**.

Check the file list shows `app.py`, `requirements.txt`, `seed.py`, and the `ui` and
`siegestats` folders. If everything landed inside an extra nested folder, delete it
and re-upload from one level deeper — `app.py` must sit at the top level.

## Step 2 — Deploy (5 min)

1. Go to [share.streamlit.io](https://share.streamlit.io) → **Sign in with GitHub** →
   authorize it.
2. **Create app** → **Deploy a public app from a repo** (works for private repos too).
3. Fill in:
   - Repository: `your-name/northwood-r6-tracker`
   - Branch: `main`
   - Main file path: `app.py`
4. Click **Advanced settings** → **Secrets**, and paste:

```toml
editor_password = "pick-something-only-you-know"
viewer_password = "pick-something-for-the-team"
ANTHROPIC_API_KEY = "sk-ant-..."
```

   Leave out the API key line if you don't have one yet — the app still runs, you
   just type scoreboards in by hand until you add it.
5. **Deploy**. First build takes 2–5 minutes.

**You now have a URL.** It asks for a password; use your editor password. The roster,
map pool and the Cumberland match are created automatically on first boot.

## Step 3 — Make the data permanent (important, 20 min)

Streamlit's free tier gives you no permanent disk: whenever the app restarts, anything
you imported is gone unless it has been copied out. Pick one.

**Option A — Google Sheets (recommended).** Follow *Step 2 and Step 3* of `DEPLOY.md`.
Once the Sheet is connected and auto-sync is ticked, every import writes a full backup
there and the app rebuilds from it on every boot. Your team also gets a readable
spreadsheet out of it.

**Option B — manual backups.** After importing, go to **Manage → Data → Download
siege.db backup**. To restore after a restart, **Manage → Data → Restore from a
backup file** and upload that file. This works, but it's on you to remember.

Do Option A if you want to stop thinking about it.

## Step 4 — Send it to the team

Give teammates the URL and the **viewer password** (dashboards, read-only). Give the
**editor password** to anyone who should import matches.

---

## Editing the code later, still with no Python

In your GitHub repo, click any file → the pencil icon → edit → **Commit changes**.
Streamlit redeploys automatically within a minute.

For bigger changes, open the repo and press the **`.`** key (a full VS Code editor
opens in your browser), or use **Code → Codespaces → Create codespace** for a browser
machine with Python already installed — 60 free hours a month.

---

## Why not Supabase / Vercel?

They'd solve a problem you don't have. Vercel hosts JavaScript, so using it would mean
rewriting the entire app — the readers, the stat math, the analysis — from scratch.
Supabase would replace SQLite with a hosted database, but SQLite here is just a file
the app manages for you; you never write SQL. Its free tier also pauses projects after
a week of inactivity, which is worse than the Google Sheets backup you already have.

Streamlit Cloud + Google Sheets gives you: no install, no command line, a shareable
link, permanent storage, and $0 — using the code that already exists and is tested.
