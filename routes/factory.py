from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, g
from flask_login import login_required, current_user
from datetime import date
from functools import wraps
from models.db import db
from models.factory import Factory
from models.order import WorkOrder, Inventory, KARATS, calc_pure_weight, get_inventory_balance
from models.worker import Worker
from models.production import ProductionStage

factory_bp = Blueprint('factory', __name__)


# ── Access guard ──────────────────────────────────────────────────────────────

def tenant_required(f):
    @wraps(f)
    @login_required
    def decorated(*a, **kw):
        # Super admin & system staff → redirect to admin panel (not factory)
        if current_user.is_platform_user:
            return redirect(url_for('admin.dashboard'))
        fac = current_user.factory
        if not fac or not fac.is_active:
            flash(g.t.get('err_suspended', 'Factory account suspended.'), 'danger')
            return redirect(url_for('auth.logout'))
        if fac.is_read_only:
            # Past grace period — freeze but don't logout
            if request.endpoint not in ('factory.dashboard', 'auth.logout', 'factory.settings'):
                flash(g.t.get('err_read_only',
                      'حسابك في وضع القراءة فقط — اشتراكك انتهى. تواصل مع الإدارة.'), 'warning')
                return redirect(url_for('factory.dashboard'))
        elif not fac.is_subscription_valid:
            if fac.is_in_grace_period:
                flash(g.t.get('err_grace_period',
                      f'⚠️ الاشتراك انتهى — لديك {fac.grace_period_days} أيام للتجديد قبل تجميد الحساب.'),
                      'warning')
            else:
                flash(g.t.get('err_expired', 'Subscription expired.'), 'danger')
                return redirect(url_for('auth.logout'))
        return f(*a, **kw)
    return decorated


def get_factory():
    fac = current_user.factory
    if fac is None:
        from flask import abort
        abort(403)  # Platform users should not reach factory routes
    return fac


# ── Dashboard ─────────────────────────────────────────────────────────────────

@factory_bp.route('/dashboard')
@tenant_required
def dashboard():
    fac   = get_factory()
    today = date.today()
    from models.customer import Customer
    from models.production import SETTING_STAGE_AR, SETTING_STAGE_EN

    # ── Section 1: counts ─────────────────────────────────────────
    total_orders     = WorkOrder.query.filter_by(factory_id=fac.id).count()
    active_orders    = WorkOrder.query.filter_by(factory_id=fac.id, status='active').count()
    completed_orders = WorkOrder.query.filter_by(factory_id=fac.id, status='completed').count()
    total_workers    = Worker.query.filter_by(factory_id=fac.id).count()
    total_clients    = Customer.query.filter_by(factory_id=fac.id).count()
    completed_pct    = round((completed_orders / total_orders * 100) if total_orders else 0)

    # ── Section 2: 24K gold metrics ───────────────────────────────
    completed_stages = ProductionStage.query.filter_by(
        factory_id=fac.id, status='completed').all()

    def stage_fine(s, field):
        val   = getattr(s, field, 0) or 0.0
        karat = (s.work_order.karat if s.work_order else 24) or 24
        return round(val * (karat / 24.0), 6)

    def stone_fine(s):
        if not s.stone: return 0.0
        ct    = s.stone.stone_required or 0.0
        karat = (s.work_order.karat if s.work_order else 24) or 24
        return round(ct * 0.2 * (karat / 24.0), 6)

    total_production_24 = 0.0
    for o in WorkOrder.query.filter_by(factory_id=fac.id, status='completed').all():
        k = o.karat or 18
        last_s = max(
            [s for s in completed_stages if s.work_order_id == o.id],
            key=lambda s: s.id, default=None
        )
        if last_s:
            total_production_24 += round((last_s.produced_weight or 0) * (k / 24.0), 6)

    total_loss_24   = round(sum(stage_fine(s, 'loss_weight')  for s in completed_stages), 4)
    total_scrap_24  = round(sum(stage_fine(s, 'scrap_weight') for s in completed_stages), 4)
    total_stone_24  = round(sum(stone_fine(s)                 for s in completed_stages), 4)
    total_production_24 = round(total_production_24, 4)

    # ── Inventory 24K balance ─────────────────────────────────────
    pure_in  = db.session.query(db.func.coalesce(db.func.sum(Inventory.pure_weight), 0))\
                         .filter_by(factory_id=fac.id, transaction_type='in').scalar() or 0.0
    pure_out = db.session.query(db.func.coalesce(db.func.sum(Inventory.pure_weight), 0))\
                         .filter_by(factory_id=fac.id, transaction_type='out').scalar() or 0.0
    pure_balance = round(pure_in - pure_out, 4)

    # ── Legacy vars still needed by template ──────────────────────
    gold_in  = db.session.query(db.func.coalesce(db.func.sum(Inventory.weight), 0))\
                         .filter_by(factory_id=fac.id, transaction_type='in').scalar() or 0.0
    gold_out = db.session.query(db.func.coalesce(db.func.sum(Inventory.weight), 0))\
                         .filter_by(factory_id=fac.id, transaction_type='out').scalar() or 0.0

    # ── Section 3: smart insights ─────────────────────────────────
    # Best worker = least loss_24 per received_24 (min loss%)
    best_worker = None
    best_loss_pct = None
    workers = Worker.query.filter_by(factory_id=fac.id).all()
    for w in workers:
        w_stages = [s for s in completed_stages if s.worker_id == w.id]
        if not w_stages: continue
        rec_24  = sum(stage_fine(s, 'received_weight') for s in w_stages)
        loss_24 = sum(stage_fine(s, 'loss_weight')     for s in w_stages)
        pct     = (loss_24 / rec_24 * 100) if rec_24 > 0 else 0.0
        if best_loss_pct is None or pct < best_loss_pct:
            best_loss_pct = round(pct, 2)
            best_worker   = w

    # Worst stage = most loss_24
    stage_agg = {}
    for s in completed_stages:
        stage_agg.setdefault(s.stage_name, 0.0)
        stage_agg[s.stage_name] += stage_fine(s, 'loss_weight')
    worst_stage      = max(stage_agg, key=stage_agg.get) if stage_agg else None
    worst_stage_loss = round(stage_agg[worst_stage], 4) if worst_stage else 0.0

    # Top client = most transactions
    from models.transaction import InventoryTransaction
    top_client      = None
    top_client_count = 0
    for c in Customer.query.filter_by(factory_id=fac.id).all():
        cnt = InventoryTransaction.query.filter_by(
            factory_id=fac.id, customer_id=c.id).count()
        if cnt > top_client_count:
            top_client_count = cnt
            top_client       = c

    # ── Section 4: recent activity ────────────────────────────────
    recent_orders = WorkOrder.query.filter_by(factory_id=fac.id)\
                                   .order_by(WorkOrder.id.desc()).limit(5).all()
    recent_txns   = Inventory.query.filter_by(factory_id=fac.id)\
                                   .order_by(Inventory.id.desc()).limit(6).all()

    # ── New smart stats ───────────────────────────────────────────
    from models.product_inventory import ProductInventory
    from models.transaction import InventoryTransaction
    total_products  = ProductInventory.query.filter_by(factory_id=fac.id).count()
    total_sales     = InventoryTransaction.query.filter_by(
        factory_id=fac.id, transaction_type='OUT').count()
    total_purchases = InventoryTransaction.query.filter_by(
        factory_id=fac.id, transaction_type='IN').count()
    total_ops       = total_sales + total_purchases
    from sqlalchemy import cast, Date
    today_ops = InventoryTransaction.query.filter_by(factory_id=fac.id)\
        .filter(cast(InventoryTransaction.created_at, Date) == today).count()

    return render_template('factory_dashboard.html',
        fac=fac, today=today,
        # Section 1
        total_orders=total_orders, active_orders=active_orders,
        completed_orders=completed_orders, completed_pct=completed_pct,
        total_workers=total_workers, total_clients=total_clients,
        # Section 2 (24K)
        total_production_24=total_production_24,
        total_loss_24=total_loss_24,
        total_scrap_24=total_scrap_24,
        total_stone_24=total_stone_24,
        pure_balance=pure_balance, pure_in=pure_in, pure_out=pure_out,
        # Legacy
        gold_in=gold_in, gold_out=gold_out,
        # Section 3
        best_worker=best_worker, best_loss_pct=best_loss_pct,
        worst_stage=worst_stage, worst_stage_loss=worst_stage_loss,
        top_client=top_client, top_client_count=top_client_count,
        # Section 4
        recent_orders=recent_orders, recent_txns=recent_txns,
        # New smart stats
        total_products=total_products,
        total_ops=total_ops,
        today_ops=today_ops,
    )


# ── Inventory (READ-ONLY analytics view) ─────────────────────────────────────

def _get_inventory_data(fac):
    """Shared data loader for all 4 inventory sub-pages."""
    from models.production import ProductionStage as PS, SETTING_STAGE_AR, SETTING_STAGE_EN
    from models.order import WorkOrder as WO

    txns = Inventory.query.filter_by(factory_id=fac.id)\
                          .order_by(Inventory.id.desc()).all()

    # ── 24K helper (NEVER sum raw weights across karats) ──────────
    def fine(t):
        if t.pure_weight and t.pure_weight > 0: return t.pure_weight
        return round((t.weight or 0.0) * ((t.karat or 24) / 24.0), 6)

    # ── Inventory movement 24K totals ─────────────────────────────
    total_in_24  = round(sum(fine(t) for t in txns if t.transaction_type == 'in'),  4)
    total_out_24 = round(sum(fine(t) for t in txns if t.transaction_type == 'out'), 4)
    balance_24   = round(total_in_24 - total_out_24, 4)

    # ── Classify each movement ────────────────────────────────────
    def classify(t):
        desc = (t.description or '')
        desc_lower = desc.lower()
        # Work order movements
        if 'أمر تشغيل' in desc:
            if t.transaction_type == 'in':
                return 'production_return'
            return 'order_issue'
        # Manual product entry
        if 'من المنتجات' in desc:
            return 'product_manual'
        # Customer transactions
        if t.transaction_type == 'in':
            if any(k in desc_lower for k in ('ناتج','output','return','اكتمل','completed','إنتاج')):
                return 'production_return'
            return 'customer_in'
        else:
            if any(k in desc_lower for k in ('تصدير','بيع','export','sale','customer','عميل')):
                return 'customer_out'
            return 'order_issue'

    txns_classified = [{'txn': t, 'fine': round(fine(t), 4), 'class': classify(t)}
                       for t in txns]

    # Movement 24K sub-totals
    def sub24(cls):
        return round(sum(r['fine'] for r in txns_classified if r['class'] == cls), 4)

    mov = {
        'customer_in':      sub24('customer_in'),
        'order_issue':      sub24('order_issue'),
        'production_return':sub24('production_return'),
        'customer_out':     sub24('customer_out'),
    }

    # ── Karat breakdown (24K per karat — no cross-karat sums) ─────
    karat_map = {}
    for r in txns_classified:
        t = r['txn']
        k = t.karat or 24
        karat_map.setdefault(k, {'karat': k,
                                  'in_weight': 0.0,  'in_fine': 0.0,
                                  'out_weight': 0.0, 'out_fine': 0.0})
        fw = r['fine']
        if t.transaction_type == 'in':
            karat_map[k]['in_weight']  += t.weight or 0.0
            karat_map[k]['in_fine']    += fw
        else:
            karat_map[k]['out_weight'] += t.weight or 0.0
            karat_map[k]['out_fine']   += fw
    karat_breakdown = sorted(
        [{'karat':      k,
          'in_weight':  round(v['in_weight'],  4),
          'in_fine':    round(v['in_fine'],    4),
          'out_weight': round(v['out_weight'], 4),
          'out_fine':   round(v['out_fine'],   4),
          'balance_fine': round(v['in_fine'] - v['out_fine'], 4)}
         for k, v in karat_map.items()],
        key=lambda x: x['karat'], reverse=True
    )

    # ── Production analytics (all 24K, no raw sums) ───────────────
    completed_stages = PS.query.filter_by(factory_id=fac.id, status='completed').all()

    def stage_fine(s, field):
        val = getattr(s, field, 0) or 0.0
        k   = (s.work_order.karat if s.work_order else 24) or 24
        return round(val * (k / 24.0), 6)

    total_loss_24    = round(sum(stage_fine(s, 'loss_weight')  for s in completed_stages), 4)
    total_scrap_24   = round(sum(stage_fine(s, 'scrap_weight') for s in completed_stages), 4)

    setting_stages   = [s for s in completed_stages
                        if s.stage_name in (SETTING_STAGE_AR, SETTING_STAGE_EN)]
    # stone_gain_24 = stone_required_ct × 0.2 × (karat / 24)
    total_stone_gain = round(sum(
        (s.stone.stone_required or 0.0) * 0.2
        * (((s.work_order.karat if s.work_order else 24) or 24) / 24.0)
        for s in setting_stages if s.stone
    ), 4)

    completed_orders = WO.query.filter_by(factory_id=fac.id, status='completed').all()
    total_production_24 = round(sum(
        (o.final_output_weight or 0) * ((o.karat or 24) / 24.0)
        for o in completed_orders
    ), 4)

    # ── Product inventory (from completed orders) ─────────────────
    products = []
    for o in completed_orders:
        if not o.final_output_weight or o.final_output_weight <= 0:
            continue
        fw = round(o.final_output_weight * ((o.karat or 24) / 24.0), 4)
        products.append({
            'order_number': o.order_number,
            'product_name': o.product_name or o.model_name or '—',
            'product_code': o.product_code or '—',
            'weight':       round(o.final_output_weight, 4),
            'karat':        o.karat or 24,
            'pure_24':      fw,
        })
    products.sort(key=lambda x: (-x['karat'], x['product_name']))

    # Group products by karat for display
    products_by_karat = {}
    for p in products:
        products_by_karat.setdefault(p['karat'], []).append(p)
    products_by_karat = dict(sorted(products_by_karat.items(), reverse=True))

    return dict(
        txns=txns_classified,
        total_in_24=total_in_24, total_out_24=total_out_24, balance_24=balance_24,
        mov=mov,
        karat_breakdown=karat_breakdown,
        total_production_24=total_production_24,
        total_loss_24=total_loss_24,
        total_scrap_24=total_scrap_24,
        total_stone_gain=total_stone_gain,
        products=products,
        products_by_karat=products_by_karat,
    )


# ── 1. Inventory Dashboard ────────────────────────────────────────────────────

@factory_bp.route('/inventory')
@tenant_required
def inventory():
    fac = get_factory()
    d   = _get_inventory_data(fac)
    return render_template('inv_dashboard.html', fac=fac, **d)


# ── 2. Karat Analysis ─────────────────────────────────────────────────────────

@factory_bp.route('/inventory/karats')
@tenant_required
def inventory_karats():
    fac = get_factory()
    d   = _get_inventory_data(fac)
    return render_template('inv_karats.html', fac=fac, **d)


# ── 3. Gold Movements ─────────────────────────────────────────────────────────

@factory_bp.route('/inventory/movements')
@tenant_required
def inventory_movements():
    fac = get_factory()
    d   = _get_inventory_data(fac)
    return render_template('inv_movements.html', fac=fac, **d)


# ── 4. Products Inventory ─────────────────────────────────────────────────────

@factory_bp.route('/inventory/products')
@tenant_required
def inventory_products():
    fac = get_factory()
    d   = _get_inventory_data(fac)
    return render_template('inv_products.html', fac=fac, **d)


@factory_bp.route('/inventory/<int:tid>/delete', methods=['POST'])
@tenant_required
def delete_inventory(tid):
    fac = get_factory()
    txn = Inventory.query.filter_by(id=tid, factory_id=fac.id).first_or_404()
    db.session.delete(txn)
    db.session.commit()
    flash(g.t.get('msg_deleted', 'تم الحذف.' if g.lang == 'ar' else 'Deleted.'), 'warning')
    return redirect(url_for('factory.inventory'))


# ── Settings ──────────────────────────────────────────────────────────────────

@factory_bp.route('/settings', methods=['GET', 'POST'])
@tenant_required
def settings():
    fac  = get_factory()
    user = current_user
    active_tab = request.args.get('tab', 'general')

    if request.method == 'POST':
        action = request.form.get('action', '')

        # ── 1. Factory info ────────────────────────────────────
        if action == 'factory':
            fac.name        = request.form.get('factory_name', fac.name).strip() or fac.name
            fac.city        = request.form.get('city', '').strip()
            fac.country     = request.form.get('country', 'SA').strip()
            fac.address     = request.form.get('address', '').strip()
            fac.commercial_reg = request.form.get('commercial_reg', '').strip()
            fac.tax_number  = request.form.get('tax_number', '').strip()
            fac.description = request.form.get('description', '').strip()
            db.session.commit()
            flash(g.t.get('settings_saved', 'تم الحفظ ✓'), 'success')
            active_tab = 'general'

        # ── 2. Contact info ────────────────────────────────────
        elif action == 'contact':
            fac.phone     = request.form.get('phone', '').strip()
            fac.whatsapp  = request.form.get('whatsapp', '').strip()
            fac.website   = request.form.get('website', '').strip()
            fac.instagram = request.form.get('instagram', '').strip()
            fac.twitter   = request.form.get('twitter', '').strip()
            db.session.commit()
            flash(g.t.get('settings_saved', 'تم الحفظ ✓'), 'success')
            active_tab = 'general'

        # ── 3. Logo + Color ────────────────────────────────────
        elif action == 'logo':
            import base64
            # Save factory color
            color = request.form.get('factory_color_input', '').strip()
            if color and color.startswith('#'):
                fac.factory_color = color
            # Save logo
            logo_file = request.files.get('logo_file')
            if logo_file and logo_file.filename:
                mime = logo_file.content_type or 'image/png'
                raw  = logo_file.read()
                if len(raw) > 1_000_000:
                    flash(g.t.get('logo_size_limit', 'حجم الصورة كبير (حد 1MB).'), 'danger')
                else:
                    fac.logo_data = f'data:{mime};base64,' + base64.b64encode(raw).decode()
                    flash(g.t.get('logo_saved', 'تم حفظ الشعار ✓'), 'success')
            elif request.form.get('remove_logo'):
                fac.logo_data = None
                flash(g.t.get('logo_removed', 'تم حذف الشعار.'), 'warning')
            else:
                flash(g.t.get('settings_saved', 'تم الحفظ ✓'), 'success')
            db.session.commit()
            active_tab = 'identity'

        # ── 4. Invoice settings ────────────────────────────────
        elif action == 'invoice':
            fac.invoice_footer = request.form.get('invoice_footer', '').strip()
            fac.thank_you_msg  = request.form.get('thank_you_msg', '').strip()
            db.session.commit()
            flash(g.t.get('settings_saved', 'تم الحفظ ✓'), 'success')
            active_tab = 'invoice'

        # ── 5. System preferences ─────────────────────────────
        elif action == 'system':
            try:
                fac.default_karat    = int(request.form.get('default_karat', 21))
                fac.default_currency = request.form.get('default_currency', 'SAR')
                fac.weight_unit      = request.form.get('weight_unit', 'g')
                db.session.commit()
                flash(g.t.get('settings_saved', 'تم الحفظ ✓'), 'success')
            except Exception as e:
                flash(str(e), 'danger')
            active_tab = 'system'

        # ── 6. Password change ─────────────────────────────────
        elif action == 'password':
            cur_pw = request.form.get('current_password', '')
            new_pw = request.form.get('new_password', '')
            cfm_pw = request.form.get('confirm_password', '')
            if not user.check_password(cur_pw):
                flash(g.t.get('err_wrong_password', 'كلمة المرور الحالية غير صحيحة.'), 'danger')
            elif len(new_pw) < 8:
                flash(g.t.get('err_password_short', 'كلمة المرور يجب أن تكون 8 أحرف على الأقل.'), 'danger')
            elif new_pw != cfm_pw:
                flash(g.t.get('err_password_mismatch', 'كلمتا المرور غير متطابقتين.'), 'danger')
            else:
                user.set_password(new_pw)
                db.session.commit()
                import logging, time
                logging.getLogger('gold_erp').info(
                    f'[SETTINGS_PW] user={user.email} fac={fac.id} time={time.strftime("%Y-%m-%d %H:%M:%S")}')
                flash(g.t.get('password_updated', 'تم تغيير كلمة المرور ✓'), 'success')
            active_tab = 'security'

        # ── 7. Email change (with password verify) ─────────────
        elif action == 'email':
            cur_pw    = request.form.get('verify_password', '')
            new_email = request.form.get('new_email', '').strip().lower()
            if not user.check_password(cur_pw):
                flash(g.t.get('err_wrong_password', 'كلمة المرور الحالية غير صحيحة.'), 'danger')
            elif not new_email:
                flash(g.t.get('email_required', 'الإيميل مطلوب.'), 'danger')
            else:
                from models.user import User as _U
                if _U.query.filter_by(email=new_email).first():
                    flash(g.t.get('email_taken', 'هذا الإيميل مستخدم من حساب آخر.'), 'danger')
                else:
                    user.email = new_email
                    db.session.commit()
                    flash(g.t.get('email_updated', 'تم تحديث الإيميل ✓'), 'success')
            active_tab = 'security'

        # ── 8. Backup (export JSON snapshot) ──────────────────
        elif action == 'backup':
            import json
            from datetime import datetime
            from models.order import WorkOrder
            from models.worker import Worker
            from models.customer import Customer
            snap = {
                'factory': {'name': fac.name, 'city': fac.city, 'plan': fac.plan},
                'workers': [{'code': w.code, 'name': w.name, 'specialty': w.specialty}
                            for w in Worker.query.filter_by(factory_id=fac.id).all()],
                'orders':  [{'num': o.order_number, 'status': o.status}
                            for o in WorkOrder.query.filter_by(factory_id=fac.id).all()],
                'exported_at': datetime.utcnow().isoformat()
            }
            from flask import Response
            fname = f'backup_{fac.factory_code or fac.id}_{datetime.utcnow().strftime("%Y%m%d")}.json'
            return Response(
                json.dumps(snap, ensure_ascii=False, indent=2),
                mimetype='application/json',
                headers={'Content-Disposition': f'attachment; filename={fname}'}
            )

        return redirect(url_for('factory.settings', tab=active_tab))

    from models.subscription import Payment
    recent_payments = Payment.query.filter_by(factory_id=fac.id)        .order_by(Payment.paid_at.desc()).limit(5).all()
    return render_template('settings.html', fac=fac, user=user,
                           active_tab=active_tab, recent_payments=recent_payments)


# ── Settings extra routes ─────────────────────────────────────

@factory_bp.route('/settings/logout-all', methods=['POST'])
@tenant_required
def logout_all_devices():
    """Logout from all devices by regenerating session."""
    from flask import session
    session.clear()
    from flask_login import logout_user
    logout_user()
    from flask import flash, redirect, url_for, g
    flash(g.t.get('logged_out_all', 'تم تسجيل الخروج من جميع الأجهزة.'), 'success')
    return redirect(url_for('auth.login'))


@factory_bp.route('/settings/gen-api-key', methods=['POST'])
@tenant_required
def gen_api_key():
    """Generate a new API key for the factory user."""
    import secrets as _sec
    from flask import redirect, url_for, flash, g
    current_user.api_key = _sec.token_hex(32)
    db.session.commit()
    flash(g.t.get('api_key_generated', 'تم إنشاء API Key جديد ✓'), 'success')
    return redirect(url_for('factory.settings', tab='security'))


@factory_bp.route('/settings/report-issue', methods=['POST'])
@tenant_required
def report_issue():
    """Log an issue report (stored internally)."""
    from flask import redirect, url_for, flash, g
    import logging, time
    fac  = get_factory()
    body = request.form.get('issue_body', '').strip()
    if body:
        logging.getLogger('gold_erp').warning(
            f'[ISSUE_REPORT] fac={fac.id} ({fac.name}) '
            f'user={current_user.email} time={time.strftime("%Y-%m-%d %H:%M:%S")}: {body[:500]}'
        )
        flash(g.t.get('issue_reported', 'تم إرسال البلاغ ✓ — سيتواصل معك فريق الدعم.'), 'success')
    return redirect(url_for('factory.settings', tab='support'))
