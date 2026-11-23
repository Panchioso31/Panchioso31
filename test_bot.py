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

    def __init__(self, days=(), times=("08:15", "09:00"), logged_in=True, book_text="", account=ACCOUNT):
        self.headers = {}
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
        return Response(self.book_text)


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


class BookingTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(bot, "DRY_RUN", False)
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
        site.account = ACCOUNT + "<p>Cita Consular: 1 febrero, 2027, 08:15</p>"
        self.assertEqual(bot.check_once(client)[0], "booked")

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


class MainLoopTest(unittest.TestCase):
    def run_main(self, site, dry_run=True):
        with mock.patch.object(bot, "DRY_RUN", dry_run), \
                mock.patch.object(bot.requests, "Session", return_value=site), \
                mock.patch.object(bot.time, "sleep") as sleep:
            return bot.main([]), sleep

    def test_se_detiene_al_reservar(self):
        site = FakeSite(days=["2027-02-01"], book_text="successfully scheduled")
        code, sleep = self.run_main(site, dry_run=False)
        self.assertEqual(code, 0)
        sleep.assert_not_called()

    def test_se_detiene_tras_intentos_sin_confirmar(self):
        site = FakeSite(days=["2027-02-01"], book_text="no disponible")
        code, sleep = self.run_main(site, dry_run=False)
        self.assertEqual(code, 2)
        bookings = [p for p in site.posts if p[0].endswith("/appointment")]
        self.assertEqual(len(bookings), 3)
        self.assertEqual(sleep.call_count, 2)

    def test_once_hace_una_sola_consulta(self):
        site = FakeSite(days=["2027-02-01"])
        with mock.patch.object(bot.requests, "Session", return_value=site), \
                mock.patch.object(bot.time, "sleep") as sleep:
            self.assertEqual(bot.main(["--once"]), 0)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
