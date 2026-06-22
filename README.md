# Odoo Padel Reservation Management

Módulo de Odoo para gestionar reservas de pistas de pádel desde el sitio web, el portal del cliente y el backend interno.

El módulo está pensado para instalaciones que necesitan publicar disponibilidad online, bloquear horarios, gestionar tarifas por duración y franja horaria, crear reservas internas y vincular las reservas con ventas, pagos, facturación y devoluciones.

## Características principales

- Publicación de una página web de reserva de pistas de pádel.
- Consulta de disponibilidad por fecha, pista, duración y hora.
- Creación de reservas online con bloqueo temporal mientras el cliente completa el pago.
- Liberación automática de reservas web pendientes de pago mediante cron.
- Gestión interna de reservas desde el backend de Odoo.
- Planning interno por pistas.
- Gestión de pistas activas/inactivas.
- Bloqueo de pistas por mantenimiento, eventos u otros motivos.
- Gestión de tarifas por duración y franjas horarias.
- Reservas recurrentes desde asistente interno.
- Área de portal para que el cliente consulte sus reservas de pádel.
- Solicitud/cancelación de reservas desde portal según el flujo configurado.
- Integración con pedidos de venta, facturas y pagos.
- Registro de pagos manuales mediante asistente interno.
- Soporte para preparación de devoluciones Redsys desde la reserva.
- Grupos de seguridad para usuario y administrador de pádel.

## Requisitos

Este módulo está preparado para Odoo 19 según el manifiesto incluido.

Dependencias declaradas:

- `base`
- `mail`
- `calendar`
- `website`
- `portal`
- `sale`
- `website_sale`
- `payment`
- `account`
- `point_of_sale`

También requiere que el entorno de Odoo tenga configurado correctamente el sitio web, los métodos de pago y, si se van a utilizar devoluciones online, el proveedor de pago compatible con Redsys.

## Instalación

1. Copiar la carpeta `odoo_padel_reservation_management` dentro del directorio de addons de Odoo.
2. Reiniciar el servidor de Odoo.
3. Actualizar la lista de aplicaciones.
4. Buscar el módulo **Odoo Padel Reservation Management** o `odoo_padel_reservation_management`.
5. Instalar el módulo.

En Odoo.sh, se recomienda añadir el módulo al repositorio Git del proyecto, hacer commit de los cambios y desplegar en una rama de pruebas antes de pasarlo a producción.

## Configuración inicial

Después de instalar el módulo:

1. Revisar los permisos de usuario y asignar los grupos correspondientes.
2. Crear las pistas de pádel desde el menú de configuración del módulo.
3. Crear las tarifas aplicables por duración y franja horaria.
4. Revisar los parámetros de configuración:
   - Hora de apertura.
   - Hora de cierre.
   - Intervalo del planning.
   - Duraciones permitidas.
   - Minutos de bloqueo de reservas pendientes de pago.
5. Configurar el producto de reserva de pista si se desea ajustar su nombre, impuestos o cuentas contables.
6. Revisar el flujo de pago web en Odoo.
7. Probar una reserva completa en entorno de pruebas antes de activarlo públicamente.

## Uso

### Reserva web

El cliente puede acceder a la página de reserva de pádel, seleccionar fecha, duración, pista y hora disponible. Si la reserva tiene importe, el sistema crea una reserva pendiente de pago y mantiene el hueco bloqueado durante el tiempo configurado.

### Gestión interna

Los usuarios internos pueden:

- Crear reservas manuales.
- Confirmar reservas.
- Cancelar reservas.
- Finalizar reservas.
- Marcar reservas como no presentadas.
- Registrar pagos manuales.
- Consultar el planning interno.
- Crear reservas recurrentes.
- Bloquear pistas por periodos concretos.

### Portal del cliente

Los clientes autenticados pueden consultar sus reservas de pádel desde el portal. El módulo incluye vistas para listado, detalle y acciones de gestión de reservas.

## Parámetros técnicos

El módulo utiliza varios parámetros de sistema de Odoo:

- `padel.opening_hour`: hora de apertura.
- `padel.closing_hour`: hora de cierre.
- `padel.slot_step_minutes`: intervalo de los huecos del planning.
- `padel.allowed_durations`: duraciones permitidas, separadas por coma.
- `padel.payment_hold_minutes`: minutos de bloqueo de reservas pendientes de pago.
- `padel.timezone`: zona horaria usada para las reservas.
- `padel.manual_payment_default_partner_id`: contacto usado como referencia para pagos manuales cuando corresponde.

## Seguridad

El módulo define dos grupos principales:

- Usuario de pádel.
- Administrador de pádel.

Los usuarios internos tienen acceso a la app base. Los administradores del sistema reciben permisos de administración del módulo.

Antes de usarlo en producción, conviene revisar los permisos de acceso y adaptarlos a la política interna de cada instalación.

## Datos creados por el módulo

El módulo crea:

- Una secuencia para reservas de pádel.
- Un producto de tipo servicio para la reserva de pista de pádel.
- Un cron para liberar reservas web pendientes de pago caducadas.

No crea pistas ni tarifas por defecto, por lo que deben configurarse manualmente tras la instalación.

## Integración con pagos y devoluciones

El módulo se integra con el flujo de ventas y pagos de Odoo.

Incluye lógica específica para gestionar devoluciones Redsys desde la reserva. Esta funcionalidad debe probarse cuidadosamente en un entorno de pruebas antes de usarse en producción, especialmente si el proveedor de pago permite operaciones reales de devolución.

## Desarrollo

Estructura principal del módulo:

```text
odoo_padel_reservation_management/
├── controllers/
├── data/
├── models/
├── security/
├── static/
├── views/
├── __init__.py
├── __manifest__.py
└── README.md
```

## Recomendaciones antes de publicar el repositorio

Antes de hacer público este módulo, se recomienda:

1. Sustituir o parametrizar cualquier URL específica de una empresa o instalación concreta.
2. Revisar textos de emails, portal y condiciones legales para que sean genéricos o configurables.
3. Añadir un archivo `LICENSE` coherente con la licencia declarada en el manifiesto.
4. Añadir un `.gitignore` para evitar publicar cachés, archivos temporales, entornos virtuales o copias locales.
5. Probar instalación, actualización y desinstalación en una base de datos de pruebas.
6. Revisar permisos de seguridad y reglas de acceso antes de usarlo con datos reales.
7. Verificar que no se incluyen credenciales, claves API, tokens, exportaciones de base de datos ni datos personales.
8. Documentar claramente qué versión de Odoo soporta el módulo.

## Licencia

Este módulo declara licencia `LGPL-3` en su manifiesto.

Si el repositorio se va a publicar en GitHub, se recomienda añadir el texto completo de la licencia en un archivo `LICENSE`.

## Autor

Camping Fuente

Web: <https://www.campingfuente.com>
