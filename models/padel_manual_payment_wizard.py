# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PadelManualPaymentWizard(models.TransientModel):
    _name = 'padel.manual.payment.wizard'
    _description = 'Registrar pago manual reserva padel'

    booking_id = fields.Many2one('padel.booking', string='Reserva', required=True, readonly=True)
    partner_id = fields.Many2one(related='booking_id.partner_id', string='Cliente', readonly=True)
    amount = fields.Monetary(string='Importe a pagar', required=True)
    currency_id = fields.Many2one('res.currency', string='Moneda', required=True, default=lambda self: self.env.company.currency_id)
    pos_config_id = fields.Many2one(
        'pos.config',
        string='Punto de Venta',
        required=False,
        domain=[('id', 'in', [1, 2])],
        help='Punto de Venta/caja donde se registrara el cobro manual. Solo se permiten los TPV ID 1 e ID 2.',
    )
    pos_session_id = fields.Many2one(
        'pos.session',
        string='Sesion TPV',
        required=False,
        domain="[('state', 'in', ['opened', 'opening_control', 'closing_control']), ('config_id', '=', pos_config_id)]",
        help='Sesion abierta del Punto de Venta seleccionado donde se registrara el cobro para que aparezca en el cierre de caja.',
    )
    pos_payment_method_id = fields.Many2one(
        'pos.payment.method',
        string='Metodo de pago TPV',
        required=False,
        domain="[('id', 'in', available_pos_payment_method_ids)]",
    )
    available_pos_payment_method_ids = fields.Many2many(
        'pos.payment.method',
        compute='_compute_available_pos_payment_method_ids',
        string='Metodos TPV disponibles',
    )
    payment_date = fields.Date(string='Fecha de pago', required=True, default=fields.Date.context_today)
    communication = fields.Char(string='Concepto')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        booking = self.env['padel.booking'].browse(self.env.context.get('active_id'))
        if booking and booking.exists():
            residual = booking.price or 0.0
            if booking.invoice_id and booking.invoice_id.exists():
                residual = booking.invoice_id.amount_residual or booking.invoice_id.amount_total or residual
            res.update({
                'booking_id': booking.id,
                'amount': residual,
                'currency_id': (booking.sale_order_id.currency_id or self.env.company.currency_id).id if booking.sale_order_id else self.env.company.currency_id.id,
                'communication': _('Reserva padel %s') % (booking.name or ''),
            })
            # Por defecto se propone el Punto de Venta ID 2, pero el usuario puede cambiarlo
            # manualmente entre los TPV permitidos ID 1 e ID 2.
            default_config = self.env['pos.config'].sudo().browse(2)
            if default_config.exists():
                res['pos_config_id'] = default_config.id

            session = self.env['pos.session'].sudo().search([
                ('config_id', '=', 2),
                ('state', 'in', ['opened', 'opening_control'])
            ], limit=1)
            if not session:
                session = self.env['pos.session'].sudo().search([
                    ('config_id', '=', 2),
                    ('state', '=', 'closing_control')
                ], limit=1)
            if not session:
                session = self.env['pos.session'].sudo().search([
                    ('config_id', 'in', [1, 2]),
                    ('state', 'in', ['opened', 'opening_control'])
                ], limit=1)
            if not session:
                session = self.env['pos.session'].sudo().search([
                    ('config_id', 'in', [1, 2]),
                    ('state', '=', 'closing_control')
                ], limit=1)
            if session:
                res['pos_config_id'] = session.config_id.id if session.config_id else res.get('pos_config_id')
                res['pos_session_id'] = session.id
                methods = session.config_id.payment_method_ids if session.config_id and 'payment_method_ids' in session.config_id._fields else self.env['pos.payment.method']
                if methods:
                    res['pos_payment_method_id'] = methods[:1].id
            elif default_config.exists() and 'payment_method_ids' in default_config._fields and default_config.payment_method_ids:
                res['pos_payment_method_id'] = default_config.payment_method_ids[:1].id
        return res

    @api.depends('pos_config_id', 'pos_session_id')
    def _compute_available_pos_payment_method_ids(self):
        for wizard in self:
            methods = self.env['pos.payment.method']
            config = wizard.pos_config_id or wizard.pos_session_id.config_id
            if config and 'payment_method_ids' in config._fields:
                methods = config.payment_method_ids
            wizard.available_pos_payment_method_ids = methods

    @api.onchange('pos_config_id')
    def _onchange_pos_config_id(self):
        for wizard in self:
            session = self.env['pos.session']
            if wizard.pos_config_id:
                session = self.env['pos.session'].sudo().search([
                    ('config_id', '=', wizard.pos_config_id.id),
                    ('config_id', 'in', [1, 2]),
                    ('state', 'in', ['opened', 'opening_control']),
                ], limit=1)
                if not session:
                    session = self.env['pos.session'].sudo().search([
                        ('config_id', '=', wizard.pos_config_id.id),
                        ('config_id', 'in', [1, 2]),
                        ('state', '=', 'closing_control'),
                    ], limit=1)
            wizard.pos_session_id = session.id if session else False
            methods = wizard.available_pos_payment_method_ids
            wizard.pos_payment_method_id = methods[:1].id if methods else False

    @api.onchange('pos_session_id')
    def _onchange_pos_session_id(self):
        for wizard in self:
            if wizard.pos_session_id and wizard.pos_session_id.config_id and wizard.pos_config_id != wizard.pos_session_id.config_id:
                wizard.pos_config_id = wizard.pos_session_id.config_id.id
            methods = wizard.available_pos_payment_method_ids
            if wizard.pos_payment_method_id not in methods:
                wizard.pos_payment_method_id = methods[:1].id if methods else False

    def action_register_manual_payment(self):
        self.ensure_one()
        booking = self.booking_id.sudo()
        if not booking.exists():
            raise UserError(_('No se ha encontrado la reserva.'))
        if self.amount <= 0:
            raise UserError(_('El importe del pago debe ser superior a 0.'))
        if booking.state == 'cancelled':
            raise UserError(_('No se puede registrar un pago manual en una reserva cancelada.'))
        if not self.pos_config_id:
            raise UserError(_('Debe seleccionar el Punto de Venta donde se registrara el cobro.'))
        if self.pos_config_id.id not in (1, 2):
            raise UserError(_('Solo se puede registrar el cobro manual en los Puntos de Venta ID 1 o ID 2.'))
        if not self.pos_session_id:
            raise UserError(_('Debe seleccionar una sesion de TPV abierta para registrar el cobro en caja.'))
        if self.pos_session_id.config_id != self.pos_config_id:
            raise UserError(_('La sesion seleccionada no pertenece al Punto de Venta indicado.'))
        if not self.pos_payment_method_id:
            raise UserError(_('Debe seleccionar un metodo de pago TPV para registrar el cobro en caja.'))

        booking._padel_prepare_manual_pos_payment(
            amount=self.amount,
            pos_session=self.pos_session_id,
            pos_payment_method=self.pos_payment_method_id,
            payment_date=self.payment_date,
            communication=self.communication,
        )
        return {'type': 'ir.actions.act_window_close'}
