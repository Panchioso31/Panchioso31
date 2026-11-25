# Mantiene el bot corriendo: si se cierra o falla, lo vuelve a iniciar al minuto.
# Lo lanza la tarea programada "BotCitasVisa" al iniciar sesión en Windows.
$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot
$python = "C:\Users\Francisco\AppData\Local\Python\pythoncore-3.15-64\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

# Evita dos bots a la vez (comparten la sesión de la cuenta y se botarían entre sí).
$mutex = New-Object System.Threading.Mutex($false, "Global\BotCitasVisa")
if (-not $mutex.WaitOne(0)) { exit 0 }

while ($true) {
    & $python -u bot.py *>> "$PSScriptRoot\bot_consola.log"
    Add-Content -Path "$PSScriptRoot\bot_consola.log" -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] El bot terminó (código $LASTEXITCODE); lo reinicio en 60 s."
    Start-Sleep -Seconds 60
}
