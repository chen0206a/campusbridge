import hashlib
import hmac
import secrets
import time

from sqlalchemy import select

from campusbridge.contracts import AccessDenied, Identity, digest, identifier
from campusbridge.db import Account, LoginToken, Student

DEMO_PASSWORD = "CampusDemo-2026!"  # Published, fictional, loopback development accounts only.


def password_hash(password):
    salt = secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310000).hex()
    return "pbkdf2_sha256:310000:" + salt + ":" + value


def password_matches(password, stored):
    try:
        algorithm, iterations, salt, expected = stored.split(":")
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class Authentication:
    def __init__(self, database, settings):
        self.db, self.settings = database, settings

    def seed(self):
        profiles = [("student_lan", "student", "学生澜"), ("student_he", "student", "学生禾"),
                    ("counselor_yu", "counselor", None)]
        with self.db.sessions.begin() as session:
            for username, role, name in profiles:
                if session.scalar(select(Account).where(Account.username == username)):
                    continue
                account = Account(id=identifier(), username=username, password_hash=password_hash(DEMO_PASSWORD),
                                  role=role, campus_id="fictional-campus-a")
                session.add(account)
                session.flush()
                if role == "student":
                    session.add(Student(id=account.id, display_name=name, synthetic=True))

    def login(self, username, password):
        with self.db.sessions.begin() as session:
            account = session.scalar(select(Account).where(Account.username == username))
            encoded = account.password_hash if account else password_hash("unavailable-account")
            if not password_matches(password, encoded) or account is None or not account.enabled:
                raise AccessDenied("INVALID_CREDENTIALS")
            token = secrets.token_urlsafe(32)
            session.add(LoginToken(token_hash=digest(token), account_id=account.id,
                                   expires_at=time.time() + self.settings.token_lifetime_seconds))
            return {"access_token": token, "token_type": "bearer", "role": account.role,
                    "expires_in": self.settings.token_lifetime_seconds}

    def authenticate(self, token):
        with self.db.sessions() as session:
            credential = session.get(LoginToken, digest(token))
            if credential is None or credential.expires_at <= time.time():
                raise AccessDenied("INVALID_TOKEN")
            account = session.get(Account, credential.account_id)
            if account is None or not account.enabled:
                raise AccessDenied("INVALID_TOKEN")
            return Identity(account_id=account.id, role=account.role, campus_id=account.campus_id)
