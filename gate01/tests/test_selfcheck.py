"""セルフチェックリスト13項目を自動確認するテスト（pytest）"""
import os
import re
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as app_module  # noqa: E402
from init_db import init_db  # noqa: E402

ADMIN, HANAKO, JIRO, INACTIVE = 1, 2, 3, 4
NEW_ID, INPROG_ID, DONE_ID, PENDING_ID = 1, 2, 3, 4


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    init_db(db)
    monkeypatch.setattr(app_module, "DB_PATH", db)
    monkeypatch.delenv("FAIL_HISTORY", raising=False)
    app_module.app.config["TESTING"] = True
    c = app_module.app.test_client()
    c.db_path = db
    return c


def login(c, uid):
    c.post("/login", data={"user_id": uid})


def row(c, iid):
    conn = sqlite3.connect(c.db_path)
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM inquiries WHERE id=?", (iid,)).fetchone()
    conn.close()
    return r


def hist_count(c, iid, field=None):
    conn = sqlite3.connect(c.db_path)
    q = "SELECT COUNT(*) FROM inquiry_histories WHERE inquiry_id=?"
    args = [iid]
    if field:
        q += " AND field_name=?"
        args.append(field)
    n = conn.execute(q, args).fetchone()[0]
    conn.close()
    return n


def post_status(c, iid, status, comment=""):
    return c.post(f"/inquiries/{iid}/status", follow_redirects=True,
                  data={"status": status, "comment": comment, "updated_at": row(c, iid)["updated_at"]})


def post_assignee(c, iid, assignee):
    return c.post(f"/inquiries/{iid}/assignee", follow_redirects=True,
                  data={"assignee_id": assignee, "updated_at": row(c, iid)["updated_at"]})


def radios(html):
    return re.findall(r'name="status" value="(\w+)"', html)


# 1
def test_01_new_shows_only_in_progress(client):
    login(client, ADMIN)
    assert radios(client.get(f"/inquiries/{NEW_ID}").get_data(as_text=True)) == ["IN_PROGRESS"]


# 2
def test_02_new_to_done_rejected(client):
    login(client, ADMIN)
    html = post_status(client, NEW_ID, "DONE").get_data(as_text=True)
    assert "このステータスへは変更できません。画面を再読み込みしてください。" in html
    assert row(client, NEW_ID)["status"] == "NEW"


# 3 & 4
def test_03_04_reopen_clears_closed_at(client):
    login(client, ADMIN)
    assert row(client, DONE_ID)["closed_at"] is not None
    html = post_status(client, DONE_ID, "IN_PROGRESS").get_data(as_text=True)
    assert "ステータスを「対応中」に変更しました。" in html
    r = row(client, DONE_ID)
    assert r["status"] == "IN_PROGRESS" and r["closed_at"] is None


# 5
def test_05_auto_assign_operator(client):
    login(client, HANAKO)
    post_status(client, NEW_ID, "IN_PROGRESS")
    assert row(client, NEW_ID)["assignee_id"] == HANAKO


# 6
def test_06_member_cannot_change_others(client):
    login(client, JIRO)
    html = post_assignee(client, INPROG_ID, JIRO).get_data(as_text=True)  # 担当は佐藤
    assert "この操作を行う権限がありません。" in html
    assert row(client, INPROG_ID)["assignee_id"] == HANAKO


# 7
def test_07_member_self_assign(client):
    login(client, JIRO)
    html = post_assignee(client, NEW_ID, JIRO).get_data(as_text=True)
    assert "担当者を「鈴木 次郎」に変更しました。" in html
    assert row(client, NEW_ID)["assignee_id"] == JIRO


# 8
def test_08_admin_unassign_only_new(client):
    login(client, ADMIN)
    post_assignee(client, NEW_ID, HANAKO)
    html = post_assignee(client, NEW_ID, "").get_data(as_text=True)
    assert "担当者を未割り当てに戻しました。" in html
    assert row(client, NEW_ID)["assignee_id"] is None
    html = post_assignee(client, INPROG_ID, "").get_data(as_text=True)
    assert "この操作を行う権限がありません。" in html
    assert row(client, INPROG_ID)["assignee_id"] == HANAKO


# 9
def test_09_inactive_user_hidden_and_rejected(client):
    login(client, ADMIN)
    html = client.get(f"/inquiries/{NEW_ID}").get_data(as_text=True)
    assert "高橋 三郎" not in html
    html = post_assignee(client, NEW_ID, INACTIVE).get_data(as_text=True)
    assert "指定されたユーザーは選択できません。" in html


# 10
def test_10_history_increments(client):
    login(client, ADMIN)
    before = hist_count(client, INPROG_ID)
    post_status(client, INPROG_ID, "PENDING")
    assert hist_count(client, INPROG_ID) == before + 1
    post_assignee(client, INPROG_ID, JIRO)
    assert hist_count(client, INPROG_ID) == before + 2


# 11
def test_11_comment_201_chars(client):
    login(client, ADMIN)
    html = post_status(client, NEW_ID, "IN_PROGRESS", "あ" * 201).get_data(as_text=True)
    assert "コメントは200文字以内で入力してください。" in html
    assert row(client, NEW_ID)["status"] == "NEW"
    html = post_status(client, NEW_ID, "IN_PROGRESS", "あ" * 200).get_data(as_text=True)
    assert "ステータスを「対応中」に変更しました。" in html


# 12（文言の完全一致は各テストの assert で確認。定数自体もここで固定）
def test_12_messages_exact():
    m = app_module
    assert m.MSG_STATUS_OK.format("対応中") == "ステータスを「対応中」に変更しました。"
    assert m.MSG_ASSIGNEE_OK.format("佐藤 花子") == "担当者を「佐藤 花子」に変更しました。"
    assert m.MSG_UNASSIGN_OK == "担当者を未割り当てに戻しました。"
    assert m.MSG_INVALID_TRANSITION == "このステータスへは変更できません。画面を再読み込みしてください。"
    assert m.MSG_NO_PERMISSION == "この操作を行う権限がありません。"
    assert m.MSG_DONE_ASSIGNEE == "完了済みの問い合わせは担当者を変更できません。"
    assert m.MSG_INVALID_USER == "指定されたユーザーは選択できません。"
    assert m.MSG_COMMENT_TOO_LONG == "コメントは200文字以内で入力してください。"
    assert m.MSG_NOT_FOUND == "指定された問い合わせは存在しません。"


# 13
def test_13_rollback_when_history_fails(client, monkeypatch):
    login(client, ADMIN)
    ua = row(client, INPROG_ID)["updated_at"]
    monkeypatch.setenv("FAIL_HISTORY", "1")
    app_module.app.config["PROPAGATE_EXCEPTIONS"] = False
    resp = client.post(f"/inquiries/{INPROG_ID}/status",
                       data={"status": "DONE", "comment": "", "updated_at": ua})
    assert resp.status_code == 500
    r = row(client, INPROG_ID)
    assert r["status"] == "IN_PROGRESS" and r["closed_at"] is None and r["updated_at"] == ua
    assert hist_count(client, INPROG_ID) == 0


# ---- 補足チェック（13項目以外） ----
def test_r02_done_assignee_rejected(client):
    login(client, ADMIN)
    html = post_assignee(client, DONE_ID, JIRO).get_data(as_text=True)
    assert "完了済みの問い合わせは担当者を変更できません。" in html


def test_not_found(client):
    login(client, ADMIN)
    r = client.get("/inquiries/999")
    assert r.status_code == 404 and "指定された問い合わせは存在しません。" in r.get_data(as_text=True)


def test_n02_conflict(client):
    login(client, ADMIN)
    html = client.post(f"/inquiries/{NEW_ID}/status", follow_redirects=True,
                       data={"status": "IN_PROGRESS", "updated_at": "1999-01-01 00:00:00.000000"}
                       ).get_data(as_text=True)
    assert "他のユーザーが更新しました。画面を再読み込みしてください。" in html
    assert row(client, NEW_ID)["status"] == "NEW"


def test_v04_member_locked_and_v07(client):
    login(client, JIRO)
    html = client.get(f"/inquiries/{INPROG_ID}").get_data(as_text=True)
    assert "担当者の変更は管理者に依頼してください" in html and "disabled" in html
    assert '<option value="">未割り当て</option>' not in html
    assert "変更履歴はありません" in html


def test_all_transitions_matrix(client):
    login(client, ADMIN)
    allowed = {("NEW", "IN_PROGRESS"), ("IN_PROGRESS", "PENDING"), ("IN_PROGRESS", "DONE"),
               ("PENDING", "IN_PROGRESS"), ("DONE", "IN_PROGRESS")}
    start = {"NEW": NEW_ID, "IN_PROGRESS": INPROG_ID, "DONE": DONE_ID, "PENDING": PENDING_ID}
    for src, iid in start.items():
        for dst in ["NEW", "IN_PROGRESS", "PENDING", "DONE"]:
            if src == dst:
                continue
            ok = (src, dst) in allowed
            html = post_status(client, iid, dst).get_data(as_text=True)
            assert ("変更しました。" in html) == ok, (src, dst)
            if ok:  # 元に戻す（テスト用に DB を直接戻す）
                conn = sqlite3.connect(client.db_path)
                conn.execute("UPDATE inquiries SET status=? WHERE id=?", (src, iid))
                conn.commit()
                conn.close()
