# -*- coding: utf-8 -*-
from datetime import datetime, date, time, timedelta

import pytz
import logging
from html import escape as html_escape
from urllib.parse import urlencode

from odoo import fields, http, _
from odoo.exceptions import ValidationError
from odoo.http import request

_logger = logging.getLogger(__name__)


class PadelReservationController(http.Controller):

    def _config(self):
        ICP = request.env['ir.config_parameter'].sudo()
        durations = ICP.get_param('padel.allowed_durations', '60,90,120')
        parsed_durations = []
        for item in durations.split(','):
            item = item.strip()
            if item.isdigit():
                parsed_durations.append(int(item))
        return {
            'opening_hour': float(ICP.get_param('padel.opening_hour', 9.0)),
            'closing_hour': float(ICP.get_param('padel.closing_hour', 22.0)),
            'slot_step_minutes': int(ICP.get_param('padel.slot_step_minutes', 30)),
            'durations': parsed_durations or [60, 90, 120],
            'payment_hold_minutes': int(ICP.get_param('padel.payment_hold_minutes', 10)),
            'timezone': ICP.get_param('padel.timezone', 'Europe/Madrid') or 'Europe/Madrid',
        }

    def _default_duration(self, config=None):
        config = config or self._config()
        durations = config.get('durations') or [60, 90, 120]
        if 90 in durations:
            return 90
        return durations[0] if durations else 90

    def _float_to_time(self, value):
        hour = int(value)
        minute = int(round((value - hour) * 60))
        return time(hour, minute)

    def _format_price(self, price):
        return ("%.2f" % (price or 0.0)).replace('.', ',')

    def _module_reservas_url(self):
        # URL de salida desde el planning HTML para volver al cliente web de Odoo,
        # dentro de la app Padel y con el resto de vistas/menus disponibles.
        return '/web#action=odoo_padel_reservation_management.action_padel_booking&model=padel.booking&view_type=list&menu_id=odoo_padel_reservation_management.menu_padel_booking'

    def _booking_form_url(self, booking_id):
        return '/web#id=%s&model=padel.booking&view_type=form&action=odoo_padel_reservation_management.action_padel_booking&menu_id=odoo_padel_reservation_management.menu_padel_booking' % booking_id

    def _timezone(self):
        tz_name = self._config().get('timezone') or 'Europe/Madrid'
        try:
            return pytz.timezone(tz_name)
        except Exception:
            return pytz.timezone('Europe/Madrid')

    def _local_to_utc(self, local_dt):
        """Convert a website local datetime into the UTC naive datetime used by Odoo."""
        tz = self._timezone()
        try:
            localized = tz.localize(local_dt, is_dst=None)
        except pytz.NonExistentTimeError:
            localized = tz.localize(local_dt + timedelta(hours=1), is_dst=True)
        except pytz.AmbiguousTimeError:
            localized = tz.localize(local_dt, is_dst=False)
        return localized.astimezone(pytz.UTC).replace(tzinfo=None)

    def _is_available(self, court, start_dt, end_dt):
        Booking = request.env['padel.booking'].sudo()
        blocking_states = Booking._blocking_states()
        booking_domain = [
            ('court_id', '=', court.id),
            ('state', 'in', blocking_states),
            ('start_datetime', '<', fields.Datetime.to_string(end_dt)),
            ('end_datetime', '>', fields.Datetime.to_string(start_dt)),
        ]
        if Booking.search_count(booking_domain):
            return False
        block_domain = [
            ('court_id', '=', court.id),
            ('active', '=', True),
            ('start_datetime', '<', fields.Datetime.to_string(end_dt)),
            ('end_datetime', '>', fields.Datetime.to_string(start_dt)),
        ]
        if request.env['padel.court.block'].sudo().search_count(block_domain):
            return False
        return True

    def _is_internal_slot_available(self, court, start_dt, end_dt):
        """Check the whole duration selected in the internal planning.

        Draft reservations are included here because the internal planning displays
        them as occupied blocks and a user must not be able to create another
        draft on top of an existing one.
        """
        Booking = request.env['padel.booking'].sudo()
        planning_states = ['draft', 'pending_payment', 'confirmed', 'done']
        booking_domain = [
            ('court_id', '=', court.id),
            ('state', 'in', planning_states),
            ('start_datetime', '<', fields.Datetime.to_string(end_dt)),
            ('end_datetime', '>', fields.Datetime.to_string(start_dt)),
        ]
        if Booking.search_count(booking_domain):
            return False
        block_domain = [
            ('court_id', '=', court.id),
            ('active', '=', True),
            ('start_datetime', '<', fields.Datetime.to_string(end_dt)),
            ('end_datetime', '>', fields.Datetime.to_string(start_dt)),
        ]
        if request.env['padel.court.block'].sudo().search_count(block_domain):
            return False
        return True

    def _availability_payload(self, selected_date=None, duration=None):
        config = self._config()
        selected_date = selected_date or date.today()
        duration = int(duration or self._default_duration(config))

        if duration not in config['durations']:
            duration = self._default_duration(config)

        opening = self._float_to_time(config['opening_hour'])
        closing = self._float_to_time(config['closing_hour'])
        current = datetime.combine(selected_date, opening)
        limit = datetime.combine(selected_date, closing)
        step = timedelta(minutes=config['slot_step_minutes'])
        duration_delta = timedelta(minutes=duration)

        courts = request.env['padel.court'].sudo().search([
            ('active', '=', True),
            ('allow_website_booking', '=', True),
        ], order='sequence, name')

        slots = []
        while current + duration_delta <= limit:
            item = {
                'time': current.strftime('%H:%M'),
                'courts': [],
            }
            for court in courts:
                local_start_dt = current
                local_end_dt = current + duration_delta
                start_dt = self._local_to_utc(local_start_dt)
                end_dt = self._local_to_utc(local_end_dt)
                Booking = request.env['padel.booking'].sudo()
                blocking_states = Booking._blocking_states()
                overlap_booking = Booking.search([
                    ('court_id', '=', court.id),
                    ('state', 'in', blocking_states),
                    ('start_datetime', '<', fields.Datetime.to_string(end_dt)),
                    ('end_datetime', '>', fields.Datetime.to_string(start_dt)),
                ], order='start_datetime asc, id asc', limit=1)
                overlap_block = request.env['padel.court.block'].sudo().search([
                    ('court_id', '=', court.id),
                    ('active', '=', True),
                    ('start_datetime', '<', fields.Datetime.to_string(end_dt)),
                    ('end_datetime', '>', fields.Datetime.to_string(start_dt)),
                ], limit=1)
                available = not overlap_booking and not overlap_block
                if available:
                    status_label = _('Libre')
                elif overlap_block:
                    status_label = _('Bloqueado')
                elif self._utc_to_local_string(overlap_booking.start_datetime) == current.strftime('%H:%M'):
                    status_label = _('Ocupado')
                else:
                    status_label = _('No disponible')
                price = Booking._get_price_for_values({
                    'court_id': court.id,
                    'start_datetime': fields.Datetime.to_string(start_dt),
                    'end_datetime': fields.Datetime.to_string(end_dt),
                    'website_available_only': True,
                })
                item['courts'].append({
                    'court_id': court.id,
                    'court_name': court.name,
                    'available': available,
                    'status_label': status_label,
                    'price': price,
                    'price_formatted': self._format_price(price),
                })
            slots.append(item)
            current += step

        return {
            'date': selected_date.isoformat(),
            'duration': duration,
            'courts': [{'id': c.id, 'name': c.name} for c in courts],
            'slots': slots,
        }

    @http.route('/padel/reservar', type='http', auth='public', website=True, sitemap=True)
    def padel_booking_page(self, booking_date=None, duration=None, **kwargs):
        config = self._config()
        try:
            selected_date = date.fromisoformat(booking_date) if booking_date else date.today()
        except Exception:
            selected_date = date.today()
        try:
            duration = int(duration or self._default_duration(config))
        except Exception:
            duration = self._default_duration(config)
        if duration not in config['durations']:
            duration = self._default_duration(config)
        availability = self._availability_payload(selected_date, duration)
        values = {
            'courts': availability['courts'],
            'slots': availability['slots'],
            'durations': config['durations'],
            'selected_duration': duration,
            'today': selected_date.isoformat(),
        }
        return request.render('odoo_padel_reservation_management.padel_booking_page', values)

    @http.route('/padel/availability/json', type='json', auth='public', website=True, csrf=False)
    def padel_availability_json(self, booking_date=None, duration=None, **kwargs):
        try:
            selected_date = date.fromisoformat(booking_date) if booking_date else date.today()
            duration = int(duration or self._default_duration())
        except Exception:
            return {'error': _('Fecha o duracion no valida.')}
        return self._availability_payload(selected_date, duration)

    def _create_or_get_sale_order_for_booking(self, booking):
        if booking.sale_order_id:
            return booking.sale_order_id.sudo()
        booking.sudo().action_create_sale_order()
        return booking.sale_order_id.sudo()

    def _prepare_website_checkout_for_booking(self, booking):
        """Prepare the standard Website Sale cart for a padel booking.

        This method is intentionally defensive for Odoo.sh/Odoo 19 databases
        where the website_sale cart helpers may differ slightly. It first tries
        the normal website cart. If that helper is not available or fails, it
        creates a draft sale order linked to the current website and stores it
        in the website session so /shop/cart opens the normal shop checkout.
        """
        booking = booking.sudo()
        product = request.env.ref('odoo_padel_reservation_management.product_padel_booking', raise_if_not_found=False)
        if not product:
            raise ValidationError(_('No se ha encontrado el producto de reserva de padel.'))
        if not booking.partner_id:
            raise ValidationError(_('Debe indicarse un cliente para preparar el pago.'))
        if not request.website:
            raise ValidationError(_('No se ha podido identificar el sitio web para preparar el pago.'))

        product = product.sudo()
        product_vals = {}
        if 'sale_ok' in product._fields and not product.sale_ok:
            product_vals['sale_ok'] = True
        if 'active' in product._fields and not product.active:
            product_vals['active'] = True
        if 'website_published' in product._fields and not product.website_published:
            product_vals['website_published'] = True
        if product_vals:
            product.write(product_vals)
        if hasattr(product, 'product_tmpl_id') and product.product_tmpl_id:
            tmpl_vals = {}
            tmpl = product.product_tmpl_id.sudo()
            if 'sale_ok' in tmpl._fields and not tmpl.sale_ok:
                tmpl_vals['sale_ok'] = True
            if 'website_published' in tmpl._fields and not tmpl.website_published:
                tmpl_vals['website_published'] = True
            if tmpl_vals:
                tmpl.write(tmpl_vals)

        SaleOrder = request.env['sale.order'].sudo()
        SaleLine = request.env['sale.order.line'].sudo()

        order = False
        try:
            if hasattr(request.website, 'sale_get_order'):
                order = request.website.sale_get_order(force_create=True)
                if order:
                    order = order.sudo()
        except Exception:
            _logger.exception('No se ha podido obtener el carrito web existente para reserva de padel %s', booking.name)
            order = False

        if not order or order.state not in ('draft', 'sent'):
            try:
                request.session.pop('sale_order_id', None)
            except Exception:
                pass
            order_vals = {
                'partner_id': booking.partner_id.id,
                'partner_invoice_id': booking.partner_id.id,
                'partner_shipping_id': booking.partner_id.id,
                'origin': booking.name,
            }
            if 'website_id' in SaleOrder._fields:
                order_vals['website_id'] = request.website.id
            if 'company_id' in SaleOrder._fields:
                company = getattr(request.website, 'company_id', False) or request.env.company
                if company:
                    order_vals['company_id'] = company.id
            if 'pricelist_id' in SaleOrder._fields:
                pricelist = getattr(request.website, 'pricelist_id', False)
                if pricelist:
                    order_vals['pricelist_id'] = pricelist.id
            order = SaleOrder.create(order_vals)
        else:
            order.write({
                'partner_id': booking.partner_id.id,
                'partner_invoice_id': booking.partner_id.id,
                'partner_shipping_id': booking.partner_id.id,
                'origin': booking.name,
            })

        # Make the padel checkout exclusive. This avoids mixing a reservation
        # with products already in the cart and guarantees the paid amount is the
        # reservation amount shown in the planning.
        if order.order_line:
            order.order_line.sudo().unlink()

        line_name = self._sale_line_name_for_booking(booking, product)
        line_vals = {
            'order_id': order.id,
            'product_id': product.id,
            'name': line_name,
            'product_uom_qty': 1,
            'price_unit': booking.price or 0.0,
        }
        if 'padel_booking_id' in SaleLine._fields:
            line_vals['padel_booking_id'] = booking.id
        if 'padel_locked_price' in SaleLine._fields:
            line_vals['padel_locked_price'] = booking.price or 0.0
        if 'product_uom' in SaleLine._fields and getattr(product, 'uom_id', False):
            line_vals['product_uom'] = product.uom_id.id
        line = SaleLine.create(line_vals)
        # Force the price again after product/pricelist onchange/default logic.
        forced_vals = {'name': line_name, 'price_unit': booking.price or 0.0, 'product_uom_qty': 1}
        if 'padel_booking_id' in line._fields:
            forced_vals['padel_booking_id'] = booking.id
        if 'padel_locked_price' in line._fields:
            forced_vals['padel_locked_price'] = booking.price or 0.0
        line.write(forced_vals)
        if hasattr(order, '_padel_restore_locked_prices'):
            order._padel_restore_locked_prices()

        booking.write({'sale_order_id': order.id})

        # Store the order as the active website cart. The website shop will then
        # use the standard /shop/cart -> checkout -> payment provider flow.
        request.session['sale_order_id'] = order.id
        request.session['website_sale_cart_quantity'] = int(sum(order.order_line.mapped('product_uom_qty')) or 1)
        try:
            request.session.modified = True
        except Exception:
            pass

        # Some Odoo builds use a cart access token; create it when the method is available.
        try:
            if hasattr(order, '_portal_ensure_token'):
                order._portal_ensure_token()
        except Exception:
            _logger.exception('No se ha podido generar token de portal para pedido padel %s', order.name)

        return '/shop/cart?padel_booking_id=%s' % booking.id

    def _sale_line_name_for_booking(self, booking, product):
        start_txt = self._utc_to_local_string(booking.start_datetime, '%d/%m/%Y %H:%M')
        end_txt = self._utc_to_local_string(booking.end_datetime, '%H:%M')
        return '%s - %s - %s a %s' % (
            product.display_name,
            booking.court_id.name,
            start_txt,
            end_txt,
        )

    @http.route('/padel/booking/create', type='http', auth='public', methods=['POST'], website=True, csrf=True)
    def padel_booking_create(self, **post):
        try:
            court_id = int(post.get('court_id'))
            booking_date = date.fromisoformat(post.get('booking_date'))
            hour, minute = [int(x) for x in post.get('booking_time').split(':')]
            duration = int(post.get('duration'))
        except Exception:
            return request.render('odoo_padel_reservation_management.padel_booking_error', {
                'message': _('No se han recibido datos validos para crear la reserva.'),
            })

        config = self._config()
        if duration not in config['durations']:
            return request.render('odoo_padel_reservation_management.padel_booking_error', {
                'message': _('La duracion seleccionada no esta permitida.'),
            })

        court = request.env['padel.court'].sudo().browse(court_id)
        if not court.exists() or not court.allow_website_booking:
            return request.render('odoo_padel_reservation_management.padel_booking_error', {
                'message': _('La pista seleccionada no esta disponible para reserva web.'),
            })

        local_start_dt = datetime.combine(booking_date, time(hour, minute))
        local_end_dt = local_start_dt + timedelta(minutes=duration)
        start_dt = self._local_to_utc(local_start_dt)
        end_dt = self._local_to_utc(local_end_dt)
        if not self._is_available(court, start_dt, end_dt):
            return request.render('odoo_padel_reservation_management.padel_booking_error', {
                'message': _('Lo sentimos, este horario acaba de ser reservado o bloqueado por otro usuario. Por favor, vuelve al planning y selecciona otro hueco disponible.'),
            })

        partner = False
        email = (post.get('customer_email') or '').strip()
        name = (post.get('customer_name') or '').strip()
        phone = (post.get('customer_phone') or '').strip()
        if email:
            partner = request.env['res.partner'].sudo().search([('email', '=', email)], limit=1)
        if not partner and name:
            partner = request.env['res.partner'].sudo().create({
                'name': name,
                'email': email,
                'phone': phone,
            })

        Booking = request.env['padel.booking'].sudo()
        price = Booking._get_price_for_values({
            'court_id': court.id,
            'start_datetime': fields.Datetime.to_string(start_dt),
            'end_datetime': fields.Datetime.to_string(end_dt),
            'website_available_only': True,
        })
        try:
            booking = Booking.create({
                'court_id': court.id,
                'partner_id': partner.id if partner else False,
                'customer_name': name,
                'customer_phone': phone,
                'customer_email': email,
                'start_datetime': fields.Datetime.to_string(start_dt),
                'end_datetime': fields.Datetime.to_string(end_dt),
                'origin': 'website',
                'state': 'pending_payment',
                'payment_deadline': fields.Datetime.now() + timedelta(minutes=config['payment_hold_minutes']),
                'price': price,
            })
        except ValidationError:
            return request.render('odoo_padel_reservation_management.padel_booking_error', {
                'message': _('Lo sentimos, este horario acaba de ser reservado o bloqueado por otro usuario. Por favor, vuelve al planning y selecciona otro hueco disponible.'),
            })
        if price and price > 0:
            try:
                checkout_url = self._prepare_website_checkout_for_booking(booking)
                if checkout_url:
                    return request.redirect(checkout_url)
            except Exception as e:
                _logger.exception('Error preparando checkout web de padel para la reserva %s', booking.name)
                booking.sudo().message_post(body=_('Error preparando el pago online. La reserva NO se ha anulado automaticamente; queda pendiente de revision manual.'))
                return request.render('odoo_padel_reservation_management.padel_booking_error', {
                    'message': _('No se ha podido preparar el pago online. La reserva queda pendiente de revision manual por administracion.'),
                })
        else:
            booking.sudo().write({'state': 'confirmed', 'payment_deadline': False})

        return request.render('odoo_padel_reservation_management.padel_booking_confirmation', {
            'booking': booking,
        })

    @http.route('/padel/booking/payment/return/<int:booking_id>', type='http', auth='public', website=True, sitemap=False)
    def padel_booking_payment_return(self, booking_id, **kwargs):
        booking = request.env['padel.booking'].sudo().browse(booking_id)
        if not booking.exists():
            return request.redirect('/padel/reservar')
        return request.render('odoo_padel_reservation_management.padel_booking_confirmation', {
            'booking': booking,
        })

    def _utc_to_local_string(self, utc_dt, fmt='%H:%M'):
        if not utc_dt:
            return ''
        tz = self._timezone()
        if isinstance(utc_dt, str):
            utc_dt = fields.Datetime.from_string(utc_dt)
        if utc_dt.tzinfo:
            aware = utc_dt.astimezone(pytz.UTC)
        else:
            aware = pytz.UTC.localize(utc_dt)
        return aware.astimezone(tz).strftime(fmt)

    def _booking_state_label(self, state):
        labels = dict(request.env['padel.booking']._fields['state'].selection)
        return labels.get(state, state or '')

    def _booking_state_class(self, state):
        return {
            'draft': 'draft',
            'pending_payment': 'pending',
            'confirmed': 'confirmed',
            'done': 'done',
            'cancelled': 'cancelled',
            'no_show': 'no_show',
        }.get(state or '', 'draft')

    def _internal_planning_payload(self, selected_date=None, duration=None):
        config = self._config()
        selected_date = selected_date or date.today()
        duration = int(duration or self._default_duration(config))
        if duration not in config['durations']:
            duration = self._default_duration(config)

        opening = self._float_to_time(config['opening_hour'])
        closing = self._float_to_time(config['closing_hour'])
        current = datetime.combine(selected_date, opening)
        limit = datetime.combine(selected_date, closing)
        step = timedelta(minutes=config['slot_step_minutes'])
        duration_delta = timedelta(minutes=duration)

        courts = request.env['padel.court'].sudo().search([('active', '=', True)], order='sequence, name')
        Booking = request.env['padel.booking'].sudo()
        planning_states = ['draft', 'pending_payment', 'confirmed', 'done']
        slots = []

        while current + duration_delta <= limit:
            start_dt = self._local_to_utc(current)
            end_dt = self._local_to_utc(current + duration_delta)
            item = {'time': current.strftime('%H:%M'), 'courts': []}
            for court in courts:
                row_next_dt = self._local_to_utc(current + step)
                display_booking = Booking.search([
                    ('court_id', '=', court.id),
                    ('state', 'in', planning_states),
                    ('start_datetime', '>=', fields.Datetime.to_string(start_dt)),
                    ('start_datetime', '<', fields.Datetime.to_string(row_next_dt)),
                ], order='start_datetime asc, id asc', limit=1)
                collision_booking = Booking.search([
                    ('court_id', '=', court.id),
                    ('state', 'in', planning_states),
                    ('start_datetime', '<', fields.Datetime.to_string(end_dt)),
                    ('end_datetime', '>', fields.Datetime.to_string(start_dt)),
                ], order='start_datetime asc, id asc', limit=1)
                booking = display_booking or collision_booking
                block = request.env['padel.court.block'].sudo().search([
                    ('court_id', '=', court.id),
                    ('active', '=', True),
                    ('start_datetime', '<', fields.Datetime.to_string(end_dt)),
                    ('end_datetime', '>', fields.Datetime.to_string(start_dt)),
                ], limit=1)
                price = Booking._get_price_for_values({
                    'court_id': court.id,
                    'start_datetime': fields.Datetime.to_string(start_dt),
                    'end_datetime': fields.Datetime.to_string(end_dt),
                    'backend_available_only': True,
                })
                if booking:
                    booking_start = self._utc_to_local_string(booking.start_datetime)
                    booking_end = self._utc_to_local_string(booking.end_datetime)
                    if display_booking:
                        label = booking.partner_id.name or booking.customer_name or booking.name or _('Reserva')
                        item['courts'].append({
                            'available': False,
                            'is_block': False,
                            'label': label,
                            'state': booking.state,
                            'state_label': self._booking_state_label(booking.state),
                            'state_class': self._booking_state_class(booking.state),
                            'start': booking_start,
                            'end': booking_end,
                            'url': self._booking_form_url(booking.id),
                        })
                    else:
                        # The selected duration would collide with an existing reservation,
                        # but the reservation starts later. Do not draw the booking earlier;
                        # show the cell as unavailable for the selected duration.
                        item['courts'].append({
                            'available': False,
                            'is_block': False,
                            'label': _('No disponible'),
                            'state': 'blocked',
                            'state_label': _('No disponible'),
                            'state_class': 'block',
                            'start': '',
                            'end': '',
                            'url': False,
                        })
                elif block:
                    item['courts'].append({
                        'available': False,
                        'is_block': True,
                        'label': block.name or _('Bloqueado'),
                        'state': 'blocked',
                        'state_label': _('Bloqueado'),
                        'state_class': 'block',
                        'start': self._utc_to_local_string(block.start_datetime),
                        'end': self._utc_to_local_string(block.end_datetime),
                        'url': False,
                    })
                else:
                    item['courts'].append({
                        'available': True,
                        'is_block': False,
                        'label': _('Libre'),
                        'state': 'free',
                        'state_label': _('Libre'),
                        'state_class': 'free',
                        'price': price,
                        'price_formatted': self._format_price(price),
                        'court_id': court.id,
                        'booking_date': selected_date.isoformat(),
                        'booking_time': current.strftime('%H:%M'),
                        'duration': duration,
                        'new_url': '/padel/internal/planning/new?court_id=%s&booking_date=%s&booking_time=%s&duration=%s' % (court.id, selected_date.isoformat(), current.strftime('%H:%M'), duration),
                    })
            slots.append(item)
            current += step

        return {
            'date': selected_date.isoformat(),
            'duration': duration,
            'durations': config['durations'],
            'courts': courts,
            'slots': slots,
        }

    def _planning_html_response(self, payload):
        date_value = html_escape(payload['date'])
        duration_options = []
        for duration in payload['durations']:
            selected = ' selected="selected"' if int(duration) == int(payload['duration']) else ''
            duration_options.append('<option value="%s"%s>%s minutos</option>' % (int(duration), selected, int(duration)))

        parts = []
        parts.append('<!doctype html><html><head><meta charset="utf-8"/>')
        parts.append('<title>Planning padel</title>')
        parts.append('<style>body{font-family:Arial,Helvetica,sans-serif;margin:0;background:#f5f6f7;color:#111827;font-size:14px}.top{display:flex;align-items:center;gap:12px;padding:12px 16px;background:#fff;border-bottom:1px solid #d8dadd;position:sticky;top:0;z-index:20}h1{font-size:20px;margin:0 18px 0 0}label{font-weight:600}input,select{height:32px;border:1px solid #cbd5e1;border-radius:4px;padding:0 8px;background:#fff}button,.btn{height:32px;border:1px solid #714b67;border-radius:4px;background:#714b67;color:#fff;padding:6px 14px;text-decoration:none;display:inline-flex;align-items:center;box-sizing:border-box}.btn.secondary{background:#fff;color:#374151;border-color:#cbd5e1}.content{padding:14px 16px}.legend{display:flex;gap:16px;align-items:center;margin-bottom:12px;flex-wrap:wrap}.box{width:14px;height:14px;border-radius:3px;display:inline-block;vertical-align:middle;margin-right:5px}.free{background:#f3f4f6;border:1px solid #9ca3af;color:#374151}.draft{background:#e0f2fe;border:1px solid #0284c7;color:#075985}.pending{background:#fef3c7;border:1px solid #f59e0b;color:#92400e}.confirmed{background:#dcfce7;border:1px solid #16a34a;color:#166534}.done{background:#1e3a8a;border:1px solid #1e40af;color:#ffffff}.cancelled{background:#fee2e2;border:1px solid #ef4444;color:#991b1b}.no_show{background:#fce7f3;border:1px solid #db2777;color:#9d174d}.block{background:#f3e8ff;border:1px solid #9333ea;color:#581c87}.scroll{overflow:auto;border:1px solid #d8dadd;background:#fff;max-height:calc(100vh - 118px)}table{border-collapse:separate;border-spacing:0;width:max-content;min-width:100%}th,td{border-right:1px solid #d8dadd;border-bottom:1px solid #d8dadd;padding:0}thead th{position:sticky;top:0;z-index:5;background:#f3f4f6;height:42px;text-align:center;font-size:14px}.time_head{position:sticky;left:0;z-index:6!important;width:82px;min-width:82px;background:#f3f4f6!important}.time{position:sticky;left:0;z-index:4;width:82px;min-width:82px;height:58px;background:#f8f9fa;text-align:center;font-weight:700;font-size:13px}.court{min-width:280px;width:280px}.cell{min-width:280px;width:280px;height:58px}.slot{display:block;margin:5px;height:46px;border-radius:5px;text-align:center;line-height:1.15;padding:7px 6px;box-sizing:border-box;font-size:13px;overflow:hidden;text-decoration:none}.slot strong{display:block;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.slot small{display:block;font-size:11px;opacity:.9;margin-top:3px}.empty{padding:16px;background:#fff3cd;border:1px solid #ffec99;border-radius:4px}</style>')
        parts.append('</head><body>')
        parts.append('<form id="padel_planning_form" class="top" method="get" action="/padel/internal/planning">')
        parts.append('<h1>Planning padel</h1>')
        parts.append('<label for="planning_date">Fecha</label><input id="planning_date" type="date" name="planning_date" value="%s"/>' % date_value)
        parts.append('<label for="duration">Duracion</label><select id="duration" name="duration">%s</select>' % ''.join(duration_options))
        parts.append('<noscript><button type="submit">Actualizar</button></noscript>')
        module_url = self._module_reservas_url()
        parts.append('<a class="btn secondary" href="%s">Ver reservas</a>' % html_escape(module_url))
        parts.append('<a class="btn secondary" href="%s">Salir al modulo</a>' % html_escape(module_url))
        parts.append('</form><div class="content">')
        if payload.get('warning') == 'slot_unavailable':
            parts.append('<div class="empty" style="background:#fee2e2;border-color:#fecaca;color:#991b1b;margin-bottom:12px;">No se puede crear la reserva: el tramo completo seleccionado ya no esta libre para esa pista y duracion.</div>')
        parts.append('<div class="legend"><span><span class="box free"></span>Libre</span><span><span class="box draft"></span>Borrador</span><span><span class="box pending"></span>Pendiente de pago</span><span><span class="box confirmed"></span>Confirmada</span><span><span class="box done"></span>Finalizada</span><span><span class="box block"></span>Bloqueado</span></div>')
        courts = payload['courts']
        if not courts:
            parts.append('<div class="empty">No hay pistas activas configuradas.</div>')
        else:
            parts.append('<div class="scroll"><table><thead><tr><th class="time_head">Hora</th>')
            for court in courts:
                parts.append('<th class="court">%s</th>' % html_escape(court.name or ''))
            parts.append('</tr></thead><tbody>')
            for slot in payload['slots']:
                parts.append('<tr><th class="time">%s</th>' % html_escape(slot['time']))
                for court_slot in slot['courts']:
                    parts.append('<td class="cell">')
                    state_class = html_escape(court_slot.get('state_class') or 'free')
                    if court_slot.get('available'):
                        parts.append('<a class="slot free" href="%s"><strong>Libre</strong><small>%s €</small></a>' % (html_escape(court_slot.get('new_url') or '#'), html_escape(court_slot.get('price_formatted') or '')))
                    elif court_slot.get('url'):
                        parts.append('<a class="slot %s" href="%s"><strong>%s</strong><small>%s · %s - %s</small></a>' % (state_class, html_escape(court_slot.get('url') or ''), html_escape(court_slot.get('label') or ''), html_escape(court_slot.get('state_label') or ''), html_escape(court_slot.get('start') or ''), html_escape(court_slot.get('end') or '')))
                    else:
                        parts.append('<span class="slot %s"><strong>%s</strong><small>%s - %s</small></span>' % (state_class, html_escape(court_slot.get('label') or ''), html_escape(court_slot.get('start') or ''), html_escape(court_slot.get('end') or '')))
                    parts.append('</td>')
                parts.append('</tr>')
            parts.append('</tbody></table></div>')
        
        parts.append('<script>')
        parts.append('(function(){var f=document.getElementById("padel_planning_form");if(!f){return;}var d=document.getElementById("planning_date");var u=document.getElementById("duration");var busy=false;function go(){if(busy){return;}busy=true;if(f.requestSubmit){f.requestSubmit();}else{f.submit();}}if(d){d.addEventListener("change",go);}if(u){u.addEventListener("change",go);}})();')
        parts.append('</script>')
        parts.append('</div></body></html>')
        return request.make_response(''.join(parts), headers=[('Content-Type', 'text/html; charset=utf-8')])

    @http.route('/padel/internal/planning/new', type='http', auth='user', website=False)
    def padel_internal_planning_new(self, court_id=None, booking_date=None, booking_time=None, duration=None, **kwargs):
        try:
            court_id = int(court_id)
            selected_date = date.fromisoformat(booking_date)
            hour, minute = [int(x) for x in (booking_time or '').split(':')]
            duration = int(duration or self._default_duration())
        except Exception:
            return request.redirect('/padel/internal/planning')

        config = self._config()
        if duration not in config['durations']:
            duration = self._default_duration(config)

        court = request.env['padel.court'].sudo().browse(court_id)
        if not court.exists() or not court.active:
            return request.redirect('/padel/internal/planning')

        local_start_dt = datetime.combine(selected_date, time(hour, minute))
        local_end_dt = local_start_dt + timedelta(minutes=duration)
        start_dt = self._local_to_utc(local_start_dt)
        end_dt = self._local_to_utc(local_end_dt)

        # Do not create a draft if the full selected duration is no longer free.
        if not self._is_internal_slot_available(court, start_dt, end_dt):
            return request.redirect('/padel/internal/planning?planning_date=%s&duration=%s&warning=slot_unavailable' % (selected_date.isoformat(), duration))

        Booking = request.env['padel.booking'].sudo()
        price = Booking._get_price_for_values({
            'court_id': court.id,
            'start_datetime': fields.Datetime.to_string(start_dt),
            'end_datetime': fields.Datetime.to_string(end_dt),
            'backend_available_only': True,
        })
        booking = Booking.with_context(allow_padel_create_draft=True).create({
            'court_id': court.id,
            'start_datetime': fields.Datetime.to_string(start_dt),
            'end_datetime': fields.Datetime.to_string(end_dt),
            'origin': 'manual',
            'state': 'draft',
            'payment_deadline': False,
            'price': price,
        })
        return request.redirect(self._booking_form_url(booking.id))

    @http.route('/padel/internal/exit', type='http', auth='user', website=False)
    def padel_internal_exit(self, **kwargs):
        return request.redirect(self._module_reservas_url())

    @http.route('/padel/internal/planning', type='http', auth='user', website=False)
    def padel_internal_planning(self, planning_date=None, duration=None, warning=None, **kwargs):
        try:
            selected_date = date.fromisoformat(planning_date) if planning_date else date.today()
            duration = int(duration or self._default_duration())
        except Exception:
            selected_date = date.today()
            duration = self._default_duration()
        payload = self._internal_planning_payload(selected_date, duration)
        payload['warning'] = warning
        return self._planning_html_response(payload)
