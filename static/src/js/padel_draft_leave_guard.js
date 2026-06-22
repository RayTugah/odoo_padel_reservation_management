/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";

function isPadelBookingDraft(controller) {
    try {
        if (controller.props?.resModel !== "padel.booking") {
            return false;
        }
        const root = controller.model?.root;
        const data = root?.data || {};
        return data.state === "draft";
    } catch (error) {
        return false;
    }
}

function notifyDraftBlocked(controller) {
    const message = _t(
        "No puede salir de una reserva de pádel mientras esté en estado Borrador. " +
        "Antes de volver al menú o salir de la ficha, cambie la reserva a Pendiente de pago, Confirmada, Finalizada o Cancelada."
    );
    if (controller.notification) {
        controller.notification.add(message, { type: "danger" });
    } else if (controller.env?.services?.notification) {
        controller.env.services.notification.add(message, { type: "danger" });
    } else {
        window.alert(message);
    }
}

patch(FormController.prototype, {
    async beforeLeave() {
        if (isPadelBookingDraft(this)) {
            notifyDraftBlocked(this);
            return false;
        }
        if (super.beforeLeave) {
            return await super.beforeLeave(...arguments);
        }
        return true;
    },

    async beforeUnload() {
        if (isPadelBookingDraft(this)) {
            notifyDraftBlocked(this);
            return false;
        }
        if (super.beforeUnload) {
            return await super.beforeUnload(...arguments);
        }
        return true;
    },
});
