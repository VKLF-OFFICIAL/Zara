# =====================================================================
# ZARA - Lanzador Táctico para Windows (PowerShell 5.1 y 7)
# Protocol J.A.R.V.I.S. | Asistente para el Señor VK
# =====================================================================
# Este archivo se guarda en UTF-8 con BOM: sin él, Windows PowerShell 5.1 lo lee como
# ANSI y los acentos (o una raya "–") pueden romper el análisis del script.

[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$Cli,
    [switch]$Install,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

# Forzar codificación de consola a UTF-8
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

# True si el programa arranca y termina con código 0 (sin mostrar su salida).
function Test-Exec {
    param([string]$Exe, [string[]]$ExeArgs = @())
    try {
        & $Exe @ExeArgs 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# True si el intérprete es Python >= 3.10 y funciona. El alias de la Microsoft Store
# (WindowsApps\python.exe sin Python instalado) sale con 9009 y no pasa la prueba.
function Test-Python {
    param([string]$Exe, [string[]]$PreArgs = @())
    return (Test-Exec -Exe $Exe -ExeArgs ($PreArgs + @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)')))
}

# [FIX MUL-4/V1_-2] Busca un Python base para crear el venv. Devuelve @{ Exe; PreArgs } o $null.
# El ejecutable y sus argumentos van separados: `& "py -3.12"` buscaba un comando con ese
# nombre literal (CommandNotFoundException).
function Find-BasePython {
    $storeAliases = @()
    # 1) python.exe reales del PATH; los de WindowsApps se dejan para el final
    foreach ($cmd in @(Get-Command python.exe -CommandType Application -All -ErrorAction SilentlyContinue)) {
        if ($cmd.Path -like '*\WindowsApps\*') {
            $storeAliases += $cmd.Path
            continue
        }
        if (Test-Python -Exe $cmd.Path) {
            return @{ Exe = $cmd.Path; PreArgs = @() }
        }
    }
    # 2) Lanzador py de python.org (instalación sin "Add python.exe to PATH"). Se prefieren
    #    3.13/3.12, que tienen todas las dependencias (Kokoro no existe para 3.14+).
    if (Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue) {
        foreach ($version in @('-3.13', '-3.12', '-3.11', '-3.10', '-3')) {
            if (Test-Python -Exe 'py' -PreArgs @($version)) {
                return @{ Exe = 'py'; PreArgs = @($version) }
            }
        }
    }
    # 3) Python de la Microsoft Store (el alias solo funciona si está instalado de verdad)
    foreach ($alias in $storeAliases) {
        if (Test-Python -Exe $alias) {
            return @{ Exe = $alias; PreArgs = @() }
        }
    }
    return $null
}

# Localizar intérprete de Python preferente
$PythonExe = $null
$AutoInstall = $false
$AppVenvPy = Join-Path $ScriptDir 'app\.venv\Scripts\python.exe'
$VenvDir = Join-Path $ScriptDir '.venv'
$VenvPy = Join-Path $VenvDir 'Scripts\python.exe'

if (Test-Path $AppVenvPy) {
    # Entorno que gestiona el usuario: se usa tal cual
    $PythonExe = $AppVenvPy
} else {
    # Un .venv roto (copiado de otro equipo o creado con un Python ya desinstalado) se recrea
    if ((Test-Path $VenvDir) -and -not (Test-Exec -Exe $VenvPy -ExeArgs @('-m', 'pip', '--version'))) {
        Write-Host "[!] El entorno virtual .venv está incompleto o roto. Recreándolo..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force $VenvDir -ErrorAction SilentlyContinue
    }

    if (-not (Test-Path $VenvPy)) {
        $Base = Find-BasePython
        if (-not $Base) {
            Write-Host "[!] Error: No se localizó Python 3.10+ en el sistema ni en un entorno virtual." -ForegroundColor Red
            Write-Host "    Instálelo con:  winget install -e --id Python.Python.3.12"
            Write-Host "    o desde https://www.python.org/downloads/ marcando 'Add python.exe to PATH'."
            Write-Host "    Si Windows abre la Microsoft Store al escribir 'python', desactive el alias en"
            Write-Host "    Configuración > Aplicaciones > Alias de ejecución de aplicaciones."
            exit 1
        }
        $BaseExe = $Base.Exe
        $BaseArgs = $Base.PreArgs
        Write-Host "[*] Creando entorno virtual .venv con $BaseExe $BaseArgs..." -ForegroundColor Cyan
        & $BaseExe @BaseArgs -m venv $VenvDir
        if (($LASTEXITCODE -ne 0) -or -not (Test-Path $VenvPy)) {
            Write-Host "[!] Error: No se pudo crear el entorno virtual .venv." -ForegroundColor Red
            Remove-Item -Recurse -Force $VenvDir -ErrorAction SilentlyContinue
            exit 1
        }
        & $VenvPy -m pip install --upgrade pip
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[!] No se pudo actualizar pip; se continúa con el del entorno virtual." -ForegroundColor Yellow
        }
    }

    $PythonExe = $VenvPy
    # setup_and_run.py instala las dependencias o completa una instalación que se cortó;
    # si ya está todo, no descarga nada.
    $AutoInstall = $true
}

# Construir argumentos para setup_and_run.py
$RunArgs = @(Join-Path $ScriptDir 'setup_and_run.py')
if ($Check) { $RunArgs += "--check" }
if ($Cli) { $RunArgs += "--cli" }
if ($Install -or $AutoInstall) { $RunArgs += "--install" }
if ($RemainingArgs) { $RunArgs += $RemainingArgs }

Write-Host "[*] Iniciando ZARA vía PowerShell..." -ForegroundColor Cyan
& $PythonExe @RunArgs
exit $LASTEXITCODE
