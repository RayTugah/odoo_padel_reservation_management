(function () {
    'use strict';

    function escapeHtml(value) {
        return String(value || '').replace(/[&<>"']/g, function (char) {
            return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'}[char]);
        });
    }

    async function jsonRpc(url, params) {
        const response = await fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({jsonrpc: '2.0', method: 'call', params: params || {}, id: Date.now()}),
        });
        const data = await response.json();
        if (data.error) {
            throw new Error(data.error.data && data.error.data.message ? data.error.data.message : 'Error');
        }
        return data.result;
    }

    function getSelectedDurationLabel(duration) {
        const minutes = parseInt(duration, 10) || 0;
        if (minutes === 60) {
            return '1 hora';
        }
        if (minutes === 90) {
            return '1 hora y media';
        }
        if (minutes % 60 === 0) {
            return (minutes / 60) + ' horas';
        }
        return minutes + ' minutos';
    }

    function openBookingPanel(data) {
        const panel = document.getElementById('padel_selected_slot_panel');
        if (!panel) {
            return;
        }
        const courtName = document.getElementById('padel_selected_court_name');
        const date = document.getElementById('padel_selected_date_text');
        const time = document.getElementById('padel_selected_time_text');
        const duration = document.getElementById('padel_selected_duration_text');
        const price = document.getElementById('padel_selected_price_text');
        const courtIdInput = document.getElementById('padel_form_court_id');
        const bookingDateInput = document.getElementById('padel_form_booking_date');
        const bookingTimeInput = document.getElementById('padel_form_booking_time');
        const durationInput = document.getElementById('padel_form_duration');

        if (courtName) { courtName.textContent = data.courtName; }
        if (date) { date.textContent = data.date; }
        if (time) { time.textContent = data.time; }
        if (duration) { duration.textContent = getSelectedDurationLabel(data.duration); }
        if (price) { price.textContent = (data.priceFormatted || data.price) + ' €'; }
        if (courtIdInput) { courtIdInput.value = data.courtId; }
        if (bookingDateInput) { bookingDateInput.value = data.date; }
        if (bookingTimeInput) { bookingTimeInput.value = data.time; }
        if (durationInput) { durationInput.value = data.duration; }

        panel.classList.remove('d-none');
        panel.scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    function bindSlotButtons() {
        document.querySelectorAll('.o_padel_slot_button').forEach(function (button) {
            button.addEventListener('click', function () {
                document.querySelectorAll('.o_padel_slot_button.o_selected').forEach(function (selected) {
                    selected.classList.remove('o_selected');
                });
                button.classList.add('o_selected');
                openBookingPanel({
                    courtId: button.dataset.courtId,
                    courtName: button.dataset.courtName,
                    date: button.dataset.bookingDate,
                    time: button.dataset.bookingTime,
                    duration: button.dataset.duration,
                    price: button.dataset.price,
                    priceFormatted: button.dataset.priceFormatted,
                });
            });
        });
    }

    function renderAvailability(container, payload, selectedDate, duration) {
        if (!payload || payload.error) {
            container.innerHTML = '<div class="alert alert-danger">' + escapeHtml(payload && payload.error ? payload.error : 'No se ha podido consultar la disponibilidad.') + '</div>';
            return;
        }
        if (!payload.courts.length) {
            container.innerHTML = '<div class="alert alert-warning">No hay pistas disponibles para reserva web.</div>';
            return;
        }
        if (!payload.slots.length) {
            container.innerHTML = '<div class="alert alert-warning">No hay tramos disponibles para la fecha y duracion seleccionadas.</div>';
            return;
        }

        let html = '';
        html += '<div class="o_padel_planning_legend mb-3">';
        html += '<span class="o_padel_legend_item"><span class="o_padel_legend_box o_free"></span> Libre</span>';
        html += '<span class="o_padel_legend_item"><span class="o_padel_legend_box o_busy"></span> Ocupado</span>';
        html += '</div>';
        html += '<div class="o_padel_planning_wrapper">';
        html += '<table class="table table-bordered o_padel_planning_table">';
        html += '<thead><tr><th class="o_padel_time_col">Hora</th>';
        payload.courts.forEach(function (court) {
            html += '<th class="o_padel_court_col">' + escapeHtml(court.name) + '</th>';
        });
        html += '</tr></thead><tbody>';
        payload.slots.forEach(function (slot) {
            html += '<tr>';
            html += '<th class="o_padel_time_cell">' + escapeHtml(slot.time) + '</th>';
            slot.courts.forEach(function (court) {
                if (court.available) {
                    html += '<td class="o_padel_slot_cell o_available">';
                    html += '<button type="button" class="o_padel_slot_button"';
                    html += ' data-court-id="' + escapeHtml(court.court_id) + '"';
                    html += ' data-court-name="' + escapeHtml(court.court_name) + '"';
                    html += ' data-booking-date="' + escapeHtml(selectedDate) + '"';
                    html += ' data-booking-time="' + escapeHtml(slot.time) + '"';
                    html += ' data-duration="' + escapeHtml(duration) + '"';
                    html += ' data-price="' + escapeHtml(court.price) + '" data-price-formatted="' + escapeHtml(court.price_formatted || court.price) + '">';
                    html += '<span class="o_slot_state">Libre</span>';
                    html += '<span class="o_slot_price">' + escapeHtml(court.price_formatted || court.price) + ' €</span>';
                    html += '</button>';
                    html += '</td>';
                } else {
                    html += '<td class="o_padel_slot_cell o_busy">';
                    html += '<span class="o_slot_state">' + escapeHtml(court.status_label || 'Ocupado') + '</span>';
                    html += '</td>';
                }
            });
            html += '</tr>';
        });
        html += '</tbody></table></div>';
        container.innerHTML = html;
        bindSlotButtons();
    }

    async function loadAvailability() {
        const dateInput = document.getElementById('padel_booking_date');
        const durationInput = document.getElementById('padel_booking_duration');
        const container = document.getElementById('padel_availability_result');
        const selectedPanel = document.getElementById('padel_selected_slot_panel');
        if (!dateInput || !durationInput || !container) {
            return;
        }
        if (selectedPanel) {
            selectedPanel.classList.add('d-none');
        }
        container.innerHTML = '<div class="alert alert-info">Consultando disponibilidad...</div>';
        try {
            const payload = await jsonRpc('/padel/availability/json', {
                booking_date: dateInput.value,
                duration: parseInt(durationInput.value, 10),
            });
            renderAvailability(container, payload, dateInput.value, durationInput.value);
        } catch (error) {
            container.innerHTML = '<div class="alert alert-danger">No se ha podido consultar la disponibilidad.</div>';
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        const button = document.getElementById('padel_check_availability');
        const dateInput = document.getElementById('padel_booking_date');
        const durationInput = document.getElementById('padel_booking_duration');
        let refreshTimer = null;

        function scheduleAvailabilityRefresh() {
            if (refreshTimer) {
                clearTimeout(refreshTimer);
            }
            refreshTimer = setTimeout(loadAvailability, 120);
        }

        if (button) {
            button.addEventListener('click', loadAvailability);
        }
        if (dateInput) {
            dateInput.addEventListener('change', scheduleAvailabilityRefresh);
            dateInput.addEventListener('input', scheduleAvailabilityRefresh);
        }
        if (durationInput) {
            durationInput.addEventListener('change', scheduleAvailabilityRefresh);
            durationInput.addEventListener('input', scheduleAvailabilityRefresh);
        }
        if (document.getElementById('padel_availability_result')) {
            loadAvailability();
        }
    });
}());
