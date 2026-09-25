"""DB 作成と動作確認用データ投入（2.3 データ定義どおり）"""
import os
import sqlite3
import sys

SCHEMA = """
DROP TABLE IF EXISTS inquiry_histories;
DROP TABLE IF EXISTS inquiries;
DROP TABLE IF EXISTS users;

CREATE TABLE users (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      VARCHAR(50) NOT NULL,
    role      VARCHAR(20) NOT NULL DEFAULT 'MEMBER',
    is_active BOOLEAN     NOT NULL DEFAULT 1
);

CREATE TABLE inquiries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          VARCHAR(100) NOT NULL,
    body           TEXT         NOT NULL,
    requester_name VARCHAR(50)  NOT NULL,
    status         VARCHAR(20)  NOT NULL DEFAULT 'NEW',
    assignee_id    BIGINT       NULL DEFAULT NULL REFERENCES users(id),
    priority       VARCHAR(10)  NOT NULL DEFAULT 'MIDDLE',
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at      DATETIME     NULL DEFAULT NULL
);

CREATE TABLE inquiry_histories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    inquiry_id  BIGINT      NOT NULL,
    changed_by  BIGINT      NOT NULL,
    field_name  VARCHAR(20) NOT NULL,
    old_value   VARCHAR(50) NULL DEFAULT NULL,
    new_value   VARCHAR(50) NULL DEFAULT NULL,
    changed_at  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

USERS = [
    # (name, role, is_active)
    ("管理 一郎", "ADMIN", 1),
    ("佐藤 花子", "MEMBER", 1),
    ("鈴木 次郎", "MEMBER", 1),
    ("高橋 三郎", "MEMBER", 0),   # 無効ユーザー（プルダウンに出ないことの確認用）
]

INQUIRIES = [
    # (title, body, requester, status, assignee_id, priority, created, closed)
    ("プリンタが印刷できません", "3階の複合機で印刷しようとするとエラーになります。",
     "山田 太郎", "NEW", None, "MIDDLE", "2026-07-29 10:15:00.000000", None),
    ("Excel をインストールしたい", "業務で Excel が必要になりました。",
     "田中 一美", "IN_PROGRESS", 2, "LOW", "2026-07-29 11:00:00.000000", None),
    ("PC が起動しない", "電源ボタンを押しても画面が真っ暗です。",
     "伊藤 健", "DONE", 2, "HIGH", "2026-07-28 09:30:00.000000", "2026-07-28 15:00:00.000000"),
    ("VPN に接続できない", "在宅勤務時に VPN がつながりません。",
     "渡辺 由美", "PENDING", 3, "HIGH", "2026-07-28 14:00:00.000000", None),
    ("マウスが反応しない", "USB マウスが認識されません。",
     "中村 優", "NEW", None, "LOW", "2026-07-30 09:00:00.000000", None),
]


def init_db(path):
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO users (name, role, is_active) VALUES (?, ?, ?)", USERS)
    for t, b, r, s, a, p, c, closed in INQUIRIES:
        conn.execute(
            "INSERT INTO inquiries (title, body, requester_name, status, assignee_id, priority,"
            " created_at, updated_at, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (t, b, r, s, a, p, c, c, closed))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "inquiry.db")
    init_db(target)
    print(f"initialized: {target}")
