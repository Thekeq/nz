import requests
from bs4 import BeautifulSoup
import json

def getsession(url):
    try:
        response = requests.get(url)#, cookies=cookies, headers=headers)
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

def getinfo(url1):
    response = requests.get(url1)
    soup = BeautifulSoup(response.text, 'html.parser')
    if "name" in response.text:
        json_data = response.text
        data = json.loads(json_data)

        # Извлечение информации из поля "name"
        name_value = data["settings"]["name"]

        return name_value