# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    padel_booking_id = fields.Many2one('padel.booking', string='Reserva de padel', copy=False, ondelete='set null')
    padel_locked_price = fields.Float(string='Precio bloqueado padel', copy=False)

    def _padel_get_forced_price(self):
        self.ensure_one()
        if self.padel_locked_price:
            return self.padel_locked_price
        if self.padel_booking_id:
            return self.padel_booking_id.price or 0.0
        return False

    def _padel_restore_locked_prices(self):
        """Keep padel cart lines at the booking price.

        Website Sale may recompute cart lines when the customer changes address,
        pricelist, checkout step, fiscal position, etc. The padel booking price is
        calculated by the padel tariff engine, so it must not be replaced by the
        product list price, which is normally 0.00 for the generic padel product.
        """
        for line in self.sudo():
            if not line.padel_booking_id:
                continue
            forced_price = line._padel_get_forced_price()
            vals = {}
            if line.product_uom_qty != 1:
                vals['product_uom_qty'] = 1
            if line.price_unit != forced_price:
                vals['price_unit'] = forced_price
            if not line.padel_locked_price and forced_price:
                vals['padel_locked_price'] = forced_price
            if vals:
                super(SaleOrderLine, line.with_context(skip_padel_price_restore=True)).write(vals)
        return True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            booking_id = vals.get('padel_booking_id')
            if booking_id:
                booking = self.env['padel.booking'].sudo().browse(booking_id)
                price = vals.get('padel_locked_price') or booking.price or 0.0
                vals['price_unit'] = price
                vals['padel_locked_price'] = price
                vals['product_uom_qty'] = vals.get('product_uom_qty') or 1
        lines = super().create(vals_list)
        if not self.env.context.get('skip_padel_price_restore'):
            lines._padel_restore_locked_prices()
        return lines

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('skip_padel_price_restore'):
            self._padel_restore_locked_prices()
        return res


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _padel_booking_lines(self):
        return self.order_line.filtered(lambda line: line.padel_booking_id)

    def _padel_restore_locked_prices(self):
        for order in self.sudo():
            lines = order._padel_booking_lines()
            if lines:
                lines._padel_restore_locked_prices()
        return True

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('skip_padel_price_restore'):
            self._padel_restore_locked_prices()
        return res

    def action_confirm(self):
        self._padel_restore_locked_prices()
        for order in self:
            padel_lines = order._padel_booking_lines()
            if not padel_lines:
                continue
            expected = sum((line.padel_locked_price or line.padel_booking_id.price or 0.0) * line.product_uom_qty for line in padel_lines)
            if expected > 0 and order.amount_total <= 0:
                raise UserError(_('No se puede confirmar una reserva de padel con importe 0,00 €. Revise el carrito antes de continuar.'))
        return super().action_confirm()

    def action_cancel(self):
        bookings = self.env['padel.booking'].sudo().search([
            ('sale_order_id', 'in', self.ids),
            ('state', '=', 'pending_payment'),
        ])
        res = super().action_cancel()
        for booking in bookings:
            booking.message_post(body=_('Pedido de venta cancelado. La reserva NO se ha anulado automaticamente; queda pendiente de revision manual.'))
        return res

