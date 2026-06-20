from datetime import datetime
from models.db import db

STAGE_NAMES_AR = [
    'صب',
    'كاستنيج',
    'سحب',
    'ليزر',
    'مغنتك',
    'درام',
    'تشبيك ولحام (مودل)',
    'تركيب الأحجار',
    'تلميع',
    'ترميل وشبنم',
    'نقش',
]
STAGE_NAMES_EN = STAGE_NAMES_AR   # Arabic only — no English translation needed
SETTING_STAGE_AR = 'تركيب الأحجار'
SETTING_STAGE_EN = 'تركيب الأحجار'

STAGE_ICONS = {
    'صب':                   'fa-fire',
    'كاستنيج':              'fa-cube',
    'سحب':                  'fa-arrows-alt-h',
    'ليزر':                 'fa-bolt',
    'مغنتك':                'fa-magnet',
    'درام':                 'fa-drum',
    'تشبيك ولحام (مودل)':  'fa-tools',
    'تركيب الأحجار':        'fa-gem',
    'تلميع':                'fa-star',
    'ترميل وشبنم':          'fa-wind',
    'نقش':                  'fa-pen-nib',
    # Legacy names (for existing DB records)
    'ليزر':       'fa-bolt',
    'لحام':       'fa-tools',
    'مغناطيس':   'fa-magnet',
    'Casting':    'fa-fire',
    'Laser':      'fa-bolt',
    'Soldering':  'fa-tools',
    'Magnet':     'fa-magnet',
    'Setting':    'fa-gem',
    'Polishing':  'fa-star',
    'Engraving':  'fa-pen-nib',
}


class ProductionStage(db.Model):
    __tablename__ = 'production_stage'

    id            = db.Column(db.Integer, primary_key=True)
    factory_id    = db.Column(db.Integer, db.ForeignKey('factory.id'), nullable=False, index=True)
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_order.id'), nullable=False)
    worker_id     = db.Column(db.Integer, db.ForeignKey('worker.id'), nullable=True)
    stage_name    = db.Column(db.String(60), nullable=False)

    # ── Phase 1 — Handover ───────────────────────────────────
    received_weight = db.Column(db.Float, default=0.0)   # gold only
    handover_date   = db.Column(db.DateTime)

    # ── Phase 2 — Completion ─────────────────────────────────
    produced_weight = db.Column(db.Float, default=0.0)   # gold output
    scrap_weight    = db.Column(db.Float, default=0.0)   # gold scrap returned

    # Calculated fields (gold only — stones tracked separately in Stone)
    loss_weight        = db.Column(db.Float, default=0.0)
    gold_gain          = db.Column(db.Float, default=0.0)
    loss_percentage    = db.Column(db.Float, default=0.0)
    # Setting-stage specific: gold inside the final product after removing stone weight
    gold_actual_output = db.Column(db.Float, default=0.0)
    is_loss_high       = db.Column(db.Boolean, default=False)

    completion_date = db.Column(db.DateTime)
    status          = db.Column(db.String(20), default='in_progress')  # in_progress | completed
    notes           = db.Column(db.Text)

    stone = db.relationship('Stone', back_populates='stage', uselist=False,
                             cascade='all,delete')

    def calculate_loss(self):
        """
        Unified gold loss formula for ALL stages.

        NON-SETTING stages:
          VALIDATION: scrap >= 0
          loss = received - produced - scrap
          gold_actual_output = produced  (no stones)

        SETTING stage (تركيب الأحجار):
          VALIDATION: scrap >= 0
          stone_gain_g   = stone_required * 0.2   ← REQUIRED only (embedded weight)
          gold_actual_output = produced - stone_gain_g
          loss = received - gold_actual_output - scrap

          Stone loss (unaccounted stones):
            extra_stone    = stone_given - stone_required
            stone_loss_ct  = max(extra_stone - stone_returned, 0)

        CONSTANTS:
          CARAT_TO_GRAM      = 0.2
          LOSS_ALERT_PERCENT = 2
        """
        CARAT_TO_GRAM      = 0.2
        LOSS_ALERT_PERCENT = 2.0

        received = self.received_weight or 0.0
        produced = self.produced_weight or 0.0
        scrap    = self.scrap_weight    or 0.0

        # ── VALIDATION: scrap cannot be negative (all stages) ──
        if scrap < 0:
            raise ValueError(
                'رجيع الذهب لا يمكن أن يكون سالباً / Gold scrap cannot be negative'
            )

        is_setting = self.stage_name in (SETTING_STAGE_AR, SETTING_STAGE_EN)

        if is_setting and self.stone:
            stone_required_ct = self.stone.stone_required or 0.0
            stone_given_ct    = self.stone.stone_given    or 0.0
            stone_returned_ct = self.stone.stone_returned or 0.0

            # ── Stone loss (unaccounted) ───────────────────────────
            extra_stone_ct   = stone_given_ct - stone_required_ct
            stone_loss_carat = max(extra_stone_ct - stone_returned_ct, 0.0)
            self.stone.stone_loss_carat = stone_loss_carat

            # ── Stone gain = REQUIRED stones embedded in product ──
            # (NOT given-returned — required is the real embedded weight)
            stone_gain_g = stone_required_ct * CARAT_TO_GRAM

            # ── Gold accounting ────────────────────────────────────
            gold_actual_output = produced - stone_gain_g
            self.gold_actual_output = gold_actual_output

            raw = received - gold_actual_output - scrap
        else:
            # ── All other stages: simple formula ──────────────────
            raw = received - produced - scrap
            self.gold_actual_output = produced   # no stones embedded

        if raw < 0:
            self.gold_gain   = abs(raw)
            self.loss_weight = 0.0
        else:
            self.gold_gain   = 0.0
            self.loss_weight = raw

        denom = received if received > 0 else 1.0
        self.loss_percentage = (self.loss_weight / denom) * 100
        self.is_loss_high    = self.loss_percentage > LOSS_ALERT_PERCENT

    # ── Convenience properties ────────────────────────────────

    @property
    def stone_used_grams(self):
        """Grams of stone embedded in product = stone_required_grams (not given-returned)."""
        if self.stone:
            return self.stone.stone_required_grams
        return 0.0

    def __repr__(self):
        return f'<Stage {self.stage_name} order={self.work_order_id}>'
