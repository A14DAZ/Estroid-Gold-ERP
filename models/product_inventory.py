"""
ProductInventory — Finished products stock.
Completely separate from gold Inventory model.
"""
from datetime import datetime
from models.db import db


def get_product_total_weight(product):
    """
    Central calculation — single source of truth.
    per_unit : total = unit_weight × quantity
    bulk     : total = unit_weight  (IS the total, do NOT multiply)
    single   : total = unit_weight  (one piece, do NOT multiply)
    """
    mode = getattr(product, 'input_mode', None)
    uw   = float(product.unit_weight or 0)
    qty  = float(product.quantity    or 1)

    # Legacy records without input_mode: if qty==1 treat as single (safe default)
    if not mode:
        mode = 'per_unit' if qty > 1 else 'single'

    if mode == 'per_unit':
        return round(uw * qty, 6)
    else:  # bulk or single: unit_weight IS the total
        return round(uw, 6)


def to_24k(weight, karat):
    """Convert any weight to pure 24K equivalent."""
    return round(float(weight) * (float(karat or 24) / 24.0), 6)


class ProductInventory(db.Model):
    __tablename__ = 'product_inventory'

    id           = db.Column(db.Integer, primary_key=True)
    factory_id   = db.Column(db.Integer, db.ForeignKey('factory.id'),
                             nullable=False, index=True)
    product_name = db.Column(db.String(120), nullable=False, default='')
    product_code = db.Column(db.String(60),  nullable=False, default='')
    karat        = db.Column(db.Integer,  default=18, nullable=False)
    unit_weight  = db.Column(db.Float,   default=0.0, nullable=False)
    quantity     = db.Column(db.Float,   default=0.0, nullable=False)
    # Stored total_weight so we can increment correctly on upsert
    _total_weight = db.Column('total_weight', db.Float, default=0.0)
    # Source tracking: 'manual' | 'from_work_order'
    source_type  = db.Column(db.String(20), default='manual')
    # Input mode: 'per_unit' | 'bulk' | 'single'
    input_mode   = db.Column(db.String(20), default='per_unit')
    # Stored total weight (authoritative — set once on save, not recalculated)
    _stored_total = db.Column('stored_total', db.Float, default=0.0)
    # Optional stone fields
    has_stones   = db.Column(db.Boolean,  default=False)
    stone_type   = db.Column(db.String(80), default='')
    stone_weight = db.Column(db.Float,   default=0.0)   # grams
    # Gold used in 24K (deducted from gold inventory on manual add)
    gold_used_24 = db.Column(db.Float,   default=0.0)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow,
                             onupdate=datetime.utcnow)

    # ── Computed properties ───────────────────────────────────────

    @property
    def total_weight(self):
        return get_product_total_weight(self)

    @property
    def pure_24_weight(self):
        return round(get_product_total_weight(self) * ((self.karat or 24) / 24.0), 4)

    # ── Entry mode helpers ────────────────────────────────────────

    @staticmethod
    def resolve_weights(mode, unit_weight=0.0, total_weight=0.0,
                        quantity=1.0, weight=0.0):
        """
        Resolve (unit_weight, quantity) from any of the 3 entry modes.

        MODE 1 — per_unit:   unit_weight + quantity  → total auto
        MODE 2 — bulk:       total_weight + quantity → unit = total / qty
        MODE 3 — single:     weight                 → qty=1, unit=weight
        """
        qty = max(quantity or 1.0, 0.0001)  # prevent divide-by-zero

        if mode == 'single':
            return round(weight, 4), 1.0

        elif mode == 'bulk':
            tw  = round(total_weight, 4)
            uw  = round(tw / qty, 6) if qty > 0 else 0.0
            return uw, round(qty, 4)

        else:  # per_unit (default)
            uw = round(unit_weight, 4)
            return uw, round(qty, 4)

    # ── Upsert: match on product_code + karat ────────────────────

    @classmethod
    def upsert(cls, factory_id, product_code, product_name,
               karat, unit_weight, qty_to_add, source_type='manual',
               input_mode='per_unit',
               has_stones=False, stone_type='', stone_weight=0.0,
               gold_used_24=0.0):
        """
        Add qty_to_add units of a product.
        Merge key: product_code + karat.
        """
        existing = cls.query.filter_by(
            factory_id=factory_id,
            product_code=product_code,
            karat=karat,
        ).first()

        if existing:
            existing.quantity     = round((existing.quantity or 0.0) + qty_to_add, 4)
            existing.gold_used_24 = round((existing.gold_used_24 or 0.0) + gold_used_24, 6)
            existing.updated_at   = datetime.utcnow()
            if product_name:
                existing.product_name = product_name
            print(f"DB SAVE (UPDATE) → unit_weight: {existing.unit_weight}  quantity: {existing.quantity}")
            return existing
        else:
            record = cls(
                factory_id=factory_id,
                product_code=product_code,
                product_name=product_name,
                karat=karat,
                unit_weight=unit_weight,
                quantity=qty_to_add,
                source_type=source_type,
                input_mode=input_mode,
                has_stones=has_stones,
                stone_type=stone_type,
                stone_weight=stone_weight,
                gold_used_24=gold_used_24,
            )
            print(f"DB SAVE (INSERT) → unit_weight: {unit_weight}  quantity: {qty_to_add}")
            db.session.add(record)
            return record

    def __repr__(self):
        return (f'<Product {self.product_code} '
                f'{self.karat}K qty={self.quantity} '
                f'total={self.total_weight}g>')
