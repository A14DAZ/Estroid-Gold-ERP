from models.db import db


class AppSetting(db.Model):
    """Simple key-value store for platform-wide settings."""
    __tablename__ = 'app_setting'

    id    = db.Column(db.Integer, primary_key=True)
    key   = db.Column(db.String(80), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, default='')

    @classmethod
    def get(cls, key, default=''):
        row = cls.query.filter_by(key=key).first()
        return row.value if row else default

    @classmethod
    def set(cls, key, value):
        row = cls.query.filter_by(key=key).first()
        if row:
            row.value = value
        else:
            db.session.add(cls(key=key, value=value))
        db.session.commit()
