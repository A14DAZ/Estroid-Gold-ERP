from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from sqlalchemy import text

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'warning'


def init_extensions(app):
    db.init_app(app)
    login_manager.init_app(app)

    from models.user import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    with app.app_context():
        from models.archive import ReportArchive   # register table
        from models.setting import AppSetting          # register app_setting table
        from models.subscription import (               # register subscription tables
            SubscriptionPlan, Payment, FactoryNote
        )
        db.create_all()
        _run_migrations()
        _seed_superadmin()
        _seed_subscription_plans()
        _seed_app_settings()


def _col_exists(conn, table, column):
    """Check if a column exists using SQLAlchemy text()."""
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


def _run_migrations():
    """
    Add any column that exists in the ORM model but not yet in the SQLite file.
    Safe to run on every startup — skips columns that already exist.
    """
    migrations = [
        # factory
        ('factory',          'activation_code',     'TEXT'),
        ('factory',          'subscription_expiry',  'DATE'),
        # inventory
        ('inventory',        'karat',                'INTEGER DEFAULT 24'),
        ('inventory',        'pure_weight',          'REAL DEFAULT 0.0'),
        # work_order
        ('work_order',       'customer_phone',       'TEXT DEFAULT ""'),
        ('work_order',       'notes',                'TEXT DEFAULT ""'),
        ('work_order',       'final_output_weight',  'REAL DEFAULT 0.0'),
        ('work_order',       'product_name',         'TEXT DEFAULT ""'),
        ('work_order',       'product_code',         'TEXT DEFAULT ""'),
        ('work_order',       'client_id',            'INTEGER'),
        # worker
        ('worker',           'notes',                'TEXT DEFAULT ""'),
        # production_stage
        ('production_stage', 'gold_gain',            'REAL DEFAULT 0.0'),
        ('production_stage', 'gold_actual_output',   'REAL DEFAULT 0.0'),
        ('production_stage', 'is_loss_high',         'INTEGER DEFAULT 0'),
        # user — phone + OTP
        ('user', 'country_code',                     'TEXT DEFAULT "+966"'),
        ('user', 'phone_number',                     'TEXT DEFAULT ""'),
        ('user', 'is_phone_verified',                'INTEGER DEFAULT 0'),
        ('user', 'otp_code',                         'TEXT'),
        ('user', 'otp_expires_at',                   'TIMESTAMP'),
        # stone — new weight fields
        ('stone', 'stone_unit',                      'TEXT DEFAULT "carat"'),
        ('stone', 'stone_required',                  'REAL DEFAULT 0.0'),
        ('stone', 'stone_given',                     'REAL DEFAULT 0.0'),
        ('stone', 'stone_returned',                  'REAL DEFAULT 0.0'),
        ('stone', 'stone_loss_carat',                'REAL DEFAULT 0.0'),
        # inventory_transaction — sales + purchases fields
        ('customer',             'code',         'TEXT DEFAULT ""'),
        ('worker',               'code',              'TEXT DEFAULT ""'),
        ('worker',               'termination_date',  'TEXT DEFAULT NULL'),
        ('worker',               'termination_notes', 'TEXT DEFAULT NULL'),
        ('worker',               'nationality',       'TEXT DEFAULT NULL'),
        ('worker',               'date_of_birth',     'TEXT DEFAULT NULL'),
        ('worker',               'id_number',         'TEXT DEFAULT NULL'),
        ('worker',               'start_date',        'TEXT DEFAULT NULL'),
        ('factory',              'logo_data',         'TEXT DEFAULT NULL'),
        ('factory',              'factory_code',      'TEXT DEFAULT NULL'),
        ('factory',              'factory_color',     "TEXT DEFAULT '#D4AF37'"),
        ('user',                 'last_login_at',     'DATETIME DEFAULT NULL'),
        ('user',                 'last_login_ip',     'TEXT DEFAULT NULL'),
        ('user',                 'api_key',           'TEXT DEFAULT NULL'),
        ('factory',              'country',           "TEXT DEFAULT 'SA'"),
        ('factory',              'address',           'TEXT DEFAULT '''),
        ('factory',              'whatsapp',          'TEXT DEFAULT '''),
        ('factory',              'website',           'TEXT DEFAULT '''),
        ('factory',              'instagram',         'TEXT DEFAULT '''),
        ('factory',              'twitter',           'TEXT DEFAULT '''),
        ('factory',              'commercial_reg',    'TEXT DEFAULT '''),
        ('factory',              'tax_number',        'TEXT DEFAULT '''),
        ('factory',              'description',       'TEXT DEFAULT '''),
        ('factory',              'invoice_footer',    'TEXT DEFAULT '''),
        ('factory',              'thank_you_msg',     'TEXT DEFAULT '''),
        ('factory',              'default_karat',     'INTEGER DEFAULT 21'),
        ('factory',              'default_currency',  "TEXT DEFAULT 'SAR'"),
        ('factory',              'weight_unit',       "TEXT DEFAULT 'g'"),
        ('factory',              'subscription_start','DATE DEFAULT NULL'),
        ('factory',              'subscription_price','REAL DEFAULT 0'),
        ('factory',              'grace_period_days', 'INTEGER DEFAULT 3'),
        ('factory',              'health_score',      'INTEGER DEFAULT 100'),
        # app_setting is created via db.create_all() — no ALTER needed
        ('user',                 'employee_code',     'TEXT DEFAULT NULL'),
        ('user',                 'system_role',       'TEXT DEFAULT "factory"'),
        ('worker',               'id_expiry_date',    'TEXT DEFAULT NULL'),
        ('worker',               'photo_data',        'TEXT DEFAULT NULL'),
        ('inventory_transaction', 'product_name',   'TEXT DEFAULT ""'),
        ('inventory_transaction', 'product_code',   'TEXT DEFAULT ""'),
        ('inventory_transaction', 'price_per_gram', 'REAL DEFAULT 0.0'),
        ('inventory_transaction', 'total_price',    'REAL DEFAULT 0.0'),
        ('inventory_transaction', 'op_code',        'TEXT DEFAULT ""'),
        ('inventory_transaction', 'sale_type',      'TEXT DEFAULT "raw_gold"'),
        ('inventory_transaction', 'is_return',      'INTEGER DEFAULT 0'),
        # product_inventory — input mode tracking
        ('product_inventory', 'input_mode',   'TEXT DEFAULT "per_unit"'),
        ('product_inventory', 'stored_total', 'REAL DEFAULT 0.0'),
    ]

    with db.engine.connect() as conn:
        for table, column, defn in migrations:
            try:
                if not _col_exists(conn, table, column):
                    conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {defn}'))
                    conn.commit()
                    print(f'  [Migration] Added {table}.{column}')
            except Exception as exc:
                print(f'  [Migration] Skip {table}.{column}: {exc}')

    # Backfill factory_code for existing factories without one
    try:
        with db.engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id FROM factory WHERE factory_code IS NULL OR factory_code = '' ORDER BY id"
            )).fetchall()
            counter = 1
            existing = conn.execute(text(
                "SELECT factory_code FROM factory WHERE factory_code IS NOT NULL AND factory_code != ''"
            )).fetchall()
            for row in existing:
                code = row[0]
                if code and code.upper().startswith('FAC-'):
                    try:
                        n = int(code.split('-')[1])
                        if n >= counter:
                            counter = n + 1
                    except Exception:
                        pass
            for row in rows:
                fid = row[0]
                code = 'FAC-' + str(counter).zfill(4)
                conn.execute(text("UPDATE factory SET factory_code = :c WHERE id = :i"),
                             {'c': code, 'i': fid})
                counter += 1
            conn.commit()
            if rows:
                print(f'  [Migration] Backfilled factory_code for {len(rows)} factories')
    except Exception as exc:
        print(f'  [Migration] factory_code backfill error: {exc}')


def _seed_subscription_plans():
    """Seed default subscription plans if none exist."""
    from models.subscription import SubscriptionPlan
    if SubscriptionPlan.query.count() == 0:
        plans = [
            SubscriptionPlan(slug='basic',      name='Basic',      price_month=99,  price_year=990,  max_workers=5,  max_orders=100, sort_order=1),
            SubscriptionPlan(slug='pro',        name='Pro',        price_month=199, price_year=1990, max_workers=15, max_orders=500, sort_order=2),
            SubscriptionPlan(slug='enterprise', name='Enterprise', price_month=399, price_year=3990, max_workers=0,  max_orders=0,   sort_order=3),
        ]
        for p in plans:
            db.session.add(p)
        db.session.commit()
        print('  [Seed] Subscription plans created')


def _seed_app_settings():
    """Ensure default platform settings exist."""
    from models.setting import AppSetting
    defaults = {
        'support_whatsapp':  '',   # e.g. 905396672357 (no +)
        'support_email':     '',   # e.g. support@estroid.com
        'platform_logo':     '',   # base64 image data
        'platform_name':     'Estroid Gold ERP',
        'platform_tagline':  'Smart ERP System for Gold Factories',
        'platform_version':  'v5.0',
    }
    for key, val in defaults.items():
        if not AppSetting.query.filter_by(key=key).first():
            db.session.add(AppSetting(key=key, value=val))
    db.session.commit()


def _seed_superadmin():
    from models.user import User
    from werkzeug.security import generate_password_hash
    if not User.query.filter_by(email='admin@goldplatform.com').first():
        sa = User(
            email='admin@goldplatform.com',
            password_hash=generate_password_hash('Admin@12345'),
            full_name='Platform Admin',
            role='superadmin',
        )
        db.session.add(sa)
        db.session.commit()
        print('  [Seed] Superadmin created: admin@goldplatform.com / Admin@12345')
