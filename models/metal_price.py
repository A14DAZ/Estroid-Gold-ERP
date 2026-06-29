from datetime import datetime
from .db import db

TROY_OZ_TO_GRAMS = 31.1035  # 1 troy ounce = 31.1035 grams


class MetalPrice(db.Model):
    __tablename__ = 'metal_price'

    id            = db.Column(db.Integer, primary_key=True)
    gold_oz_usd   = db.Column(db.Float, nullable=True)   # spot price per troy oz
    silver_oz_usd = db.Column(db.Float, nullable=True)
    usd_sar       = db.Column(db.Float, default=3.75)
    usd_try       = db.Column(db.Float, default=32.0)
    source        = db.Column(db.String(100), default='metals.live')
    fetched_at    = db.Column(db.DateTime, default=datetime.utcnow)

    # Calculated per-gram prices (USD) stored for history
    gold_24k_usd  = db.Column(db.Float, nullable=True)
    gold_22k_usd  = db.Column(db.Float, nullable=True)
    gold_21k_usd  = db.Column(db.Float, nullable=True)
    gold_18k_usd  = db.Column(db.Float, nullable=True)
    silver_g_usd  = db.Column(db.Float, nullable=True)

    @staticmethod
    def calc_karats(gold_oz_usd):
        """Return dict of per-gram prices for each karat."""
        g24 = gold_oz_usd / TROY_OZ_TO_GRAMS
        return {
            'gold_24k': round(g24, 4),
            'gold_22k': round(g24 * 22 / 24, 4),
            'gold_21k': round(g24 * 21 / 24, 4),
            'gold_18k': round(g24 * 18 / 24, 4),
        }

    def to_dict(self):
        g24 = (self.gold_oz_usd / TROY_OZ_TO_GRAMS) if self.gold_oz_usd else 0
        s_g = (self.silver_oz_usd / TROY_OZ_TO_GRAMS) if self.silver_oz_usd else 0
        return {
            'gold_oz_usd':   round(self.gold_oz_usd or 0, 2),
            'silver_oz_usd': round(self.silver_oz_usd or 0, 4),
            'gold_24k_usd':  round(g24, 4),
            'gold_22k_usd':  round(g24 * 22 / 24, 4),
            'gold_21k_usd':  round(g24 * 21 / 24, 4),
            'gold_18k_usd':  round(g24 * 18 / 24, 4),
            'silver_g_usd':  round(s_g, 4),
            'usd_sar':       self.usd_sar or 3.75,
            'usd_try':       self.usd_try or 32.0,
            'source':        self.source,
            'fetched_at':    self.fetched_at.strftime('%Y-%m-%d %H:%M UTC') if self.fetched_at else '',
            'fetched_ts':    int(self.fetched_at.timestamp()) if self.fetched_at else 0,
        }
