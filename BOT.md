# Bot de citas de visa — Embajada de EE. UU. en Lima

Consulta periódicamente el sistema de citas (`ais.usvisa-info.com/es-pe`) y, cuando
aparece una fecha anterior a tu cita actual, la reprograma automáticamente.

## Requisitos

- Ya tener una cita agendada en el sistema (el bot **reprograma**, no crea la primera cita).
- Python 3.9 o superior.

## Instalación

```bash
pip install -r requirements.txt
cp .env.example .env   # y completa tus datos
python bot.py
```

## Cómo usarlo

1. Deja `DRY_RUN=true` la primera vez: el bot solo avisa de las fechas que encuentra.
2. Cuando veas que inicia sesión y consulta bien, pon `DRY_RUN=false`.
3. Déjalo corriendo en una computadora encendida. Al reprogramar, avisa y se detiene.

## Advertencias

- **Intervalo:** el sitio bloquea por varias horas las cuentas que consultan demasiado
  seguido. Consultar cada segundo te deja sin acceso; 60 s es un valor razonable.
- **Límite de reprogramaciones:** el sistema permite un número limitado de cambios.
  Usa `MIN_DATE` para no gastar uno en una fecha que no te sirve.
- El uso de herramientas automáticas no está permitido por los términos del sitio;
  úsalo bajo tu responsabilidad y solo con tu propia cuenta.
- Tras una reserva, confirma siempre en la web que la cita quedó registrada.
