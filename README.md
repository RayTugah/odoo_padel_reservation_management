# Odoo Padel Reservation Management

Custom module for **Odoo 19 / Odoo.sh** designed to manage padel court bookings from the backend, customer portal, and public website.

The module allows managing courts, pricing rules, availability, manual bookings, online bookings, payments, invoicing, customer portal access, cancellations, refunds, and internal court planning.

## General Information

- **Technical name:** `odoo_padel_reservation_management`
- **Version:** `19.0.1.0.0`
- **Author:** RayTugah
- **License:** LGPL-3
- **Compatibility:** Odoo 19 / Odoo.sh
- **Purpose:** Padel court booking management

## Main Features

### Court Management

The module allows creating and managing padel courts from Odoo.

Each court can be configured with:

- Court name.
- Active/inactive status.
- Website booking availability.
- Display order.
- Internal planning and availability information.

## Padel Bookings

The module includes a dedicated padel booking model.

Each booking includes:

- Booking number.
- Booking name.
- Linked customer, if available.
- Phone number.
- Email.
- Court.
- Start date and time.
- End date and time.
- Duration.
- Amount.
- Status.
- Origin.
- Sales order.
- Invoice.
- Incoming payment.
- Payment transaction.
- Refund transaction.
- Credit note.
- Outgoing refund payment.
- Chatter messages and traceability.

## Booking Statuses

Bookings can have the following statuses:

- Draft.
- Pending payment.
- Confirmed.
- Finished.
- Cancelled.
- No-show.

The **Draft** status is intended for internal editing. The system allows returning a booking to draft in order to edit it, but prevents it from being left permanently in that state without changing it afterwards to an operational status.

## Internal Planning

The module includes an internal planning view for staff.

Features:

- Daily planning.
- Separate columns by court.
- Free and occupied slots.
- Duration selector.
- Default duration of 90 minutes.
- Automatic refresh when changing date or duration.
- Booking creation from free slots.
- Availability validation.
- Colors based on booking status.
- Direct access to existing bookings.

Planning colors:

- Free: grey.
- Draft: status-specific color.
- Pending payment: status-specific color.
- Confirmed: status-specific color.
- Finished: dark blue.
- Cancelled / unavailable: differentiated color.

## Website Bookings

The module adds a public booking page:

/padel/reservar

From this page, the customer can:

* Select a date.
* Select a duration.
* View availability by court.
* Select a free slot.
* Enter their details.
* Proceed to the website cart.
* Complete payment through the configured Odoo payment gateway.

The default duration on the website is **90 minutes**.

The page also includes links to:

* My padel bookings.
* Sales and cancellation terms.

## Availability Control

The system checks availability at several points:

* When loading the planning.
* When selecting a slot.
* When creating the booking.
* When changing date, time, or court.
* When generating recurring bookings.

This prevents overlaps between confirmed, pending payment, draft, finished bookings, and court blocks.

## Online Bookings Pending Payment

Bookings created from the website are initially set as **Pending payment**.

The expiration cron only applies to bookings:

* Created from the website.
* In pending payment status.
* With an expired payment deadline.

Manual bookings are not automatically cancelled by the cron.

## Pricing

The module allows configuring padel pricing rules with:

* Application to all courts or to a specific court.
* Application to all days or to a specific day.
* Light and no-light time ranges.
* Price for 60 minutes.
* Price for 90 minutes.
* Price for 120 minutes.

The price can be calculated proportionally when a booking crosses from no-light time to light time.

Example:

* 60 minutes: €5 without light / €9 with light.
* 90 minutes: €7.50 without light / €13.50 with light.
* 120 minutes: €10 without light / €18 with light.

If the date, time, court, or duration of a booking is changed, the system recalculates the amount according to the active pricing rules.

## Online Payment

The online payment flow uses Odoo’s standard eCommerce flow.

Process:

1. The customer selects a slot.
2. A pending payment booking is created.
3. A website cart/sales order is created.
4. The booking line is added with the calculated price.
5. The customer completes checkout.
6. Payment is processed through the configured payment gateway.
7. After successful payment, the booking is confirmed.
8. An invoice is generated if applicable.
9. The confirmation email is logged in the booking chatter.

## Manual POS Payment

For manually created bookings, the module allows registering the payment directly from the booking.

Button:

```text
Register POS Payment
```

The wizard allows the user to:

* Select the Point of Sale.
* Select an open POS session.
* Select the payment method.
* Register the amount.
* Create a POS order.
* Generate an invoice.
* Confirm the booking.
* Register the payment in the selected POS cash control.

The default Point of Sale is ID 2, but the user may select between the allowed POS records ID 1 and ID 2.

## Invoicing

The module can generate and link:

* Sales order.
* Customer invoice.
* Incoming payment.
* Credit note.
* Outgoing refund payment.

For manual payments, the system generates the invoice and records the payment in the selected POS.

## Redsys Refunds

The module includes an internal button to prepare a Redsys refund from the booking form.

Button:

```text
Prepare Redsys Refund
```

Features:

* Double confirmation before executing the refund.
* Important warning indicating that the action may refund money to the customer.
* Attempted refund using the standard Odoo flow.
* If the connector does not process it correctly, direct refund attempt through Redsys REST.
* Result logged in the chatter.
* Refund transaction linked to the booking.
* Credit note creation if the refund is successful.
* Outgoing refund payment registration.

Refunds are not executed automatically from the customer portal.

## Customer Portal

The module adds a portal section:

```text
/my/padel
```

Features:

* Custom padel icon.
* Customer booking list.
* Ordered by booking number descending.
* Booking detail view.
* Possibility to cancel bookings from the portal.
* Informational message about administrative refund review.
* Link to sales and cancellation terms.

When a customer cancels a booking from the portal:

* The booking is set to cancelled.
* The action is logged in the chatter.
* An internal email is sent to the campsite.
* The internal email includes a direct link to the booking in Odoo.

## Automatic Emails

### Customer Confirmation Email

When an online booking is confirmed, the system automatically sends a confirmation email to the customer.

The email includes:

* Booking number.
* Booking name.
* Date.
* Court.
* Time slot.
* Amount paid.
* Key pickup instructions.
* Lighting instructions.
* Key return instructions.
* Cancellation and change information.
* Link to the booking portal.
* Link to sales and cancellation terms.

The email sending is always logged in the booking chatter, including:

* Recipient.
* Subject.
* Generated email ID.
* Error message, if sending fails.
* Warning if no customer email is available.

### Internal Email for Portal Cancellations

When a customer cancels a booking from the portal, an internal email is sent to the campsite with the subject:

```text
Padel booking cancelled from the portal
```

It includes:

* Booking number.
* Customer.
* Email.
* Phone.
* Court.
* Date and time.
* Amount.
* Linked sales order.
* Direct link to the booking in Odoo.

## Recurring Bookings

The module includes a recurring booking tool.

It allows selecting:

* Customer.
* Booking name.
* Court.
* Day of the week.
* Start time.
* End time.
* Start date.
* End date.
* Status of the generated bookings.

Before creating the bookings, the system checks overlaps and court blocks. If conflicts are detected, bookings are not created and a warning is shown with the affected dates.

## Chatter and Traceability

The module logs important booking changes in the chatter:

* Creation.
* Status changes.
* Date changes.
* Time changes.
* Court changes.
* Amount changes.
* Customer changes.
* Sales order creation.
* Invoice generation.
* Payment registration.
* Email sending.
* Portal cancellations.
* Refunds.
* Credit notes.
* Outgoing payments.
* Payment or refund errors.

## Sales and Cancellation Terms

The module links to the sales and cancellation terms published in Odoo Knowledge:

```text
https://campingfuente.odoo.com/knowledge/article/722
```

This link appears in:

* Public booking page.
* Confirmation email.
* Customer portal.
* Cancellation information messages.

## Dependencies

Main module dependencies:

```python
'base',
'web',
'website',
'website_sale',
'portal',
'sale',
'account',
'payment',
'mail',
'calendar',
'point_of_sale',
'contacts',
```

## Installation on Odoo.sh

1. Copy the `odoo_padel_reservation_management` folder into the Odoo.sh repository.
2. Commit and push the changes.
3. Wait for the Odoo.sh build to complete.
4. Enable developer mode in Odoo.
5. Go to Apps.
6. Update the app list.
7. Search for `Odoo Padel Reservation Management`.
8. Install or update the module.

## Recommended Initial Configuration

After installing the module:

1. Create padel courts.
2. Mark the courts as active.
3. Enable website booking for the relevant courts.
4. Configure pricing rules.
5. Review the padel booking product.
6. Review the online payment provider.
7. Review available Points of Sale.
8. Review the email template.
9. Review the customer portal.
10. Test the full flow:

    * Website booking.
    * Online payment.
    * Confirmation email.
    * Portal access.
    * Cancellation.
    * Manual POS payment.
    * Redsys refund from backend.

## Security

The module defines access rights for internal Odoo users and portal access for customers.

Customers can only view their own bookings from the portal.

Administrative operations, refunds, manual payments, recurring bookings, and configuration are restricted to authorized internal users.

## Important Notes

* Manual bookings are not automatically cancelled due to payment expiration.
* Website bookings pending payment may be automatically cancelled by cron when the payment deadline expires.
* Automatic refunds are not allowed from the portal.
* Redsys refunds can only be requested from the backend by authorized staff.
* The refund button includes double confirmation.
* Draft status is allowed for internal editing, but should not be left as the final working status.

## License

This module is distributed under the **LGPL-3** license.

## Author

**RayTugah**
