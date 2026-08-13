# Деплой на VPS (Ubuntu 22.04 / 24.04)

Бот тримає один процес із long polling. **Два процеси з одним `BOT_TOKEN`
працювати не можуть** — Telegram віддасть `409 Conflict`, і оновлення почнуть
губитись. Тому старий інстанс на PythonAnywhere треба зупинити ДО запуску нового.

Розкладка збігається з іншими проєктами на цьому сервері:
код у `/opt/nz/app`, venv у `/opt/nz/venv`, окремий системний
користувач `nzbot` без shell.

---

## 0. Що переносимо руками

Цих файлів немає в git і бути не повинно:

| Файл | Чому критичний |
|---|---|
| `.env` | Містить `key` — ключ Fernet. **Без нього всі збережені паролі щоденників не розшифруються**, 1000 користувачів доведеться просити перелогінитись. |
| `data.db` | VIP-підписки (зокрема оплачені), реферали, токени, налаштування сповіщень. |

---

## 1. Зупинити бота на PythonAnywhere

Спочатку зупинити старий процес — інакше буде конфлікт polling.
Забрати з PythonAnywhere свіжу копію бази і `.env` (через їхній файловий
менеджер або `scp`).

## 2. Підготувати сервер

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git

# Годинник у київському часі: розклад, нагадування і дайджест
# рахуються від Europe/Kyiv, а денні лічильники активності — від
# локального часу сервера. Так вони не розїдуться.
sudo timedatectl set-timezone Europe/Kyiv
timedatectl
```

## 3. Викласти код

```bash
useradd --system --home /opt/nz --shell /usr/sbin/nologin nzbot

mkdir -p /opt/nz
git clone https://github.com/Thekeq/nz.git /opt/nz/app

python3 -m venv /opt/nz/venv
/opt/nz/venv/bin/pip install --upgrade pip
/opt/nz/venv/bin/pip install -r /opt/nz/app/requirements.txt
```

## 4. Залити секрети й базу

З локальної машини (Git Bash / PowerShell):

```bash
scp .env    root@СЕРВЕР:/opt/nz/app/.env
scp data.db root@СЕРВЕР:/opt/nz/app/data.db
```

Права на секрети — тільки власнику:

```bash
chown -R nzbot:nzbot /opt/nz
chmod 600 /opt/nz/app/.env /opt/nz/app/data.db
```

## 5. Перевірити запуск руками

Перед systemd — переконатись, що бот взагалі стартує:

```bash
cd /opt/nz/app
/opt/nz/venv/bin/python main.py
```

Очікувано в логах: `Bot starting`. Напиши боту `/start` — має відповісти.
Потім `Ctrl+C`.

Якщо тут падає `RuntimeError: BOT_TOKEN not set` — `.env` не долетів або
лежить не в тій теці.

## 6. Systemd-сервіс

```bash
sudo cp /opt/nz/app/deploy/nz-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nz-bot

systemctl status nz-bot
journalctl -u nz-bot -f          # живі логи
```

Сервіс сам підніметься після падіння і після перезавантаження VPS.

---

## Оновлення після пушу в git

```bash
cd /opt/nz/app
git pull
/opt/nz/venv/bin/pip install -q -r requirements.txt   # якщо змінювались залежності
sudo systemctl restart nz-bot
```

## Бекапи

Бот сам робить копію бази щодня о 03:30 у `/opt/nz/app/backups/`
(зберігає 7 останніх). Це копії **на тому ж диску** — вони рятують від
кривої міграції чи випадкового видалення, але не від смерті VPS.
Раз на тиждень варто забирати копію до себе:

```bash
scp root@СЕРВЕР:/opt/nz/app/backups/data-*.db ./
```

## Якщо щось пішло не так

| Симптом | Причина |
|---|---|
| Бот мовчить, у логах `Conflict: terminated by other getUpdates` | Десь ще живий другий інстанс (PythonAnywhere або друга копія сервісу) |
| `RuntimeError: key not set` | `.env` не на місці або без змінної `key` |
| `InvalidToken` при розшифровці паролів | Підмінили `key` — потрібен рівно той самий Fernet-ключ, що й раніше |
| Сервіс перезапускається по колу | `journalctl -u nz-bot -n 50` покаже трейс |
