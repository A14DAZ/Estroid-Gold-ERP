from flask import Blueprint, render_template, redirect, url_for, flash, request, g
from flask_login import login_required, current_user
from models.db import db
from models.product_inventory import ProductInventory, get_product_total_weight, to_24k
from models.order import KARATS, Inventory, get_inventory_balance
from datetime import date

prod_inv_bp = Blueprint('prod_inv', __name__)


def _factory():
    fac = current_user.factory
    if fac is None:
        from flask import abort
        abort(403)
    return fac


# ── List ──────────────────────────────────────────────────────────────────────

@prod_inv_bp.route('/')
@login_required
def list_products():
    fac      = _factory()
    products = ProductInventory.query.filter_by(factory_id=fac.id)\
                                     .order_by(ProductInventory.karat.desc(),
                                               ProductInventory.product_name).all()

    total_qty    = round(sum(p.quantity                   for p in products), 4)
    total_weight = round(sum(get_product_total_weight(p)  for p in products), 4)
    total_pure24 = round(sum(to_24k(get_product_total_weight(p), p.karat) for p in products), 4)

    # Karat breakdown
    karat_map = {}
    for p in products:
        k  = p.karat
        tw = get_product_total_weight(p)
        karat_map.setdefault(k, {'karat': k, 'qty': 0.0, 'weight': 0.0, 'pure24': 0.0})
        karat_map[k]['qty']    += p.quantity
        karat_map[k]['weight'] += tw
        karat_map[k]['pure24'] += to_24k(tw, k)
    karat_groups = sorted(karat_map.values(), key=lambda x: x['karat'], reverse=True)
    for g in karat_groups:
        g['qty']    = round(g['qty'],    4)
        g['weight'] = round(g['weight'], 4)
        g['pure24'] = round(g['pure24'], 6)

    return render_template('product_inventory.html',
                           fac=fac, products=products,
                           total_qty=total_qty,
                           total_weight=total_weight,
                           total_pure24=total_pure24,
                           karat_groups=karat_groups,
                           karats=KARATS)


# ── Add product — dedicated page (GET) ───────────────────────────────────────

@prod_inv_bp.route('/add-page')
@login_required
def add_product_page():
    fac     = _factory()
    balance = get_inventory_balance(fac.id)
    return render_template('product_add.html',
                           fac=fac, karats=KARATS, balance=balance)


# ── Add product — POST (from both list page form and dedicated page) ──────────

@prod_inv_bp.route('/add', methods=['POST'])
@login_required
def add_product():
    fac          = _factory()
    product_name = request.form.get('product_name', '').strip()
    product_code = request.form.get('product_code', '').strip()
    karat        = int(request.form.get('karat', 18))
    mode = request.form.get('input_mode') or request.form.get('entry_mode') or 'per_unit'

    # JS computes these before submit via the submit event listener
    uw  = float(request.form.get('resolved_unit_weight', 0) or 0)
    qty = float(request.form.get('resolved_quantity',    1) or 1)

    print(f"DEBUG UW:  {uw}")
    print(f"DEBUG QTY: {qty}")

    # Stone fields
    has_stones   = bool(request.form.get('has_stones'))
    stone_type   = request.form.get('stone_type', '').strip()
    stone_weight = float(request.form.get('stone_weight', 0) or 0)

    if not product_code:
        flash('كود المنتج مطلوب.', 'danger')
        return redirect(url_for('prod_inv.add_product_page'))
    if qty <= 0:
        flash('الكمية يجب أن تكون أكبر من الصفر.', 'danger')
        return redirect(url_for('prod_inv.add_product_page'))
    if uw <= 0:
        flash('وزن الوحدة يجب أن يكون أكبر من الصفر.', 'danger')
        return redirect(url_for('prod_inv.add_product_page'))

    # ── Calculate gold total ─────────────────────────────────────
    # bulk mode: uw IS the total (stored as-is) — do NOT multiply by qty
    if mode == 'bulk':
        gold_total = round(uw, 4)
    else:
        gold_total = round(uw * qty, 4)
    gold_used_24 = round(gold_total * (karat / 24.0), 6)

    print(f"DEBUG TOTAL: {gold_total}")

    # ── ADD to gold inventory (manual entry = incoming gold) ──────
    # Work order products are handled separately — no double count here.
    gold_txn = Inventory(
        factory_id=fac.id,
        transaction_type='in',                   # ← ADD (not deduct)
        weight=gold_total,
        karat=karat,
        description=f'من المنتجات — وارد (منتج يدوي): {product_code}',
        transaction_date=date.today(),
    )
    gold_txn.compute_pure()
    db.session.add(gold_txn)

    # ── Add to product inventory ──────────────────────────────────
    ProductInventory.upsert(
        factory_id=fac.id,
        product_code=product_code,
        product_name=product_name,
        karat=karat,
        unit_weight=uw,
        qty_to_add=qty,
        source_type='manual',
        input_mode=mode,
        has_stones=has_stones,
        stone_type=stone_type if has_stones else '',
        stone_weight=stone_weight if has_stones else 0.0,
        gold_used_24=gold_used_24,
    )
    db.session.commit()

    flash(
        f'تمت إضافة {product_name or product_code} — '
        f'{qty:.0f} وحدة × {uw:.3f}جم = {gold_total:.3f}جم ({karat}K) — '
        f'تم خصم الذهب من المخزون.',
        'success'
    )
    return redirect(url_for('prod_inv.list_products'))


# ── Analysis ──────────────────────────────────────────────────────────────────

@prod_inv_bp.route('/analysis')
@login_required
def analysis():
    fac      = _factory()
    products = ProductInventory.query.filter_by(factory_id=fac.id)\
                                     .order_by(ProductInventory.karat.desc()).all()

    # Group by karat — use get_product_total_weight for all calculations
    karat_map = {}
    for p in products:
        k  = p.karat
        tw = get_product_total_weight(p)
        karat_map.setdefault(k, {'karat': k, 'qty': 0.0,
                                  'weight': 0.0, 'pure24': 0.0})
        karat_map[k]['qty']    += p.quantity
        karat_map[k]['weight'] += tw
        karat_map[k]['pure24'] += to_24k(tw, k)
    karat_groups = sorted(karat_map.values(), key=lambda x: x['karat'], reverse=True)

    total_qty    = round(sum(p.quantity                    for p in products), 4)
    total_weight = round(sum(get_product_total_weight(p)   for p in products), 4)
    total_pure24 = round(sum(to_24k(get_product_total_weight(p), p.karat) for p in products), 4)

    return render_template('product_analysis.html',
                           fac=fac, products=products,
                           karat_groups=karat_groups,
                           total_qty=total_qty,
                           total_weight=total_weight,
                           total_pure24=total_pure24)


# ── Delete record ─────────────────────────────────────────────────────────────

@prod_inv_bp.route('/<int:pid>/delete', methods=['POST'])
@login_required
def delete_product(pid):
    fac     = _factory()
    product = ProductInventory.query.filter_by(id=pid, factory_id=fac.id).first_or_404()
    name    = product.product_name or product.product_code
    db.session.delete(product)
    db.session.commit()
    flash(f'تم حذف المنتج "{name}" من المخزون.', 'warning')
    return redirect(url_for('prod_inv.list_products'))
