from datetime import datetime, date
from models.db import db
from models.order import calc_pure_weight


class InventoryTransaction(db.Model):
    """
    Dedicated inventory transaction table for sales/exports (OUT) and purchases/imports (IN).
    Separate from the legacy Inventory model — does NOT replace it.
    OUT = sales/export: includes price_per_gram, total_price, product info
    IN  = purchases:    includes supplier info
    """
    __tablename__ = 'inventory_transaction'

    id                  = db.Column(db.Integer, primary_key=True)
    factory_id          = db.Column(db.Integer, db.ForeignKey('factory.id'),
                                    nullable=False, index=True)
    customer_id         = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=True)
    transaction_type    = db.Column(db.String(10), nullable=False)   # IN | OUT
    karat               = db.Column(db.Integer, default=24)
    weight              = db.Column(db.Float, nullable=False)
    pure_weight         = db.Column(db.Float, default=0.0)
    # Sales fields (OUT)
    product_name        = db.Column(db.String(120), default='')
    product_code        = db.Column(db.String(60),  default='')
    price_per_gram      = db.Column(db.Float, default=0.0)
    total_price         = db.Column(db.Float, default=0.0)
    op_code             = db.Column(db.String(20),  default='')   # SAL-0001 / PUR-0001
    sale_type           = db.Column(db.String(20),  default='raw_gold')  # product | raw_gold
    is_return           = db.Column(db.Integer,      default=0)   # 1 = رجيع
    # Legacy labor fields (kept for backward compat)
    labor_per_gram      = db.Column(db.Float, default=0.0)
    labor_total         = db.Column(db.Float, default=0.0)
    description         = db.Column(db.String(250))
    transaction_date    = db.Column(db.Date, default=date.today)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)

    def compute(self):
        """Calculate derived fields before saving."""
        self.pure_weight = calc_pure_weight(self.weight, self.karat)
        self.labor_total = round((self.weight or 0.0) * (self.labor_per_gram or 0.0), 4)
        self.total_price = round((self.weight or 0.0) * (self.price_per_gram or 0.0), 4)

    def __repr__(self):
        return f'<InvTxn {self.transaction_type} {self.weight}g @ {self.karat}K>'
