import html
from html.parser import HTMLParser
import json
import re
import time
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []
    def handle_data(self, data):
        self.parts.append(data)

def plain(value):
    parser = TextOnly(); parser.feed(html.unescape(html.unescape(value or '')))
    return ' '.join(parser.parts)

def contains(text, term):
    return bool(re.search(r'(?<!\w)' + re.escape(term.lower()) + r'(?!\w)', text.lower()))

def canonical(url):
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password:
        raise ValueError('Job link must be an HTTPS URL')
    # Preserve query parameters: some employers use them to identify a job.
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip('/'), p.query, ''))

def get_json(url):
    for attempt in range(2):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'PersonalJobMatch/1.0', 'Accept': 'application/json'}), timeout=10) as response:
                return json.load(response)
        except (OSError, ValueError) as exc:
            if attempt == 1 or isinstance(exc, HTTPError) and 400 <= exc.code < 500 and exc.code != 429:
                raise
            time.sleep(attempt + 1)

def fetch(board):
    token = quote(board['token'], safe='')
    company = board['company']
    if board['provider'] == 'greenhouse':
        data = get_json(f'https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true')
        return [dict(title=j['title'], company=company, location=j['location']['name'],
                     description=plain(j.get('content', '')), url=j['absolute_url'])
                for j in data['jobs'] if j.get('internal_job_id') is not None]
    if board['provider'] == 'lever':
        rows = []; offset = 0
        while True:
            page = get_json(f'https://api.lever.co/v0/postings/{token}?mode=json&limit=100&skip={offset}')
            rows.extend(dict(title=j['text'], company=company,
                location=j.get('categories', {}).get('location', ''),
                description=plain(j.get('description', '') + ' ' + ' '.join(x.get('content', '') for x in j.get('lists', [])) + ' ' + j.get('additional', '')),
                url=j.get('applyUrl') or j['hostedUrl']) for j in page)
            if len(page) < 100: break
            offset += 100
        return rows
    if board['provider'] == 'amazon':
        return amazon_jobs()
    if board['provider'] == 'smartrecruiters':
        return smartrecruiters(board)
    raise ValueError('Unknown job-board provider')

def location_matches(location, config):
    # Only location metadata is used: a description might mention an unrelated state.
    names = {'NC': 'North Carolina', 'SC': 'South Carolina'}
    if contains(location, 'remote'):
        return config['include_remote'] and any(contains(location, term) for term in ('USA', 'US', 'United States', *config['locations']))
    return any(contains(location, s) or contains(location, names.get(s, s)) for s in config['locations'])

def rank(job, config, resume):
    title = job['title']; body = job['description']
    if not location_matches(job['location'], config): return None
    if any(contains(title, t) for t in config['exclude_title_terms']): return None
    if re.search(r'\b(?:engineer|technician|specialist|associate)\s+(?:iii|iv|v)\b', title, re.I):
        return None
    roles = [t for t in config['target_titles'] if contains(title, t)]
    if not roles: return None
    if re.search(r'\b(?:[4-9]|[1-9][0-9])\s*(?:\+|or more)?\s+years?\s+of\b',
                 job.get('minimum_qualifications', ''), re.I):
        return None
    matched = [s for s in config['skill_terms'] if contains(resume, s) and contains(body, s)]
    missing = [s for s in config['skill_terms'] if contains(body, s) and not contains(resume, s)]
    score = min(100, 40 + 8 * len(matched))
    if score < config['minimum_score']: return None
    result = dict(job, score=score, matched_skills=matched, unverified_skills=missing)
    result['url'] = canonical(job['url'])
    # These are review flags, not assertions that the candidate is unqualified.
    result['review_flags'] = [s.strip() for s in re.split(r'[\n.!?]+', body)
        if re.search(r'\b(require[ds]?|must|minimum|years?|certification|clearance)\b', s, re.I)][:5]
    return result


def smartrecruiters(board):
    token = quote(board['token'], safe='')
    endpoint = f'https://api.smartrecruiters.com/v1/companies/{token}/postings'
    jobs = []
    offset = 0
    while True:
        data = get_json(f'{endpoint}?limit=100&offset={offset}&country=us')
        rows = data['content']
        for row in rows:
            place = row['location']
            location = place.get('fullLocation') or ', '.join(
                place.get(key, '') for key in ('city', 'region', 'country'))
            if not location_matches(location, {'locations': ['NC', 'SC'], 'include_remote': True}):
                continue
            if place.get('remote'):
                location += ' (Remote, US)'
            if not any(contains(row['name'], word) for word in
                       ('support', 'technician', 'analyst', 'network', 'service desk', 'help desk', 'systems')):
                continue
            details = get_json(f"{endpoint}/{quote(row['id'], safe='')}")
            sections = details.get('jobAd', {}).get('sections', {})
            jobs.append({
                'title': row['name'], 'company': board['company'], 'location': location,
                'description': ' '.join(plain(section.get('text')) for section in sections.values()),
                'url': details['applyUrl'], 'published': row.get('releasedDate'),
            })
        offset += len(rows)
        if not rows or offset >= data['totalFound']:
            return jobs

def amazon_jobs():
    jobs = []
    for state in ('North Carolina', 'South Carolina'):
        for term in ('IT Support Associate', 'Data Center Technician'):
            params = urlencode({
                'base_query': term,
                'normalized_country_code[]': 'USA',
                'normalized_state_name[]': state,
                'result_limit': 50,
            })
            response = get_json('https://www.amazon.jobs/en/search.json?' + params)
            for item in response['jobs']:
                path = item['job_path']
                if not path.startswith('/en/jobs/'):
                    continue
                minimum = plain(item.get('basic_qualifications'))
                jobs.append({
                    'title': item['title'], 'company': 'Amazon', 'location': item['location'],
                    'description': ' '.join((plain(item.get('description')), minimum,
                                             plain(item.get('preferred_qualifications')))),
                    'minimum_qualifications': minimum,
                    'url': 'https://www.amazon.jobs' + path,
                })
    return jobs
