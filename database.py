"""Durable pilot data. Student queries always scope to the authenticated user."""
from contextlib import contextmanager
from datetime import date
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3

ROOT = Path(__file__).resolve().parent
STATUSES = ['Saved', 'Applied', 'Interview', 'Rejected', 'Offer', 'Accepted', 'Withdrawn']
SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, password TEXT NOT NULL,
    profile TEXT NOT NULL DEFAULT '{}', email_opt_in INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS invites (
    email TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE, expires TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
    url TEXT NOT NULL, title TEXT NOT NULL, company TEXT NOT NULL, status TEXT NOT NULL,
    notes TEXT NOT NULL, applied_on TEXT, follow_up TEXT, updated TEXT NOT NULL,
    PRIMARY KEY(student_id, url)
);
CREATE TABLE IF NOT EXISTS activity (
    student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
    day TEXT NOT NULL, task TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(student_id, day)
);
CREATE TABLE IF NOT EXISTS digests (
    student_id INTEGER REFERENCES students(id) ON DELETE CASCADE, day TEXT NOT NULL,
    kind TEXT NOT NULL, status TEXT NOT NULL, jobs TEXT NOT NULL,
    PRIMARY KEY(student_id, day, kind)
);
CREATE TABLE IF NOT EXISTS feedback (
    student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
    day TEXT NOT NULL, rating TEXT NOT NULL, PRIMARY KEY(student_id, day)
);
CREATE TABLE IF NOT EXISTS login_limits (
    email TEXT PRIMARY KEY, failures INTEGER NOT NULL, until INTEGER NOT NULL
);
"""

@contextmanager
def connect():
    path = Path(os.getenv('CAREER_DB', str(ROOT / 'data' / 'pilot.sqlite')))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=45)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript(SCHEMA)
        with db:
            yield db
    finally:
        db.close()

def normalize_email(email):
    email = email.strip().lower()
    if not re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', email):
        raise ValueError('Enter a valid email address.')
    return email

def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64*1024**2).hex()

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()

def invite(db, email, expires):
    token = secrets.token_urlsafe(24)
    db.execute('INSERT INTO invites VALUES (?,?,?) ON CONFLICT(email) DO UPDATE SET digest=excluded.digest, expires=excluded.expires',
               (normalize_email(email), token_hash(token), expires.isoformat()))
    return token

def register(db, email, token, password, today):
    email = normalize_email(email)
    if not 12 <= len(password) <= 128:
        raise ValueError('Use a password of 12–128 characters.')
    salt = secrets.token_bytes(16)
    encoded = salt.hex() + ':' + password_hash(password, salt)
    db.execute('BEGIN IMMEDIATE')
    row = db.execute('SELECT digest, expires FROM invites WHERE email=?', (email,)).fetchone()
    if not row or row['expires'] < today.isoformat() or not hmac.compare_digest(row['digest'], token_hash(token.strip())):
        raise ValueError('This invitation is invalid or expired.')
    try:
        student = db.execute('INSERT INTO students(email,password) VALUES (?,?)', (email, encoded)).lastrowid
    except sqlite3.IntegrityError as exc:
        raise ValueError('An account already exists. Sign in instead.') from exc
    db.execute('DELETE FROM invites WHERE email=?', (email,))
    return student

def authenticate(db, email, password, now):
    email = normalize_email(email)
    if len(password) > 128:
        return None
    db.execute('BEGIN IMMEDIATE')
    db.execute('DELETE FROM login_limits WHERE until <= ?', (now,))
    limit = db.execute('SELECT failures FROM login_limits WHERE email=?', (email,)).fetchone()
    if limit and limit['failures'] >= 8:
        return None
    row = db.execute('SELECT id,password FROM students WHERE email=?', (email,)).fetchone()
    salt, expected = row['password'].split(':') if row else ('00'*16, '00'*64)
    valid = hmac.compare_digest(password_hash(password, bytes.fromhex(salt)), expected)
    if row and valid:
        db.execute('DELETE FROM login_limits WHERE email=?', (email,))
        return row['id']
    db.execute('INSERT INTO login_limits VALUES (?,1,?) ON CONFLICT(email) DO UPDATE SET failures=failures+1',
               (email, now+900))
    return None

def student(db, student_id):
    row = db.execute('SELECT id,email,profile,email_opt_in FROM students WHERE id=?', (student_id,)).fetchone()
    return dict(row, profile=json.loads(row['profile'])) if row else None

def save_profile(db, student_id, profile, opt_in):
    db.execute('UPDATE students SET profile=?,email_opt_in=? WHERE id=?', (json.dumps(profile), int(opt_in), student_id))

def applications(db, student_id):
    return db.execute('SELECT * FROM applications WHERE student_id=? ORDER BY updated DESC, title', (student_id,)).fetchall()

def save_application(db, student_id, job, status, notes, applied_on, follow_up, today):
    if status not in STATUSES:
        raise ValueError('Unknown application status.')
    for value in (applied_on, follow_up):
        if value:
            date.fromisoformat(value)
    if status not in ('Saved', 'Withdrawn') and not applied_on:
        raise ValueError('Enter the original application date.')
    db.execute("""INSERT INTO applications VALUES (?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(student_id,url) DO UPDATE SET status=excluded.status,
                  notes=excluded.notes,applied_on=excluded.applied_on,
                  follow_up=excluded.follow_up,updated=excluded.updated""",
               (student_id, job['url'], job['title'], job['company'], status,
                notes, applied_on or None, follow_up or None, today.isoformat()))

def activity(db, student_id, day, task, done=False):
    db.execute("""INSERT INTO activity VALUES (?,?,?,?)
                  ON CONFLICT(student_id,day) DO UPDATE SET done=excluded.done""",
               (student_id, day.isoformat(), task, int(done)))
