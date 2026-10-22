import { test } from 'node:test';
import assert from 'node:assert/strict';
import { guardianNotice, meetingUrl, nextLearnerQuiz, sessionChatContact } from './sessionRules.js';

test('accepted session chat opens the other participant for either role', () => {
  const item = { learner_id: 2, mentor_id: 3, status: 'accepted' };
  for (const status of ['accepted', 'scheduled', 'awaiting_confirmation', 'completed', 'missed']) {
    assert.equal(sessionChatContact({ ...item, status }, 3), 2);
    assert.equal(sessionChatContact({ ...item, status }, 2), 3);
    assert.equal(sessionChatContact({ ...item, status }, 99), null);
  }
  for (const status of ['pending', 'cancelled']) {
    assert.equal(sessionChatContact({ ...item, status }, 3), null);
  }
});

test('guardian thresholds include online under 13 and offline under 16', () => {
  for (const [age, mode, required] of [[12,'online',true],[13,'online',false],[15,'offline',true],[16,'offline',false]]) {
    assert.equal(Boolean(guardianNotice({ age }, mode)), required);
  }
  assert.ok(guardianNotice({ age: null }, 'online'));
});
test('meeting links reject misleading and unsafe URLs', () => {
  for (const url of ['javascript:alert(1)', 'http://meet.google.com/abc', 'https://zoom.us.evil.test/x', 'https://user:pass@zoom.us/x']) assert.equal(meetingUrl(url), '');
  assert.equal(meetingUrl('meet.google.com/abc'), 'https://meet.google.com/abc');
  assert.ok(meetingUrl('https://us02web.zoom.us/j/123'));
});
test('only the learner gets an unsubmitted end-of-session quiz', () => {
  const item = { id: 1, learner_id: 2, mentor_id: 3, has_quiz: true, status: 'scheduled' };
  const data = { user: { id: 2 }, sessions: [item] };
  assert.equal(nextLearnerQuiz(data, new Set()), undefined);
  item.status = 'awaiting_confirmation';
  assert.equal(nextLearnerQuiz(data, new Set()), item);
  assert.equal(nextLearnerQuiz(data, new Set(['2:1'])), undefined);
  assert.equal(nextLearnerQuiz({ ...data, user: { id: 3 } }, new Set()), undefined);
  item.quiz_submitted = true;
  assert.equal(nextLearnerQuiz(data, new Set()), undefined);
});
