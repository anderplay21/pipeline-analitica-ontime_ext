# ============================================================
# crear_tarea_programada.ps1
# Crea (o reemplaza) la tarea de Windows "pipeline_ontime", que ejecuta run_pipeline.bat
# cada 30 minutos.
#
# Requiere PowerShell COMO ADMINISTRADOR: una tarea que corre sin sesión iniciada (S4U)
# solo la puede registrar un administrador.
#
#   powershell -ExecutionPolicy Bypass -File .\crear_tarea_programada.ps1
#   powershell -ExecutionPolicy Bypass -File .\crear_tarea_programada.ps1 -Inicio "2026-12-01 06:00" -Minutos 60
# ============================================================

param(
    [datetime]$Inicio = (Get-Date "2026-11-25 10:00"),
    [int]$Minutos = 30,
    [string]$Cuenta = "icoltrans\automatizacion",
    # Carpeta del Programador de tareas; se crea sola si no existe.
    [string]$Carpeta = "\Automatizaciones\1) Pipeline OnTime\"
)

$ErrorActionPreference = "Stop"
$raiz = $PSScriptRoot
$nombre = "pipeline_ontime"

# Si la tarea existe en otra carpeta (p. ej. la raíz, donde se creó al principio), se
# elimina para no dejar dos copias corriendo el mismo pipeline.
Get-ScheduledTask -TaskName $nombre -ErrorAction SilentlyContinue |
    Where-Object { $_.TaskPath -ne $Carpeta } |
    ForEach-Object {
        Unregister-ScheduledTask -TaskName $nombre -TaskPath $_.TaskPath -Confirm:$false
        "Eliminada la tarea anterior: $($_.TaskPath)$nombre"
    }

# Carpetas vacías que dejaron versiones anteriores de este script. Ordenar es opcional:
# si falla, se avisa y se sigue, para que nunca impida crear la tarea.
foreach ($vieja in @("\Automatizaciones\2) Pipeline OnTime")) {
    if ($vieja -eq $Carpeta.TrimEnd("\")) { continue }
    try {
        $servicio = New-Object -ComObject Schedule.Service
        $servicio.Connect()
        $f = $servicio.GetFolder($vieja)
        if ($f.GetTasks(1).Count -eq 0 -and $f.GetFolders(0).Count -eq 0) {
            # GetFolder no acepta rutas con "\" final (salvo la raíz).
            $padre = $vieja -replace '\\[^\\]+$', ''
            if (-not $padre) { $padre = "\" }
            $servicio.GetFolder($padre).DeleteFolder($f.Name, 0)
            "Eliminada la carpeta vacia: $vieja"
        }
    } catch [System.IO.FileNotFoundException] {
        # La carpeta ya no existe: nada que limpiar.
    } catch {
        "Aviso: no se pudo borrar la carpeta $vieja ($($_.Exception.Message)). Se puede borrar a mano."
    }
}

$accion = New-ScheduledTaskAction -Execute "$raiz\run_pipeline.bat" -WorkingDirectory $raiz

# Sin RepetitionDuration la repetición es indefinida.
$disparador = New-ScheduledTaskTrigger -Once -At $Inicio -RepetitionInterval (New-TimeSpan -Minutes $Minutos)

# IgnoreNew: si una corrida sigue en curso cuando toca la siguiente (la primera del día
# puede tardar ~25 min), la nueva se salta en vez de correr encima de la otra.
# StartWhenAvailable: si el servidor estaba apagado a la hora, corre apenas pueda.
$ajustes = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

# S4U: corre con o sin sesión iniciada y sin guardar la clave de Windows. No tiene
# credenciales de red, y no las necesita: SQL entra con login SQL y SharePoint con el
# certificado de la app (ambos desde config/.env).
$principal = New-ScheduledTaskPrincipal -UserId $Cuenta -LogonType S4U -RunLevel Limited

Register-ScheduledTask -TaskName $nombre -TaskPath $Carpeta -Force `
    -Description "Pipeline analitica-ontime: SharePoint -> SQL Server (AnaliticaOperativa). Cada $Minutos min desde $($Inicio.ToString('yyyy-MM-dd HH:mm'))." `
    -Action $accion -Trigger $disparador -Settings $ajustes -Principal $principal | Out-Null

$t = Get-ScheduledTask -TaskName $nombre -TaskPath $Carpeta
"Tarea:    $($t.TaskPath)$($t.TaskName) ($($t.State))"
"Cuenta:   $($t.Principal.UserId) [$($t.Principal.LogonType)]"
"Inicio:   $($t.Triggers[0].StartBoundary), cada $($t.Triggers[0].Repetition.Interval)"
"Proxima:  $((Get-ScheduledTaskInfo -TaskName $nombre -TaskPath $Carpeta).NextRunTime)"
