from flask import Blueprint, render_template, redirect, url_for, flash, request, g
from flask_login import login_required, current_user
from datetime import date
from models.db import db
from models.customer import Customer, CUSTOMER_TYPES
from models.transaction import InventoryTransaction
from models.order import Inventory, KARATS, get_inventory_balance

customers_bp = Blueprint('customers', __name__)


def _factory():
    fac = current_user.factory
    if fac is None:
        from flask import abort
        abort(403)
    return fac


def _fine(t):
    if t.pure_weight and t.pure_weight > 0:
        return t.pure_weight
    return round((t.weight or 0.0) * ((t.karat or 24) / 24.0), 6)


def _gen_return_code(factory_id):
    count = InventoryTransaction.query.filter_by(
        factory_id=factory_id, is_return=1).count()
    return f'RET-{(count + 1):04d}'


def _gen_customer_code(factory_id):
    """Generate unique CUST-0001 code for new customer."""
    count = Customer.query.filter_by(factory_id=factory_id).count()
    return f'CUST-{(count + 1):04d}'


def _customer_stats(cid, factory_id):
    """Return dict of totals for one customer — used by list + analysis."""
    txns         = InventoryTransaction.query.filter_by(
        factory_id=factory_id, customer_id=cid).all()
    outs         = [t for t in txns if t.transaction_type == 'OUT' and not t.is_return]
    ins          = [t for t in txns if t.transaction_type == 'IN'  and not t.is_return]
    returns      = [t for t in txns if t.is_return]
    total_sent   = round(sum(_fine(t) for t in outs),    4)   # gold sent to customer
    total_recv   = round(sum(_fine(t) for t in ins),     4)   # gold received from customer
    total_ret    = round(sum(_fine(t) for t in returns), 4)   # returns
    total_wages  = round(sum(t.total_price for t in outs), 2)
    balance      = round(total_sent - total_recv - total_ret, 4)  # positive = owes
    return {
        'total_sent':  total_sent,
        'total_recv':  total_recv,
        'total_ret':   total_ret,
        'total_wages': total_wages,
        'balance':     balance,
        'txn_count':   len(txns),
        'last_txn':    max((t.transaction_date for t in txns), default=None),
    }


# ── List ──────────────────────────────────────────────────────────────────────

@customers_bp.route('/')
@login_required
def list_customers():
    fac  = _factory()
    q    = request.args.get('q', '').strip()
    query = Customer.query.filter_by(factory_id=fac.id)
    if q:
        query = query.filter(
            db.or_(Customer.name.ilike(f'%{q}%'),
                   Customer.phone.ilike(f'%{q}%'),
                   Customer.code.ilike(f'%{q}%')))
    customers = query.order_by(Customer.name).all()
    stats     = {c.id: _customer_stats(c.id, fac.id) for c in customers}
    return render_template('customers.html',
                           customers=customers, fac=fac, q=q, stats=stats)


# ── Add Customer — dedicated page ────────────────────────────────────────────

@customers_bp.route('/new', methods=['GET', 'POST'])
@login_required
def create_customer():
    fac = _factory()
    if request.method == 'POST':
        name  = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        notes = request.form.get('notes', '').strip()
        if not name:
            flash('الاسم مطلوب.', 'danger')
            code = _gen_customer_code(fac.id)
            return render_template('customer_form.html', fac=fac,
                                   customer=None, next_code=code)
        code = _gen_customer_code(fac.id)
        c = Customer(factory_id=fac.id, name=name, phone=phone,
                     notes=notes, code=code, type='client')
        db.session.add(c)
        db.session.commit()
        flash(f'تم إضافة العميل: {name} ({code})', 'success')
        return redirect(url_for('customers.list_customers'))
    next_code = _gen_customer_code(fac.id)
    return render_template('customer_form.html', fac=fac,
                           customer=None, next_code=next_code)


# ── Customers Analysis ────────────────────────────────────────────────────────

@customers_bp.route('/analysis')
@login_required
def analysis():
    fac       = _factory()
    customers = Customer.query.filter_by(factory_id=fac.id).order_by(Customer.name).all()
    rows      = []
    for c in customers:
        s = _customer_stats(c.id, fac.id)
        rows.append({'customer': c, **s})

    # Aggregates
    total_customers  = len(customers)
    total_sent_all   = round(sum(r['total_sent']  for r in rows), 4)
    total_recv_all   = round(sum(r['total_recv']  for r in rows), 4)
    total_ret_all    = round(sum(r['total_ret']   for r in rows), 4)
    net_balance_all  = round(sum(r['balance']     for r in rows), 4)

    # Top 5 by highest sent + highest balance
    top_by_sent    = sorted(rows, key=lambda x: x['total_sent'],  reverse=True)[:5]
    top_by_balance = sorted(rows, key=lambda x: x['balance'],     reverse=True)[:5]

    return render_template('customers_analysis.html',
                           fac=fac, rows=rows,
                           total_customers=total_customers,
                           total_sent_all=total_sent_all,
                           total_recv_all=total_recv_all,
                           total_ret_all=total_ret_all,
                           net_balance_all=net_balance_all,
                           top_by_sent=top_by_sent,
                           top_by_balance=top_by_balance)


# ── Edit ──────────────────────────────────────────────────────────────────────

@customers_bp.route('/<int:cid>/edit', methods=['GET', 'POST'])
@login_required
def edit_customer(cid):
    fac = _factory()
    c   = Customer.query.filter_by(id=cid, factory_id=fac.id).first_or_404()
    if request.method == 'POST':
        c.name  = request.form.get('name', c.name).strip()
        c.phone = request.form.get('phone', '').strip()
        c.type  = request.form.get('type', c.type)
        c.notes = request.form.get('notes', '').strip()
        db.session.commit()
        flash('تم الحفظ.', 'success')
        return redirect(url_for('customers.list_customers'))
    return render_template('customer_form.html', fac=fac, customer=c, types=CUSTOMER_TYPES)


# ── Delete ────────────────────────────────────────────────────────────────────

@customers_bp.route('/<int:cid>/delete', methods=['POST'])
@login_required
def delete_customer(cid):
    fac = _factory()
    c   = Customer.query.filter_by(id=cid, factory_id=fac.id).first_or_404()
    db.session.delete(c)
    db.session.commit()
    flash('تم الحذف.', 'warning')
    return redirect(url_for('customers.list_customers'))


# ── Customer Profile ──────────────────────────────────────────────────────────

@customers_bp.route('/<int:cid>/profile')
@login_required
def customer_profile(cid):
    fac = _factory()
    c   = Customer.query.filter_by(id=cid, factory_id=fac.id).first_or_404()
    tab = request.args.get('tab', 'statement')   # statement | returns | analysis

    txns = InventoryTransaction.query.filter_by(
        factory_id=fac.id, customer_id=cid
    ).order_by(InventoryTransaction.transaction_date.desc(),
               InventoryTransaction.id.desc()).all()

    txns_in      = [t for t in txns if t.transaction_type == 'IN'  and not t.is_return]
    txns_out     = [t for t in txns if t.transaction_type == 'OUT' and not t.is_return]
    txns_returns = [t for t in txns if t.is_return]

    total_in_weight  = round(sum(t.weight  for t in txns_in),       4)
    total_in_pure24  = round(sum(_fine(t)  for t in txns_in),       4)
    total_out_weight = round(sum(t.weight  for t in txns_out),      4)
    total_out_pure24 = round(sum(_fine(t)  for t in txns_out),      4)
    total_ret_weight = round(sum(t.weight  for t in txns_returns),  4)
    total_ret_pure24 = round(sum(_fine(t)  for t in txns_returns),  4)
    total_wages      = round(sum(t.total_price for t in txns_out),  2)
    # Net: IN + RETURN - OUT  (in 24K)
    net_balance = round(total_in_pure24 + total_ret_pure24 - total_out_pure24, 4)

    # Per-karat breakdown for analysis tab
    all_karats = sorted(set(
        [t.karat for t in txns_in + txns_out + txns_returns]
    ), reverse=True)
    karat_analysis = []
    for k in all_karats:
        ki  = [t for t in txns_in      if t.karat == k]
        ko  = [t for t in txns_out     if t.karat == k]
        kr  = [t for t in txns_returns if t.karat == k]
        karat_analysis.append({
            'karat':    k,
            'in_24':    round(sum(_fine(t) for t in ki), 4),
            'out_24':   round(sum(_fine(t) for t in ko), 4),
            'ret_24':   round(sum(_fine(t) for t in kr), 4),
            'net':      round(
                sum(_fine(t) for t in ki) + sum(_fine(t) for t in kr)
                - sum(_fine(t) for t in ko), 4),
        })

    # Allowed return for UI display
    all_txns_flat   = InventoryTransaction.query.filter_by(factory_id=fac.id, customer_id=cid).all()
    _total_sold     = round(sum(t.weight for t in all_txns_flat
                                if t.transaction_type == 'OUT' and not t.is_return), 4)
    _total_returned = round(sum(t.weight for t in all_txns_flat if t.is_return), 4)
    allowed_return  = round(_total_sold - _total_returned, 4)

    balance = get_inventory_balance(fac.id)

    # Prior sales for return dropdown — only non-return OUT transactions
    prior_sales = [t for t in txns_out]   # already filtered in txns_out

    return render_template('customer_profile.html',
                           fac=fac, customer=c,
                           tab=tab,
                           txns=txns,
                           txns_in=txns_in,
                           txns_out=txns_out,
                           txns_returns=txns_returns,
                           prior_sales=prior_sales,
                           total_in_weight=total_in_weight,
                           total_in_pure24=total_in_pure24,
                           total_out_weight=total_out_weight,
                           total_out_pure24=total_out_pure24,
                           total_ret_weight=total_ret_weight,
                           total_ret_pure24=total_ret_pure24,
                           total_wages=total_wages,
                           net_balance=net_balance,
                           karat_analysis=karat_analysis,
                           karats=KARATS,
                           balance=balance,
                           allowed_return=allowed_return,
                           today=date.today().isoformat())


# ── Add Return (رجيع) ─────────────────────────────────────────────────────────

@customers_bp.route('/<int:cid>/return', methods=['POST'])
@login_required
def add_return(cid):
    """
    Return from customer → adds gold BACK to inventory.
    REQUIRES at least one prior OUT transaction for this customer.
    """
    fac = _factory()
    c   = Customer.query.filter_by(id=cid, factory_id=fac.id).first_or_404()

    # ── Issue 2: BLOCK if no prior outgoing transactions ──────────
    prior_sales = InventoryTransaction.query.filter_by(
        factory_id=fac.id, customer_id=cid, transaction_type='OUT'
    ).filter_by(is_return=0).all()

    if not prior_sales:
        flash('لا يمكن تسجيل رجيع — لا يوجد عمليات صادرة لهذا العميل.', 'danger')
        return redirect(url_for('customers.customer_profile', cid=cid, tab='returns'))

    # ── Issue 3: Return MUST be linked to a specific sale ─────────
    ref_txn_id   = request.form.get('ref_txn_id') or None
    ref_txn      = None
    if ref_txn_id:
        ref_txn = InventoryTransaction.query.filter_by(
            id=ref_txn_id, factory_id=fac.id, customer_id=cid
        ).first()

    weight       = float(request.form.get('ret_weight', 0) or 0)
    karat        = int(request.form.get('ret_karat', 18))
    desc         = request.form.get('ret_description', '').strip()
    txn_date_str = request.form.get('ret_date', date.today().isoformat())
    product_name = request.form.get('ret_product_name', '').strip()
    product_code = request.form.get('ret_product_code', '').strip()

    # Auto-fill from reference transaction if selected
    if ref_txn:
        karat        = ref_txn.karat
        product_name = product_name or ref_txn.product_name or ''
        product_code = product_code or ref_txn.product_code or ''

    if weight <= 0:
        flash('الوزن يجب أن يكون أكبر من الصفر.', 'danger')
        return redirect(url_for('customers.customer_profile', cid=cid, tab='returns'))

    # ── HARD VALIDATION: return cannot exceed sold ────────────────
    #
    # Level 1 — per transaction (if ref_txn selected):
    #   already_returned = sum of prior returns that reference same sale
    #   available = ref_txn.weight - already_returned
    #
    # Level 2 — per customer total:
    #   total_sold     = sum of all OUT transactions (non-return)
    #   total_returned = sum of all existing RETURN transactions
    #   allowed_return = total_sold - total_returned

    if ref_txn:
        # How much has already been returned against this specific sale?
        already_returned_txn = InventoryTransaction.query.filter_by(
            factory_id=fac.id,
            customer_id=cid,
            is_return=1,
        ).all()
        # Match by description containing ref_txn.op_code
        same_sale_returns = [
            t for t in already_returned_txn
            if ref_txn.op_code and ref_txn.op_code in (t.description or '')
        ]
        returned_so_far = round(sum(t.weight for t in same_sale_returns), 4)
        available_for_this_txn = round(ref_txn.weight - returned_so_far, 4)

        if weight > available_for_this_txn:
            flash(
                f'كمية الرجيع ({weight:.3f}جم) تتجاوز المتاح لهذه العملية. '
                f'الأصلي: {ref_txn.weight:.3f}جم، '
                f'المُرجَّع سابقاً: {returned_so_far:.3f}جم، '
                f'المتاح: {available_for_this_txn:.3f}جم.',
                'danger'
            )
            return redirect(url_for('customers.customer_profile', cid=cid, tab='returns'))

    # Level 2 — customer-level safety net (always checked)
    all_txns         = InventoryTransaction.query.filter_by(
        factory_id=fac.id, customer_id=cid).all()
    total_sold       = round(sum(t.weight for t in all_txns
                                 if t.transaction_type == 'OUT' and not t.is_return), 4)
    total_returned   = round(sum(t.weight for t in all_txns if t.is_return), 4)
    allowed_return   = round(total_sold - total_returned, 4)

    if weight > allowed_return:
        flash(
            f'كمية الرجيع تتجاوز الكمية الصادرة للعميل. '
            f'الإجمالي الصادر: {total_sold:.3f}جم، '
            f'المُرجَّع: {total_returned:.3f}جم، '
            f'المتاح للرجيع: {allowed_return:.3f}جم.',
            'danger'
        )
        return redirect(url_for('customers.customer_profile', cid=cid, tab='returns'))

    op_code = _gen_return_code(fac.id)

    txn = InventoryTransaction(
        factory_id=fac.id,
        customer_id=cid,
        transaction_type='IN',
        karat=karat,
        weight=weight,
        price_per_gram=0,
        product_name=product_name,
        product_code=product_code,
        is_return=1,
        op_code=op_code,
        description=desc or f'رجيع من العميل: {c.name}' + (f' (عملية: {ref_txn.op_code})' if ref_txn and ref_txn.op_code else ''),
        transaction_date=date.fromisoformat(txn_date_str),
    )
    txn.compute()
    db.session.add(txn)

    # ── Issue 4: Add gold back to inventory ───────────────────────
    inv_in = Inventory(
        factory_id=fac.id,
        transaction_type='in',
        weight=weight,
        karat=karat,
        description=f'من عميل - رجيع ({op_code}): {c.name}',
        transaction_date=date.fromisoformat(txn_date_str),
    )
    inv_in.compute_pure()
    db.session.add(inv_in)
    db.session.commit()

    flash(f'تم تسجيل الرجيع ({op_code}): {weight:.3f}جم @ عيار {karat}K — أضيف للمخزون.', 'success')
    return redirect(url_for('customers.customer_profile', cid=cid, tab='returns'))
