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

    # ===== Utility =====

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
                "SELECT login, password FROM creds WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return row if row else (None, None)

    def add_user(self, user_id: int, login: str, password: str):
        self.ensure_user(user_id)
        with self.connection:
            self.cursor.execute(
                """
                INSERT INTO creds(user_id, login, password, updated_at)
                VALUES (?, ?, ?, strftime('%s','now'))
                ON CONFLICT(user_id) DO UPDATE SET
                    login=excluded.login,
                    password=excluded.password,
                    updated_at=excluded.updated_at
                """,
                (user_id, login, password)
            )

    def delete_user(self, user_id: int):
        # logout: удаляем только логин/пароль, VIP не трогаем
        with self.connection:
            self.cursor.execute("DELETE FROM creds WHERE user_id=?", (user_id,))

    # ===== VIP =====

    def set_vip(self, user_id: int, days: int = 30):
        self.ensure_user(user_id)
        now = int(datetime.datetime.now().timestamp())
        add_sec = int(datetime.timedelta(days=days).total_seconds())

        with self.connection:
            row = self.cursor.execute(
                "SELECT vip, expires FROM subs WHERE user_id=?",
                (user_id,)
            ).fetchone()
            current_vip, current_expires = (int(row[0] or 0), int(row[1] or 0)) if row else (0, 0)

            if current_vip and current_expires > now:
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
                SELECT u.user_id, c.login, c.password
                FROM subs s
                JOIN users u ON u.user_id=s.user_id
                LEFT JOIN creds c ON c.user_id=u.user_id
                WHERE s.notify=1
                """
            ).fetchall()

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
                "UPDATE users SET referrer_id=? WHERE user_id=? AND (referrer_id IS NULL OR referrer_id='')",
                (referrer_id, user_id)
            )
            return self.cursor.rowcount > 0

    def get_invites(self, user_id: int) -> int:
        with self.connection:
            row = self.cursor.execute(
                "SELECT invites FROM users WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return int(row[0] or 0) if row else 0

    def add_invite(self, user_id: int, delta: int = 1):
        """
        Увеличивает invites на delta. total_invites увеличивает только при delta>0.
        Можно передавать отрицательное delta (например, -5 для обнуления цикла 5/5).
        """
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

    # ===== Broadcast =====

    def get_all_users(self):
        with self.connection:
            return self.cursor.execute("SELECT user_id FROM users").fetchall()
