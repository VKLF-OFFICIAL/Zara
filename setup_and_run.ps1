# =====================================================================
# ZARA v2.0 - Lanzador Táctico para Windows (PowerShell)
# Protocol J.A.R.V.I.S. | Asistente para el Señor VK
# =====================================================================

[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$Cli,
    [switch]$Enroll,
    [switch]$Install,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

# Forzar codificación de consola a UTF-8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

# Localizar intérprete de Python preferente
$PythonExe = $null

if (Test-Path "$ScriptDir\app\.venv\Scripts\python.exe") {
    $PythonExe = "$ScriptDir\app\.venv\Scripts\python.exe"
} elseif (Test-Path "$ScriptDir\.venv\Scripts\python.exe") {
    $PythonExe = "$ScriptDir\.venv\Scripts\python.exe"
} else {
    $PythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($PythonCmd) {
        $PythonExe = "python"
    } else {
        $PyCmd = Get-Command py -ErrorAction SilentlyContinue
        if ($PyCmd) {
            $PythonExe = "py -3.12"
        }
    }
}

if (-not $PythonExe) {
    Write-Host "[!] Error: No se localizó Python 3.10+ en el sistema ni en un entorno virtual." -ForegroundColor Red
    Write-Host "    Instale Python o uv para desplegar ZARA v2.0."
    exit 1
}

# Construir argumentos para setup_and_run.py
$RunArgs = @("$ScriptDir\setup_and_run.py")
if ($Check) { $RunArgs += "--check" }
if ($Cli) { $RunArgs += "--cli" }
if ($Enroll) { $RunArgs += "--enroll" }
if ($Install) { $RunArgs += "--install" }
if ($RemainingArgs) { $RunArgs += $RemainingArgs }

Write-Host "[*] Iniciando ZARA vía PowerShell..." -ForegroundColor Cyan
& $PythonExe $RunArgs
exit $LASTEXITCODE
