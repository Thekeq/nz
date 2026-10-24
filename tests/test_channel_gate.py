import importlib
import importlib.util
import sys
import types
import unittest
from types import SimpleNamespace


@unittest.skipIf(importlib.util.find_spec("aiogram") is None, "aiogram not installed")
class ChannelGateBypassTests(unittest.TestCase):
    """Гейт підписки не сміє з'їдати оплату і реферальний /start."""

    @classmethod
    def setUpClass(cls):
        # middlewares імпортує loader (токен бота, pytz) — підставляємо заглушку
        cls._saved = {name: sys.modules.get(name) for name in ("loader", "middlewares")}
        sys.modules["loader"] = types.SimpleNamespace(CHANNEL_ID="@x", CHANNEL_URL="https://t.me/x", bot=None)
        sys.modules.pop("middlewares", None)
        cls.mw = importlib.import_module("middlewares")

    @classmethod
    def tearDownClass(cls):
        for name, module in cls._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_successful_payment_passes(self):
        event = SimpleNamespace(successful_payment=object(), text=None)
        self.assertTrue(self.mw.bypasses_channel_gate(event))

    def test_referral_start_passes(self):
        event = SimpleNamespace(successful_payment=None, text="/start 1076078800")
        self.assertTrue(self.mw.bypasses_channel_gate(event))

    def test_regular_actions_are_gated(self):
        for text in ("/diary", "📅 Розклад", "/vip", None):
            event = SimpleNamespace(successful_payment=None, text=text)
            self.assertFalse(self.mw.bypasses_channel_gate(event), text)


if __name__ == "__main__":
    unittest.main()
