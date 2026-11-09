"""Administrator CLI for invitations, delivery, and aggregate pilot metrics."""
import argparse
from datetime import date, timedelta
import json
import os
import sys
import time

import career
import database
from settings import clock, load_env

def run_digests(send, force=False):
    day = clock().date()
    if day.weekday() >= 5 and not force:
        print('Weekend. The next scheduled run is a weekday.')
        return
    if send and any(not os.getenv(key) for key in ('SMTP_HOST', 'SMTP_USERNAME', 'SMTP_PASSWORD', 'EMAIL_FROM')):
        raise ValueError('Set SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD and EMAIL_FROM in .env.')
    with database.connect() as db:
        ids = [row['id'] for row in db.execute(
            "SELECT id FROM students WHERE profile!='{}' " + ('AND email_opt_in=1' if send else ''))]
    if not ids:
        print('No eligible student profiles.')
        return
    jobs, warnings = career.search()
    if len(warnings) == len(career.CONFIG['boards']):
        raise RuntimeError('All job sources failed. No digests attempted. ' + ', '.join(warnings))
    delivered = skipped = failed = 0
    for student_id in ids:
        with database.connect() as db:
            user = database.student(db, student_id)
            if not user:
                continue
            history = [url for row in db.execute(
                "SELECT jobs FROM digests WHERE student_id=? AND status IN ('sent','reserved','uncertain')",
                (student_id,)) for url in json.loads(row['jobs'])]
            history.extend(row['url'] for row in database.applications(db, student_id))
            review = career.weekly(db, student_id, day) if day.weekday() == 4 else None
        selected = career.matches(jobs, user['profile'], history)
        if not send:
            print(career.digest(day, selected, warnings, review=review), end='\n\n')
            continue
        coaching = career.ai_coaching(user['profile'], selected)
        body = career.digest(day, selected, warnings, coaching, review)
        with database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = database.student(db, student_id)
            if not current or not current['email_opt_in']:
                skipped += 1
                continue
            reservation = db.execute('INSERT OR IGNORE INTO digests VALUES (?,?,?,?,?)',
                (student_id, day.isoformat(), 'daily', 'reserved', json.dumps([j['url'] for j in selected])))
            if not reservation.rowcount:
                skipped += 1
                continue
        status = 'sent'
        try:
            career.send_email(current['email'], body)
            delivered += 1
        except Exception as exc:
            # A disconnect can happen after delivery. Keep the reservation to avoid a duplicate.
            status = 'uncertain'
            failed += 1
            print(f'Delivery requires review ({type(exc).__name__}).', file=sys.stderr)
        with database.connect() as db:
            db.execute("UPDATE digests SET status=? WHERE student_id=? AND day=? AND kind='daily'",
                       (status, student_id, day.isoformat()))
            if status == 'sent' and database.student(db, student_id):
                db.execute('INSERT OR IGNORE INTO activity VALUES (?,?,?,0)',
                           (student_id, day.isoformat(), career.task_for(day)))
    if send:
        print(f'Delivered {delivered} · Skipped {skipped} · Needs review {failed}')
        if failed:
            raise RuntimeError('Check delivery logs and SMTP settings.')

def worker():
    hour, minute = (int(value) for value in os.getenv('CAREER_SEND_AT', '08:00').split(':'))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError('CAREER_SEND_AT must be HH:MM.')
    last_day = None
    while True:
        now = clock()
        if now.weekday() < 5 and last_day != now.date() and (now.hour, now.minute) >= (hour, minute):
            try:
                run_digests(True)
                last_day = now.date()
            except (ValueError, RuntimeError, OSError) as exc:
                print(f'Scheduled digest: {exc}', file=sys.stderr)
                time.sleep(300)
        time.sleep(30)

def metrics(start):
    day = clock().date()
    start = start or day - timedelta(days=89)
    with database.connect() as db:
        students = db.execute('SELECT count(*) FROM students').fetchone()[0]
        participation = db.execute('SELECT count(DISTINCT student_id) FROM activity WHERE day>=? AND done=1',
                                   ((day-timedelta(days=6)).isoformat(),)).fetchone()[0]
        applied = db.execute('SELECT count(*) FROM applications WHERE applied_on>=?', (start.isoformat(),)).fetchone()[0]
        offers = db.execute("SELECT count(*) FROM applications WHERE applied_on>=? AND status IN ('Offer','Accepted')",
                            (start.isoformat(),)).fetchone()[0]
        interviews = db.execute("SELECT count(*) FROM applications WHERE applied_on>=? AND status='Interview'",
                                (start.isoformat(),)).fetchone()[0]
        completed_tasks = db.execute('SELECT count(*) FROM activity WHERE day>=? AND done=1', (start.isoformat(),)).fetchone()[0]
        feedback = dict(db.execute('SELECT rating,count(*) FROM feedback WHERE day>=? GROUP BY rating', (start.isoformat(),)))
        digests = dict(db.execute('SELECT status,count(*) FROM digests WHERE day>=? GROUP BY status', (start.isoformat(),)))
    if students < 5:
        print('Aggregate results are available once five students are enrolled.')
        return
    print(json.dumps(dict(since=start.isoformat(), enrolled=students, active_last_7_days=participation,
                          applications=applied, current_interviews=interviews, current_offers_or_acceptances=offers,
                          completed_tasks=completed_tasks, digest_status_counts=digests, recommendation_feedback=feedback), indent=2))

def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    invitation = commands.add_parser('invite')
    invitation.add_argument('email')
    digests = commands.add_parser('digest')
    digests.add_argument('--send', action='store_true')
    digests.add_argument('--allow-weekend', action='store_true')
    report = commands.add_parser('metrics')
    report.add_argument('--since', type=date.fromisoformat)
    commands.add_parser('worker')
    args = parser.parse_args()
    if args.command == 'invite':
        with database.connect() as db:
            token = database.invite(db, args.email, clock().date() + timedelta(days=7))
        print('One-use invite, valid for seven days:', token)
    elif args.command == 'digest':
        run_digests(args.send, args.allow_weekend)
    elif args.command == 'worker':
        worker()
    else:
        metrics(args.since)

if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        sys.exit(str(exc))
