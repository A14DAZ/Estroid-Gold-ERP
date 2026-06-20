from datetime import datetime
from models.db import db


class Worker(db.Model):
    __tablename__ = 'worker'

    id             = db.Column(db.Integer, primary_key=True)
    factory_id     = db.Column(db.Integer, db.ForeignKey('factory.id'), nullable=False, index=True)
    code           = db.Column(db.String(20), default='')   # EMP-0001
    name           = db.Column(db.String(120), nullable=False)
    specialty      = db.Column(db.String(80))
    phone          = db.Column(db.String(40))
    monthly_salary = db.Column(db.Float, default=0.0)
    notes          = db.Column(db.Text)
    is_active          = db.Column(db.Boolean, default=True)
    # Private — visible only in employee profile
    nationality        = db.Column(db.String(80),  nullable=True)
    date_of_birth      = db.Column(db.String(20),  nullable=True)   # YYYY-MM-DD
    id_number          = db.Column(db.String(40),  nullable=True)
    id_expiry_date     = db.Column(db.String(20),  nullable=True)   # YYYY-MM-DD
    photo_data         = db.Column(db.Text,         nullable=True)   # base64 data-URI
    start_date         = db.Column(db.Date,        nullable=True)   # auto-set on creation
    termination_date   = db.Column(db.Date,        nullable=True)
    termination_notes  = db.Column(db.Text,        nullable=True)
    created_at         = db.Column(db.DateTime, default=datetime.utcnow)

    stages = db.relationship('ProductionStage', backref='worker', lazy='dynamic',
                              foreign_keys='ProductionStage.worker_id')

    @property
    def total_stages(self):
        return self.stages.count()

    @property
    def completed_stages(self):
        return self.stages.filter_by(status='completed').count()

    @property
    def total_loss(self):
        from sqlalchemy import func
        from models.production import ProductionStage
        result = db.session.query(
            func.coalesce(func.sum(ProductionStage.loss_weight), 0)
        ).filter_by(worker_id=self.id, status='completed').scalar()
        return result or 0.0

    def __repr__(self):
        return f'<Worker {self.name}>'
