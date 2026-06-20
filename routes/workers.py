from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from models.db import db
from models.worker import Worker
from models.production import ProductionStage
from models.order import WorkOrder

workers_bp = Blueprint('workers', __name__)


def _factory():
    fac = current_user.factory
    if fac is None:
        from flask import abort
        abort(403)
    return fac


def _gen_worker_code(factory_id):
    count = Worker.query.filter_by(factory_id=factory_id).count()
    return f'EMP-{(count + 1):04d}'


def _process_photo(file_storage):
    """
    Convert uploaded image to base64 data-URI.
    Returns data-URI string or None on failure.
    Max 2MB. Allowed: jpg, jpeg, png, webp.
    """
    if not file_storage or not file_storage.filename:
        return None
    import base64
    ALLOWED = {'image/jpeg', 'image/png', 'image/webp', 'image/jpg'}
    mime = file_storage.content_type or 'image/jpeg'
    if mime not in ALLOWED:
        return None
    raw = file_storage.read()
    if len(raw) > 2 * 1024 * 1024:   # 2MB max
        return None
    return f'data:{mime};base64,' + base64.b64encode(raw).decode()


def _calc_age(date_of_birth_str):
    """Return age in years from YYYY-MM-DD string, or None."""
    if not date_of_birth_str:
        return None
    try:
        from datetime import date
        dob  = date.fromisoformat(date_of_birth_str)
        today = date.today()
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    except Exception:
        return None


def _id_expiry_status(id_expiry_str):
    """
    Returns dict: { days_left, status }
    status: 'ok' | 'warning' (≤30d) | 'expired'
    """
    if not id_expiry_str:
        return None
    try:
        from datetime import date
        exp   = date.fromisoformat(id_expiry_str)
        today = date.today()
        days  = (exp - today).days
        if days < 0:
            return {'days_left': days, 'status': 'expired'}
        elif days <= 30:
            return {'days_left': days, 'status': 'warning'}
        return {'days_left': days, 'status': 'ok'}
    except Exception:
        return None


def _stage_stats(stages):
    """
    Compute production stats from a list of stages.
    Returns dict with totals + per-karat breakdown.
    All calculations:
        produced = received - loss
        loss%    = loss / received * 100
    """
    completed = [s for s in stages if s.status == 'completed']
    total_recv = round(sum(s.received_weight or 0 for s in completed), 4)
    total_prod = round(sum(s.produced_weight or 0 for s in completed), 4)
    total_loss = round(sum(s.loss_weight     or 0 for s in completed), 4)
    loss_pct   = round((total_loss / total_recv * 100) if total_recv > 0 else 0.0, 2)
    efficiency = round(((total_prod / total_recv) * 100) if total_recv > 0 else 0.0, 2)

    # Per-karat breakdown
    karats = sorted(set(
        s.work_order.karat for s in completed if s.work_order
    ), reverse=True)
    karat_rows = []
    for k in karats:
        ks = [s for s in completed if s.work_order and s.work_order.karat == k]
        kr  = round(sum(s.received_weight or 0 for s in ks), 4)
        kp  = round(sum(s.produced_weight or 0 for s in ks), 4)
        kl  = round(sum(s.loss_weight     or 0 for s in ks), 4)
        klp = round((kl / kr * 100) if kr > 0 else 0.0, 2)
        karat_rows.append({'karat': k, 'recv': kr, 'prod': kp,
                           'loss': kl, 'loss_pct': klp})

    return {
        'total_recv': total_recv,
        'total_prod': total_prod,
        'total_loss': total_loss,
        'loss_pct':   loss_pct,
        'efficiency': efficiency,
        'karat_rows': karat_rows,
        'count':      len(completed),
    }


# ── List ──────────────────────────────────────────────────────────────────────

@workers_bp.route('/')
@login_required
def list_workers():
    fac = _factory()
    q   = request.args.get('q', '').strip()
    query = Worker.query.filter_by(factory_id=fac.id, is_active=True)
    if q:
        query = query.filter(
            db.or_(Worker.name.ilike(f'%{q}%'),
                   Worker.specialty.ilike(f'%{q}%'),
                   Worker.code.ilike(f'%{q}%')))
    workers = query.order_by(Worker.name).all()

    stats = {}
    for w in workers:
        stages = ProductionStage.query.filter_by(worker_id=w.id).all()
        stats[w.id] = _stage_stats(stages)

    total_salary = round(sum(w.monthly_salary or 0 for w in workers), 2)
    return render_template('workers.html', workers=workers, fac=fac,
                           stats=stats, total_salary=total_salary, q=q)


# ── Add ───────────────────────────────────────────────────────────────────────

@workers_bp.route('/new', methods=['GET', 'POST'])
@login_required
def create_worker():
    fac = _factory()
    if request.method == 'POST':
        name        = request.form.get('name', '').strip()
        spec        = request.form.get('specialty', '').strip()
        phone       = request.form.get('phone', '').strip()
        salary      = float(request.form.get('monthly_salary', 0) or 0)
        notes       = request.form.get('notes', '').strip()
        nationality = request.form.get('nationality', '').strip()
        dob         = request.form.get('date_of_birth', '').strip()
        id_num      = request.form.get('id_number', '').strip()

        if not name:
            flash('اسم الموظف مطلوب.', 'danger')
            return render_template('worker_form.html', fac=fac, worker=None,
                                   next_code=_gen_worker_code(fac.id))

        from datetime import date as _date
        code       = _gen_worker_code(fac.id)
        photo_data = _process_photo(request.files.get('employee_photo'))
        worker = Worker(factory_id=fac.id, name=name, specialty=spec,
                        phone=phone, monthly_salary=salary, notes=notes,
                        code=code,
                        nationality=nationality or None,
                        date_of_birth=dob or None,
                        id_number=id_num or None,
                        id_expiry_date=request.form.get('id_expiry_date','').strip() or None,
                        photo_data=photo_data,
                        start_date=_date.today())
        db.session.add(worker)
        db.session.commit()
        flash(f'تم إضافة الموظف: {name} ({code})', 'success')
        return redirect(url_for('workers.list_workers'))

    return render_template('worker_form.html', fac=fac, worker=None,
                           next_code=_gen_worker_code(fac.id))


# ── Detail ────────────────────────────────────────────────────────────────────

@workers_bp.route('/<int:wid>')
@login_required
def worker_detail(wid):
    fac    = _factory()
    worker = Worker.query.filter_by(id=wid, factory_id=fac.id).first_or_404()
    stages = ProductionStage.query.filter_by(worker_id=wid)\
                                  .order_by(ProductionStage.id.desc()).all()
    stats  = _stage_stats(stages)
    age    = _calc_age(worker.date_of_birth)
    id_expiry_info = _id_expiry_status(worker.id_expiry_date)
    return render_template('worker_detail.html', worker=worker, fac=fac,
                           stages=stages, stats=stats,
                           age=age, id_expiry_info=id_expiry_info)


# ── Edit ──────────────────────────────────────────────────────────────────────

@workers_bp.route('/<int:wid>/edit', methods=['GET', 'POST'])
@login_required
def edit_worker(wid):
    fac    = _factory()
    worker = Worker.query.filter_by(id=wid, factory_id=fac.id).first_or_404()
    if request.method == 'POST':
        worker.name           = request.form.get('name', worker.name).strip()
        worker.specialty      = request.form.get('specialty', '').strip()
        worker.phone          = request.form.get('phone', '').strip()
        worker.monthly_salary = float(request.form.get('monthly_salary', 0) or 0)
        worker.notes          = request.form.get('notes', '').strip()
        worker.nationality    = request.form.get('nationality', '').strip() or None
        worker.date_of_birth  = request.form.get('date_of_birth', '').strip() or None
        worker.id_number      = request.form.get('id_number', '').strip() or None
        worker.id_expiry_date = request.form.get('id_expiry_date', '').strip() or None
        # Update photo only if a new file was uploaded
        new_photo = _process_photo(request.files.get('employee_photo'))
        if new_photo:
            worker.photo_data = new_photo
        elif request.form.get('remove_photo'):
            worker.photo_data = None
        db.session.commit()
        flash('تم الحفظ.', 'success')
        return redirect(url_for('workers.worker_detail', wid=worker.id))
    return render_template('worker_form.html', fac=fac, worker=worker,
                           next_code=worker.code or '')


# ── Toggle / Delete ───────────────────────────────────────────────────────────

@workers_bp.route('/<int:wid>/toggle', methods=['POST'])
@login_required
def toggle_worker(wid):
    fac    = _factory()
    worker = Worker.query.filter_by(id=wid, factory_id=fac.id).first_or_404()
    worker.is_active = not worker.is_active
    db.session.commit()
    flash(f'{"مفعّل" if worker.is_active else "موقوف"}: {worker.name}', 'success')
    return redirect(url_for('workers.worker_detail', wid=worker.id))


@workers_bp.route('/<int:wid>/terminate', methods=['POST'])
@login_required
def terminate_worker(wid):
    """
    Terminate an employee:
    1) Verify current user's password
    2) Mark is_active=False + termination_date
    3) Calculate final settlement
    4) Redirect to termination summary
    """
    from models.user import User
    from werkzeug.security import check_password_hash
    from datetime import date as date_cls, timedelta

    fac    = _factory()
    worker = Worker.query.filter_by(id=wid, factory_id=fac.id).first_or_404()

    # ── Security: verify admin password ──────────────────────────
    password = request.form.get('confirm_password', '')
    if not current_user.check_password(password):
        flash('كلمة المرور غير صحيحة — لم يتم إنهاء الخدمة.', 'danger')
        return redirect(url_for('workers.worker_detail', wid=wid))

    # ── Calculate settlement ──────────────────────────────────────
    today          = date_cls.today()
    hire_date      = worker.created_at.date() if worker.created_at else today
    worked_days    = (today - hire_date).days
    daily_rate     = round((worker.monthly_salary or 0) / 30.0, 4)
    salary_due     = round(daily_rate * worked_days, 2)

    # Production stats for settlement note
    stages        = ProductionStage.query.filter_by(worker_id=wid).all()
    stats         = _stage_stats(stages)

    notes = (
        f'إنهاء الخدمة بتاريخ {today} | '
        f'أيام العمل: {worked_days} | '
        f'مستحق الراتب: {salary_due:.2f} ر.س | '
        f'إجمالي المنتج: {stats["total_prod"]:.3f}جم | '
        f'الفاقد: {stats["total_loss"]:.3f}جم'
    )
    if request.form.get('extra_notes', '').strip():
        notes += ' | ' + request.form.get('extra_notes').strip()

    # ── Mark terminated ───────────────────────────────────────────
    worker.is_active         = False
    worker.termination_date  = today
    worker.termination_notes = notes
    db.session.commit()

    flash(
        f'تم إنهاء خدمة {worker.name} ({worker.code}) — '
        f'مستحق الراتب: {salary_due:.2f} ر.س',
        'warning'
    )
    return redirect(url_for('workers.terminated_summary',
                            wid=wid,
                            salary_due=salary_due,
                            worked_days=worked_days))


@workers_bp.route('/<int:wid>/termination-summary')
@login_required
def terminated_summary(wid):
    fac    = _factory()
    worker = Worker.query.filter_by(id=wid, factory_id=fac.id).first_or_404()
    stages = ProductionStage.query.filter_by(worker_id=wid).all()
    stats  = _stage_stats(stages)

    salary_due  = float(request.args.get('salary_due', 0))
    worked_days = int(request.args.get('worked_days', 0))

    return render_template('worker_termination.html',
                           fac=fac, worker=worker,
                           stats=stats,
                           salary_due=salary_due,
                           worked_days=worked_days)


@workers_bp.route('/terminated')
@login_required
def list_terminated():
    fac     = _factory()
    workers = Worker.query.filter_by(factory_id=fac.id, is_active=False)\
                          .order_by(Worker.termination_date.desc()).all()
    stats   = {w.id: _stage_stats(
        ProductionStage.query.filter_by(worker_id=w.id).all()
    ) for w in workers}
    return render_template('workers_terminated.html',
                           fac=fac, workers=workers, stats=stats)


@workers_bp.route('/<int:wid>/delete', methods=['POST'])
@login_required
def delete_worker(wid):
    fac    = _factory()
    worker = Worker.query.filter_by(id=wid, factory_id=fac.id).first_or_404()
    ProductionStage.query.filter_by(worker_id=wid).update({'worker_id': None})
    db.session.delete(worker)
    db.session.commit()
    flash('تم حذف الموظف.', 'warning')
    return redirect(url_for('workers.list_workers'))


# ── Analysis ──────────────────────────────────────────────────────────────────

@workers_bp.route('/analysis')
@login_required
def analysis():
    fac     = _factory()
    workers = Worker.query.filter_by(factory_id=fac.id).order_by(Worker.name).all()
    rows    = []
    for w in workers:
        stages = ProductionStage.query.filter_by(worker_id=w.id).all()
        s      = _stage_stats(stages)
        rows.append({'worker': w, **s})

    # Aggregates — ALL converted to pure 24K before summing
    total_workers   = len(workers)
    total_recv_24   = round(sum(
        kr['recv'] * kr['karat'] / 24
        for r in rows for kr in r['karat_rows']
    ), 4)
    total_prod_24   = round(sum(
        kr['prod'] * kr['karat'] / 24
        for r in rows for kr in r['karat_rows']
    ), 4)
    total_loss_24   = round(sum(
        kr['loss'] * kr['karat'] / 24
        for r in rows for kr in r['karat_rows']
    ), 4)
    total_recv_raw  = round(sum(r['total_recv'] for r in rows), 4)
    avg_loss_pct    = round((total_loss_24 / total_recv_24 * 100)
                            if total_recv_24 > 0 else 0.0, 2)

    # Per-karat global breakdown
    all_karats_map = {}
    for r in rows:
        for kr in r['karat_rows']:
            k = kr['karat']
            all_karats_map.setdefault(k, {'karat': k, 'recv': 0.0, 'prod': 0.0, 'loss': 0.0})
            all_karats_map[k]['recv'] += kr['recv']
            all_karats_map[k]['prod'] += kr['prod']
            all_karats_map[k]['loss'] += kr['loss']
    global_karat_rows = sorted(all_karats_map.values(), key=lambda x: x['karat'], reverse=True)
    for g in global_karat_rows:
        g['recv'] = round(g['recv'], 4)
        g['prod'] = round(g['prod'], 4)
        g['loss'] = round(g['loss'], 4)
        g['recv_24'] = round(g['recv'] * g['karat'] / 24, 4)
        g['prod_24'] = round(g['prod'] * g['karat'] / 24, 4)
        g['loss_24'] = round(g['loss'] * g['karat'] / 24, 4)

    top_by_prod    = sorted(rows, key=lambda x: x['total_prod'],  reverse=True)[:5]
    top_by_loss    = sorted(rows, key=lambda x: x['loss_pct'])[:5]          # lowest loss = best
    top_by_ops     = sorted(rows, key=lambda x: x['count'],       reverse=True)[:5]

    return render_template('workers_analysis.html',
                           fac=fac, rows=rows,
                           total_workers=total_workers,
                           total_prod_24=total_prod_24,
                           total_recv_24=total_recv_24,
                           total_loss_24=total_loss_24,
                           total_recv_raw=total_recv_raw,
                           avg_loss_pct=avg_loss_pct,
                           global_karat_rows=global_karat_rows,
                           top_by_prod=top_by_prod,
                           top_by_loss=top_by_loss,
                           top_by_ops=top_by_ops)
