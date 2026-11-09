# Career Launch Agent

A Python and Streamlit application for a 90-day pilot with 5–10 graduating students. Each student has a private profile, employer job search, application tracker, follow-up dates, daily practice, and a rolling weekly review. The weekday worker emails opted-in students up to five new recommendations per day. Profiles start at three matches, with the maximum adjustable from one to five.

## Run on Windows

Install Python 3.12, extract this folder, and open it in VS Code. From the PowerShell terminal inside the folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py invite you@example.com
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open http://localhost:8501. Choose **Use an invitation**, enter the same email address used by the invite command, paste the printed code, and set a password. Save your profile and select **Job matches** to search live sources. On Linux or macOS use `python3.12 -m venv .venv` and `.venv/bin/python` in place of the Windows executable.

## Weekday emails

Copy `.env.example` to `.env` and add SMTP credentials from a provider supporting authenticated STARTTLS. Set `EMAIL_FROM` to an approved sender and optionally set `CAREER_APP_URL` to the dashboard address. Keep this file private. Use the same data directory for the dashboard and the worker.

From an activated virtual environment, use the following commands:

| Command | What it does |
| --- | --- |
| `python manage.py digest --allow-weekend` | Previews the digest using the current profiles and live results. |
| `python manage.py digest --send --allow-weekend` | Sends one digest to each opted-in account. Test with your own account and confirm it reaches your inbox. |
| `python manage.py worker` | Runs the weekday schedule at 8 a.m. Eastern by default. Leave it running on the host. |
| `python manage.py metrics --since 2026-11-09` | Prints aggregate pilot outcomes from your chosen start date after five students enroll. |

The worker uses the local time in `CAREER_TZ` and `CAREER_SEND_AT`, including daylight-saving changes. If the host comes online after the scheduled time, it runs that day’s briefing on a weekday. The worker and dashboard can restart without losing profiles or email history. Reservations block multiple sends to the same person on the same day. A failed or interrupted SMTP attempt is marked uncertain or remains reserved, since the email server might already have accepted it. Check SMTP logs before attempting a manual resend. Saturday and Sunday runs require the explicit manual override above.

Students opt into delivery from **Profile** and can unsubscribe there. A briefing includes current employer links, skills in common, skills to verify, up to two applications as a daily goal, and a practice task. Friday’s email includes the weekly review.

## Optional AI coaching

Set `OPENAI_API_KEY` and `OPENAI_MODEL` to a model available to your API project. Each student can opt in separately. The request sends job titles and matched or unverified keywords from the fixed skill vocabulary. Full résumé text, email, names, notes, and application history stay in the local database. Review the provider’s data controls before using the feature with volunteers. The dashboard’s matching, task suggestions, and weekly summaries work without a key. The AI supplement is generated for eligible emails and falls back to the regular briefing if the API call fails.

## Pilot hosting

The included Docker configuration runs the dashboard and worker on the same host with a shared, durable SQLite volume. Copy the environment template to `.env`, fill in the sender settings, then run:

```bash
docker compose up --build -d
docker compose exec web python manage.py invite student@example.com
```

Distribute each one-use, email-bound invite privately. The dashboard listens on the host loopback interface. For access by volunteers, configure HTTPS and WebSocket forwarding through a reverse proxy or deploy on a private network. Keep the database and admin CLI accessible only to the operator. Back up the volume with a SQLite-aware backup or while both services are stopped, and test restoration. Avoid placing the SQLite file on a network filesystem or sharing it between hosts.

This build uses a single-host architecture, with app-level student access controls and SQLite. It does not provision Supabase, use PostgreSQL row-level security, run GitHub Actions, or deploy itself. Review hosting, access, backups, and your college’s handling of student records before enrollment. An operator with shell or database access can access the underlying data. Share aggregate results with faculty. Agree on a retention period, and include backups in the deletion policy. The app supports account deletion from **Profile**, which removes the associated records in the active database.

## Sources and matching

Edit `sources.json` to change the employer boards or role and skill vocabulary. It supports Greenhouse, global Lever, SmartRecruiters, and Amazon’s careers search. The Amazon adapter uses a limited public search feed and can be disabled by removing its board. Use employer-authorized sources and review their terms before running a hosted service. SmartRecruiters and Amazon adapters focus on NC and SC. Greenhouse and Lever use their posted location metadata. A remote role must explicitly list US eligibility, and students should confirm state eligibility on the posting.

Matches use job title, location, and skill overlap. The source vocabulary and published job locations limit coverage. Employers can close positions after a search, and a search can return fewer matches or none. A match is an invitation to review requirements, not a decision about qualifications. Use **Applications → Add a job from an employer site** to track additional roles found outside the configured sources. The agent never submits an application.

## Evaluate the pilot

- Define a start date and invite 5–10 volunteers. Confirm that students can access only their own records and send a trial digest to your own account.
- Each week, review the aggregate report for participation, logged applications, current interview and offer statuses, completed tasks, and recommendation feedback. Participation in the report counts students who completed a task in the last seven days. Application counts use the date students entered.
- Spot-check employer links from the digests. Record the number that still open a live, correct posting divided by the number checked. The brief’s initial targets are 80% weekly participation and 90% live links. Review why students skipped jobs before changing title filters or adding more boards.
- At day 90, share aggregate results and review whether the workflow helped students find openings they would apply to. Review hosting and data retention before extending the pilot.

## Where to read the code

| File | Responsibility |
| --- | --- |
| `app.py` | Invitation registration, dashboard, settings, and tracker |
| `database.py` | Accounts, scoped queries, application and delivery history |
| `job_sources.py` | Live source adapters and skill-based ranking |
| `career.py` | Search coordination, coaching, weekly reviews, and email formatting |
| `manage.py` | Invites, digests, scheduling, and aggregate reporting |
