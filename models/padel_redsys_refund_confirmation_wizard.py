# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class PadelRedsysRefundConfirmationWizard(models.TransientModel):
    _name = 'padel.redsys.refund.confirmation.wizard'
    _description = 'Confirmacion devolucion Redsys padel'

    booking_id = fields.Many2one('padel.booking', string='Reserva', required=True, readonly=True)
    booking_reference = fields.Char(related='booking_id.name', string='Referencia', readonly=True)
    amount = fields.Float(related='booking_id.price', string='Importe reserva', readonly=True)
    confirm_refund = fields.Boolean(string='Confirmo que quiero preparar la devolucion del dinero')

    def action_confirm_refund(self):
        self.ensure_one()
        if not self.confirm_refund:
            raise UserError(_('Debe marcar la casilla de confirmacion para continuar.'))
        return self.booking_id.action_request_redsys_refund()
