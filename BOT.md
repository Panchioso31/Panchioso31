# Bot de citas de visa — Embajada de EE. UU. en Lima

Consulta periódicamente el sistema de citas (`ais.usvisa-info.com/es-pe`) y, cuando
aparece una fecha anterior a tu cita actual, la reprograma automáticamente.

## Requisitos

- Ya tener una cita agendada en el sistema (el bot **reprograma**, no crea la primera cita).
- Python 3.9 o superior.

## Instalación

```bash
python -m pip install -r requirements.txt
python configurar.py    # pide tus datos (la contraseña no se ve) y crea el .env
python bot.py --once    # una sola consulta, para probar
python bot.py           # vigila sin parar
```

`python -m unittest -v` ejecuta las pruebas sin conexión (simulan el sitio).

## Cómo usarlo

1. Deja `DRY_RUN=true` la primera vez: el bot solo avisa de las fechas que encuentra.
2. Cuando veas que inicia sesión y consulta bien, pon `DRY_RUN=false`.
3. Déjalo corriendo en una computadora encendida. Al reprogramar, avisa y se detiene.

## Protecciones

- Solo reserva fechas anteriores a `CURRENT_APPOINTMENT_DATE` y no anteriores a `MIN_DATE`.
- Tras enviar una reserva, la confirma por el mensaje del sitio o porque la cuenta ya
  muestra la fecha nueva. Si no se confirma `MAX_BOOKING_ATTEMPTS` veces (3), se detiene.
- Al iniciar sesión avisa si la cuenta no muestra una cita en `CURRENT_APPOINTMENT_DATE`.
- Si la cuenta tiene varias solicitudes, usa la de número más alto (la más reciente).
- Si el sitio pide captcha al iniciar sesión, el bot se detiene y lo dice; no lo resuelve.
- Todo queda registrado en `bot.log`.

## Advertencias

- **Intervalo:** el sitio bloquea por varias horas las cuentas que consultan demasiado
  seguido. Consultar cada segundo te deja sin acceso; 60 s es un valor razonable.
- **Límite de reprogramaciones:** el sistema permite un número limitado de cambios.
  Usa `MIN_DATE` para no gastar uno en una fecha que no te sirve.
- El uso de herramientas automáticas no está permitido por los términos del sitio;
  úsalo bajo tu responsabilidad y solo con tu propia cuenta.
- Tras una reserva, confirma siempre en la web que la cita quedó registrada.

## Pendientes (lo que falta que hagas tú)

- [ ] **Dar acceso a GitHub** para que el código se pueda subir al repositorio:
      instala la app de Claude en <https://github.com/apps/claude/installations/select_target>
      (elige tu cuenta y marca el repositorio `Panchioso31`).
- [ ] **Tener una cita ya agendada** en <https://ais.usvisa-info.com/es-pe/niv> (tarifa pagada).
- [ ] **Instalar Python 3.9+** en la computadora que quedará encendida (<https://www.python.org/downloads/>).
- [ ] **Crear el archivo `.env`** copiando `.env.example` y completar:
  - [ ] `USVISA_EMAIL` y `USVISA_PASSWORD` (tu cuenta de visas)
  - [ ] `CURRENT_APPOINTMENT_DATE` (fecha de tu cita actual, AAAA-MM-DD)
  - [ ] `MIN_DATE` (la fecha más temprana que te sirve)
- [ ] **Primera corrida en modo prueba** (`DRY_RUN=true`): `python bot.py` y comprobar que
      aparece "Sesión iniciada" y que lista fechas sin errores.
- [ ] **Activar la reserva real**: cambiar a `DRY_RUN=false` y dejarlo corriendo.
- [ ] (Opcional) **Avisos por Telegram**: crear un bot con @BotFather y completar
      `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`.
- [ ] **Después de que reserve**: entrar a la web y confirmar que la cita nueva quedó registrada.
