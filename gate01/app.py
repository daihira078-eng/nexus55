"""社内問い合わせ管理システム — ステータス変更・担当者割り当て機能（要件定義書 v1.2）"""
import os
import sqlite3
from datetime import datetime

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "inquiry.db"))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "gate1-dev-secret")

# ---- 2.4 ステータス定義と遷移表（画面とサーバーの両方がここを参照する） ----
STATUS_LABELS = {
    "NEW": "未対応",
    "IN_PROGRESS": "対応中",
    "PENDING": "保留",
    "DONE": "完了",
}
TRANSITIONS = {
    "NEW": ["IN_PROGRESS"],
    "IN_PROGRESS": ["PENDING", "DONE"],
    "PENDING": ["IN_PROGRESS"],
    "DONE": ["IN_PROGRESS"],
}

# ---- V-06 優先度表示 ----
PRIORITY_DISPLAY = {
    "HIGH": ("高", "priority-high"),
    "MIDDLE": ("中", "priority-middle"),
    "LOW": ("低", "priority-low"),
}

# ---- 2.8 処理結果メッセージ（句読点まで完全一致） ----
MSG_STATUS_OK = "ステータスを「{}」に変更しました。"
MSG_ASSIGNEE_OK = "担当者を「{}」に変更しました。"
MSG_UNASSIGN_OK = "担当者を未割り当てに戻しました。"
MSG_INVALID_TRANSITION = "このステータスへは変更できません。画面を再読み込みしてください。"
MSG_NO_PERMISSION = "この操作を行う権限がありません。"
MSG_DONE_ASSIGNEE = "完了済みの問い合わせは担当者を変更できません。"
MSG_INVALID_USER = "指定されたユーザーは選択できません。"
MSG_COMMENT_TOO_LONG = "コメントは200文字以内で入力してください。"
MSG_NOT_FOUND = "指定された問い合わせは存在しません。"
# ---- 2.9 N-02 ----
MSG_CONFLICT = "他のユーザーが更新しました。画面を再読み込みしてください。"
# ---- 2.7 V-04 ----
MSG_ASK_ADMIN = "担当者の変更は管理者に依頼してください"

COMMENT_MAX = 200
HISTORY_LIMIT = 20


class BusinessError(Exception):
    """業務ルール違反。メッセージをそのまま画面に出す。"""


# ------------------------------------------------------------------ DB
def get_db():
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH, isolation_level=None)  # トランザクションは手動管理
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now_str():
    # 排他制御(N-02)で比較するため、マイクロ秒まで保持する
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


def insert_history(db, inquiry_id, changed_by, field_name, old_value, new_value, at):
    if os.environ.get("FAIL_HISTORY") == "1":
        # N-01 の検証用：履歴登録を意図的に失敗させる
        raise sqlite3.OperationalError("FAIL_HISTORY=1: 履歴登録を意図的に失敗させました")
    db.execute(
        "INSERT INTO inquiry_histories (inquiry_id, changed_by, field_name, old_value, new_value, changed_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (inquiry_id, changed_by, field_name, old_value, new_value, at),
    )


# ------------------------------------------------------------------ ログインユーザー
def current_user():
    uid = session.get("user_id")
    if uid is None:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


@app.route("/login", methods=["GET", "POST"])
def login():
    """ログイン機能は既存想定。動作確認用にユーザーを選んでログインするだけの簡易版。"""
    db = get_db()
    if request.method == "POST":
        user = db.execute("SELECT * FROM users WHERE id = ? AND is_active = 1",
                          (request.form.get("user_id"),)).fetchone()
        if user:
            session["user_id"] = user["id"]
            return redirect(request.form.get("next") or url_for("index"))
    users = db.execute("SELECT * FROM users WHERE is_active = 1 ORDER BY id").fetchall()
    return render_template("login.html", users=users, next=request.args.get("next", ""))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.before_request
def require_login():
    if request.endpoint in ("login", "static"):
        return None
    if current_user() is None:
        return redirect(url_for("login", next=request.path))
    return None


# ------------------------------------------------------------------ 一覧（既存想定・遷移用の最小版）
@app.route("/")
def index():
    rows = get_db().execute(
        "SELECT i.*, u.name AS assignee_name FROM inquiries i"
        " LEFT JOIN users u ON u.id = i.assignee_id ORDER BY i.id").fetchall()
    return render_template("index.html", inquiries=rows, labels=STATUS_LABELS, user=current_user())


# ------------------------------------------------------------------ 詳細画面
def load_inquiry(db, inquiry_id):
    return db.execute("SELECT * FROM inquiries WHERE id = ?", (inquiry_id,)).fetchone()


def fmt_dt(value):
    if not value:
        return ""
    return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").strftime("%Y/%m/%d %H:%M")


@app.route("/inquiries/<int:inquiry_id>")
def detail(inquiry_id):
    db = get_db()
    user = current_user()
    inq = load_inquiry(db, inquiry_id)
    if inq is None:
        return render_template("not_found.html", message=MSG_NOT_FOUND, user=user), 404

    assignee = None
    if inq["assignee_id"] is not None:
        assignee = db.execute("SELECT * FROM users WHERE id = ?", (inq["assignee_id"],)).fetchone()

    # V-03 有効ユーザーのみ・氏名順
    active_users = db.execute("SELECT * FROM users WHERE is_active = 1").fetchall()
    active_users = sorted(active_users, key=lambda u: u["name"])

    # V-07 変更履歴（新しい順・最大20件）
    user_names = {u["id"]: u["name"] for u in db.execute("SELECT id, name FROM users")}
    histories = []
    for h in db.execute(
            "SELECT * FROM inquiry_histories WHERE inquiry_id = ?"
            " ORDER BY changed_at DESC, id DESC LIMIT ?", (inquiry_id, HISTORY_LIMIT)):
        if h["field_name"] == "status":
            label = "ステータス"
            old = STATUS_LABELS.get(h["old_value"], h["old_value"])
            new = STATUS_LABELS.get(h["new_value"], h["new_value"])
        else:
            label = "担当者"
            old = user_names.get(int(h["old_value"]), "") if h["old_value"] else "未割り当て"
            new = user_names.get(int(h["new_value"]), "") if h["new_value"] else "未割り当て"
        histories.append({
            "at": fmt_dt(h["changed_at"]),
            "by": user_names.get(h["changed_by"], ""),
            "label": label, "old": old, "new": new,
        })

    is_admin = user["role"] == "ADMIN"
    member_locked = (not is_admin) and inq["assignee_id"] is not None   # V-04
    done_locked = inq["status"] == "DONE"                               # V-05

    return render_template(
        "detail.html",
        inq=inq, user=user, assignee=assignee,
        status_label=STATUS_LABELS[inq["status"]],
        next_statuses=[(s, STATUS_LABELS[s]) for s in TRANSITIONS[inq["status"]]],  # V-01/V-02
        priority=PRIORITY_DISPLAY.get(inq["priority"], (inq["priority"], "")),
        created_at=fmt_dt(inq["created_at"]),
        active_users=active_users, is_admin=is_admin,
        assign_disabled=member_locked or done_locked,
        member_locked=member_locked, ask_admin_msg=MSG_ASK_ADMIN,
        histories=histories, comment_max=COMMENT_MAX,
    )


# ------------------------------------------------------------------ ステータス変更
def change_status(db, inquiry_id, operator, new_status, comment, expected_updated_at):
    inq = load_inquiry(db, inquiry_id)
    if inq is None:
        raise BusinessError(MSG_NOT_FOUND)
    if len(comment) > COMMENT_MAX:
        raise BusinessError(MSG_COMMENT_TOO_LONG)
    old_status = inq["status"]
    if new_status not in TRANSITIONS.get(old_status, []):
        raise BusinessError(MSG_INVALID_TRANSITION)

    db.execute("BEGIN IMMEDIATE")  # N-01
    try:
        at = now_str()
        # N-02 排他制御：updated_at が画面表示時と一致する場合のみ更新
        cur = db.execute(
            "UPDATE inquiries SET status = ?, updated_at = ? WHERE id = ? AND updated_at = ?",
            (new_status, at, inquiry_id, expected_updated_at))
        if cur.rowcount != 1:
            raise BusinessError(MSG_CONFLICT)

        # 2.5 付随処理
        if new_status == "DONE":
            db.execute("UPDATE inquiries SET closed_at = ? WHERE id = ?", (at, inquiry_id))
        if old_status == "DONE" and new_status == "IN_PROGRESS":
            db.execute("UPDATE inquiries SET closed_at = NULL WHERE id = ?", (inquiry_id,))
        if old_status == "NEW" and new_status == "IN_PROGRESS" and inq["assignee_id"] is None:
            db.execute("UPDATE inquiries SET assignee_id = ? WHERE id = ?", (operator["id"], inquiry_id))
            insert_history(db, inquiry_id, operator["id"], "assignee", None, str(operator["id"]), at)

        insert_history(db, inquiry_id, operator["id"], "status", old_status, new_status, at)
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise


@app.route("/inquiries/<int:inquiry_id>/status", methods=["POST"])
def post_status(inquiry_id):
    db = get_db()
    new_status = request.form.get("status", "")
    try:
        change_status(db, inquiry_id, current_user(), new_status,
                      request.form.get("comment", ""), request.form.get("updated_at", ""))
        flash(MSG_STATUS_OK.format(STATUS_LABELS[new_status]), "success")
    except BusinessError as e:
        flash(str(e), "error")
    return redirect(url_for("detail", inquiry_id=inquiry_id))


# ------------------------------------------------------------------ 担当者変更
def change_assignee(db, inquiry_id, operator, new_assignee_raw, expected_updated_at):
    inq = load_inquiry(db, inquiry_id)
    if inq is None:
        raise BusinessError(MSG_NOT_FOUND)
    # R-02
    if inq["status"] == "DONE":
        raise BusinessError(MSG_DONE_ASSIGNEE)

    is_admin = operator["role"] == "ADMIN"
    new_assignee = None
    new_user = None
    if new_assignee_raw == "":
        # R-05 未割り当てに戻せるのは ADMIN かつ NEW のみ
        if not (is_admin and inq["status"] == "NEW"):
            raise BusinessError(MSG_NO_PERMISSION)
    else:
        # R-01 有効ユーザーのみ
        try:
            new_assignee = int(new_assignee_raw)
        except ValueError:
            raise BusinessError(MSG_INVALID_USER)
        new_user = db.execute("SELECT * FROM users WHERE id = ? AND is_active = 1",
                              (new_assignee,)).fetchone()
        if new_user is None:
            raise BusinessError(MSG_INVALID_USER)
        # R-03 MEMBER は未割り当ての問い合わせを自分に割り当てる操作のみ（R-04 ADMIN は制限なし）
        if not is_admin and not (inq["assignee_id"] is None and new_assignee == operator["id"]):
            raise BusinessError(MSG_NO_PERMISSION)

    db.execute("BEGIN IMMEDIATE")  # N-01
    try:
        at = now_str()
        cur = db.execute(
            "UPDATE inquiries SET assignee_id = ?, updated_at = ? WHERE id = ? AND updated_at = ?",
            (new_assignee, at, inquiry_id, expected_updated_at))
        if cur.rowcount != 1:
            raise BusinessError(MSG_CONFLICT)  # N-02
        insert_history(db, inquiry_id, operator["id"], "assignee",   # R-06
                       None if inq["assignee_id"] is None else str(inq["assignee_id"]),
                       None if new_assignee is None else str(new_assignee), at)
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    return new_user


@app.route("/inquiries/<int:inquiry_id>/assignee", methods=["POST"])
def post_assignee(inquiry_id):
    db = get_db()
    try:
        new_user = change_assignee(db, inquiry_id, current_user(),
                                   request.form.get("assignee_id", ""),
                                   request.form.get("updated_at", ""))
        if new_user is None:
            flash(MSG_UNASSIGN_OK, "success")
        else:
            flash(MSG_ASSIGNEE_OK.format(new_user["name"]), "success")
    except BusinessError as e:
        flash(str(e), "error")
    return redirect(url_for("detail", inquiry_id=inquiry_id))


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        from init_db import init_db
        init_db(DB_PATH)
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
