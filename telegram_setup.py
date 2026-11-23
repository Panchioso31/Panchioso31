#!/usr/bin/env python3
"""Configura los avisos por Telegram. Uso: python telegram_setup.py

1. En Telegram, habla con @BotFather, envía /newbot y copia el token que te da.
2. Ejecuta este script y pega el token (no se verá).
3. Abre tu bot nuevo en Telegram y envíale cualquier mensaje (por ejemplo "hola").
El script detecta tu chat, lo guarda en .env y te manda un mensaje de prueba.
"""
import getpass
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

ENV = Path(__file__).resolve().parent / ".env"


def save(key, value):
    text = ENV.read_text(encoding="utf-8")
    if re.search(rf"(?m)^{key}=", text):
        text = re.sub(rf"(?m)^{key}=.*$", lambda _: f"{key}={value}", text)
    else:
        text += f"\n{key}={value}\n"
    ENV.write_text(text, encoding="utf-8")


def clipboard_token():
    """Lee el token del portapapeles de Windows (después de hacer clic en él en Telegram)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    match = re.search(r"\d{6,}:[A-Za-z0-9_-]{30,}", out)
    return match.group(0) if match else ""


def main():
    print("\n" * 40)
    token = clipboard_token() if "--portapapeles" in sys.argv else ""
    if token:
        print("Token leído del portapapeles.")
    else:
        print("PEGA EL TOKEN DE TU BOT DE TELEGRAM Y PULSA ENTER (no se verá).")
    while True:
        token = token or getpass.getpass("Token: ").strip()
        me = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15).json()
        if me.get("ok"):
            break
        print("Ese token no funciona. Cópialo de nuevo desde @BotFather.")
        token = ""
    name = me["result"]["username"]
    save("TELEGRAM_BOT_TOKEN", token)
    print(f"\nToken correcto. Ahora abre @{name} en Telegram, pulsa Iniciar y envíale 'hola'.")
    print("Esperando tu mensaje...")
    chat_id = None
    for _ in range(120):  # hasta 10 minutos
        updates = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates", timeout=15
        ).json().get("result", [])
        for update in updates:
            message = update.get("message") or {}
            if message.get("chat", {}).get("id"):
                chat_id = message["chat"]["id"]
        if chat_id:
            break
        time.sleep(5)
    if not chat_id:
        print("No llegó ningún mensaje. Vuelve a ejecutar el script.")
        return
    save("TELEGRAM_CHAT_ID", chat_id)
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": "Listo: aquí te llegarán los avisos y el reporte diario del bot de citas."},
        timeout=15,
    )
    print("\nListo. Te llegó un mensaje de prueba a Telegram. Vuelve al chat y escribe: listo")


if __name__ == "__main__":
    main()
