#!/usr/bin/env python3
"""Pide solo la contraseña (oculta) y la guarda en .env. Uso: python clave.py"""
import getpass
import re
from pathlib import Path

ENV = Path(__file__).resolve().parent / ".env"

print("\n" * 40)  # deja la pantalla limpia
print("ESCRIBE TU CONTRASEÑA DE LA CUENTA DE VISAS Y PULSA ENTER.")
print("(No se verá nada mientras escribes; es normal.)\n")
while True:
    password = getpass.getpass("Contraseña: ")
    if password:
        break
    print("Está vacía, inténtalo de nuevo.")

text = ENV.read_text(encoding="utf-8")
text = re.sub(r"(?m)^USVISA_PASSWORD=.*$", lambda _: f"USVISA_PASSWORD={password}", text)
ENV.write_text(text, encoding="utf-8")
print("\nListo, contraseña guardada. Vuelve al chat y escribe: listo")
