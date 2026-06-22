# -*- coding: utf-8 -*-
from odoo import models


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _padel_confirm_paid_bookings(self):
        """Confirm padel bookings linked to sale orders paid by these transactions.

        This is intentionally defensive because references can be generated as
        SO0001, SO0001-1, etc. The booking remains pending until a successful
        payment state is written on the transaction.
        """
        SaleOrder = self.env['sale.order'].sudo()
        Booking = self.env['padel.booking'].sudo()
        for tx in self.sudo():
            if tx.state not in ('done', 'authorized'):
                continue
            references = []
            if tx.reference:
                references.append(tx.reference)
                if '-' in tx.reference:
                    references.append(tx.reference.split('-')[0])
            orders = SaleOrder
            # Prefer the standard relation when available.
            if 'sale_order_ids' in tx._fields:
                orders |= tx.sale_order_ids.sudo()
            if references:
                orders |= SaleOrder.search([('name', 'in', list(set(references)))])
            if not orders:
                continue
            bookings = Booking.search([
                ('sale_order_id', 'in', orders.ids),
                ('state', '=', 'pending_payment'),
            ])
            if bookings:
                bookings.write({
                    'state': 'confirmed',
                    'payment_deadline': False,
                    'payment_transaction_id': tx.id,
                })
                for booking in bookings:
                    booking.message_post(body='Pago recibido. Reserva confirmada automaticamente desde la transaccion %s.' % (tx.reference or tx.id))
                bookings._create_and_post_invoice_if_needed()
        return True


    def _padel_cancel_unpaid_bookings(self):
        # Desactivado por criterio operativo: un pago cancelado/error no debe
        # anular automaticamente la reserva. La reserva quedara pendiente de pago
        # para que el personal la revise y la anule manualmente si corresponde.
        for tx in self.sudo():
            if tx.state in ('cancel', 'cancelled', 'error'):
                SaleOrder = self.env['sale.order'].sudo()
                Booking = self.env['padel.booking'].sudo()
                references = []
                if tx.reference:
                    references.append(tx.reference)
                    if '-' in tx.reference:
                        references.append(tx.reference.split('-')[0])
                orders = SaleOrder
                if 'sale_order_ids' in tx._fields:
                    orders |= tx.sale_order_ids.sudo()
                if references:
                    orders |= SaleOrder.search([('name', 'in', list(set(references)))])
                bookings = Booking.search([('sale_order_id', 'in', orders.ids), ('state', '=', 'pending_payment')]) if orders else Booking
                for booking in bookings:
                    booking.write({'payment_transaction_id': tx.id})
                    booking.message_post(body='Pago cancelado/error en la transaccion %s. La reserva NO se ha anulado automaticamente; queda pendiente de revision manual.' % (tx.reference or tx.id))
        return True

    def write(self, vals):
        res = super().write(vals)
        if 'state' in vals:
            self._padel_confirm_paid_bookings()
            self._padel_cancel_unpaid_bookings()
        return res
