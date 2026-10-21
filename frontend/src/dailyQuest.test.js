import test from 'node:test';
import assert from 'node:assert/strict';
import { questDay, questIsCurrent, questCount, questResetDelay } from './dailyQuest.js';

test('IST midnight changes the day, even across the year boundary', () => {
  const before = new Date('2026-12-31T18:29:59Z');
  const after = new Date('2026-12-31T18:30:00Z');
  assert.equal(questDay(before), '2026-12-31');
  assert.equal(questDay(after), '2027-01-01');
  assert.equal(questIsCurrent({ date: '2026-12-31' }, after), false);
  assert.equal(questIsCurrent({ date: '2027-01-01' }, after), true);
});

test('reset timer targets midnight IST and handles an expired quest', () => {
  const quest = { resets_at: '2027-01-01T00:00:00+05:30' };
  assert.equal(questResetDelay(quest, new Date('2026-12-31T18:29:59Z')), 1100);
  assert.equal(questResetDelay(quest, new Date('2027-01-01T00:00:00Z')), 100);
  assert.equal(questResetDelay({}), null);
});

test('mentor progress counts distinct profiles and is capped at the target', () => {
  const data = { dailyQuest: { kind: 'mentor_views', target: 2 } };
  assert.equal(questCount(data, [1, 1]), 1);
  assert.equal(questCount(data, [1, 2, 3]), 2);
});

test('session quests use server progress, not profile views', () => {
  for (const kind of ['book_sessions', 'complete_sessions']) {
    const data = { dailyQuest: { kind, target: 2 }, dailyQuestProgress: 1 };
    assert.equal(questCount(data, [1, 2, 3]), 1);
    assert.equal(questCount({ ...data, dailyQuestClaimed: true }), 2);
    assert.equal(questCount({ ...data, dailyQuestProgress: 0 }), 0);
  }
});
