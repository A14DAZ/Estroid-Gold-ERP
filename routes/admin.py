from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, g
from flask_login import login_required, current_user
from functools import wraps
from datetime import date, timedelta
from models.db import db
from models.user import User
from models.factory import Factory
from models.order import WorkOrder
from models.worker import Worker
from models.production import ProductionStage

admin_bp = Blueprint('admin', __name__)


def superadmin_required(f):
    """Allows both superadmin and system staff to access admin panel."""
    @wraps(f)
    @login_required
    def decorated(*a, **kw):
        if not current_user.is_platform_user:
            abort(403)
        return f(*a, **kw)
    return decorated


def superadmin_only(f):
    """Restricts access to superadmin ONLY (not staff)."""
    @wraps(f)
    @login_required
    def decorated(*a, **kw):
        if not current_user.is_superadmin:
            abort(403)
        return f(*a, **kw)
    return decorated


# ── Dashboard ─────────────────────────────────────────────────────────────────

@admin_bp.route('/dashboard')
@superadmin_required
def dashboard():
    from datetime import date
    today = date.today()
    all_factories = Factory.query.order_by(Factory.created_at.desc()).all()

    # KPIs
    total_factories   = len(all_factories)
    active_factories  = sum(1 for f in all_factories if f.is_active)
    inactive_factories = total_factories - active_factories
    total_workers_all = Worker.query.count()
    total_orders_all  = WorkOrder.query.count()
    active_orders     = WorkOrder.query.filter_by(status='active').count()
    completed_orders  = WorkOrder.query.filter_by(status='completed').count()
    completion_pct    = round(completed_orders / total_orders_all * 100) if total_orders_all else 0

    # Subscription stats
    expired_count   = sum(1 for f in all_factories if f.is_active and f.subscription_expiry and not f.is_subscription_valid)
    unlimited_count = sum(1 for f in all_factories if f.subscription_expiry is None)
    expiring_7      = sum(1 for f in all_factories if f.is_active and f.days_remaining is not None and 0 < f.days_remaining <= 7)
    expiring_30     = sum(1 for f in all_factories if f.is_active and f.days_remaining is not None and 7 < f.days_remaining <= 30)

    # Team stats (system staff)
    from models.user import User as UserModel
    team_count = UserModel.query.filter(
        UserModel.role.in_(['superadmin', 'staff'])
    ).count()

    # Alerts
    expired_list  = [f for f in all_factories if f.is_active and f.subscription_expiry and not f.is_subscription_valid]
    expiring_soon = [f for f in all_factories if f.is_active and f.days_remaining is not None and 0 < f.days_remaining <= 7]
    inactive_list = [f for f in all_factories if not f.is_active]

    stats = {
        'total_factories':   total_factories,
        'active_factories':  active_factories,
        'inactive_factories':inactive_factories,
        'total_workers':     total_workers_all,
        'total_orders':      total_orders_all,
        'active_orders':     active_orders,
        'completed_orders':  completed_orders,
        'completion_pct':    completion_pct,
        'expired_count':     expired_count,
        'unlimited_count':   unlimited_count,
        'expiring_7':        expiring_7,
        'expiring_30':       expiring_30,
        'team_count':        team_count,
    }
    alerts = {
        'expired':       expired_list,
        'expiring_soon': expiring_soon,
        'inactive':      inactive_list,
    }
    return render_template('admin_dashboard.html',
                           stats=stats, alerts=alerts, today=today)


# ── Factories ─────────────────────────────────────────────────────────────────

@admin_bp.route('/factories')
@superadmin_required
def factories():
    page = request.args.get('page', 1, type=int)
    q    = request.args.get('q', '').strip()
    query = Factory.query
    if q:
        query = query.filter(Factory.name.ilike(f'%{q}%'))
    pag = query.order_by(Factory.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False)
    return render_template('admin_factories.html', pagination=pag, q=q)


@admin_bp.route('/factories/new', methods=['GET', 'POST'])
@superadmin_required
def create_factory_slot():
    """
    Create unclaimed factory slots with pre-generated activation codes.
    Shows all existing unclaimed/claimed codes in a table.
    """
    from datetime import date, timedelta

    if request.method == 'POST':
        import secrets as _s
        plan   = request.form.get('plan', 'basic')
        expiry = request.form.get('expiry_date', '').strip() or None
        count  = max(1, min(20, int(request.form.get('count', 1) or 1)))
        action = request.form.get('action', 'generate')

        # ── Disable a slot ─────────────────────────────────────
        if action == 'disable':
            fid = request.form.get('fid', type=int)
            if fid:
                fac = Factory.query.get(fid)
                if fac and not fac.user_id:   # only unclaimed
                    fac.is_active = False
                    db.session.commit()
                    flash(f'Code {fac.activation_code} disabled.', 'warning')
            return redirect(url_for('admin.create_factory_slot'))

        # ── Generate codes ─────────────────────────────────────
        from models.factory import _gen_factory_code
        codes = []
        for _ in range(count):
            code = Factory.generate_activation_code()
            while Factory.query.filter_by(activation_code=code).first():
                code = Factory.generate_activation_code()

            fac_code = _gen_factory_code()
            while Factory.query.filter_by(factory_code=fac_code).first():
                fac_code = f'FAC-{_s.randbelow(9000)+1000}'

            fac = Factory(
                user_id=None,
                name='(unclaimed)',
                plan=plan,
                is_active=True,
                activation_code=code,
                factory_code=fac_code,
            )
            if expiry:
                try:
                    from datetime import datetime
                    fac.subscription_expiry = datetime.strptime(expiry, '%Y-%m-%d').date()
                except ValueError:
                    pass
            db.session.add(fac)
            codes.append(code)

        db.session.commit()
        flash(f"{count} {g.t.get('activation_code','كود')} — {', '.join(codes)}", 'success')
        return redirect(url_for('admin.create_factory_slot'))

    # ── GET: load all factory slots ────────────────────────────
    today = date.today()
    all_slots = Factory.query.order_by(Factory.id.desc()).all()
    unclaimed  = [s for s in all_slots if not s.user_id]
    claimed    = [s for s in all_slots if s.user_id]
    active_unclaimed = [s for s in unclaimed if s.is_active]

    stats = {
        'total':    len(all_slots),
        'active':   len(active_unclaimed),
        'used':     len(claimed),
        'unused':   len([s for s in unclaimed if s.is_active]),
        'disabled': len([s for s in unclaimed if not s.is_active]),
    }
    return render_template('admin_new_slot.html',
                           today=today, slots=all_slots, stats=stats)



@admin_bp.route('/factories/<int:fid>/toggle', methods=['POST'])
@superadmin_required
def toggle_factory(fid):
    factory = Factory.query.get_or_404(fid)
    factory.is_active = not factory.is_active
    db.session.commit()
    state = g.t.get('activated', 'Activated') if factory.is_active else g.t.get('deactivated', 'Deactivated')
    flash(f'"{factory.name}" — {state}.', 'success')
    return redirect(url_for('admin.factories'))


@admin_bp.route('/factories/<int:fid>/delete', methods=['POST'])
@superadmin_only
def delete_factory(fid):
    factory = Factory.query.get_or_404(fid)
    name = factory.name
    db.session.delete(factory)
    db.session.commit()
    flash(f'"{name}" deleted.', 'warning')
    return redirect(url_for('admin.factories'))


# ── Subscription management ───────────────────────────────────────────────────

@admin_bp.route('/factories/<int:fid>/subscription', methods=['GET', 'POST'])
@superadmin_required
def manage_subscription(fid):
    factory = Factory.query.get_or_404(fid)
    from models.customer import Customer as CustomerModel
    factory_customer_count = CustomerModel.query.filter_by(factory_id=factory.id).count()

    if request.method == 'POST':
        action = request.form.get('action', '')

        if action == 'extend':
            days = int(request.form.get('days', 30) or 30)
            factory.extend_subscription(days)
            db.session.commit()
            flash(
                f'{"تم تمديد الاشتراك " if g.lang=="ar" else "Subscription extended by "}'
                f'{days} {"يوم" if g.lang=="ar" else "days"} — '
                f'{"ينتهي" if g.lang=="ar" else "Expires"}: {factory.subscription_expiry}',
                'success'
            )

        elif action == 'set_expiry':
            expiry_str = request.form.get('expiry_date', '')
            if expiry_str:
                factory.subscription_expiry = date.fromisoformat(expiry_str)
                db.session.commit()
                flash(
                    f'{"تاريخ انتهاء الاشتراك:" if g.lang=="ar" else "Expiry set to:"} '
                    f'{factory.subscription_expiry}', 'success'
                )

        elif action == 'gen_code':
            factory.activation_code = Factory.generate_activation_code()
            db.session.commit()
            flash(
                f'{"كود التفعيل الجديد:" if g.lang=="ar" else "New activation code:"} '
                f'{factory.activation_code}', 'success'
            )

        elif action == 'update_factory_code':
            new_code = request.form.get('factory_code', '').strip().upper()
            if not new_code:
                flash('كود المصنع لا يمكن أن يكون فارغاً.' if g.lang=='ar' else 'Factory code cannot be empty.', 'danger')
            elif Factory.query.filter(Factory.factory_code == new_code, Factory.id != factory.id).first():
                flash('هذا الكود مستخدم من مصنع آخر.' if g.lang=='ar' else 'Code already used by another factory.', 'danger')
            else:
                factory.factory_code = new_code
                db.session.commit()
                flash(f'{"كود المصنع:" if g.lang=="ar" else "Factory code:"} {new_code}', 'success')

        elif action == 'reset_password':
            new_pw = request.form.get('new_password', '').strip()
            if len(new_pw) < 8:
                flash(
                    'كلمة المرور قصيرة جداً (8 أحرف على الأقل).' if g.lang == 'ar'
                    else 'Password too short (min 8 characters).', 'danger'
                )
            else:
                factory.user.set_password(new_pw)
                db.session.commit()
                flash(
                    'تم إعادة تعيين كلمة المرور.' if g.lang == 'ar'
                    else 'Password reset successfully.', 'success'
                )

        return redirect(url_for('admin.manage_subscription', fid=fid))

    from models.subscription import FactoryNote
    factory_notes = FactoryNote.query.filter_by(factory_id=factory.id)        .order_by(FactoryNote.created_at.desc()).limit(10).all()
    return render_template('admin_subscription.html', factory=factory,
                           today=date.today(), customer_count=factory_customer_count,
                           factory_notes=factory_notes)


# ── Users ─────────────────────────────────────────────────────────────────────

@admin_bp.route('/users')
@superadmin_required
def users():
    page  = request.args.get('page', 1, type=int)
    # Show only factory-linked users (not system staff)
    users = User.query.filter(User.role == 'factory').order_by(User.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False)
    return render_template('admin_users.html', pagination=users)


@admin_bp.route('/users/<int:uid>/toggle', methods=['POST'])
@superadmin_required
def toggle_user(uid):
    user = User.query.get_or_404(uid)
    if user.id == current_user.id:
        flash('Cannot deactivate yourself.', 'danger')
    else:
        user.is_active = not user.is_active
        db.session.commit()
        flash(f'{user.email} updated.', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/<int:uid>/reset-password', methods=['POST'])
@superadmin_required
def reset_user_password(uid):
    user   = User.query.get_or_404(uid)
    new_pw = request.form.get('new_password', '').strip()
    if len(new_pw) < 8:
        flash('Password too short (min 8 characters).', 'danger')
    else:
        user.set_password(new_pw)
        db.session.commit()
        flash(f'Password reset for {user.email}.', 'success')
    return redirect(url_for('admin.users'))


# ── Team (System Staff) ────────────────────────────────────────────────────────

SYSTEM_ROLES = ['superadmin', 'support', 'accountant', 'manager']

def _next_employee_code():
    """Generate next EMP-XXXX code."""
    from sqlalchemy import text as _text
    rows = db.session.execute(
        _text("SELECT employee_code FROM user WHERE employee_code IS NOT NULL AND employee_code != ''")
    ).fetchall()
    nums = []
    for (code,) in rows:
        if code and code.upper().startswith('EMP-'):
            try: nums.append(int(code.split('-')[1]))
            except: pass
    return f'EMP-{(max(nums)+1 if nums else 1):04d}'


@admin_bp.route('/team')
@superadmin_required
def team():
    """List system staff only (not factory users)."""
    from sqlalchemy import or_, not_
    # System staff = superadmin OR staff role, and NOT linked to any factory
    members = User.query.filter(
        or_(User.role == 'superadmin', User.role == 'staff')
    ).order_by(User.created_at.desc()).all()
    return render_template('admin_team.html', members=members,
                           system_roles=SYSTEM_ROLES)


@admin_bp.route('/team/add', methods=['GET', 'POST'])
@superadmin_only
def add_team_member():
    from werkzeug.security import generate_password_hash
    if request.method == 'POST':
        full_name   = request.form.get('full_name', '').strip()
        email       = request.form.get('email', '').strip().lower()
        system_role = request.form.get('system_role', 'support')
        password    = request.form.get('password', '').strip()

        if not full_name or not email or not password:
            flash(g.t.get('err_code_required', 'جميع الحقول مطلوبة.'), 'danger')
            return redirect(url_for('admin.add_team_member'))
        if len(password) < 8:
            flash(g.t.get('err_weight_positive', 'كلمة المرور قصيرة (8 أحرف على الأقل).'), 'danger')
            return redirect(url_for('admin.add_team_member'))
        if User.query.filter_by(email=email).first():
            flash(f'Email {email} already exists.', 'danger')
            return redirect(url_for('admin.add_team_member'))

        emp_code = _next_employee_code()
        while User.query.filter_by(employee_code=emp_code).first():
            import secrets as _s
            emp_code = f'EMP-{_s.randbelow(9000)+1000}'

        new_user = User(
            full_name     = full_name,
            email         = email,
            password_hash = generate_password_hash(password),
            role          = 'superadmin' if system_role == 'superadmin' else 'staff',
            system_role   = system_role,
            employee_code = emp_code,
            is_active     = True,
        )
        db.session.add(new_user)
        db.session.commit()
        flash(f'Team member {full_name} added — {emp_code}', 'success')
        return redirect(url_for('admin.team'))

    return render_template('admin_team_add.html', system_roles=SYSTEM_ROLES)


@admin_bp.route('/team/<int:uid>/toggle', methods=['POST'])
@superadmin_only
def toggle_team_member(uid):
    user = User.query.get_or_404(uid)
    if user.id == current_user.id:
        flash('Cannot deactivate yourself.', 'danger')
    else:
        user.is_active = not user.is_active
        db.session.commit()
        state = g.t.sub_active if user.is_active else g.t.sub_suspended
        flash(f'{user.full_name} — {state}', 'success')
    return redirect(url_for('admin.team'))


@admin_bp.route('/team/<int:uid>/reset-password', methods=['POST'])
@superadmin_only
def reset_team_password(uid):
    from werkzeug.security import generate_password_hash
    user   = User.query.get_or_404(uid)
    new_pw = request.form.get('new_password', '').strip()
    if len(new_pw) < 8:
        flash('Password too short (min 8).', 'danger')
    else:
        user.set_password(new_pw)
        db.session.commit()
        flash(f'Password reset for {user.email}.', 'success')
    return redirect(url_for('admin.team'))


# ── Platform Settings ──────────────────────────────────────────────────────────

@admin_bp.route('/settings', methods=['GET', 'POST'])
@superadmin_only
def platform_settings():
    """Platform-wide settings (WhatsApp number, etc.)."""
    from models.setting import AppSetting
    if request.method == 'POST':
        import re, base64
        section = request.form.get('section', 'contact')

        if section == 'identity':
            # Platform name / tagline / version
            pname = request.form.get('platform_name', '').strip()
            ptag  = request.form.get('platform_tagline', '').strip()
            pver  = request.form.get('platform_version', '').strip()
            if pname: AppSetting.set('platform_name',    pname)
            if ptag:  AppSetting.set('platform_tagline', ptag)
            if pver:  AppSetting.set('platform_version', pver)

            # Platform logo — check 'clear_logo' (form field name)
            if request.form.get('clear_logo') == '1':
                AppSetting.set('platform_logo', '')

            logo_file = request.files.get('platform_logo_file')
            if logo_file and logo_file.filename:
                data = logo_file.read()
                if len(data) <= 2 * 1024 * 1024:
                    mime = logo_file.content_type or 'image/png'
                    b64  = base64.b64encode(data).decode()
                    logo_data = f'data:{mime};base64,{b64}'
                    # Save directly via query to handle large base64
                    from models.setting import AppSetting as _AS
                    row = _AS.query.filter_by(key='platform_logo').first()
                    if row:
                        row.value = logo_data
                    else:
                        db.session.add(_AS(key='platform_logo', value=logo_data))
                    db.session.commit()
                else:
                    flash(g.t.get('logo_size_limit', 'حجم اللوقو كبير جداً (حد 2MB)'), 'danger')

        elif section == 'contact':
            wa = re.sub(r'[^0-9]', '', request.form.get('support_whatsapp', ''))
            AppSetting.set('support_whatsapp', wa)
            AppSetting.set('support_email', request.form.get('support_email', '').strip())

        flash(g.t.get('save', 'تم الحفظ') + ' ✓', 'success')
        return redirect(url_for('admin.platform_settings'))

    settings = {
        'support_whatsapp': AppSetting.get('support_whatsapp', ''),
        'support_email':    AppSetting.get('support_email', ''),
        'platform_logo':    AppSetting.get('platform_logo', ''),
        'platform_name':    AppSetting.get('platform_name', 'Estroid Gold ERP'),
        'platform_tagline': AppSetting.get('platform_tagline', 'Smart ERP System for Gold Factories'),
        'platform_version': AppSetting.get('platform_version', 'v5.0'),
    }
    return render_template('admin_settings.html', settings=settings)


# ══════════════════════════════════════════════════════════════
#  SUBSCRIPTION MANAGEMENT
# ══════════════════════════════════════════════════════════════

@admin_bp.route('/subscriptions')
@superadmin_required
def subscriptions():
    """Main subscription management dashboard."""
    from datetime import date
    from models.subscription import Payment, SubscriptionPlan
    today = date.today()
    all_factories = Factory.query.order_by(Factory.created_at.desc()).all()

    # Financial KPIs
    total_revenue  = db.session.query(db.func.coalesce(db.func.sum(Payment.amount),0)).scalar() or 0
    month_revenue  = db.session.query(db.func.coalesce(db.func.sum(Payment.amount),0))\
        .filter(db.func.strftime('%Y-%m', Payment.paid_at) == today.strftime('%Y-%m')).scalar() or 0
    payment_count  = Payment.query.count()

    expired_f  = [f for f in all_factories if f.is_active and f.subscription_expiry and not f.is_subscription_valid]
    expiring_f = [f for f in all_factories if f.is_active and f.days_remaining is not None and 0 < f.days_remaining <= 7]
    active_f   = [f for f in all_factories if f.is_active and f.is_subscription_valid]
    plans      = SubscriptionPlan.query.order_by(SubscriptionPlan.sort_order).all()

    kpis = {
        'total_revenue':  total_revenue,
        'month_revenue':  month_revenue,
        'payment_count':  payment_count,
        'expired_count':  len(expired_f),
        'expiring_count': len(expiring_f),
        'active_count':   len(active_f),
    }
    return render_template('admin_subscriptions.html',
                           factories=all_factories, kpis=kpis, plans=plans,
                           expired_f=expired_f, expiring_f=expiring_f,
                           today=today)


@admin_bp.route('/payments')
@superadmin_required
def payments():
    """Payment history page."""
    from models.subscription import Payment
    page = request.args.get('page', 1, type=int)
    q    = request.args.get('q', '').strip()
    query = Payment.query.order_by(Payment.created_at.desc())
    if q:
        query = query.join(Factory).filter(Factory.name.ilike(f'%{q}%'))
    pagination = query.paginate(page=page, per_page=20, error_out=False)
    return render_template('admin_payments.html', pagination=pagination, q=q)


@admin_bp.route('/payments/add', methods=['GET', 'POST'])
@superadmin_required
def add_payment():
    """Record a new payment manually."""
    from models.subscription import Payment, _next_invoice_number
    from flask_login import current_user as cu

    if request.method == 'POST':
        fid      = request.form.get('factory_id', type=int)
        amount   = request.form.get('amount', type=float) or 0
        method   = request.form.get('method', 'manual')
        days     = request.form.get('period_days', 30, type=int)
        notes_v  = request.form.get('notes', '')
        currency = request.form.get('currency', 'SAR')

        factory = Factory.query.get_or_404(fid)
        inv_no  = _next_invoice_number()

        pay = Payment(
            factory_id     = fid,
            recorded_by_id = cu.id,
            invoice_number = inv_no,
            plan_slug      = factory.plan,
            amount         = amount,
            currency       = currency,
            method         = method,
            status         = 'paid',
            period_days    = days,
            notes          = notes_v,
        )
        db.session.add(pay)
        # Extend subscription
        factory.extend_subscription(days)
        db.session.commit()

        flash(f'{g.t.get("payment_recorded","تم تسجيل الدفعة")} — {inv_no}', 'success')
        return redirect(url_for('admin.payments'))

    factories = Factory.query.filter_by(is_active=True).order_by(Factory.name).all()
    plans = __import__('models.subscription', fromlist=['SubscriptionPlan']).SubscriptionPlan.query.all()
    return render_template('admin_payment_add.html', factories=factories, plans=plans)


@admin_bp.route('/payments/<int:pid>/invoice')
@superadmin_required
def payment_invoice(pid):
    """Print/view a payment invoice."""
    from models.subscription import Payment
    pay = Payment.query.get_or_404(pid)
    return render_template('admin_invoice.html', pay=pay, fac=pay.factory,
                           today=__import__('datetime').date.today())


@admin_bp.route('/plans')
@superadmin_required
def subscription_plans():
    from models.subscription import SubscriptionPlan
    plans = SubscriptionPlan.query.order_by(SubscriptionPlan.sort_order).all()
    return render_template('admin_plans.html', plans=plans)


@admin_bp.route('/plans/save', methods=['POST'])
@superadmin_only
def save_plan():
    from models.subscription import SubscriptionPlan
    pid         = request.form.get('plan_id', type=int)
    slug        = request.form.get('slug','').strip().lower()
    name        = request.form.get('name','').strip()
    price_month = request.form.get('price_month', type=float) or 0
    price_year  = request.form.get('price_year', type=float) or 0

    if pid:
        plan = SubscriptionPlan.query.get_or_404(pid)
    else:
        plan = SubscriptionPlan(slug=slug)
        db.session.add(plan)

    plan.name        = name
    plan.price_month = price_month
    plan.price_year  = price_year
    db.session.commit()
    flash(g.t.get('save','تم الحفظ') + ' ✓', 'success')
    return redirect(url_for('admin.subscription_plans'))


@admin_bp.route('/notes/add', methods=['POST'])
@superadmin_required
def add_factory_note():
    from models.subscription import FactoryNote
    from flask_login import current_user as cu
    fid  = request.form.get('factory_id', type=int)
    body = request.form.get('body','').strip()
    tag  = request.form.get('tag','general')
    if fid and body:
        db.session.add(FactoryNote(factory_id=fid, author_id=cu.id, body=body, tag=tag))
        db.session.commit()
        flash(g.t.get('notes','تمت إضافة الملاحظة.'), 'success')
    return redirect(url_for('admin.manage_subscription', fid=fid))


# ══════════════════════════════════════════════════════════════
#  HEALTH SCORE + ANALYTICS
# ══════════════════════════════════════════════════════════════

def _calc_health_score(factory) -> int:
    """
    Score 0-100 for each factory:
    - 30pts: subscription active + not expiring soon
    - 25pts: payment regularity (has recent payments)
    - 25pts: activity (orders in last 30 days)
    - 20pts: tenure (months using system)
    """
    score = 0
    from datetime import date, timedelta
    from models.subscription import Payment
    from models.order import WorkOrder

    today = date.today()

    # Subscription health (30pts)
    if factory.is_subscription_valid:
        days = factory.days_remaining
        if days is None or days > 30:
            score += 30
        elif days > 7:
            score += 20
        elif days > 0:
            score += 10
    # else 0

    # Payment regularity (25pts)
    payment_count = Payment.query.filter_by(factory_id=factory.id).count()
    if payment_count >= 6:   score += 25
    elif payment_count >= 3: score += 18
    elif payment_count >= 1: score += 10

    # Activity — orders last 30 days (25pts)
    cutoff = today - timedelta(days=30)
    recent_orders = WorkOrder.query.filter(
        WorkOrder.factory_id == factory.id,
        WorkOrder.created_at >= cutoff
    ).count()
    if recent_orders >= 20:  score += 25
    elif recent_orders >= 5: score += 18
    elif recent_orders >= 1: score += 10

    # Tenure (20pts)
    if factory.created_at:
        months = (today - factory.created_at.date()).days // 30
        if months >= 12:   score += 20
        elif months >= 6:  score += 14
        elif months >= 1:  score += 7

    return min(score, 100)


@admin_bp.route('/analytics')
@superadmin_required
def analytics():
    """Financial analytics dashboard with charts."""
    from datetime import date, timedelta
    from models.subscription import Payment
    today = date.today()
    all_factories = Factory.query.all()

    # Monthly revenue (last 12 months)
    monthly = []
    for i in range(11, -1, -1):
        d = today.replace(day=1) - timedelta(days=i*30)
        ym = d.strftime('%Y-%m')
        rev = db.session.query(
            db.func.coalesce(db.func.sum(Payment.amount), 0)
        ).filter(
            db.func.strftime('%Y-%m', Payment.paid_at) == ym
        ).scalar() or 0
        monthly.append({'month': d.strftime('%b %Y'), 'revenue': round(float(rev), 2)})

    total_rev = sum(m['revenue'] for m in monthly)
    avg_sub   = round(total_rev / len(all_factories), 2) if all_factories else 0

    active_f  = [f for f in all_factories if f.is_active and f.is_subscription_valid]
    expired_f = [f for f in all_factories if not f.is_subscription_valid and f.subscription_expiry]

    # Plan distribution
    plan_dist = {}
    for f in all_factories:
        plan_dist[f.plan or 'basic'] = plan_dist.get(f.plan or 'basic', 0) + 1

    # Top factories by payments
    top_payers = db.session.query(
        Factory, db.func.coalesce(db.func.sum(Payment.amount), 0).label('total')
    ).outerjoin(Payment).group_by(Factory.id).order_by(
        db.literal_column('total').desc()
    ).limit(5).all()

    return render_template('admin_analytics.html',
                           monthly=monthly, today=today,
                           total_rev=total_rev, avg_sub=avg_sub,
                           active_count=len(active_f),
                           expired_count=len(expired_f),
                           total_factories=len(all_factories),
                           plan_dist=plan_dist,
                           top_payers=top_payers)


@admin_bp.route('/health/recalc', methods=['POST'])
@superadmin_required
def recalc_health():
    """Recalculate health scores for all factories."""
    factories = Factory.query.all()
    for f in factories:
        f.health_score = _calc_health_score(f)
    db.session.commit()
    flash(f'{len(factories)} {g.t.get("health_score","حسابات محدثة")} ✓', 'success')
    return redirect(url_for('admin.analytics'))
