"""
routes/metals.py
Live Metal Prices — fetch, cache, serve.

External APIs used (both free, no key required by default):
  • Spot prices : https://api.metals.live/v1/spot
  • FX rates    : https://open.er-api.com/v6/latest/USD
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta

from flask import Blueprint, render_template, jsonify, g, request
from flask_login import login_required, current_user

from models.db import db
from models.metal_price import MetalPrice
from models.setting import AppSetting

metals_bp = Blueprint('metals', __name__)

# ── helpers ──────────────────────────────────────────────────────────────────

def _setting(key, default=''):
    row = AppSetting.query.filter_by(key=key).first()
    return (row.value or default) if row else default


def _fetch_spot_prices():
    """
    Try metals.live first; fall back to a simple calculation if it fails.
    Returns (gold_oz_usd, silver_oz_usd) or (None, None).
    """
    api_key = _setting('metals_api_key', '')
    source  = _setting('metals_api_source', 'metals.live')

    # ── metals.live (free, no key) ────────────────────────────
    if source in ('metals.live', ''):
        try:
            url = 'https://api.metals.live/v1/spot'
            req = urllib.request.Request(url, headers={'User-Agent': 'GoldERP/5'})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
            # response: [{"gold": 2034.5, "silver": 22.45, ...}]
            if isinstance(data, list) and data:
                row = data[0]
            elif isinstance(data, dict):
                row = data
            else:
                return None, None
            gold   = float(row.get('gold', 0)) or None
            silver = float(row.get('silver', 0)) or None
            return gold, silver
        except Exception as exc:
            print(f'  [Metals] metals.live error: {exc}')

    # ── metalpriceapi.com (paid, requires key) ────────────────
    if source == 'metalpriceapi.com' and api_key:
        try:
            url = f'https://api.metalpriceapi.com/v1/latest?api_key={api_key}&base=USD&currencies=XAU,XAG'
            req = urllib.request.Request(url, headers={'User-Agent': 'GoldERP/5'})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
            rates = data.get('rates', {})
            gold   = 1 / rates['XAU'] if rates.get('XAU') else None
            silver = 1 / rates['XAG'] if rates.get('XAG') else None
            return gold, silver
        except Exception as exc:
            print(f'  [Metals] metalpriceapi error: {exc}')

    return None, None


def _fetch_fx_rates():
    """
    Returns (usd_sar, usd_try). Falls back to fixed defaults if offline.
    """
    try:
        url = 'https://open.er-api.com/v6/latest/USD'
        req = urllib.request.Request(url, headers={'User-Agent': 'GoldERP/5'})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        rates = data.get('rates', {})
        return float(rates.get('SAR', 3.75)), float(rates.get('TRY', 32.0))
    except Exception:
        last = MetalPrice.query.order_by(MetalPrice.id.desc()).first()
        if last:
            return last.usd_sar, last.usd_try
        return 3.75, 32.0


def _refresh_prices():
    """
    Fetch fresh prices from external APIs, save to DB, return MetalPrice row.
    If external APIs fail, return the most recent saved row.
    """
    gold, silver = _fetch_spot_prices()
    sar, try_ = _fetch_fx_rates()

    if gold:
        karats = MetalPrice.calc_karats(gold)
        row = MetalPrice(
            gold_oz_usd   = round(gold, 2),
            silver_oz_usd = round(silver or 0, 4),
            usd_sar       = round(sar, 4),
            usd_try       = round(try_, 4),
            source        = _setting('metals_api_source', 'metals.live'),
            fetched_at    = datetime.utcnow(),
            gold_24k_usd  = karats['gold_24k'],
            gold_22k_usd  = karats['gold_22k'],
            gold_21k_usd  = karats['gold_21k'],
            gold_18k_usd  = karats['gold_18k'],
            silver_g_usd  = round((silver or 0) / 31.1035, 4),
        )
        db.session.add(row)
        db.session.commit()
        return row

    # fall back to cached
    return MetalPrice.query.order_by(MetalPrice.id.desc()).first()


def _get_current_price(force=False):
    """
    Return the latest MetalPrice row. Refresh if stale or forced.
    """
    if not force and _setting('metals_auto_refresh', 'true') != 'true':
        return MetalPrice.query.order_by(MetalPrice.id.desc()).first()

    try:
        interval = int(_setting('metals_refresh_interval', '1'))
    except ValueError:
        interval = 1

    last = MetalPrice.query.order_by(MetalPrice.id.desc()).first()
    if not force and last:
        age = datetime.utcnow() - last.fetched_at
        if age < timedelta(minutes=interval):
            return last          # still fresh

    return _refresh_prices()


# ── routes ────────────────────────────────────────────────────────────────────

@metals_bp.route('/')
@login_required
def metals_page():
    price = _get_current_price()
    history = MetalPrice.query.order_by(MetalPrice.id.desc()).limit(50).all()
    interval = int(_setting('metals_refresh_interval', '1'))
    auto     = _setting('metals_auto_refresh', 'true') == 'true'
    return render_template(
        'metals.html',
        price=price,
        history=history,
        refresh_interval=interval,
        auto_refresh=auto,
        t=g.t, lang=g.lang, dir=g.dir,
    )


@metals_bp.route('/api/prices')
def api_prices():
    """JSON endpoint — called by frontend JS every N minutes."""
    try:
        force = request.args.get('force') == '1'
        price = _get_current_price(force=force)
        if not price:
            return jsonify({'ok': False, 'error': 'no_data'}), 503
        d = price.to_dict()

        # compute previous price for direction arrow
        prev = MetalPrice.query.order_by(MetalPrice.id.desc()).offset(1).first()
        d['prev_gold_oz_usd'] = prev.gold_oz_usd if prev else d['gold_oz_usd']
        return jsonify({'ok': True, 'data': d})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@metals_bp.route('/api/history')
@login_required
def api_history():
    """Last 50 rows for charting."""
    rows = MetalPrice.query.order_by(MetalPrice.id.asc()).all()
    return jsonify([r.to_dict() for r in rows])


@metals_bp.route('/api/current-for-order')
@login_required
def api_current_for_order():
    """Lightweight JSON used by work-order / sales forms."""
    price = _get_current_price()
    if not price:
        return jsonify({'ok': False})
    d = price.to_dict()
    # Convert to SAR (most common for factory ops)
    sar = d['usd_sar']
    return jsonify({
        'ok': True,
        'gold_24k_sar': round(d['gold_24k_usd'] * sar, 2),
        'gold_22k_sar': round(d['gold_22k_usd'] * sar, 2),
        'gold_21k_sar': round(d['gold_21k_usd'] * sar, 2),
        'gold_18k_sar': round(d['gold_18k_usd'] * sar, 2),
        'silver_g_sar': round(d['silver_g_usd'] * sar, 4),
        'usd_sar':      sar,
        'fetched_at':   d['fetched_at'],
        'source':       d['source'],
    })
