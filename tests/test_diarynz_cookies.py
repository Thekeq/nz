import json
import importlib.util
import unittest

missing_deps = [
    name
    for name in ("requests", "cloudscraper", "bs4")
    if importlib.util.find_spec(name) is None
]


@unittest.skipIf(bool(missing_deps), f"missing dependencies: {', '.join(missing_deps)}")
class DiaryNzCookieTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global requests, _dump_cookies, _load_cookies, _session_signature

        import requests
        from services.diarynz import _dump_cookies, _load_cookies, _session_signature

    def make_scraper(self):
        scraper = type("DummyScraper", (), {})()
        scraper.cookies = requests.cookies.RequestsCookieJar()
        scraper.headers = {"User-Agent": "ua-1"}
        return scraper

    def test_cookie_dump_load_preserves_session_cookie_metadata_and_user_agent(self):
        source = self.make_scraper()
        source.cookies.set_cookie(requests.cookies.create_cookie(
            name="PHPSESSID",
            value="session-id",
            domain="nz.ua",
            path="/",
            secure=True,
            expires=4102444800,
            rest={"HttpOnly": None},
        ))
        source.cookies.set_cookie(requests.cookies.create_cookie(
            name="cf_clearance",
            value="cf-token",
            domain=".nz.ua",
            path="/",
            secure=True,
            expires=4102444801,
        ))

        raw = _dump_cookies(source)
        payload = json.loads(raw)
        self.assertEqual(payload["user_agent"], "ua-1")
        self.assertEqual({c["name"] for c in payload["cookies"]}, {"PHPSESSID", "cf_clearance"})

        target = self.make_scraper()
        target.headers["User-Agent"] = "ua-2"
        _load_cookies(target, raw)

        self.assertEqual(target.headers["User-Agent"], "ua-1")
        php_session = next(cookie for cookie in target.cookies if cookie.name == "PHPSESSID")
        self.assertEqual(php_session.value, "session-id")
        self.assertEqual(php_session.domain, "nz.ua")
        self.assertEqual(php_session.path, "/")
        self.assertTrue(php_session.secure)
        self.assertEqual(php_session.expires, 4102444800)

    def test_legacy_cookie_format_still_loads(self):
        scraper = self.make_scraper()
        _load_cookies(scraper, '{"PHPSESSID":"legacy-session"}')
        self.assertEqual(scraper.cookies.get("PHPSESSID"), "legacy-session")

    def test_session_signature_changes_when_cookie_expiry_changes(self):
        scraper = self.make_scraper()
        scraper.cookies.set_cookie(requests.cookies.create_cookie(
            name="PHPSESSID",
            value="session-id",
            domain="nz.ua",
            expires=1,
        ))
        first = _session_signature(_dump_cookies(scraper))

        scraper.cookies.clear()
        scraper.cookies.set_cookie(requests.cookies.create_cookie(
            name="PHPSESSID",
            value="session-id",
            domain="nz.ua",
            expires=2,
        ))
        second = _session_signature(_dump_cookies(scraper))

        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
