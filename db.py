import sqlite3
import datetime
import threading


class _LockedConnection:
    """Обгортка над sqlite3.Connection: серіалізує доступ із різних потоків
    (хендлери в event loop + виклики через asyncio.to_thread у фонових задачах).
    RLock — щоб execute() всередині блоку `with connection:` не дедлочився."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.RLock()

    def __enter__(self):
        self._lock.acquire()
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            return self._conn.__exit__(exc_type, exc, tb)
        finally:
            self._lock.release()

    def execute(self, *args, **kwargs):
        with self._lock:
            return self._conn.execute(*args, **kwargs)

    def backup(self, target, **kwargs):
        with self._lock:
            return self._conn.backup(target, **kwargs)

    def close(self):
        with self._lock:
            return self._conn.close()


class DataBase:
    def __init__(self, db_file):
        self.connection = _LockedConnection(
            sqlite3.connect(db_file, check_same_thread=False)
        )
        self._configure_connection()
        self._init_schema()

    def _configure_connection(self):
        with self.connection:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=NORMAL")
            self.connection.execute("PRAGMA busy_timeout=5000")
            self.connection.execute("PRAGMA foreign_keys=ON")

    def _init_schema(self):
        with self.connection:
            # Основная информация о пользователе (рефералы, приглашения)
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS users (
                  user_id        INTEGER PRIMARY KEY,
                  referrer_id    INTEGER,
                  invites        INTEGER NOT NULL DEFAULT 0,
                  total_invites  INTEGER NOT NULL DEFAULT 0,
                  created_at     INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                )
            """)

            # Логин и пароль NZ
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS creds (
                  user_id    INTEGER PRIMARY KEY,
                  login      TEXT,
                  password   TEXT,
                  updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                  FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # VIP, срок, нотификации
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS subs (
                  user_id  INTEGER PRIMARY KEY,
                  vip      INTEGER NOT NULL DEFAULT 0,
                  expires  INTEGER NOT NULL DEFAULT 0,
                  notify   INTEGER NOT NULL DEFAULT 0,
                  FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            # Таблица активності користувачів по днях
            self.connection.execute("""
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
                self.connection.execute("ALTER TABLE subs ADD COLUMN notify_grades INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass

            # хто заблокував бота — виключаємо з розсилок
            try:
                self.connection.execute("ALTER TABLE users ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass

            try:
                self.connection.execute("ALTER TABLE creds ADD COLUMN verified INTEGER NOT NULL DEFAULT 0;")
                self.connection.execute("ALTER TABLE creds ADD COLUMN verified_at INTEGER;")
            except Exception:
                pass

            try:
                self.connection.execute("ALTER TABLE creds ADD COLUMN provider TEXT NOT NULL DEFAULT 'nz';")
            except Exception:
                pass

                # на всякий: старые строки могли остаться NULL
            try:
                self.connection.execute("UPDATE creds SET provider='nz' WHERE provider IS NULL OR provider='';")
            except Exception:
                pass
            # last sent grade hash per user
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS grades_state (
                  user_id INTEGER PRIMARY KEY,
                  last_hash TEXT,
                  updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                  FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS ref_rewarded (
                  user_id     INTEGER PRIMARY KEY,      -- тот, кого пригласили
                  referrer_id INTEGER NOT NULL,         -- кто пригласил
                  rewarded_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                )
            """)

            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                  user_id    INTEGER NOT NULL,
                  provider   TEXT NOT NULL,
                  cookies    TEXT NOT NULL,
                  updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                  PRIMARY KEY(user_id, provider),
                  FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS command_metrics (
                  day        INTEGER NOT NULL,
                  command    TEXT NOT NULL,
                  calls      INTEGER NOT NULL DEFAULT 0,
                  errors     INTEGER NOT NULL DEFAULT 0,
                  total_ms   INTEGER NOT NULL DEFAULT 0,
                  max_ms     INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY(day, command)
                )
            """)

            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS nz_session_metrics (
                  day     INTEGER NOT NULL,
                  event   TEXT NOT NULL,
                  count   INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY(day, event)
                )
            """)

            try:
                self.connection.execute("ALTER TABLE subs ADD COLUMN tokens INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass

            # FSM-стани aiogram (переживають рестарт бота)
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS fsm_storage (
                  key        TEXT PRIMARY KEY,
                  state      TEXT,
                  data       TEXT NOT NULL DEFAULT '{}',
                  updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                )
            """)

            # Монетизація: тріал, джерело VIP (paid/ref), воронка закінчення,
            # тижневий безкоштовний ліміт ШІ
            for ddl in (
                "ALTER TABLE subs ADD COLUMN trial_used INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE subs ADD COLUMN vip_source TEXT NOT NULL DEFAULT 'paid'",
                "ALTER TABLE subs ADD COLUMN expiry_stage INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE subs ADD COLUMN ai_free_used INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE subs ADD COLUMN ai_free_week INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE subs ADD COLUMN notify_homework INTEGER NOT NULL DEFAULT 0",
                # дайджест — opt-in: за замовчуванням вимкнений, щоб не спамити
                "ALTER TABLE subs ADD COLUMN notify_digest INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE subs ADD COLUMN channel_bonus_used INTEGER NOT NULL DEFAULT 0",
            ):
                try:
                    self.connection.execute(ddl)
                except Exception:
                    pass

            # Хеші вже надісланих ДЗ (щоб пушити тільки нові)
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS homework_state (
                  user_id    INTEGER PRIMARY KEY,
                  hashes     TEXT NOT NULL DEFAULT '',
                  updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                  FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # Видані реферальні нагороди (для місячного ліміту безкоштовного VIP)
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS ref_vip_grants (
                  referrer_id INTEGER NOT NULL,
                  granted_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                )
            """)

    # ===== FSM storage (aiogram) =====

    def fsm_set_state(self, key: str, state: str | None):
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO fsm_storage(key, state, updated_at)
                VALUES (?, ?, strftime('%s','now'))
                ON CONFLICT(key) DO UPDATE SET
                    state=excluded.state,
                    updated_at=excluded.updated_at
                """,
                (key, state)
            )

    def fsm_get_state(self, key: str) -> str | None:
        with self.connection:
            row = self.connection.execute(
                "SELECT state FROM fsm_storage WHERE key=?", (key,)
            ).fetchone()
            return row[0] if row else None

    def fsm_set_data(self, key: str, data_json: str):
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO fsm_storage(key, data, updated_at)
                VALUES (?, ?, strftime('%s','now'))
                ON CONFLICT(key) DO UPDATE SET
                    data=excluded.data,
                    updated_at=excluded.updated_at
                """,
                (key, data_json)
            )

    def fsm_get_data(self, key: str) -> str:
        with self.connection:
            row = self.connection.execute(
                "SELECT data FROM fsm_storage WHERE key=?", (key,)
            ).fetchone()
            return row[0] if row and row[0] else "{}"

    def fsm_purge_older_than(self, seconds: int = 48 * 3600):
        """Видаляє «завислі» стани, які не оновлювались довше seconds."""
        cutoff = int(datetime.datetime.now().timestamp()) - seconds
        with self.connection:
            self.connection.execute(
                "DELETE FROM fsm_storage WHERE updated_at < ?", (cutoff,)
            )

    # ===== Maintenance =====

    def mark_blocked(self, user_id: int):
        """Юзер заблокував бота: більше не шлемо йому нічого і не скрапимо для нього.
        Знімається автоматично в ensure_user, коли він повернеться."""
        with self.connection:
            self.connection.execute("UPDATE users SET blocked=1 WHERE user_id=?", (user_id,))
            self.connection.execute(
                "UPDATE subs SET notify=0, notify_grades=0, notify_homework=0, notify_digest=0 "
                "WHERE user_id=?",
                (user_id,)
            )

    def backup_to(self, dest_path: str):
        """Консистентна копія бази через sqlite backup API (працює і під WAL)."""
        dest = sqlite3.connect(dest_path)
        try:
            self.connection.backup(dest)
        finally:
            dest.close()

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
            rows = self.connection.execute(
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
            self.connection.execute("UPDATE subs SET tokens=? WHERE user_id=?", (amount, user_id))

    def get_tokens(self, user_id: int) -> int:
        """Повертає баланс токенів користувача."""
        with self.connection:
            row = self.connection.execute("SELECT tokens FROM subs WHERE user_id=?", (user_id,)).fetchone()
            return int(row[0] or 0) if row else 0

    def deduct_tokens(self, user_id: int, amount: int):
        """Віднімає токени. Не дозволяє опуститися нижче 0."""
        with self.connection:
            self.connection.execute(
                "UPDATE subs SET tokens = MAX(0, tokens - ?) WHERE user_id=?",
                (amount, user_id)
            )

    def set_session_cookies(self, user_id: int, provider: str, cookies: str):
        self.ensure_user(user_id)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO sessions(user_id, provider, cookies, updated_at)
                VALUES (?, ?, ?, strftime('%s','now'))
                ON CONFLICT(user_id, provider) DO UPDATE SET
                    cookies=excluded.cookies,
                    updated_at=excluded.updated_at
                """,
                (user_id, provider, cookies)
            )

    def get_session_cookies(self, user_id: int, provider: str) -> str | None:
        with self.connection:
            row = self.connection.execute(
                "SELECT cookies FROM sessions WHERE user_id=? AND provider=?",
                (user_id, provider)
            ).fetchone()
            return row[0] if row else None

    def delete_session_cookies(self, user_id: int, provider: str | None = None):
        with self.connection:
            if provider:
                self.connection.execute(
                    "DELETE FROM sessions WHERE user_id=? AND provider=?",
                    (user_id, provider)
                )
            else:
                self.connection.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def record_command_metric(self, command: str, duration_ms: int, ok: bool = True):
        day = datetime.date.today().toordinal()
        command = (command or "unknown")[:80]
        duration_ms = max(0, int(duration_ms or 0))
        errors = 0 if ok else 1
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO command_metrics(day, command, calls, errors, total_ms, max_ms)
                VALUES (?, ?, 1, ?, ?, ?)
                ON CONFLICT(day, command) DO UPDATE SET
                    calls=command_metrics.calls + 1,
                    errors=command_metrics.errors + excluded.errors,
                    total_ms=command_metrics.total_ms + excluded.total_ms,
                    max_ms=MAX(command_metrics.max_ms, excluded.max_ms)
                """,
                (day, command, errors, duration_ms, duration_ms)
            )

    def get_command_metrics(self, days: int = 7, limit: int = 12):
        min_day = datetime.date.today().toordinal() - (days - 1)
        with self.connection:
            rows = self.connection.execute(
                """
                SELECT command,
                       SUM(calls) as calls,
                       SUM(errors) as errors,
                       SUM(total_ms) as total_ms,
                       MAX(max_ms) as max_ms
                FROM command_metrics
                WHERE day >= ?
                GROUP BY command
                ORDER BY calls DESC
                LIMIT ?
                """,
                (min_day, limit)
            ).fetchall()
        return [
            {
                "command": row[0],
                "calls": int(row[1] or 0),
                "errors": int(row[2] or 0),
                "avg_ms": int((row[3] or 0) / row[1]) if row[1] else 0,
                "max_ms": int(row[4] or 0),
            }
            for row in rows
        ]

    def record_nz_session_event(self, event: str):
        day = datetime.date.today().toordinal()
        event = (event or "unknown")[:40]
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO nz_session_metrics(day, event, count)
                VALUES (?, ?, 1)
                ON CONFLICT(day, event) DO UPDATE SET count=count + 1
                """,
                (day, event)
            )

    def get_nz_session_metrics(self, days: int = 7):
        min_day = datetime.date.today().toordinal() - (days - 1)
        with self.connection:
            rows = self.connection.execute(
                """
                SELECT event, SUM(count)
                FROM nz_session_metrics
                WHERE day >= ?
                GROUP BY event
                """,
                (min_day,)
            ).fetchall()
        return {row[0]: int(row[1] or 0) for row in rows}

    def try_mark_ref_rewarded(self, user_id: int) -> bool:
        """
        Возвращает True только первый раз.
        Дальше False (значит уже засчитали).
        """
        with self.connection:
            cur = self.connection.execute(
                """
                INSERT OR IGNORE INTO ref_rewarded(user_id, referrer_id)
                SELECT user_id, referrer_id FROM users WHERE user_id=?
                """,
                (user_id,)
            )
            return cur.rowcount == 1

    def get_referrer_for_reward(self, user_id: int) -> int | None:
        with self.connection:
            row = self.connection.execute(
                "SELECT referrer_id FROM users WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return int(row[0]) if row and row[0] else None

    def add_invite_and_get(self, user_id: int, delta: int = 1) -> int:
        self.ensure_user(user_id)
        with self.connection:
            self.connection.execute(
                "UPDATE users SET invites = invites + ? WHERE user_id=?",
                (delta, user_id)
            )
            if delta > 0:
                self.connection.execute(
                    "UPDATE users SET total_invites = total_invites + ? WHERE user_id=?",
                    (delta, user_id)
                )
            row = self.connection.execute(
                "SELECT invites FROM users WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return int(row[0] or 0) if row else 0

    def get_invite_progress(self, user_id: int):
        self.ensure_user(user_id)
        with self.connection:
            row = self.connection.execute(
                "SELECT invites, total_invites FROM users WHERE user_id=?",
                (user_id,)
            ).fetchone()
        if not row:
            return 0, 0
        return int(row[0] or 0), int(row[1] or 0)

    def try_consume_invites(self, user_id: int, need: int = 3) -> bool:
        """
        Атомарно списывает need, только если invites >= need.
        Возвращает True если списало.
        """
        self.ensure_user(user_id)
        with self.connection:
            cur = self.connection.execute(
                "UPDATE users SET invites = invites - ? WHERE user_id=? AND invites >= ?",
                (need, user_id, need)
            )
            return cur.rowcount == 1

    def ensure_user(self, user_id: int):
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO users(user_id) VALUES (?)", (user_id,))
            self.connection.execute("INSERT OR IGNORE INTO subs(user_id) VALUES (?)", (user_id,))
            # юзер щось написав боту — отже, розблокував
            self.connection.execute(
                "UPDATE users SET blocked=0 WHERE user_id=? AND blocked=1", (user_id,)
            )

    # ===== Credentials (login/password) =====

    def has_credentials(self, user_id: int) -> bool:
        with self.connection:
            row = self.connection.execute(
                "SELECT login, password FROM creds WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return bool(row and row[0] and row[1])

    def set_creds_verified(self, user_id: int, val: int = 1):
        with self.connection:
            self.connection.execute(
                "UPDATE creds SET verified=?, verified_at=strftime('%s','now') WHERE user_id=?",
                (val, user_id)
            )

    def is_creds_verified(self, user_id: int) -> bool:
        with self.connection:
            row = self.connection.execute(
                "SELECT verified FROM creds WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return bool(int(row[0])) if row else False

    def count_verified_by_provider(self, provider: str) -> int:
        with self.connection:
            row = self.connection.execute(
                "SELECT COUNT(*) FROM creds WHERE verified=1 AND provider=?",
                (provider,)
            ).fetchone()
            return row[0] if row else 0

    def user_exists(self, user_id: int) -> bool:
        with self.connection:
            row = self.connection.execute(
                "SELECT 1 FROM users WHERE user_id=? LIMIT 1",
                (user_id,)
            ).fetchone()
            return row is not None

    def get_user(self, user_id: int):
        with self.connection:
            row = self.connection.execute(
                "SELECT login, password, COALESCE(provider,'nz') FROM creds WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return row if row else (None, None, "nz")

    def add_user(self, user_id: int, login: str, password: str, provider: str = "nz"):
        self.ensure_user(user_id)
        provider = provider if provider in ("nz", "human") else "nz"
        with self.connection:
            self.connection.execute(
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
            self.connection.execute("DELETE FROM creds WHERE user_id=?", (user_id,))
            self.connection.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    # ===== VIP =====

    def set_vip(self, user_id: int, days: int = 30, source: str = "paid"):
        self.ensure_user(user_id)
        now = int(datetime.datetime.now().timestamp())

        with self.connection:
            row = self.connection.execute(
                "SELECT vip, expires, COALESCE(vip_source,'paid') FROM subs WHERE user_id=?",
                (user_id,)
            ).fetchone()
            # Перевіряємо поточний статус
            current_vip = int(row[0] or 0) if row else 0
            current_expires = int(row[1] or 0) if row else 0
            current_source = row[2] if row else "paid"

            # Якщо днів 0 — це означає назавжди
            if days == 0:
                new_expires = 0
            else:
                add_sec = int(datetime.timedelta(days=days).total_seconds())

                # Логіка додавання часу:
                # 1. Якщо юзер вже VIP і час ще не вийшов (і це не безліміт) — додаємо до залишку.
                # 2. Якщо у юзера "назавжди" (0), додавання днів ігнорується, він лишається вічним.
                if current_vip and current_expires == 0:
                    new_expires = 0  # Вже вічний, лишаємо вічним
                elif current_vip and current_expires > now:
                    new_expires = current_expires + add_sec
                else:
                    new_expires = now + add_sec

            # Реферальні дні не підвищують статус до 'paid'
            # і не понижують активний платний VIP до 'ref'
            active = current_vip and (current_expires == 0 or current_expires > now)
            if source == "paid" or (active and current_source == "paid"):
                new_source = "paid"
            else:
                new_source = "ref"

            self.connection.execute(
                """
                INSERT INTO subs(user_id, vip, expires, vip_source, expiry_stage)
                VALUES (?, 1, ?, ?, 0)
                ON CONFLICT(user_id) DO UPDATE SET
                    vip=1,
                    expires=excluded.expires,
                    vip_source=excluded.vip_source,
                    expiry_stage=0
                """,
                (user_id, new_expires, new_source)
            )

    def get_vip_status(self, user_id: int):
        with self.connection:
            row = self.connection.execute(
                "SELECT vip, expires FROM subs WHERE user_id=?",
                (user_id,)
            ).fetchone()
            if not row:
                return False, 0
            vip, expires = int(row[0] or 0), int(row[1] or 0)
            return bool(vip), expires

    def get_vip_source(self, user_id: int) -> str:
        """'paid' — куплений/тріал/адмінський, 'ref' — за запрошення друзів."""
        with self.connection:
            row = self.connection.execute(
                "SELECT COALESCE(vip_source,'paid') FROM subs WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return row[0] if row else "paid"

    def try_use_channel_bonus(self, user_id: int) -> bool:
        """True тільки один раз — бонус за підписку на канал видається раз."""
        self.ensure_user(user_id)
        with self.connection:
            cur = self.connection.execute(
                "UPDATE subs SET channel_bonus_used=1 WHERE user_id=? AND channel_bonus_used=0",
                (user_id,)
            )
            return cur.rowcount == 1

    def try_use_trial(self, user_id: int) -> bool:
        """True тільки один раз — коли юзер ще не використовував пробний VIP."""
        self.ensure_user(user_id)
        with self.connection:
            cur = self.connection.execute(
                "UPDATE subs SET trial_used=1 WHERE user_id=? AND trial_used=0",
                (user_id,)
            )
            return cur.rowcount == 1

    # ===== Безкоштовний тижневий ліміт ШІ (для не-VIP) =====

    @staticmethod
    def _current_ai_week() -> int:
        return datetime.date.today().toordinal() // 7

    def free_ai_left(self, user_id: int, limit: int = 3) -> int:
        self.ensure_user(user_id)
        week = self._current_ai_week()
        with self.connection:
            # новий тиждень — скидаємо лічильник
            self.connection.execute(
                "UPDATE subs SET ai_free_used=0, ai_free_week=? WHERE user_id=? AND ai_free_week<>?",
                (week, user_id, week)
            )
            row = self.connection.execute(
                "SELECT ai_free_used FROM subs WHERE user_id=?", (user_id,)
            ).fetchone()
            used = int(row[0] or 0) if row else 0
            return max(0, limit - used)

    def try_use_free_ai(self, user_id: int, limit: int = 3) -> bool:
        """Атомарно списує 1 безкоштовний AI-запит тижня. True — якщо вдалося."""
        self.ensure_user(user_id)
        week = self._current_ai_week()
        with self.connection:
            self.connection.execute(
                "UPDATE subs SET ai_free_used=0, ai_free_week=? WHERE user_id=? AND ai_free_week<>?",
                (week, user_id, week)
            )
            cur = self.connection.execute(
                "UPDATE subs SET ai_free_used=ai_free_used+1 WHERE user_id=? AND ai_free_used<?",
                (user_id, limit)
            )
            return cur.rowcount == 1

    def refund_free_ai(self, user_id: int):
        """Повертає списаний безкоштовний запит, якщо ШІ впав не з вини юзера."""
        with self.connection:
            self.connection.execute(
                "UPDATE subs SET ai_free_used = MAX(0, ai_free_used - 1) WHERE user_id=?",
                (user_id,)
            )

    # ===== Ліміт реферальних нагород =====

    def add_ref_grant(self, referrer_id: int):
        with self.connection:
            self.connection.execute(
                "INSERT INTO ref_vip_grants(referrer_id) VALUES (?)",
                (referrer_id,)
            )

    def count_recent_ref_grants(self, referrer_id: int, days: int = 30) -> int:
        cutoff = int(datetime.datetime.now().timestamp()) - days * 24 * 3600
        with self.connection:
            row = self.connection.execute(
                "SELECT COUNT(*) FROM ref_vip_grants WHERE referrer_id=? AND granted_at>=?",
                (referrer_id, cutoff)
            ).fetchone()
            return int(row[0] or 0)

    # ===== Воронка закінчення VIP =====

    def get_vips_expiring_within(self, seconds: int):
        """VIP, що закінчуються протягом seconds і ще не отримали нагадування."""
        now = int(datetime.datetime.now().timestamp())
        with self.connection:
            return self.connection.execute(
                """
                SELECT user_id, expires FROM subs
                WHERE vip=1 AND expires>? AND expires<=? AND expiry_stage=0
                """,
                (now, now + seconds)
            ).fetchall()

    def get_vips_just_expired(self, grace_seconds: int):
        """VIP, що закінчилися нещодавно і ще не отримали win-back пропозицію."""
        now = int(datetime.datetime.now().timestamp())
        with self.connection:
            return self.connection.execute(
                """
                SELECT user_id, expires FROM subs
                WHERE vip=1 AND expires>0 AND expires<=? AND expires>? AND expiry_stage<2
                """,
                (now, now - grace_seconds)
            ).fetchall()

    def set_expiry_stage(self, user_id: int, stage: int):
        with self.connection:
            self.connection.execute(
                "UPDATE subs SET expiry_stage=? WHERE user_id=?",
                (stage, user_id)
            )

    # ===== Notify =====

    def toggle_notify(self, user_id: int) -> bool:
        self.ensure_user(user_id)
        with self.connection:
            row = self.connection.execute(
                "SELECT notify FROM subs WHERE user_id=?",
                (user_id,)
            ).fetchone()
            current = int(row[0] or 0) if row else 0
            new_val = 0 if current == 1 else 1
            self.connection.execute(
                """
                INSERT INTO subs(user_id, notify) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET notify=excluded.notify
                """,
                (user_id, new_val)
            )
            return bool(new_val)

    def user_notify(self, user_id: int) -> bool:
        with self.connection:
            row = self.connection.execute(
                "SELECT notify FROM subs WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return bool(int(row[0] or 0)) if row else False

    def get_users_with_notify(self):
        with self.connection:
            return self.connection.execute(
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
            row = self.connection.execute(
                "SELECT notify_grades FROM subs WHERE user_id=?",
                (user_id,)
            ).fetchone()
            current = int(row[0] or 0) if row else 0
            new_val = 0 if current == 1 else 1
            self.connection.execute(
                """
                INSERT INTO subs(user_id, notify_grades) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET notify_grades=excluded.notify_grades
                """,
                (user_id, new_val)
            )
        return bool(new_val)

    def user_notify_grades(self, user_id: int) -> bool:
        with self.connection:
            row = self.connection.execute(
                "SELECT notify_grades FROM subs WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return bool(int(row[0] or 0)) if row else False

    def get_users_with_grades_notify(self):
        with self.connection:
            return self.connection.execute(
                """
                SELECT u.user_id, c.login, c.password, COALESCE(c.provider,'nz')
                FROM subs s
                JOIN users u ON u.user_id=s.user_id
                LEFT JOIN creds c ON c.user_id=u.user_id
                WHERE s.notify_grades=1
                """
            ).fetchall()

    def toggle_notify_homework(self, user_id: int) -> bool:
        self.ensure_user(user_id)
        with self.connection:
            row = self.connection.execute(
                "SELECT notify_homework FROM subs WHERE user_id=?",
                (user_id,)
            ).fetchone()
            current = int(row[0] or 0) if row else 0
            new_val = 0 if current == 1 else 1
            self.connection.execute(
                """
                INSERT INTO subs(user_id, notify_homework) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET notify_homework=excluded.notify_homework
                """,
                (user_id, new_val)
            )
        return bool(new_val)

    def user_notify_homework(self, user_id: int) -> bool:
        with self.connection:
            row = self.connection.execute(
                "SELECT notify_homework FROM subs WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return bool(int(row[0] or 0)) if row else False

    def get_users_with_homework_notify(self):
        with self.connection:
            return self.connection.execute(
                """
                SELECT u.user_id, c.login, c.password, COALESCE(c.provider,'nz')
                FROM subs s
                JOIN users u ON u.user_id=s.user_id
                LEFT JOIN creds c ON c.user_id=u.user_id
                WHERE s.notify_homework=1
                """
            ).fetchall()

    def get_homework_hashes(self, user_id: int) -> list[str]:
        with self.connection:
            row = self.connection.execute(
                "SELECT hashes FROM homework_state WHERE user_id=?",
                (user_id,)
            ).fetchone()
            if not row or not row[0]:
                return []
            return row[0].split("|")

    def set_homework_hashes(self, user_id: int, hashes: list[str], keep: int = 80):
        """Тримаємо вікно останніх хешів: ДЗ на день може бути ~10 позицій."""
        self.ensure_user(user_id)
        value = "|".join(hashes[:keep])
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO homework_state(user_id, hashes, updated_at)
                VALUES (?, ?, strftime('%s','now'))
                ON CONFLICT(user_id) DO UPDATE SET
                    hashes=excluded.hashes,
                    updated_at=excluded.updated_at
                """,
                (user_id, value)
            )

    # ===== Ранковий дайджест =====

    def toggle_notify_digest(self, user_id: int) -> bool:
        self.ensure_user(user_id)
        with self.connection:
            row = self.connection.execute(
                "SELECT notify_digest FROM subs WHERE user_id=?", (user_id,)
            ).fetchone()
            new_val = 0 if (int(row[0] or 0) if row else 0) == 1 else 1
            self.connection.execute(
                """
                INSERT INTO subs(user_id, notify_digest) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET notify_digest=excluded.notify_digest
                """,
                (user_id, new_val)
            )
        return bool(new_val)

    def user_notify_digest(self, user_id: int) -> bool:
        with self.connection:
            row = self.connection.execute(
                "SELECT notify_digest FROM subs WHERE user_id=?", (user_id,)
            ).fetchone()
            return bool(int(row[0] or 0)) if row else False

    def get_digest_recipients(self, active_days: int = 14):
        """Хто підписаний на дайджест, має підтверджені креди і був активний
        за останні active_days. Фільтр по активності не дає скрапити мертві
        акаунти щоранку. Повертає (user_id, login, password, provider, is_vip)."""
        min_day = datetime.date.today().toordinal() - active_days
        now_ts = int(datetime.datetime.now().timestamp())
        with self.connection:
            rows = self.connection.execute(
                """
                SELECT c.user_id, c.login, c.password, COALESCE(c.provider,'nz'),
                       COALESCE(s.vip,0), COALESCE(s.expires,0)
                FROM creds c
                LEFT JOIN subs s ON s.user_id=c.user_id
                WHERE c.login IS NOT NULL AND c.password IS NOT NULL
                  AND c.verified=1
                  AND COALESCE(s.notify_digest,0)=1
                  AND EXISTS (
                      SELECT 1 FROM activity a
                      WHERE a.user_id=c.user_id AND a.day >= ?
                  )
                """,
                (min_day,)
            ).fetchall()

        out = []
        for user_id, login, password, provider, vip, expires in rows:
            is_vip = bool(vip) and (int(expires or 0) == 0 or int(expires or 0) > now_ts)
            out.append((user_id, login, password, provider, is_vip))
        return out

    def get_last_grade_hashes(self, user_id: int) -> list[str]:
        with self.connection:
            row = self.connection.execute(
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
            self.connection.execute(
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
            row = self.connection.execute(
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
            cur = self.connection.execute(
                "UPDATE users SET referrer_id=? WHERE user_id=? AND referrer_id IS NULL",
                (referrer_id, user_id)
            )
            return cur.rowcount > 0

    # ===== Broadcast =====

    def get_all_users(self):
        with self.connection:
            return self.connection.execute(
                "SELECT user_id FROM users WHERE blocked=0"
            ).fetchall()

    def get_non_logged_users(self):
        """
        Повертає список (user_id,) користувачів, яких немає в таблиці creds
        (тобто вони натиснули /start, але не увійшли в акаунт).
        """
        with self.connection:
            return self.connection.execute("""
                SELECT u.user_id
                FROM users u
                LEFT JOIN creds c ON u.user_id = c.user_id
                WHERE c.user_id IS NULL AND u.blocked=0
            """).fetchall()

    def count_blocked(self) -> int:
        with self.connection:
            return self.connection.execute(
                "SELECT COUNT(*) FROM users WHERE blocked=1"
            ).fetchone()[0]

    # ===== Activity (commands per day/week) =====

    def add_activity(self, user_id: int):
        """
        Инкрементирует количество действий пользователя за сегодняшний день.
        Используем day = date.toordinal(), чтобы удобно считать "последние 7 дней".
        """
        self.ensure_user(user_id)
        today = datetime.date.today().toordinal()
        with self.connection:
            self.connection.execute(
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
            rows = self.connection.execute(
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

            total_creds = self.connection.execute("SELECT COUNT(*) FROM creds").fetchone()[0]

            new_today = self.connection.execute(
                "SELECT COUNT(*) FROM users WHERE created_at >= ? AND created_at < ?",
                (start_of_today, end_of_today)
            ).fetchone()[0]

            new_days = self.connection.execute(
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
            rows = self.connection.execute(
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
