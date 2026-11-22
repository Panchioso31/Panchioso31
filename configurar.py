#!/usr/bin/env python3
"""Asistente para completar el archivo .env sin abrir un editor.

Uso:  python configurar.py
La contraseña se escribe oculta y solo se guarda en .env (que git ignora).
"""
import getpass
import re
import shutil
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV = HERE / ".env"
EXAMPLE = HERE / ".env.example"


def ask_date(label, current, allow_empty=False):
    while True:
        hint = f" [{current}]" if current else ""
        value = input(f"{label} (AAAA-MM-DD){hint}: ").strip() or current
        if not value and allow_empty:
            return ""
        try:
            datetime.strptime(value, "%Y-%m-%d")
            return value
        except ValueError:
            print("  Formato inválido. Ejemplo: 2027-03-01")


def main():
    if not ENV.exists():
        shutil.copy(EXAMPLE, ENV)
    lines = ENV.read_text(encoding="utf-8").splitlines()
    values = {}
    for line in lines:
        match = re.match(r"^([A-Z_]+)=(.*)$", line)
        if match:
            values[match.group(1)] = match.group(2)

    print("Completa tus datos (Enter deja el valor que aparece entre corchetes).\n")
    email = input(f"Correo de la cuenta de visas [{values.get('USVISA_EMAIL', '')}]: ").strip()
    values["USVISA_EMAIL"] = email or values.get("USVISA_EMAIL", "")
    while True:
        password = getpass.getpass("Contraseña (no se verá al escribir; Enter = no cambiar): ")
        if password or values.get("USVISA_PASSWORD"):
            break
        print("  La contraseña no puede quedar vacía.")
    if password:
        values["USVISA_PASSWORD"] = password
    values["CURRENT_APPOINTMENT_DATE"] = ask_date(
        "Fecha de tu cita ACTUAL", values.get("CURRENT_APPOINTMENT_DATE", "")
    )
    values["MIN_DATE"] = ask_date(
        "Fecha más temprana que aceptas (Enter vacío = desde hoy)",
        values.get("MIN_DATE", ""),
        allow_empty=True,
    )
    values["DRY_RUN"] = "true"

    out = []
    for line in lines:
        match = re.match(r"^([A-Z_]+)=", line)
        out.append(f"{match.group(1)}={values[match.group(1)]}" if match else line)
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\nGuardado en {ENV}. Modo prueba activado (DRY_RUN=true).")
    print("Ya puedes volver al chat y decir que está listo.")


if __name__ == "__main__":
    main()
