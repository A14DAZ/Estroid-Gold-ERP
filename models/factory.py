import secrets
import string
from datetime import datetime, date, timedelta
from models.db import db


def _gen_code():
    """Generate an activation code like ABCD-1234-EFGH-5678."""
    chars = string.ascii_uppercase + string.digits
    return '-'.join(''.join(secrets.choice(chars) for _ in range(5)) for _ in range(4))


def _gen_factory_code():
    """Generate unique factory code FAC-0001, FAC-0002 ..."""
    try:
        from sqlalchemy import text as _text
        from models.db import db as _db
        rows = _db.session.execute(
            _text("SELECT factory_code FROM factory WHERE factory_code IS NOT NULL AND factory_code != ''")
        ).fetchall()
        nums = []
        for (code,) in rows:
            if code and code.upper().startswith('FAC-'):
                try: nums.append(int(code.split('-')[1]))
                except: pass
        return f'FAC-{(max(nums)+1 if nums else 1):04d}'
    except Exception:
        import secrets as _s
        return f'FAC-{_s.randbelow(9000)+1000}'


class Factory(db.Model):
    __tablename__ = 'factory'

    id                  = db.Column(db.Integer, primary_key=True)
    user_id             = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=True)
    name                = db.Column(db.String(120), nullable=False)
    city                = db.Column(db.String(80))
    country             = db.Column(db.String(60), default='SA')
    address             = db.Column(db.Text, default='')
    phone               = db.Column(db.String(40))
    whatsapp            = db.Column(db.String(40), default='')
    website             = db.Column(db.String(120), default='')
    instagram           = db.Column(db.String(80), default='')
    twitter             = db.Column(db.String(80), default='')
    commercial_reg      = db.Column(db.String(40), default='')  # سجل تجاري
    tax_number          = db.Column(db.String(40), default='')  # رقم ضريبي
    description         = db.Column(db.Text, default='')
    # ── Print / Invoice settings ──────────────────────────────
    invoice_footer      = db.Column(db.Text, default='')       # تذييل الفاتورة
    thank_you_msg       = db.Column(db.String(200), default='') # عبارة الشكر
    # ── System preferences ───────────────────────────────────
    default_karat       = db.Column(db.Integer, default=21)
    default_currency    = db.Column(db.String(10), default='SAR')
    weight_unit         = db.Column(db.String(10), default='g')  # g / oz
    factory_color       = db.Column(db.String(10), default='#D4AF37')  # brand color
    logo_data           = db.Column(db.Text, nullable=True)   # base64 encoded image
    plan                = db.Column(db.String(20), default='basic')
    is_active           = db.Column(db.Boolean, default=True)
    # ── Subscription ──────────────────────────────────────────
    activation_code     = db.Column(db.String(24), unique=True)   # e.g. ABCDE-12345-FGHIJ-67890
    factory_code        = db.Column(db.String(20), unique=True, nullable=True)  # FAC-0001
    # ── Subscription financial ────────────────────────────────
    subscription_start  = db.Column(db.Date, nullable=True)
    subscription_price  = db.Column(db.Float, default=0.0)   # agreed price
    grace_period_days   = db.Column(db.Integer, default=3)   # days after expiry before freeze
    health_score        = db.Column(db.Integer, default=100)  # 0-100
    subscription_expiry = db.Column(db.Date)
    # ──────────────────────────────────────────────────────────
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)

    user    = db.relationship('User', back_populates='factory', foreign_keys=[user_id])
    workers = db.relationship('Worker', backref='factory', lazy='dynamic', cascade='all,delete')
    orders    = db.relationship('WorkOrder', backref='factory', lazy='dynamic', cascade='all,delete')

    # ── Subscription helpers ──────────────────────────────────

    @property
    def is_subscription_valid(self):
        """True when no expiry set OR expiry is in the future."""
        if self.subscription_expiry is None:
            return True
        return date.today() <= self.subscription_expiry

    @property
    def is_in_grace_period(self):
        """True if subscription just expired but within grace_period_days."""
        if self.subscription_expiry is None:
            return False
        from datetime import date
        days_over = (date.today() - self.subscription_expiry).days
        return 0 < days_over <= (self.grace_period_days or 3)

    @property
    def is_read_only(self):
        """True if past grace period — account is frozen (read-only)."""
        if self.subscription_expiry is None:
            return False
        from datetime import date
        days_over = (date.today() - self.subscription_expiry).days
        return days_over > (self.grace_period_days or 3)

    @property
    def subscription_status(self):
        if not self.is_active:
            return 'suspended'
        if not self.is_subscription_valid:
            return 'expired'
        return 'active'

    @property
    def days_remaining(self):
        if self.subscription_expiry is None:
            return None
        delta = (self.subscription_expiry - date.today()).days
        return max(delta, 0)

    def extend_subscription(self, days: int):
        base = max(self.subscription_expiry or date.today(), date.today())
        self.subscription_expiry = base + timedelta(days=days)

    @staticmethod
    def generate_activation_code():
        return _gen_code()

    # ── Gold stock helpers ────────────────────────────────────

    @property
    def active_orders_count(self):
        from models.order import WorkOrder
        return WorkOrder.query.filter_by(factory_id=self.id, status='active').count()

    @property
    def total_pure_gold(self):
        """Net pure-gold balance (24K equivalent) from inventory."""
        from models.order import Inventory
        pure_in = db.session.query(
            db.func.coalesce(db.func.sum(Inventory.pure_weight), 0)
        ).filter_by(factory_id=self.id, transaction_type='in').scalar() or 0.0

        pure_out = db.session.query(
            db.func.coalesce(db.func.sum(Inventory.pure_weight), 0)
        ).filter_by(factory_id=self.id, transaction_type='out').scalar() or 0.0

        return round(pure_in - pure_out, 4)

    @property
    def total_gold_stock(self):
        """Net raw-weight balance (kept for backwards compat)."""
        from models.order import Inventory
        gold_in = db.session.query(
            db.func.coalesce(db.func.sum(Inventory.weight), 0)
        ).filter_by(factory_id=self.id, transaction_type='in').scalar() or 0.0

        gold_out = db.session.query(
            db.func.coalesce(db.func.sum(Inventory.weight), 0)
        ).filter_by(factory_id=self.id, transaction_type='out').scalar() or 0.0

        return round(gold_in - gold_out, 4)

    def __repr__(self):
        return f'<Factory {self.name}>'
