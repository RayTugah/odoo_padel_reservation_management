# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    padel_opening_hour = fields.Float(
        string='Hora apertura padel',
        config_parameter='padel.opening_hour',
        default=9.0,
    )
    padel_closing_hour = fields.Float(
        string='Hora cierre padel',
        config_parameter='padel.closing_hour',
        default=22.0,
    )
    padel_slot_step_minutes = fields.Integer(
        string='Intervalo de planning',
        config_parameter='padel.slot_step_minutes',
        default=30,
    )
    padel_allowed_durations = fields.Char(
        string='Duraciones permitidas',
        config_parameter='padel.allowed_durations',
        default='60,90,120',
        help='Indicar duraciones en minutos separadas por coma. Ejemplo: 60,90,120',
    )
    padel_payment_hold_minutes = fields.Integer(
        string='Minutos de bloqueo pendiente de pago',
        config_parameter='padel.payment_hold_minutes',
        default=10,
    )
