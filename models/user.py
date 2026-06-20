import random
import string
from datetime import datetime, timedelta
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from models.db import db


def _gen_otp(length=6):
    return ''.join(random.choices(string.digits, k=length))


class User(UserMixin, db.Model):
    __tablename__ = 'user'

    id            = db.Column(db.Integer, primary_key=True)
    email         = db.Column(db.String(150), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    full_name     = db.Column(db.String(120), nullable=False)
    role          = db.Column(db.String(20), default='factory')   # superadmin | factory

    # ── Phone ─────────────────────────────────────────────────
    country_code  = db.Column(db.String(10),  default='+966')     # e.g. +966
    phone_number  = db.Column(db.String(20))                       # without country code

    # ── Account state ─────────────────────────────────────────
    is_active       = db.Column(db.Boolean, default=True)
    # ── Team / System staff ───────────────────────────────────
    employee_code   = db.Column(db.String(20), unique=True, nullable=True)  # EMP-001
    system_role     = db.Column(db.String(30), default='factory')
    # system_role values: superadmin | support | accountant | manager | factory
    is_phone_verified = db.Column(db.Boolean, default=False)

    # ── OTP ───────────────────────────────────────────────────
    otp_code       = db.Column(db.String(10))
    otp_expires_at = db.Column(db.DateTime)

    last_login = db.Column(db.DateTime)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)
    last_login_ip = db.Column(db.String(45), nullable=True)
    api_key       = db.Column(db.String(64), unique=True, nullable=True)

    factory = db.relationship('Factory', back_populates='user', uselist=False,
                               foreign_keys='Factory.user_id')

    # ── Password helpers ──────────────────────────────────────

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    # ── OTP helpers ───────────────────────────────────────────

    def generate_otp(self, expires_minutes=15):
        """Generate a 6-digit OTP valid for `expires_minutes`."""
        self.otp_code = _gen_otp(6)
        self.otp_expires_at = datetime.utcnow() + timedelta(minutes=expires_minutes)
        return self.otp_code

    def verify_otp(self, code):
        """Return True if code matches and hasn't expired."""
        if not self.otp_code or not self.otp_expires_at:
            return False
        if datetime.utcnow() > self.otp_expires_at:
            return False
        return self.otp_code.strip() == str(code).strip()

    def clear_otp(self):
        self.otp_code = None
        self.otp_expires_at = None

    # ── Phone formatting ──────────────────────────────────────

    @property
    def full_phone(self):
        if self.phone_number:
            return f"{self.country_code or ''}{self.phone_number}"
        return ''

    # ── Role helpers ──────────────────────────────────────────

    @property
    def is_superadmin(self):
        return self.role == 'superadmin'

    @property
    def is_staff(self):
        """System team member (not factory, not superadmin)."""
        return self.role in ('superadmin', 'staff') and self.factory is None

    @property
    def is_platform_user(self):
        """Either superadmin or system staff — can access admin panel."""
        return self.role in ('superadmin', 'staff')

    def __repr__(self):
        return f'<User {self.email}>'
