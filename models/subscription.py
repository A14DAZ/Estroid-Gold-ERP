"""
Subscription management models:
  - SubscriptionPlan  : plan definitions (Basic / Pro / Enterprise)
  - Payment           : payment records per factory
  - FactoryNote       : internal notes on a factory
"""
from datetime import datetime
from models.db import db


class SubscriptionPlan(db.Model):
    __tablename__ = 'subscription_plan'

    id          = db.Column(db.Integer, primary_key=True)
    slug        = db.Column(db.String(30), unique=True, nullable=False)  # basic / pro / enterprise
    name        = db.Column(db.String(60), nullable=False)
    price_month = db.Column(db.Float, default=0.0)   # monthly price
    price_year  = db.Column(db.Float, default=0.0)   # yearly price
    max_workers = db.Column(db.Integer, default=0)   # 0 = unlimited
    max_orders  = db.Column(db.Integer, default=0)
    max_customers = db.Column(db.Integer, default=0)
    features    = db.Column(db.Text, default='')     # comma-separated
    is_active   = db.Column(db.Boolean, default=True)
    sort_order  = db.Column(db.Integer, default=0)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)


class Payment(db.Model):
    __tablename__ = 'payment'

    id             = db.Column(db.Integer, primary_key=True)
    factory_id     = db.Column(db.Integer, db.ForeignKey('factory.id'), nullable=False, index=True)
    recorded_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    invoice_number = db.Column(db.String(30), unique=True)  # PAY-0001
    plan_slug      = db.Column(db.String(30), default='basic')
    amount         = db.Column(db.Float, default=0.0)
    currency       = db.Column(db.String(10), default='SAR')
    method         = db.Column(db.String(30), default='manual')
    # method: manual | bank_transfer | cash | stripe | paypal
    status         = db.Column(db.String(20), default='paid')
    # status: paid | pending | failed | refunded
    period_days    = db.Column(db.Integer, default=30)
    notes          = db.Column(db.Text, default='')
    paid_at        = db.Column(db.DateTime, default=datetime.utcnow)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    factory      = db.relationship('Factory', backref=db.backref('payments', lazy='dynamic'))
    recorded_by  = db.relationship('User', foreign_keys=[recorded_by_id])


class FactoryNote(db.Model):
    __tablename__ = 'factory_note'

    id         = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey('factory.id'), nullable=False, index=True)
    author_id  = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    body       = db.Column(db.Text, nullable=False)
    tag        = db.Column(db.String(30), default='general')
    # tag: general | payment | issue | discount | contacted
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    factory = db.relationship('Factory', backref=db.backref('notes_list', lazy='dynamic'))
    author  = db.relationship('User', foreign_keys=[author_id])


def _next_invoice_number():
    """Generate PAY-0001, PAY-0002 ..."""
    try:
        last = Payment.query.order_by(Payment.id.desc()).first()
        n = (last.id + 1) if last else 1
        return f'PAY-{n:04d}'
    except Exception:
        import secrets
        return f'PAY-{secrets.randbelow(9000)+1000}'
