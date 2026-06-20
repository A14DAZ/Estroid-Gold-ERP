from datetime import datetime
from models.db import db

CUSTOMER_TYPES = ['client']


class Customer(db.Model):
    __tablename__ = 'customer'

    id         = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey('factory.id'), nullable=False, index=True)
    code       = db.Column(db.String(20), default='')   # CUST-0001
    name       = db.Column(db.String(120), nullable=False)
    phone      = db.Column(db.String(40))
    type       = db.Column(db.String(20), default='client')
    notes      = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    transactions = db.relationship('InventoryTransaction', backref='customer',
                                   lazy='dynamic', cascade='all,delete')

    def __repr__(self):
        return f'<Customer {self.name}>'
