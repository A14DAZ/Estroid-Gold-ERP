from datetime import datetime
from models.db import db


class ReportArchive(db.Model):
    __tablename__ = 'report_archive'

    id            = db.Column(db.Integer, primary_key=True)
    factory_id    = db.Column(db.Integer, db.ForeignKey('factory.id'),
                              nullable=False, index=True)
    label         = db.Column(db.String(120), nullable=False)   # e.g. "Q1 2026"
    date_from     = db.Column(db.String(20), nullable=True)
    date_to       = db.Column(db.String(20), nullable=True)
    snapshot_json = db.Column(db.Text, nullable=False)           # JSON summary
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Archive {self.label}>'
