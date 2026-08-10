import logging
import os
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://naurok.com.ua"


def getsession(url: str) -> str | None:
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        session = soup.find(attrs={"ng-init": True})
        if not session:
            return None

        ng_init_value = session.get("ng-init", "")
        values = ng_init_value.split(",")
        if len(values) < 3:
            return None

        session_id = values[2].strip()
        if session_id:
            return f"{BASE_URL}/api2/test/sessions/{session_id}"
    except Exception:
        logger.exception("Failed to extract Naurok session url")

    return None


def getinfo(url1: str | None) -> str:
    if not url1:
        return "Не вдалося отримати сесію тесту."

    response = requests.get(url1, timeout=10)
    response.raise_for_status()

    try:
        data = response.json()
    except ValueError:
        return ""

    result_parts = []
    settings = data.get("settings", {})
    name_value = settings.get("name")
    if name_value:
        result_parts.append(f"Назва: {name_value}\n")

    document_id = None
    for q in data.get("questions", []):
        image = q.get("image")
        if not image:
            continue

        path_parts = urlparse(image).path.strip("/").split("/")
        if len(path_parts) >= 4:
            document_id = path_parts[3]
            break

    if document_id:
        link = get_link(document_id)
        if link:
            result_parts.append(str(link))

    return "".join(result_parts) or "Посилання не знайдено."


def _naurok_headers() -> dict[str, str]:
    headers = {
        "accept": "*/*",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "origin": BASE_URL,
        "referer": f"{BASE_URL}/",
        "user-agent": "Mozilla/5.0",
        "x-requested-with": "XMLHttpRequest",
    }

    csrf_token = os.getenv("NAUROK_CSRF_TOKEN", "").strip()
    if csrf_token:
        headers["x-csrf-token"] = csrf_token

    return headers


def _naurok_cookies() -> dict[str, str]:
    raw_cookie = os.getenv("NAUROK_COOKIE", "").strip()
    if not raw_cookie:
        return {}

    cookies = {}
    for part in raw_cookie.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        cookies[key.strip()] = value.strip()
    return cookies


def get_link(test_id: int | str) -> str | None:
    cookies = _naurok_cookies()
    if not cookies:
        logger.warning("NAUROK_COOKIE is not configured; bookmark lookup is disabled")
        return None

    session = requests.Session()
    headers = _naurok_headers()

    res = session.get(BASE_URL, cookies=cookies, headers=headers, timeout=10)
    res.raise_for_status()

    csrftoken = session.cookies.get("csrftoken")
    if not csrftoken:
        soup = BeautifulSoup(res.text, "html.parser")
        csrf_input = soup.find("input", {"name": "_csrf"})
        if csrf_input:
            csrftoken = csrf_input.get("value")

    data = {"_csrf": csrftoken or ""}

    toggle_url = f"{BASE_URL}/api/test/document-bookmark/toggle/{test_id}"
    response = session.post(toggle_url, cookies=cookies, headers=headers, data=data, timeout=10)
    response.raise_for_status()

    bookmark = session.get(f"{BASE_URL}/test/bookmarks", cookies=cookies, headers=headers, timeout=10)
    bookmark.raise_for_status()

    soup = BeautifulSoup(bookmark.text, "html.parser")
    first_link = soup.select_one(".file-item.test-item .headline a")
    href = first_link.get("href", "").strip() if first_link else ""

    session.post(toggle_url, cookies=cookies, headers=headers, data=data, timeout=10)

    if not href:
        return None
    return BASE_URL + href
