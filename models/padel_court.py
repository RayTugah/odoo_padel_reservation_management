# -*- coding: utf-8 -*-
from odoo import api, fields, models


class PadelCourt(models.Model):
    _name = 'padel.court'
    _description = 'Pista de padel'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence, name'

    name = fields.Char(string='Nombre', required=True, tracking=True)
    sequence = fields.Integer(string='Secuencia', default=10)
    active = fields.Boolean(string='Activa', default=True, tracking=True)
    allow_website_booking = fields.Boolean(string='Permitir reserva web', default=True, tracking=True)
    court_type = fields.Selection([
        ('outdoor', 'Exterior'),
        ('covered', 'Cubierta'),
        ('indoor', 'Interior'),
    ], string='Tipo de pista', default='outdoor')
    default_price = fields.Float(string='Precio por defecto')
    color = fields.Integer(string='Color')
    note = fields.Text(string='Notas internas')
    booking_count = fields.Integer(string='Reservas', compute='_compute_booking_count')

    def _compute_booking_count(self):
        grouped = self.env['padel.booking'].read_group(
            [('court_id', 'in', self.ids)], ['court_id'], ['court_id']
        )
        counts = {item['court_id'][0]: item['court_id_count'] for item in grouped if item.get('court_id')}
        for court in self:
            court.booking_count = counts.get(court.id, 0)

    def action_view_bookings(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Reservas de %s' % self.name,
            'res_model': 'padel.booking',
            'view_mode': 'list,form',
            'domain': [('court_id', '=', self.id)],
            'context': {'default_court_id': self.id},
        }
