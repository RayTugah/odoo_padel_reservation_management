# -*- coding: utf-8 -*-
from datetime import timedelta
import base64
import json
import hmac
import hashlib

import requests
import pytz
from markupsafe import escape as html_escape

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except Exception:  # pragma: no cover - cryptography is present in Odoo, but keep module load safe.
    default_backend = Cipher = algorithms = modes = None

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError, UserError


class PadelBooking(models.Model):
    _name = 'padel.booking'
    _description = 'Reserva de pista de padel'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'start_datetime desc'
    _rec_name = 'calendar_name'

    name = fields.Char(string='Referencia', required=True, copy=False, readonly=True, default='Nuevo')
    calendar_name = fields.Char(string='Nombre en planning', compute='_compute_calendar_name', store=True)
    court_id = fields.Many2one('padel.court', string='Pista', required=True, tracking=True)
    partner_id = fields.Many2one('res.partner', string='Cliente', tracking=True)
    customer_name = fields.Char(string='Nombre Reserva', tracking=True)
    customer_phone = fields.Char(string='Telefono', tracking=True)
    customer_email = fields.Char(string='Email', tracking=True)
    start_datetime = fields.Datetime(string='Inicio', required=True, tracking=True)
    end_datetime = fields.Datetime(string='Fin', required=True, tracking=True)
    duration_minutes = fields.Integer(string='Duracion en minutos', compute='_compute_duration', store=True)
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('pending_payment', 'Pendiente de pago'),
        ('confirmed', 'Confirmada'),
        ('cancelled', 'Cancelada'),
        ('no_show', 'No presentado'),
        ('done', 'Finalizada'),
    ], string='Estado', default='pending_payment', tracking=True, required=True)
    origin = fields.Selection([
        ('manual', 'Manual'),
        ('website', 'Web'),
        ('portal', 'Portal'),
        ('internal', 'Interno'),
    ], string='Origen', default='manual', required=True, tracking=True)
    price = fields.Float(string='Importe', tracking=True)
    sale_order_id = fields.Many2one('sale.order', string='Pedido de venta', copy=False, tracking=True)
    invoice_id = fields.Many2one('account.move', string='Factura', copy=False, readonly=True, tracking=True)
    manual_payment_id = fields.Many2one('account.payment', string='Pago entrante manual', copy=False, readonly=True, tracking=True)
    manual_pos_order_id = fields.Many2one('pos.order', string='Pedido TPV manual', copy=False, readonly=True, tracking=True)
    manual_pos_payment_id = fields.Many2one('pos.payment', string='Pago TPV manual', copy=False, readonly=True, tracking=True)
    payment_transaction_id = fields.Many2one('payment.transaction', string='Transaccion de pago', copy=False, readonly=True, tracking=True)
    refund_transaction_id = fields.Many2one('payment.transaction', string='Transaccion de devolucion', copy=False, readonly=True, tracking=True)
    refund_credit_note_id = fields.Many2one('account.move', string='Factura rectificativa', copy=False, readonly=True, tracking=True)
    refund_payment_id = fields.Many2one('account.payment', string='Pago saliente devolucion', copy=False, readonly=True, tracking=True)
    refund_state = fields.Selection([
        ('none', 'Sin devolucion'),
        ('not_needed', 'No necesaria'),
        ('requested', 'Solicitada'),
        ('done', 'Devuelta'),
        ('manual', 'Revision manual'),
        ('error', 'Error devolucion'),
    ], string='Estado devolucion', default='none', copy=False, tracking=True)
    refunded_amount = fields.Float(string='Importe devuelto', copy=False, readonly=True, tracking=True)
    refund_date = fields.Datetime(string='Fecha devolucion', copy=False, readonly=True, tracking=True)
    refund_message = fields.Text(string='Mensaje devolucion', copy=False, readonly=True, tracking=True)
    confirmation_email_sent = fields.Boolean(string='Email confirmacion enviado', copy=False, readonly=True, tracking=True)
    portal_change_sale_order_id = fields.Many2one('sale.order', string='Pedido diferencia cambio portal', copy=False)
    portal_pending_start_datetime = fields.Datetime(string='Nuevo inicio pendiente portal', copy=False)
    portal_pending_end_datetime = fields.Datetime(string='Nuevo fin pendiente portal', copy=False)
    portal_pending_price = fields.Float(string='Nuevo importe pendiente portal', copy=False)
    portal_pending_price_difference = fields.Float(string='Diferencia pendiente portal', copy=False)
    payment_deadline = fields.Datetime(string='Limite de pago', tracking=True)
    note = fields.Text(string='Observaciones', tracking=True)
    color = fields.Integer(string='Color por estado', compute='_compute_state_color', store=True)


    def _padel_local_datetime_text(self, value, fmt='%d/%m/%Y %H:%M'):
        if not value:
            return ''
        try:
            tz_name = self.env['ir.config_parameter'].sudo().get_param('padel.timezone', 'Europe/Madrid') or 'Europe/Madrid'
            tz = pytz.timezone(tz_name)
            if value.tzinfo:
                aware = value.astimezone(pytz.UTC)
            else:
                aware = pytz.UTC.localize(value)
            return aware.astimezone(tz).strftime(fmt)
        except Exception:
            return fields.Datetime.to_string(value)

    def action_send_booking_confirmation_email(self):
        """Enviar email generico de confirmacion al cliente tras confirmar una reserva web."""
        for booking in self.sudo():
            if booking.confirmation_email_sent:
                booking.message_post(body=_('El email de confirmacion ya constaba como enviado anteriormente. No se reenvia para evitar duplicados.'))
                continue
            if booking.origin != 'website':
                continue
            email_to = booking.customer_email or (booking.partner_id.email if booking.partner_id and 'email' in booking.partner_id._fields else '')
            if not email_to:
                booking.message_post(body=_('No se ha enviado el email de confirmacion porque la reserva no tiene email de cliente.'))
                booking.write({'confirmation_email_sent': True})
                continue
            company = booking.company_id if 'company_id' in booking._fields and booking.company_id else self.env.company
            email_from = company.email or self.env.user.email or ''
            customer_name = booking.customer_name or booking.partner_id.display_name or _('cliente')
            base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
            portal_url = '%s/my/padel' % base_url.rstrip('/')
            conditions_url = self.env['ir.config_parameter'].sudo().get_param('padel.conditions_url', '') or '#' 
            fecha_txt = booking._padel_local_datetime_text(booking.start_datetime, '%d/%m/%Y')
            hora_inicio_txt = booking._padel_local_datetime_text(booking.start_datetime, '%H:%M')
            hora_fin_txt = booking._padel_local_datetime_text(booking.end_datetime, '%H:%M')
            importe_txt = ('%.2f' % (booking.price or 0.0)).replace('.', ',')
            body_html = ''.join([
                '<p>Hola.</p>',
                '<p>Le informamos de los datos de su reserva de pista de pádel con ID: <strong>%s</strong>, a nombre de <strong>%s</strong>.</p>' % (
                    html_escape(booking.name or ''), html_escape(customer_name or '')
                ),
                '<p>',
                '📅 <strong>Fecha:</strong> %s<br/>' % html_escape(fecha_txt),
                '🎾 <strong>Pista:</strong> %s<br/>' % html_escape(booking.court_id.display_name or ''),
                '🕒 <strong>Horario:</strong> de %s a %s<br/>' % (html_escape(hora_inicio_txt), html_escape(hora_fin_txt)),
                '💳 <strong>Importe abonado:</strong> %s €' % html_escape(importe_txt),
                '</p>',
                '<p><strong>Información para el acceso y uso de la pista:</strong></p>',
                '<p>🔑 <strong>Recogida de llaves</strong><br/>',
                '• Si el inicio de su reserva es durante el horario de apertura de recepción, las llaves deberán recogerse en recepción.<br/>',
                '• Si el inicio de su reserva es una vez cerrada la recepción, las llaves deberán solicitarse en el restaurante del camping.</p>',
                '<p>💡 <strong>Iluminación</strong><br/>',
                '• Si su reserva incluye iluminación, podrá activarla desde el cuadro eléctrico situado junto a la pista 3, pulsando el botón correspondiente a la pista reservada.</p>',
                '<p>📮 <strong>Entrega de llaves</strong><br/>',
                '• Una vez finalizado el uso de la pista, las llaves deberán depositarse en el buzón situado junto a la entrada de recepción.</p>',
                '<p>❌ <strong>Cancelaciones y cambios</strong><br/>',
                '• Puede solicitar la cancelación de su reserva desde el portal de reservas.<br/>',
                '• Las cancelaciones solicitadas con más de 5 horas de antelación al inicio de la reserva darán derecho a la devolución íntegra del importe abonado.<br/>',
                '• Las solicitudes recibidas con menos de 5 horas de antelación no tendrán derecho automático a devolución y serán estudiadas por la administración.<br/>',
                '• Si desea solicitar un cambio de horario, puede responder directamente a este correo y estudiaremos la disponibilidad existente.</p>',
                '<p><strong>Gestión de reservas:</strong><br/>',
                '<a href="%s">Ver o gestionar mis reservas de pádel</a></p>' % html_escape(portal_url),
                '<p><strong>Condiciones de venta y cancelación:</strong><br/>',
                '<a href="%s">Consultar condiciones de devolución y reserva del pádel</a></p>' % html_escape(conditions_url),
                '<p>Muchas gracias.</p>',
                '<p>%s</p>' % html_escape(company.name or ''),
            ])
            subject = 'Confirmación reserva pista de pádel %s' % (booking.name or '')
            try:
                mail = self.env['mail.mail'].sudo().create({
                    'subject': subject,
                    'email_from': email_from,
                    'email_to': email_to,
                    'body_html': body_html,
                    'auto_delete': False,
                })
                mail.send()
                booking.write({'confirmation_email_sent': True})
                booking.message_post(body=_(
                    'Email de confirmacion enviado automaticamente al cliente.<br/>'
                    '<strong>Destinatario:</strong> %s<br/>'
                    '<strong>Asunto:</strong> %s<br/>'
                    '<strong>ID correo Odoo:</strong> %s'
                ) % (html_escape(email_to), html_escape(subject), mail.id))
            except Exception as error:
                booking.write({'confirmation_email_sent': False})
                booking.message_post(body=_(
                    'No se ha podido enviar el email de confirmacion automaticamente.<br/>'
                    '<strong>Destinatario:</strong> %s<br/>'
                    '<strong>Asunto:</strong> %s<br/>'
                    '<strong>Error:</strong> %s'
                ) % (html_escape(email_to), html_escape(subject), html_escape(str(error))))
                raise
        return True


    def action_send_portal_cancellation_notification(self):
        """Enviar aviso interno cuando un cliente anula una reserva desde el portal."""
        for booking in self:
            company = self.env.company
            email_to = company.email or self.env.user.email or ''
            email_from = company.email or self.env.user.email or ''

            def fmt_dt(value):
                if not value:
                    return ''
                try:
                    tz_name = self.env['ir.config_parameter'].sudo().get_param('padel.timezone', 'Europe/Madrid') or 'Europe/Madrid'
                    tz = pytz.timezone(tz_name)
                    if value.tzinfo:
                        aware = value.astimezone(pytz.UTC)
                    else:
                        aware = pytz.UTC.localize(value)
                    return aware.astimezone(tz).strftime('%d/%m/%Y %H:%M')
                except Exception:
                    return fields.Datetime.to_string(value)

            state_label = dict(booking._fields['state'].selection).get(booking.state, booking.state or '')
            base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
            booking_url = '%s/web#id=%s&model=padel.booking&view_type=form' % (base_url.rstrip('/'), booking.id)
            body_html = ''.join([
                '<p><strong>Reserva de padel anulada desde el portal</strong></p>',
                '<p>',
                '<strong>Reserva:</strong> %s<br/>' % html_escape(booking.name or ''),
                '<strong>Enlace directo:</strong> <a href="%s">Abrir reserva en Odoo</a><br/>' % html_escape(booking_url),
                '<strong>Nombre reserva:</strong> %s<br/>' % html_escape(booking.customer_name or ''),
                '<strong>Cliente:</strong> %s<br/>' % html_escape(booking.partner_id.display_name or ''),
                '<strong>Email cliente:</strong> %s<br/>' % html_escape(booking.customer_email or (booking.partner_id.email if booking.partner_id and 'email' in booking.partner_id._fields else '') or ''),
                '<strong>Teléfono:</strong> %s<br/>' % html_escape(booking.customer_phone or ''),
                '<strong>Pista:</strong> %s<br/>' % html_escape(booking.court_id.display_name or ''),
                '<strong>Inicio:</strong> %s<br/>' % html_escape(fmt_dt(booking.start_datetime)),
                '<strong>Fin:</strong> %s<br/>' % html_escape(fmt_dt(booking.end_datetime)),
                '<strong>Importe:</strong> %.2f €<br/>' % (booking.price or 0.0),
                '<strong>Estado actual:</strong> %s<br/>' % html_escape(state_label),
                '<strong>Pedido:</strong> %s<br/>' % html_escape(booking.sale_order_id.name or ''),
                '</p>',
                '<p>El cliente ha anulado la reserva desde el portal. La devolución debe valorarse por administración según las condiciones de reserva y devolución del pádel.</p>',
            ])
            self.env['mail.mail'].sudo().create({
                'subject': 'Reserva de padel anulada desde el portal - %s' % (booking.name or ''),
                'email_from': email_from,
                'email_to': email_to,
                'body_html': body_html,
                'auto_delete': False,
            }).send()
            booking.message_post(body=_(
                'Aviso interno enviado por email: reserva de padel anulada desde el portal. Enlace interno: %s'
            ) % booking_url)
        return True

    @api.depends('state')
    def _compute_state_color(self):
        color_map = {
            'draft': 2,
            'pending_payment': 3,
            'confirmed': 10,
            'done': 7,
            'cancelled': 1,
            'no_show': 4,
        }
        for booking in self:
            booking.color = color_map.get(booking.state, 0)

    def _draft_forbidden_message(self):
        return _(
            'No se puede dejar una reserva de padel en borrador. '
            'Debe estar en Pendiente de pago, Confirmada, Finalizada o Cancelada.'
        )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Las reservas no deben quedar guardadas como borrador.
            # Si alguna entrada antigua o URL intenta crearla en borrador,
            # se convierte automaticamente a Pendiente de pago.
            if vals.get('state', 'pending_payment') == 'draft' and not self.env.context.get('allow_padel_create_draft'):
                vals['state'] = 'pending_payment'
            if vals.get('name', 'Nuevo') == 'Nuevo':
                vals['name'] = self.env['ir.sequence'].next_by_code('padel.booking') or 'Nuevo'
            self._fill_customer_fields_from_partner_vals(vals)
            if vals.get('start_datetime') and vals.get('end_datetime') and not vals.get('price'):
                vals['price'] = self._get_price_for_values(vals)
        records = super().create(vals_list)
        records._check_availability()
        for booking in records:
            booking.message_post(body=_(
                'Reserva creada. Estado: %s. Pista: %s. Importe: %.2f €.'
            ) % (booking.state, booking.court_id.display_name, booking.price or 0.0))
        confirmed_website = records.filtered(lambda booking: booking.state == 'confirmed' and booking.origin == 'website')
        if confirmed_website:
            confirmed_website.action_send_booking_confirmation_email()
        return records

    def _price_recalculation_fields(self):
        return {'court_id', 'start_datetime', 'end_datetime'}

    def _values_for_price_recalculation(self, vals):
        self.ensure_one()
        return {
            'court_id': vals.get('court_id', self.court_id.id),
            'start_datetime': vals.get('start_datetime', self.start_datetime),
            'end_datetime': vals.get('end_datetime', self.end_datetime),
            'backend_available_only': True,
        }

    def _sync_editable_sale_order_price(self):
        """Update linked draft quotation/cart lines after an internal price recalculation.

        Posted invoices and confirmed sale orders are intentionally not rewritten
        because they already have accounting impact. In those cases the booking
        amount is updated and staff can manage any accounting correction manually.
        """
        for booking in self.sudo():
            order = booking.sale_order_id
            if not order or order.state not in ('draft', 'sent'):
                continue
            lines = order.order_line.filtered(lambda line: getattr(line, 'padel_booking_id', False) and line.padel_booking_id.id == booking.id)
            vals = {
                'price_unit': booking.price or 0.0,
                'padel_locked_price': booking.price or 0.0,
                'product_uom_qty': 1,
            }
            for line in lines:
                line.with_context(skip_padel_price_restore=True).write(vals)
        return True

    @api.onchange('court_id', 'start_datetime', 'end_datetime')
    def _onchange_recalculate_price(self):
        for booking in self:
            if booking.court_id and booking.start_datetime and booking.end_datetime and booking.end_datetime > booking.start_datetime:
                booking.price = booking._get_price_for_values({
                    'court_id': booking.court_id.id,
                    'start_datetime': booking.start_datetime,
                    'end_datetime': booking.end_datetime,
                    'backend_available_only': True,
                })

    def write(self, vals):
        if vals.get('state') == 'draft' and not self.env.context.get('allow_padel_return_to_draft'):
            raise ValidationError(self._draft_forbidden_message())

        price_fields = self._price_recalculation_fields()
        must_recalculate_price = bool(price_fields & set(vals.keys())) and not self.env.context.get('skip_padel_price_recalculation')

        # A multi-record write can require a different price for each record.
        # Split it to avoid applying one calculated price to all records.
        if must_recalculate_price and len(self) > 1:
            for booking in self:
                booking.write(dict(vals))
            return True

        portal_action = self.env.context.get('padel_portal_action')
        old_states = {booking.id: booking.state for booking in self}
        old_prices = {booking.id: booking.price for booking in self}
        if vals.get('partner_id') and not {'customer_name', 'customer_phone', 'customer_email'} & set(vals.keys()):
            self._fill_customer_fields_from_partner_vals(vals)
        if must_recalculate_price and self:
            price_vals = self._values_for_price_recalculation(vals)
            vals = dict(vals)
            vals['price'] = self._get_price_for_values(price_vals)
        res = super().write(vals)
        if {'court_id', 'start_datetime', 'end_datetime', 'state'} & set(vals.keys()):
            self._check_availability()
        if must_recalculate_price:
            self._sync_editable_sale_order_price()
            for booking in self:
                old_price = old_prices.get(booking.id) or 0.0
                new_price = booking.price or 0.0
                if abs(old_price - new_price) >= 0.01:
                    booking.message_post(body=_(
                        'Importe recalculado automaticamente por cambio de pista/fecha/hora: %.2f € → %.2f €.'
                    ) % (old_price, new_price))
        if portal_action:
            for booking in self:
                booking.message_post(body=_(
                    'Cambio realizado desde portal: %s.'
                ) % portal_action)
        if 'state' in vals:
            confirmed_website = self.filtered(
                lambda booking: booking.state == 'confirmed'
                and old_states.get(booking.id) != 'confirmed'
                and booking.origin == 'website'
            )
            if confirmed_website:
                confirmed_website.action_send_booking_confirmation_email()
        return res


    @api.depends('name', 'partner_id', 'partner_id.name', 'customer_name')
    def _compute_calendar_name(self):
        for booking in self:
            booking.calendar_name = booking.partner_id.name or booking.customer_name or booking.name or _('Reserva de padel')

    def _get_partner_phone_value(self, partner):
        # Odoo 19 / custom databases may not include the mobile field on res.partner.
        # Read only fields that are available in the current registry.
        phone_fields = ['mobile', 'phone']
        for field_name in phone_fields:
            if field_name in partner._fields:
                value = partner[field_name]
                if value:
                    return value
        return False

    def _fill_customer_fields_from_partner_vals(self, vals):
        partner_id = vals.get('partner_id')
        if not partner_id:
            return vals
        partner = self.env['res.partner'].browse(partner_id)
        if not partner.exists():
            return vals
        if not vals.get('customer_name'):
            vals['customer_name'] = partner.name or False
        if not vals.get('customer_phone'):
            vals['customer_phone'] = self._get_partner_phone_value(partner)
        if not vals.get('customer_email'):
            vals['customer_email'] = partner.email if 'email' in partner._fields else False
        return vals

    @api.onchange('partner_id')
    def _onchange_partner_id_fill_customer_data(self):
        for booking in self:
            if booking.partner_id:
                booking.customer_name = booking.partner_id.name or False
                booking.customer_phone = booking._get_partner_phone_value(booking.partner_id)
                booking.customer_email = booking.partner_id.email if 'email' in booking.partner_id._fields else False
            else:
                booking.customer_name = False
                booking.customer_phone = False
                booking.customer_email = False

    @api.depends('start_datetime', 'end_datetime')
    def _compute_duration(self):
        for booking in self:
            if booking.start_datetime and booking.end_datetime:
                delta = booking.end_datetime - booking.start_datetime
                booking.duration_minutes = int(delta.total_seconds() / 60)
            else:
                booking.duration_minutes = 0

    @api.constrains('start_datetime', 'end_datetime')
    def _check_dates(self):
        for booking in self:
            if booking.start_datetime and booking.end_datetime and booking.end_datetime <= booking.start_datetime:
                raise ValidationError(_('La hora de fin debe ser posterior a la hora de inicio.'))

    def _blocking_states(self):
        return ['pending_payment', 'confirmed', 'done']

    def _check_availability(self):
        for booking in self:
            if booking.state not in booking._blocking_states():
                continue
            if not booking.court_id or not booking.start_datetime or not booking.end_datetime:
                continue

            overlap_domain = [
                ('id', '!=', booking.id),
                ('court_id', '=', booking.court_id.id),
                ('state', 'in', booking._blocking_states()),
                ('start_datetime', '<', booking.end_datetime),
                ('end_datetime', '>', booking.start_datetime),
            ]
            if self.search_count(overlap_domain):
                raise ValidationError(_('La pista ya tiene una reserva en ese tramo horario.'))

            block_domain = [
                ('court_id', '=', booking.court_id.id),
                ('active', '=', True),
                ('start_datetime', '<', booking.end_datetime),
                ('end_datetime', '>', booking.start_datetime),
            ]
            if self.env['padel.court.block'].search_count(block_domain):
                raise ValidationError(_('La pista esta bloqueada en ese tramo horario.'))

    def _get_pricing_timezone(self):
        tz_name = self.env['ir.config_parameter'].sudo().get_param('padel.timezone', 'Europe/Madrid') or 'Europe/Madrid'
        try:
            return pytz.timezone(tz_name)
        except Exception:
            return pytz.timezone('Europe/Madrid')

    def _utc_to_pricing_local(self, utc_dt):
        if not utc_dt:
            return utc_dt
        if utc_dt.tzinfo:
            aware = utc_dt.astimezone(pytz.UTC)
        else:
            aware = pytz.UTC.localize(utc_dt)
        return aware.astimezone(self._get_pricing_timezone())


    def _float_range_matches(self, start_float, end_float, hour_float):
        # Horario normal: 09:00-18:00. Horario cruzando medianoche: 22:00-02:00.
        if start_float <= end_float:
            return start_float <= hour_float < end_float
        return hour_float >= start_float or hour_float < end_float

    def _duration_light_prices(self, rule, duration):
        if duration == 60:
            return rule.no_light_price_60 or 0.0, rule.with_light_price_60 or 0.0
        if duration == 90:
            return rule.no_light_price_90 or 0.0, rule.with_light_price_90 or 0.0
        if duration == 120:
            return rule.no_light_price_120 or 0.0, rule.with_light_price_120 or 0.0
        return None, None

    def _minute_price_from_duration_light_rule(self, rule, duration, hour_float):
        no_light_price, with_light_price = self._duration_light_prices(rule, duration)
        if no_light_price is None or with_light_price is None or duration <= 0:
            return None
        if self._float_range_matches(rule.no_light_hour_from, rule.no_light_hour_to, hour_float):
            return no_light_price / duration
        if self._float_range_matches(rule.with_light_hour_from, rule.with_light_hour_to, hour_float):
            return with_light_price / duration
        return None

    def _minute_price_from_legacy_light_split_rule(self, rule, hour_float):
        divisor = rule.duration_minutes or 60
        if self._float_range_matches(rule.no_light_hour_from, rule.no_light_hour_to, hour_float):
            return (rule.no_light_price_hour or 0.0) / divisor
        if self._float_range_matches(rule.with_light_hour_from, rule.with_light_hour_to, hour_float):
            return (rule.with_light_price_hour or 0.0) / divisor
        return None

    def _best_duration_light_rule_for_date(self, court_id, local_dt, website_only=False, backend_only=False):
        weekday = str(local_dt.weekday())
        candidates = self._candidate_price_rules(
            court_id, weekday,
            website_only=website_only,
            backend_only=backend_only,
            pricing_type='duration_light_table'
        )
        if not candidates:
            return self.env['padel.price.rule']
        return sorted(candidates, key=lambda r: (-self._rule_specificity(r), r.sequence, r.id))[0]

    def _best_legacy_light_split_rule_for_date(self, court_id, local_dt, duration=0, website_only=False, backend_only=False):
        weekday = str(local_dt.weekday())
        candidates = self._candidate_price_rules(
            court_id, weekday,
            website_only=website_only,
            backend_only=backend_only,
            pricing_type='light_split'
        )
        candidates = candidates.filtered(lambda r: r.duration_minutes in (duration, 0))
        if not candidates:
            return self.env['padel.price.rule']
        return sorted(candidates, key=lambda r: (-self._rule_specificity(r), r.sequence, r.id))[0]

    def _get_duration_light_table_price(self, court_id, local_start, duration, website_only=False, backend_only=False):
        if duration not in (60, 90, 120):
            return None
        total = 0.0
        for minute_index in range(duration):
            local_minute = local_start + timedelta(minutes=minute_index)
            rule = self._best_duration_light_rule_for_date(
                court_id, local_minute,
                website_only=website_only,
                backend_only=backend_only
            )
            if not rule:
                return None
            hour_float = local_minute.hour + local_minute.minute / 60.0
            minute_price = self._minute_price_from_duration_light_rule(rule, duration, hour_float)
            if minute_price is None:
                return None
            total += minute_price
        return round(total, 2)

    def _get_light_split_price(self, court_id, local_start, duration, website_only=False, backend_only=False):
        if duration <= 0:
            return None
        total = 0.0
        for minute_index in range(duration):
            local_minute = local_start + timedelta(minutes=minute_index)
            rule = self._best_legacy_light_split_rule_for_date(
                court_id, local_minute, duration=duration,
                website_only=website_only,
                backend_only=backend_only
            )
            if not rule:
                return None
            hour_float = local_minute.hour + local_minute.minute / 60.0
            minute_price = self._minute_price_from_legacy_light_split_rule(rule, hour_float)
            if minute_price is None:
                return None
            total += minute_price
        return round(total, 2)

    def _rule_matches_hour(self, rule, hour_float):
        return self._float_range_matches(rule.hour_from, rule.hour_to, hour_float)

    def _rule_specificity(self, rule):
        score = 0
        if rule.court_scope == 'specific' and rule.court_id:
            score += 10
        if rule.weekday_scope == 'specific' and rule.weekday:
            score += 5
        if rule.duration_minutes:
            score += 2
        return score

    def _candidate_price_rules(self, court_id, weekday, website_only=False, backend_only=False, pricing_type=False):
        domain = [('active', '=', True)]
        if pricing_type:
            domain.append(('pricing_type', '=', pricing_type))
        if website_only:
            domain.append(('website_available', '=', True))
        if backend_only:
            domain.append(('backend_available', '=', True))
        rules = self.env['padel.price.rule'].search(domain)
        matched = rules.filtered(lambda r:
            (r.court_scope == 'all' or (r.court_scope == 'specific' and r.court_id.id == court_id))
            and (r.weekday_scope == 'all' or (r.weekday_scope == 'specific' and r.weekday == weekday))
        )
        return matched

    def _best_price_rule_at(self, court_id, local_dt, duration=0, website_only=False, backend_only=False, pricing_type=False):
        weekday = str(local_dt.weekday())
        hour_float = local_dt.hour + local_dt.minute / 60.0
        candidates = self._candidate_price_rules(court_id, weekday, website_only, backend_only, pricing_type)
        candidates = candidates.filtered(lambda r: self._rule_matches_hour(r, hour_float))
        if pricing_type == 'fixed':
            candidates = candidates.filtered(lambda r: r.duration_minutes in (duration, 0))
        elif pricing_type == 'hourly_prorated':
            candidates = candidates.filtered(lambda r: not r.duration_minutes)
        if not candidates:
            return self.env['padel.price.rule']
        return sorted(candidates, key=lambda r: (-self._rule_specificity(r), r.sequence, r.id))[0]

    def _get_hourly_prorated_price(self, court_id, local_start, duration, website_only=False, backend_only=False):
        if duration <= 0:
            return None
        total = 0.0
        breakdown = []
        current_rule = False
        current_minutes = 0
        for minute_index in range(duration):
            local_minute = local_start + timedelta(minutes=minute_index)
            rule = self._best_price_rule_at(
                court_id, local_minute, duration=0,
                website_only=website_only, backend_only=backend_only,
                pricing_type='hourly_prorated'
            )
            if not rule:
                return None
            total += (rule.price or 0.0) / 60.0
            if current_rule and current_rule.id == rule.id:
                current_minutes += 1
            else:
                if current_rule:
                    breakdown.append((current_rule, current_minutes))
                current_rule = rule
                current_minutes = 1
        if current_rule:
            breakdown.append((current_rule, current_minutes))
        return round(total, 2)

    @api.model
    def _get_price_for_values(self, vals):
        start = fields.Datetime.to_datetime(vals.get('start_datetime'))
        end = fields.Datetime.to_datetime(vals.get('end_datetime'))
        court_id = vals.get('court_id')
        if not start or not end:
            return 0.0
        duration = int((end - start).total_seconds() / 60)
        local_start = self._utc_to_pricing_local(start)
        website_only = bool(vals.get('website_available_only'))
        backend_only = bool(vals.get('backend_available_only'))

        # Nueva logica: una sola tarifa contiene los precios de 60, 90 y 120 minutos,
        # tanto sin luz como con luz. Si la reserva cruza tramos, se prorratea por minutos.
        duration_light_price = self._get_duration_light_table_price(
            court_id, local_start, duration,
            website_only=website_only, backend_only=backend_only
        )
        if duration_light_price is not None:
            return duration_light_price

        # Compatibilidad con la version anterior: una tarifa por duracion con dos precios.
        light_split_price = self._get_light_split_price(
            court_id, local_start, duration,
            website_only=website_only, backend_only=backend_only
        )
        if light_split_price is not None:
            return light_split_price

        # Compatibilidad con la version anterior: varias tarifas por hora proporcional.
        prorated_price = self._get_hourly_prorated_price(
            court_id, local_start, duration,
            website_only=website_only, backend_only=backend_only
        )
        if prorated_price is not None:
            return prorated_price

        # Si no existe cobertura completa por minutos, se mantiene la logica de precio fijo.
        rule = self._best_price_rule_at(
            court_id, local_start, duration=duration,
            website_only=website_only, backend_only=backend_only,
            pricing_type='fixed'
        )
        if rule:
            return rule.price
        court = self.env['padel.court'].browse(court_id) if court_id else self.env['padel.court']
        return court.default_price or 0.0

    def action_confirm(self):
        for booking in self:
            booking.state = 'confirmed'
        return True

    def action_return_to_draft(self):
        for booking in self:
            previous_state = booking.state
            booking.with_context(allow_padel_return_to_draft=True).write({
                'state': 'draft',
                'payment_deadline': False,
            })
            booking.message_post(body=_(
                'Reserva devuelta a borrador desde el estado %s para realizar modificaciones. '
                'Recuerde pasarla de nuevo a Pendiente de pago, Confirmada, Finalizada o Cancelada antes de darla por terminada.'
            ) % (previous_state or '-'))
        return True

    def action_pending_payment(self):
        minutes = int(self.env['ir.config_parameter'].sudo().get_param('padel.payment_hold_minutes', 10))
        for booking in self:
            booking.write({
                'state': 'pending_payment',
                'payment_deadline': fields.Datetime.now() + timedelta(minutes=minutes),
            })
        return True

    def _padel_paid_transactions(self):
        self.ensure_one()
        Tx = self.env['payment.transaction'].sudo()
        txs = Tx
        order = self.sale_order_id.sudo()
        if self.payment_transaction_id:
            txs |= self.payment_transaction_id.sudo()
        if order:
            if 'transaction_ids' in order._fields:
                txs |= order.transaction_ids.sudo()
            if 'sale_order_ids' in Tx._fields:
                txs |= Tx.search([('sale_order_ids', 'in', order.ids)])
            if order.name:
                txs |= Tx.search([('reference', 'ilike', order.name)])
        return txs.filtered(lambda tx: tx.state in ('done', 'authorized'))

    def _padel_refund_state_data(self, refund_tx, amount_to_refund, extra_message=False):
        refund_tx = refund_tx.sudo()
        state_message = getattr(refund_tx, 'state_message', False) or ''
        if refund_tx.state == 'done':
            return 'done', _('Devolucion realizada correctamente en la transaccion %s por %.2f €.') % (refund_tx.reference or refund_tx.id, amount_to_refund)
        if refund_tx.state == 'error':
            return 'error', _('Redsys/Odoo ha devuelto error al solicitar la devolucion en la transaccion %s: %s') % (refund_tx.reference or refund_tx.id, state_message or _('sin detalle'))
        if refund_tx.state in ('pending', 'authorized'):
            return 'requested', _('Devolucion enviada/solicitada al proveedor en la transaccion %s por %.2f €. Estado actual: %s.') % (refund_tx.reference or refund_tx.id, amount_to_refund, refund_tx.state)
        # Si queda en borrador, normalmente Odoo ha creado la transaccion hija pero no la ha enviado al proveedor.
        # En ese caso no se puede considerar devuelta ni solicitada.
        msg = _('Se ha creado la transaccion de devolucion %s por %.2f €, pero sigue en Borrador. En este estado NO se ha enviado la devolucion a Redsys. El flujo estandar de Odoo no saco la devolucion de Borrador. El modulo ha intentado tambien la devolucion REST directa contra Redsys; revise el detalle tecnico para ver la respuesta exacta.') % (refund_tx.reference or refund_tx.id, amount_to_refund)
        if extra_message:
            msg += _(' Detalle tecnico: %s') % extra_message
        return 'manual', msg


    def _redsys_field_value(self, record, field_names):
        for field_name in field_names:
            if field_name in record._fields:
                value = record[field_name]
                if value:
                    return value
        return False

    def _redsys_original_order_reference(self, tx):
        """Return the exact Ds_Order used in the original Redsys payment.

        Redsys refunds must be sent with the same order number used in the
        original authorization. The refund child transaction has its own Odoo
        reference, but Redsys will reject the refund if that child reference is
        sent as Ds_Order.
        """
        self.ensure_one()
        candidates = [
            self._redsys_field_value(tx, ['provider_reference']),
            tx.reference,
        ]
        for value in candidates:
            if value:
                return str(value).strip()
        return False

    def _redsys_currency_numeric_code(self, tx):
        currency = tx.currency_id
        if not currency:
            return '978'
        value = self._redsys_field_value(currency, ['numeric_code', 'iso_numeric'])
        if value:
            return str(value).zfill(3)
        if currency.name == 'EUR':
            return '978'
        if currency.name == 'USD':
            return '840'
        if currency.name == 'GBP':
            return '826'
        return '978'

    def _redsys_amount_to_cents(self, amount):
        return str(int(round(float(amount or 0.0) * 100))).zfill(12)

    def _redsys_get_credentials(self, tx):
        provider = tx.provider_id.sudo()
        if not provider or provider.code != 'redsys':
            raise UserError(_('La transaccion vinculada no pertenece al proveedor Redsys.'))
        merchant_code = self._redsys_field_value(provider, [
            'redsys_merchant_code', 'redsys_merchantcode', 'redsys_fuc',
            'merchant_code', 'merchantcode'
        ])
        terminal = self._redsys_field_value(provider, [
            'redsys_merchant_terminal', 'redsys_terminal', 'merchant_terminal', 'terminal'
        ]) or '1'
        secret_key = self._redsys_field_value(provider, [
            'redsys_secret_key', 'redsys_secretkey', 'redsys_signature_key',
            'redsys_key', 'secret_key', 'merchant_secret'
        ])
        if not merchant_code or not terminal or not secret_key:
            raise UserError(_(
                'No se han podido leer las credenciales Redsys del proveedor de pago. '
                'Revise que existan Merchant Code, Merchant Terminal y Secret Key.'
            ))
        return provider, str(merchant_code).strip(), str(terminal).strip(), str(secret_key).strip()

    def _redsys_rest_endpoint(self, provider):
        # Official Redsys REST endpoint for refunds is /sis/rest/trataPeticionREST.
        # Odoo's provider _redsys_get_api_url() returns /sis/realizarPago for redirects,
        # so build the REST URL explicitly from the provider state.
        state = getattr(provider, 'state', False)
        if state == 'enabled':
            return 'https://sis.redsys.es/sis/rest/trataPeticionREST'
        return 'https://sis-t.redsys.es:25443/sis/rest/trataPeticionREST'

    def _redsys_b64_json(self, values):
        raw = json.dumps(values, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        return base64.urlsafe_b64encode(raw).decode('ascii')

    def _redsys_signature(self, provider, merchant_parameters_b64, order_reference, secret_key):
        if hasattr(provider, '_redsys_calculate_signature'):
            return provider._redsys_calculate_signature(merchant_parameters_b64, order_reference, secret_key)
        if not (Cipher and algorithms and modes and default_backend):
            raise UserError(_('No esta disponible la libreria cryptography para firmar la peticion directa a Redsys.'))
        decoded_key = base64.b64decode(secret_key)
        encoded_order = order_reference.encode('utf-8').ljust(16, b'\x00')
        cipher = Cipher(algorithms.TripleDES(decoded_key), modes.CBC(b'\x00' * 8), backend=default_backend())
        encryptor = cipher.encryptor()
        derived_key = encryptor.update(encoded_order) + encryptor.finalize()
        signature = hmac.new(derived_key, merchant_parameters_b64.encode('utf-8'), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(signature).decode('ascii')

    def _redsys_decode_response(self, data):
        if isinstance(data, dict) and data.get('Ds_MerchantParameters'):
            value = data['Ds_MerchantParameters']
            padded = value + ('=' * ((4 - len(value) % 4) % 4))
            try:
                decoded = base64.urlsafe_b64decode(padded.encode('ascii')).decode('utf-8')
                return json.loads(decoded)
            except Exception:
                return {'raw_response': data, 'decode_error': True}
        return data if isinstance(data, dict) else {'raw_response': data}

    def _redsys_response_value(self, response_data, key):
        for candidate in (key, key.upper(), key.lower()):
            if isinstance(response_data, dict) and candidate in response_data:
                return response_data[candidate]
        return False

    def _redsys_mark_refund_tx_done(self, refund_tx, provider_reference, message):
        vals = {}
        if 'provider_reference' in refund_tx._fields and provider_reference:
            vals['provider_reference'] = provider_reference
        if vals:
            refund_tx.sudo().write(vals)
        for method_name in ['_set_done']:
            method = getattr(refund_tx.sudo(), method_name, False)
            if method:
                try:
                    method(state_message=message)
                    return True
                except TypeError:
                    try:
                        method(message)
                        return True
                    except TypeError:
                        try:
                            method()
                            return True
                        except Exception:
                            pass
                except Exception:
                    pass
        # Last fallback: keep Odoo consistent if the provider method did not expose a setter.
        write_vals = {'state': 'done'}
        if 'state_message' in refund_tx._fields:
            write_vals['state_message'] = message
        refund_tx.sudo().write(write_vals)
        return True

    def _redsys_mark_refund_tx_error(self, refund_tx, message):
        for method_name in ['_set_error']:
            method = getattr(refund_tx.sudo(), method_name, False)
            if method:
                try:
                    method(message)
                    return True
                except TypeError:
                    try:
                        method(state_message=message)
                        return True
                    except Exception:
                        pass
                except Exception:
                    pass
        write_vals = {'state': 'error'}
        if 'state_message' in refund_tx._fields:
            write_vals['state_message'] = message
        refund_tx.sudo().write(write_vals)
        return True

    def _redsys_direct_refund_request(self, original_tx, refund_tx, amount_to_refund):
        """Send a direct REST refund to Redsys when the Odoo connector leaves the child in draft."""
        self.ensure_one()
        provider, merchant_code, terminal, secret_key = self._redsys_get_credentials(original_tx)
        original_order = self._redsys_original_order_reference(original_tx)
        if not original_order:
            raise UserError(_('No se ha podido localizar el numero de pedido original enviado a Redsys.'))
        merchant_params = {
            'DS_MERCHANT_ORDER': original_order,
            'DS_MERCHANT_MERCHANTCODE': merchant_code,
            'DS_MERCHANT_TERMINAL': terminal,
            'DS_MERCHANT_TRANSACTIONTYPE': '3',
            'DS_MERCHANT_CURRENCY': self._redsys_currency_numeric_code(original_tx),
            'DS_MERCHANT_AMOUNT': self._redsys_amount_to_cents(amount_to_refund),
        }
        params_b64 = self._redsys_b64_json(merchant_params)
        signature = self._redsys_signature(provider, params_b64, original_order, secret_key)
        payload = {
            'Ds_SignatureVersion': 'HMAC_SHA256_V1',
            'Ds_MerchantParameters': params_b64,
            'Ds_Signature': signature,
        }
        endpoint = self._redsys_rest_endpoint(provider)
        try:
            response = requests.post(endpoint, json=payload, timeout=30)
        except Exception as exc:
            raise UserError(_('No se ha podido conectar con Redsys para enviar la devolucion: %s') % exc)
        try:
            response_json = response.json()
        except Exception:
            response_json = {'raw_response': response.text}
        response_data = self._redsys_decode_response(response_json)
        ds_response = str(self._redsys_response_value(response_data, 'Ds_Response') or '')
        ds_error = self._redsys_response_value(response_data, 'Ds_ErrorCode') or self._redsys_response_value(response_json, 'errorCode') or ''
        auth_code = self._redsys_response_value(response_data, 'Ds_AuthorisationCode') or ''
        if response.status_code >= 400:
            msg = _('Redsys ha devuelto HTTP %s al solicitar la devolucion. Respuesta: %s') % (response.status_code, response.text[:1000])
            self._redsys_mark_refund_tx_error(refund_tx, msg)
            return 'error', msg
        if ds_response and ds_response.zfill(4) == '0900':
            msg = _('Devolucion Redsys enviada correctamente por REST. Pedido Redsys: %s. Importe: %.2f €. Respuesta Ds_Response=%s. Autorizacion=%s.') % (
                original_order, amount_to_refund, ds_response, auth_code or '-'
            )
            self._redsys_mark_refund_tx_done(refund_tx, original_order, msg)
            return 'done', msg
        error_detail = ds_error or ds_response or response.text[:500] or response_json
        msg = _('Redsys no ha aceptado la devolucion REST. Pedido Redsys: %s. Importe: %.2f €. Detalle: %s') % (
            original_order, amount_to_refund, error_detail
        )
        self._redsys_mark_refund_tx_error(refund_tx, msg)
        return 'error', msg

    def _send_refund_child_to_provider(self, refund_tx, original_tx=False):
        """Try to send/process an Odoo refund child transaction.

        In this database, Odoo creates the refund child transaction correctly, but
        the screenshot shows it remains in Draft. The provider request must be
        triggered on that child refund transaction, not only on the original
        payment. Different Odoo/Redsys modules expose slightly different method
        names, so this method is intentionally defensive.
        """
        refund_tx = refund_tx.sudo()
        technical_notes = []

        method_names = [
            '_send_refund_request',
            '_send_payment_request',
            '_send_void_request',
        ]
        for method_name in method_names:
            method = getattr(refund_tx, method_name, False)
            if not method:
                continue
            try:
                method()
                technical_notes.append('%s: OK' % method_name)
                break
            except TypeError as exc:
                # Some provider methods accept kwargs only in certain versions. Try a
                # second minimal call with the amount context, then keep the detail.
                try:
                    method(amount_to_refund=abs(refund_tx.amount or 0.0))
                    technical_notes.append('%s(amount_to_refund): OK' % method_name)
                    break
                except Exception as exc2:
                    technical_notes.append('%s: %s / %s' % (method_name, exc, exc2))
            except Exception as exc:
                technical_notes.append('%s: %s' % (method_name, exc))

        # Try to run post-processing if the module exposes it. These calls are
        # guarded because method names/signatures vary between Odoo versions and
        # payment providers.
        for method_name in ['_post_process', '_post_process_after_done', 'action_post_process', 'action_process']:
            method = getattr(refund_tx, method_name, False)
            if not method:
                continue
            try:
                method()
                technical_notes.append('%s: OK' % method_name)
            except Exception as exc:
                technical_notes.append('%s: %s' % (method_name, exc))

        try:
            refund_tx.invalidate_recordset()
        except Exception:
            pass

        # If the standard Odoo/Redsys connector only created the child transaction
        # but did not move it out of Draft, send the refund directly through Redsys REST.
        if original_tx and refund_tx.state == 'draft' and original_tx.provider_id and original_tx.provider_id.code == 'redsys':
            try:
                direct_state, direct_message = self._redsys_direct_refund_request(original_tx, refund_tx, abs(refund_tx.amount or 0.0))
                technical_notes.append('direct_redsys_rest: %s - %s' % (direct_state, direct_message))
            except Exception as exc:
                technical_notes.append('direct_redsys_rest: %s' % (exc.args[0] if getattr(exc, 'args', None) else exc))
            try:
                refund_tx.invalidate_recordset()
            except Exception:
                pass
        return '; '.join(technical_notes)

    def _try_refund_payment(self, amount=None):
        """Request the provider refund using Odoo's refund child transaction.

        Odoo must create a child payment.transaction with operation='refund' and
        that child transaction must then be sent to the provider. In Redsys, if
        the child stays in Draft, the refund has not actually been transmitted.
        """
        for booking in self.sudo():
            amount_to_refund = amount if amount is not None else (booking.price or 0.0)
            if amount_to_refund <= 0:
                booking.write({
                    'refund_state': 'not_needed',
                    'refund_message': _('No hay importe a devolver.'),
                })
                booking.message_post(body=_('No se solicita devolucion porque el importe es 0,00 €.'))
                continue

            txs = booking._padel_paid_transactions()
            if not txs:
                msg = _('No se ha encontrado ninguna transaccion pagada/autorizada vinculada para solicitar la devolucion automaticamente.')
                booking.write({'refund_state': 'manual', 'refund_message': msg})
                booking.message_post(body=msg)
                continue

            tx = txs.sorted('id')[-1]
            booking.payment_transaction_id = tx.id

            existing_refunds = tx.child_transaction_ids.filtered(lambda r: r.operation == 'refund') if 'child_transaction_ids' in tx._fields else self.env['payment.transaction'].sudo()

            # If a previous attempt created a draft refund, send that same child
            # transaction instead of creating duplicates. This is exactly the case
            # visible in the screenshot.
            draft_refunds = existing_refunds.filtered(lambda r: r.state == 'draft' and abs(abs(r.amount or 0.0) - amount_to_refund) < 0.01)
            if draft_refunds:
                refund_tx = draft_refunds.sorted('id')[-1]
                try:
                    technical = booking._send_refund_child_to_provider(refund_tx, tx)
                    state, msg = booking._padel_refund_state_data(refund_tx, amount_to_refund, technical)
                    booking.write({
                        'refund_state': state,
                        'refund_transaction_id': refund_tx.id,
                        'refunded_amount': amount_to_refund if state in ('done', 'requested') else 0.0,
                        'refund_date': fields.Datetime.now(),
                        'refund_message': msg,
                    })
                    booking.message_post(body=msg)
                    if state == 'done':
                        booking._padel_finalize_refund_accounting(amount_to_refund, refund_tx=refund_tx)
                    continue
                except Exception as exc:
                    msg = _('No se ha podido procesar la transaccion hija de devolucion ya existente: %s') % (exc.args[0] if getattr(exc, 'args', None) else exc)
                    booking.write({'refund_state': 'error', 'refund_message': msg})
                    booking.message_post(body=msg)
                    continue

            done_refunds = existing_refunds.filtered(lambda r: r.state == 'done')
            done_amount = abs(sum(done_refunds.mapped('amount')))
            if done_amount and done_amount >= amount_to_refund - 0.01:
                refund_tx = done_refunds.sorted('id')[-1]
                msg = _('Ya existe una devolucion realizada para esta transaccion por %.2f €. Se crea/verifica la factura rectificativa y el pago saliente contable.') % done_amount
                booking.write({
                    'refund_state': 'done',
                    'refund_transaction_id': refund_tx.id,
                    'refunded_amount': done_amount,
                    'refund_date': fields.Datetime.now(),
                    'refund_message': msg,
                })
                booking.message_post(body=msg)
                booking._padel_finalize_refund_accounting(amount_to_refund, refund_tx=refund_tx)
                continue

            already_requested_amount = abs(sum(existing_refunds.filtered(lambda r: r.state in ('pending', 'authorized')).mapped('amount')))
            if already_requested_amount and already_requested_amount >= amount_to_refund - 0.01:
                msg = _('Ya existe una devolucion solicitada para esta transaccion por %.2f €. Revisa la transaccion de pago para confirmar el estado.') % already_requested_amount
                booking.write({
                    'refund_state': 'requested',
                    'refunded_amount': already_requested_amount,
                    'refund_date': fields.Datetime.now(),
                    'refund_message': msg,
                })
                booking.message_post(body=msg)
                continue

            try:
                refund_tx = self.env['payment.transaction'].sudo()

                if tx.state == 'done':
                    if hasattr(tx, 'action_refund'):
                        before_refunds = tx.child_transaction_ids if 'child_transaction_ids' in tx._fields else self.env['payment.transaction'].sudo()
                        tx.sudo().with_context(payment_backend_action=True).action_refund(amount_to_refund=amount_to_refund)
                        after_refunds = tx.child_transaction_ids if 'child_transaction_ids' in tx._fields else self.env['payment.transaction'].sudo()
                        refund_tx = (after_refunds - before_refunds).filtered(lambda r: r.operation == 'refund')[:1]
                        if not refund_tx and 'child_transaction_ids' in tx._fields:
                            refund_tx = tx.child_transaction_ids.filtered(lambda r: r.operation == 'refund').sorted('id')[-1:]
                    elif hasattr(tx, '_refund'):
                        refund_tx = tx.sudo().with_context(payment_backend_action=True)._refund(amount_to_refund=amount_to_refund)
                    else:
                        msg = _('El proveedor de pago vinculado no expone el flujo estandar de devolucion de Odoo. Revisar devolucion manualmente en Redsys/Odoo.')
                        booking.write({'refund_state': 'manual', 'refund_message': msg})
                        booking.message_post(body=msg)
                        continue

                elif tx.state == 'authorized':
                    if hasattr(tx, 'action_void'):
                        tx.sudo().with_context(payment_backend_action=True).action_void()
                        msg = _('La operacion estaba autorizada pero no capturada. Se ha solicitado la anulacion/cancelacion de la autorizacion al proveedor de pago.')
                        booking.write({
                            'refund_state': 'requested',
                            'refunded_amount': amount_to_refund,
                            'refund_date': fields.Datetime.now(),
                            'refund_message': msg,
                        })
                        booking.message_post(body=msg)
                        continue
                    msg = _('La transaccion esta autorizada, pero el proveedor no expone anulacion automatica compatible. Revisar manualmente.')
                    booking.write({'refund_state': 'manual', 'refund_message': msg})
                    booking.message_post(body=msg)
                    continue

                else:
                    msg = _('La transaccion vinculada no esta en un estado que permita devolucion automatica. Estado actual: %s.') % (tx.state or '-')
                    booking.write({'refund_state': 'manual', 'refund_message': msg})
                    booking.message_post(body=msg)
                    continue

                refund_tx = refund_tx.sudo() if refund_tx else refund_tx
                if refund_tx:
                    technical = booking._send_refund_child_to_provider(refund_tx, tx)
                    state, msg = booking._padel_refund_state_data(refund_tx, amount_to_refund, technical)
                    booking.write({
                        'refund_state': state,
                        'refund_transaction_id': refund_tx.id,
                        'refunded_amount': amount_to_refund if state in ('done', 'requested') else 0.0,
                        'refund_date': fields.Datetime.now(),
                        'refund_message': msg,
                    })
                    booking.message_post(body=msg)
                    if state == 'done':
                        booking._padel_finalize_refund_accounting(amount_to_refund, refund_tx=refund_tx)
                else:
                    msg = _('Se ha llamado al flujo de devolucion de Odoo, pero no se ha encontrado la transaccion hija de devolucion. Revisa la transaccion de pago original.')
                    booking.write({
                        'refund_state': 'manual',
                        'refunded_amount': 0.0,
                        'refund_date': fields.Datetime.now(),
                        'refund_message': msg,
                    })
                    booking.message_post(body=msg)
            except Exception as exc:
                msg = _('No se ha podido solicitar la devolucion automatica: %s') % (exc.args[0] if getattr(exc, 'args', None) else exc)
                booking.write({'refund_state': 'error', 'refund_message': msg})
                booking.message_post(body=msg)
        return True


    def _padel_get_refund_invoice(self):
        self.ensure_one()
        invoice = self.invoice_id.sudo() if self.invoice_id else self.env['account.move'].sudo()
        if invoice and invoice.exists() and invoice.move_type == 'out_invoice':
            return invoice
        order = self.sale_order_id.sudo()
        if order and 'invoice_ids' in order._fields:
            invoices = order.invoice_ids.filtered(lambda inv: inv.move_type == 'out_invoice' and inv.state == 'posted')
            if invoices:
                return invoices.sorted('id')[-1]
            invoices = order.invoice_ids.filtered(lambda inv: inv.move_type == 'out_invoice')
            if invoices:
                return invoices.sorted('id')[-1]
        return self.env['account.move'].sudo()

    def _padel_find_refund_journal(self, original_tx=False, original_invoice=False):
        journal = self.env['account.journal'].sudo()
        payment = self.env['account.payment'].sudo()
        if original_tx and 'payment_id' in original_tx._fields and original_tx.payment_id:
            payment = original_tx.payment_id.sudo()
        if payment and payment.exists() and payment.journal_id:
            return payment.journal_id
        if original_invoice and original_invoice.exists():
            try:
                payments_widget = original_invoice.invoice_payments_widget or {}
                if isinstance(payments_widget, dict):
                    content = payments_widget.get('content') or []
                    for item in content:
                        journal_name = item.get('journal_name')
                        if journal_name:
                            journal = self.env['account.journal'].sudo().search([('name', '=', journal_name)], limit=1)
                            if journal:
                                return journal
            except Exception:
                pass
        journal = self.env['account.journal'].sudo().search([('type', 'in', ['bank', 'cash'])], limit=1)
        return journal

    def _padel_create_refund_credit_note(self, amount_to_refund):
        self.ensure_one()
        invoice = self._padel_get_refund_invoice()
        if not invoice:
            raise UserError(_('No se ha encontrado una factura de cliente vinculada para crear la factura rectificativa.'))
        if invoice.state != 'posted':
            try:
                invoice.action_post()
            except Exception as exc:
                raise UserError(_('No se ha podido publicar la factura original antes de rectificarla: %s') % (exc.args[0] if getattr(exc, 'args', None) else exc))

        existing = self.refund_credit_note_id.sudo() if self.refund_credit_note_id else self.env['account.move'].sudo()
        if existing and existing.exists():
            if existing.state == 'draft':
                existing.action_post()
            return existing

        amount_to_refund = float(amount_to_refund or 0.0)
        full_refund = abs((invoice.amount_total or 0.0) - amount_to_refund) < 0.01
        credit_note = self.env['account.move'].sudo()
        today = fields.Date.context_today(self)

        if full_refund and hasattr(invoice, '_reverse_moves'):
            defaults = [{
                'invoice_date': today,
                'date': today,
                'ref': _('Devolucion reserva padel %s') % self.name,
            }]
            credit_note = invoice._reverse_moves(default_values_list=defaults, cancel=False)
            credit_note = credit_note[:1]
        else:
            # Fallback for partial/different amount refunds: create a proportional credit note
            # from the invoice lines. In normal padel reservations this should match the full
            # invoice amount, but this keeps the accounting flow usable if there are taxes or
            # adjusted prices.
            factor = 1.0
            if invoice.amount_total:
                factor = amount_to_refund / invoice.amount_total
            line_commands = []
            normal_lines = invoice.invoice_line_ids.filtered(lambda l: not l.display_type)
            for line in normal_lines:
                line_commands.append((0, 0, {
                    'product_id': line.product_id.id if line.product_id else False,
                    'name': _('Rectificacion %s') % (line.name or self.name),
                    'quantity': line.quantity,
                    'price_unit': (line.price_unit or 0.0) * factor,
                    'tax_ids': [(6, 0, line.tax_ids.ids)],
                    'account_id': line.account_id.id,
                }))
            if not line_commands:
                account = False
                if invoice.invoice_line_ids:
                    account = invoice.invoice_line_ids[0].account_id.id
                if not account:
                    raise UserError(_('No se ha encontrado cuenta contable para crear la factura rectificativa.'))
                line_commands.append((0, 0, {
                    'name': _('Devolucion reserva padel %s') % self.name,
                    'quantity': 1,
                    'price_unit': amount_to_refund,
                    'account_id': account,
                }))
            credit_note = self.env['account.move'].sudo().create({
                'move_type': 'out_refund',
                'partner_id': invoice.partner_id.id,
                'invoice_date': today,
                'date': today,
                'reversed_entry_id': invoice.id,
                'ref': _('Devolucion reserva padel %s') % self.name,
                'invoice_line_ids': line_commands,
            })

        if credit_note and credit_note.state == 'draft':
            credit_note.action_post()
        self.write({'refund_credit_note_id': credit_note.id})
        self.message_post(body=_('Factura rectificativa creada/publicada para la devolucion: %s.') % (credit_note.name or credit_note.display_name))
        return credit_note

    def _padel_register_refund_outgoing_payment(self, credit_note, amount_to_refund, original_tx=False):
        self.ensure_one()
        if self.refund_payment_id and self.refund_payment_id.exists():
            return self.refund_payment_id
        if not credit_note or not credit_note.exists():
            raise UserError(_('No hay factura rectificativa para registrar el pago saliente.'))
        if credit_note.state != 'posted':
            credit_note.action_post()
        journal = self._padel_find_refund_journal(original_tx=original_tx, original_invoice=self._padel_get_refund_invoice())
        if not journal:
            raise UserError(_('No se ha encontrado diario de banco/caja para registrar el pago saliente de la devolucion.'))
        ctx = {
            'active_model': 'account.move',
            'active_ids': credit_note.ids,
            'active_id': credit_note.id,
        }
        vals = {
            'amount': abs(amount_to_refund or credit_note.amount_residual or credit_note.amount_total),
            'payment_date': fields.Date.context_today(self),
            'journal_id': journal.id,
        }
        wizard = self.env['account.payment.register'].sudo().with_context(**ctx).create(vals)
        payments = wizard._create_payments()
        payment = payments[:1] if payments else self.env['account.payment'].sudo()
        if payment:
            self.write({'refund_payment_id': payment.id})
            self.message_post(body=_('Pago saliente de devolucion registrado en Odoo: %s por %.2f €.') % (payment.name or payment.display_name, abs(amount_to_refund or 0.0)))
        return payment

    def _padel_finalize_refund_accounting(self, amount_to_refund, refund_tx=False):
        """Create accounting documents after Redsys confirms the refund.

        The Redsys REST refund moves money outside the normal Odoo payment wizard.
        To keep Accounting consistent, create a customer credit note and register
        an outbound payment against that credit note.
        """
        for booking in self.sudo():
            try:
                credit_note = booking._padel_create_refund_credit_note(amount_to_refund)
            except Exception as exc:
                msg = _('La devolucion Redsys se ha realizado, pero no se ha podido crear la factura rectificativa automaticamente: %s') % (exc.args[0] if getattr(exc, 'args', None) else exc)
                booking.message_post(body=msg)
                booking.write({'refund_message': (booking.refund_message or '') + '\n' + msg})
                continue
            try:
                booking._padel_register_refund_outgoing_payment(credit_note, amount_to_refund, original_tx=booking.payment_transaction_id)
            except Exception as exc:
                msg = _('La factura rectificativa se ha creado, pero no se ha podido registrar automaticamente el pago saliente: %s') % (exc.args[0] if getattr(exc, 'args', None) else exc)
                booking.message_post(body=msg)
                booking.write({'refund_message': (booking.refund_message or '') + '\n' + msg})
        return True


    def _cleanup_unpaid_checkout(self):
        """Clean draft website checkout documents for an unpaid/cancelled booking.

        The booking is kept as Cancelada for traceability, but its draft cart/order
        is cancelled or emptied so the same customer cannot continue an old checkout.
        """
        for booking in self.sudo():
            order = booking.sale_order_id.sudo()
            if not order:
                continue
            paid_txs = booking._padel_paid_transactions()
            if paid_txs:
                continue
            try:
                padel_lines = order.order_line.filtered(lambda l: getattr(l, 'padel_booking_id', False) and l.padel_booking_id.id == booking.id)
                if order.state in ('draft', 'sent'):
                    if padel_lines:
                        padel_lines.unlink()
                    if not order.order_line and hasattr(order, 'action_cancel'):
                        order.action_cancel()
                booking.message_post(body=_('Carrito/pedido web pendiente limpiado al anular la reserva sin pago.'))
            except Exception as exc:
                booking.message_post(body=_('No se ha podido limpiar automaticamente el carrito/pedido web pendiente: %s') % (exc.args[0] if getattr(exc, 'args', None) else exc))
        return True

    def action_cancel(self):
        portal_cancel = self.env.context.get('padel_portal_cancel')
        refund_on_cancel = self.env.context.get('padel_refund_on_cancel')
        skip_refund = self.env.context.get('padel_skip_refund')
        before_states = {booking.id: booking.state for booking in self}
        self.write({'state': 'cancelled', 'payment_deadline': False})
        unpaid_to_cleanup = self.filtered(lambda b: before_states.get(b.id) == 'pending_payment')
        if unpaid_to_cleanup:
            unpaid_to_cleanup._cleanup_unpaid_checkout()
        for booking in self:
            if self.env.context.get('padel_payment_cancel'):
                origin_msg = _('Reserva anulada automaticamente por pago cancelado o fallido.')
            else:
                origin_msg = _('Reserva anulada desde portal.') if portal_cancel else _('Reserva anulada.')
            booking.message_post(body=origin_msg)
        if refund_on_cancel and not skip_refund and not portal_cancel:
            to_refund = self.filtered(lambda b: before_states.get(b.id) == 'confirmed')
            if to_refund:
                to_refund._try_refund_payment()
        elif portal_cancel:
            for booking in self:
                booking.write({
                    'refund_state': 'manual',
                    'refund_message': _('Devolucion no automatica desde portal. El equipo de administracion valorara la devolucion en las proximas horas segun las condiciones de devolucion y reserva del padel.'),
                })
        return True


    def action_open_redsys_refund_warning(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Confirmar devolución Redsys'),
            'res_model': 'padel.redsys.refund.confirmation.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_booking_id': self.id},
        }

    def action_request_redsys_refund(self):
        """Manual back-office button to request a Redsys/Odoo refund.

        Portal cancellations never call this method. It is intended only for staff
        users from the booking form, so reception/administration can request the
        refund from Odoo without opening the Redsys back office.
        """
        for booking in self:
            if booking.price <= 0:
                raise UserError(_('No se puede solicitar devolucion porque la reserva no tiene importe.'))
            if booking.refund_state in ('done', 'requested'):
                raise UserError(_('Ya existe una devolucion realizada o solicitada para esta reserva. Revise el estado de devolucion.'))
            paid_txs = booking._padel_paid_transactions()
            if not paid_txs:
                raise UserError(_('No se ha encontrado ninguna transaccion pagada/autorizada vinculada a esta reserva.'))
            booking.message_post(body=_('Solicitud manual de devolucion Redsys iniciada desde Odoo por %s.') % self.env.user.display_name)
        self.with_context(padel_manual_refund_button=True)._try_refund_payment()
        if len(self) == 1 and self.refund_transaction_id:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Transaccion de devolucion'),
                'res_model': 'payment.transaction',
                'res_id': self.refund_transaction_id.id,
                'view_mode': 'form',
                'target': 'current',
            }
        return True

    def action_open_refund_transaction(self):
        self.ensure_one()
        if not self.refund_transaction_id:
            raise UserError(_('No hay ninguna transaccion de devolucion vinculada a esta reserva.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Transaccion de devolucion'),
            'res_model': 'payment.transaction',
            'res_id': self.refund_transaction_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_payment_transaction(self):
        self.ensure_one()
        if not self.payment_transaction_id:
            raise UserError(_('No hay ninguna transaccion de pago vinculada a esta reserva.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Transaccion de pago'),
            'res_model': 'payment.transaction',
            'res_id': self.payment_transaction_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_done(self):
        self.write({'state': 'done'})
        return True

    def action_no_show(self):
        self.write({'state': 'no_show'})
        return True



    def _portal_apply_paid_change_if_needed(self, paid_order=None):
        """Apply a portal date/time change after the difference order is paid."""
        for booking in self.sudo():
            if paid_order and booking.portal_change_sale_order_id and booking.portal_change_sale_order_id.id != paid_order.id:
                continue
            if not booking.portal_pending_start_datetime or not booking.portal_pending_end_datetime:
                continue
            difference = booking.portal_pending_price_difference or 0.0
            vals = {
                'start_datetime': booking.portal_pending_start_datetime,
                'end_datetime': booking.portal_pending_end_datetime,
                'price': booking.portal_pending_price or booking.price,
                'portal_pending_start_datetime': False,
                'portal_pending_end_datetime': False,
                'portal_pending_price': 0.0,
                'portal_pending_price_difference': 0.0,
                'portal_change_sale_order_id': False,
            }
            try:
                booking.write(vals)
            except ValidationError as exc:
                booking.message_post(body=_(
                    'No se ha podido aplicar automaticamente el cambio pagado desde portal: %s'
                ) % (exc.args[0] if getattr(exc, 'args', None) else exc))
                continue
            if paid_order:
                order = paid_order.sudo()
                if order.state in ('draft', 'sent'):
                    order.action_confirm()
                invoices = order.invoice_ids.filtered(lambda inv: inv.move_type == 'out_invoice')
                if not invoices:
                    invoices = order._create_invoices()
                invoice = invoices[:1]
                if invoice and invoice.state == 'draft':
                    invoice.action_post()
                booking.message_post(body=_(
                    'Cambio de fecha/hora aplicado tras el pago de la diferencia de %.2f €.'
                ) % difference)
        return True

    def _create_and_post_invoice_if_needed(self):
        """Create and post the customer invoice for paid padel website orders.

        The website checkout keeps the standard ecommerce flow. Once the payment
        transaction is done/authorized, the sale order is confirmed if needed and
        the invoice is generated automatically for traceability in Accounting.
        """
        for booking in self.sudo():
            order = booking.sale_order_id.sudo()
            if not order or booking.invoice_id:
                continue
            if order.state in ('draft', 'sent'):
                order.action_confirm()
            invoices = order.invoice_ids.filtered(lambda inv: inv.move_type == 'out_invoice')
            if not invoices:
                invoices = order._create_invoices()
            invoice = invoices[:1]
            if invoice and invoice.state == 'draft':
                invoice.action_post()
            if invoice:
                booking.invoice_id = invoice.id
        return True

    def action_create_sale_order(self):
        product = self.env.ref('odoo_padel_reservation_management.product_padel_booking', raise_if_not_found=False)
        if not product:
            raise UserError(_('No se ha encontrado el producto de reserva de padel.'))
        for booking in self:
            if booking.sale_order_id:
                continue
            accounting_partner = booking._padel_get_accounting_partner_for_manual_payment()
            order = self.env['sale.order'].create({
                'partner_id': accounting_partner.id,
                'origin': booking.name,
                'order_line': [(0, 0, {
                    'product_id': product.id,
                    'name': '%s - %s - %s / %s min' % (
                        product.display_name,
                        booking.court_id.name,
                        fields.Datetime.to_string(booking.start_datetime),
                        booking.duration_minutes,
                    ),
                    'product_uom_qty': 1,
                    'price_unit': booking.price,
                    'padel_booking_id': booking.id,
                    'padel_locked_price': booking.price,
                })],
            })
            booking.sale_order_id = order.id
        return True


    def action_open_manual_payment_wizard(self):
        self.ensure_one()
        if self.price <= 0:
            raise UserError(_('La reserva no tiene importe a pagar.'))
        if self.state == 'cancelled':
            raise UserError(_('No se puede registrar el pago de una reserva cancelada.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Registrar pago manual'),
            'res_model': 'padel.manual.payment.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'active_model': 'padel.booking',
                'active_id': self.id,
                'default_booking_id': self.id,
            },
        }

    def _padel_get_accounting_partner_for_manual_payment(self):
        """Return the partner to use for accounting documents.

        Manual padel bookings may be created only with Nombre Reserva, without
        selecting an existing contact. Accounting documents in Odoo still require
        a partner, so in that case a generic internal customer is used.
        """
        self.ensure_one()
        if self.partner_id:
            return self.partner_id.sudo()

        ICP = self.env['ir.config_parameter'].sudo()
        partner_id = int(ICP.get_param('padel.manual_payment_default_partner_id', 0) or 0)
        partner = self.env['res.partner'].sudo().browse(partner_id) if partner_id else self.env['res.partner'].sudo()
        if partner and partner.exists():
            return partner

        partner = self.env['res.partner'].sudo().search([('name', '=', 'Cliente contado padel')], limit=1)
        if not partner:
            vals = {
                'name': 'Cliente contado padel',
                'company_type': 'person',
                'customer_rank': 1,
            }
            if self.customer_email and 'email' in self.env['res.partner']._fields:
                vals['email'] = self.customer_email
            if self.customer_phone and 'phone' in self.env['res.partner']._fields:
                vals['phone'] = self.customer_phone
            partner = self.env['res.partner'].sudo().create(vals)
        ICP.set_param('padel.manual_payment_default_partner_id', partner.id)
        return partner

    def _padel_get_or_create_sale_order_for_manual_payment(self):
        self.ensure_one()
        if self.sale_order_id:
            order = self.sale_order_id.sudo()
            # Keep padel line price locked in case the product has a zero list price.
            for line in order.order_line.filtered(lambda l: getattr(l, 'padel_booking_id', False) and l.padel_booking_id.id == self.id):
                vals = {'price_unit': self.price or 0.0}
                if 'padel_locked_price' in line._fields:
                    vals['padel_locked_price'] = self.price or 0.0
                line.sudo().write(vals)
            return order
        product = self.env.ref('odoo_padel_reservation_management.product_padel_booking', raise_if_not_found=False)
        if not product:
            raise UserError(_('No se ha encontrado el producto de reserva de padel.'))
        line_vals = {
            'product_id': product.id,
            'name': '%s - %s - %s / %s min' % (
                product.display_name,
                self.court_id.name,
                fields.Datetime.to_string(self.start_datetime),
                self.duration_minutes,
            ),
            'product_uom_qty': 1,
            'price_unit': self.price or 0.0,
            'padel_booking_id': self.id,
        }
        # Campo añadido en el modulo para bloquear el precio en ecommerce/checkout.
        if 'padel_locked_price' in self.env['sale.order.line']._fields:
            line_vals['padel_locked_price'] = self.price or 0.0
        accounting_partner = self._padel_get_accounting_partner_for_manual_payment()
        order = self.env['sale.order'].sudo().create({
            'partner_id': accounting_partner.id,
            'origin': self.name,
            'order_line': [(0, 0, line_vals)],
        })
        if not self.partner_id:
            self.message_post(body=_(
                'Pago manual sin cliente seleccionado: se usara el contacto contable generico %s para pedido, factura y pago. Nombre Reserva: %s.'
            ) % (accounting_partner.display_name, self.customer_name or self.name or ''))
        self.write({'sale_order_id': order.id})
        self.message_post(body=_('Pedido de venta creado para pago manual: %s por %.2f €.') % (order.name or order.display_name, self.price or 0.0))
        return order

    def _padel_create_or_get_invoice_for_manual_payment(self, order):
        self.ensure_one()
        if self.invoice_id and self.invoice_id.exists():
            invoice = self.invoice_id.sudo()
            if invoice.state == 'draft':
                invoice.action_post()
            return invoice
        order = order.sudo()
        if order.state in ('draft', 'sent'):
            order.action_confirm()
        invoices = order.invoice_ids.filtered(lambda inv: inv.move_type == 'out_invoice')
        if not invoices:
            invoices = order._create_invoices()
        invoice = invoices[:1]
        if not invoice:
            raise UserError(_('No se ha podido crear la factura de la reserva.'))
        if invoice.state == 'draft':
            invoice.action_post()
        self.write({'invoice_id': invoice.id})
        self.message_post(body=_('Factura creada/publicada para pago manual: %s.') % (invoice.name or invoice.display_name))
        return invoice.sudo()

    def _padel_register_incoming_payment(self, invoice, amount, journal, payment_method_line=False, payment_date=False, communication=False):
        self.ensure_one()
        if not invoice or not invoice.exists():
            raise UserError(_('No hay factura para registrar el pago.'))
        if invoice.state != 'posted':
            invoice.action_post()
        ctx = {
            'active_model': 'account.move',
            'active_ids': invoice.ids,
            'active_id': invoice.id,
        }
        vals = {
            'amount': amount,
            'payment_date': payment_date or fields.Date.context_today(self),
            'journal_id': journal.id,
            'communication': communication or _('Reserva padel %s') % (self.name or ''),
        }
        # En Odoo 19 el wizard usa payment_method_line_id cuando esta disponible.
        Wizard = self.env['account.payment.register'].sudo().with_context(**ctx)
        if payment_method_line and 'payment_method_line_id' in Wizard._fields:
            vals['payment_method_line_id'] = payment_method_line.id
        wizard = Wizard.create(vals)
        payments = wizard._create_payments()
        payment = payments[:1] if payments else self.env['account.payment'].sudo()
        if not payment:
            raise UserError(_('No se ha podido registrar el pago.'))
        self.write({'manual_payment_id': payment.id})
        self.message_post(body=_('Pago entrante manual registrado: %s por %.2f € en el diario %s.') % (
            payment.name or payment.display_name,
            amount,
            journal.display_name,
        ))
        return payment

    def _padel_get_pos_line_amounts(self, product, partner, amount, pos_session):
        """Return POS line values so the POS total equals the amount charged.

        POS orders are used for manual payments because their payments are included
        in the cash/card counts of the POS session. The booking price is treated as
        the final amount to collect from the customer.
        """
        self.ensure_one()
        currency = pos_session.currency_id if 'currency_id' in pos_session._fields and pos_session.currency_id else self.env.company.currency_id
        company = pos_session.company_id if 'company_id' in pos_session._fields and pos_session.company_id else self.env.company
        taxes = product.taxes_id.filtered(lambda t: not t.company_id or t.company_id == company) if product and 'taxes_id' in product._fields else self.env['account.tax']
        unit_price = amount
        total_excluded = amount
        total_included = amount
        if taxes:
            computed = taxes.compute_all(unit_price, currency, 1.0, product=product, partner=partner)
            computed_included = computed.get('total_included') or 0.0
            # If product taxes are not price-included, adapt the unit price so the
            # POS order total remains exactly the amount collected.
            if computed_included and abs(computed_included - amount) > 0.01:
                unit_price = unit_price * amount / computed_included
                computed = taxes.compute_all(unit_price, currency, 1.0, product=product, partner=partner)
            total_excluded = computed.get('total_excluded', unit_price)
            total_included = computed.get('total_included', amount)
        return unit_price, total_excluded, total_included

    def _padel_create_pos_order_for_manual_payment(self, amount, pos_session, pos_payment_method, payment_date=False, communication=False):
        self.ensure_one()
        if not pos_session or not pos_session.exists():
            raise UserError(_('Debe seleccionar una sesion de Punto de Venta abierta.'))
        if pos_session.state not in ('opened', 'opening_control', 'closing_control'):
            raise UserError(_('La sesion de Punto de Venta seleccionada no esta abierta.'))
        if not pos_payment_method or not pos_payment_method.exists():
            raise UserError(_('Debe seleccionar un metodo de pago del Punto de Venta.'))
        if pos_session.config_id and 'payment_method_ids' in pos_session.config_id._fields and pos_payment_method not in pos_session.config_id.payment_method_ids:
            raise UserError(_('El metodo de pago seleccionado no pertenece al Punto de Venta de la sesion.'))

        product = self.env.ref('odoo_padel_reservation_management.product_padel_booking', raise_if_not_found=False)
        if not product:
            raise UserError(_('No se ha encontrado el producto de reserva de padel.'))
        partner = self._padel_get_accounting_partner_for_manual_payment()
        unit_price, total_excluded, total_included = self._padel_get_pos_line_amounts(product, partner, amount, pos_session)
        order_name = '%s - Pago manual padel' % (self.name or _('Reserva padel'))
        line_name = '%s - %s - %s / %s min' % (
            product.display_name,
            self.court_id.name or '',
            fields.Datetime.to_string(self.start_datetime),
            self.duration_minutes,
        )
        line_vals = {
            'product_id': product.id,
            'qty': 1.0,
            'price_unit': unit_price,
            'discount': 0.0,
        }
        line_fields = self.env['pos.order.line']._fields
        if 'price_subtotal' in line_fields:
            line_vals['price_subtotal'] = total_excluded
        if 'price_subtotal_incl' in line_fields:
            line_vals['price_subtotal_incl'] = total_included
        if 'full_product_name' in line_fields:
            line_vals['full_product_name'] = line_name
        if 'name' in line_fields:
            line_vals['name'] = line_name

        payment_vals = {
            'amount': amount,
            'payment_method_id': pos_payment_method.id,
        }
        if 'payment_date' in self.env['pos.payment']._fields:
            payment_vals['payment_date'] = fields.Datetime.now()
        if 'session_id' in self.env['pos.payment']._fields:
            payment_vals['session_id'] = pos_session.id

        pos_order_vals = {
            'session_id': pos_session.id,
            'partner_id': partner.id,
            'lines': [(0, 0, line_vals)],
            'payment_ids': [(0, 0, payment_vals)],
        }
        order_fields = self.env['pos.order']._fields
        if 'amount_tax' in order_fields:
            pos_order_vals['amount_tax'] = total_included - total_excluded
        if 'amount_total' in order_fields:
            pos_order_vals['amount_total'] = total_included
        if 'amount_paid' in order_fields:
            pos_order_vals['amount_paid'] = amount
        if 'amount_return' in order_fields:
            pos_order_vals['amount_return'] = 0.0
        if 'to_invoice' in order_fields:
            pos_order_vals['to_invoice'] = True
        if 'pos_reference' in self.env['pos.order']._fields:
            pos_order_vals['pos_reference'] = order_name
        if 'note' in self.env['pos.order']._fields:
            pos_order_vals['note'] = communication or order_name
        if 'user_id' in self.env['pos.order']._fields:
            pos_order_vals['user_id'] = self.env.user.id
        if 'company_id' in self.env['pos.order']._fields and pos_session.company_id:
            pos_order_vals['company_id'] = pos_session.company_id.id
        if 'config_id' in self.env['pos.order']._fields and pos_session.config_id:
            pos_order_vals['config_id'] = pos_session.config_id.id
        if 'pricelist_id' in self.env['pos.order']._fields:
            pricelist = False
            if pos_session.config_id and 'pricelist_id' in pos_session.config_id._fields:
                pricelist = pos_session.config_id.pricelist_id
            if not pricelist and partner and 'property_product_pricelist' in partner._fields:
                pricelist = partner.property_product_pricelist
            if pricelist:
                pos_order_vals['pricelist_id'] = pricelist.id
        if 'currency_id' in self.env['pos.order']._fields:
            currency = pos_session.currency_id if 'currency_id' in pos_session._fields and pos_session.currency_id else self.env.company.currency_id
            pos_order_vals['currency_id'] = currency.id

        pos_order = self.env['pos.order'].sudo().create(pos_order_vals)
        # Confirm/paid flow. The POS methods differ slightly between Odoo versions,
        # so call the standard actions only when available.
        try:
            if hasattr(pos_order, 'action_pos_order_paid'):
                pos_order.action_pos_order_paid()
            elif 'state' in pos_order._fields:
                pos_order.write({'state': 'paid'})
        except Exception as exc:
            raise UserError(_('El pedido TPV se ha creado, pero no se ha podido marcar como pagado: %s') % exc)

        invoice = self.env['account.move'].sudo()
        for field_name in ('account_move', 'account_move_id', 'invoice_id'):
            if field_name in pos_order._fields and pos_order[field_name]:
                invoice = pos_order[field_name].sudo()
                break
        if not invoice and 'account_move' in pos_order._fields:
            invoice = pos_order.account_move.sudo()
        if invoice and invoice.exists() and invoice.state == 'draft':
            invoice.action_post()

        payment = pos_order.payment_ids[:1].sudo() if 'payment_ids' in pos_order._fields and pos_order.payment_ids else self.env['pos.payment'].sudo()
        vals = {
            'manual_pos_order_id': pos_order.id,
            'manual_pos_payment_id': payment.id if payment else False,
        }
        if invoice and invoice.exists():
            vals['invoice_id'] = invoice.id
        self.write(vals)
        if not self.partner_id:
            self.message_post(body=_(
                'Pago manual TPV sin cliente seleccionado: se ha usado el contacto contable generico %s. Nombre Reserva: %s.'
            ) % (partner.display_name, self.customer_name or self.name or ''))
        self.message_post(body=_(
            'Pago manual registrado por Punto de Venta: pedido TPV %s, metodo %s, importe %.2f €. Este cobro entrara en el recuento de caja de la sesion %s.'
        ) % (
            pos_order.name or pos_order.display_name,
            pos_payment_method.display_name,
            amount,
            pos_session.name or pos_session.display_name,
        ))
        if invoice and invoice.exists():
            self.message_post(body=_('Factura TPV creada/publicada para pago manual: %s.') % (invoice.name or invoice.display_name))
        return pos_order, invoice, payment

    def _padel_prepare_manual_pos_payment(self, amount, pos_session, pos_payment_method, payment_date=False, communication=False):
        for booking in self:
            if booking.price <= 0:
                raise UserError(_('La reserva no tiene importe a pagar.'))
            if amount <= 0:
                raise UserError(_('El importe del pago debe ser superior a 0.'))
            pos_order, invoice, payment = booking._padel_create_pos_order_for_manual_payment(
                amount=amount,
                pos_session=pos_session,
                pos_payment_method=pos_payment_method,
                payment_date=payment_date,
                communication=communication,
            )
            booking.write({
                'state': 'confirmed',
                'payment_deadline': False,
            })
            booking.message_post(body=_('Reserva confirmada tras registrar pago manual por TPV. Pedido TPV: %s. Factura: %s.') % (
                pos_order.name or pos_order.display_name,
                invoice.name if invoice and invoice.exists() else _('sin factura localizada'),
            ))
        return True

    def _padel_prepare_manual_invoice_and_payment(self, amount, journal, payment_method_line=False, payment_date=False, communication=False):
        for booking in self:
            if booking.price <= 0:
                raise UserError(_('La reserva no tiene importe a pagar.'))
            if amount <= 0:
                raise UserError(_('El importe del pago debe ser superior a 0.'))
            order = booking._padel_get_or_create_sale_order_for_manual_payment()
            invoice = booking._padel_create_or_get_invoice_for_manual_payment(order)
            payment = booking._padel_register_incoming_payment(
                invoice,
                amount,
                journal,
                payment_method_line=payment_method_line,
                payment_date=payment_date,
                communication=communication,
            )
            booking.write({
                'state': 'confirmed',
                'payment_deadline': False,
            })
            booking.message_post(body=_('Reserva confirmada tras registrar pago manual. Factura: %s. Pago: %s.') % (
                invoice.name or invoice.display_name,
                payment.name or payment.display_name,
            ))
        return True

    @api.model
    def cron_release_expired_pending_bookings(self):
        """Cancel only expired website bookings pending payment.

        Manual/internal bookings must never be cancelled by the system. They can
        remain pending payment until staff decides what to do. This cron only
        releases slots created from the public website checkout that were left
        unpaid past their payment deadline.
        """
        now = fields.Datetime.now()
        expired_website_bookings = self.sudo().search([
            ('origin', '=', 'website'),
            ('state', '=', 'pending_payment'),
            ('payment_deadline', '!=', False),
            ('payment_deadline', '<', now),
        ])
        for booking in expired_website_bookings:
            booking.with_context(padel_cron_expired_website=True).write({
                'state': 'cancelled',
                'payment_deadline': False,
            })
            booking.message_post(body=_(
                'Reserva web pendiente de pago caducada y anulada automaticamente. '
                'La anulacion automatica solo aplica a reservas originadas desde la web.'
            ))
            try:
                booking._cleanup_unpaid_website_cart()
            except Exception:
                _logger.exception('No se ha podido limpiar el carrito web caducado de la reserva de padel %s', booking.name)
        return True
