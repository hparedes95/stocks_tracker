param([switch]$Elevated)

# Migracion idempotente: solo las tareas registradas por Stocks Tracker.
$ErrorActionPreference = 'Stop'
$names = @('Stocks Tracker - actualizacion diaria',
           'Stocks Tracker - ciclo del bot',
           'Stocks Tracker - prueba de backups')
if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) {
    Write-Error 'No se puede comprobar el Programador de tareas. No se confirma la retirada.'
    exit 1
}
try {
    $tasks = @(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop | Where-Object {
        $_.TaskPath -eq '\' -and $_.TaskName -in $names
    })
    foreach ($task in $tasks) {
        $ours = @($task.Actions | Where-Object {
            $_.Arguments -match 'stocks\.ps1' -or
            $_.Arguments -match 'stocks_tracker\.'
        })
        if ($ours.Count -eq 0) {
            throw "La tarea '$($task.TaskName)' no tiene una accion reconocida; revisala manualmente."
        }
        Stop-ScheduledTask -InputObject $task -ErrorAction Stop
        Unregister-ScheduledTask -InputObject $task -Confirm:$false -ErrorAction Stop
        Write-Host "Retirada: $($task.TaskName)"
    }
    exit 0
} catch {
    $motivo = $_.Exception.Message
    $sinPermiso = ($_.Exception.HResult -eq -2147024891 -or
                   $motivo -match '(?i)acceso denegado|access (is )?denied')
    if ($sinPermiso -and -not $Elevated) {
        Write-Host "Windows necesita permiso de administrador para retirar tareas antiguas."
        Write-Host "Acepta la ventana de Control de cuentas de usuario." -ForegroundColor Yellow
        try {
            $argumentos = @(
                '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', ('"{0}"' -f $PSCommandPath), '-Elevated'
            )
            $proceso = Start-Process -FilePath 'powershell.exe' `
                -ArgumentList $argumentos -Verb RunAs -Wait -PassThru -ErrorAction Stop
            exit $proceso.ExitCode
        } catch {
            Write-Error ("No se concedio el permiso para retirar las tareas antiguas. " +
                         "Vuelve a intentarlo y acepta la ventana de Windows.")
            exit 1
        }
    }
    Write-Error "No se ha completado la retirada de tareas: $motivo"
    exit 1
}
