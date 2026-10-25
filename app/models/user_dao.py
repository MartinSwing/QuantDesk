# -*- coding: utf-8 -*-
"""用户与资料 DAO"""
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


class User:
    """Flask-Login 用户对象"""

    def __init__(self, row: dict):
        self.id = row["user_account"]
        self.user_account = row["user_account"]
        self.role = row.get("role", "user")

    @property
    def is_admin(self):
        return self.role == "admin"

    # Flask-Login 接口
    def get_id(self):
        return self.user_account

    @property
    def is_authenticated(self):
        return True

    @property
    def is_active(self):
        return True

    @property
    def is_anonymous(self):
        return False


class UserDAO:
    @staticmethod
    def get_by_account(user_account: str):
        return db.fetch_one("SELECT * FROM users WHERE user_account = %s", (user_account,))

    @staticmethod
    def verify(user_account: str, password: str):
        row = UserDAO.get_by_account(user_account)
        if row and check_password_hash(row["password_hash"], password):
            return row
        return None

    @staticmethod
    def next_account() -> str:
        """系统自动生成六位递增账号"""
        row = db.fetch_one("SELECT MAX(user_account) AS max_acc FROM users WHERE user_account REGEXP '^[0-9]{6}$'")
        max_acc = row["max_acc"] if row and row["max_acc"] else "0"
        return f"{int(max_acc) + 1:06d}"

    @staticmethod
    def create(password: str, role: str = "user") -> str:
        user_account = UserDAO.next_account()
        with db.transaction() as cur:
            cur.execute(
                "INSERT INTO users (user_account, password_hash, role) VALUES (%s,%s,%s)",
                (user_account, generate_password_hash(password), role),
            )
            cur.execute("INSERT INTO accounts (user_account) VALUES (%s)", (user_account,))
        return user_account

    @staticmethod
    def update_password(user_account: str, new_password: str):
        db.execute(
            "UPDATE users SET password_hash = %s WHERE user_account = %s",
            (generate_password_hash(new_password), user_account),
        )

    @staticmethod
    def set_role(user_account: str, role: str):
        db.execute("UPDATE users SET role = %s WHERE user_account = %s", (role, user_account))

    @staticmethod
    def set_status(user_account: str, status: str):
        db.execute("UPDATE users SET status = %s WHERE user_account = %s", (status, user_account))

    @staticmethod
    def list_all():
        sql = """
            SELECT u.id, u.user_account, u.role, u.status, u.created_at,
                   p.real_name, p.phone
            FROM users u LEFT JOIN user_profiles p ON u.user_account = p.user_account
            ORDER BY u.id
        """
        return db.fetch_all(sql)


class ProfileDAO:
    @staticmethod
    def get(user_account: str):
        return db.fetch_one("SELECT * FROM user_profiles WHERE user_account = %s", (user_account,))

    @staticmethod
    def create(user_account: str, data: dict):
        db.execute(
            """INSERT INTO user_profiles
               (user_account, real_name, id_card, birthday, phone, bank_card, province, occupation, address)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                user_account, data["real_name"], data["id_card"], data["birthday"],
                data["phone"], data["bank_card"], data["province"],
                data["occupation"], data.get("address", ""),
            ),
        )

    @staticmethod
    def find_by_identity(real_name: str, id_card: str, phone: str):
        """找回密码的身份校验"""
        return db.fetch_one(
            "SELECT user_account FROM user_profiles WHERE real_name=%s AND id_card=%s AND phone=%s",
            (real_name, id_card, phone),
        )
