import sqlite3
import datetime


class DataBase:
    def __init__(self, db_file):
        self.connection = sqlite3.connect(db_file, check_same_thread=False)
        self.cursor = self.connection.cursor()
        self._init_schema()

    def _init_schema(self):
        with self.connection:
            # Основная информация о пользователе (рефералы, приглашения)
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                  user_id        INTEGER PRIMARY KEY,
                  referrer_id    INTEGER,
                  invites        INTEGER NOT NULL DEFAULT 0,
                  total_invites  INTEGER NOT NULL DEFAULT 0,
                  created_at     INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                )
            """)

            # Логин и пароль NZ
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS creds (
                  user_id    INTEGER PRIMARY KEY,
                  login      TEXT,
                  password   TEXT,
                  updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                  FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # VIP, срок, нотификации
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS subs (
                  user_id  INTEGER PRIMARY KEY,
                  vip      INTEGER NOT NULL DEFAULT 0,
                  expires  INTEGER NOT NULL DEFAULT 0,
                  notify   INTEGER NOT NULL DEFAULT 0,
                  FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            # Таблица активності користувачів по днях
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS activity (
                  user_id INTEGER NOT NULL,
                  day     INTEGER NOT NULL,      -- номер дня (ordinal)
                  actions INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY(user_id, day),
                  FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # --- after CREATE TABLE subs ---
            try:
                self.cursor.execute("ALTER TABLE subs ADD COLUMN notify_grades INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass

            try:
                self.cursor.execute("ALTER TABLE creds ADD COLUMN verified INTEGER NOT NULL DEFAULT 0;")
                self.cursor.execute("ALTER TABLE creds ADD COLUMN verified_at INTEGER;")
            except Exception:
                pass

            try:
                self.cursor.execute("ALTER TABLE creds ADD COLUMN provider TEXT NOT NULL DEFAULT 'nz';")
            except Exception:
                pass

                # на всякий: старые строки могли остаться NULL
            try:
                self.cursor.execute("UPDATE creds SET provider='nz' WHERE provider IS NULL OR provider='';")
            except Exception:
                pass
            # last sent grade hash per user
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS grades_state (
                  user_id INTEGER PRIMARY KEY,
                  last_hash TEXT,
                  updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                  FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS ref_rewarded (
                  user_id     INTEGER PRIMARY KEY,      -- тот, кого пригласили
                  referrer_id INTEGER NOT NULL,         -- кто пригласил
                  rewarded_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                )
            """)

            try:
                self.cursor.execute("ALTER TABLE subs ADD COLUMN tokens INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass

    # ===== Utility =====
    def get_vip_referral_stats(self, days: int = 14):
        """
        Повертає список кортежів (user_id, invite_count) для ВСІХ активних VIP-користувачів.
        invite_count — кількість запрошених за останні days днів.
        Сортує від найбільшої кількості запрошень до найменшої.
        """
        now_ts = int(datetime.datetime.now().timestamp())
        start_ts = now_ts - (days * 24 * 60 * 60)

        with self.connection:
            # Логіка:
            # 1. Беремо таблицю subs (VIP юзери)
            # 2. Робимо LEFT JOIN з ref_rewarded (щоб порахувати запрошених)
            # 3. Фільтруємо тільки активних VIP
            # 4. Фільтруємо запрошення тільки за останні N днів
            rows = self.cursor.execute(
                """
                SELECT s.user_id, COUNT(r.user_id) as invite_count
                FROM subs s
                LEFT JOIN ref_rewarded r 
                    ON s.user_id = r.referrer_id 
                    AND r.rewarded_at >= ?
                WHERE s.vip = 1 AND (s.expires = 0 OR s.expires > ?)
                GROUP BY s.user_id
                ORDER BY invite_count DESC
                """,
                (start_ts, now_ts)
            ).fetchall()
            return rows

    def get_raffle_participants(self, days: int = 14):
        """
        Повертає повний список ID користувачів, де ID повторюється стільки разів,
        скільки у юзера квитків (1 за VIP + N за рефералів).
        """
        stats = self.get_vip_referral_stats(days=days)  # Використовуємо твій метод статистики
        tickets_drum = []

        for user_id, invite_count in stats:
            # 1 квиток за VIP + кількість запрошених
            total_tickets = invite_count + 1
            tickets_drum.extend([user_id] * total_tickets)

        return tickets_drum

    def set_tokens(self, user_id: int, amount: int):
        """Встановлює фіксовану кількість токенів (перезаписує старе значення)."""
        self.ensure_user(user_id)
        with self.connection:
            self.cursor.execute("UPDATE subs SET tokens=? WHERE user_id=?", (amount, user_id))

    def get_tokens(self, user_id: int) -> int:
        """Повертає баланс токенів користувача."""
        with self.connection:
            row = self.cursor.execute("SELECT tokens FROM subs WHERE user_id=?", (user_id,)).fetchone()
            return int(row[0] or 0) if row else 0

    def deduct_tokens(self, user_id: int, amount: int):
        """Віднімає токени. Не дозволяє опуститися нижче 0."""
        with self.connection:
            self.cursor.execute(
                "UPDATE subs SET tokens = MAX(0, tokens - ?) WHERE user_id=?",
                (amount, user_id)
            )

    def try_mark_ref_rewarded(self, user_id: int) -> bool:
        """
        Возвращает True только первый раз.
        Дальше False (значит уже засчитали).
        """
        with self.connection:
            self.cursor.execute(
                """
                INSERT OR IGNORE INTO ref_rewarded(user_id, referrer_id)
                SELECT user_id, referrer_id FROM users WHERE user_id=?
                """,
                (user_id,)
            )
            return self.cursor.rowcount == 1

    def get_referrer_for_reward(self, user_id: int) -> int | None:
        with self.connection:
            row = self.cursor.execute(
                "SELECT referrer_id FROM users WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return int(row[0]) if row and row[0] else None

    def add_invite_and_get(self, user_id: int, delta: int = 1) -> int:
        self.ensure_user(user_id)
        with self.connection:
            self.cursor.execute(
                "UPDATE users SET invites = invites + ? WHERE user_id=?",
                (delta, user_id)
            )
            if delta > 0:
                self.cursor.execute(
                    "UPDATE users SET total_invites = total_invites + ? WHERE user_id=?",
                    (delta, user_id)
                )
            row = self.cursor.execute(
                "SELECT invites FROM users WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return int(row[0] or 0) if row else 0

    def try_consume_invites(self, user_id: int, need: int = 3) -> bool:
        """
        Атомарно списывает need, только если invites >= need.
        Возвращает True если списало.
        """
        self.ensure_user(user_id)
        with self.connection:
            self.cursor.execute(
                "UPDATE users SET invites = invites - ? WHERE user_id=? AND invites >= ?",
                (need, user_id, need)
            )
            return self.cursor.rowcount == 1

    def ensure_user(self, user_id: int):
        with self.connection:
            self.cursor.execute("INSERT OR IGNORE INTO users(user_id) VALUES (?)", (user_id,))
            self.cursor.execute("INSERT OR IGNORE INTO subs(user_id) VALUES (?)", (user_id,))

    # ===== Credentials (login/password) =====

    def has_credentials(self, user_id: int) -> bool:
        with self.connection:
            row = self.cursor.execute(
                "SELECT login, password FROM creds WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return bool(row and row[0] and row[1])

    def set_creds_verified(self, user_id: int, val: int = 1):
        with self.connection:
            self.cursor.execute(
                "UPDATE creds SET verified=?, verified_at=strftime('%s','now') WHERE user_id=?",
                (val, user_id)
            )

    def is_creds_verified(self, user_id: int) -> bool:
        with self.connection:
            row = self.cursor.execute(
                "SELECT verified FROM creds WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return bool(int(row[0])) if row else False

    def count_verified_by_provider(self, provider: str) -> int:
        with self.connection:
            row = self.cursor.execute(
                "SELECT COUNT(*) FROM creds WHERE verified=1 AND provider=?",
                (provider,)
            ).fetchone()
            return row[0] if row else 0

    def user_exists(self, user_id: int) -> bool:
        with self.connection:
            row = self.cursor.execute(
                "SELECT 1 FROM users WHERE user_id=? LIMIT 1",
                (user_id,)
            ).fetchone()
            return row is not None

    def get_user(self, user_id: int):
        with self.connection:
            row = self.cursor.execute(
                "SELECT login, password, COALESCE(provider,'nz') FROM creds WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return row if row else (None, None, "nz")

    def add_user(self, user_id: int, login: str, password: str, provider: str = "nz"):
        self.ensure_user(user_id)
        provider = provider if provider in ("nz", "human") else "nz"
        with self.connection:
            self.cursor.execute(
                """
                INSERT INTO creds(user_id, login, password, provider, updated_at)
                VALUES (?, ?, ?, ?, strftime('%s','now'))
                ON CONFLICT(user_id) DO UPDATE SET
                    login=excluded.login,
                    password=excluded.password,
                    provider=excluded.provider,
                    updated_at=excluded.updated_at
                """,
                (user_id, login, password, provider)
            )

    def delete_user(self, user_id: int):
        # logout: удаляем только логин/пароль, VIP не трогаем
        with self.connection:
            self.cursor.execute("DELETE FROM creds WHERE user_id=?", (user_id,))

    # ===== VIP =====

    def set_vip(self, user_id: int, days: int = 30):
        self.ensure_user(user_id)

        # Якщо днів 0 — це означає назавжди
        if days == 0:
            new_expires = 0
        else:
            now = int(datetime.datetime.now().timestamp())
            add_sec = int(datetime.timedelta(days=days).total_seconds())

            with self.connection:
                row = self.cursor.execute(
                    "SELECT vip, expires FROM subs WHERE user_id=?",
                    (user_id,)
                ).fetchone()
                # Перевіряємо поточний статус
                current_vip = int(row[0] or 0) if row else 0
                current_expires = int(row[1] or 0) if row else 0

                # Логіка додавання часу:
                # 1. Якщо юзер вже VIP і час ще не вийшов (і це не безліміт) — додаємо до залишку.
                # 2. Якщо у юзера "назавжди" (0), то додавання днів не має змінити статус (залишаємо 0),
                #    ХІБА ЩО ти хочеш "понизити" його до тимчасового.
                #    У цьому коді: якщо у юзера "назавжди", додавання днів ігнорується, він лишається вічним.

                if current_vip and current_expires == 0:
                    new_expires = 0  # Вже вічний, лишаємо вічним
                elif current_vip and current_expires > now:
                    new_expires = current_expires + add_sec
                else:
                    new_expires = now + add_sec

        self.cursor.execute(
            """
            INSERT INTO subs(user_id, vip, expires)
            VALUES (?, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET vip=1, expires=excluded.expires
            """,
            (user_id, new_expires)
        )

    def get_vip_status(self, user_id: int):
        with self.connection:
            row = self.cursor.execute(
                "SELECT vip, expires FROM subs WHERE user_id=?",
                (user_id,)
            ).fetchone()
            if not row:
                return False, 0
            vip, expires = int(row[0] or 0), int(row[1] or 0)
            return bool(vip), expires

    # ===== Notify =====

    def toggle_notify(self, user_id: int) -> bool:
        self.ensure_user(user_id)
        with self.connection:
            row = self.cursor.execute(
                "SELECT notify FROM subs WHERE user_id=?",
                (user_id,)
            ).fetchone()
            current = int(row[0] or 0) if row else 0
            new_val = 0 if current == 1 else 1
            self.cursor.execute(
                """
                INSERT INTO subs(user_id, notify) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET notify=excluded.notify
                """,
                (user_id, new_val)
            )
            return bool(new_val)

    def user_notify(self, user_id: int) -> bool:
        with self.connection:
            row = self.cursor.execute(
                "SELECT notify FROM subs WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return bool(int(row[0] or 0)) if row else False

    def get_users_with_notify(self):
        with self.connection:
            return self.cursor.execute(
                """
                SELECT u.user_id, c.login, c.password, COALESCE(c.provider,'nz')
                FROM subs s
                JOIN users u ON u.user_id=s.user_id
                LEFT JOIN creds c ON c.user_id=u.user_id
                WHERE s.notify=1
                """
            ).fetchall()

    def toggle_notify_grades(self, user_id: int) -> bool:
        self.ensure_user(user_id)
        with self.connection:
            row = self.cursor.execute(
                "SELECT notify_grades FROM subs WHERE user_id=?",
                (user_id,)
            ).fetchone()
            current = int(row[0] or 0) if row else 0
            new_val = 0 if current == 1 else 1
            self.cursor.execute(
                """
                INSERT INTO subs(user_id, notify_grades) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET notify_grades=excluded.notify_grades
                """,
                (user_id, new_val)
            )
        return bool(new_val)

    def user_notify_grades(self, user_id: int) -> bool:
        with self.connection:
            row = self.cursor.execute(
                "SELECT notify_grades FROM subs WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return bool(int(row[0] or 0)) if row else False

    def get_users_with_grades_notify(self):
        with self.connection:
            return self.cursor.execute(
                """
                SELECT u.user_id, c.login, c.password, COALESCE(c.provider,'nz')
                FROM subs s
                JOIN users u ON u.user_id=s.user_id
                LEFT JOIN creds c ON c.user_id=u.user_id
                WHERE s.notify_grades=1
                """
            ).fetchall()

    def get_last_grade_hashes(self, user_id: int) -> list[str]:
        with self.connection:
            row = self.cursor.execute(
                "SELECT last_hash FROM grades_state WHERE user_id=?",
                (user_id,)
            ).fetchone()

            if not row or not row[0]:
                return []

            return row[0].split("|")

    def set_last_grade_hashes(self, user_id: int, hashes: list[str]):
        self.ensure_user(user_id)

        value = "|".join(hashes[:3])

        with self.connection:
            self.cursor.execute(
                """
                INSERT INTO grades_state(user_id, last_hash, updated_at)
                VALUES (?, ?, strftime('%s','now'))
                ON CONFLICT(user_id)
                DO UPDATE SET
                    last_hash=excluded.last_hash,
                    updated_at=excluded.updated_at
                """,
                (user_id, value)
            )

    # ===== Referrals =====

    def get_referrer(self, user_id: int):
        with self.connection:
            row = self.cursor.execute(
                "SELECT referrer_id FROM users WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return row[0] if row else None

    def set_referrer(self, user_id: int, referrer_id: int) -> bool:
        """
        Ставит referrer_id только если ещё не установлен и не self-ref.
        Возвращает True, если поле обновлено.
        """
        if referrer_id == user_id:
            return False
        self.ensure_user(user_id)
        self.ensure_user(referrer_id)
        with self.connection:
            self.cursor.execute(
                "UPDATE users SET referrer_id=? WHERE user_id=? AND referrer_id IS NULL",
                (referrer_id, user_id)
            )
            return self.cursor.rowcount > 0

    # ===== Broadcast =====

    def get_all_users(self):
        with self.connection:
            return self.cursor.execute("SELECT user_id FROM users").fetchall()

    # ===== Activity (commands per day/week) =====

    def add_activity(self, user_id: int):
        """
        Инкрементирует количество действий пользователя за сегодняшний день.
        Используем day = date.toordinal(), чтобы удобно считать "последние 7 дней".
        """
        self.ensure_user(user_id)
        today = datetime.date.today().toordinal()
        with self.connection:
            self.cursor.execute(
                """
                INSERT INTO activity(user_id, day, actions)
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, day) DO UPDATE SET actions = actions + 1
                """,
                (user_id, today)
            )

    def get_activity_summary(self, days: int = 7):
        today_date = datetime.date.today()
        today_ordinal = today_date.toordinal()

        # Для активності (Activity)
        min_day = today_ordinal - (days - 1)

        # Для нових юзерів (New Users) - ТЕПЕР ПО КАЛЕНДАРЮ (00:00:00)
        # Це виправить розбіжність з графіком
        start_date = today_date - datetime.timedelta(days=days - 1)
        start_of_period = int(datetime.datetime.combine(start_date, datetime.time.min).timestamp())

        # Границі сьогоднішнього дня для new_today
        start_of_today = int(datetime.datetime.combine(today_date, datetime.time.min).timestamp())
        end_of_today = start_of_today + 24 * 60 * 60
        now_ts = int(datetime.datetime.now().timestamp())

        with self.connection:
            # Основний запит активності
            rows = self.cursor.execute(
                """
                SELECT u.user_id,
                       COALESCE(SUM(a.actions), 0) AS total_actions,
                       s.vip,
                       s.expires
                FROM users u
                LEFT JOIN activity a ON a.user_id = u.user_id AND a.day >= ?
                LEFT JOIN subs s ON s.user_id = u.user_id
                GROUP BY u.user_id
                """,
                (min_day,)
            ).fetchall()

            total_creds = self.cursor.execute("SELECT COUNT(*) FROM creds").fetchone()[0]

            new_today = self.cursor.execute(
                "SELECT COUNT(*) FROM users WHERE created_at >= ? AND created_at < ?",
                (start_of_today, end_of_today)
            ).fetchone()[0]

            new_days = self.cursor.execute(
                "SELECT COUNT(*) FROM users WHERE created_at >= ?",
                (start_of_period,)
            ).fetchone()[0]

        inactive = low_active = active = very_active = valuable = 0

        for _, total_actions, vip, expires in rows:
            total_actions = total_actions or 0
            vip = int(vip or 0)
            expires = int(expires or 0)
            is_vip = bool(vip) and (expires == 0 or expires > now_ts)

            if total_actions == 0:
                inactive += 1
            elif total_actions <= 2:
                low_active += 1
            elif total_actions <= 6:
                active += 1
            else:
                if is_vip:
                    valuable += 1
                else:
                    very_active += 1

        return {
            "total": len(rows),
            "total_creds": total_creds,
            "new_today": new_today,
            "new_days": new_days,
            "inactive": inactive,
            "low_active": low_active,
            "active": active,
            "very_active": very_active,
            "valuable": valuable,
        }

    def get_daily_growth(self, days: int = 7):
        # 1. Рахуємо timestamp початку періоду
        start_date = datetime.date.today() - datetime.timedelta(days=days - 1)
        start_ts = int(datetime.datetime.combine(start_date, datetime.time.min).timestamp())

        with self.connection:
            # 2. SQL: Групуємо по даті (YYYY-MM-DD)
            # Використовуємо sqlite модифікатор 'unixepoch', щоб перетворити число в дату
            rows = self.cursor.execute(
                """
                SELECT date(created_at, 'unixepoch', 'localtime') as day_date, 
                       COUNT(*) as cnt
                FROM users
                WHERE created_at >= ?
                GROUP BY day_date
                ORDER BY day_date ASC
                """,
                (start_ts,)
            ).fetchall()

        # 3. Перетворюємо результат SQL у словник: {'2024-01-01': 5, '2024-01-03': 2}
        data_map = {r[0]: r[1] for r in rows}

        # 4. Формуємо красиві списки для графіка (Labels і Data)
        # Нам треба пройтися по кожному дню, навіть якщо там було 0 юзерів
        labels = []
        counts = []

        current_date = start_date
        today = datetime.date.today()

        while current_date <= today:
            date_str = current_date.strftime("%Y-%m-%d")  # Ключ для пошуку
            label_str = current_date.strftime("%d.%m")  # Красива дата для графіка (03.01)

            labels.append(label_str)
            counts.append(data_map.get(date_str, 0))  # Якщо дати немає в базі, ставимо 0

            current_date += datetime.timedelta(days=1)

        return labels, counts
