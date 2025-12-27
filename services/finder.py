import requests
from bs4 import BeautifulSoup
import json
from urllib.parse import urlparse


def getsession(url):
    try:
        response = requests.get(url)  # , cookies=cookies, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        session = soup.find(attrs={"ng-init": True})
        ng_init_value = session['ng-init']
        values = ng_init_value.split(',')
        third_value = values[2].strip()
        if third_value:
            url1 = "https://naurok.com.ua/api2/test/sessions/" + third_value
            return url1
    except Exception as ex:
        print(f"Произошла ошибка: {ex}")


def getinfo(url1: str) -> str:
    response = requests.get(url1, timeout=10)
    response.raise_for_status()

    result_parts = []

    # Пытаемся разобрать JSON
    try:
        data = response.json()
    except ValueError:
        # Если вдруг прилетела не JSON-ответка – просто ничего не возвращаем
        return ""

    # 1) Название теста (settings.name)
    settings = data.get("settings", {})
    name_value = settings.get("name")
    if name_value:
        result_parts.append(f"Назва: {name_value}\n")

    # 2) ID оригинального теста из URL картинки (если есть)
    document_id = None

    for q in data.get("questions", []):
        image = q.get("image")
        if not image:
            continue

        # /uploads/test/1499129/3048996/filename.png
        path_parts = urlparse(image).path.strip("/").split("/")
        # ['uploads', 'test', '1499129', '3048996', 'filename.png']

        if len(path_parts) >= 4:
            document_id = path_parts[3]
            break  # берём первый найденный

    link = "Не найдено"

    if document_id:
        link = get_link(document_id)
        if link:
            result_parts.append(str(link))

    return "".join(result_parts)


cookies = {
    'PHPSESSID': '8ottqcofdkm53gqpab4k339sch',
    '_identity': 'ef29ada95814140ebf388446f0efe08f21d7337684084ad06676952aef7eb68fa%3A2%3A%7Bi%3A0%3Bs%3A9%3A%22_identity%22%3Bi%3A1%3Bs%3A53%3A%22%5B1499313%2C%22sNSKn7Uq3gvrdpM5RqA5jOwNM0HGbF42%22%2C86313600%5D%22%3B%7D',
    '_ga_LPR60N8YM0': 'GS2.3.s1757050573$o1$g0$t1757050573$j60$l0$h0',
    '_csrf': '721ee3696f0e06313100eefb1f19c7f4466de8a280ef7df3b9bf7c5e7453704ba%3A2%3A%7Bi%3A0%3Bs%3A5%3A%22_csrf%22%3Bi%3A1%3Bs%3A32%3A%22WLPaZ5EVqAnl4Fns5aN5_m0zg7RrA4nf%22%3B%7D',
    '_gid': 'GA1.3.793395222.1764436847',
    '_gat_gtag_UA_108352460_1': '1',
    '_dc_gtm_UA-108352460-1': '1',
    '_dc_gtm_UA-108352460-4': '1',
    '_gat_UA-108352460-4': '1',
    '_gat_UA-108352460-3': '1',
    '_ga_2KK52CM69N': 'GS2.3.s1764440978$o17$g1$t1764443899$j60$l0$h0',
    '_ga': 'GA1.1.152428771.1757050561',
    '_ga_1R05Q72V2L': 'GS2.1.s1764436846$o26$g1$t1764443916$j36$l0$h0',
    '_ga_2LWSGFF2KK': 'GS2.3.s1764436847$o26$g1$t1764443916$j36$l0$h0',
}

headers = {
    'accept': '*/*',
    'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7,uk;q=0.6',
    'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
    'origin': 'https://naurok.com.ua',
    'priority': 'u=1, i',
    'referer': 'https://naurok.com.ua/test/test-po-python-2580316.html',
    'sec-ch-ua': '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
    'x-csrf-token': 'IB9JwdSwHMoV6qYVYHO5BsPNFhDDhgehigOXBtaJ2pV3UxmgjoVZnGSryHlUNdd19qxYJZzrN9vtNMV0l7208w==',
    'x-requested-with': 'XMLHttpRequest',
    # 'cookie': 'PHPSESSID=8ottqcofdkm53gqpab4k339sch; _identity=ef29ada95814140ebf388446f0efe08f21d7337684084ad06676952aef7eb68fa%3A2%3A%7Bi%3A0%3Bs%3A9%3A%22_identity%22%3Bi%3A1%3Bs%3A53%3A%22%5B1499313%2C%22sNSKn7Uq3gvrdpM5RqA5jOwNM0HGbF42%22%2C86313600%5D%22%3B%7D; _ga_LPR60N8YM0=GS2.3.s1757050573$o1$g0$t1757050573$j60$l0$h0; _csrf=721ee3696f0e06313100eefb1f19c7f4466de8a280ef7df3b9bf7c5e7453704ba%3A2%3A%7Bi%3A0%3Bs%3A5%3A%22_csrf%22%3Bi%3A1%3Bs%3A32%3A%22WLPaZ5EVqAnl4Fns5aN5_m0zg7RrA4nf%22%3B%7D; _gid=GA1.3.793395222.1764436847; _gat_gtag_UA_108352460_1=1; _dc_gtm_UA-108352460-1=1; _dc_gtm_UA-108352460-4=1; _gat_UA-108352460-4=1; _gat_UA-108352460-3=1; _ga_2KK52CM69N=GS2.3.s1764440978$o17$g1$t1764443899$j60$l0$h0; _ga=GA1.1.152428771.1757050561; _ga_1R05Q72V2L=GS2.1.s1764436846$o26$g1$t1764443916$j36$l0$h0; _ga_2LWSGFF2KK=GS2.3.s1764436847$o26$g1$t1764443916$j36$l0$h0',
}

BASE_URL = "https://naurok.com.ua"


def get_link(test_id: int | str) -> str | None:
    session = requests.Session()

    # 1. Первый запрос – просто чтобы получить страницу и, при необходимости, csrf
    res = session.get(BASE_URL, timeout=10)
    res.raise_for_status()

    csrftoken = session.cookies.get("csrftoken")

    if not csrftoken:
        soup = BeautifulSoup(res.text, "html.parser")
        csrf_input = soup.find("input", {"name": "_csrf"})
        if csrf_input:
            csrftoken = csrf_input.get("value")
    # 2. Делаешь свой POST (оставляю как есть, ты сам контролируешь cookies/headers)
    data = {"_csrf": csrftoken}

    response = requests.post(
        f"{BASE_URL}/api/test/document-bookmark/toggle/{test_id}",
        cookies=cookies,
        headers=headers,
        data=data,
        timeout=10,
    )
    # если хочешь – можешь тут добавить check на response.status_code

    # 3. Тянем страницу закладок и парсим первый тест
    bookmark = session.get(f"{BASE_URL}/test/bookmarks", cookies=cookies, headers=headers, timeout=10)
    bookmark.raise_for_status()

    soup = BeautifulSoup(bookmark.text, "html.parser")
    first_link = soup.select_one(".file-item.test-item .headline a")

    if not first_link:
        return None

    href = first_link.get("href", "").strip()
    if not href:
        return None

    # склеиваем с базовым урлом
    response = requests.post(
        f"{BASE_URL}/api/test/document-bookmark/toggle/{test_id}",
        cookies=cookies,
        headers=headers,
        data=data,
        timeout=10,
    )
    return BASE_URL + href
