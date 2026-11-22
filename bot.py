#!/usr/bin/env python3
"""Vigila las citas de visa (ais.usvisa-info.com, Perú) y reprograma a la fecha más próxima.

Uso:  python bot.py
Configuración: archivo .env (ver .env.example).
"""
import os
import random
import re
import sys
import time
from datetime import date, datetime

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = "https://ais.usvisa-info.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and not value:
        sys.exit(f"Falta la variable {name} en el archivo .env")
    return value


def parse_date(text):
    return datetime.strptime(text, "%Y-%m-%d").date()


EMAIL = env("USVISA_EMAIL", required=True)
PASSWORD = env("USVISA_PASSWORD", required=True)
CURRENT_DATE = parse_date(env("CURRENT_APPOINTMENT_DATE", required=True))
LOCALE = env("LOCALE", "es-pe")
FACILITY_ID = env("FACILITY_ID", "115")  # 115 = Lima
SCHEDULE_ID = env("SCHEDULE_ID", "")
MIN_DATE = parse_date(env("MIN_DATE", date.today().isoformat()))
CHECK_INTERVAL = float(env("CHECK_INTERVAL_SECONDS", "60"))
BLOCK_COOLDOWN = float(env("BLOCK_COOLDOWN_SECONDS", "1800"))
DRY_RUN = env("DRY_RUN", "true").lower() in ("1", "true", "yes", "si", "sí")
TELEGRAM_TOKEN = env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = env("TELEGRAM_CHAT_ID", "")

NIV = f"{BASE}/{LOCALE}/niv"


class SessionExpired(Exception):
    pass


def log(message):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


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


def pick_date(available, current, minimum):
    """La fecha más próxima que sea anterior a la cita actual y no anterior al mínimo."""
    candidates = sorted(d for d in available if minimum <= d < current)
    return candidates[0] if candidates else None


class VisaClient:
    def __init__(self):
        self.http = requests.Session()
        self.http.headers["User-Agent"] = USER_AGENT
        self.schedule_id = SCHEDULE_ID

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
        account = self.http.get(f"{NIV}/account", timeout=30)
        if "sign_in" in account.url:
            detail = re.sub(r"\s+", " ", response.text)[:500]
            if "captcha" in response.text.lower():
                detail = "el sitio pide resolver un captcha. " + detail
            sys.exit(
                f"No se pudo iniciar sesión (HTTP {response.status_code}). "
                f"Respuesta del sitio: {detail}"
            )
        if not self.schedule_id:
            # La cuenta puede listar solicitudes antiguas; la vigente es la de número más alto.
            found = {int(number) for number in re.findall(r"/schedule/(\d+)/", account.text)}
            if not found:
                sys.exit("No se encontró el número de solicitud; ponlo en SCHEDULE_ID.")
            self.schedule_id = str(max(found))
        log(f"Sesión iniciada (solicitud {self.schedule_id})")

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
        return response.ok and ("exitosamente" in text or "successfully" in text)


def main():
    if CHECK_INTERVAL < 20:
        log("AVISO: con intervalos tan cortos el sitio suele bloquear la cuenta por horas.")
    log(
        f"Cita actual: {CURRENT_DATE}. Busco fechas desde {MIN_DATE}. "
        f"Modo {'PRUEBA (no reserva)' if DRY_RUN else 'REAL (reserva automática)'}."
    )
    client = VisaClient()
    client.login()

    while True:
        wait = CHECK_INTERVAL * random.uniform(0.8, 1.2)
        try:
            dates = client.available_dates()
            if not dates:
                # Lista vacía: no hay cupos o el sitio limitó temporalmente la cuenta.
                log("Sin fechas disponibles.")
            else:
                best = pick_date(dates, CURRENT_DATE, MIN_DATE)
                if best is None:
                    log(f"La más próxima es {min(dates)}; no mejora la cita actual.")
                else:
                    times = client.available_times(best)
                    if not times:
                        log(f"{best} apareció pero ya no tiene horarios.")
                    elif DRY_RUN:
                        notify(f"[PRUEBA] Hay cita el {best} a las {times[0]} (no reservada).")
                    elif client.book(best, times[0]):
                        notify(f"¡Cita reprogramada para el {best} a las {times[0]}! Verifica en la web.")
                        return
                    else:
                        notify(f"Intenté reservar el {best} {times[0]} pero no se confirmó. Revisa la web.")
        except SessionExpired:
            log("Sesión expirada; inicio sesión de nuevo.")
            client.login()
            continue
        except requests.HTTPError as error:
            status = error.response.status_code if error.response is not None else "?"
            log(f"Error HTTP {status}; posible bloqueo. Espero {BLOCK_COOLDOWN:.0f}s.")
            wait = BLOCK_COOLDOWN
        except (requests.RequestException, ValueError, RuntimeError) as error:
            log(f"Error temporal: {error}")
        time.sleep(wait)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Detenido.")
