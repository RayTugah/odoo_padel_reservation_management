# -*- coding: utf-8 -*-
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PadelRecurringBookingWizard(models.TransientModel):
    _name = 'padel.recurring.booking.wizard'
    _description = 'Asistente de reservas recurrentes de padel'

    partner_id = fields.Many2one('res.partner', string='Cliente')
    customer_name = fields.Char(string='Nombre Reserva', required=True)
    customer_phone = fields.Char(string='Telefono')
    customer_email = fields.Char(string='Email')
    court_id = fields.Many2one('padel.court', string='Pista', required=True, domain=[('active', '=', True)])
    weekday = fields.Selection([
        ('0', 'Lunes'),
        ('1', 'Martes'),
        ('2', 'Miercoles'),
        ('3', 'Jueves'),
        ('4', 'Viernes'),
        ('5', 'Sabado'),
        ('6', 'Domingo'),
    ], string='Dia de la semana', required=True)
    date_from = fields.Date(string='Fecha desde', required=True)
    date_to = fields.Date(string='Fecha hasta', required=True)
    start_hour = fields.Float(string='Hora inicio', required=True, default=9.0)
    end_hour = fields.Float(string='Hora fin', required=True, default=10.0)
    state = fields.Selection([
        ('pending_payment', 'Pendiente de pago'),
        ('confirmed', 'Confirmada'),
        ('done', 'Finalizada'),
        ('cancelled', 'Cancelada'),
    ], string='Estado de las reservas', default='confirmed', required=True)
    origin = fields.Selection([
        ('manual', 'Manual'),
        ('internal', 'Interno'),
    ], string='Origen', default='manual', required=True)
    note = fields.Text(string='Observaciones')

    def _get_partner_phone_value(self, partner):
        for field_name in ['mobile', 'phone']:
            if field_name in partner._fields:
                value = partner[field_name]
                if value:
                    return value
        return False

    @api.onchange('partner_id')
    def _onchange_partner_id_fill_customer_data(self):
        for wizard in self:
            if wizard.partner_id:
                wizard.customer_name = wizard.partner_id.name or False
                wizard.customer_phone = wizard._get_partner_phone_value(wizard.partner_id)
                wizard.customer_email = wizard.partner_id.email if 'email' in wizard.partner_id._fields else False
            else:
                wizard.customer_name = False
                wizard.customer_phone = False
                wizard.customer_email = False

    def _float_to_time(self, value):
        hours = int(value)
        minutes = int(round((value - hours) * 60))
        if minutes == 60:
            hours += 1
            minutes = 0
        if hours < 0 or hours > 23 or minutes < 0 or minutes > 59:
            raise ValidationError(_('La hora indicada no es valida.'))
        return time(hour=hours, minute=minutes)

    def _get_timezone(self):
        tz_name = self.env['ir.config_parameter'].sudo().get_param('padel.timezone', 'Europe/Madrid') or 'Europe/Madrid'
        try:
            return pytz.timezone(tz_name)
        except Exception:
            return pytz.timezone('Europe/Madrid')

    def _local_datetime_to_utc_naive(self, local_date, hour_float):
        tz = self._get_timezone()
        local_dt = datetime.combine(local_date, self._float_to_time(hour_float))
        localized = tz.localize(local_dt, is_dst=None)
        return localized.astimezone(pytz.UTC).replace(tzinfo=None)

    def _iter_matching_dates(self):
        self.ensure_one()
        date_from = fields.Date.to_date(self.date_from)
        date_to = fields.Date.to_date(self.date_to)
        if date_to < date_from:
            raise ValidationError(_('La fecha hasta debe ser igual o posterior a la fecha desde.'))
        weekday = int(self.weekday)
        current = date_from
        while current <= date_to:
            if current.weekday() == weekday:
                yield current
            current += timedelta(days=1)

    def _validate_time_range(self):
        self.ensure_one()
        if self.end_hour <= self.start_hour:
            raise ValidationError(_('La hora de fin debe ser posterior a la hora de inicio.'))
        duration = int(round((self.end_hour - self.start_hour) * 60))
        if duration <= 0:
            raise ValidationError(_('La duracion debe ser superior a 0 minutos.'))
        return duration

    def _find_conflicts(self, planned_slots):
        booking_model = self.env['padel.booking']
        block_model = self.env['padel.court.block']
        conflicts = []
        blocking_states = booking_model._blocking_states()
        for local_date, start_dt, end_dt in planned_slots:
            booking_conflict = booking_model.search([
                ('court_id', '=', self.court_id.id),
                ('state', 'in', blocking_states),
                ('start_datetime', '<', end_dt),
                ('end_datetime', '>', start_dt),
            ], limit=1)
            if booking_conflict:
                conflicts.append(_('%s: conflicto con %s') % (
                    fields.Date.to_string(local_date),
                    booking_conflict.calendar_name or booking_conflict.name,
                ))
                continue
            block_conflict = block_model.search([
                ('court_id', '=', self.court_id.id),
                ('active', '=', True),
                ('start_datetime', '<', end_dt),
                ('end_datetime', '>', start_dt),
            ], limit=1)
            if block_conflict:
                conflicts.append(_('%s: pista bloqueada') % fields.Date.to_string(local_date))
        return conflicts

    def action_create_recurring_bookings(self):
        self.ensure_one()
        self._validate_time_range()
        dates = list(self._iter_matching_dates())
        if not dates:
            raise UserError(_('No hay ningun dia dentro del rango que coincida con el dia de la semana seleccionado.'))

        planned_slots = []
        for local_date in dates:
            start_dt = self._local_datetime_to_utc_naive(local_date, self.start_hour)
            end_dt = self._local_datetime_to_utc_naive(local_date, self.end_hour)
            planned_slots.append((local_date, start_dt, end_dt))

        conflicts = self._find_conflicts(planned_slots)
        if conflicts:
            message = _('No se han creado las reservas recurrentes porque existen conflictos:') + '\n\n'
            message += '\n'.join(conflicts[:20])
            if len(conflicts) > 20:
                message += '\n' + _('... y %s conflictos mas.') % (len(conflicts) - 20)
            raise UserError(message)

        booking_model = self.env['padel.booking']
        created = self.env['padel.booking']
        for local_date, start_dt, end_dt in planned_slots:
            vals = {
                'court_id': self.court_id.id,
                'partner_id': self.partner_id.id or False,
                'customer_name': self.customer_name,
                'customer_phone': self.customer_phone,
                'customer_email': self.customer_email,
                'start_datetime': start_dt,
                'end_datetime': end_dt,
                'state': self.state,
                'origin': self.origin,
                'note': self.note,
                'backend_available_only': True,
            }
            vals['price'] = booking_model._get_price_for_values(vals)
            vals.pop('backend_available_only', None)
            created |= booking_model.create(vals)

        action = self.env.ref('odoo_padel_reservation_management.action_padel_booking').read()[0]
        action['domain'] = [('id', 'in', created.ids)]
        action['context'] = {'create': False}
        return action
