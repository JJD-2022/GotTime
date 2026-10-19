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
from datetime import timedelta
from unittest.mock import patch
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

    def test_payment_credit_retry_is_idempotent(self):
        backend.app.config['RAZORPAY_KEY_SECRET']='audit-only-secret'
        order='audit_'+uuid.uuid4().hex
        self.sql("INSERT INTO credit_payments(user_id,package_id,credits,amount_paise,provider_order_id,status) VALUES (%s,'starter',5,9900,%s,'created')",(self.learner,order))
        signature=hmac.new(b'audit-only-secret',f'{order}|test_payment'.encode(),hashlib.sha256).hexdigest()
        body={'razorpay_order_id':order,'razorpay_payment_id':'test_payment','razorpay_signature':signature}
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _: self.request(self.learner,'/api/credits/verify',body).status_code,range(8)))
        self.assertEqual(results,[200]*8)
        self.assertEqual(self.sql('SELECT balance FROM credits WHERE user_id=%s',(self.learner,))[0]['balance'],15)

    def test_due_reminder_delivered_once(self):
        sid=self.session()
        self.sql("INSERT INTO session_reminders(session_id,user_id,reminder_key,scheduled_at,message) VALUES (%s,%s,'test',%s,'Audit reminder')",(sid,self.learner,backend.app_now()-timedelta(minutes=1)))
        def job(_):
            with backend.app.app_context(): backend.dispatch_due_session_reminders(self.learner)
        with ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(job,range(4)))
        self.assertEqual(self.sql("SELECT COUNT(*) n FROM notifications WHERE user_id=%s AND message='Audit reminder'",(self.learner,))[0]['n'],1)

if __name__ == '__main__':
    unittest.main(verbosity=2)
