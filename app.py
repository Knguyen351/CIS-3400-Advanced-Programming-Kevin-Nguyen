"""Career Launch Agent dashboard."""
from datetime import date
import hashlib
import time

import streamlit as st

import career
import database
from job_sources import canonical
from settings import clock, load_env

load_env()
st.set_page_config(page_title='Career Launch Agent', page_icon='🧭', layout='wide')
st.title('Career Launch Agent')
st.caption('Find early-career IT jobs, track applications, and plan your next move.')
today = clock().date()

def login():
    signin, signup = st.tabs(['Sign in', 'Use an invitation'])
    with signin, st.form('signin'):
        email = st.text_input('Email', key='login_email', max_chars=254)
        password = st.text_input('Password', type='password', key='login_password', max_chars=128)
        if st.form_submit_button('Sign in', type='primary'):
            try:
                with database.connect() as db:
                    student_id = database.authenticate(db, email, password, int(time.time()))
                if student_id:
                    st.session_state.clear()
                    st.session_state.student_id = student_id
                    st.rerun()
                st.error('Sign-in failed. Check your credentials or try again in 15 minutes.')
            except ValueError:
                st.error('Enter a valid email address.')
    with signup, st.form('signup'):
        email = st.text_input('Invitation email', max_chars=254)
        token = st.text_input('Invitation code', type='password', max_chars=128)
        password = st.text_input('New password (12–128 characters)', type='password', max_chars=128)
        if st.form_submit_button('Create account'):
            try:
                with database.connect() as db:
                    student_id = database.register(db, email, token, password, today)
                st.session_state.clear()
                st.session_state.student_id = student_id
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

def profile_form(user):
    profile = user['profile']
    st.subheader('Your job preferences')
    if st.session_state.pop('profile_saved', False):
        st.success('Preferences saved.')
    st.caption('Enter skills and work experience. Leave out contact details and other sensitive information.')
    with st.form('profile'):
        major = st.text_input('Major', profile.get('major', ''), max_chars=120)
        graduation = st.text_input('Expected graduation (YYYY-MM)', profile.get('graduation', ''), max_chars=7)
        locations = st.multiselect('States', ['NC', 'SC'], default=profile.get('locations', ['NC', 'SC']))
        remote = st.checkbox('Include explicitly US-remote roles', profile.get('remote', False))
        options = career.CONFIG['target_titles']
        titles = st.multiselect('Target titles', options, default=[t for t in profile.get('titles', options) if t in options])
        skills = st.text_area('Skills', profile.get('skills', ''), max_chars=3000)
        resume = st.text_area('Résumé text', profile.get('resume', ''), max_chars=15000)
        limit = st.slider('Maximum jobs per digest', 1, 5, profile.get('limit', 3))
        ai = st.checkbox('Allow optional AI coaching using job titles and matched skill keywords',
                         profile.get('ai', False))
        email = st.checkbox('Send me weekday briefings', bool(user['email_opt_in']))
        if st.form_submit_button('Save preferences', type='primary'):
            try:
                if not major.strip() or not locations or not titles or not (skills.strip() or resume.strip()):
                    raise ValueError('Enter your major, select locations and titles, and add your skills or résumé.')
                date.fromisoformat(graduation + '-01')
                with database.connect() as db:
                    database.save_profile(db, user['id'], dict(major=major, graduation=graduation,
                        locations=locations, titles=titles, remote=remote, skills=skills,
                        resume=resume, limit=limit, ai=ai), email)
                st.session_state.pop('search_results', None)
                st.session_state.profile_saved = True
                st.rerun()
            except ValueError as exc:
                st.error('Use YYYY-MM for graduation.' if 'isoformat' in str(exc) else str(exc))
    if user['email_opt_in'] and st.button('Unsubscribe now'):
        with database.connect() as db:
            db.execute('UPDATE students SET email_opt_in=0 WHERE id=?', (user['id'],))
        st.rerun()
    with st.expander('Delete account and pilot data'):
        st.caption('This deletes your profile, tracked applications, tasks, and digest history from the active database.')
        confirm = st.checkbox('I want to delete my account')
        if st.button('Delete my account', disabled=not confirm):
            with database.connect() as db:
                db.execute('DELETE FROM students WHERE id=?', (user['id'],))
            st.session_state.clear()
            st.rerun()

@st.cache_data(ttl=900, show_spinner=False)
def public_jobs():
    return career.search()

def job_cards(user):
    profile = user['profile']
    if not profile:
        st.info('Complete your profile before searching.')
        return
    if st.button('Search live employer boards', type='primary'):
        with st.spinner('Checking employer boards…'):
            jobs, warnings = public_jobs()
            st.session_state.search_results = (career.matches(jobs, profile), warnings, len(jobs))
    if 'search_results' not in st.session_state:
        return
    jobs, warnings, scanned = st.session_state.search_results
    st.caption(f'{len(jobs)} matches from {scanned:,} postings returned by configured sources.')
    if warnings:
        st.warning('Unavailable sources: ' + ', '.join(warnings))
    if not jobs:
        st.info('No postings matched these preferences on this run.')
    for job in jobs:
        with st.container(border=True):
            st.subheader(job['title'])
            st.caption(f"{job['company']} · {job['location']}")
            st.write('Skills in common: ' + ', '.join(job['matched_skills']))
            if job['unverified_skills']:
                st.write('Skills to check: ' + ', '.join(job['unverified_skills']))
            st.link_button('View employer application', job['url'])
            key = hashlib.sha256(job['url'].encode()).hexdigest()
            if st.button('Save job', key=key):
                with database.connect() as db:
                    db.execute('INSERT OR IGNORE INTO applications VALUES (?,?,?,?,?,?,?,?,?)',
                               (user['id'], job['url'], job['title'], job['company'],
                                'Saved', '', None, None, today.isoformat()))
                st.toast('Job is in your tracker.')

def tracker(user):
    student_id = user['id']
    with database.connect() as db:
        rows = {row['url']: dict(row) for row in database.applications(db, student_id)}
    with st.expander('Add a job from an employer site'):
        with st.form('manual'):
            title = st.text_input('Job title', max_chars=200)
            company = st.text_input('Employer', max_chars=160)
            url = st.text_input('Employer URL', max_chars=1000)
            if st.form_submit_button('Add to tracker'):
                try:
                    if not title.strip() or not company.strip():
                        raise ValueError('Enter the title and employer.')
                    with database.connect() as db:
                        db.execute('INSERT OR IGNORE INTO applications VALUES (?,?,?,?,?,?,?,?,?)',
                                   (student_id, canonical(url), title, company,
                                    'Saved', '', None, None, today.isoformat()))
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    if not rows:
        st.info('Save a match or add an employer posting to start tracking.')
        return
    def job_label(url):
        row = rows[url]
        return f"{row['title']} · {row['company']} · {row['status']}"
    selected_url = st.selectbox('Choose a tracked job', list(rows), format_func=job_label)
    job = rows[selected_url]
    st.link_button('View employer application', job['url'])
    if job['follow_up'] and job['follow_up'] <= today.isoformat() and job['status'] in ('Applied', 'Interview'):
        st.warning('A follow-up is due.')
    with st.form('update_application_' + hashlib.sha256(job['url'].encode()).hexdigest()):
        status = st.selectbox('Status', database.STATUSES, index=database.STATUSES.index(job['status']))
        applied = st.date_input('Applied on', date.fromisoformat(job['applied_on']) if job['applied_on'] else None)
        follow_up = st.date_input('Follow-up date', date.fromisoformat(job['follow_up']) if job['follow_up'] else None)
        notes = st.text_area('Private notes', job['notes'], max_chars=3000)
        if st.form_submit_button('Update application'):
            try:
                with database.connect() as db:
                    database.save_application(db, student_id, job, status, notes,
                                              applied.isoformat() if applied else None,
                                              follow_up.isoformat() if follow_up else None, today)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

def progress(user):
    with database.connect() as db:
        review = career.weekly(db, user['id'], today)
        daily = db.execute('SELECT task,done FROM activity WHERE student_id=? AND day=?',
                           (user['id'], today.isoformat())).fetchone()
    task = daily['task'] if daily else career.task_for(today)
    st.subheader('Today’s practice')
    st.write(task)
    done = st.checkbox('I finished this task', bool(daily and daily['done']))
    if st.button('Save task progress'):
        with database.connect() as db:
            database.activity(db, user['id'], today, task, done)
        st.success('Task progress saved.')
    st.subheader('Your weekly review')
    st.text(review)
    rating = st.radio('How useful were the recent job recommendations?',
                      ['Useful', 'Somewhat useful', 'Not useful'], index=None)
    if st.button('Save feedback', disabled=rating is None):
        with database.connect() as db:
            db.execute('INSERT INTO feedback VALUES (?,?,?) ON CONFLICT(student_id,day) DO UPDATE SET rating=excluded.rating',
                       (user['id'], today.isoformat(), rating))
        st.success('Feedback saved.')

def main():
    student_id = st.session_state.get('student_id')
    with database.connect() as db:
        user = database.student(db, student_id) if student_id else None
    if not user:
        login()
        return
    with st.sidebar:
        st.caption(user['email'])
        if st.button('Sign out'):
            st.session_state.clear()
            st.rerun()
        pages = ['Profile', 'Job matches', 'Applications', 'Progress']
        page = st.radio('Workspace', pages, key='workspace')
    {'Profile': profile_form, 'Job matches': job_cards, 'Applications': tracker, 'Progress': progress}[page](user)

main()
