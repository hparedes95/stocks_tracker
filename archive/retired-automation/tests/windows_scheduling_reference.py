"""Requisitos históricos retirados. No se ejecutan."""

def test_the_installer_schedules_everything_without_being_asked():
    """Lo pedido es que funcione sin tocar nada: si el instalador no programa
    las tareas, el usuario tiene que acordarse de ejecutarlas."""
    src = text("installer/install.ps1")
    assert "stocks.ps1') autostart" in src or "autostart" in src, (
        "el instalador no programa nada"
    )



def test_the_schedule_is_defined_in_exactly_one_place():
    """El instalador duplicaba la logica de programacion, y paso lo que pasa
    siempre con una copia: al anadir el ciclo del bot en `autostart`, el
    instalador siguio registrando solo la tarea de datos. Quien instalaba desde
    cero se quedaba sin bot y sin ninguna senal de que faltaba algo."""
    instalador = text("installer/install.ps1")
    assert "Register-ScheduledTask" not in instalador, (
        "el instalador vuelve a registrar tareas por su cuenta: la definicion "
        "se duplicara y las dos copias divergiran"
    )
    lanzador = text("scripts/windows/stocks.ps1")
    assert lanzador.count("Register-ScheduledTask") == 3, (
        "se esperan exactamente tres: datos, bot y simulacro de backups"
    )



def test_the_scheduled_tasks_survive_a_powered_off_computer():
    """Sin StartWhenAvailable la tarea se pierde cada noche que el equipo este
    apagado, que en un ordenador personal son casi todas."""
    lanzador = text("scripts/windows/stocks.ps1")
    # Se cuenta el FLAG (`-StartWhenAvailable`) y no la palabra suelta: la
    # palabra aparece tambien en un comentario, y contarla daria tres donde
    # hay dos usos reales.
    assert lanzador.count("-StartWhenAvailable") == 3, (
        "alguna de las tres tareas se perderia si el equipo esta apagado"
    )



def test_the_bot_runs_more_than_once_a_day():
    """Cripto no cierra. Un stop que solo se mira a las 23:15 no es un stop,
    es una consulta."""
    bloque = bot_schedule_block()
    assert "RepetitionInterval" in bloque, "el bot se programa una sola vez al dia"
    assert "New-TimeSpan -Hours 6" in bloque



def test_the_bot_does_not_collide_with_the_data_update():
    """DuckDB admite un solo escritor. Si el ciclo del bot cayera a la misma
    hora que la ingesta, uno de los dos se quedaria sin poder anotar lo que
    acaba de hacer."""
    src = text("scripts/windows/stocks.ps1")
    assert "-Daily -At '23:15'" in src
    assert "-Once -At '00:20'" in bot_schedule_block(), (
        "el bot arranca en punto y puede chocar con la ingesta"
    )



def test_turning_the_automation_off_stops_the_bot_too():
    """Quitar solo la tarea de datos dejaria el bot operando despues de que el
    usuario creyera haberlo desactivado. Es la peor forma posible de que un
    boton de apagado no apague."""
    src = text("scripts/windows/stocks.ps1")
    off = src[src.index("'autostart-off' {"):src.index("'ingest' {")]
    assert "Stocks Tracker - ciclo del bot" in off, (
        "el bot sigue programado despues de desactivar la automatizacion"
    )
    assert "Stocks Tracker - actualizacion diaria" in off



def test_the_cycle_task_targets_the_crypto_venue():
    """Sin --venue caeria en el bot de acciones: reglas de bolsa, stops de
    2,5x ATR y limite PDT aplicados a cripto."""
    src = text("scripts/windows/stocks.ps1")
    ciclo = src[src.index("    'ciclo' {"):src.index("    'pendientes' {")]
    assert "--venue kraken" in ciclo



def test_the_cycle_does_not_pin_the_mode():
    """El modo sale del mandato. Fijarlo en el script significaria que
    cambiar `mode:` en trading.yaml no hace nada, y el bot programado seguiria
    simulando con aspecto de estar operando."""
    src = text("scripts/windows/stocks.ps1")
    ciclo = src[src.index("    'ciclo' {"):src.index("    'pendientes' {")]
    assert "--mode" not in ciclo, "el ciclo fija el modo e ignora el mandato"



def test_the_automation_warns_that_a_powered_off_computer_does_not_trade():
    """Es la limitacion mas importante de ejecutar esto en un ordenador
    personal, y quien lo activa tiene que saberla en ese momento, no
    descubrirla un lunes."""
    src = text("scripts/windows/stocks.ps1")
    assert "con el ordenador apagado el bot NO opera" in src



def test_the_first_run_also_brings_the_crypto_history():
    """Si hubiera que acordarse de un comando aparte, el bot de cripto se
    quedaria sin datos y sin decir por que: el ranking saldria vacio con todo
    lo demas descargado."""
    assert "ingest_crypto" in task_block("universo"), (
        "la instalacion completa no trae el historico de cripto"
    )



def test_every_launch_keeps_the_crypto_history_fresh():
    """El lanzador llama a `update` en cada arranque. Sin cripto ahi, el
    universo de acciones se mantendria al dia y el de cripto se quedaria
    congelado en la fecha de instalacion, con el bot decidiendo sobre precios
    viejos."""
    assert "ingest_crypto" in task_block("update")



def test_the_nightly_run_covers_both_universes():
    assert "ingest_crypto" in task_block("daily")



def test_both_gates_run_by_themselves():
    """El veredicto sobre si un mercado puede operar tiene que existir aunque
    nadie abra una consola nunca. Es lo que se lee en el dashboard."""
    daily = task_block("daily")
    assert "--venue kraken --gate" in daily, "la puerta de cripto no se ejecuta sola"
    assert "polymarket_calibration" in daily, "el estudio de Polymarket no se ejecuta solo"



def test_a_failing_study_does_not_stop_the_rest():
    """Un suspenso es un resultado, y que Polymarket no responda un domingo no
    puede dejar sin ejecutar lo demas."""
    daily = task_block("daily")
    trozo = daily[daily.index("--venue kraken --gate"):]
    assert "catch" in trozo, "un fallo de la puerta de cripto detiene el ciclo"
