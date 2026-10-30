"""Real MySQL regression tests; use ONLY a newly created disposable database."""
import os
os.environ['GOTTIME_AUTO_MIGRATE'] = '0'
import unittest
import uuid
import hashlib
import hmac
import json
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from unittest.mock import patch, Mock
import pymysql
import app as backend
from manage import add_indexes


class Flows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.name = 'gottime_audit_' + uuid.uuid4().hex[:12]
        cls.db_options = dict(host=backend.app.config['MYSQL_HOST'], user=backend.app.config['MYSQL_USER'],
                              password=backend.app.config['MYSQL_PASSWORD'], autocommit=True,
                              cursorclass=pymysql.cursors.DictCursor)
        cls.admin = pymysql.connect(**cls.db_options)
        with cls.admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE `{cls.name}`')
        backend.app.config.update(MYSQL_DB=cls.name, TESTING=True)
        with backend.app.app_context():
            backend.initialize_database()
            add_indexes()

    @classmethod
    def tearDownClass(cls):
        # Generated name created by this test run; never the configured application DB.
        assert cls.name.startswith('gottime_audit_') and len(cls.name) == 26
        with cls.admin.cursor() as cursor:
            cursor.execute(f'DROP DATABASE `{cls.name}`')
        cls.admin.close()

    def sql(self, query, params=()):
        with backend.app.app_context():
            with backend.get_db().cursor() as cursor:
                cursor.execute(query, params)
                return cursor.fetchall() if cursor.description else cursor.lastrowid

    def user(self, age=20, balance=10):
        born = backend.app_now().date().replace(year=backend.app_now().year - age)
        uid = self.sql('INSERT INTO users(username,email,password,birthdate,age) VALUES (%s,%s,%s,%s,%s)',
                       ('Test', uuid.uuid4().hex+'@example.test', backend.generate_password_hash('correct-pass'), born, age))
        self.sql('INSERT INTO credits(user_id,balance) VALUES (%s,%s)', (uid,balance))
        return uid

    def setUp(self):
        self.learner, self.mentor = self.user(), self.user()
        self.skill = self.sql("INSERT INTO skills(name,category,created_by) VALUES ('Python','Programming',%s)", (self.mentor,))
        self.sql("INSERT INTO user_skills(user_id,skill_id,availability,preferred_time) VALUES (%s,%s,'both','17:00-21:00')", (self.mentor,self.skill))
        self.time = (backend.app_now()+timedelta(days=2)).replace(hour=18,minute=0,second=0,microsecond=0)

    def request(self, uid, path, body=None, method='POST'):
        with backend.app.test_client() as client:
            return client.open(path, method=method, json=body, headers={'X-GotTime-Auth':backend.make_auth_token(uid)})

    def session(self, status='scheduled', joined=False, when=None, mode='online'):
        return self.sql('''INSERT INTO sessions(learner_id,mentor_id,skill_id,status,scheduled_for,mode,learner_joined_at,mentor_joined_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
                        (self.learner,self.mentor,self.skill,status,when or self.time,mode,
                         backend.app_now() if joined else None,backend.app_now() if joined else None))

    def booking(self, learner=None, when=None, **extra):
        return self.request(learner or self.learner, '/api/sessions', {'mentorId':self.mentor,'skillId':self.skill,
            'mode':'online','scheduledFor':(when or self.time).isoformat(), **extra})

    def test_parallel_confirm_transfers_once(self):
        sid = self.session('awaiting_confirmation', True)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.request(self.learner,f'/api/sessions/{sid}/confirm').status_code, range(12)))
        self.assertEqual(results.count(200), 1, results)
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s',(self.learner,))[0]['balance'],9)
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM transactions WHERE related_session_id=%s',(sid,))[0]['n'],2)

    def test_profile_session_list_matches_count_and_hides_private_details(self):
        self.session('completed')
        self.session('completed')
        self.session('pending')
        self.session('missed')
        self.sql("INSERT INTO sessions(learner_id,mentor_id,skill_id,status) VALUES (%s,%s,%s,'completed')",
                 (self.mentor, self.learner, self.skill))
        path = f'/api/users/{self.mentor}/completed-sessions'
        own = self.request(self.mentor, path, method='GET')
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own.json['total'], 3)
        self.assertEqual(len(own.json['sessions']), 3)
        self.assertEqual({row['role'] for row in own.json['sessions']}, {'Taught', 'Learned'})
        self.assertTrue(all(row['counterpart_name'] for row in own.json['sessions']))
        self.assertEqual(self.request(self.mentor, '/api/app', method='GET').json['user']['sessions_completed'], own.json['total'])
        other = self.request(self.user(), path, method='GET').json
        self.assertTrue(all(row['counterpart_name'] is None for row in other['sessions']))
        self.assertEqual(set(other['sessions'][0]), {'id', 'skill_name', 'session_date', 'role', 'counterpart_name'})

    def test_profile_session_list_pagination_and_auth(self):
        for _ in range(21):
            self.session('completed')
        path = f'/api/users/{self.mentor}/completed-sessions'
        first = self.request(self.learner, path, method='GET').json
        second = self.request(self.learner, path+'?page=2', method='GET').json
        self.assertEqual(first['total'], 21)
        self.assertEqual(len(first['sessions']), 20)
        self.assertTrue(first['has_more'])
        self.assertEqual(len(second['sessions']), 1)
        self.assertFalse(second['has_more'])
        self.assertFalse({row['id'] for row in first['sessions']} & {row['id'] for row in second['sessions']})
        for page in ['0', '-1', 'bad']:
            self.assertEqual(self.request(self.learner, path+'?page='+page, method='GET').status_code, 400)
        self.assertEqual(self.request(self.learner, '/api/users/0/completed-sessions', method='GET').status_code, 404)
        with backend.app.test_client() as client:
            self.assertEqual(client.get(path).status_code, 401)

    def test_activity_calendar_records_real_days_once_across_months_and_gaps(self):
        today = backend.today_ist().replace(month=10, day=2)
        for offset in [-3, -2, 0, 0]:
            with patch.object(backend, 'today_ist', return_value=today + timedelta(days=offset)), backend.app.app_context():
                backend.record_app_visit(self.mentor)
        path = f'/api/users/{self.mentor}/activity'
        with patch.object(backend, 'today_ist', return_value=today):
            october = self.request(self.mentor, path, method='GET').json
            september = self.request(self.learner, path+f'?month={today.year}-09', method='GET').json
            self.assertEqual(october['active_days'], [today.isoformat()])
            self.assertEqual(september['active_days'], [(today-timedelta(days=3)).isoformat(), (today-timedelta(days=2)).isoformat()])
            self.assertEqual(october['streak'], 1)
            self.assertEqual(self.request(self.mentor, f'/api/users/{self.learner}/activity', method='GET').json['active_days'], [])
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM user_activity_days WHERE user_id=%s', (self.mentor,))[0]['n'], 3)

    def test_activity_calendar_recovers_only_the_recorded_consecutive_run(self):
        today = backend.today_ist().replace(day=15)
        last = today - timedelta(days=1)
        self.sql('UPDATE users SET streak=4,last_visit_on=%s WHERE id=%s', (last,self.mentor))
        with patch.object(backend, 'today_ist', return_value=today), backend.app.app_context():
            backend.record_app_visit(self.mentor)
        path = f'/api/users/{self.mentor}/activity'
        with patch.object(backend, 'today_ist', return_value=today):
            result = self.request(self.learner, path, method='GET').json
        self.assertEqual(result['active_days'], [(today - timedelta(days=offset)).isoformat() for offset in range(4, -1, -1)])
        self.assertEqual(result['streak'], 5)
        self.assertIn('Older visits', result['history_note'])
        for month in ['bad', '2026-13', '2026-1', '1999-12', '2026-01-01']:
            self.assertEqual(self.request(self.mentor, path+'?month='+month, method='GET').status_code, 400)
        with backend.app.test_client() as client:
            self.assertEqual(client.get(path).status_code, 401)
        self.assertEqual(self.request(self.mentor, '/api/users/0/activity', method='GET').status_code, 404)

    def test_activity_calendar_two_day_legacy_streak_highlights_both_days(self):
        today = date(2026, 10, 30)
        self.sql('UPDATE users SET streak=2,last_visit_on=%s WHERE id=%s', (today, self.mentor))
        self.sql('INSERT INTO user_activity_days(user_id,activity_date) VALUES (%s,%s)', (self.mentor, today))
        path = f'/api/users/{self.mentor}/activity'
        with patch.object(backend, 'today_ist', return_value=today):
            result = self.request(self.learner, path, method='GET').json
        self.assertEqual(result['streak'], 2)
        self.assertEqual(result['active_days'], ['2026-10-29', '2026-10-30'])
        # A later gap must not erase the recovered older run.
        later = date(2026, 11, 2)
        with patch.object(backend, 'today_ist', return_value=later), backend.app.app_context():
            backend.record_app_visit(self.mentor)
        with patch.object(backend, 'today_ist', return_value=later):
            history = self.request(self.mentor, path+'?month=2026-10', method='GET').json
        self.assertEqual(history['streak'], 1)
        self.assertEqual(history['active_days'], ['2026-10-29', '2026-10-30'])

    def test_activity_calendar_streak_spanning_months_stays_in_its_actual_dates(self):
        today = date(2027, 1, 1)
        self.sql('UPDATE users SET streak=2,last_visit_on=%s WHERE id=%s', (today, self.mentor))
        path = f'/api/users/{self.mentor}/activity'
        with patch.object(backend, 'today_ist', return_value=today):
            january = self.request(self.learner, path, method='GET').json
            december = self.request(self.learner, path+'?month=2026-12', method='GET').json
        self.assertEqual(january['streak'], 2)
        self.assertEqual(january['active_days'], ['2027-01-01'])
        self.assertEqual(december['active_days'], ['2026-12-31'])

    def test_recorded_streak_recovery_excludes_future_and_unknown_history(self):
        today = date(2026, 10, 30)
        self.assertEqual(backend.recorded_streak_days(None, 7, today), set())
        self.assertEqual(backend.recorded_streak_days(today + timedelta(days=1), 2, today), set())
        self.assertEqual(backend.recorded_streak_days(today, 0, today), {today})

    def test_missed_notice_is_persistent_per_account_and_session(self):
        sid = self.session('missed')
        path = '/api/sessions/missed-notices/claim'
        payload = {'session_ids': [sid]}
        self.assertEqual(self.request(self.mentor, path, payload).json['session_ids'], [sid])
        # Each request has a new browser session; no local storage is needed.
        self.assertEqual(self.request(self.mentor, path, payload).json['session_ids'], [])
        self.assertEqual(self.request(self.learner, path, payload).json['session_ids'], [sid])
        second = self.session('missed')
        self.assertEqual(self.request(self.mentor, path, {'session_ids': [sid, second]}).json['session_ids'], [second])
        self.assertEqual(self.sql('SELECT status FROM sessions WHERE id=%s', (sid,))[0]['status'], 'missed')
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s', (self.learner,))[0]['balance'], 10)
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM transactions WHERE related_session_id=%s', (sid,))[0]['n'], 0)

    def test_parallel_missed_notice_claims_display_only_once(self):
        sid = self.session('missed')
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.request(self.mentor, '/api/sessions/missed-notices/claim', {'session_ids': [sid]}).json['session_ids'], range(12)))
        self.assertEqual(sum(sid in ids for ids in results), 1)
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM missed_session_notices WHERE session_id=%s AND user_id=%s', (sid, self.mentor))[0]['n'], 1)

    def test_missed_notice_imports_old_dismissals_and_checks_ownership(self):
        old, new, pending = self.session('missed'), self.session('missed'), self.session('pending')
        path = '/api/sessions/missed-notices/claim'
        self.assertEqual(self.request(self.mentor, path, {'session_ids': [old, new, pending], 'previously_seen': [old]}).json['session_ids'], [new])
        self.assertEqual(self.request(self.mentor, path, {'session_ids': [old, new]}).json['session_ids'], [])
        other = self.user()
        self.assertEqual(self.request(other, path, {'session_ids': [old, new], 'previously_seen': [old]}).json['session_ids'], [])
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM missed_session_notices WHERE user_id=%s', (other,))[0]['n'], 0)
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM missed_session_notices WHERE session_id=%s', (pending,))[0]['n'], 0)

    def test_missed_notice_validation_and_additive_bootstrap(self):
        path = '/api/sessions/missed-notices/claim'
        with backend.app.test_client() as client:
            self.assertEqual(client.post(path, json={'session_ids': [1]}).status_code, 401)
        for payload in [[], {}, {'session_ids': '1'}, {'session_ids': [True]}, {'session_ids': [-1]},
                        {'session_ids': list(range(1, 502))}, {'session_ids': [1], 'previously_seen': '1'}]:
            self.assertEqual(self.request(self.mentor, path, payload).status_code, 400)
        sid = self.session('missed')
        with patch.object(backend, 'db_table_exists', return_value=False):
            self.assertEqual(self.request(self.mentor, path, {'session_ids': [sid]}).json['session_ids'], [sid])
            self.assertEqual(self.request(self.mentor, path, {'session_ids': [sid]}).json['session_ids'], [])

    def test_mentor_reviews_match_profile_average_and_show_optional_feedback(self):
        self.sql('UPDATE credits SET balance=100000 WHERE user_id=%s', (self.mentor,))
        sid1, sid2 = self.session('completed'), self.session('completed')
        self.assertEqual(self.request(self.learner, f'/api/sessions/{sid1}/rating', {'score': 5, 'feedback': 'Very clear explanations'}).status_code, 200)
        self.assertEqual(self.request(self.learner, f'/api/sessions/{sid2}/rating', {'score': 3}).status_code, 200)
        # Two skills must not duplicate ratings in the directory.
        second = self.sql("INSERT INTO skills(name,category) VALUES ('Singing','Music')")
        self.sql('INSERT INTO user_skills(user_id,skill_id) VALUES (%s,%s)', (self.mentor, second))
        other = self.user(balance=0)
        result = self.request(other, f'/api/mentors/{self.mentor}/reviews', method='GET')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(float(result.json['summary']['rating']), 4)
        self.assertEqual(result.json['summary']['rating_count'], 2)
        self.assertEqual(result.json['summary']['review_count'], 1)
        self.assertFalse(result.json['has_more'])
        entries = result.json['reviews']
        self.assertEqual([entry['score'] for entry in entries], [3, 5])
        self.assertIsNone(entries[0]['feedback'])
        self.assertEqual(entries[1]['feedback'], 'Very clear explanations')
        self.assertEqual(set(entries[0]), {'id', 'score', 'skill_name', 'session_date', 'feedback'})
        self.assertEqual(entries[0]['skill_name'], 'Python')
        self.assertEqual(set(result.json['mentor']), {'id', 'username'})
        app_data = self.request(other, '/api/app', method='GET').json
        for source in ['mentors', 'leaderboard']:
            matches = [person for person in app_data[source] if person['id'] == self.mentor]
            self.assertTrue(matches)
            for person in matches:
                self.assertEqual(float(person['rating']), 4)
                self.assertEqual(person['rating_count'], 2)
                self.assertEqual(person['review_count'], 1)
        own = self.request(self.mentor, '/api/auth/me', method='GET').json['user']
        self.assertEqual(float(own['rating']), 4)
        self.assertEqual(own['review_count'], 1)
        # Editing the rating/removing feedback updates the average and list.
        self.request(self.learner, f'/api/sessions/{sid1}/rating', {'score': 1, 'feedback': ''})
        updated = self.request(other, f'/api/mentors/{self.mentor}/reviews', method='GET').json
        self.assertEqual(float(updated['summary']['rating']), 2)
        self.assertEqual(updated['summary']['rating_count'], 2)
        self.assertEqual(updated['summary']['review_count'], 0)

    def test_mentor_reviews_filter_invalid_sessions_and_keep_mentors_separate(self):
        valid = self.session('completed')
        invalid = self.session('pending')
        self.request(self.learner, f'/api/sessions/{valid}/rating', {'score': 4, 'feedback': 'Good session'})
        for sid, ratee, rater in [(invalid, self.mentor, self.learner), (valid, self.learner, self.mentor)]:
            self.sql('INSERT INTO ratings(session_id,ratee_id,rater_id,score) VALUES (%s,%s,%s,5)', (sid, ratee, rater))
        self.sql('INSERT INTO reviews(session_id,reviewer_id,reviewee_id,content) VALUES (%s,%s,%s,%s)',
                 (valid, self.learner, self.mentor, 'Latest feedback'))
        result = self.request(self.learner, f'/api/mentors/{self.mentor}/reviews', method='GET').json
        self.assertEqual(result['summary']['rating_count'], 1)
        self.assertEqual(result['summary']['review_count'], 1)
        self.assertEqual(result['reviews'][0]['feedback'], 'Latest feedback')
        empty = self.request(self.mentor, f'/api/mentors/{self.learner}/reviews', method='GET').json
        self.assertEqual(empty['reviews'], [])
        self.assertEqual(float(empty['summary']['rating']), 0)

    def test_mentor_reviews_paginate_and_require_valid_authenticated_requests(self):
        for _ in range(21):
            sid = self.session('completed')
            self.sql('INSERT INTO ratings(session_id,rater_id,ratee_id,score) VALUES (%s,%s,%s,5)', (sid,self.learner,self.mentor))
        path = f'/api/mentors/{self.mentor}/reviews'
        first = self.request(self.learner, path, method='GET').json
        second = self.request(self.learner, path+'?page=2', method='GET').json
        self.assertEqual(len(first['reviews']), 20)
        self.assertTrue(first['has_more'])
        self.assertEqual(len(second['reviews']), 1)
        self.assertFalse(second['has_more'])
        self.assertFalse({row['id'] for row in first['reviews']} & {row['id'] for row in second['reviews']})
        self.assertEqual(first['summary'], second['summary'])
        for page in ['0', '-1', 'bad', '100001']:
            self.assertEqual(self.request(self.learner, path+'?page='+page, method='GET').status_code, 400)
        self.assertEqual(self.request(self.learner, '/api/mentors/0/reviews', method='GET').status_code, 404)
        with backend.app.test_client() as client:
            self.assertEqual(client.get(path).status_code, 401)
        self.sql('UPDATE users SET is_active=0 WHERE id=%s', (self.mentor,))
        self.assertEqual(self.request(self.learner, path, method='GET').status_code, 404)

    def test_birthdays_show_other_active_users_without_private_birthdate(self):
        self.sql("UPDATE users SET birthdate='2000-12-31' WHERE id IN (%s,%s)", (self.learner, self.mentor))
        inactive = self.user()
        self.sql("UPDATE users SET birthdate='1990-12-31',is_active=0 WHERE id=%s", (inactive,))
        other_day = self.user()
        self.sql("UPDATE users SET birthdate='2000-01-01' WHERE id=%s", (other_day,))
        missing = self.user()
        self.sql('UPDATE users SET birthdate=NULL WHERE id=%s', (missing,))
        with patch.object(backend, 'today_ist', return_value=date(2026, 12, 31)):
            result = self.request(self.learner, '/api/birthdays/today', method='GET')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['date'], '2026-12-31')
        self.assertEqual([person['id'] for person in result.json['people']], [self.mentor])
        self.assertEqual(set(result.json['people'][0]), {'id', 'username'})
        with backend.app.test_client() as client:
            self.assertEqual(client.get('/api/birthdays/today').status_code, 401)

    def test_birthdays_follow_server_day_and_handle_leap_day(self):
        self.sql("UPDATE users SET birthdate='2000-02-29' WHERE id=%s", (self.mentor,))
        for day, expected in [(date(2028, 2, 28), False), (date(2028, 2, 29), True), (date(2028, 3, 1), False), (date(2027, 2, 28), False)]:
            with patch.object(backend, 'today_ist', return_value=day):
                result = self.request(self.learner, '/api/birthdays/today', method='GET')
            self.assertEqual(result.json['date'], day.isoformat())
            self.assertEqual(self.mentor in [person['id'] for person in result.json['people']], expected)

    def test_community_feedback_persists_and_uses_authenticated_author(self):
        self.sql('UPDATE users SET username=%s WHERE id=%s', ('Actual author', self.learner))
        result = self.request(self.learner, '/api/feedback', {'message': '  My real feedback  ',
                              'userId': self.mentor, 'name': 'Spoofed', 'email': 'private@example.test'})
        self.assertEqual(result.status_code, 201)
        story = result.json['story']
        self.assertEqual(story['name'], 'Actual author')
        self.assertEqual(story['message'], 'My real feedback')
        self.assertEqual(set(story), {'id', 'name', 'message', 'created_at'})
        self.assertEqual(self.sql('SELECT user_id FROM community_feedback WHERE id=%s', (story['id'],))[0]['user_id'], self.learner)
        # A different browser/user reads the same persisted submission.
        listing = self.request(self.mentor, '/api/feedback', method='GET')
        self.assertEqual(listing.status_code, 200)
        self.assertIn(story, listing.json['stories'])
        self.sql('UPDATE users SET is_active=0 WHERE id=%s', (self.learner,))
        listing = self.request(self.mentor, '/api/feedback', method='GET')
        self.assertNotIn(story, listing.json['stories'])

    def test_community_feedback_validates_input_and_requires_login(self):
        for message in ['', '   ', 123, ['text'], 'x' * 2001, None]:
            self.assertEqual(self.request(self.learner, '/api/feedback', {'message': message}).status_code, 400)
        self.assertEqual(self.request(self.learner, '/api/feedback', ['invalid']).status_code, 400)
        self.assertEqual(self.request(self.learner, '/api/feedback', {'message': 'x' * 2000}).status_code, 201)
        with backend.app.test_client() as client:
            self.assertEqual(client.get('/api/feedback').status_code, 401)
            self.assertEqual(client.post('/api/feedback', json={'message': 'anonymous'}).status_code, 401)

    def test_community_feedback_initializes_missing_storage(self):
        # Preserve fixtures; this runs only inside this suite's disposable database.
        assert backend.app.config['MYSQL_DB'] == self.name
        self.sql('RENAME TABLE community_feedback TO feedback_test_backup')
        backend._schema_cache.clear()
        try:
            result = self.request(self.learner, '/api/feedback', method='GET')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json['stories'], [])
            saved = self.request(self.learner, '/api/feedback', {'message': 'Works after upgrade'})
            self.assertEqual(saved.status_code, 201)
            # Repeated initialization must preserve submissions.
            with backend.app.app_context():
                backend.ensure_community_feedback_table()
            listing = self.request(self.mentor, '/api/feedback', method='GET')
            self.assertIn(saved.json['story'], listing.json['stories'])
        finally:
            self.sql('DROP TABLE IF EXISTS community_feedback')
            self.sql('RENAME TABLE feedback_test_backup TO community_feedback')

    def test_community_feedback_can_initialize_on_first_submission(self):
        assert backend.app.config['MYSQL_DB'] == self.name
        self.sql('RENAME TABLE community_feedback TO feedback_test_backup')
        backend._schema_cache.clear()
        try:
            result = self.request(self.learner, '/api/feedback', {'message': 'First submission'})
            self.assertEqual(result.status_code, 201)
            self.assertEqual(result.json['story']['message'], 'First submission')
        finally:
            self.sql('DROP TABLE IF EXISTS community_feedback')
            self.sql('RENAME TABLE feedback_test_backup TO community_feedback')

    def test_community_feedback_initialization_failure_can_retry(self):
        with patch.object(backend, 'db_table_exists', return_value=False), \
                patch.object(backend, 'ensure_community_feedback_table', side_effect=RuntimeError('Unavailable')):
            with self.assertLogs(backend.app.logger, level='ERROR'):
                self.assertEqual(self.request(self.learner, '/api/feedback', method='GET').status_code, 503)
                self.assertEqual(self.request(self.learner, '/api/feedback', {'message': 'not saved'}).status_code, 503)
        self.assertEqual(self.request(self.learner, '/api/feedback', method='GET').status_code, 200)

    def test_app_visit_streak_starts_repeats_advances_and_restarts(self):
        today = backend.today_ist()
        for offset, expected in [(0, 1), (0, 1), (1, 2), (2, 3), (4, 1), (4, 1)]:
            with patch.object(backend, 'today_ist', return_value=today + timedelta(days=offset)):
                result = self.request(self.learner, '/api/app', method='GET')
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json['user']['streak'], expected)
                self.assertEqual(self.request(self.learner, '/api/auth/me', method='GET').json['user']['streak'], expected)
        row = self.sql('SELECT streak,last_visit_on FROM users WHERE id=%s', (self.learner,))[0]
        self.assertEqual(row, {'streak': 1, 'last_visit_on': today + timedelta(days=4)})
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s', (self.learner,))[0]['balance'], 10)

    def test_parallel_app_visits_increment_streak_only_once(self):
        self.sql('UPDATE users SET streak=4,last_visit_on=%s WHERE id=%s',
                 (backend.today_ist() - timedelta(days=1), self.learner))
        def visit(_):
            with backend.app.app_context():
                backend.record_app_visit(self.learner)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(visit, range(12)))
        self.assertEqual(self.sql('SELECT streak FROM users WHERE id=%s', (self.learner,))[0]['streak'], 5)

    def test_streak_expires_in_profiles_and_does_not_count_other_users_visits(self):
        today = backend.today_ist()
        self.sql('UPDATE credits SET balance=100000 WHERE user_id=%s', (self.mentor,))
        self.sql('UPDATE users SET streak=7,last_visit_on=%s WHERE id=%s', (today - timedelta(days=2), self.mentor))
        result = self.request(self.learner, '/api/app', method='GET')
        for source in ['mentors', 'leaderboard']:
            mentor = next(row for row in result.json[source] if row['id'] == self.mentor)
            self.assertEqual(mentor['streak'], 0)
        self.assertEqual(self.request(self.mentor, '/api/auth/me', method='GET').json['user']['streak'], 0)
        self.assertEqual(self.sql('SELECT last_visit_on FROM users WHERE id=%s', (self.mentor,))[0]['last_visit_on'], today - timedelta(days=2))
        self.assertEqual(self.request(self.mentor, '/api/app', method='GET').json['user']['streak'], 1)

    def test_anonymous_app_request_does_not_record_a_visit(self):
        with backend.app.test_client() as client:
            self.assertEqual(client.get('/api/app').status_code, 401)
        self.assertIsNone(self.sql('SELECT last_visit_on FROM users WHERE id=%s', (self.learner,))[0]['last_visit_on'])

    def test_visit_tracking_waits_for_schema_upgrade(self):
        with patch.object(backend, 'db_column_exists', return_value=False), patch.object(backend, 'get_db') as database:
            backend.record_app_visit(self.learner)
            self.assertEqual(backend.active_streak_select(), '0 AS streak')
            database.assert_not_called()

    def test_failed_completion_rolls_everything_back(self):
        sid = self.session('awaiting_confirmation', True)
        with patch.object(backend, 'award_badges', side_effect=RuntimeError('simulate failure')):
            with self.assertRaises(RuntimeError): self.request(self.learner,f'/api/sessions/{sid}/confirm')
        self.assertEqual(self.sql('SELECT status FROM sessions WHERE id=%s',(sid,))[0]['status'],'awaiting_confirmation')
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s',(self.learner,))[0]['balance'],10)
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM transactions WHERE related_session_id=%s',(sid,))[0]['n'],0)

    def test_parallel_booking_prevents_overlap(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.booking().status_code, range(8)))
        self.assertEqual(results.count(201),1,results)
        self.assertEqual(results.count(409),7,results)

    def availability(self, when=None, uid=None, **extra):
        return self.request(uid or self.learner, '/api/sessions/availability', {
            'mentorId': self.mentor, 'skillId': self.skill,
            'scheduledFor': (when or self.time).isoformat(), **extra})

    def test_live_clash_preview_and_back_to_back_bookings(self):
        sid = self.session('pending')
        for minutes in [-59, 0, 30, 59]:
            result = self.availability(self.time + timedelta(minutes=minutes))
            self.assertEqual(result.status_code, 200)
            self.assertFalse(result.json['available'])
            self.assertIn('You already have Python', result.json['message'])
            self.assertIn(self.time.strftime('%d %b %Y'), result.json['message'])
            self.assertIn('06:00 PM', result.json['message'])
            self.assertIn('07:00 PM', result.json['message'])
        for minutes in [-60, 60]:
            self.assertTrue(self.availability(self.time + timedelta(minutes=minutes)).json['available'])
        self.assertEqual(self.booking(when=self.time + timedelta(minutes=30)).status_code, 409)
        self.assertEqual(self.booking(when=self.time + timedelta(minutes=60)).status_code, 201)
        self.assertEqual(self.sql('SELECT status FROM sessions WHERE id=%s', (sid,))[0]['status'], 'pending')

    def test_clashes_cover_both_roles_and_hide_other_users_details(self):
        self.session()
        other = self.user()
        result = self.availability(uid=other)
        self.assertFalse(result.json['available'])
        self.assertEqual(result.json['message'], 'The mentor already has a session during that time. Choose another time.')
        # Our learner is teaching another session, even though the selected mentor is free.
        self.sql('UPDATE sessions SET mentor_id=%s, learner_id=%s WHERE mentor_id=%s', (self.learner, other, self.mentor))
        self.assertIn('You already have', self.availability().json['message'])
        self.assertEqual(self.booking().status_code, 409)

    def test_live_clash_statuses_and_cross_midnight_duration(self):
        self.sql('UPDATE user_skills SET duration=120 WHERE user_id=%s', (self.mentor,))
        start = self.time.replace(hour=23, minute=30)
        sid = self.session(when=start)
        for status in ['pending', 'accepted', 'scheduled', 'awaiting_confirmation']:
            self.sql('UPDATE sessions SET status=%s WHERE id=%s', (status, sid))
            self.assertFalse(self.availability(start + timedelta(minutes=90)).json['available'])
            self.assertTrue(self.availability(start + timedelta(minutes=120)).json['available'])
        for status in ['cancelled', 'completed', 'missed']:
            self.sql('UPDATE sessions SET status=%s WHERE id=%s', (status, sid))
            self.assertTrue(self.availability(start).json['available'])

    def test_preview_uses_requested_duration_and_rechecks_on_booking(self):
        self.sql('UPDATE user_skills SET duration=120 WHERE user_id=%s', (self.mentor,))
        self.assertTrue(self.availability().json['available'])
        self.session(when=self.time + timedelta(minutes=90))
        self.assertFalse(self.availability().json['available'])
        self.assertEqual(self.booking().status_code, 409)

    def test_rescheduling_and_chat_use_actual_skill_duration(self):
        self.sql('UPDATE user_skills SET duration=120 WHERE user_id=%s', (self.mentor,))
        sid = self.session(when=self.time + timedelta(days=1))
        other = self.user()
        self.sql('INSERT INTO sessions(learner_id,mentor_id,skill_id,status,scheduled_for) VALUES (%s,%s,%s,%s,%s)',
                 (other, self.mentor, self.skill, 'scheduled', self.time + timedelta(minutes=90)))
        body = {'scheduledFor': self.time.isoformat()}
        self.assertEqual(self.request(self.mentor, f'/api/sessions/{sid}/schedule', body).status_code, 409)
        self.assertEqual(self.request(self.mentor, f'/api/messages/{self.learner}/schedule', body).status_code, 409)
        self.assertEqual(self.request(self.mentor, f'/api/messages/{self.user()}/schedule', body).status_code, 409)
        # The session itself must not prevent a no-op reschedule.
        self.assertEqual(self.request(self.mentor, f'/api/sessions/{sid}/schedule', {'scheduledFor': (self.time + timedelta(days=1)).isoformat()}).status_code, 200)

    def test_availability_validates_inputs_and_does_not_create_bookings(self):
        for extra in [{'mentorId': 'invalid'}, {'skillId': -1}, {'scheduledFor': 'invalid'},
                      {'scheduledFor': backend.app_now().isoformat()}, {'mentorId': self.learner}]:
            self.assertEqual(self.availability(**extra).status_code, 400)
        self.assertTrue(self.availability().json['available'])
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM sessions WHERE learner_id=%s', (self.learner,))[0]['n'], 0)
        with backend.app.test_client() as client:
            self.assertEqual(client.post('/api/sessions/availability', json={}).status_code, 401)

    def test_legacy_booking_cannot_bypass_clash_check(self):
        self.session()
        with backend.app.test_client() as client:
            with client.session_transaction() as state:
                state['user_id'] = self.learner
            result = client.post('/sessions', data={'mentor_id': self.mentor, 'skill_id': self.skill,
                                                    'scheduled_for': self.time.isoformat(), 'mode': 'online'})
            self.assertEqual(result.status_code, 410)  # Retired form routes stay disabled.
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM sessions WHERE learner_id=%s', (self.learner,))[0]['n'], 1)

    def test_credit_commitments_and_range(self):
        self.sql('UPDATE credits SET balance=1 WHERE user_id=%s',(self.learner,))
        self.assertEqual(self.booking().status_code,201)
        self.assertEqual(self.booking(when=self.time+timedelta(days=1)).status_code,409)
        other=self.user()
        self.assertEqual(self.booking(learner=other,when=self.time.replace(hour=10)).status_code,400)

    def test_chat_schedule_has_no_time_range_but_checks_conflicts(self):
        body={'scheduledFor': self.time.replace(hour=10).isoformat()}
        r=self.request(self.mentor,f'/api/messages/{self.learner}/schedule',body)
        self.assertEqual(r.status_code,200,r.json)
        sid=r.json['session']['id']
        self.assertEqual(self.request(self.mentor,f'/api/sessions/{sid}/schedule',{'scheduledFor':self.time.replace(hour=11).isoformat()}).status_code,200)

    def test_guardian_checks_booking_and_join(self):
        for age, mode, required in [(12,'online',True),(13,'online',False),(15,'offline',True),(16,'offline',False)]:
            uid=self.user(age)
            self.learner=uid
            sid=self.session(mode=mode)
            r=self.request(uid,f'/api/sessions/{sid}/join',{'guardianConfirmed':'false'})
            self.assertEqual(r.status_code,403 if required else 200,r.json)
            self.assertEqual(self.request(uid,f'/api/sessions/{sid}/join',{'guardianConfirmed':True}).status_code,200)
        self.learner=self.user(12)
        self.assertEqual(self.booking().status_code,400)
        self.assertEqual(self.booking(guardianConfirmed='true').status_code,400)

    def test_completed_and_missed_cannot_reopen(self):
        for state in ['completed','missed','cancelled','awaiting_confirmation']:
            sid=self.session(state)
            self.assertEqual(self.request(self.mentor,f'/api/sessions/{sid}/accept').status_code,409)
            self.assertEqual(self.request(self.mentor,f'/api/sessions/{sid}/schedule',{'scheduledFor':self.time.isoformat()}).status_code,400)

    def test_first_teaching_badge_requires_completed_mentor_session_and_awards_once(self):
        sid = self.session('awaiting_confirmation', joined=True)
        with backend.app.app_context():
            self.assertNotIn('First Session Taught', backend.award_badges(self.mentor))
        result = self.request(self.learner,f'/api/sessions/{sid}/confirm')
        self.assertEqual(result.status_code,200,result.json)
        for _ in range(2):
            mentor_badges = self.request(self.mentor,'/api/app',method='GET').json['badges']
            self.assertEqual(len([b for b in mentor_badges if b['name']=='First Session Taught']),1)
            self.assertNotIn('10 Hours Taught',[b['name'] for b in mentor_badges])
            learner_badges = self.request(self.learner,'/api/app',method='GET').json['badges']
            self.assertNotIn('First Session Taught',[b['name'] for b in learner_badges])
            with backend.app.app_context():
                self.assertNotIn('First Session Taught',backend.award_badges(self.mentor))
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM notifications WHERE user_id=%s AND message='You unlocked the First Session Taught badge.'",(self.mentor,))[0]['n'],1)

    def test_existing_teaching_badge_keeps_identity_and_earned_date(self):
        badge_id = self.sql("SELECT id FROM badges WHERE name='10 Hours Taught'")[0]['id']
        earned_at = '2026-09-01 12:00:00'
        self.sql('INSERT INTO user_badges(user_id,badge_id,earned_at) VALUES (%s,%s,%s)',(self.mentor,badge_id,earned_at))
        response = self.request(self.mentor,'/api/app',method='GET')
        self.assertEqual(response.status_code,200,response.json)
        badge = next(b for b in response.json['badges'] if b['id']==badge_id)
        self.assertEqual(badge['name'],'First Session Taught')
        self.assertEqual(badge['description'],'Completed your first session as a mentor.')
        rows = self.sql('SELECT badge_id,earned_at FROM user_badges WHERE user_id=%s',(self.mentor,))
        self.assertEqual(len(rows),1)
        self.assertEqual(str(rows[0]['earned_at']),earned_at)

    def test_acceptance_lists_both_chat_partners_without_messages(self):
        sid = self.session('pending')
        def contacts(uid):
            response = self.request(uid,'/api/app',method='GET')
            self.assertEqual(response.status_code,200,response.json)
            return response.json['contacts']
        self.assertNotIn(self.learner,[c['id'] for c in contacts(self.mentor)])
        self.assertNotIn(self.mentor,[c['id'] for c in contacts(self.learner)])
        result = self.request(self.mentor,f'/api/sessions/{sid}/accept')
        self.assertEqual(result.status_code,200,result.json)
        for uid, other in ((self.mentor,self.learner),(self.learner,self.mentor)):
            found = [c for c in contacts(uid) if c['id'] == other]
            self.assertEqual(len(found),1)
            self.assertTrue(found[0]['username'])
            self.assertIsNone(found[0]['last_message'])
            self.assertEqual(found[0]['unread_count'],0)
            self.assertEqual(self.request(uid,f'/api/messages/{other}',method='GET').json['messages'],[])
        outsider = self.user()
        self.assertEqual(contacts(outsider),[])
        # Older accepted sessions are included automatically; multiple sessions do not duplicate contacts.
        self.session('scheduled',when=self.time+timedelta(days=1))
        self.assertEqual([c['id'] for c in contacts(self.mentor)].count(self.learner),1)
        self.sql("UPDATE sessions SET status='completed' WHERE id=%s",(sid,))
        self.assertIn(self.learner,[c['id'] for c in contacts(self.mentor)])

    def test_accepted_mentor_can_start_encrypted_chat_and_learner_reply(self):
        self.session('accepted')
        for sender, receiver, message in ((self.mentor,self.learner,'Meeting link: https://meet.google.com/abc-defg-hij'),
                                          (self.learner,self.mentor,'Thanks, see you there!')):
            # Encryption itself is exercised by chatCrypto.test.js; the API stores an envelope unchanged.
            envelope = {'audit':message}
            result = self.request(sender,f'/api/messages/{receiver}',{'content':message,'encryptedContent':envelope})
            self.assertEqual(result.status_code,201,result.json)
            # Messages sent in the same second must still preserve send order.
            self.sql("UPDATE messages SET created_at='2026-10-01 12:00:00' WHERE (sender_id=%s AND receiver_id=%s) OR (sender_id=%s AND receiver_id=%s)",
                     (self.mentor,self.learner,self.learner,self.mentor))
            thread = self.request(receiver,f'/api/messages/{sender}',method='GET').json['messages']
            self.assertEqual(thread[-1]['sender_id'],sender)
            self.assertEqual(json.loads(thread[-1]['encrypted_content']),envelope)
        self.assertEqual(len(thread),2)

    def test_pending_declined_and_inactive_users_are_not_new_chat_contacts(self):
        sid = self.session('pending')
        self.assertEqual(self.request(self.mentor,f'/api/sessions/{sid}/decline').status_code,200)
        self.assertEqual(self.request(self.mentor,'/api/app',method='GET').json['contacts'],[])
        self.session('accepted')
        self.sql('UPDATE users SET is_active=0 WHERE id=%s',(self.learner,))
        self.assertEqual(self.request(self.mentor,'/api/app',method='GET').json['contacts'],[])

    def test_missed_sessions_deduct_once_and_preserve_attendance(self):
        sid=self.session(when=backend.app_now()-timedelta(hours=2))
        attended=self.session(joined=True,when=backend.app_now()-timedelta(hours=2))
        def job(_):
            with backend.app.app_context(): backend.mark_missed_sessions(self.learner)
        with ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(job,range(4)))
        self.assertEqual(self.sql('SELECT status FROM sessions WHERE id=%s',(sid,))[0]['status'],'missed')
        self.assertEqual(self.sql('SELECT status FROM sessions WHERE id=%s',(attended,))[0]['status'],'scheduled')
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s',(self.learner,))[0]['balance'],9)
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s',(self.mentor,))[0]['balance'],10)

    def test_unfinished_joined_session_expires_at_24_hours_after_its_end(self):
        start = backend.app_now().replace(microsecond=0) - timedelta(days=2)
        self.sql('UPDATE user_skills SET duration=90 WHERE user_id=%s', (self.mentor,))
        sid = self.session(joined=True, when=start)
        cutoff = start + timedelta(minutes=90, hours=24)
        with patch.object(backend, 'app_now', return_value=cutoff-timedelta(seconds=1)), backend.app.app_context():
            backend.mark_missed_sessions(self.learner)
        self.assertEqual(self.sql('SELECT status FROM sessions WHERE id=%s', (sid,))[0]['status'], 'scheduled')
        with patch.object(backend, 'app_now', return_value=cutoff), backend.app.app_context():
            backend.mark_missed_sessions(self.learner)
            backend.mark_missed_sessions(self.mentor)
        self.assertEqual(self.sql('SELECT status FROM sessions WHERE id=%s', (sid,))[0]['status'], 'missed')
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM transactions WHERE related_session_id=%s', (sid,))[0]['n'], 0)
        self.assertEqual([r['balance'] for r in self.sql('SELECT balance FROM credits WHERE user_id IN (%s,%s)', (self.learner,self.mentor))], [10,10])

    def test_late_mark_done_is_rejected_without_opening_app_first(self):
        sid = self.session('accepted', True, backend.app_now()-timedelta(hours=26))
        self.assertEqual(self.request(self.mentor, f'/api/sessions/{sid}/complete').status_code, 409)
        self.assertEqual(self.sql('SELECT status FROM sessions WHERE id=%s', (sid,))[0]['status'], 'missed')
        self.assertEqual(self.request(self.learner, f'/api/sessions/{sid}/confirm').status_code, 403)
        self.assertEqual(self.request(self.mentor, f'/api/sessions/{sid}/join').status_code, 400)
        self.assertEqual(self.request(self.mentor, f'/api/sessions/{sid}/schedule', {'scheduledFor': self.time.isoformat()}).status_code, 400)
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM transactions WHERE related_session_id=%s', (sid,))[0]['n'], 0)

    def test_mark_done_before_cutoff_can_be_confirmed_after_cutoff(self):
        now = backend.app_now().replace(microsecond=0)
        sid = self.session(joined=True, when=now-timedelta(hours=24))
        with patch.object(backend, 'app_now', return_value=now):
            self.assertEqual(self.request(self.mentor, f'/api/sessions/{sid}/complete').status_code, 200)
        with patch.object(backend, 'app_now', return_value=now+timedelta(days=2)):
            self.assertEqual(self.request(self.learner, f'/api/sessions/{sid}/confirm').status_code, 200)
        self.assertEqual(self.sql('SELECT status FROM sessions WHERE id=%s', (sid,))[0]['status'], 'completed')
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s', (self.mentor,))[0]['balance'], 11)

    def test_expiry_preserves_other_states_and_unrelated_accounts(self):
        old = backend.app_now()-timedelta(days=3)
        preserved = [(self.session(status, True, old), status) for status in
                     ['pending', 'awaiting_confirmation', 'completed', 'cancelled']]
        sid = self.session('accepted', True, old)
        other = self.user()
        with backend.app.app_context():
            backend.mark_missed_sessions(other)
        self.assertEqual(self.sql('SELECT status FROM sessions WHERE id=%s', (sid,))[0]['status'], 'accepted')
        payload = self.request(self.mentor, '/api/app', method='GET')
        self.assertEqual(payload.status_code, 200)
        self.assertEqual(next(s['status'] for s in payload.json['sessions'] if s['id']==sid), 'missed')
        for session_id, status in preserved:
            self.assertEqual(self.sql('SELECT status FROM sessions WHERE id=%s', (session_id,))[0]['status'], status)

    def test_expiry_uses_default_duration_when_saved_skill_was_removed(self):
        now = backend.app_now().replace(microsecond=0)
        sid = self.session(joined=True, when=now-timedelta(hours=25))
        self.sql('DELETE FROM user_skills WHERE user_id=%s', (self.mentor,))
        with patch.object(backend, 'app_now', return_value=now):
            self.assertEqual(self.request(self.mentor, f'/api/sessions/{sid}/complete').status_code, 409)
        self.assertEqual(self.sql('SELECT status FROM sessions WHERE id=%s', (sid,))[0]['status'], 'missed')

    def test_parallel_late_completions_never_award_credit(self):
        sid = self.session(joined=True, when=backend.app_now()-timedelta(days=2))
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.request(self.mentor, f'/api/sessions/{sid}/complete').status_code, range(8)))
        self.assertEqual(results, [409]*8)
        self.assertEqual(self.sql('SELECT status FROM sessions WHERE id=%s', (sid,))[0]['status'], 'missed')
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM transactions WHERE related_session_id=%s', (sid,))[0]['n'], 0)

    def test_quiz_lifecycle_and_duplicate_submit(self):
        sid=self.session(joined=True)
        route=f'/api/sessions/{sid}/quiz'
        questions=[{'question':f'Question {i}','options':['Yes','No','Maybe','None'],'correct':'A'} for i in range(5)]
        self.assertEqual(self.request(self.learner,route,{'questions':questions}).status_code,403)
        self.assertEqual(self.request(self.mentor,route,{'questions':questions}).status_code,200)
        quiz=self.request(self.learner,route,method='GET').json
        self.assertFalse(quiz['canSubmit'])
        self.assertNotIn('correct',quiz['questions'][0])
        self.assertEqual(self.request(self.learner,route+'/submit',{'answers':{}}).status_code,400)
        self.assertEqual(self.request(self.mentor,f'/api/sessions/{sid}/complete').status_code,200)
        self.assertTrue(self.request(self.learner,route,method='GET').json['canSubmit'])
        self.assertEqual(self.request(self.mentor,route,{'questions':questions}).status_code,400)
        answers={str(q['id']):'A' for q in quiz['questions']}
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _: self.request(self.learner,route+'/submit',{'answers':answers}).status_code,range(4)))
        self.assertEqual(results.count(200),1,results)
        self.assertEqual(self.request(self.learner,route,method='GET').json['attempt']['score'],5)

    def test_login_rejects_wrong_password_and_unverified_google(self):
        email=self.sql('SELECT email FROM users WHERE id=%s',(self.learner,))[0]['email']
        with backend.app.test_client() as client:
            self.assertEqual(client.post('/api/auth/login',json={'email':email,'password':'wrong-pass'}).status_code,401)
            self.assertEqual(client.post('/api/auth/login',json={'email':email,'password':'correct-pass'}).status_code,200)
            self.assertEqual(client.post('/api/auth/google-profile',json={'profile':{'email':email}}).status_code,410)

    def test_seen_message_cannot_be_edited(self):
        sent=self.request(self.learner,f'/api/messages/{self.mentor}',{'content':'hello','encryptedContent':{'test':True}})
        self.assertEqual(sent.status_code,201)
        thread=self.request(self.mentor,f'/api/messages/{self.learner}',method='GET').json
        mid=thread['messages'][0]['id']
        self.assertEqual(self.request(self.learner,f'/api/messages/item/{mid}',{'content':'edit','encryptedContent':{}},method='PATCH').status_code,409)
        self.assertEqual(self.request(self.mentor,f'/api/messages/item/{mid}',{'scope':'everyone'},method='DELETE').status_code,403)
        self.assertEqual(self.request(self.learner,f'/api/messages/item/{mid}',{'scope':'everyone'},method='DELETE').status_code,200)

    def recovery_fixture(self):
        learner_key = {'kty': 'EC', 'crv': 'P-256', 'x': 'a'*43, 'y': 'b'*43}
        mentor_key = {'kty': 'EC', 'crv': 'P-256', 'x': 'c'*43, 'y': 'd'*43}
        for uid, key in ((self.learner, learner_key), (self.mentor, mentor_key)):
            self.sql('UPDATE users SET public_key=%s WHERE id=%s', (json.dumps(key), uid))
        original = json.dumps({'v': 1, 'copies': {str(self.learner): {'data': 'original ciphertext'}}})
        mid = self.sql("INSERT INTO messages(sender_id,receiver_id,content,encrypted_content,is_read) VALUES (%s,%s,'[encrypted message]',%s,1)", (self.learner, self.mentor, original))
        recovery = {'recipientId': self.mentor, 'senderPublicKey': learner_key, 'receiverPublicKey': mentor_key,
                    'iv': base64.b64encode(bytes(12)).decode(), 'data': base64.b64encode(bytes(32)).decode()}
        return mid, original, {'recoveryCopy': recovery, 'expectedEncryptedContent': original}

    def test_message_recovery_preserves_original_and_read_status(self):
        mid, original, body = self.recovery_fixture()
        route = f'/api/messages/item/{mid}/recover'
        self.assertEqual(self.request(self.user(), route, body).status_code, 404)
        self.assertEqual(self.request(self.learner, route, body).status_code, 200)
        row = self.sql('SELECT * FROM messages WHERE id=%s', (mid,))[0]
        result = json.loads(row['encrypted_content'])
        self.assertEqual(result['copies'], json.loads(original)['copies'])
        self.assertEqual(len(result['recoveryCopies']), 1)
        self.assertEqual(row['content'], '[encrypted message]')
        self.assertEqual(row['is_read'], 1)
        self.assertIsNone(row['edited_at'])
        self.assertEqual(self.request(self.learner, route, body).status_code, 409)
        body['expectedEncryptedContent'] = row['encrypted_content']
        self.assertEqual(self.request(self.learner, route, body).status_code, 200)
        # The old migration endpoint cannot overwrite encrypted history anymore.
        self.assertEqual(self.request(self.learner, f'/api/messages/item/{mid}/encrypt', {'encryptedContent': {}}).status_code, 409)
        self.assertEqual(self.sql('SELECT encrypted_content FROM messages WHERE id=%s', (mid,))[0]['encrypted_content'], row['encrypted_content'])

    def test_message_recovery_rejects_invalid_keys_deletions_and_stale_content(self):
        mid, original, body = self.recovery_fixture()
        route = f'/api/messages/item/{mid}/recover'
        wrong_peer = json.loads(json.dumps(body))
        wrong_peer['recoveryCopy']['recipientId'] = self.learner
        self.assertEqual(self.request(self.learner, route, wrong_peer).status_code, 403)
        wrong_key = json.loads(json.dumps(body))
        wrong_key['recoveryCopy']['receiverPublicKey']['x'] = 'z'*43
        self.assertEqual(self.request(self.learner, route, wrong_key).status_code, 409)
        private = json.loads(json.dumps(body))
        private['recoveryCopy']['senderPublicKey']['d'] = 'private key'
        self.assertEqual(self.request(self.learner, route, private).status_code, 400)
        self.sql('UPDATE messages SET encrypted_content=%s WHERE id=%s', (json.dumps({'copies': {}, 'edited': True}), mid))
        self.assertEqual(self.request(self.learner, route, body).status_code, 409)
        self.sql('UPDATE messages SET encrypted_content=%s, deleted_for_sender=1 WHERE id=%s', (original, mid))
        self.assertEqual(self.request(self.learner, route, body).status_code, 404)
        self.sql('UPDATE messages SET deleted_for_sender=0, deleted_for_everyone=1 WHERE id=%s', (mid,))
        self.assertEqual(self.request(self.learner, route, body).status_code, 404)

    def test_dashboard_and_ownership(self):
        sid=self.session()
        self.assertEqual(self.request(self.user(),f'/api/sessions/{sid}/join').status_code,404)
        r=self.request(self.learner,'/api/app',method='GET')
        self.assertEqual(r.status_code,200,r.json)
        self.assertTrue(any(s['id']==sid for s in r.json['sessions']))
        self.assertEqual(self.request(self.learner,'/api/admin',method='GET').status_code,403)

    def test_underage_members_can_save_and_edit_skills_but_are_not_listed(self):
        minor = self.user(age=17)
        self.sql("UPDATE users SET role='mentor',age=99,username='AAA Junior' WHERE id=%s", (minor,))
        body = {'name': 'Junior drawing', 'category': 'Other', 'description': 'My saved skill',
                'duration': 60, 'availability': 'online', 'preferred_time': '17:00-21:00'}
        saved = self.request(minor, '/api/skills', body)
        self.assertEqual(saved.status_code, 201, saved.json)
        self.assertIn('turn 18', saved.json['message'])
        edited = self.request(minor, '/api/skills', {**body, 'id': saved.json['skill']['id'], 'description': 'Updated skill'})
        self.assertEqual(edited.status_code, 200)
        own = self.request(minor, '/api/app', method='GET').json
        self.assertFalse(own['user']['can_mentor'])
        self.assertEqual(len(own['mySkills']), 1)
        self.assertEqual(own['mySkills'][0]['description'], 'Updated skill')
        others = self.request(self.learner, '/api/app', method='GET').json
        self.assertNotIn(minor, [person['id'] for person in others['mentors']])

    def test_mentor_listing_unlocks_on_eighteenth_birthday_without_skill_changes(self):
        self.sql("UPDATE users SET birthdate='2008-10-31',age=99,username='AAA Birthday mentor' WHERE id=%s", (self.mentor,))
        for now, eligible in [(datetime(2026, 10, 30, 23, 59), False), (datetime(2026, 10, 31), True)]:
            with patch.object(backend, 'app_now', return_value=now):
                own = self.request(self.mentor, '/api/auth/me', method='GET').json
                directory = self.request(self.learner, '/api/app', method='GET').json
                self.assertEqual(own['user']['can_mentor'], eligible)
                self.assertEqual(self.mentor in [person['id'] for person in directory['mentors']], eligible)
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM user_skills WHERE user_id=%s', (self.mentor,))[0]['n'], 1)

    def test_ineligible_mentor_cannot_be_booked_even_with_a_stale_card(self):
        today = backend.app_now().date()
        dates = [today.replace(year=today.year-17), None, today + timedelta(days=1)]
        for born in dates:
            self.sql('UPDATE users SET birthdate=%s,age=99 WHERE id=%s', (born, self.mentor))
            availability = self.availability()
            self.assertEqual(availability.status_code, 403, availability.json)
            result = self.booking()
            self.assertEqual(result.status_code, 409, result.json)
            self.assertIn('18 or older', result.json['error'])
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM sessions WHERE mentor_id=%s', (self.mentor,))[0]['n'], 0)
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s', (self.learner,))[0]['balance'], 10)

    def test_underage_mentor_cannot_bypass_rule_through_chat_or_session_actions(self):
        born = backend.app_now().date().replace(year=backend.app_now().year-17)
        self.sql('UPDATE users SET birthdate=%s WHERE id=%s', (born, self.mentor))
        body = {'scheduledFor': self.time.isoformat()}
        chat = self.request(self.mentor, f'/api/messages/{self.learner}/schedule', body)
        self.assertEqual(chat.status_code, 409, chat.json)
        sid = self.session('pending')
        self.assertEqual(self.request(self.mentor, f'/api/sessions/{sid}/accept').status_code, 403)
        self.assertEqual(self.request(self.mentor, f'/api/sessions/{sid}/schedule', body).status_code, 409)
        self.sql("UPDATE sessions SET status='accepted' WHERE id=%s", (sid,))
        self.assertEqual(self.request(self.mentor, f'/api/sessions/{sid}/join', {'guardianConfirmed': True}).status_code, 403)
        self.assertEqual(self.request(self.learner, f'/api/sessions/{sid}/join').status_code, 403)
        self.assertEqual(self.sql('SELECT status FROM sessions WHERE id=%s', (sid,))[0]['status'], 'accepted')

    def test_underage_learner_can_still_book_an_adult_mentor(self):
        learner = self.user(age=17)
        result = self.booking(learner=learner)
        self.assertEqual(result.status_code, 201, result.json)
        self.assertTrue(self.request(self.mentor, '/api/auth/me', method='GET').json['user']['can_mentor'])

    def test_mentor_age_uses_full_birthdate_and_handles_leap_day(self):
        with patch.object(backend, 'app_now', return_value=datetime(2026, 2, 28)):
            self.assertFalse(backend.can_be_mentor({'birthdate': '2008-02-29', 'age': 99}))
            self.assertTrue(backend.can_be_mentor({'birthdate': '2008-02-28'}))
            self.assertFalse(backend.can_be_mentor({'birthdate': 'bad'}))
            self.assertFalse(backend.can_be_mentor({'age': 25}))
        with patch.object(backend, 'app_now', return_value=datetime(2026, 3, 1)):
            self.assertTrue(backend.can_be_mentor({'birthdate': '2008-02-29'}))

    def test_registration_birthdate_profile_skill_and_notifications(self):
        with backend.app.test_client() as client:
            r=client.post('/api/auth/register',json={'username':'Audit member','email':uuid.uuid4().hex+'@example.test','password':'test-password'})
        self.assertEqual(r.status_code,201,r.json)
        uid=r.json['user']['id']
        self.assertEqual(self.request(uid,'/api/app',method='GET').status_code,428)
        self.assertEqual(self.request(uid,'/api/auth/birthdate',{'birthdate':'2000-01-02'}).status_code,200)
        self.assertEqual(self.request(uid,'/api/profile',{'bio':'Testing skills','location':'Chennai'},method='PATCH').status_code,200)
        self.assertEqual(self.request(uid,'/api/skills',{'name':'Audit skill','category':'Other','description':'Test','duration':60,'availability':'online','preferred_time':'17:00-21:00'}).status_code,201)
        data=self.request(uid,'/api/app',method='GET').json
        self.assertEqual(len(data['mySkills']),1)
        self.assertEqual(data['user']['credits'],10)
        self.assertEqual(self.request(uid,'/api/notifications/read').status_code,200)
        sid=data['mySkills'][0]['id']
        self.assertEqual(self.request(uid,f'/api/skills/{sid}',method='DELETE').status_code,200)

    def test_rating_after_completion(self):
        sid=self.session('awaiting_confirmation',True)
        self.assertEqual(self.request(self.learner,f'/api/sessions/{sid}/rating',{'score':5}).status_code,403)
        self.assertEqual(self.request(self.learner,f'/api/sessions/{sid}/confirm').status_code,200)
        self.assertEqual(self.request(self.learner,f'/api/sessions/{sid}/rating',{'score':5,'feedback':'Helpful'}).status_code,200)

    def test_saved_rating_reaches_mentor_profile_and_updates_average(self):
        # Keep this mentor visible in the limited leaderboard fixture.
        self.sql('UPDATE credits SET balance=100000 WHERE user_id=%s', (self.mentor,))
        sid = self.session('completed', True)
        before = self.request(self.mentor, '/api/auth/me', method='GET').json['user']
        self.assertEqual(float(before['rating']), 0)
        self.assertEqual(before['rating_count'], 0)

        def check_average(expected, count):
            own = self.request(self.mentor, '/api/app', method='GET')
            self.assertEqual(own.status_code, 200, own.json)
            self.assertEqual(float(own.json['user']['rating']), expected)
            self.assertEqual(own.json['user']['rating_count'], count)
            auth = self.request(self.mentor, '/api/auth/me', method='GET').json['user']
            self.assertEqual(float(auth['rating']), expected)
            viewer = self.request(self.learner, '/api/app', method='GET').json
            mentor = next(row for row in viewer['mentors'] if row['id'] == self.mentor)
            leader = next(row for row in viewer['leaderboard'] if row['id'] == self.mentor)
            self.assertEqual(float(mentor['rating']), expected)
            self.assertEqual(float(leader['rating']), expected)
            self.assertEqual(leader['rating_count'], count)
            self.assertEqual(float(viewer['user']['rating']), 0)

        self.assertEqual(self.request(self.learner, f'/api/sessions/{sid}/rating', {'score': 5}).status_code, 200)
        check_average(5, 1)
        second = self.session('completed', True)
        self.assertEqual(self.request(self.learner, f'/api/sessions/{second}/rating', {'score': 3}).status_code, 200)
        check_average(4, 2)
        # Editing a previous rating changes the average, not the rating count.
        self.assertEqual(self.request(self.learner, f'/api/sessions/{sid}/rating', {'score': 1}).status_code, 200)
        check_average(2, 2)

    def test_profile_counts_real_completions_without_rewriting_wallet_or_history(self):
        self.sql('UPDATE credits SET balance=100000 WHERE user_id=%s', (self.mentor,))
        self.sql('UPDATE users SET sessions_completed=0, streak=3, last_visit_on=%s WHERE id=%s',
                 (backend.today_ist(), self.mentor))
        self.session('completed', True)
        self.session('completed', True)
        for status in ['pending', 'accepted', 'scheduled', 'awaiting_confirmation', 'cancelled', 'missed']:
            self.session(status)
        # Include a completed learning session on this mentor's personal profile.
        self.sql('INSERT INTO sessions(learner_id,mentor_id,skill_id,status,scheduled_for) VALUES (%s,%s,%s,%s,%s)',
                 (self.mentor, self.learner, self.skill, 'completed', self.time))
        own = self.request(self.mentor, '/api/app', method='GET')
        self.assertEqual(own.status_code, 200, own.json)
        self.assertEqual(own.json['user']['sessions_completed'], 3)
        self.assertEqual(own.json['user']['credits'], 100000)
        self.assertEqual(own.json['user']['streak'], 3)
        # A rating ensures this fixture is in the limited mentor directory.
        sid = self.session('completed', True)
        self.assertEqual(self.request(self.learner, f'/api/sessions/{sid}/rating', {'score': 5}).status_code, 200)
        viewer = self.request(self.learner, '/api/app', method='GET').json
        for collection in ['mentors', 'leaderboard']:
            row = next(row for row in viewer[collection] if row['id'] == self.mentor)
            self.assertEqual(row['sessions_completed'], 4)
        # Reading profiles does not repair by making financial or history mutations.
        self.assertEqual(self.sql('SELECT sessions_completed FROM users WHERE id=%s', (self.mentor,))[0]['sessions_completed'], 0)
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s', (self.mentor,))[0]['balance'], 100000)

    def test_payment_credit_retry_is_idempotent(self):
        payment, body = self.payment_fixture()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda n: (self.request(self.learner,'/api/credits/verify',body)
                if n % 2 else self.payment_webhook(payment, 'order.paid' if n % 4 else 'payment.captured')).status_code,range(12)))
        self.assertEqual(results,[200]*12)
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s',(self.learner,))[0]['balance'],15)
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM transactions WHERE user_id=%s',(self.learner,))[0]['n'],1)
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM notifications WHERE user_id=%s AND type='credit'",(self.learner,))[0]['n'],1)

    def test_upi_config_is_trimmed_and_exposes_only_public_payment_details(self):
        with patch.dict(backend.app.config, {'DONATION_UPI_ID': ' demo@bank ', 'DONATION_UPI_NAME': ' GotTime '}):
            result = self.request(self.learner, '/api/donations/upi', method='GET')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json, {'upiId': 'demo@bank', 'payeeName': 'GotTime'})
            self.assertEqual(result.headers['Cache-Control'], 'no-store')
        with patch.dict(backend.app.config, {'DONATION_UPI_ID': ' ', 'DONATION_UPI_NAME': ' '}):
            result = self.request(self.learner, '/api/donations/upi', method='GET')
            self.assertEqual(result.json, {'upiId': '', 'payeeName': 'GotTime'})
        with backend.app.test_client() as client:
            self.assertEqual(client.get('/api/donations/upi').status_code, 401)

    def payment_fixture(self, donation=False):
        config = patch.dict(backend.app.config, RAZORPAY_KEY_ID='rzp_test_audit',
                            RAZORPAY_KEY_SECRET='audit-only-secret', RAZORPAY_WEBHOOK_SECRET='audit-webhook-secret')
        config.start()
        self.addCleanup(config.stop)
        order, pid = 'order_'+uuid.uuid4().hex, 'pay_'+uuid.uuid4().hex
        if donation:
            self.sql("INSERT INTO donation_payments(user_id,amount_paise,provider_order_id,status) VALUES (%s,9900,%s,'created')",(self.learner,order))
        else:
            self.sql("INSERT INTO credit_payments(user_id,package_id,credits,amount_paise,provider_order_id,status) VALUES (%s,'starter',5,9900,%s,'created')",(self.learner,order))
        payment = dict(id=pid, order_id=order, amount=9900, currency='INR', status='captured', captured=True, amount_refunded=0)
        response = Mock()
        response.json.return_value = payment
        provider = patch.object(backend.requests, 'get', return_value=response)
        self.payment_lookup = provider.start()
        self.addCleanup(provider.stop)
        body = dict(razorpay_order_id=order, razorpay_payment_id=pid,
                    razorpay_signature=hmac.new(b'audit-only-secret',f'{order}|{pid}'.encode(),hashlib.sha256).hexdigest())
        return payment, body

    def payment_webhook(self, payment, event='payment.captured', signature=None):
        raw = json.dumps(dict(event=event, payload=dict(payment=dict(entity=payment)))).encode()
        signed = hmac.new(b'audit-webhook-secret',raw,hashlib.sha256).hexdigest()
        with backend.app.test_client() as client:
            return client.post('/api/payments/razorpay/webhook', data=raw, content_type='application/json',
                               headers={'X-Razorpay-Signature':signature if signature is not None else signed})

    def test_payment_rejects_wrong_signature_owner_amount_currency_order(self):
        payment, body = self.payment_fixture()
        self.assertEqual(self.request(self.mentor,'/api/credits/verify',body).status_code,404)
        self.assertEqual(self.request(self.learner,'/api/credits/verify',{**body,'razorpay_signature':'f'*64}).status_code,400)
        self.assertEqual(self.payment_webhook(payment, signature='f'*64).status_code,400)
        self.payment_lookup.assert_not_called()
        for key, value in [('amount',1),('currency','USD'),('order_id','order_wrong'),('id','pay_wrong')]:
            previous = payment[key]
            payment[key] = value
            r = self.request(self.learner,'/api/credits/verify',body)
            self.assertIn(r.status_code,(409,502),r.json)
            payment[key] = previous
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s',(self.learner,))[0]['balance'],10)

    def test_payment_waits_for_capture_and_webhook_completes_later(self):
        payment, body = self.payment_fixture()
        payment.update(status='authorized',captured=False)
        r = self.request(self.learner,'/api/credits/verify',body)
        self.assertEqual(r.status_code,202,r.json)
        self.assertFalse(r.json['paid'])
        self.assertEqual(self.payment_webhook(payment).status_code,503)
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s',(self.learner,))[0]['balance'],10)
        payment.update(status='captured',captured=True)
        self.assertEqual(self.payment_webhook(payment).status_code,200)
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s',(self.learner,))[0]['balance'],15)

    def test_refunded_or_failed_payment_never_grants_credits(self):
        payment, body = self.payment_fixture()
        for changes in (dict(status='failed',captured=False),dict(status='refunded',captured=True),
                        dict(status='captured',captured=False),dict(status='captured',captured=True,amount_refunded=100)):
            payment.update(changes)
            self.assertEqual(self.request(self.learner,'/api/credits/verify',body).status_code,409)
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s',(self.learner,))[0]['balance'],10)

    def test_payment_provider_failure_retries_and_fulfillment_rolls_back(self):
        payment, body = self.payment_fixture()
        self.payment_lookup.side_effect = backend.requests.Timeout()
        self.assertEqual(self.payment_webhook(payment).status_code,503)
        self.payment_lookup.side_effect = None
        with patch.object(backend,'create_notification',side_effect=RuntimeError('simulated failure')):
            with self.assertRaises(RuntimeError): self.payment_webhook(payment)
        self.assertEqual(self.sql('SELECT status FROM credit_payments WHERE provider_order_id=%s',(payment['order_id'],))[0]['status'],'created')
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s',(self.learner,))[0]['balance'],10)
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM transactions WHERE user_id=%s',(self.learner,))[0]['n'],0)
        self.assertEqual(self.payment_webhook(payment).status_code,200)

    def test_donation_webhook_and_browser_retry_do_not_award_credits(self):
        payment, body = self.payment_fixture(donation=True)
        self.assertEqual(self.payment_webhook(payment).status_code,200)
        self.assertEqual(self.request(self.learner,'/api/donations/verify',body).status_code,200)
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s',(self.learner,))[0]['balance'],10)
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM notifications WHERE user_id=%s AND type='donation'",(self.learner,))[0]['n'],1)

    def test_live_orders_require_webhook_secret_and_unknown_webhooks_are_ignored(self):
        payment, body = self.payment_fixture()
        payment['order_id'] = 'order_unknown'
        self.assertEqual(self.payment_webhook(payment).status_code,200)
        self.payment_lookup.assert_not_called()
        with patch.dict(backend.app.config, RAZORPAY_KEY_ID='rzp_live_audit', RAZORPAY_WEBHOOK_SECRET=''):
            with patch.object(backend.requests,'post') as create:
                self.assertEqual(self.request(self.learner,'/api/donations/create',{'amount':10}).status_code,503)
                self.assertEqual(self.payment_webhook(payment).status_code,503)
                create.assert_not_called()

    def test_order_creation_uses_server_price_and_does_not_grant_credits(self):
        self.payment_fixture()
        package_id, package = next(iter(backend.credit_packages().items()))
        oid = 'order_'+uuid.uuid4().hex
        response = Mock(status_code=200)
        response.json.return_value = dict(id=oid,amount=int(package['price'])*100,currency='INR')
        with patch.object(backend.requests,'post',return_value=response) as create:
            r = self.request(self.learner,'/api/credits/purchase',dict(packageId=package_id,amount=1,credits=999999))
            self.assertEqual(r.status_code,200,r.json)
            self.assertEqual(create.call_args.kwargs['json']['amount'],int(package['price'])*100)
            self.assertEqual(self.sql('SELECT credits FROM credit_payments WHERE provider_order_id=%s',(oid,))[0]['credits'],package['credits'])
            response.json.return_value['currency']='USD'
            self.assertEqual(self.request(self.learner,'/api/credits/purchase',dict(packageId=package_id)).status_code,502)
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s',(self.learner,))[0]['balance'],10)

    def test_existing_live_payment_can_finish_while_webhook_is_being_configured(self):
        payment, body = self.payment_fixture()
        with patch.dict(backend.app.config, RAZORPAY_KEY_ID='rzp_live_audit', RAZORPAY_WEBHOOK_SECRET=''):
            self.assertEqual(self.request(self.learner,'/api/credits/verify',body).status_code,200)

    def test_checkout_order_errors_are_safe_and_missing_signature_is_bad_request(self):
        payment, body = self.payment_fixture()
        for path, payload in (('/api/credits/purchase', {'packageId':'starter'}),
                              ('/api/donations/create', {'amount':10})):
            for upstream, expected in ((401,401),(400,500),(503,500)):
                response = Mock(status_code=upstream)
                response.json.side_effect = ValueError('provider returned non-JSON')
                with patch.object(backend.requests,'post',return_value=response):
                    r = self.request(self.learner,path,payload)
                    self.assertEqual(r.status_code,expected,r.json)
                    response.json.assert_not_called()
            with patch.object(backend.requests,'post',side_effect=backend.requests.Timeout()):
                self.assertEqual(self.request(self.learner,path,payload).status_code,500)
        body.pop('razorpay_signature')
        for path in ('/api/credits/verify','/api/donations/verify'):
            self.assertEqual(self.request(self.learner,path,body).status_code,400)
        self.payment_lookup.assert_not_called()
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s',(self.learner,))[0]['balance'],10)

    def quest(self, key):
        quest = dict(next(q for q in backend.DAILY_QUESTS if q['key'] == key))
        quest.update(date=backend.today_ist().isoformat(), event_key='daily_quest_' + key)
        quest['resets_at'] = f"{backend.today_ist() + timedelta(days=1)}T00:00:00+05:30"
        return quest

    def claim_quest(self, quest, uid=None, **extra):
        return self.request(uid or self.learner, '/api/quests/daily-curiosity',
                            {'quest_key': quest['key'], 'quest_date': quest['date'], **extra})

    def test_daily_quest_weighted_xp_and_first_badge_awarded_once(self):
        quest = self.quest('mentor_spotlight')
        with patch.object(backend, 'current_daily_quest', return_value=quest):
            with ThreadPoolExecutor(max_workers=6) as pool:
                responses = list(pool.map(lambda _: self.claim_quest(quest, mentor_ids=[self.mentor]), range(8)))
            self.assertEqual([r.status_code for r in responses], [200] * 8)
            self.assertEqual(sum(r.json['awarded'] for r in responses), 1)
            data = self.request(self.learner, '/api/app', method='GET').json
            self.assertTrue(data['dailyQuestClaimed'])
            self.assertEqual(data['dailyQuestProgress'], 1)
            self.assertEqual(data['user']['xp'], 10)
            self.assertEqual([b['name'] for b in data['badges']], ['First Daily Quest'])
            self.assertEqual([b for r in responses for b in r.json.get('unlockedBadges', [])], ['First Daily Quest'])
        with backend.app.test_request_context():
            backend.reconcile_progress_badges(self.learner)
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM user_badges WHERE user_id=%s', (self.learner,))[0]['n'], 1)
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s', (self.learner,))[0]['balance'], 10)

    def test_daily_quest_rejects_stale_or_invalid_claims(self):
        quest = self.quest('mentor_explorer')
        with patch.object(backend, 'current_daily_quest', return_value=quest):
            for ids in ([self.mentor, self.mentor], [self.learner, self.mentor], [99999999, self.mentor], ['²', '9' * 5000], 'bad'):
                self.assertEqual(self.claim_quest(quest, mentor_ids=ids, progress=999).status_code, 400)
            self.assertEqual(self.claim_quest(quest, quest_date='2000-01-01').status_code, 409)
            self.assertEqual(self.claim_quest(quest, quest_key='wrong').status_code, 409)
            self.assertEqual(self.request(self.learner, '/api/quests/daily-reset').status_code, 403)

    def test_booking_quest_uses_today_not_client_count_or_session_date(self):
        quest = self.quest('book_two')
        self.session('cancelled')
        old = self.session()
        self.sql('UPDATE sessions SET created_at=%s WHERE id=%s', (backend.app_now()-timedelta(days=2), old))
        with patch.object(backend, 'current_daily_quest', return_value=quest):
            self.assertEqual(self.claim_quest(quest, progress=999).status_code, 400)
            self.session('pending')
            data = self.request(self.learner, '/api/app', method='GET').json
            self.assertEqual(data['dailyQuestProgress'], 1)
            self.assertEqual(self.claim_quest(quest, progress=999).status_code, 400)
            self.session('scheduled')  # Future scheduled date; created today.
            self.assertTrue(self.claim_quest(quest).json['awarded'])
            self.assertEqual(self.claim_quest(quest, uid=self.mentor, progress=999).status_code, 400)

    def test_completion_quest_requires_confirmed_sessions_today_for_either_side(self):
        quest = self.quest('complete_two')
        self.session('scheduled', joined=True)  # Merely opening a meeting is not completion.
        with patch.object(backend, 'current_daily_quest', return_value=quest):
            self.assertEqual(self.claim_quest(quest, progress=999).status_code, 400)
            for index in range(2):
                sid = self.session('awaiting_confirmation', joined=True)
                self.assertEqual(self.request(self.learner, f'/api/sessions/{sid}/confirm').status_code, 200)
                data = self.request(self.learner, '/api/app', method='GET').json
                self.assertEqual(data['dailyQuestProgress'], index + 1)
                if index == 0:
                    self.assertEqual(self.claim_quest(quest).status_code, 400)
            for uid in (self.learner, self.mentor):
                result = self.claim_quest(quest, uid=uid)
                self.assertEqual(result.status_code, 200)
                self.assertTrue(result.json['awarded'])
        with backend.app.app_context():
            self.assertEqual(backend.daily_quest_progress(self.learner, quest), 2)
        self.sql('UPDATE transactions SET created_at=%s WHERE user_id IN (%s,%s)',
                 (backend.app_now()-timedelta(days=1), self.learner, self.mentor))
        with backend.app.app_context():
            self.assertEqual(backend.daily_quest_progress(self.learner, quest), 0)

    def test_quest_next_day_fresh_and_legacy_same_day_claim_respected(self):
        quest = self.quest('mentor_spotlight')
        self.sql("INSERT INTO xp_events(user_id,event_key,amount,event_date) VALUES (%s,'daily_quest_old',50,%s)",
                 (self.learner, backend.today_ist()))
        with patch.object(backend, 'current_daily_quest', return_value=quest):
            self.assertFalse(self.claim_quest(quest, mentor_ids=[self.mentor]).json['awarded'])
            self.sql('UPDATE xp_events SET event_date=%s WHERE user_id=%s',
                     (backend.today_ist()-timedelta(days=1), self.learner))
            self.assertFalse(self.request(self.learner, '/api/app', method='GET').json['dailyQuestClaimed'])
            self.assertTrue(self.claim_quest(quest, mentor_ids=[self.mentor]).json['awarded'])

    def test_quest_notification_failure_rolls_back_xp_and_claim(self):
        quest = self.quest('mentor_spotlight')
        with patch.object(backend, 'current_daily_quest', return_value=quest):
            with patch.object(backend, 'create_notification', side_effect=RuntimeError('test rollback')):
                with self.assertRaises(RuntimeError):
                    self.claim_quest(quest, mentor_ids=[self.mentor])
            self.assertTrue(self.claim_quest(quest, mentor_ids=[self.mentor]).json['awarded'])
        self.assertEqual(self.sql('SELECT xp FROM users WHERE id=%s', (self.learner,))[0]['xp'], 10)

    def test_quest_xp_unlocks_every_reached_milestone(self):
        quest = self.quest('mentor_spotlight')
        for before, expected in ((39, []), (40, ['Curiosity Spark']),
                                 (240, ['Curiosity Spark', 'Knowledge Seeker']),
                                 (490, ['Curiosity Spark', 'Knowledge Seeker', 'Time Legend']),
                                 (495, ['Curiosity Spark', 'Knowledge Seeker', 'Time Legend'])):
            uid = self.user()
            self.sql('UPDATE users SET xp=%s WHERE id=%s', (before, uid))
            with patch.object(backend, 'current_daily_quest', return_value=quest):
                result = self.claim_quest(quest, uid=uid, mentor_ids=[self.mentor])
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json['xp'], before + 10)
                self.assertCountEqual(result.json['unlockedBadges'], expected + ['First Daily Quest'])
                self.assertFalse(self.claim_quest(quest, uid=uid, mentor_ids=[self.mentor]).json['awarded'])

    def test_first_quest_badge_does_not_repeat_on_later_days(self):
        quest = self.quest('mentor_spotlight')
        with patch.object(backend, 'current_daily_quest', return_value=quest):
            self.assertEqual(self.claim_quest(quest, mentor_ids=[self.mentor]).json['unlockedBadges'], ['First Daily Quest'])
            tomorrow = backend.today_ist() + timedelta(days=1)
            quest = {**quest, 'date': tomorrow.isoformat()}
        with patch.object(backend, 'current_daily_quest', return_value=quest):
            second = self.claim_quest(quest, mentor_ids=[self.mentor])
            self.assertEqual(second.json['xp'], 20)
            self.assertEqual(second.json['unlockedBadges'], [])
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM notifications WHERE user_id=%s AND type='badge'", (self.learner,))[0]['n'], 1)

    def test_existing_quest_xp_is_reconciled_without_repaying_or_repeating_badges(self):
        self.sql('UPDATE users SET xp=500 WHERE id=%s', (self.learner,))
        self.sql("INSERT INTO xp_events(user_id,event_key,amount,event_date) VALUES (%s,'daily_quest_old',500,%s)",
                 (self.learner, backend.today_ist()-timedelta(days=1)))
        expected = ['Curiosity Spark', 'Knowledge Seeker', 'Time Legend', 'First Daily Quest']
        for _ in range(2):
            data = self.request(self.learner, '/api/app', method='GET').json
            self.assertEqual(data['user']['xp'], 500)
            self.assertCountEqual([b['name'] for b in data['badges']], expected)
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM notifications WHERE user_id=%s AND type='badge'", (self.learner,))[0]['n'], 4)

    def test_new_badge_catalog_entry_created_once_on_existing_deployment(self):
        # Simulate an existing deployment that does not run init when it starts.
        self.sql("DELETE ub FROM user_badges ub JOIN badges b ON b.id=ub.badge_id WHERE b.name='First Daily Quest'")
        self.sql("DELETE FROM badges WHERE name='First Daily Quest'")
        users = [self.user() for _ in range(4)]
        quest = self.quest('mentor_spotlight')
        with patch.object(backend, 'current_daily_quest', return_value=quest):
            with ThreadPoolExecutor(max_workers=4) as pool:
                responses = list(pool.map(lambda uid: self.claim_quest(quest, uid=uid, mentor_ids=[self.mentor]), users))
        self.assertEqual([r.status_code for r in responses], [200] * 4)
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM badges WHERE name='First Daily Quest'")[0]['n'], 1)
        for r in responses:
            self.assertEqual(r.json['unlockedBadges'], ['First Daily Quest'])

    def test_non_quest_xp_still_unlocks_normal_badges(self):
        with backend.app.test_request_context():
            backend.award_xp(self.learner, 50, 'test_regular_xp', 'Regular XP')
        badges = self.sql('SELECT b.name FROM user_badges ub JOIN badges b ON b.id=ub.badge_id WHERE ub.user_id=%s', (self.learner,))
        self.assertIn('Curiosity Spark', [b['name'] for b in badges])

    def test_due_reminder_delivered_once(self):
        sid=self.session()
        self.sql("INSERT INTO session_reminders(session_id,user_id,reminder_key,scheduled_at,message) VALUES (%s,%s,'test',%s,'Audit reminder')",(sid,self.learner,backend.app_now()-timedelta(minutes=1)))
        def job(_):
            with backend.app.app_context(): backend.dispatch_due_session_reminders(self.learner)
        with ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(job,range(4)))
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM notifications WHERE user_id=%s AND message='Audit reminder'",(self.learner,))[0]['n'],1)

if __name__ == '__main__':
    unittest.main(verbosity=2)
