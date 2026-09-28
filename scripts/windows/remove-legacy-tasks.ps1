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
    Write-Error "No se ha completado la retirada de tareas: $($_.Exception.Message)"
    exit 1
}
