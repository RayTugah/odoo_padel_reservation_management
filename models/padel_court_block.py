# -*- coding: utf-8 -*-
from odoo import fields, models


class PadelCourtBlock(models.Model):
    _name = 'padel.court.block'
    _description = 'Bloqueo de pista de padel'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'start_datetime desc'

    name = fields.Char(string='Motivo', required=True, default='Bloqueo de pista')
    court_id = fields.Many2one('padel.court', string='Pista', required=True, ondelete='cascade')
    start_datetime = fields.Datetime(string='Inicio', required=True, tracking=True)
    end_datetime = fields.Datetime(string='Fin', required=True, tracking=True)
    visible_on_website = fields.Boolean(string='Visible en web', default=False)
    active = fields.Boolean(default=True)

    def name_get(self):
        result = []
        for block in self:
            label = '%s - %s' % (block.court_id.name, block.name)
            result.append((block.id, label))
        return result
