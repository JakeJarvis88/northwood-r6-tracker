"""Keep the database alive by storing it in the GitHub repo.

Why: free hosting has no permanent disk, so anything imported disappears when the
app restarts. Google Sheets solves that but needs a Google Cloud service account.
This is the shorter path — you already have a repo, so the app commits the
database file back to it after every save and downloads it again on boot.

Setup (one token, ~3 minutes):
  1. github.com -> Settings -> Developer settings -> Personal access tokens ->
     Fine-grained tokens -> Generate new token.
  2. Repository access: only the tracker repo. Permissions: Contents -> Read and write.
  3. Put it in Streamlit secrets as:
         github_token = "github_pat_..."
         github_repo  = "your-username/northwood-r6-tracker"

Every save writes a real commit, so you also get free version history: if something
gets mangled you can restore any earlier copy from the repo's commit list.

Concurrency is last-write-wins, same as the Sheets backup: one person imports at a
time. A push that would overwrite a newer commit is detected and reported rather
than silently clobbering it.
"""
import base64
import os

API = "https://api.github.com"
DB_PATH_IN_REPO = "data/siege.db"


def configured(token, repo):
    return bool(token and repo and "/" in str(repo))


def _headers(token):
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _meta(token, repo, path=DB_PATH_IN_REPO, branch=None):
    """File metadata (sha, size, download_url) or None when absent."""
    import requests
    params = {"ref": branch} if branch else {}
    r = requests.get(f"{API}/repos/{repo}/contents/{path}", headers=_headers(token),
                     params=params, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else None


def remote_info(token, repo, path=DB_PATH_IN_REPO, branch=None):
    """(exists, size_bytes, sha) — used to show status without downloading."""
    m = _meta(token, repo, path, branch)
    if not m:
        return False, 0, None
    return True, int(m.get("size") or 0), m.get("sha")


def pull(token, repo, dest, path=DB_PATH_IN_REPO, branch=None):
    """Download the stored database over `dest`. Returns bytes written, or 0."""
    import requests
    m = _meta(token, repo, path, branch)
    if not m:
        return 0
    content = m.get("content")
    if content and m.get("encoding") == "base64":
        blob = base64.b64decode(content)
    else:  # files over ~1 MB come back without inline content
        url = m.get("download_url")
        if not url:
            return 0
        resp = requests.get(url, headers=_headers(token), timeout=60)
        resp.raise_for_status()
        blob = resp.content
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".incoming"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, dest)
    return len(blob)


def push(token, repo, src, path=DB_PATH_IN_REPO, branch=None, message=None,
         expect_sha="auto"):
    """Commit the local database to the repo. Returns the new commit sha.

    expect_sha="auto" reads the current sha first and overwrites it. Pass a sha
    you previously saw to refuse the push if someone else has committed since.
    """
    import requests
    if not os.path.exists(src):
        raise FileNotFoundError(src)
    with open(src, "rb") as f:
        blob = f.read()
    sha = expect_sha
    if expect_sha == "auto":
        m = _meta(token, repo, path, branch)
        sha = m.get("sha") if m else None
    body = {"message": message or "Update tracker database",
            "content": base64.b64encode(blob).decode()}
    if sha:
        body["sha"] = sha
    if branch:
        body["branch"] = branch
    r = requests.put(f"{API}/repos/{repo}/contents/{path}", headers=_headers(token),
                     json=body, timeout=60)
    if r.status_code == 409:
        raise RuntimeError("The stored database changed since this session loaded it — "
                           "someone else saved in the meantime. Reload the app before saving "
                           "again so their work isn't overwritten.")
    r.raise_for_status()
    return (r.json().get("commit") or {}).get("sha")


def check(token, repo):
    """Validate the token/repo pair. Returns (ok, message)."""
    import requests
    try:
        r = requests.get(f"{API}/repos/{repo}", headers=_headers(token), timeout=30)
    except Exception as e:
        return False, f"Couldn't reach GitHub: {e}"
    if r.status_code == 404:
        return False, "Repo not found, or the token can't see it. Check the owner/name and that "\
                      "the token grants access to this repository."
    if r.status_code in (401, 403):
        return False, "GitHub rejected the token. It may be expired or missing the "\
                      "Contents: Read and write permission."
    if not r.ok:
        return False, f"GitHub returned {r.status_code}."
    if not r.json().get("permissions", {}).get("push", False):
        return False, "The token can read this repo but not write to it — set Contents to "\
                      "Read and write."
    return True, "Connected. The database will be saved to this repo."
