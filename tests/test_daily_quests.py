"""Daily rotation tests; importing the app never starts migrations here."""
import os
os.environ['GOTTIME_AUTO_MIGRATE'] = '0'

import unittest
from datetime import date, timedelta
from unittest.mock import patch
import app as backend


class DailyQuestRotation(unittest.TestCase):
    def test_changes_every_day_including_year_and_cycle_boundaries(self):
        previous = None
        for offset in range(370):
            day = date(2026, 12, 28) + timedelta(days=offset)
            with patch.object(backend, 'today_ist', return_value=day):
                quest = backend.current_daily_quest()
                self.assertEqual(quest, backend.current_daily_quest())
            self.assertNotEqual(previous, quest['key'])
            self.assertEqual(quest['date'], day.isoformat())
            self.assertEqual(quest['resets_at'], f'{day + timedelta(days=1)}T00:00:00+05:30')
            previous = quest['key']

    def test_rotation_has_real_actions_and_one_two_session_targets(self):
        self.assertEqual({q['kind'] for q in backend.DAILY_QUESTS},
                         {'mentor_views', 'book_sessions', 'complete_sessions'})
        for kind in ('book_sessions', 'complete_sessions'):
            self.assertEqual({q['target'] for q in backend.DAILY_QUESTS if q['kind'] == kind}, {1, 2})
        self.assertEqual({q['key']: q['xp'] for q in backend.DAILY_QUESTS}, {
            'mentor_spotlight': 10, 'mentor_explorer': 20, 'book_one': 25,
            'book_two': 40, 'complete_one': 50, 'complete_two': 100,
        })
        self.assertTrue(all('date' not in q for q in backend.DAILY_QUESTS))


if __name__ == '__main__':
    unittest.main()
