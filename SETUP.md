# Running the Built In job scraper on GitHub (daily, automated)

This gets `builtin_jobs.py` running on a schedule in GitHub Actions, emailing you
only the new CNS / gene-therapy / gene-editing matches each morning. It mirrors
the setup you already use for the new-company detector, so the Gmail app-password
and cron pieces will feel familiar.

You can add this to the **same repo** as your company detector (recommended — one
place, one secret) or a fresh repo. Steps below assume the same repo.

---

## What each file is

| File | Where it goes in the repo | Purpose |
|---|---|---|
| `builtin_jobs.py` | repo root | the scraper |
| `company_config.yaml` | repo root | focus keywords (shared with your detector) |
| `requirements.txt` | repo root | Python deps (merge with your existing one if present) |
| `builtin_jobs.yml` | `.github/workflows/` | the daily schedule |
| `jobs_seen.json` | created automatically on first run | tracks already-reported jobs |
| `jobs_archive.csv` | created automatically on first run | permanent CSV log of every job found (deduped, dated) |

If your repo already has a `requirements.txt`, just make sure it includes
`requests`, `beautifulsoup4`, and `PyYAML` — don't overwrite the existing one.

---

## Step 1 — Add the files to your repo

Using the GitHub website (no command line needed):

1. Open your repo on github.com.
2. Click **Add file -> Upload files**.
3. Drag in `builtin_jobs.py`, `company_config.yaml`, and `requirements.txt`.
   (Skip `company_config.yaml` if it's already there from the detector.)
4. Click **Commit changes**.
5. Now add the workflow. Click **Add file -> Create new file**.
6. In the filename box type exactly: `.github/workflows/builtin_jobs.yml`
   (typing the slashes creates the folders automatically).
7. Paste the contents of `builtin_jobs.yml` into the editor.
8. Click **Commit changes**.

Prefer the command line? From your local clone:

```bash
mkdir -p .github/workflows
cp builtin_jobs.py company_config.yaml requirements.txt .
cp builtin_jobs.yml .github/workflows/
git add builtin_jobs.py company_config.yaml requirements.txt .github/workflows/builtin_jobs.yml
git commit -m "Add Built In daily job scraper"
git push
```

---

## Step 2 — Create a Gmail App Password (skip if you already have one)

The scraper sends the digest through Gmail. Gmail won't accept your normal
password from a script, so you need a 16-character **App Password**.

1. Go to <https://myaccount.google.com/security>.
2. Turn on **2-Step Verification** if it isn't already (required for App Passwords).
3. Go to <https://myaccount.google.com/apppasswords>.
4. Name it something like `builtin-scraper` and click **Create**.
5. Copy the 16-character password it shows (looks like `abcd efgh ijkl mnop`).
   Remove the spaces when you paste it in Step 3.

If you already made one for the company detector, you can reuse that same App
Password — no need to create a second.

---

## Step 3 — Add repository secrets

Secrets keep your password out of the code. In your repo:

1. Click **Settings** (top nav of the repo, not your account settings).
2. Left sidebar: **Secrets and variables -> Actions**.
3. Click **New repository secret** and add each of these:

   | Name | Value |
   |---|---|
   | `GMAIL_APP_PASSWORD` | the 16-char app password from Step 2 (no spaces) |
   | `GMAIL_ADDRESS` | your Gmail address, e.g. `you@gmail.com` |

   (If `GMAIL_APP_PASSWORD` already exists from the detector, leave it — just add
   `GMAIL_ADDRESS` if it's missing.)

4. Click **Add secret** for each.

The workflow reads these at runtime; they're never written to the repo or logs.

---

## Step 4 — Run it once by hand to test

Don't wait for the 7:30 AM cron — trigger it now:

1. Repo -> **Actions** tab.
2. If prompted "Workflows aren't running," click the button to enable them.
3. Left sidebar: click **Built In Job Scraper**.
4. Click **Run workflow -> Run workflow** (the green button).
5. Wait ~1-3 minutes, then click into the run to watch the log.

**On the very first run, every match is "new," so expect a full inbox** — the
seen-file starts empty. After that, each day reports only genuinely new postings.

What success looks like in the log:
- `Run scraper + email new hits` step ends with `emailed digest to ...`
- `Commit updated seen-file` step commits `jobs_seen.json`
- You get an email grouped under **CNS + Gene**, **Gene**, **CNS** headers

---

## Step 5 — Confirm the daily schedule

Nothing more to do — the `cron: '30 11 * * *'` line runs it every day at
11:30 UTC (7:30 AM Eastern in winter, 6:30 AM in summer; GitHub cron is always
UTC and doesn't shift for daylight saving).

To change the time, edit that line in `.github/workflows/builtin_jobs.yml`.
For example 9:00 AM Eastern in winter = `0 14 * * *`.

> **Note:** GitHub sometimes delays scheduled runs by a few minutes to an hour
> under load. That's normal and doesn't affect results.

---

## Tuning it later (all optional)

**Change what counts as a match** — edit `company_config.yaml`. The `focus_keywords`
block feeds both this scraper and your company detector, so one edit updates both.

**Change which titles are considered** — the workflow uses the default
(`scientist` or `director`). To narrow it, add a line to the run command:
```
--title-include scientist "associate director"
```

**Search a different slice of Built In** — change the `--url`. Any Built In jobs
search URL works; the `search=` term and the `/jobs/<industry>` path are the main
levers. `--pages auto` reads the page count automatically.

**Get an email even on quiet days** — add `--email-empty` to the run command if
you'd rather get a "no new matches" note than silence.

**Two CSV outputs, different jobs** — `--csv new_jobs.csv` is a *snapshot* of just
this run's hits and is overwritten each day. `--archive jobs_archive.csv` is a
*permanent log*: new jobs are appended (deduped on job ID) with a `first_seen`
date, so it grows into a full history of everything the scraper has ever found.
The workflow commits the archive back to the repo after each run, so it persists.
Open it in Excel or Sheets any time to see the running record.

**See everything, not just categorized hits** — drop `--focus-only`. Uncategorized
jobs sort to the bottom of the JSON/CSV (they're never emailed regardless).

---

## Troubleshooting

**No email arrived.** Check the Actions log for the scraper step. If it says
`GMAIL_APP_PASSWORD not set`, the secret name is wrong or missing. If it says
`no new hits`, that's expected on a day with nothing new — add `--email-empty`
to confirm the pipeline runs. Also check spam.

**`SMTPAuthenticationError` in the log.** The app password is wrong, has spaces
in it, or 2-Step Verification got turned off. Redo Step 2 and update the secret.

**The commit step fails with a permissions error.** Confirm the workflow has
`permissions: contents: write` (it does as written) and that repo
**Settings -> Actions -> General -> Workflow permissions** is set to
**Read and write permissions**.

**Every run emails the same jobs.** The seen-file isn't being committed. Check the
`Commit updated seen-file` step succeeded and that `jobs_seen.json` exists in the
repo after a run.

**Built In returns fewer jobs than the website shows.** The site restricts heavy
automated access; keep the default `--delay` (2 seconds) and don't lower it. This
is for personal job-search use — be a polite scraper.
