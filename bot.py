#!/usr/bin/env python3
"""Vigila las citas de visa (ais.usvisa-info.com, Perú) y reprograma a la fecha más próxima.

Uso:
    python bot.py          vigila sin parar
    python bot.py --once   hace una sola consulta y termina (para probar)

Configuración: archivo .env (ver .env.example o ejecutar "python configurar.py").
"""
import os
import random
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

BASE = "https://ais.usvisa-info.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
MONTHS_ES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)
MONTHS_EN = (
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
)


def env(name, default=None, required=False):
    value = os.getenv(name) or default
    if required and not value:
        sys.exit(f"Falta la variable {name} en el archivo .env (ejecuta: python configurar.py)")
    return value


def parse_date(text):
    return datetime.strptime(text, "%Y-%m-%d").date()


def env_date(name, default=None, required=False):
    text = env(name, default, required)
    try:
        return parse_date(text)
    except ValueError:
        sys.exit(f"{name} debe tener el formato AAAA-MM-DD (ahora dice: {text})")


EMAIL = env("USVISA_EMAIL", required=True)
PASSWORD = env("USVISA_PASSWORD", required=True)
CURRENT_DATE = env_date("CURRENT_APPOINTMENT_DATE", required=True)
LOCALE = env("LOCALE", "es-pe")
FACILITY_ID = env("FACILITY_ID", "115")  # 115 = Lima
SCHEDULE_ID = env("SCHEDULE_ID", "")
MIN_DATE = env_date("MIN_DATE", date.today().isoformat())
CHECK_INTERVAL = float(env("CHECK_INTERVAL_SECONDS", "60"))
BLOCK_COOLDOWN = float(env("BLOCK_COOLDOWN_SECONDS", "1800"))
MAX_BOOKING_ATTEMPTS = int(env("MAX_BOOKING_ATTEMPTS", "3"))
DRY_RUN = env("DRY_RUN", "true").lower() in ("1", "true", "yes", "si", "sí")
def parse_ranges(text):
    """"2026-12-12:2026-12-18,2027-01-05" -> [(inicio, fin), ...] (días que no puedes ir)."""
    ranges = []
    for part in filter(None, (p.strip() for p in (text or "").split(","))):
        start, _, end = part.partition(":")
        try:
            ranges.append((parse_date(start), parse_date(end or start)))
        except ValueError:
            sys.exit(f"EXCLUDE_DATES tiene un rango inválido: {part} (usa AAAA-MM-DD:AAAA-MM-DD)")
    return ranges


EXCLUDE = parse_ranges(env("EXCLUDE_DATES", ""))
TELEGRAM_TOKEN = env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = env("TELEGRAM_CHAT_ID", "")
REPORT_HOUR = int(env("REPORT_HOUR", "21"))  # hora del reporte diario (0-23)
LOG_FILE = HERE / "bot.log"

NIV = f"{BASE}/{LOCALE}/niv"


class SessionExpired(Exception):
    """El sitio pidió iniciar sesión otra vez."""


class LoginError(Exception):
    """El sitio no aceptó el inicio de sesión."""


def log(message):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def notify(message):
    log(message)
    if TELEGRAM_TOKEN and TELEGRAM_CHAT:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT, "text": message},
                timeout=15,
            )
        except requests.RequestException as error:
            log(f"No se pudo enviar el aviso por Telegram: {error}")


def csrf_token(html):
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
    if not match:
        raise RuntimeError("No se encontró el token CSRF en la página")
    return match.group(1)


def pick_date(available, current, minimum, exclude=()):
    """La fecha más próxima anterior a la cita actual, no anterior al mínimo y fuera de los rangos excluidos."""
    candidates = sorted(
        d for d in available
        if minimum <= d < current and not any(a <= d <= b for a, b in exclude)
    )
    return candidates[0] if candidates else None


def page_mentions_date(html, day):
    """True si la página nombra ese día (p. ej. "5 noviembre, 2027" o "5 November, 2027")."""
    months = f"{MONTHS_ES[day.month - 1]}|{MONTHS_EN[day.month - 1]}"
    pattern = rf"\b0?{day.day}\s+(?:de\s+)?(?:{months}),?\s+(?:de\s+)?{day.year}\b"
    return re.search(pattern, html, re.IGNORECASE) is not None


def appointment_in_account(html, schedule_id):
    """Fecha de la cita consular que la cuenta muestra para esa solicitud (o None)."""
    start = html.find(f"/schedule/{schedule_id}/")
    if start < 0:
        return None
    following = re.search(r"/schedule/(?!%s/)\d+/" % re.escape(str(schedule_id)), html[start + 1:])
    block = html[start: start + 1 + following.start()] if following else html[start:]
    months = "|".join(MONTHS_ES + MONTHS_EN)
    match = re.search(
        rf"consular-appt.*?(\d{{1,2}})\s+(?:de\s+)?({months}),?\s+(?:de\s+)?(\d{{4}})",
        block, re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    name = match.group(2).lower()
    month = (MONTHS_ES.index(name) if name in MONTHS_ES else MONTHS_EN.index(name)) + 1
    return date(int(match.group(3)), month, int(match.group(1)))


class VisaClient:
    def __init__(self, http=None):
        self.http = http or requests.Session()
        self.http.headers["User-Agent"] = USER_AGENT
        self.schedule_id = SCHEDULE_ID
        self.current_date = CURRENT_DATE

    @property
    def appointment_url(self):
        return f"{NIV}/schedule/{self.schedule_id}/appointment"

    def login(self):
        page = self.http.get(f"{NIV}/users/sign_in", timeout=30)
        page.raise_for_status()
        response = self.http.post(
            f"{NIV}/users/sign_in",
            headers={
                "X-CSRF-Token": csrf_token(page.text),
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{NIV}/users/sign_in",
            },
            data={
                "user[email]": EMAIL,
                "user[password]": PASSWORD,
                "policy_confirmed": "1",
                "commit": "Iniciar sesión",
            },
            timeout=30,
        )
        account = self.account_page(allow_expired=True)
        if account is None:
            detail = re.sub(r"\s+", " ", response.text)[:500]
            if "captcha" in response.text.lower():
                detail = "el sitio pide resolver un captcha. " + detail
            raise LoginError(
                f"No se pudo iniciar sesión (HTTP {response.status_code}). "
                f"Respuesta del sitio: {detail}"
            )
        if not self.schedule_id:
            # La cuenta puede listar solicitudes antiguas; la vigente es la de número más alto.
            found = {int(number) for number in re.findall(r"/schedule/(\d+)/", account)}
            if not found:
                raise LoginError("No se encontró el número de solicitud; ponlo en SCHEDULE_ID.")
            self.schedule_id = str(max(found))
        log(f"Sesión iniciada (solicitud {self.schedule_id})")
        self.refresh_current(account)

    def refresh_current(self, account=None):
        """Lee de la cuenta la fecha real de tu cita; si no se puede, usa CURRENT_APPOINTMENT_DATE."""
        account = account if account is not None else self.account_page()
        real = appointment_in_account(account, self.schedule_id)
        previous = self.current_date
        self.current_date = real or CURRENT_DATE
        if real is None:
            log(f"AVISO: no pude leer tu cita en la cuenta; uso {CURRENT_DATE} del .env.")
        elif real != previous:
            notify(f"Tu cita en la cuenta es el {real}. Desde ahora solo busco fechas anteriores a esa.")
        return self.current_date

    def account_page(self, allow_expired=False):
        response = self.http.get(f"{NIV}/account", timeout=30)
        if "sign_in" in response.url:
            if allow_expired:
                return None
            raise SessionExpired()
        response.raise_for_status()
        return response.text

    def _json(self, path):
        response = self.http.get(
            f"{self.appointment_url}/{path}",
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": self.appointment_url,
            },
            timeout=30,
        )
        if response.status_code in (401, 403) or "sign_in" in response.url:
            raise SessionExpired()
        response.raise_for_status()
        return response.json()

    def available_dates(self):
        days = self._json(f"days/{FACILITY_ID}.json?appointments[expedite]=false")
        return [parse_date(day["date"]) for day in days]

    def available_times(self, day):
        data = self._json(
            f"times/{FACILITY_ID}.json?date={day.isoformat()}&appointments[expedite]=false"
        )
        return data.get("available_times") or data.get("business_times") or []

    def book(self, day, hour):
        """Envía la reprogramación. Devuelve True solo si el sitio la confirma."""
        page = self.http.get(self.appointment_url, timeout=30)
        if "sign_in" in page.url:
            raise SessionExpired()
        response = self.http.post(
            self.appointment_url,
            headers={"Referer": self.appointment_url},
            data={
                "authenticity_token": csrf_token(page.text),
                "confirmed_limit_message": "1",
                "use_consulate_appointment_capacity": "true",
                "appointments[consulate_appointment][facility_id]": FACILITY_ID,
                "appointments[consulate_appointment][date]": day.isoformat(),
                "appointments[consulate_appointment][time]": hour,
            },
            timeout=30,
        )
        text = response.text.lower()
        if response.ok and ("exitosamente" in text or "successfully" in text):
            return True
        # Segunda comprobación: ¿la cuenta ya muestra la fecha nueva?
        try:
            return page_mentions_date(self.account_page(), day)
        except (requests.RequestException, SessionExpired):
            return False


class DailyReport:
    """Junta lo que pasó en el día y lo envía una vez al día a la hora REPORT_HOUR."""

    def __init__(self, now=None):
        now = now or datetime.now()
        self.current = CURRENT_DATE
        self.reset()
        # Si ya pasó la hora del reporte hoy, el primero sale mañana.
        self.last_sent = now.date() if now.hour >= REPORT_HOUR else None

    def reset(self):
        self.checks = 0
        self.errors = 0
        self.earliest = None

    def record(self, earliest=None, error=False):
        self.checks += 1
        self.errors += int(error)
        if earliest and (self.earliest is None or earliest < self.earliest):
            self.earliest = earliest

    def message(self, now):
        best = f"la fecha más próxima vista fue {self.earliest}" if self.earliest else "no se vieron fechas"
        return (
            f"Reporte del {now:%d/%m/%Y}: {self.checks} consultas, {self.errors} errores; {best}. "
            f"Tu cita está el {self.current}. El bot sigue buscando desde {MIN_DATE}."
        )

    def maybe_send(self, now=None):
        now = now or datetime.now()
        if now.hour >= REPORT_HOUR and self.last_sent != now.date():
            notify(self.message(now))
            self.last_sent = now.date()
            self.reset()
            return True
        return False


def check_once(client):
    """Una consulta. Devuelve: empty, no_better, no_times, found, booked o not_booked."""
    dates = client.available_dates()
    client.last_earliest = min(dates) if dates else None
    if not dates:
        return "empty", "Sin fechas disponibles."
    current = getattr(client, "current_date", CURRENT_DATE)
    best = pick_date(dates, current, MIN_DATE, EXCLUDE)
    if best is None:
        return "no_better", f"La más próxima es {min(dates)}; no mejora la cita actual."
    times = client.available_times(best)
    if not times:
        return "no_times", f"{best} apareció pero ya no tiene horarios."
    if DRY_RUN:
        return "found", f"[PRUEBA] Hay cita el {best} a las {times[0]} (no reservada)."
    # Justo antes de reservar, vuelve a leer la cita real (la embajada pudo haberla movido).
    if best >= client.refresh_current():
        return "no_better", f"{best} ya no mejora tu cita real ({client.current_date})."
    if client.book(best, times[0]):
        return "booked", f"¡Cita reprogramada para el {best} a las {times[0]}! Verifica en la web."
    return "not_booked", f"Intenté reservar el {best} {times[0]} pero el sitio no lo confirmó."


def main(argv=None):
    once = "--once" in (sys.argv[1:] if argv is None else argv)
    if CHECK_INTERVAL < 20:
        log("AVISO: con intervalos tan cortos el sitio suele bloquear la cuenta por horas.")
    log(
        f"Cita actual: {CURRENT_DATE}. Busco fechas desde {MIN_DATE}. "
        f"Modo {'PRUEBA (no reserva)' if DRY_RUN else 'REAL (reserva automática)'}."
    )
    for start, end in EXCLUDE:
        log(f"No reservo entre {start} y {end}.")
    client = VisaClient()
    try:
        client.login()
    except (LoginError, requests.RequestException, RuntimeError) as error:
        sys.exit(str(error))

    if not once:
        notify(
            f"Bot de citas iniciado en modo {'PRUEBA' if DRY_RUN else 'REAL'}. "
            f"Cita actual: {client.current_date}. Te mando un reporte cada día a las {REPORT_HOUR}:00."
        )
    report = DailyReport()
    booking_attempts = 0
    relogins = 0
    last_outcome = None
    while True:
        wait = CHECK_INTERVAL * random.uniform(0.8, 1.2)
        try:
            outcome, message = check_once(client)
            report.record(getattr(client, "last_earliest", None))
            relogins = 0
            if outcome in ("found", "booked", "not_booked"):
                notify(message)
            elif outcome != last_outcome or outcome != "empty":
                log(message)
            last_outcome = outcome
            if outcome == "booked":
                return 0
            if outcome == "not_booked":
                booking_attempts += 1
                if booking_attempts >= MAX_BOOKING_ATTEMPTS:
                    notify(
                        f"Me detengo tras {booking_attempts} intentos de reserva sin confirmar. "
                        "Revisa tu cita en la web antes de volver a iniciarme."
                    )
                    return 2
        except SessionExpired:
            relogins += 1
            log("Sesión expirada; inicio sesión de nuevo.")
            try:
                client.login()
            except LoginError as error:
                notify(f"El bot se detuvo: {error}")
                return 1
            except (requests.RequestException, RuntimeError) as error:
                log(f"No pude iniciar sesión: {error}")
            if relogins < 3:
                continue
            log(f"La sesión se cae seguido; espero {BLOCK_COOLDOWN:.0f}s.")
            wait = BLOCK_COOLDOWN
        except requests.HTTPError as error:
            status = error.response.status_code if error.response is not None else "?"
            log(f"Error HTTP {status}; posible bloqueo. Espero {BLOCK_COOLDOWN:.0f}s.")
            report.record(error=True)
            wait = BLOCK_COOLDOWN
        except (requests.RequestException, ValueError, KeyError, RuntimeError) as error:
            log(f"Error temporal: {error}")
            report.record(error=True)
        if once:
            return 0
        report.current = client.current_date
        report.maybe_send()
        time.sleep(wait)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("Detenido.")
