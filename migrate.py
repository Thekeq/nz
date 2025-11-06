#!/usr/bin/env python3
import os
import sys
import shutil
import sqlite3
from datetime import datetime


def table_exists(cur, name: str) -> bool:
    row = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (name,)).fetchone()
    return row is not None


def col_exists(cur, table: str, col: str) -> bool:
    cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table});").fetchall()]
    return col in cols


def backup_db(db_path: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.bak_{ts}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def main():
    # DB path from argv or default data.db in CWD
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data.db"
    if not os.path.exists(db_path):
        print(f"[ERROR] DB file not found: {db_path}")
        sys.exit(1)

    print(f"[INFO] Using DB: {db_path}")
    backup_path = backup_db(db_path)
    print(f"[INFO] Backup created: {backup_path}")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    try:
        cur.execute("PRAGMA foreign_keys=OFF;")
        cur.execute("BEGIN TRANSACTION;")

        # 1) New tables
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users_new (
          user_id        INTEGER PRIMARY KEY,
          referrer_id    INTEGER,
          invites        INTEGER NOT NULL DEFAULT 0,
          total_invites  INTEGER NOT NULL DEFAULT 0,
          created_at     INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS creds (
          user_id    INTEGER PRIMARY KEY,
          login      TEXT,
          password   TEXT,
          updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
          FOREIGN KEY(user_id) REFERENCES users_new(user_id) ON DELETE CASCADE
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS subs (
          user_id  INTEGER PRIMARY KEY,
          vip      INTEGER NOT NULL DEFAULT 0,
          expires  INTEGER NOT NULL DEFAULT 0,
          notify   INTEGER NOT NULL DEFAULT 0,
          FOREIGN KEY(user_id) REFERENCES users_new(user_id) ON DELETE CASCADE
        )""")

        # 2) Read source structure
        if not table_exists(cur, "users"):
            raise RuntimeError("Source table 'users' not found. Aborting.")

        has_login = col_exists(cur, "users", "login")
        has_pass = col_exists(cur, "users", "password")
        has_vip = col_exists(cur, "users", "vip")
        has_expires = col_exists(cur, "users", "expires")
        has_notify = col_exists(cur, "users", "notify")

        # 3) Fill users_new with all user_ids
        cur.execute(
            "INSERT OR IGNORE INTO users_new(user_id) SELECT DISTINCT user_id FROM users WHERE user_id IS NOT NULL;")

        # 4) Fill creds if columns present
        if has_login or has_pass:
            sel_login = "login" if has_login else "NULL"
            sel_pass = "password" if has_pass else "NULL"
            cur.execute(f"""                INSERT OR REPLACE INTO creds(user_id, login, password)
                SELECT user_id, {sel_login}, {sel_pass}
                  FROM users WHERE user_id IS NOT NULL
            """)

        # 5) Fill subs with safe defaults if columns missing
        sel_vip = "COALESCE(vip,0)" if has_vip else "0"
        sel_expires = "COALESCE(expires,0)" if has_expires else "0"
        sel_notify = "COALESCE(notify,0)" if has_notify else "0"

        cur.execute(f"""            INSERT OR REPLACE INTO subs(user_id, vip, expires, notify)
            SELECT user_id, {sel_vip}, {sel_expires}, {sel_notify}
              FROM users WHERE user_id IS NOT NULL
        """)

        # 6) Move referrals → users_new if exists
        if table_exists(cur, "referrals"):
            # set referrer_id, invites, total_invites
            cur.execute("""                UPDATE users_new
                   SET referrer_id = (SELECT r.referrer_id FROM referrals r WHERE r.user_id = users_new.user_id),
                       invites     = COALESCE((SELECT r.invites FROM referrals r WHERE r.user_id = users_new.user_id), 0),
                       total_invites = COALESCE((SELECT r.invites FROM referrals r WHERE r.user_id = users_new.user_id), 0)
            """)
        else:
            print("[WARN] Table 'referrals' not found. Skipping referral migration.")

        # 7) Orphans cleanup (safety)
        cur.execute("DELETE FROM creds WHERE user_id NOT IN (SELECT user_id FROM users_new);")
        cur.execute("DELETE FROM subs  WHERE user_id NOT IN (SELECT user_id FROM users_new);")

        # 8) Swap tables
        cur.execute("ALTER TABLE users RENAME TO users_old;")
        if table_exists(cur, "referrals"):
            cur.execute("ALTER TABLE referrals RENAME TO referrals_old;")
        cur.execute("ALTER TABLE users_new RENAME TO users;")

        cur.execute("COMMIT;")
        print("[OK] Migration completed successfully.")
        print("[NOTE] Verify data, then optionally drop legacy tables:")
        print("       DROP TABLE IF EXISTS users_old;")
        print("       DROP TABLE IF EXISTS referrals_old;")

    except Exception as e:
        cur.execute("ROLLBACK;")
        print(f"[ERROR] Migration failed: {e}")
        print("[INFO] Your original DB is safe. You have a backup at:", backup_path)
        sys.exit(2)
    finally:
        cur.execute("PRAGMA foreign_keys=ON;")
        conn.close()


if __name__ == '__main__':
    main()
