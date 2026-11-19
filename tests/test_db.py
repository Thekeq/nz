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
        self.db.set_session_cookies(1, "encrypted-cookies")
        self.assertEqual(self.db.get_session_cookies(1), "encrypted-cookies")

        self.db.delete_session_cookies(1)
        self.assertIsNone(self.db.get_session_cookies(1))

    def test_delete_user_clears_sessions_but_keeps_vip(self):
        self.db.add_user(1, "login", "password")
        self.db.set_vip(1, 30)
        self.db.set_session_cookies(1, "encrypted-cookies")

        self.db.delete_user(1)

        self.assertFalse(self.db.has_credentials(1))
        self.assertIsNone(self.db.get_session_cookies(1))
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

    def test_weekly_wrapped_claim_is_one_shot_after_success(self):
        self.db.ensure_user(1)

        self.assertTrue(self.db.try_claim_weekly_wrapped(1, "2026-09-25"))
        self.db.set_weekly_wrapped_status(1, "2026-09-25", "sent")

        self.assertFalse(self.db.try_claim_weekly_wrapped(1, "2026-09-25"))
        self.assertEqual(self.db.get_weekly_wrapped_logs()[0]["status"], "sent")

    def test_weekly_wrapped_can_retry_after_no_grades(self):
        self.db.ensure_user(1)

        self.assertTrue(self.db.try_claim_weekly_wrapped(1, "2026-09-25"))
        self.db.set_weekly_wrapped_status(1, "2026-09-25", "no_grades", "empty")

        self.assertTrue(self.db.try_claim_weekly_wrapped(1, "2026-09-25"))

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
        self.db.add_user(1, "login", "encpw")
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


class FunnelMetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = DataBase(os.path.join(self.tmpdir.name, "test.db"))

    def tearDown(self):
        self.db.connection.close()
        self.tmpdir.cleanup()

    def test_funnel_is_collected_and_kept_out_of_top_actions(self):
        for _ in range(3):
            self.db.record_command_metric("funnel:vip_menu", 0)
        self.db.record_command_metric("funnel:invoice:week", 0)
        self.db.record_command_metric("/diary", 120)

        funnel = self.db.get_funnel_metrics(days=7)
        self.assertEqual(funnel, {"vip_menu": 3, "invoice:week": 1})

        top = [m["command"] for m in self.db.get_command_metrics(days=7)]
        self.assertEqual(top, ["/diary"])


class VipEnablesNotificationsTests(unittest.TestCase):
    """VIP без увімкнених сповіщень — порожній: юзер нічого не відчуває
    і нічого не втрачає, коли VIP закінчується."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = DataBase(os.path.join(self.tmpdir.name, "test.db"))

    def tearDown(self):
        self.db.connection.close()
        self.tmpdir.cleanup()

    def test_new_vip_turns_on_grades_and_reminders(self):
        self.db.set_vip(1, days=3, source="ref")
        self.assertTrue(self.db.user_notify(1))
        self.assertTrue(self.db.user_notify_grades(1))
        self.assertFalse(self.db.user_notify_digest(1))  # дайджест лишається opt-in

    def test_extension_keeps_user_choice(self):
        self.db.set_vip(1, days=3)
        self.db.toggle_notify_grades(1)                  # сам вимкнув
        self.db.set_vip(1, days=3, source="ref")         # друг дав ще 3 дні
        self.assertFalse(self.db.user_notify_grades(1))

    def test_new_vip_after_expiry_turns_on_again(self):
        self.db.set_vip(1, days=3)
        self.db.remove_vip(1)
        self.db.toggle_notify_grades(1)                  # вимкнено
        self.db.set_vip(1, days=7)                       # купив
        self.assertTrue(self.db.user_notify_grades(1))


class ParentModeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = DataBase(os.path.join(self.tmpdir.name, "test.db"))

    def tearDown(self):
        self.db.connection.close()
        self.tmpdir.cleanup()

    def test_invite_is_reused_until_redeemed(self):
        # повторне відкриття меню не має вбивати вже надіслане посилання
        token = self.db.get_or_create_parent_invite(1, "Петро")
        self.assertEqual(self.db.get_or_create_parent_invite(1, "Петро"), token)

    def test_invite_is_one_shot(self):
        token = self.db.get_or_create_parent_invite(1, "Петро")
        self.assertEqual(self.db.redeem_parent_invite(token, 2, "Мама"), (1, "Петро"))
        self.assertIsNone(self.db.redeem_parent_invite(token, 3, "Хтось"))
        self.assertEqual(self.db.get_parents(1), [(2, "Мама", "Петро")])
        self.assertEqual(self.db.get_children(2), [(1, "Петро")])

    def test_child_cannot_be_own_parent(self):
        token = self.db.get_or_create_parent_invite(1, "Петро")
        self.assertIsNone(self.db.redeem_parent_invite(token, 1, "Петро"))
        self.assertEqual(self.db.get_parents(1), [])

    def test_expired_invite_rejected(self):
        token = self.db.get_or_create_parent_invite(1, "Петро")
        with self.db.connection:
            self.db.connection.execute("UPDATE parent_invites SET created_at=0")
        self.assertIsNone(self.db.redeem_parent_invite(token, 2, "Мама"))

    def test_unlink_and_ownership_check(self):
        token = self.db.get_or_create_parent_invite(1, "Петро")
        self.db.redeem_parent_invite(token, 2, "Мама")
        self.assertEqual(self.db.get_child_name(2, 1), "Петро")
        self.assertIsNone(self.db.get_child_name(3, 1))   # чужі батьки не платять «за» дитину
        self.assertTrue(self.db.unlink_parent(2, 1))
        self.assertFalse(self.db.unlink_parent(2, 1))
        self.assertIsNone(self.db.get_child_name(2, 1))

    def test_watched_child_gets_grade_checks_even_with_own_notify_off(self):
        self.db.ensure_user(1)
        self.db.add_user(1, "login", "enc")
        self.assertEqual(self.db.get_users_with_grades_notify(), [])
        token = self.db.get_or_create_parent_invite(1, "Петро")
        self.db.redeem_parent_invite(token, 2, "Мама")
        rows = self.db.get_users_with_grades_notify()
        self.assertEqual([(r[0], r[3]) for r in rows], [(1, 0)])


class AiBonusAndSeenGradesTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = DataBase(os.path.join(self.tmpdir.name, "test.db"))

    def tearDown(self):
        self.db.connection.close()
        self.tmpdir.cleanup()

    def test_bonus_spent_after_weekly(self):
        self.db.add_ai_bonus(1, 2)
        self.assertEqual(self.db.free_ai_left(1, limit=1), 3)
        self.assertTrue(self.db.try_use_free_ai(1, limit=1))   # тижневий
        self.assertTrue(self.db.try_use_free_ai(1, limit=1))   # бонус
        self.assertTrue(self.db.try_use_free_ai(1, limit=1))   # бонус
        self.assertFalse(self.db.try_use_free_ai(1, limit=1))
        self.assertEqual(self.db.free_ai_left(1, limit=1), 0)

    def test_swap_seen_grades(self):
        self.assertEqual(self.db.swap_seen_grades(1, 10), -1)
        self.assertEqual(self.db.swap_seen_grades(1, 12), 10)
        self.assertEqual(self.db.swap_seen_grades(1, 12), 12)
