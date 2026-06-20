from models.db import db

CARAT_TO_GRAM = 0.2   # 1 carat = 0.2 grams
GRAM_TO_CARAT = 5.0   # 1 gram  = 5 carats

STONE_UNITS = ['carat', 'gram']


def to_grams(value, unit):
    """Convert stone weight to grams regardless of unit."""
    if unit == 'carat':
        return (value or 0.0) * CARAT_TO_GRAM
    return value or 0.0   # already grams


def to_carats(value, unit):
    """Convert stone weight to carats regardless of unit."""
    if unit == 'gram':
        return (value or 0.0) * GRAM_TO_CARAT
    return value or 0.0   # already carats


class Stone(db.Model):
    __tablename__ = 'stone'

    id         = db.Column(db.Integer, primary_key=True)
    stage_id   = db.Column(db.Integer, db.ForeignKey('production_stage.id'),
                           unique=True, nullable=False)

    stone_type  = db.Column(db.String(80))
    stone_color = db.Column(db.String(60))
    notes       = db.Column(db.Text)

    # ── unit used for all stone fields below ────────────────
    stone_unit  = db.Column(db.String(10), default='carat')  # 'carat' | 'gram'

    # ── Phase 1 (handover) ───────────────────────────────────
    # How many stones were REQUIRED for the job
    stone_required = db.Column(db.Float, default=0.0)
    # How many stones were GIVEN to the worker
    stone_given    = db.Column(db.Float, default=0.0)

    # ── Phase 2 (completion) ─────────────────────────────────
    # How many stones came BACK from the worker
    stone_returned   = db.Column(db.Float, default=0.0)
    # Calculated: extra_stone - returned (stones unaccounted for)
    stone_loss_carat = db.Column(db.Float, default=0.0)

    stage = db.relationship('ProductionStage', back_populates='stone')

    # ── Gram conversions (always in grams internally) ────────

    @property
    def stone_given_grams(self):
        return to_grams(self.stone_given, self.stone_unit)

    @property
    def stone_returned_grams(self):
        return to_grams(self.stone_returned, self.stone_unit)

    @property
    def stone_required_grams(self):
        return to_grams(self.stone_required, self.stone_unit)

    @property
    def stone_used_grams(self):
        """
        Grams of stone EMBEDDED in the product.
        Uses stone_required (not given-returned) — required is what's actually in the piece.
        """
        return self.stone_required_grams

    @property
    def stone_gain_grams(self):
        """Alias for stone_used_grams — the gold gain attributed to embedded stones."""
        return self.stone_required_grams

    @property
    def stone_used(self):
        """Stone used in original unit = required amount."""
        return self.stone_required or 0.0

    # ── Legacy compat (old code used weight_carats) ──────────
    @property
    def weight_grams(self):
        return self.stone_given_grams

    @property
    def returned_weight_grams(self):
        return self.stone_returned_grams

    # ── Helpers ───────────────────────────────────────────────
    @staticmethod
    def carats_to_grams(ct):
        return (ct or 0.0) * CARAT_TO_GRAM

    @staticmethod
    def grams_to_carats(g):
        return (g or 0.0) * GRAM_TO_CARAT

    def __repr__(self):
        return f'<Stone {self.stone_type} given={self.stone_given}{self.stone_unit}>'
