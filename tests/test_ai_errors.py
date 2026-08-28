import asyncio
import importlib.util
import unittest

def _absent(name: str) -> bool:
    # find_spec падає, а не повертає None, коли немає батьківського пакета
    try:
        return importlib.util.find_spec(name) is None
    except (ImportError, ModuleNotFoundError, ValueError):
        return True


missing_deps = [n for n in ("google.genai", "PIL", "dotenv") if _absent(n)]


@unittest.skipIf(bool(missing_deps), f"missing dependencies: {', '.join(missing_deps)}")
class AIFailureClassificationTests(unittest.TestCase):
    """Збій провайдера має доходити до користувача як збій.

    Ціна помилки тут не косметична: тільки AIUnavailable повертає безкоштовний
    запит і будить адміна. Виняток, який до неї не доїхав, виглядає для
    користувача як «погане запитання» — і поломка живе, доки хтось не полізе
    в журнал.
    """

    @classmethod
    def setUpClass(cls):
        global ai_module
        from services import ai as ai_module

    def _raises(self, error: Exception):
        original = ai_module._generate_content

        def boom(contents, max_output_tokens):
            raise error

        ai_module._generate_content = boom
        try:
            with self.assertRaises(ai_module.AIUnavailable):
                asyncio.run(ai_module.ai("2+2?"))
        finally:
            ai_module._generate_content = original

    def test_geo_block_is_an_outage(self):
        # реальна відмова Google: 400, якого не було в старому переліку
        self._raises(RuntimeError(
            "400 FAILED_PRECONDITION. {'error': {'code': 400, 'message': "
            "'User location is not supported for the API use.'}}"))

    def test_quota_is_an_outage(self):
        self._raises(RuntimeError("429 RESOURCE_EXHAUSTED"))

    def test_unknown_error_is_an_outage_too(self):
        # саме той випадок, на якому попався попередній перелік статусів
        self._raises(RuntimeError("щось геть нове"))


if __name__ == "__main__":
    unittest.main()
