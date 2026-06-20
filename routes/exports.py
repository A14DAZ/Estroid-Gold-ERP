from flask import Blueprint, render_template, redirect, url_for, flash, request, g
from flask_login import login_required, current_user
from datetime import date
from models.db import db
from models.customer import Customer
from models.transaction import InventoryTransaction
from models.order import Inventory, KARATS, get_inventory_balance
from models.product_inventory import ProductInventory, get_product_total_weight

exports_bp = Blueprint('exports', __name__)


def _factory():
    fac = current_user.factory
    if fac is None:
        from flask import abort
        abort(403)
    return fac


def _pure(weight, karat):
    return round(float(weight or 0) * (float(karat or 24) / 24.0), 6)


def _gen_op_code(factory_id, prefix):
    """Generate unique operation code: SAL-0001 or PUR-0001."""
    t_type = 'OUT' if prefix == 'SAL' else 'IN'
    count  = InventoryTransaction.query.filter_by(
        factory_id=factory_id, transaction_type=t_type
    ).count()
    return f'{prefix}-{(count + 1):04d}'


# ═══════════════════════════════════════════════════════
# SALES (OUT)
# ═══════════════════════════════════════════════════════

@exports_bp.route('/')
@login_required
def list_exports():
    fac  = _factory()
    txns = InventoryTransaction.query.filter_by(
        factory_id=fac.id, transaction_type='OUT'
    ).order_by(InventoryTransaction.id.desc()).all()

    count        = len(txns)
    total_pure24 = round(sum(_pure(t.weight, t.karat) for t in txns), 4)
    total_price  = round(sum(t.total_price for t in txns), 2)
    balance      = get_inventory_balance(fac.id)

    return render_template('exports.html',
                           fac=fac, txns=txns, count=count,
                           total_pure24=total_pure24, total_price=total_price,
                           balance=balance, today=date.today().isoformat())


@exports_bp.route('/new', methods=['GET', 'POST'])
@login_required
def create_export():
    fac       = _factory()
    customers = Customer.query.filter_by(factory_id=fac.id).order_by(Customer.name).all()
    products  = ProductInventory.query.filter_by(factory_id=fac.id)\
                                      .order_by(ProductInventory.product_name).all()
    balance   = get_inventory_balance(fac.id)

    def _render_form(**kw):
        return render_template('export_form.html', fac=fac, customers=customers,
                               products=products, karats=KARATS, balance=balance,
                               today=date.today().isoformat(), **kw)

    if request.method == 'POST':
        sale_type      = request.form.get('sale_type', 'raw_gold')
        customer_id    = request.form.get('customer_id') or None
        price_per_gram = float(request.form.get('price_per_gram', 0) or 0)

        # ── Customer is MANDATORY ─────────────────────────────────
        if not customer_id:
            flash('يجب اختيار عميل قبل إتمام عملية البيع.', 'danger')
            return _render_form()
        description    = request.form.get('description', '').strip()
        txn_date_str   = request.form.get('transaction_date', date.today().isoformat())

        if sale_type == 'product':
            product_id = request.form.get('product_id') or None
            product    = ProductInventory.query.filter_by(
                id=product_id, factory_id=fac.id).first() if product_id else None

            if not product:
                flash('يرجى اختيار منتج.', 'danger')
                return _render_form()

            karat        = product.karat
            product_name = product.product_name
            product_code = product.product_code
            mode         = product.input_mode or 'per_unit'

            if mode == 'bulk':
                # User enters weight to sell — qty for record only, not used in weight calc
                weight   = float(request.form.get('sell_weight', 0) or 0)
                sell_qty = float(request.form.get('sell_qty_bulk', 1) or 1)
                if weight <= 0:
                    flash('يرجى إدخال الوزن المراد بيعه.', 'danger')
                    return _render_form()
                # Reduce product quantity by 1 (bulk = one lot sold)
                product.quantity = max(round(product.quantity - 1, 4), 0)

            elif mode == 'single':
                # One piece — weight and qty auto from product
                weight   = get_product_total_weight(product)
                sell_qty = 1.0
                if product.quantity < 1:
                    flash('لا يوجد مخزون كافٍ من هذا المنتج.', 'danger')
                    return _render_form()
                product.quantity = max(round(product.quantity - 1, 4), 0)

            else:  # per_unit
                sell_qty = float(request.form.get('sell_qty', 1) or 1)
                unit_w   = get_product_total_weight(product) / max(product.quantity, 1)
                weight   = round(unit_w * sell_qty, 4)
                if sell_qty > product.quantity:
                    flash(f'الكمية المطلوبة ({sell_qty:.0f}) تتجاوز المتوفر ({product.quantity:.2f}).', 'danger')
                    return _render_form()
                product.quantity = round(product.quantity - sell_qty, 4)

        else:
            # ── Sell raw gold ─────────────────────────────────────
            weight       = float(request.form.get('weight', 0) or 0)
            karat        = int(request.form.get('karat', 24))
            product_name = request.form.get('product_name', '').strip()
            product_code = request.form.get('product_code', '').strip()

            if weight <= 0:
                flash('الوزن يجب أن يكون أكبر من الصفر.', 'danger')
                return _render_form()

            current_balance = get_inventory_balance(fac.id)
            if current_balance < weight:
                flash(f'المخزون غير كافي. الرصيد: {current_balance:.3f}جم، المطلوب: {weight:.3f}جم', 'danger')
                return _render_form()

        op_code = _gen_op_code(fac.id, 'SAL')

        txn = InventoryTransaction(
            factory_id=fac.id,
            customer_id=customer_id,
            transaction_type='OUT',
            karat=karat,
            weight=weight,
            price_per_gram=price_per_gram,
            product_name=product_name,
            product_code=product_code,
            op_code=op_code,
            sale_type=sale_type,
            description=description or f'بيع {"منتج" if sale_type=="product" else "ذهب"}: {product_name or ""}',
            transaction_date=date.fromisoformat(txn_date_str),
        )
        txn.compute()
        db.session.add(txn)

        # Deduct from gold inventory
        inv_out = Inventory(
            factory_id=fac.id,
            transaction_type='out',
            weight=weight,
            karat=karat,
            description=f'مبيعات/تصدير ({op_code}): {product_name or description or ""}',
            transaction_date=date.fromisoformat(txn_date_str),
        )
        inv_out.compute_pure()
        db.session.add(inv_out)
        db.session.commit()

        return redirect(url_for('exports.txn_detail', tid=txn.id))

    return _render_form()


@exports_bp.route('/result/<int:tid>')
@login_required
def export_result(tid):
    """Legacy redirect — kept for backward compat."""
    return redirect(url_for('exports.txn_detail', tid=tid))


@exports_bp.route('/invoice/<int:tid>')
@login_required
def txn_detail(tid):
    """Operation summary / invoice page — for both sales and purchases."""
    fac = _factory()
    txn = InventoryTransaction.query.filter_by(id=tid, factory_id=fac.id).first_or_404()
    return render_template('txn_detail.html', fac=fac, txn=txn)


# ═══════════════════════════════════════════════════════
# PURCHASES (IN)
# ═══════════════════════════════════════════════════════

@exports_bp.route('/purchases')
@login_required
def list_purchases():
    fac  = _factory()
    txns = InventoryTransaction.query.filter_by(
        factory_id=fac.id, transaction_type='IN'
    ).order_by(InventoryTransaction.id.desc()).all()

    count        = len(txns)
    total_pure24 = round(sum(_pure(t.weight, t.karat) for t in txns), 4)
    total_price  = round(sum(t.total_price for t in txns), 2)

    return render_template('purchases.html',
                           fac=fac, txns=txns, count=count,
                           total_pure24=total_pure24, total_price=total_price,
                           today=date.today().isoformat())


@exports_bp.route('/import/new', methods=['GET', 'POST'])
@login_required
def create_import():
    fac       = _factory()
    customers = Customer.query.filter_by(factory_id=fac.id).order_by(Customer.name).all()

    def _render_form(**kw):
        return render_template('import_form.html', fac=fac, customers=customers,
                               karats=KARATS, today=date.today().isoformat(), **kw)

    if request.method == 'POST':
        purchase_type  = request.form.get('purchase_type', 'raw_gold')
        customer_id    = request.form.get('customer_id') or None
        karat          = int(request.form.get('karat', 24))
        description    = request.form.get('description', '').strip()
        txn_date_str   = request.form.get('transaction_date', date.today().isoformat())
        price_per_gram = float(request.form.get('price_per_gram', 0) or 0)
        weight         = float(request.form.get('weight', 0) or 0)
        product_name   = request.form.get('product_name', '').strip()
        product_code   = request.form.get('product_code', '').strip()

        # ── 1. Customer is MANDATORY ──────────────────────────────
        if not customer_id:
            flash('يجب اختيار عميل / مورد قبل إتمام عملية الشراء.', 'danger')
            return _render_form()

        if purchase_type == 'product':
            # ── Purchase product from supplier → add to product inventory ──
            buy_qty    = float(request.form.get('buy_qty', 1) or 1)
            buy_karat  = int(request.form.get('buy_karat', karat))
            buy_weight = float(request.form.get('buy_weight', 0) or 0)   # total weight from scale

            if not product_code:
                flash('يرجى إدخال كود المنتج.', 'danger')
                return _render_form()
            if buy_weight <= 0:
                flash('الوزن يجب أن يكون أكبر من الصفر.', 'danger')
                return _render_form()

            weight  = buy_weight          # total weight stored as-is in transaction
            karat   = buy_karat

            # Store total_weight as unit_weight, qty = user input — bulk mode
            ProductInventory.upsert(
                factory_id=fac.id,
                product_code=product_code,
                product_name=product_name,
                karat=buy_karat,
                unit_weight=buy_weight,     # ← total weight AS-IS (not divided)
                qty_to_add=buy_qty,         # ← user's quantity (10 stays 10)
                source_type='manual',
                input_mode='bulk',          # ← bulk: get_product_total_weight returns uw directly
                gold_used_24=_pure(buy_weight, buy_karat),
            )

        if weight <= 0:
            flash('الوزن يجب أن يكون أكبر من الصفر.', 'danger')
            return _render_form()

        op_code = _gen_op_code(fac.id, 'PUR')

        txn = InventoryTransaction(
            factory_id=fac.id,
            customer_id=customer_id,
            transaction_type='IN',
            karat=karat,
            weight=weight,
            price_per_gram=price_per_gram,
            product_name=product_name,
            product_code=product_code,
            op_code=op_code,
            sale_type=purchase_type,
            labor_per_gram=0,
            description=description or f'شراء {"منتج" if purchase_type=="product" else "ذهب خام"}: {product_name or ""}',
            transaction_date=date.fromisoformat(txn_date_str),
        )
        txn.compute()
        db.session.add(txn)

        # Add to gold inventory
        inv_in = Inventory(
            factory_id=fac.id,
            transaction_type='in',
            weight=weight,
            karat=karat,
            description=f'مشتريات ({op_code}): {description or product_name or ""}',
            transaction_date=date.fromisoformat(txn_date_str),
        )
        inv_in.compute_pure()
        db.session.add(inv_in)
        db.session.commit()

        return redirect(url_for('exports.txn_detail', tid=txn.id))

    return _render_form()
