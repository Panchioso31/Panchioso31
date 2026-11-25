"""Pruebas sin conexión: simulan las respuestas del sitio. Uso: python -m unittest -v"""
import os
import unittest
from datetime import date
from unittest import mock

os.environ.update(
    USVISA_EMAIL="prueba@example.com",
    USVISA_PASSWORD="clave-de-prueba",
    CURRENT_APPOINTMENT_DATE="2027-11-05",
    MIN_DATE="2026-12-01",
    DRY_RUN="true",
    SCHEDULE_ID="",
    FACILITY_ID="115",
    LOCALE="es-pe",
    CHECK_INTERVAL_SECONDS="60",
    MAX_BOOKING_ATTEMPTS="3",
    MIN_GAIN_DAYS="0",
    EXCLUDE_DATES="",
    TELEGRAM_BOT_TOKEN="",
    TELEGRAM_CHAT_ID="",
)

import bot  # noqa: E402

bot.LOG_FILE = os.devnull
CSRF = '<meta name="csrf-token" content="TOKEN123" />'
ACCOUNT = (
    CSRF
    + '<a href="/es-pe/niv/schedule/76627525/continue">vieja</a>'
    + '<a href="/es-pe/niv/schedule/80000001/continue">nueva</a>'
    + "<p>Cita Consular: 5 noviembre, 2027, 08:15 Lima Hora Local at Lima</p>"
)


class Response:
    def __init__(self, text="", status=200, url="https://ais.usvisa-info.com/x", data=None):
        self.text, self.status_code, self.url, self._data = text, status, url, data
        self.ok = status < 400

    def json(self):
        if self._data is None:
            raise ValueError("no es JSON")
        return self._data

    def raise_for_status(self):
        if not self.ok:
            raise bot.requests.HTTPError(response=self)


class FakeSite:
    """Sesión HTTP falsa: responde según la URL y guarda lo que se envió."""

    def __init__(self, days=(), times=("08:15", "09:00"), logged_in=True, book_text="", account=ACCOUNT,
                 applies=None):
        self.headers = {}
        # applies: si el sitio de verdad cambia la cita (por defecto, cuando responde con éxito).
        self.applies = applies if applies is not None else any(
            word in book_text for word in ("exitosamente", "successfully"))
        self.days, self.times, self.logged_in = list(days), list(times), logged_in
        self.book_text, self.account = book_text, account
        self.posts = []

    def get(self, url, **kwargs):
        if url.endswith("/users/sign_in"):
            return Response(CSRF)
        if not self.logged_in:
            return Response("", url="https://ais.usvisa-info.com/es-pe/niv/users/sign_in")
        if url.endswith("/account"):
            return Response(self.account)
        if "/days/" in url:
            return Response(data=[{"date": d, "business_day": True} for d in self.days])
        if "/times/" in url:
            return Response(data={"available_times": self.times, "business_times": self.times})
        return Response(CSRF)

    def post(self, url, data=None, **kwargs):
        self.posts.append((url, data))
        if url.endswith("/users/sign_in"):
            return Response("captcha requerido" if not self.logged_in else "ok")
        if self.applies and url.endswith("/appointment"):
            schedule = url.split("/schedule/")[1].split("/")[0]
            self.account = account_with(schedule, data["appointments[consulate_appointment][date]"])
        return Response(self.book_text)


def account_with(schedule, iso_day):
    day = date.fromisoformat(iso_day)
    return (
        CSRF + f'<a href="/es-pe/niv/schedule/{schedule}/continue_actions">Continuar</a>'
        + f'<p class="consular-appt">Cita Consular: {day.day} {bot.MONTHS_ES[day.month - 1]}, {day.year}, 08:15 Lima</p>'
    )


def client_for(site):
    client = bot.VisaClient(http=site)
    client.login()
    return client


class PickDateTest(unittest.TestCase):
    def test_elige_la_mas_proxima_dentro_del_rango(self):
        dates = [date(2027, 3, 1), date(2026, 11, 30), date(2027, 1, 10), date(2028, 1, 1)]
        self.assertEqual(bot.pick_date(dates, date(2027, 11, 5), date(2026, 12, 1)), date(2027, 1, 10))

    def test_salta_los_dias_excluidos(self):
        dates = [date(2026, 12, 12), date(2026, 12, 18), date(2026, 12, 19)]
        excl = bot.parse_ranges("2026-12-12:2026-12-18")
        self.assertEqual(bot.pick_date(dates, date(2027, 8, 2), date(2026, 11, 23), excl), date(2026, 12, 19))

    def test_ignora_la_fecha_actual_y_posteriores(self):
        dates = [date(2027, 11, 5), date(2027, 12, 1)]
        self.assertIsNone(bot.pick_date(dates, date(2027, 11, 5), date(2026, 12, 1)))


class PageDateTest(unittest.TestCase):
    def test_reconoce_espanol_e_ingles(self):
        day = date(2027, 11, 5)
        self.assertTrue(bot.page_mentions_date("Cita: 5 noviembre, 2027, 08:15", day))
        self.assertTrue(bot.page_mentions_date("5 de noviembre de 2027", day))
        self.assertTrue(bot.page_mentions_date("5 November, 2027", day))
        self.assertFalse(bot.page_mentions_date("15 noviembre, 2027", day))
        self.assertFalse(bot.page_mentions_date("5 noviembre, 2026", day))


REAL_ACCOUNT = (
    CSRF
    + '<a href="/es-pe/niv/schedule/80000001/continue_actions">Continuar</a>'
    + '<p class="consular-appt"><strong>Cita Consular:</strong> 15 enero, 2027, 09:30 Lima</p>'
    + '<a href="/es-pe/niv/schedule/76627525/continue_actions">Continuar</a>'
    + '<p class="consular-appt"><strong>Cita Consular:</strong> 5 noviembre, 2026, 07:30 Lima</p>'
)


class RealAppointmentTest(unittest.TestCase):
    def test_lee_la_cita_de_la_solicitud_correcta(self):
        self.assertEqual(bot.appointment_in_account(REAL_ACCOUNT, "80000001"), date(2027, 1, 15))
        self.assertEqual(bot.appointment_in_account(REAL_ACCOUNT, "76627525"), date(2026, 11, 5))
        self.assertIsNone(bot.appointment_in_account(REAL_ACCOUNT, "99999999"))

    def test_si_la_embajada_adelanto_la_cita_no_reserva_algo_peor(self):
        # .env dice 2027-11-05, pero la cuenta ya muestra 2027-01-15: 2027-02-01 sería peor.
        site = FakeSite(days=["2027-02-01"], book_text="successfully", account=REAL_ACCOUNT)
        with mock.patch.object(bot, "DRY_RUN", False), mock.patch.object(bot, "notify"):
            outcome, _ = bot.check_once(client_for(site))
        self.assertEqual(outcome, "no_better")
        self.assertFalse(any(url.endswith("/appointment") for url, _ in site.posts))


class LoginTest(unittest.TestCase):
    def test_elige_la_solicitud_mas_reciente(self):
        self.assertEqual(client_for(FakeSite()).schedule_id, "80000001")

    def test_envia_credenciales_y_casilla_de_politicas(self):
        site = FakeSite()
        client_for(site)
        _, data = site.posts[0]
        self.assertEqual(data["user[email]"], "prueba@example.com")
        self.assertEqual(data["policy_confirmed"], "1")

    def test_falla_con_mensaje_si_hay_captcha(self):
        with self.assertRaises(bot.LoginError) as caught:
            client_for(FakeSite(logged_in=False))
        self.assertIn("captcha", str(caught.exception))


class CheckOnceTest(unittest.TestCase):
    def test_sin_fechas(self):
        self.assertEqual(bot.check_once(client_for(FakeSite()))[0], "empty")

    def test_fecha_que_no_mejora(self):
        site = FakeSite(days=["2028-02-01"])
        self.assertEqual(bot.check_once(client_for(site))[0], "no_better")

    def test_fecha_sin_horarios(self):
        site = FakeSite(days=["2027-02-01"], times=[])
        self.assertEqual(bot.check_once(client_for(site))[0], "no_times")

    def test_modo_prueba_no_reserva(self):
        site = FakeSite(days=["2027-02-01"])
        client = client_for(site)
        outcome, message = bot.check_once(client)
        self.assertEqual(outcome, "found")
        self.assertIn("2027-02-01", message)
        self.assertEqual(len(site.posts), 1)  # solo el inicio de sesión

    def test_sesion_expirada(self):
        site = FakeSite(days=["2027-02-01"])
        client = client_for(site)
        site.logged_in = False
        with self.assertRaises(bot.SessionExpired):
            bot.check_once(client)


class MinGainTest(unittest.TestCase):
    def test_no_reserva_si_adelanta_menos_de_30_dias(self):
        # Cita actual 2027-11-05: el 2027-10-20 adelanta solo 16 días.
        site = FakeSite(days=["2027-10-20"], book_text="successfully")
        with mock.patch.object(bot, "DRY_RUN", False), mock.patch.object(bot, "MIN_GAIN_DAYS", 30):
            self.assertEqual(bot.check_once(client_for(site))[0], "no_better")
        self.assertFalse(any(url.endswith("/appointment") for url, _ in site.posts))

    def test_reserva_si_adelanta_30_dias_o_mas(self):
        site = FakeSite(days=["2027-10-06", "2027-10-20"], book_text="successfully")
        with mock.patch.object(bot, "DRY_RUN", False), mock.patch.object(bot, "MIN_GAIN_DAYS", 30):
            self.assertEqual(bot.check_once(client_for(site))[0], "booked")
        self.assertEqual(site.posts[-1][1]["appointments[consulate_appointment][date]"], "2027-10-06")


class BookingTest(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(bot, "DRY_RUN", False), mock.patch.object(bot.time, "sleep")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_reserva_confirmada_por_mensaje(self):
        site = FakeSite(days=["2027-02-01"], book_text="Ha programado exitosamente su cita")
        outcome, _ = bot.check_once(client_for(site))
        self.assertEqual(outcome, "booked")
        url, data = site.posts[-1]
        self.assertTrue(url.endswith("/schedule/80000001/appointment"))
        self.assertEqual(data["appointments[consulate_appointment][date]"], "2027-02-01")
        self.assertEqual(data["appointments[consulate_appointment][time]"], "08:15")
        self.assertEqual(data["authenticity_token"], "TOKEN123")

    def test_reserva_confirmada_por_la_cuenta(self):
        site = FakeSite(days=["2027-02-01"], book_text="redirigido")
        client = client_for(site)
        site.applies = True
        self.assertEqual(bot.check_once(client)[0], "booked")
        self.assertEqual(client.current_date, date(2027, 2, 1))

    def test_mensaje_de_exito_sin_cambio_en_la_cuenta_no_cuenta(self):
        # Pasó de verdad: el sitio dijo "exitosamente" pero la cita siguió igual.
        site = FakeSite(days=["2027-02-01"], book_text="Ha programado exitosamente su cita", applies=False)
        self.assertEqual(bot.check_once(client_for(site))[0], "not_booked")

    def test_ignora_horas_nulas(self):
        # Pasó de verdad: available_times = [None] para el 2027-01-08.
        site = FakeSite(days=["2027-02-01"], times=[None], book_text="successfully")
        self.assertEqual(bot.check_once(client_for(site))[0], "no_times")
        self.assertFalse(any(url.endswith("/appointment") for url, _ in site.posts))
        site = FakeSite(days=["2027-02-01"], times=[None, "09:00"], book_text="successfully")
        self.assertEqual(bot.check_once(client_for(site))[0], "booked")
        self.assertEqual(site.posts[-1][1]["appointments[consulate_appointment][time]"], "09:00")

    def test_salta_una_hora_la_fecha_que_fallo(self):
        site = FakeSite(days=["2027-01-08", "2027-02-01"], book_text="no disponible")
        client = client_for(site)
        self.assertEqual(bot.check_once(client)[0], "not_booked")
        site.applies = True
        self.assertEqual(bot.check_once(client)[0], "booked")
        self.assertEqual(site.posts[-1][1]["appointments[consulate_appointment][date]"], "2027-02-01")

    def test_reserva_no_confirmada(self):
        site = FakeSite(days=["2027-02-01"], book_text="El horario ya no está disponible")
        self.assertEqual(bot.check_once(client_for(site))[0], "not_booked")


class DailyReportTest(unittest.TestCase):
    def test_envia_una_vez_al_dia_a_la_hora(self):
        from datetime import datetime
        with mock.patch.object(bot, "REPORT_HOUR", 21), mock.patch.object(bot, "notify") as notify:
            report = bot.DailyReport(now=datetime(2026, 11, 23, 10, 0))
            report.record(date(2027, 8, 2))
            report.record(date(2027, 3, 1))
            report.record(error=True)
            self.assertFalse(report.maybe_send(datetime(2026, 11, 23, 20, 59)))
            self.assertTrue(report.maybe_send(datetime(2026, 11, 23, 21, 0)))
            self.assertIn("3 consultas, 1 errores", notify.call_args[0][0])
            self.assertIn("2027-03-01", notify.call_args[0][0])
            self.assertFalse(report.maybe_send(datetime(2026, 11, 23, 23, 0)))
            self.assertTrue(report.maybe_send(datetime(2026, 11, 24, 21, 5)))

    def test_si_arranca_tarde_espera_al_dia_siguiente(self):
        from datetime import datetime
        with mock.patch.object(bot, "REPORT_HOUR", 21), mock.patch.object(bot, "notify"):
            report = bot.DailyReport(now=datetime(2026, 11, 23, 22, 0))
            self.assertFalse(report.maybe_send(datetime(2026, 11, 23, 23, 0)))

    def test_reporte_cada_hora(self):
        from datetime import datetime
        with mock.patch.object(bot, "REPORT_HOURLY", True), mock.patch.object(bot, "notify") as notify:
            report = bot.DailyReport(now=datetime(2026, 11, 23, 10, 15))
            report.record(date(2027, 8, 2))
            self.assertFalse(report.maybe_send(datetime(2026, 11, 23, 10, 59)))
            self.assertTrue(report.maybe_send(datetime(2026, 11, 23, 11, 0)))
            self.assertIn("23/11/2026 11:00", notify.call_args[0][0])
            self.assertIn("1 consultas", notify.call_args[0][0])
            self.assertFalse(report.maybe_send(datetime(2026, 11, 23, 11, 30)))
            self.assertTrue(report.maybe_send(datetime(2026, 11, 23, 12, 1)))
            self.assertIn("0 consultas", notify.call_args[0][0])


class MainLoopTest(unittest.TestCase):
    def run_main(self, site, dry_run=True):
        with mock.patch.object(bot, "DRY_RUN", dry_run), \
                mock.patch.object(bot.requests, "Session", return_value=site), \
                mock.patch.object(bot.time, "sleep") as sleep:
            return bot.main([]), sleep

    def test_sigue_buscando_tras_reservar(self):
        site = FakeSite(days=["2027-02-01"], book_text="successfully scheduled")

        class Stop(Exception):
            pass

        with mock.patch.object(bot, "DRY_RUN", False), mock.patch.object(bot, "notify"),                 mock.patch.object(bot.requests, "Session", return_value=site),                 mock.patch.object(bot.time, "sleep", side_effect=Stop):
            with self.assertRaises(Stop):  # llegó a la espera: no se detuvo tras reservar
                bot.main([])
        bookings = [p for p in site.posts if p[0].endswith("/appointment")]
        self.assertEqual(len(bookings), 1)

    def test_no_se_apaga_si_una_reserva_falla(self):
        site = FakeSite(days=["2027-02-01"], book_text="no disponible")

        class Stop(Exception):
            pass

        with mock.patch.object(bot, "DRY_RUN", False), mock.patch.object(bot, "notify"),                 mock.patch.object(bot.requests, "Session", return_value=site),                 mock.patch.object(bot.time, "sleep", side_effect=[None, None, Stop]):
            with self.assertRaises(Stop):
                bot.main([])
        bookings = [p for p in site.posts if p[0].endswith("/appointment")]
        self.assertEqual(len(bookings), 1)  # la fecha fallida se salta en las vueltas siguientes

    def test_once_hace_una_sola_consulta(self):
        site = FakeSite(days=["2027-02-01"])
        with mock.patch.object(bot.requests, "Session", return_value=site), \
                mock.patch.object(bot.time, "sleep") as sleep:
            self.assertEqual(bot.main(["--once"]), 0)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
