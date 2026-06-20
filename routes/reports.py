from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from models.db import db
from models.order import WorkOrder
from models.worker import Worker
from models.production import ProductionStage
from datetime import date

reports_bp = Blueprint('reports', __name__)


def _factory():
    fac = current_user.factory
    if fac is None:
        from flask import abort
        abort(403)
    return fac


# ── Shared 24K helpers ────────────────────────────────────────────────────────

def fine(weight, karat):
    """Convert any weight to pure 24K equivalent. NEVER sum raw weights."""
    return round((weight or 0.0) * ((karat or 24) / 24.0), 6)


def stage_fine(stage, field):
    """Convert a stage weight field to 24K using the work order's karat."""
    val   = getattr(stage, field, 0) or 0.0
    karat = (stage.work_order.karat if stage.work_order else 24) or 24
    return fine(val, karat)


def stone_fine(stage):
    """
    Stone gain in 24K:
    stone_required_ct * 0.2 * (karat / 24)
    """
    if not stage.stone: return 0.0
    ct    = stage.stone.stone_required or 0.0
    karat = (stage.work_order.karat if stage.work_order else 24) or 24
    return round(ct * 0.2 * (karat / 24.0), 6)


# ── Index ─────────────────────────────────────────────────────────────────────

@reports_bp.route('/')
@login_required
def index():
    return render_template('reports.html', report=None, fac=_factory())


# ── 1. Work Orders Report ─────────────────────────────────────────────────────

@reports_bp.route('/orders')
@login_required
def orders_report():
    fac   = _factory()
    q     = request.args.get('q', '').strip()
    query = WorkOrder.query.filter_by(factory_id=fac.id)
    if q:
        query = query.filter(WorkOrder.order_number.ilike(f'%{q}%'))
    orders = query.order_by(WorkOrder.id.desc()).all()

    data = []
    for o in orders:
        k      = o.karat or 18
        stages = ProductionStage.query.filter_by(
            work_order_id=o.id, status='completed').all()

        # 24K conversions per stage
        loss_24       = sum(stage_fine(s, 'loss_weight')  for s in stages)
        scrap_24      = sum(stage_fine(s, 'scrap_weight') for s in stages)
        stone_gain_24 = sum(stone_fine(s)                 for s in stages)

        # Final output = LAST stage produced_weight × karat/24
        last = stages[-1] if stages else None
        output_24 = fine(last.produced_weight if last else 0.0, k)

        data.append({
            'order':        o,
            'stages':       stages,
            'output_24':    round(output_24,    4),
            'loss_24':      round(loss_24,      4),
            'scrap_24':     round(scrap_24,     4),
            'stone_gain_24':round(stone_gain_24,4),
        })

    grand_output_24    = round(sum(d['output_24']     for d in data), 4)
    grand_loss_24      = round(sum(d['loss_24']       for d in data), 4)
    grand_scrap_24     = round(sum(d['scrap_24']      for d in data), 4)
    grand_stone_24     = round(sum(d['stone_gain_24'] for d in data), 4)

    return render_template('reports.html', report='orders', fac=fac,
                           data=data, q=q,
                           grand_output_24=grand_output_24,
                           grand_loss_24=grand_loss_24,
                           grand_scrap_24=grand_scrap_24,
                           grand_stone_24=grand_stone_24,
                           today=date.today())


# ── 2. Workers Report ─────────────────────────────────────────────────────────

@reports_bp.route('/workers')
@login_required
def workers_report():
    fac     = _factory()
    workers = Worker.query.filter_by(factory_id=fac.id).all()
    data    = []

    for w in workers:
        stages = ProductionStage.query.filter_by(
            worker_id=w.id, status='completed').all()

        # 24K per stage (use work order karat for each stage)
        received_24   = sum(stage_fine(s, 'received_weight') for s in stages)
        produced_24   = sum(stage_fine(s, 'produced_weight') for s in stages)
        loss_24       = sum(stage_fine(s, 'loss_weight')     for s in stages)
        scrap_24      = sum(stage_fine(s, 'scrap_weight')    for s in stages)
        stone_gain_24 = sum(stone_fine(s)                    for s in stages)

        loss_pct = (loss_24 / received_24 * 100) if received_24 > 0 else 0.0

        data.append({
            'worker':       w,
            'stage_count':  len(stages),
            'received_24':  round(received_24,   4),
            'produced_24':  round(produced_24,   4),
            'loss_24':      round(loss_24,       4),
            'scrap_24':     round(scrap_24,      4),
            'stone_gain_24':round(stone_gain_24, 4),
            'loss_pct':     round(loss_pct,      2),
        })

    data.sort(key=lambda x: x['loss_24'])
    return render_template('reports.html', report='workers', fac=fac,
                           data=data, today=date.today())


# ── 3. Stages Report ──────────────────────────────────────────────────────────

@reports_bp.route('/stages')
@login_required
def stages_report():
    fac = _factory()
    from models.production import SETTING_STAGE_AR, SETTING_STAGE_EN

    all_stages = ProductionStage.query.filter_by(
        factory_id=fac.id, status='completed').all()

    agg = {}
    for s in all_stages:
        name = s.stage_name
        agg.setdefault(name, {
            'stage_name':   name,
            'count':        0,
            'received_24':  0.0,
            'produced_24':  0.0,
            'scrap_24':     0.0,
            'loss_24':      0.0,
            'stone_24':     0.0,
        })
        ag = agg[name]
        ag['count']       += 1
        ag['received_24'] += stage_fine(s, 'received_weight')
        ag['produced_24'] += stage_fine(s, 'produced_weight')
        ag['scrap_24']    += stage_fine(s, 'scrap_weight')
        ag['loss_24']     += stage_fine(s, 'loss_weight')
        if name in (SETTING_STAGE_AR, SETTING_STAGE_EN):
            ag['stone_24'] += stone_fine(s)

    rows = []
    for ag in agg.values():
        r24      = ag['received_24']
        loss_pct = (ag['loss_24'] / r24 * 100) if r24 > 0 else 0.0
        rows.append({
            'stage_name':  ag['stage_name'],
            'count':       ag['count'],
            'received_24': round(ag['received_24'], 4),
            'produced_24': round(ag['produced_24'], 4),
            'scrap_24':    round(ag['scrap_24'],    4),
            'loss_24':     round(ag['loss_24'],     4),
            'stone_24':    round(ag['stone_24'],    4),
            'loss_pct':    round(loss_pct,          2),
        })

    rows.sort(key=lambda x: x['loss_24'], reverse=True)
    return render_template('reports.html', report='stages', fac=fac,
                           data=rows, today=date.today())


# ── 4. Production Report ──────────────────────────────────────────────────────

@reports_bp.route('/production')
@login_required
def production_report():
    fac = _factory()

    completed_orders = WorkOrder.query.filter_by(
        factory_id=fac.id, status='completed').all()

    total_produced_24  = 0.0
    total_loss_24      = 0.0
    total_scrap_24     = 0.0
    total_stone_24     = 0.0
    order_rows         = []

    for o in completed_orders:
        k      = o.karat or 18
        stages = ProductionStage.query.filter_by(
            work_order_id=o.id, status='completed').all()

        # Final output = LAST stage only
        last       = stages[-1] if stages else None
        out_24     = fine(last.produced_weight if last else 0.0, k)
        loss_24    = sum(stage_fine(s, 'loss_weight')  for s in stages)
        scrap_24   = sum(stage_fine(s, 'scrap_weight') for s in stages)
        stone_24   = sum(stone_fine(s)                 for s in stages)

        total_produced_24 += out_24
        total_loss_24     += loss_24
        total_scrap_24    += scrap_24
        total_stone_24    += stone_24

        order_rows.append({
            'order':    o,
            'out_24':   round(out_24,   4),
            'loss_24':  round(loss_24,  4),
            'scrap_24': round(scrap_24, 4),
            'stone_24': round(stone_24, 4),
        })

    # Efficiency = produced / (produced + loss)
    denominator = total_produced_24 + total_loss_24
    efficiency  = round((total_produced_24 / denominator * 100), 2) if denominator > 0 else 0.0

    return render_template('reports.html', report='production', fac=fac,
                           total_produced_24=round(total_produced_24, 4),
                           total_loss_24=round(total_loss_24,     4),
                           total_scrap_24=round(total_scrap_24,   4),
                           total_stone_24=round(total_stone_24,   4),
                           efficiency=efficiency,
                           order_rows=order_rows,
                           today=date.today())


# ── 5. Inventory Report (DO NOT MODIFY — already correct) ────────────────────

@reports_bp.route('/inventory-report')
@login_required
def inventory_report():
    fac = _factory()
    from models.order import Inventory

    txns_all = Inventory.query.filter_by(factory_id=fac.id)\
                              .order_by(Inventory.id.desc()).all()

    def fine_inv(txn):
        if txn.pure_weight and txn.pure_weight > 0:
            return txn.pure_weight
        return round((txn.weight or 0.0) * ((txn.karat or 24) / 24.0), 6)

    total_in_24  = round(sum(fine_inv(t) for t in txns_all if t.transaction_type == 'in'),  4)
    total_out_24 = round(sum(fine_inv(t) for t in txns_all if t.transaction_type == 'out'), 4)
    balance_24   = round(total_in_24 - total_out_24, 4)
    gold_in      = round(sum(t.weight for t in txns_all if t.transaction_type == 'in'),  4)
    gold_out     = round(sum(t.weight for t in txns_all if t.transaction_type == 'out'), 4)

    def classify(txn):
        desc = (txn.description or '').lower()
        if txn.transaction_type == 'in':
            if any(k in desc for k in ('ناتج', 'output', 'return', 'اكتمل', 'completed')):
                return 'production_return'
            return 'purchase'
        else:
            if any(k in desc for k in ('تصدير', 'بيع', 'export', 'sale', 'customer')):
                return 'customer_sale'
            return 'work_order_issue'

    txns_enriched = [{'txn': t, 'fine_weight': round(fine_inv(t), 4),
                      'movement_class': classify(t)} for t in txns_all]

    karat_map = {}
    for t in txns_all:
        k = t.karat or 24
        karat_map.setdefault(k, {'karat': k, 'total_weight': 0.0, 'total_fine': 0.0,
                                  'in_weight': 0.0, 'in_fine': 0.0,
                                  'out_weight': 0.0, 'out_fine': 0.0})
        fw = fine_inv(t)
        karat_map[k]['total_weight'] += t.weight or 0.0
        karat_map[k]['total_fine']   += fw
        if t.transaction_type == 'in':
            karat_map[k]['in_weight'] += t.weight or 0.0
            karat_map[k]['in_fine']   += fw
        else:
            karat_map[k]['out_weight'] += t.weight or 0.0
            karat_map[k]['out_fine']   += fw

    karat_analysis = sorted(karat_map.values(), key=lambda x: x['karat'], reverse=True)
    for row in karat_analysis:
        for key in ('total_weight','total_fine','in_weight','in_fine','out_weight','out_fine'):
            row[key] = round(row[key], 4)

    return render_template('reports.html', report='inventory_report', fac=fac,
                           total_in_24=total_in_24, total_out_24=total_out_24,
                           balance_24=balance_24, gold_in=gold_in, gold_out=gold_out,
                           balance=round(gold_in - gold_out, 4),
                           txns=txns_enriched, karat_analysis=karat_analysis,
                           today=date.today())


# ── 6. Clients Report ─────────────────────────────────────────────────────────

@reports_bp.route('/customers-report')
@login_required
def customers_report():
    fac = _factory()
    from models.customer import Customer
    from models.transaction import InventoryTransaction

    customers = Customer.query.filter_by(factory_id=fac.id).all()
    data = []
    for c in customers:
        txns     = InventoryTransaction.query.filter_by(
            factory_id=fac.id, customer_id=c.id).all()
        txns_in  = [t for t in txns if t.transaction_type == 'IN']
        txns_out = [t for t in txns if t.transaction_type == 'OUT']

        # 24K: use stored pure_weight, fallback to live calc
        def fine_txn(t):
            if t.pure_weight and t.pure_weight > 0: return t.pure_weight
            return round((t.weight or 0.0) * ((t.karat or 24) / 24.0), 6)

        total_in_24  = round(sum(fine_txn(t) for t in txns_in),  4)
        total_out_24 = round(sum(fine_txn(t) for t in txns_out), 4)
        total_wages  = round(sum(t.total_price for t in txns_out), 2)
        balance_24   = round(total_in_24 - total_out_24, 4)

        data.append({
            'customer':    c,
            'total_in_24': total_in_24,
            'total_out_24':total_out_24,
            'balance_24':  balance_24,
            'total_wages': total_wages,
            'txn_count':   len(txns),
        })

    return render_template('reports.html', report='customers_report', fac=fac,
                           data=data, today=date.today())


# ── 7. Products Report ────────────────────────────────────────────────────────

@reports_bp.route('/products-report')
@login_required
def products_report():
    fac = _factory()
    from models.product_inventory import ProductInventory, get_product_total_weight
    from datetime import date as date_cls

    # Date filter
    date_from = request.args.get('date_from', '')
    date_to   = request.args.get('date_to',   '')

    products = ProductInventory.query.filter_by(factory_id=fac.id)\
                                     .order_by(ProductInventory.product_name).all()

    rows = []
    for p in products:
        total_w = get_product_total_weight(p)
        pure_24 = round(total_w * (p.karat / 24.0), 4)
        rows.append({
            'product':  p,
            'total_w':  round(total_w, 4),
            'pure_24':  pure_24,
            'mode':     p.input_mode or 'per_unit',
        })

    grand_w   = round(sum(r['total_w']  for r in rows), 4)
    grand_24  = round(sum(r['pure_24']  for r in rows), 4)
    grand_qty = round(sum(r['product'].quantity for r in rows), 2)

    return render_template('reports.html', report='products_report', fac=fac,
                           rows=rows,
                           grand_w=grand_w, grand_24=grand_24, grand_qty=grand_qty,
                           today=date_cls.today())


# ── 8. Purchases Report ───────────────────────────────────────────────────────

@reports_bp.route('/purchases-report')
@login_required
def purchases_report():
    fac = _factory()
    from models.transaction import InventoryTransaction
    from datetime import date as date_cls, datetime

    date_from_str = request.args.get('date_from', '')
    date_to_str   = request.args.get('date_to',   '')

    query = InventoryTransaction.query.filter_by(
        factory_id=fac.id, transaction_type='IN'
    ).filter_by(is_return=0).order_by(InventoryTransaction.id.desc())

    txns = query.all()

    # Date filter (client side label — backend slice if provided)
    if date_from_str:
        try:
            df = date_cls.fromisoformat(date_from_str)
            txns = [t for t in txns if t.transaction_date and t.transaction_date >= df]
        except Exception:
            pass
    if date_to_str:
        try:
            dt = date_cls.fromisoformat(date_to_str)
            txns = [t for t in txns if t.transaction_date and t.transaction_date <= dt]
        except Exception:
            pass

    def fine_txn(t):
        if t.pure_weight and t.pure_weight > 0: return t.pure_weight
        return round((t.weight or 0.0) * ((t.karat or 24) / 24.0), 6)

    total_weight_24 = round(sum(fine_txn(t) for t in txns), 4)
    total_price     = round(sum(t.total_price or 0 for t in txns), 2)

    return render_template('reports.html', report='purchases_report', fac=fac,
                           txns=txns,
                           total_weight_24=total_weight_24,
                           total_price=total_price,
                           fine_txn=fine_txn,
                           date_from=date_from_str, date_to=date_to_str,
                           today=date_cls.today())


# ── 9. Sales Report ───────────────────────────────────────────────────────────

@reports_bp.route('/sales-report')
@login_required
def sales_report():
    fac = _factory()
    from models.transaction import InventoryTransaction
    from datetime import date as date_cls

    date_from_str = request.args.get('date_from', '')
    date_to_str   = request.args.get('date_to',   '')

    query = InventoryTransaction.query.filter_by(
        factory_id=fac.id, transaction_type='OUT'
    ).filter_by(is_return=0).order_by(InventoryTransaction.id.desc())

    txns = query.all()

    if date_from_str:
        try:
            df = date_cls.fromisoformat(date_from_str)
            txns = [t for t in txns if t.transaction_date and t.transaction_date >= df]
        except Exception:
            pass
    if date_to_str:
        try:
            dt = date_cls.fromisoformat(date_to_str)
            txns = [t for t in txns if t.transaction_date and t.transaction_date <= dt]
        except Exception:
            pass

    def fine_txn(t):
        if t.pure_weight and t.pure_weight > 0: return t.pure_weight
        return round((t.weight or 0.0) * ((t.karat or 24) / 24.0), 6)

    total_weight_24 = round(sum(fine_txn(t) for t in txns), 4)
    total_price     = round(sum(t.total_price or 0 for t in txns), 2)

    return render_template('reports.html', report='sales_report', fac=fac,
                           txns=txns,
                           total_weight_24=total_weight_24,
                           total_price=total_price,
                           fine_txn=fine_txn,
                           date_from=date_from_str, date_to=date_to_str,
                           today=date_cls.today())


# ── 10. Archive ───────────────────────────────────────────────────────────────

@reports_bp.route('/archive')
@login_required
def archive_index():
    """List all archive periods for this factory."""
    from models.archive import ReportArchive
    fac      = _factory()
    archives = ReportArchive.query.filter_by(factory_id=fac.id)\
                                  .order_by(ReportArchive.created_at.desc()).all()
    return render_template('reports.html', report='archive_index', fac=fac,
                           archives=archives, today=date.today())


@reports_bp.route('/archive/create', methods=['POST'])
@login_required
def create_archive():
    """
    Archive current period:
    1) Verify password
    2) Snapshot counts + totals into ReportArchive
    3) Data stays intact — only a snapshot record is created
    """
    from models.archive import ReportArchive
    from models.transaction import InventoryTransaction
    from models.order import Inventory
    import json

    fac      = _factory()
    password = request.form.get('archive_password', '')
    if not current_user.check_password(password):
        from flask import flash, redirect, url_for
        from flask import flash
        flash('كلمة المرور غير صحيحة — لم يتم الأرشفة.', 'danger')
        return redirect(url_for('reports.archive_index'))

    label      = request.form.get('archive_label', '').strip() or f'أرشيف {date.today()}'
    date_from  = request.form.get('arch_date_from', '') or None
    date_to    = request.form.get('arch_date_to',   '') or None

    # Snapshot purchases
    pur = InventoryTransaction.query.filter_by(
        factory_id=fac.id, transaction_type='IN', is_return=0).all()
    sal = InventoryTransaction.query.filter_by(
        factory_id=fac.id, transaction_type='OUT', is_return=0).all()
    inv = Inventory.query.filter_by(factory_id=fac.id).all()

    def fine_t(t):
        return round((t.weight or 0) * ((t.karat or 24) / 24.0), 6)

    snapshot = {
        'purchases_count': len(pur),
        'purchases_24':    round(sum(fine_t(t) for t in pur), 4),
        'purchases_value': round(sum(t.total_price or 0 for t in pur), 2),
        'sales_count':     len(sal),
        'sales_24':        round(sum(fine_t(t) for t in sal), 4),
        'sales_value':     round(sum(t.total_price or 0 for t in sal), 2),
        'inventory_in':    round(sum((r.weight or 0) for r in inv if r.transaction_type=='in'), 4),
        'inventory_out':   round(sum((r.weight or 0) for r in inv if r.transaction_type=='out'), 4),
    }

    arch = ReportArchive(
        factory_id=fac.id,
        label=label,
        date_from=date_from,
        date_to=date_to,
        snapshot_json=json.dumps(snapshot, ensure_ascii=False),
    )
    from models.db import db
    db.session.add(arch)
    db.session.commit()

    from flask import flash, redirect, url_for
    flash(f'تم الأرشفة: {label}', 'success')
    return redirect(url_for('reports.archive_index'))


@reports_bp.route('/archive/<int:aid>')
@login_required
def archive_view(aid):
    from models.archive import ReportArchive
    import json
    fac  = _factory()
    arch = ReportArchive.query.filter_by(id=aid, factory_id=fac.id).first_or_404()
    snap = json.loads(arch.snapshot_json or '{}')
    return render_template('reports.html', report='archive_view', fac=fac,
                           arch=arch, snap=snap, today=date.today())
