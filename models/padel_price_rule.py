# -*- coding: utf-8 -*-
from odoo import api, fields, models


class PadelPriceRule(models.Model):
    _name = 'padel.price.rule'
    _description = 'Regla de precio de padel'
    _order = 'sequence, name'

    name = fields.Char(string='Nombre', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    court_scope = fields.Selection([
        ('all', 'Todas las pistas'),
        ('specific', 'Una pista concreta'),
    ], string='Aplicar a', default='all', required=True)
    court_id = fields.Many2one('padel.court', string='Pista concreta')

    weekday_scope = fields.Selection([
        ('all', 'Todos los dias'),
        ('specific', 'Un dia concreto'),
    ], string='Dias', default='all', required=True)
    weekday = fields.Selection([
        ('0', 'Lunes'),
        ('1', 'Martes'),
        ('2', 'Miercoles'),
        ('3', 'Jueves'),
        ('4', 'Viernes'),
        ('5', 'Sabado'),
        ('6', 'Domingo'),
    ], string='Dia de la semana')

    pricing_type = fields.Selection([
        ('fixed', 'Precio fijo por reserva'),
        ('duration_light_table', 'Precios 60/90/120 con y sin luz'),
        ('light_split', 'Precio con/sin luz antiguo por duracion'),
        ('hourly_prorated', 'Precio por hora proporcional antiguo'),
    ], string='Tipo de precio', default='duration_light_table', required=True)

    light_type = fields.Selection([
        ('none', 'General'),
        ('no_light', 'Sin luz'),
        ('with_light', 'Con luz'),
    ], string='Tipo de horario', default='none', required=True,
        help='Campo antiguo conservado por compatibilidad. Para nuevas tarifas usa "Precios 60/90/120 con y sin luz".')
    hour_from = fields.Float(string='Hora desde', default=0.0)
    hour_to = fields.Float(string='Hora hasta', default=24.0)
    duration_minutes = fields.Integer(
        string='Duracion exacta en minutos',
        help='Campo antiguo. En nuevas tarifas no hace falta crear una tarifa por duracion.',
    )
    price = fields.Float(string='Precio', required=True, default=0.0,
                         help='Campo antiguo para precio fijo o precio proporcional antiguo.')

    no_light_hour_from = fields.Float(string='Sin luz desde', default=9.0)
    no_light_hour_to = fields.Float(string='Sin luz hasta', default=18.0)
    with_light_hour_from = fields.Float(string='Con luz desde', default=18.0)
    with_light_hour_to = fields.Float(string='Con luz hasta', default=24.0)

    # Campos antiguos conservados para compatibilidad con tarifas ya existentes de versiones anteriores.
    no_light_price_hour = fields.Float(string='Precio sin luz antiguo')
    with_light_price_hour = fields.Float(string='Precio con luz antiguo')

    # Nueva forma de tarifa: una sola regla contiene los precios de todas las duraciones.
    no_light_price_60 = fields.Float(string='60 min sin luz')
    with_light_price_60 = fields.Float(string='60 min con luz')
    no_light_price_90 = fields.Float(string='90 min sin luz')
    with_light_price_90 = fields.Float(string='90 min con luz')
    no_light_price_120 = fields.Float(string='120 min sin luz')
    with_light_price_120 = fields.Float(string='120 min con luz')

    website_available = fields.Boolean(string='Aplicar en web', default=True)
    backend_available = fields.Boolean(string='Aplicar en backend', default=True)

    @api.onchange('pricing_type')
    def _onchange_pricing_type(self):
        if self.pricing_type == 'hourly_prorated':
            self.duration_minutes = 0
        if self.pricing_type in ('light_split', 'duration_light_table'):
            self.light_type = 'none'
            self.hour_from = 0.0
            self.hour_to = 24.0
            self.price = 0.0
        if self.pricing_type == 'duration_light_table':
            self.duration_minutes = 0

    @api.onchange('court_scope')
    def _onchange_court_scope(self):
        if self.court_scope == 'all':
            self.court_id = False

    @api.onchange('weekday_scope')
    def _onchange_weekday_scope(self):
        if self.weekday_scope == 'all':
            self.weekday = False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('court_scope') == 'all':
                vals['court_id'] = False
            if vals.get('weekday_scope') == 'all':
                vals['weekday'] = False
            if vals.get('pricing_type') == 'hourly_prorated':
                vals['duration_minutes'] = 0
            if vals.get('pricing_type') in ('light_split', 'duration_light_table'):
                vals.setdefault('light_type', 'none')
                vals.setdefault('hour_from', 0.0)
                vals.setdefault('hour_to', 24.0)
                vals.setdefault('price', 0.0)
            if vals.get('pricing_type') == 'duration_light_table':
                vals['duration_minutes'] = 0
        return super().create(vals_list)

    def write(self, vals):
        vals = dict(vals)
        if vals.get('court_scope') == 'all':
            vals['court_id'] = False
        if vals.get('weekday_scope') == 'all':
            vals['weekday'] = False
        if vals.get('pricing_type') == 'hourly_prorated':
            vals['duration_minutes'] = 0
        if vals.get('pricing_type') in ('light_split', 'duration_light_table'):
            vals.setdefault('light_type', 'none')
            vals.setdefault('hour_from', 0.0)
            vals.setdefault('hour_to', 24.0)
            vals.setdefault('price', 0.0)
        if vals.get('pricing_type') == 'duration_light_table':
            vals['duration_minutes'] = 0
        return super().write(vals)

    def _read_legacy_duration_price(self, duration, field_name):
        rule = self.search([
            ('active', '=', True),
            ('pricing_type', 'in', ['light_split', 'duration_light_table']),
            ('duration_minutes', '=', duration),
        ], order='court_scope, weekday_scope, sequence, id', limit=1)
        if rule:
            return rule[field_name] or 0.0
        return 0.0

    @api.model
    def _consolidate_tariffs_into_single_duration_light_rule(self):
        """Create one active tariff containing prices for 60, 90 and 120 minutes.

        Previous versions created one active tariff per duration. This method
        migrates that structure into one rule with six price fields. Old rules
        are archived, never deleted.
        """
        existing_unified = self.env.ref(
            'odoo_padel_reservation_management.padel_price_rule_unified_duration_light',
            raise_if_not_found=False,
        )

        active_rules = self.search([('active', '=', True)], order='sequence, id')
        source = existing_unified or active_rules[:1]

        vals = {
            'name': 'Tarifa general padel',
            'sequence': 10,
            'active': True,
            'pricing_type': 'duration_light_table',
            'court_scope': source.court_scope if source else 'all',
            'court_id': source.court_id.id if source and source.court_scope == 'specific' and source.court_id else False,
            'weekday_scope': source.weekday_scope if source else 'all',
            'weekday': source.weekday if source and source.weekday_scope == 'specific' else False,
            'no_light_hour_from': source.no_light_hour_from if source else 9.0,
            'no_light_hour_to': source.no_light_hour_to if source else 18.0,
            'with_light_hour_from': source.with_light_hour_from if source else 18.0,
            'with_light_hour_to': source.with_light_hour_to if source else 24.0,
            'no_light_price_60': 5.0,
            'with_light_price_60': 9.0,
            'no_light_price_90': 7.5,
            'with_light_price_90': 13.5,
            'no_light_price_120': 10.0,
            'with_light_price_120': 18.0,
            'website_available': True,
            'backend_available': True,
        }

        # Preserve prices from the previous three-tariff setup when possible.
        for duration, no_field, with_field in [
            (60, 'no_light_price_60', 'with_light_price_60'),
            (90, 'no_light_price_90', 'with_light_price_90'),
            (120, 'no_light_price_120', 'with_light_price_120'),
        ]:
            legacy = self.search([
                ('active', '=', True),
                ('pricing_type', '=', 'light_split'),
                ('duration_minutes', '=', duration),
            ], order='sequence, id', limit=1)
            if legacy:
                vals[no_field] = legacy.no_light_price_hour or vals[no_field]
                vals[with_field] = legacy.with_light_price_hour or vals[with_field]

        if existing_unified:
            existing_unified.write(vals)
            unified = existing_unified
        else:
            unified = self.create(vals)
            self.env['ir.model.data'].sudo().create({
                'module': 'odoo_padel_reservation_management',
                'name': 'padel_price_rule_unified_duration_light',
                'model': 'padel.price.rule',
                'res_id': unified.id,
                'noupdate': False,
            })

        old_rules = self.search([('active', '=', True), ('id', '!=', unified.id)])
        if old_rules:
            old_rules.write({'active': False})
        return True
