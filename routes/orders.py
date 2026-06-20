from flask import Blueprint, render_template, redirect, url_for, flash, request, g, abort
from flask_login import login_required, current_user
from datetime import datetime, date
from models.db import db
from models.factory import Factory
from models.order import WorkOrder, Inventory, KARATS, get_inventory_balance
from models.worker import Worker
from models.production import (ProductionStage, STAGE_NAMES_AR, STAGE_NAMES_EN,
                                SETTING_STAGE_AR, SETTING_STAGE_EN, STAGE_ICONS)
from models.stone import Stone

orders_bp = Blueprint('orders', __name__)

# Allowed status transitions
TRANSITIONS = {
    'active':    ['completed', 'cancelled'],
    'completed': [],
    'cancelled': [],
}


def _factory():
    fac = current_user.factory
    if fac is None:
        from flask import abort
        abort(403)
    return fac


def _own_order(order_id):
    fac = _factory()
    order = WorkOrder.query.filter_by(id=order_id, factory_id=fac.id).first_or_404()
    return order


# ── List ──────────────────────────────────────────────────────────────────────

@orders_bp.route('/')
@login_required
def list_orders():
    fac    = _factory()
    page   = request.args.get('page', 1, type=int)
    status = request.args.get('status', '')
    q      = request.args.get('q', '').strip()

    query = WorkOrder.query.filter_by(factory_id=fac.id)
    if status:
        query = query.filter_by(status=status)
    if q:
        query = query.filter(
            db.or_(WorkOrder.order_number.ilike(f'%{q}%'),
                   WorkOrder.customer_name.ilike(f'%{q}%'),
                   WorkOrder.model_name.ilike(f'%{q}%')))

    pag = query.order_by(WorkOrder.id.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('orders.html', pagination=pag, status=status, q=q)


# ── Create ────────────────────────────────────────────────────────────────────

@orders_bp.route('/new', methods=['GET', 'POST'])
@login_required
def create_order():
    fac = _factory()
    from models.customer import Customer
    clients = Customer.query.filter_by(factory_id=fac.id).order_by(Customer.name).all()

    if request.method == 'POST':
        model_name   = request.form.get('model_name', '').strip()
        product_name = request.form.get('product_name', '').strip()
        product_code = request.form.get('product_code', '').strip()
        client_id    = request.form.get('client_id') or None
        if client_id:
            client_id = int(client_id)
        karat          = int(request.form.get('karat', 18))
        initial_weight = float(request.form.get('initial_weight', 0))
        notes          = request.form.get('notes', '').strip()

        # Populate legacy name/phone from client record for backward compat
        customer_name  = ''
        customer_phone = ''
        if client_id:
            from models.customer import Customer as Cust
            cl = Cust.query.get(client_id)
            if cl:
                customer_name  = cl.name
                customer_phone = cl.phone or ''

        if initial_weight <= 0:
            flash(
                'الوزن الابتدائي يجب أن يكون أكبر من الصفر.' if g.lang == 'ar'
                else 'Initial weight must be positive.',
                'danger'
            )
            return render_template('order_form.html', fac=fac, karats=KARATS,
                                   order=None, clients=clients)

        # ── STOCK CHECK ──────────────────────────────────────────────
        balance = get_inventory_balance(fac.id)
        if balance < initial_weight:
            flash(
                f'لا يمكن فتح أمر: المخزون غير كافي. '
                f'الرصيد الحالي: {balance:.3f} جم، المطلوب: {initial_weight:.3f} جم'
                if g.lang == 'ar' else
                f'Cannot create order: insufficient stock. '
                f'Balance: {balance:.3f}g, Required: {initial_weight:.3f}g',
                'danger'
            )
            return render_template('order_form.html', fac=fac, karats=KARATS,
                                   order=None, clients=clients)

        order = WorkOrder(
            factory_id=fac.id,
            order_number=WorkOrder.generate_number(fac.id),
            client_id=client_id,
            model_name=model_name,
            product_name=product_name,
            product_code=product_code,
            customer_name=customer_name,
            customer_phone=customer_phone,
            karat=karat,
            initial_weight=initial_weight,
            notes=notes,
        )
        db.session.add(order)

        # ── DEDUCT initial_weight from inventory as an "out" transaction ──
        txn = Inventory(
            factory_id=fac.id,
            transaction_type='out',
            weight=initial_weight,
            karat=karat,
            description=f'أمر تشغيل — صادر: {order.order_number}',
            transaction_date=date.today(),
        )
        txn.compute_pure()
        db.session.add(txn)
        db.session.commit()

        # Update txn description now that we have the order_number
        txn.description = f'أمر تشغيل — صادر: {order.order_number}'
        db.session.commit()

        flash(
            f'{'تم إنشاء أمر' if g.lang == 'ar' else 'Order'} {order.order_number} '
            f'{'— تم خصم' if g.lang == 'ar' else '— deducted'} {initial_weight:.3f}g '
            f'{'من المخزون.' if g.lang == 'ar' else 'from inventory.'}',
            'success'
        )
        return redirect(url_for('orders.order_detail', order_id=order.id))

    return render_template('order_form.html', fac=fac, karats=KARATS,
                           order=None, clients=clients)


# ── Detail ────────────────────────────────────────────────────────────────────

@orders_bp.route('/<int:order_id>', methods=['GET'])
@login_required
def order_detail(order_id):
    order   = _own_order(order_id)
    fac     = _factory()
    from flask import session

    # Default: closed orders are read-only
    view_only = order.status in ('completed', 'cancelled')

    # Override: if user unlocked this order via password gate, allow full edit
    if session.get('edit_mode_order_id') == order.id:
        view_only = False   # temp edit mode granted

    workers = Worker.query.filter_by(factory_id=fac.id, is_active=True).all()
    stages  = ProductionStage.query.filter_by(work_order_id=order.id)\
                                   .order_by(ProductionStage.id).all()
    lang = g.lang
    stage_list    = STAGE_NAMES_AR if lang == 'ar' else STAGE_NAMES_EN
    setting_stage = SETTING_STAGE_AR if lang == 'ar' else SETTING_STAGE_EN

    return render_template('order_detail.html',
        order=order, fac=fac, workers=workers,
        stages=stages, stage_list=stage_list,
        setting_stage=setting_stage,
        stage_icons=STAGE_ICONS,
        transitions=TRANSITIONS,
        view_only=view_only,
    )


# ── Edit Password Gate ────────────────────────────────────────────────────────

@orders_bp.route('/<int:order_id>/edit-auth', methods=['GET', 'POST'])
@login_required
def edit_auth(order_id):
    """
    Password gate for edit.
    GET        → show password page
    POST (pwd) → verify → set session['edit_mode_order_id'] → redirect to order_detail
    """
    order = _own_order(order_id)
    fac   = _factory()

    if request.method == 'POST':
        pwd = request.form.get('password', '')
        if current_user.check_password(pwd):
            from flask import session
            session['edit_mode_order_id'] = order.id   # grant temp edit mode
            return redirect(url_for('orders.order_detail', order_id=order.id))
        flash('كلمة المرور غير صحيحة.', 'danger')

    return render_template('order_password.html',
                           order=order, fac=fac, mode='edit')


# ── Delete Password Gate ───────────────────────────────────────────────────────

@orders_bp.route('/<int:order_id>/delete-auth', methods=['GET', 'POST'])
@login_required
def delete_auth(order_id):
    """
    Separate password page before delete.
    GET  → show order_password.html (mode='delete')
    POST → verify password → redirect to delete_order (passes token)
    """
    order = _own_order(order_id)
    fac   = _factory()

    if request.method == 'POST':
        pwd = request.form.get('password', '')
        if current_user.check_password(pwd):
            # Password correct — call delete logic directly
            return _do_delete(order, fac)
        flash('كلمة المرور غير صحيحة.', 'danger')

    return render_template('order_password.html',
                           order=order, fac=fac, mode='delete')


def _do_delete(order, fac):
    """Shared delete logic — called after password verification."""
    order_number = order.order_number

    # Delete all Inventory movements linked to this order
    linked_txns = Inventory.query.filter(
        Inventory.factory_id == fac.id,
        Inventory.description.contains(order_number)
    ).all()
    for txn in linked_txns:
        db.session.delete(txn)

    # If active: restore initial_weight to inventory
    if order.status == 'active':
        restore_txn = Inventory(
            factory_id=fac.id,
            transaction_type='in',
            weight=order.initial_weight,
            karat=order.karat,
            description=f'أمر تشغيل — وارد (حذف أمر نشط): {order_number}',
            transaction_date=date.today(),
        )
        restore_txn.compute_pure()
        db.session.add(restore_txn)

    # Delete all ProductionStage records
    for stage in ProductionStage.query.filter_by(work_order_id=order.id).all():
        db.session.delete(stage)

    db.session.delete(order)
    db.session.commit()

    flash(f'تم حذف الأمر {order_number} وإلغاء جميع تأثيراته على المخزون.', 'warning')
    return redirect(url_for('orders.list_orders'))


@orders_bp.route('/<int:order_id>/cancel-edit-mode', methods=['POST'])
@login_required
def cancel_edit_mode(order_id):
    """Clear temp edit session without saving."""
    from flask import session
    session.pop('edit_mode_order_id', None)
    return redirect(url_for('orders.order_detail', order_id=order_id))


# ── Edit ──────────────────────────────────────────────────────────────────────

@orders_bp.route('/<int:order_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_order(order_id):
    order = _own_order(order_id)
    fac   = _factory()

    # edit_order must ALWAYS be reached via edit_auth (password page).
    # If accessed directly (GET without coming from auth), redirect to password gate.
    if request.method == 'GET':
        return redirect(url_for('orders.edit_auth', order_id=order.id))

    # POST only from here — form submission after password gate
    from models.customer import Customer
    clients = Customer.query.filter_by(factory_id=fac.id).order_by(Customer.name).all()

    if request.method == 'POST':
        order.model_name   = request.form.get('model_name', '').strip()
        order.product_name = request.form.get('product_name', '').strip()
        order.product_code = request.form.get('product_code', '').strip()
        client_id          = request.form.get('client_id') or None
        order.client_id    = int(client_id) if client_id else None
        order.karat        = int(request.form.get('karat', order.karat))
        order.initial_weight = float(request.form.get('initial_weight', order.initial_weight))
        order.notes        = request.form.get('notes', '').strip()
        # Sync legacy fields from client record
        if order.client_id:
            cl = Customer.query.get(order.client_id)
            if cl:
                order.customer_name  = cl.name
                order.customer_phone = cl.phone or ''
        else:
            order.customer_name  = ''
            order.customer_phone = ''
        db.session.commit()

        # Clear temp edit mode from session after successful save
        from flask import session
        session.pop('edit_mode_order_id', None)

        flash('تم التحديث.' if g.lang == 'ar' else 'Order updated.', 'success')
        return redirect(url_for('orders.order_detail', order_id=order.id))

    return render_template('order_form.html', fac=fac, karats=KARATS,
                           order=order, clients=clients)


# ── Status change ─────────────────────────────────────────────────────────────

@orders_bp.route('/<int:order_id>/status', methods=['POST'])
@login_required
def change_status(order_id):
    order      = _own_order(order_id)
    fac        = _factory()
    new_status = request.form.get('status', '')

    if new_status not in TRANSITIONS.get(order.status, []):
        flash('Invalid status transition.', 'danger')
        return redirect(url_for('orders.order_detail', order_id=order.id))

    # ── On order CLOSE: calculate output + return gold to inventory ──
    if new_status == 'completed':
        # VALIDATION: cannot complete without at least one completed stage
        completed_stages_count = ProductionStage.query.filter_by(
            work_order_id=order.id, status='completed').count()
        if completed_stages_count == 0:
            # Redirect to confirmation page — show warning + two action buttons
            return redirect(url_for('orders.confirm_no_stages', order_id=order.id))

        # final_output = last completed stage produced_weight (NOT a sum)
        final_output = order.last_stage_output
        # total_loss   = initial - last_output (sequential flow)
        total_loss   = order.total_loss
        order.final_output_weight = round(final_output, 4)

        # 2. Accounting validation (warning only, don't block)
        diff = order.accounting_diff   # initial - final_output - total_loss
        if abs(diff) > 0.001:          # allow 1mg tolerance for float rounding
            flash(
                f'⚠️ تنبيه: يوجد فرق محاسبي في الأوزان — '
                f'الفرق: {diff:+.4f} جم'
                if g.lang == 'ar' else
                f'⚠️ Warning: accounting discrepancy detected — '
                f'Diff: {diff:+.4f}g',
                'warning'
            )

        # 3. Return final_output to inventory as an "in" transaction
        if final_output > 0:
            # Guard: prevent negative stock (shouldn't happen but safety check)
            balance_now = get_inventory_balance(fac.id)
            # final_output is being added back — always safe
            txn = Inventory(
                factory_id=fac.id,
                transaction_type='in',
                weight=final_output,
                karat=order.karat,
                description=f'أمر تشغيل — وارد (إنتاج): {order.order_number}',
                transaction_date=date.today(),
            )
            txn.compute_pure()
            db.session.add(txn)

        order.status = new_status
        db.session.commit()

        # 4. Auto-add to finished products inventory (if product_code set)
        if order.product_code and final_output > 0:
            from models.product_inventory import ProductInventory
            ProductInventory.upsert(
                factory_id=fac.id,
                product_code=order.product_code,
                product_name=order.product_name or order.model_name or order.product_code,
                karat=order.karat,
                unit_weight=round(final_output, 4),
                qty_to_add=1,
            )
            db.session.commit()

        flash(
            f'اكتمل الأمر {order.order_number} '
            f'— الذهب الناتج: {final_output:.3f}g | '
            f'الفاقد: {total_loss:.3f}g',
            'success'
        )
        return redirect(url_for('orders.list_orders'))   # ← redirect to list

    elif new_status == 'cancelled':
        # On cancel: return initial_weight to stock
        txn = Inventory(
            factory_id=fac.id,
            transaction_type='in',
            weight=order.initial_weight,
            karat=order.karat,
            description=f'أمر تشغيل — وارد (إلغاء): {order.order_number}',
            transaction_date=date.today(),
        )
        txn.compute_pure()
        db.session.add(txn)
        order.status = new_status
        db.session.commit()
        flash(
            f'تم إلغاء الأمر {order.order_number} وإعادة '
            f'{order.initial_weight:.3f} جم إلى المخزون.',
            'warning'
        )
    else:
        order.status = new_status
        db.session.commit()
        flash(f'Order status → {new_status}.', 'success')

    return redirect(url_for('orders.order_detail', order_id=order.id))


# ── Confirm page: order has no stages ────────────────────────────────────────

@orders_bp.route('/<int:order_id>/confirm-no-stages', methods=['GET'])
@login_required
def confirm_no_stages(order_id):
    """Warning page when user tries to complete an order with no stages."""
    order = _own_order(order_id)
    fac   = _factory()
    return render_template('order_no_stages.html', order=order, fac=fac)


@orders_bp.route('/<int:order_id>/cancel-from-no-stages', methods=['POST'])
@login_required
def cancel_from_no_stages(order_id):
    """
    User chose to cancel from no-stages page.
    Reuses same cancelled logic as change_status:
    restore initial_weight to inventory + set status = cancelled.
    """
    order = _own_order(order_id)
    fac   = _factory()

    txn = Inventory(
        factory_id=fac.id,
        transaction_type='in',
        weight=order.initial_weight,
        karat=order.karat,
        description=f'أمر تشغيل — وارد (إلغاء بدون مراحل): {order.order_number}',
        transaction_date=date.today(),
    )
    txn.compute_pure()
    db.session.add(txn)
    order.status = 'cancelled'
    db.session.commit()

    flash(
        f'تم إلغاء الأمر {order.order_number} وإعادة '
        f'{order.initial_weight:.3f} جم إلى المخزون.',
        'warning'
    )
    return redirect(url_for('orders.list_orders'))


# ── Delete ────────────────────────────────────────────────────────────────────

@orders_bp.route('/<int:order_id>/delete', methods=['POST'])
@login_required
def delete_order(order_id):
    """POST-only delete — called from delete_auth after password verified."""
    order = _own_order(order_id)
    fac   = _factory()
    # Redirect to password gate if accessed directly without auth
    return redirect(url_for('orders.delete_auth', order_id=order.id))


# ── Add Stage (Phase 1 — Handover) ────────────────────────────────────────────

@orders_bp.route('/<int:order_id>/add-stage', methods=['GET', 'POST'])
@login_required
def add_stage(order_id):
    order   = _own_order(order_id)
    fac     = _factory()
    workers = Worker.query.filter_by(factory_id=fac.id, is_active=True).all()
    lang    = g.lang
    stage_list    = STAGE_NAMES_AR if lang == 'ar' else STAGE_NAMES_EN
    setting_stage = SETTING_STAGE_AR if lang == 'ar' else SETTING_STAGE_EN

    # ── Determine locked received_weight ─────────────────────────────
    last_stage = ProductionStage.query.filter_by(
        work_order_id=order.id, status='completed'
    ).order_by(ProductionStage.id.desc()).first()

    if last_stage:
        locked_weight = last_stage.produced_weight
        locked_source = (
            f'{"ناتج المرحلة السابقة" if lang=="ar" else "Previous stage output"}: '
            f'{last_stage.stage_name} → {locked_weight:.3f}g'
        )
    else:
        locked_weight = order.initial_weight
        locked_source = (
            f'{"الوزن الابتدائي للأمر" if lang=="ar" else "Order initial weight"}: '
            f'{locked_weight:.3f}g'
        )

    if request.method == 'POST':
        stage_name = request.form.get('stage_name', '')
        worker_id  = request.form.get('worker_id') or None
        notes      = request.form.get('notes', '').strip()

        # ── ALWAYS use locked_weight — ignore any user-supplied value ──
        received_weight = locked_weight

        if received_weight <= 0:
            flash(
                'الوزن الابتدائي للأمر صفر — أضف وزناً للمخزون أولاً.' if lang == 'ar'
                else 'Order initial weight is zero — add inventory first.',
                'danger'
            )
            return render_template('stage_handover.html',
                                   order=order, fac=fac, workers=workers,
                                   stage_list=stage_list, setting_stage=setting_stage,
                                   locked_weight=locked_weight, locked_source=locked_source)

        stage = ProductionStage(
            factory_id=fac.id,
            work_order_id=order.id,
            worker_id=worker_id,
            stage_name=stage_name,
            received_weight=received_weight,
            handover_date=datetime.utcnow(),
            status='in_progress',
            notes=notes,
        )
        db.session.add(stage)
        db.session.flush()

        # Stone details for Setting stage
        if stage_name in (SETTING_STAGE_AR, SETTING_STAGE_EN):
            stone_type     = request.form.get('stone_type', '').strip()
            stone_color    = request.form.get('stone_color', '').strip()
            stone_unit     = request.form.get('stone_unit', 'carat')
            stone_required = float(request.form.get('stone_required', 0) or 0)
            stone_given    = float(request.form.get('stone_given', 0) or 0)
            stone_notes    = request.form.get('stone_notes', '').strip()

            if stone_type or stone_given > 0 or stone_required > 0:
                stone = Stone(
                    stage_id=stage.id,
                    stone_type=stone_type,
                    stone_color=stone_color,
                    stone_unit=stone_unit,
                    stone_required=stone_required,
                    stone_given=stone_given,
                    notes=stone_notes,
                )
                db.session.add(stone)

        db.session.commit()
        flash(
            f'{"تم تسليم مرحلة" if lang=="ar" else "Stage handed over:"} "{stage_name}" '
            f'{"— الوزن المستلم:" if lang=="ar" else "— Received:"} {received_weight:.3f}g',
            'success'
        )
        return redirect(url_for('orders.order_detail', order_id=order.id))

    return render_template('stage_handover.html',
                           order=order, fac=fac, workers=workers,
                           stage_list=stage_list, setting_stage=setting_stage,
                           locked_weight=locked_weight, locked_source=locked_source)


# ── Complete Stage (Phase 2) ──────────────────────────────────────────────────

@orders_bp.route('/stage/<int:stage_id>/complete', methods=['GET', 'POST'])
@login_required
def complete_stage(stage_id):
    fac   = _factory()
    stage = ProductionStage.query.filter_by(id=stage_id, factory_id=fac.id).first_or_404()
    order = stage.work_order
    setting_stage = SETTING_STAGE_AR if g.lang == 'ar' else SETTING_STAGE_EN
    is_setting = stage.stage_name in (SETTING_STAGE_AR, SETTING_STAGE_EN)

    if request.method == 'POST':
        stage.produced_weight = float(request.form.get('produced_weight', 0) or 0)
        stage.scrap_weight    = float(request.form.get('scrap_weight', 0) or 0)
        stage.notes           = request.form.get('notes', stage.notes or '').strip()

        # VALIDATION: رجيع الذهب cannot be negative
        if stage.scrap_weight < 0:
            flash(
                'خطأ: رجيع الذهب لا يمكن أن يكون سالباً.' if g.lang == 'ar'
                else 'Error: Gold scrap (رجيع الذهب) cannot be negative.',
                'danger'
            )
            return render_template('stage_complete.html',
                                   stage=stage, order=order, fac=fac,
                                   is_setting=is_setting)

        # VALIDATION: produced + scrap cannot exceed received (non-setting stages only)
        if not is_setting:
            if stage.produced_weight + stage.scrap_weight > stage.received_weight:
                flash(
                    'خطأ: لا يمكن أن يكون مجموع الإنتاج والرجيع أكبر من الوزن المستلم',
                    'danger'
                )
                return render_template('stage_complete.html',
                                       stage=stage, order=order, fac=fac,
                                       is_setting=is_setting)

        # ── Setting stage: update returned stones + validate ──
        if is_setting and stage.stone:
            stone_returned_val = float(request.form.get('stone_returned', 0) or 0)

            # VALIDATION: returned cannot exceed given
            stone_given = stage.stone.stone_given or 0.0
            if stone_returned_val > stone_given:
                flash(
                    'خطأ: الأحجار المرتجعة أكثر من المسلَّمة.' if g.lang == 'ar'
                    else 'Validation Error: Returned stones cannot exceed given stones.',
                    'danger'
                )
                return render_template('stage_complete.html',
                                       stage=stage, order=order, fac=fac,
                                       is_setting=is_setting)

            stage.stone.stone_returned = stone_returned_val

        # ── Calculate loss (Setting uses stone-aware formula) ──
        stage.calculate_loss()
        stage.completion_date = datetime.utcnow()
        stage.status = 'completed'
        db.session.commit()

        # ── Flash result messages ──────────────────────────────
        if is_setting and stage.stone:
            stone_used_ct   = stage.stone.stone_used
            stone_req_ct    = stage.stone.stone_required or 0.0
            stone_used_g    = stage.stone.stone_used_grams

            # Stone usage warnings
            if stone_used_ct < stone_req_ct:
                flash(
                    f'⚠️ {"لم تُستخدم جميع الأحجار المطلوبة" if g.lang=="ar" else "Not all required stones were used"} '
                    f'({stone_used_ct:.3f} / {stone_req_ct:.3f} ct)',
                    'warning'
                )
            elif stone_used_ct > stone_req_ct:
                flash(
                    f'⚠️ {"استُخدمت أحجار إضافية" if g.lang=="ar" else "Extra stones used"} '
                    f'({stone_used_ct:.3f} ct > {stone_req_ct:.3f} ct required)',
                    'warning'
                )

        # Gold loss messages
        if stage.gold_actual_output < 0:
            flash(
                '🔴 خطأ محاسبي: الناتج الذهبي الفعلي سالب. راجع الأوزان.' if g.lang == 'ar'
                else '🔴 Accounting Error: Gold actual output is negative. Please review weights.',
                'danger'
            )
        elif is_setting and stage.loss_weight < 0:
            flash(
                '⚠️ خطأ محاسبي: الرجيع أكبر من الفاقد.' if g.lang == 'ar'
                else '⚠️ Accounting Error: Scrap (رجيع) exceeds expected loss.',
                'warning'
            )
        elif stage.is_loss_high:
            flash(
                f'⚠️ {"اكتملت المرحلة — تحذير: الفاقد" if g.lang=="ar" else "Stage completed — Warning: Loss"} '
                f'{stage.loss_percentage:.2f}% '
                f'{"يتجاوز 2%!" if g.lang=="ar" else "exceeds 2%!"}',
                'warning'
            )
        else:
            flash(
                f'{"اكتملت مرحلة" if g.lang=="ar" else "Stage"} '
                f'"{stage.stage_name}" '
                f'{"بنجاح — الفاقد:" if g.lang=="ar" else "completed — Loss:"} '
                f'{stage.loss_weight:.3f}g ({stage.loss_percentage:.2f}%)',
                'success'
            )

        return redirect(url_for('orders.order_detail', order_id=order.id))

    return render_template('stage_complete.html',
                           stage=stage, order=order, fac=fac,
                           is_setting=is_setting)


# ── Delete Stage ──────────────────────────────────────────────────────────────

@orders_bp.route('/stage/<int:stage_id>/delete', methods=['POST'])
@login_required
def delete_stage(stage_id):
    fac   = _factory()
    stage = ProductionStage.query.filter_by(id=stage_id, factory_id=fac.id).first_or_404()
    oid   = stage.work_order_id
    db.session.delete(stage)
    db.session.commit()
    flash('Stage deleted.', 'warning')
    return redirect(url_for('orders.order_detail', order_id=oid))
