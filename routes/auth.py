from flask import Blueprint, render_template, redirect, url_for, flash, request, g, session
from flask_login import login_user, logout_user, login_required, current_user
from datetime import datetime
from models.db import db
from models.user import User
from models.factory import Factory

auth_bp = Blueprint('auth', __name__)

from utils.countries import get_phone_codes, get_countries


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(_after_login_url(current_user))

    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember'))

        user = User.query.filter_by(email=email).first()

        if not user or not user.check_password(password):
            flash(g.t.get('err_invalid_credentials',
                          'البريد الإلكتروني أو كلمة المرور غير صحيحة.' if g.lang == 'ar'
                          else 'Invalid email or password.'), 'danger')
            return render_template('login.html', email=email)

        if not user.is_active:
            flash(g.t.get('err_account_deactivated',
                          'الحساب معطّل. تواصل مع الدعم.' if g.lang == 'ar'
                          else 'Account deactivated. Contact support.'), 'danger')
            return render_template('login.html', email=email)

        # Only check factory status for actual factory users
        if user.role == 'factory' and user.factory:
            fac = user.factory
            if not fac.is_active:
                flash(g.t.get('err_suspended',
                              'حساب المصنع موقوف.' if g.lang == 'ar'
                              else 'Factory account is suspended.'), 'danger')
                return render_template('login.html', email=email)
            if not fac.is_subscription_valid:
                flash(g.t.get('err_expired',
                              'انتهى اشتراك المصنع. تواصل مع الإدارة.' if g.lang == 'ar'
                              else 'Factory subscription has expired. Contact admin.'), 'danger')
                return render_template('login.html', email=email)

        login_user(user, remember=remember)
        # Record last login time and IP
        try:
            from datetime import datetime as _dt
            user.last_login_at = _dt.utcnow()
            user.last_login_ip = request.remote_addr or ''
            db.session.commit()
        except Exception:
            pass
        user.last_login = datetime.utcnow()
        db.session.commit()

        next_page = request.args.get('next')
        # Platform users (admin/staff) must ALWAYS go to admin panel — ignore 'next'
        if user.is_platform_user:
            return redirect(_after_login_url(user))
        return redirect(next_page or _after_login_url(user))

    return render_template('login.html')


# ─────────────────────────────────────────────────────────────────────────────
# REGISTER — Step 1: form + activation code
# ─────────────────────────────────────────────────────────────────────────────

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('factory.dashboard'))

    if request.method == 'POST':
        t = g.t

        act_code   = request.form.get('activation_code', '').strip().upper()
        fname      = request.form.get('factory_name', '').strip()
        fullname   = request.form.get('full_name', '').strip()
        email      = request.form.get('email', '').strip().lower()
        pw         = request.form.get('password', '')
        pw2        = request.form.get('confirm_password', '')
        city       = request.form.get('city', '').strip()
        reg_country= request.form.get('reg_country', 'SA').strip()
        country_cd = request.form.get('country_code', '+966').strip()
        phone_num  = request.form.get('phone_number', '').strip()

        # ── Validate activation code FIRST ─────────────────────
        factory_slot = None
        if not act_code:
            flash(t.get('err_code_required',
                        'كود التفعيل مطلوب.' if g.lang == 'ar'
                        else 'Activation code is required.'), 'danger')
            return render_template('register.html',
                                   factory_name=fname, full_name=fullname,
                                   email=email, country_codes=get_phone_codes(g.lang), countries=get_countries(g.lang))

        factory_slot = Factory.query.filter_by(activation_code=act_code).first()
        if not factory_slot:
            flash(t.get('err_code_invalid',
                        'كود التفعيل غير صحيح.' if g.lang == 'ar'
                        else 'Activation code is invalid.'), 'danger')
            return render_template('register.html',
                                   factory_name=fname, full_name=fullname,
                                   email=email, country_codes=get_phone_codes(g.lang), countries=get_countries(g.lang))

        # Code must not already be claimed (user_id still None means unclaimed)
        if factory_slot.user_id is not None:
            flash(t.get('err_code_used',
                        'كود التفعيل مستخدم بالفعل.' if g.lang == 'ar'
                        else 'Activation code already used.'), 'danger')
            return render_template('register.html',
                                   factory_name=fname, full_name=fullname,
                                   email=email, country_codes=get_phone_codes(g.lang), countries=get_countries(g.lang))

        # ── Remaining field validation ──────────────────────────
        errors = []
        if not fname:    errors.append(t.get('err_factory_required', 'Factory name required.'))
        if not fullname: errors.append(t.get('err_name_required', 'Full name required.'))
        if not email:    errors.append(t.get('err_email_required', 'Email required.'))
        if len(pw) < 8:  errors.append(t.get('err_pw_short', 'Password must be at least 8 chars.'))
        if pw != pw2:    errors.append(t.get('err_pw_mismatch', 'Passwords do not match.'))
        if not phone_num:errors.append(t.get('err_phone_required',
                                             'رقم الجوال مطلوب.' if g.lang == 'ar'
                                             else 'Phone number is required.'))
        if User.query.filter_by(email=email).first():
            errors.append(t.get('err_email_taken', 'Email already registered.'))

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('register.html',
                                   factory_name=fname, full_name=fullname,
                                   email=email, country_codes=get_phone_codes(g.lang), countries=get_countries(g.lang),
                                   activation_code=act_code)

        # ── Create user (inactive until OTP verified) ───────────
        user = User(
            email=email, full_name=fullname, role='factory',
            country_code=country_cd, phone_number=phone_num,
            is_active=False,          # activated after OTP
            is_phone_verified=False,
        )
        user.set_password(pw)
        otp = user.generate_otp(expires_minutes=15)
        db.session.add(user)
        db.session.flush()   # get user.id

        # ── Claim the factory slot ──────────────────────────────
        factory_slot.user_id = user.id
        factory_slot.name    = fname
        factory_slot.city    = city
        factory_slot.country = reg_country
        db.session.commit()

        # ── Mock OTP — store in session + show on screen ────────
        session['otp_user_id'] = user.id
        session['otp_phone']   = f'{country_cd}{phone_num}'

        # In production: send SMS here. For now, flash it.
        flash(
            f'{"كود التحقق (تجريبي):" if g.lang == "ar" else "OTP (mock):"} '
            f'<strong style="font-size:20px;letter-spacing:3px;">{otp}</strong>',
            'info'
        )
        return redirect(url_for('auth.verify_otp'))

    return render_template('register.html',
                           country_codes=get_phone_codes(g.lang), countries=get_countries(g.lang))


# ─────────────────────────────────────────────────────────────────────────────
# REGISTER — Step 2: OTP verification
# ─────────────────────────────────────────────────────────────────────────────

@auth_bp.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    if current_user.is_authenticated:
        return redirect(url_for('factory.dashboard'))

    uid = session.get('otp_user_id')
    if not uid:
        flash('Session expired. Please register again.', 'warning')
        return redirect(url_for('auth.register'))

    user = User.query.get(uid)
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('auth.register'))

    phone_display = session.get('otp_phone', '')

    if request.method == 'POST':
        entered = request.form.get('otp_code', '').strip()

        if user.verify_otp(entered):
            user.is_phone_verified = True
            user.is_active         = True
            user.clear_otp()
            db.session.commit()
            session.pop('otp_user_id', None)
            session.pop('otp_phone', None)

            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.commit()

            flash(
                'تم التحقق من رقم الجوال بنجاح! مرحباً.' if g.lang == 'ar'
                else 'Phone verified! Welcome.',
                'success'
            )
            return redirect(url_for('factory.dashboard'))
        else:
            flash(
                'الكود غير صحيح أو منتهي الصلاحية.' if g.lang == 'ar'
                else 'Invalid or expired OTP code.',
                'danger'
            )

    return render_template('verify_otp.html',
                           phone_display=phone_display,
                           user=user)


# ─────────────────────────────────────────────────────────────────────────────
# RESEND OTP
# ─────────────────────────────────────────────────────────────────────────────

@auth_bp.route('/resend-otp', methods=['POST'])
def resend_otp():
    uid = session.get('otp_user_id')
    if not uid:
        return redirect(url_for('auth.register'))
    user = User.query.get(uid)
    if not user:
        return redirect(url_for('auth.register'))

    otp = user.generate_otp(expires_minutes=15)
    db.session.commit()

    flash(
        f'{"كود التحقق الجديد (تجريبي):" if g.lang == "ar" else "New OTP (mock):"} '
        f'<strong style="font-size:20px;letter-spacing:3px;">{otp}</strong>',
        'info'
    )
    return redirect(url_for('auth.verify_otp'))


# ─────────────────────────────────────────────────────────────────────────────
# LOGOUT
# ─────────────────────────────────────────────────────────────────────────────

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))


def _after_login_url(user):
    # Superadmin OR system staff → admin panel
    if user.is_superadmin or user.role == 'staff':
        return url_for('admin.dashboard')
    return url_for('factory.dashboard')


# ══════════════════════════════════════════════════════════════
# FORGOT PASSWORD — 3 steps:
#   1. /forgot-password  → verify email + phone
#   2. /reset-password   → set new password (token in session)
# ══════════════════════════════════════════════════════════════

# Simple in-memory rate limiter (per IP, max 5 attempts per 15 min)
_reset_attempts: dict = {}  # {ip: [timestamp, ...]}

def _check_rate_limit(ip: str) -> bool:
    """Returns True if allowed, False if rate-limited."""
    import time
    now = time.time()
    window = 15 * 60  # 15 minutes
    attempts = _reset_attempts.get(ip, [])
    # Keep only attempts within window
    attempts = [t for t in attempts if now - t < window]
    _reset_attempts[ip] = attempts
    if len(attempts) >= 5:
        return False
    attempts.append(now)
    _reset_attempts[ip] = attempts
    return True


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    from models.user import User
    from flask import session

    if request.method == 'POST':
        ip = request.remote_addr or '0.0.0.0'
        if not _check_rate_limit(ip):
            flash(g.t.get('err_rate_limit',
                          'محاولات كثيرة — انتظر 15 دقيقة وأعد المحاولة.'), 'danger')
            return redirect(url_for('auth.forgot_password'))

        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        # Normalize phone: keep digits only for comparison
        import re
        phone_digits = re.sub(r'\D', '', phone)

        user = User.query.filter_by(email=email).first()

        # Verify both email AND phone match
        valid = False
        if user:
            user_phone_digits = re.sub(r'\D', '', user.full_phone or '')
            # Match last N digits (handles country code variations)
            n = min(len(phone_digits), len(user_phone_digits), 9)
            if n >= 7 and phone_digits[-n:] == user_phone_digits[-n:]:
                valid = True

        if valid:
            import secrets as _sec
            token = _sec.token_urlsafe(32)
            session['reset_token'] = token
            session['reset_uid']   = user.id
            session['reset_ip']    = ip
            import time
            session['reset_exp']   = time.time() + 900  # 15 min
            return redirect(url_for('auth.reset_password'))
        else:
            flash(g.t.get('err_reset_mismatch',
                          'البيانات غير مطابقة — تأكد من الإيميل ورقم الجوال.'), 'danger')

    return render_template('forgot_password.html')


@auth_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    from models.user import User
    from models.db import db
    from flask import session
    import time

    # Validate session token
    token   = session.get('reset_token')
    uid     = session.get('reset_uid')
    exp     = session.get('reset_exp', 0)
    sess_ip = session.get('reset_ip')
    ip      = request.remote_addr or '0.0.0.0'

    if not token or not uid or time.time() > exp or sess_ip != ip:
        flash(g.t.get('err_session_expired',
                      'انتهت صلاحية الجلسة — أعد طلب استعادة كلمة المرور.'), 'danger')
        return redirect(url_for('auth.forgot_password'))

    user = User.query.get(uid)
    if not user:
        flash(g.t.get('err_not_found', 'المستخدم غير موجود.'), 'danger')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        pw1 = request.form.get('password', '').strip()
        pw2 = request.form.get('password2', '').strip()

        if len(pw1) < 8:
            flash(g.t.get('err_password_short',
                          'كلمة المرور يجب أن تكون 8 أحرف على الأقل.'), 'danger')
            return redirect(url_for('auth.reset_password'))
        if pw1 != pw2:
            flash(g.t.get('err_password_mismatch',
                          'كلمتا المرور غير متطابقتين.'), 'danger')
            return redirect(url_for('auth.reset_password'))

        # Update password
        user.set_password(pw1)
        db.session.commit()

        # Log the event
        import logging
        logging.getLogger('gold_erp').info(
            f'[PASSWORD_RESET] user={user.email} ip={ip} '
            f'time={time.strftime("%Y-%m-%d %H:%M:%S")}'
        )

        # Clear session tokens
        session.pop('reset_token', None)
        session.pop('reset_uid',   None)
        session.pop('reset_exp',   None)
        session.pop('reset_ip',    None)

        flash(g.t.get('password_updated',
                      'تم تحديث كلمة المرور بنجاح. سجل الدخول.'), 'success')
        return redirect(url_for('auth.login'))

    return render_template('reset_password.html', user=user)
