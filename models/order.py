from datetime import datetime, date
from models.db import db

KARATS = [18, 21, 22, 24]


def calc_pure_weight(weight, karat):
    """Convert any karat weight to 24K pure-gold equivalent."""
    return round((weight or 0.0) * ((karat or 18) / 24.0), 6)


class WorkOrder(db.Model):
    __tablename__ = 'work_order'

    id             = db.Column(db.Integer, primary_key=True)
    factory_id     = db.Column(db.Integer, db.ForeignKey('factory.id'), nullable=False, index=True)
    client_id      = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=True)
    order_number   = db.Column(db.String(30), nullable=False)
    model_name     = db.Column(db.String(120))
    product_name   = db.Column(db.String(120))
    product_code   = db.Column(db.String(60))
    customer_name  = db.Column(db.String(120))   # kept for legacy display
    customer_phone = db.Column(db.String(40))     # kept for legacy display
    karat          = db.Column(db.Integer, default=18, nullable=False)
    initial_weight = db.Column(db.Float, default=0.0)
    final_output_weight = db.Column(db.Float, default=0.0)   # set on order close
    status         = db.Column(db.String(20), default='active')   # active | completed | cancelled
    notes          = db.Column(db.Text)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('factory_id', 'order_number'),)

    stages = db.relationship('ProductionStage', backref='work_order', lazy='dynamic',
                              cascade='all,delete')
    client = db.relationship('Customer', foreign_keys=[client_id], lazy='joined')

    @staticmethod
    def generate_number(factory_id):
        year = datetime.utcnow().year
        last = WorkOrder.query.filter_by(factory_id=factory_id)\
                              .order_by(WorkOrder.id.desc()).first()
        seq = 1
        if last and last.order_number.startswith(str(year)):
            try:
                seq = int(last.order_number.split('-')[1]) + 1
            except Exception:
                pass
        return f'{year}-{seq:04d}'

    @property
    def total_loss(self):
        """
        Total gold loss = SUM(stage.loss_weight) across all completed stages.
        loss_weight is already calculated correctly in calculate_loss() as:
            loss = received - produced - scrap
        Scrap is NOT included here — it is tracked separately via total_scrap.
        """
        from sqlalchemy import func
        from models.production import ProductionStage
        result = db.session.query(
            func.coalesce(func.sum(ProductionStage.loss_weight), 0)
        ).filter_by(work_order_id=self.id, status='completed').scalar()
        return round(result or 0.0, 4)

    @property
    def total_scrap(self):
        """
        Total returned gold (رجيع الذهب) = SUM(stage.scrap_weight).
        Separate from loss — scrap is gold returned by the worker, NOT lost.
        """
        from sqlalchemy import func
        from models.production import ProductionStage
        result = db.session.query(
            func.coalesce(func.sum(ProductionStage.scrap_weight), 0)
        ).filter_by(work_order_id=self.id, status='completed').scalar()
        return round(result or 0.0, 4)

    @property
    def total_gain(self):
        """Gold gain from stone weight (kept for backward compat)."""
        from sqlalchemy import func
        from models.production import ProductionStage
        result = db.session.query(
            func.coalesce(func.sum(ProductionStage.gold_gain), 0)
        ).filter_by(work_order_id=self.id, status='completed').scalar()
        return result or 0.0

    @property
    def last_stage_output(self):
        """Produced weight of the LAST completed stage — the true final output."""
        from models.production import ProductionStage
        last = ProductionStage.query.filter_by(
            work_order_id=self.id, status='completed'
        ).order_by(ProductionStage.id.desc()).first()
        return last.produced_weight if last else 0.0

    @property
    def total_final_output(self):
        """
        Final output = last completed stage produced_weight.
        NOT a sum (summing would accumulate weight across sequential stages).
        """
        return self.last_stage_output

    @property
    def total_stone_gain(self):
        """
        Total grams of stone embedded = SUM(stone_required_grams) across all setting stages.
        Uses stone_required (not given/returned) — required is the actual embedded weight.
        """
        from models.production import ProductionStage
        stages = ProductionStage.query.filter_by(
            work_order_id=self.id, status='completed').all()
        return round(sum(
            s.stone.stone_required_grams for s in stages if s.stone
        ), 4)

    @property
    def accounting_diff(self):
        """
        Checks: initial_weight == last_output + total_loss + total_scrap
        Returns gap; 0.0 = perfectly balanced.
        """
        return round(
            self.initial_weight
            - self.last_stage_output
            - self.total_loss
            - self.total_scrap,
            4
        )

    def __repr__(self):
        return f'<WorkOrder {self.order_number}>'


# ─────────────────────────────────────────────────────────────────────────────
# Inventory  (with karat + pure_weight)
# ─────────────────────────────────────────────────────────────────────────────

class Inventory(db.Model):
    __tablename__ = 'inventory'

    id               = db.Column(db.Integer, primary_key=True)
    factory_id       = db.Column(db.Integer, db.ForeignKey('factory.id'), nullable=False, index=True)
    transaction_type = db.Column(db.String(10), default='in')   # in | out
    weight           = db.Column(db.Float,   nullable=False)    # actual weight (any karat)
    karat            = db.Column(db.Integer, default=24)         # NEW: karat of this lot
    pure_weight      = db.Column(db.Float,   nullable=False, default=0.0)  # NEW: 24K equivalent
    description      = db.Column(db.String(200))
    transaction_date = db.Column(db.Date, default=date.today)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)

    def compute_pure(self):
        """Call before add/commit to fill pure_weight automatically."""
        self.pure_weight = calc_pure_weight(self.weight, self.karat)

    def __repr__(self):
        return f'<Inventory {self.transaction_type} {self.weight}g @ {self.karat}K = {self.pure_weight}g pure>'


def get_inventory_balance(factory_id):
    """Return current raw-weight inventory balance for a factory."""
    from sqlalchemy import func
    gold_in = db.session.query(
        func.coalesce(func.sum(Inventory.weight), 0)
    ).filter_by(factory_id=factory_id, transaction_type='in').scalar() or 0.0
    gold_out = db.session.query(
        func.coalesce(func.sum(Inventory.weight), 0)
    ).filter_by(factory_id=factory_id, transaction_type='out').scalar() or 0.0
    return round(gold_in - gold_out, 4)
