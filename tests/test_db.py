import os
import tempfile
import unittest

from db import DataBase


class DataBaseTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        self.db = DataBase(self.db_path)

    def tearDown(self):
        self.db.connection.close()
        self.tmpdir.cleanup()

    def test_connection_uses_wal(self):
        journal_mode = self.db.connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(journal_mode.lower(), "wal")

    def test_tokens_do_not_go_negative(self):
        self.db.set_tokens(1, 100)
        self.db.deduct_tokens(1, 250)
        self.assertEqual(self.db.get_tokens(1), 0)

    def test_vip_extends_existing_expiry(self):
        self.db.set_vip(1, days=3)
        _, first_expires = self.db.get_vip_status(1)
        self.db.set_vip(1, days=2)
        _, second_expires = self.db.get_vip_status(1)
        self.assertGreaterEqual(second_expires - first_expires, 2 * 24 * 60 * 60 - 2)

    def test_forever_vip_stays_forever_when_days_added(self):
        self.db.set_vip(1, days=0)
        self.db.set_vip(1, days=30)
        is_vip, expires = self.db.get_vip_status(1)
        self.assertTrue(is_vip)
        self.assertEqual(expires, 0)

    def test_referral_reward_marked_once(self):
        self.db.set_referrer(2, 1)
        self.assertTrue(self.db.try_mark_ref_rewarded(2))
        self.assertFalse(self.db.try_mark_ref_rewarded(2))
        self.assertEqual(self.db.get_referrer_for_reward(2), 1)

    def test_grade_hashes_keep_latest_three(self):
        self.db.set_last_grade_hashes(1, ["a", "b", "c", "d"])
        self.assertEqual(self.db.get_last_grade_hashes(1), ["a", "b", "c"])

    def test_session_cookies_roundtrip_and_delete(self):
        self.db.set_session_cookies(1, "nz", "encrypted-cookies")
        self.assertEqual(self.db.get_session_cookies(1, "nz"), "encrypted-cookies")

        self.db.delete_session_cookies(1, "nz")
        self.assertIsNone(self.db.get_session_cookies(1, "nz"))

    def test_delete_user_clears_sessions_but_keeps_vip(self):
        self.db.add_user(1, "login", "password", "nz")
        self.db.set_vip(1, 30)
        self.db.set_session_cookies(1, "nz", "encrypted-cookies")

        self.db.delete_user(1)

        self.assertFalse(self.db.has_credentials(1))
        self.assertIsNone(self.db.get_session_cookies(1, "nz"))
        self.assertTrue(self.db.get_vip_status(1)[0])

    def test_command_metrics_aggregate_latency_and_errors(self):
        self.db.record_command_metric("/diary", 100, ok=True)
        self.db.record_command_metric("/diary", 300, ok=False)

        metrics = self.db.get_command_metrics(days=1)
        diary = next(item for item in metrics if item["command"] == "/diary")
        self.assertEqual(diary["calls"], 2)
        self.assertEqual(diary["errors"], 1)
        self.assertEqual(diary["avg_ms"], 200)
        self.assertEqual(diary["max_ms"], 300)

    def test_nz_session_metrics_count_events(self):
        self.db.record_nz_session_event("cookie_reuse")
        self.db.record_nz_session_event("cookie_reuse")
        self.db.record_nz_session_event("login")

        metrics = self.db.get_nz_session_metrics(days=1)
        self.assertEqual(metrics["cookie_reuse"], 2)
        self.assertEqual(metrics["login"], 1)

    def test_invite_progress_returns_current_and_total_invites(self):
        self.db.add_invite_and_get(1, 2)
        self.db.try_consume_invites(1, 1)

        current, total = self.db.get_invite_progress(1)
        self.assertEqual(current, 1)
        self.assertEqual(total, 2)


if __name__ == "__main__":
    unittest.main()


class DigestOptInTests(unittest.TestCase):
    """Дайджест — opt-in. За замовчуванням вимкнений, інакше бот
    розсилає ранкові повідомлення тим, хто про це не просив."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = DataBase(os.path.join(self.tmpdir.name, "test.db"))
        self.db.add_user(1, "login", "encpw", provider="nz")
        self.db.set_creds_verified(1, 1)
        self.db.add_activity(1)

    def tearDown(self):
        self.db.connection.close()
        self.tmpdir.cleanup()

    def test_digest_is_off_by_default(self):
        self.assertFalse(self.db.user_notify_digest(1))
        self.assertEqual(self.db.get_digest_recipients(), [])

    def test_toggle_on_adds_to_recipients(self):
        self.assertTrue(self.db.toggle_notify_digest(1))
        recipients = self.db.get_digest_recipients()
        self.assertEqual([r[0] for r in recipients], [1])

    def test_toggle_off_removes_from_recipients(self):
        self.db.toggle_notify_digest(1)
        self.assertFalse(self.db.toggle_notify_digest(1))
        self.assertEqual(self.db.get_digest_recipients(), [])


class BlockedUserTests(unittest.TestCase):
    """Хто заблокував бота — випадає з розсилок, поки не повернеться."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = DataBase(os.path.join(self.tmpdir.name, "test.db"))
        self.db.ensure_user(1)
        self.db.ensure_user(2)

    def tearDown(self):
        self.db.connection.close()
        self.tmpdir.cleanup()

    def test_blocked_user_drops_out_of_broadcasts(self):
        self.assertEqual(len(self.db.get_all_users()), 2)
        self.assertEqual(len(self.db.get_non_logged_users()), 2)

        self.db.mark_blocked(1)

        self.assertEqual([r[0] for r in self.db.get_all_users()], [2])
        self.assertEqual([r[0] for r in self.db.get_non_logged_users()], [2])
        self.assertEqual(self.db.count_blocked(), 1)

    def test_blocking_turns_off_notifications(self):
        self.db.toggle_notify(1)
        self.db.toggle_notify_digest(1)
        self.db.mark_blocked(1)

        self.assertFalse(self.db.user_notify(1))
        self.assertFalse(self.db.user_notify_digest(1))

    def test_returning_user_is_unblocked(self):
        self.db.mark_blocked(1)
        self.db.ensure_user(1)          # написав боту знову

        self.assertEqual(self.db.count_blocked(), 0)
        self.assertEqual(len(self.db.get_all_users()), 2)


class ChannelBonusTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = DataBase(os.path.join(self.tmpdir.name, "test.db"))

    def tearDown(self):
        self.db.connection.close()
        self.tmpdir.cleanup()

    def test_channel_bonus_is_one_shot(self):
        self.assertTrue(self.db.try_use_channel_bonus(1))
        self.assertFalse(self.db.try_use_channel_bonus(1))

    def test_bonus_is_per_user(self):
        self.assertTrue(self.db.try_use_channel_bonus(1))
        self.assertTrue(self.db.try_use_channel_bonus(2))

    # Сусідній проєкт віддає весь свій список при кожному опитуванні —
    # захист від подвійної видачі тут єдиний, і він у вставці.
    def test_partner_grant_is_one_shot(self):
        self.assertTrue(self.db.claim_partner_grant(1, "nz"))
        self.assertFalse(self.db.claim_partner_grant(1, "nz"))

    # Кнопка «тиждень VIP за гру» ховається саме по цьому прапорцю
    def test_partner_grant_flag_follows_the_claim(self):
        self.assertFalse(self.db.has_partner_grant(1, "nz"))
        self.db.claim_partner_grant(1, "nz")
        self.assertTrue(self.db.has_partner_grant(1, "nz"))
        self.assertFalse(self.db.has_partner_grant(1, "other"))

    def test_partner_grant_is_per_user_and_source(self):
        self.assertTrue(self.db.claim_partner_grant(1, "nz"))
        self.assertTrue(self.db.claim_partner_grant(2, "nz"))
        self.assertTrue(self.db.claim_partner_grant(1, "other"))
