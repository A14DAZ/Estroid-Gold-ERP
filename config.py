import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'gold-saas-secret-2025-change-in-production')

    # Use DATABASE_URL from environment (Neon/render PostgreSQL),
    # fall back to local SQLite for development
    _db_url = os.environ.get('DATABASE_URL', '')
    if _db_url.startswith('postgres://'):
        _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = _db_url or ('sqlite:///' + os.path.join(BASE_DIR, 'gold_saas.db'))

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    ITEMS_PER_PAGE = 20

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False

config = {
    'development': DevelopmentConfig,
    'production':  ProductionConfig,
    'default':     DevelopmentConfig,
}
