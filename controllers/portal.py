# -*- coding: utf-8 -*-
from datetime import date, datetime, time, timedelta
import logging

import pytz

from odoo import fields, http, _
from odoo.exceptions import ValidationError
from odoo.http import request
from odoo.addons.portal.controllers.portal import pager as portal_pager
from odoo.addons.sale.controllers.portal import CustomerPortal

_logger = logging.getLogger(__name__)


class PadelPortalController(CustomerPortal):
    """Portal del cliente para reservas de padel."""

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'padel_booking_count' in counters:
            values['padel_booking_count'] = request.env['padel.booking'].sudo().search_count(self._portal_booking_domain())
        return values

    def _portal_partner(self):
        return request.env.user.partner_id

    def _portal_booking_domain(self):
        partner = self._portal_partner()
        domain = [('state', 'in', ['pending_payment', 'confirmed', 'done', 'cancelled'])]
        email = (partner.email or '').strip()
        if email:
            domain = ['&'] + domain + ['|', ('partner_id', '=', partner.id), ('customer_email', '=', email)]
        else:
            domain = ['&'] + domain + [('partner_id', '=', partner.id)]
        return domain

    def _get_portal_booking(self, booking_id):
        booking = request.env['padel.booking'].sudo().browse(booking_id)
        if not booking.exists():
            return False
        partner = self._portal_partner()
        if booking.partner_id and booking.partner_id.id == partner.id:
            return booking
        if booking.customer_email and partner.email and booking.customer_email.strip().lower() == partner.email.strip().lower():
            return booking
        return False

    def _config(self):
        ICP = request.env['ir.config_parameter'].sudo()
        durations = []
        for item in (ICP.get_param('padel.allowed_durations', '60,90,120') or '').split(','):
            item = item.strip()
            if item.isdigit():
                durations.append(int(item))
        return {
            'durations': durations or [60, 90, 120],
            'timezone': ICP.get_param('padel.timezone', 'Europe/Madrid') or 'Europe/Madrid',
            'payment_hold_minutes': int(ICP.get_param('padel.payment_hold_minutes', 10)),
        }

    def _timezone(self):
        try:
            return pytz.timezone(self._config()['timezone'])
        except Exception:
            return pytz.timezone('Europe/Madrid')

    def _local_to_utc(self, local_dt):
        tz = self._timezone()
        try:
            localized = tz.localize(local_dt, is_dst=None)
        except pytz.NonExistentTimeError:
            localized = tz.localize(local_dt + timedelta(hours=1), is_dst=True)
        except pytz.AmbiguousTimeError:
            localized = tz.localize(local_dt, is_dst=False)
        return localized.astimezone(pytz.UTC).replace(tzinfo=None)

    def _utc_to_local_string(self, utc_dt, fmt='%d/%m/%Y %H:%M'):
        if not utc_dt:
            return ''
        if isinstance(utc_dt, str):
            utc_dt = fields.Datetime.from_string(utc_dt)
        if utc_dt.tzinfo:
            aware = utc_dt.astimezone(pytz.UTC)
        else:
            aware = pytz.UTC.localize(utc_dt)
        return aware.astimezone(self._timezone()).strftime(fmt)

    def _is_available_for_update(self, booking, court, start_dt, end_dt):
        Booking = request.env['padel.booking'].sudo()
        domain = [
            ('id', '!=', booking.id),
            ('court_id', '=', court.id),
            ('state', 'in', Booking._blocking_states()),
            ('start_datetime', '<', fields.Datetime.to_string(end_dt)),
            ('end_datetime', '>', fields.Datetime.to_string(start_dt)),
        ]
        if Booking.search_count(domain):
            return False
        block_domain = [
            ('court_id', '=', court.id),
            ('active', '=', True),
            ('start_datetime', '<', fields.Datetime.to_string(end_dt)),
            ('end_datetime', '>', fields.Datetime.to_string(start_dt)),
        ]
        return not request.env['padel.court.block'].sudo().search_count(block_domain)

    def _padel_transactions_for_booking(self, booking):
        order = booking.sale_order_id.sudo()
        if not order:
            return request.env['payment.transaction'].sudo()
        Tx = request.env['payment.transaction'].sudo()
        txs = Tx
        if 'transaction_ids' in order._fields:
            txs |= order.transaction_ids.sudo()
        if 'sale_order_ids' in Tx._fields:
            txs |= Tx.search([('sale_order_ids', 'in', order.ids)])
        if order.name:
            txs |= Tx.search([('reference', 'ilike', order.name)])
        return txs.filtered(lambda tx: tx.state in ('done', 'authorized'))

    def _try_refund_booking_payment(self, booking):
        """Best-effort automatic refund.

        Odoo/payment provider APIs vary by version and provider. Redsys refunds
        can only be automated when the installed payment provider exposes a
        compatible refund method and the transaction has the needed provider data.
        This method never blocks the cancellation if no compatible refund method
        is available; it returns a message for the portal.
        """
        txs = self._padel_transactions_for_booking(booking)
        if not txs:
            return False, _('No se ha encontrado una transaccion pagada para devolver automaticamente.')
        amount = booking.price or 0.0
        for tx in txs:
            try:
                if hasattr(tx, 'action_refund'):
                    tx.action_refund()
                    return True, _('Se ha solicitado la devolucion automatica del pago.')
                if hasattr(tx, '_send_refund_request'):
                    try:
                        tx._send_refund_request(amount_to_refund=amount)
                    except TypeError:
                        try:
                            tx._send_refund_request(amount)
                        except TypeError:
                            tx._send_refund_request()
                    return True, _('Se ha solicitado la devolucion automatica del pago.')
            except Exception:
                _logger.exception('No se ha podido ejecutar la devolucion automatica de la reserva padel %s', booking.name)
                return False, _('La reserva se ha anulado, pero no se ha podido ejecutar la devolucion automatica. Revisa la transaccion de pago en Odoo/Redsys.')
        return False, _('La reserva se ha anulado, pero el proveedor de pago no expone una devolucion automatica compatible desde este modulo.')


    @http.route('/my/padel', type='http', auth='user', website=True)
    def portal_my_padel_bookings(self, page=1, **kw):
        Booking = request.env['padel.booking'].sudo()
        domain = self._portal_booking_domain()
        total = Booking.search_count(domain)
        pager = portal_pager(url='/my/padel', total=total, page=page, step=20)
        bookings = Booking.search(domain, order='name desc, id desc', limit=20, offset=pager['offset'])
        return request.render('odoo_padel_reservation_management.portal_my_padel_bookings', {
            'bookings': bookings,
            'pager': pager,
            'page_name': 'padel_bookings',
            'padel_success': request.session.pop('padel_portal_success', None),
            'padel_error': request.session.pop('padel_portal_error', None),
        })

    @http.route('/my/padel/<int:booking_id>', type='http', auth='user', website=True)
    def portal_padel_booking_detail(self, booking_id, **kw):
        booking = self._get_portal_booking(booking_id)
        if not booking:
            return request.redirect('/my/padel')
        return request.render('odoo_padel_reservation_management.portal_padel_booking_detail', {
            'booking': booking,
            'page_name': 'padel_bookings',
            'local_start': self._utc_to_local_string(booking.start_datetime),
            'local_end': self._utc_to_local_string(booking.end_datetime),
            'padel_success': request.session.pop('padel_portal_success', None),
            'padel_error': request.session.pop('padel_portal_error', None),
        })

    @http.route('/my/padel/<int:booking_id>/edit', type='http', auth='user', website=True, methods=['GET', 'POST'])
    def portal_padel_booking_edit_disabled(self, booking_id, **kw):
        booking = self._get_portal_booking(booking_id)
        if not booking:
            return request.redirect('/my/padel')
        request.session['padel_portal_error'] = _('La modificacion de reservas desde el portal esta desactivada. Solo puedes anular la reserva y realizar una nueva.')
        return request.redirect('/my/padel/%d' % booking.id)

    @http.route('/my/padel/<int:booking_id>/cancel', type='http', auth='user', website=True, methods=['POST'])
    def portal_padel_booking_cancel(self, booking_id, **post):
        booking = self._get_portal_booking(booking_id)
        if not booking:
            return request.redirect('/my/padel')
        if booking.state not in ('pending_payment', 'confirmed'):
            request.session['padel_portal_error'] = _('Esta reserva ya no se puede anular desde el portal.')
            return request.redirect('/my/padel/%d' % booking.id)
        try:
            booking.sudo().with_context(
                padel_portal_cancel=True,
                padel_portal_action='Anulacion solicitada por el cliente',
                padel_skip_refund=True,
            ).action_cancel()
            booking.sudo().action_send_portal_cancellation_notification()
            conditions_url = request.env['ir.config_parameter'].sudo().get_param('padel.conditions_url', '') or ''
            if conditions_url:
                msg = _(
                    'Tu reserva %(booking)s ha sido anulada correctamente y el hueco queda liberado. '
                    'El equipo de administracion valorara la devolucion de la misma en las proximas horas. '
                    'Por favor, revisa las condiciones de devolucion y de reserva del padel: %(conditions_url)s'
                ) % {'booking': booking.name, 'conditions_url': conditions_url}
            else:
                msg = _(
                    'Tu reserva %(booking)s ha sido anulada correctamente y el hueco queda liberado. '
                    'El equipo de administracion valorara la devolucion de la misma en las proximas horas.'
                ) % {'booking': booking.name}
            booking.sudo().message_post(body=_(
                'Anulacion realizada desde el portal. No se solicita devolucion automatica desde el portal. '
                'Se informa al cliente de que administracion valorara la devolucion en las proximas horas y se le remite a las condiciones de devolucion y reserva del padel.'
            ))
            request.session['padel_portal_success'] = msg
        except Exception as exc:
            request.session['padel_portal_error'] = str(exc.args[0] if getattr(exc, 'args', None) else exc)
        return request.redirect('/my/padel/%d' % booking.id)
