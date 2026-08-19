"""Composicion de los universos con fechas: el sesgo de supervivencia.

El fallo que estos tests fijan ya estaba en produccion y era del tipo peor: la
tabla que existia para EVITAR el sesgo de supervivencia lo estaba produciendo.

`valid_to` no se rellenaba nunca. Cada ingesta insertaba una fila con
`valid_from = hoy` y `valid_to = NULL`, asi que despues de tres dias habia tres
intervalos abiertos del mismo ticker. Y como todos los consumidores preguntan
por "los miembros de hoy" con `WHERE valid_to IS NULL`, esa consulta devolvia
todos los tickers que habian estado alguna vez, incluido el que salio del
indice hace meses.

Los tests de aqui prueban las tres transiciones —entra, sigue, sale— y sobre
todo la del medio, que es la que estaba mal: seguir NO puede escribir nada.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from stocks_tracker.core import membership as m


@pytest.fixture
def conn(tmp_path, monkeypatch):
    from stocks_tracker.core import db

    class Stub:
        warehouse_path = tmp_path / "test.duckdb"

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    with db.connect() as c:
        yield c


LUNES = date(2024, 1, 1)
MARTES = date(2024, 1, 2)
MIERCOLES = date(2024, 1, 3)


def _filas(conn, universo="SP100"):
    return conn.execute(
        "SELECT ticker, valid_from, valid_to FROM universe_membership "
        "WHERE universe = ? ORDER BY ticker, valid_from", [universo],
    ).fetchall()


# ---------------------------------------------------------------------------
# Las tres transiciones
# ---------------------------------------------------------------------------
def test_a_new_member_opens_an_interval(conn):
    m.actualizar(conn, "SP100", ["AAA", "BBB"], LUNES)
    assert _filas(conn) == [("AAA", LUNES, None), ("BBB", LUNES, None)]


def test_a_continuing_member_writes_nothing_at_all(conn):
    """EL FALLO QUE HABIA. Insertar una fila cada dia convertia la tabla en un
    registro diario, hacia que `valid_from` significara "el dia que se miro" en
    vez de "el dia que entro", y dejaba varios intervalos abiertos del mismo
    ticker a la vez."""
    m.actualizar(conn, "SP100", ["AAA"], LUNES)
    m.actualizar(conn, "SP100", ["AAA"], MARTES)
    m.actualizar(conn, "SP100", ["AAA"], MIERCOLES)
    assert _filas(conn) == [("AAA", LUNES, None)], \
        "tres dias en el indice son UN intervalo, no tres"


def test_a_departing_member_gets_its_interval_closed(conn):
    m.actualizar(conn, "SP100", ["AAA", "BBB"], LUNES)
    m.actualizar(conn, "SP100", ["AAA"], MARTES)
    assert _filas(conn) == [("AAA", LUNES, None), ("BBB", LUNES, MARTES)]


def test_the_departure_is_reported_so_it_can_be_seen(conn):
    """Que un valor salga del S&P 500 es una noticia. Si el cambio no se dijera
    en ninguna parte, el universo cambiaria bajo los pies sin que nadie lo
    supiera."""
    m.actualizar(conn, "SP100", ["AAA", "BBB"], LUNES)
    cambios = m.actualizar(conn, "SP100", ["AAA", "CCC"], MARTES)
    assert cambios["salen"] == ["BBB"]
    assert cambios["entran"] == ["CCC"]
    assert cambios["siguen"] == 1


def test_a_member_that_comes_back_gets_a_second_interval(conn):
    """Salir y volver son dos periodos distintos, no uno largo. Fusionarlos
    diria que estuvo en el indice un tiempo en el que no estuvo."""
    m.actualizar(conn, "SP100", ["AAA"], LUNES)
    m.actualizar(conn, "SP100", [], MARTES)
    m.actualizar(conn, "SP100", ["AAA"], MIERCOLES)
    assert _filas(conn) == [("AAA", LUNES, MARTES), ("AAA", MIERCOLES, None)]


def test_running_twice_the_same_day_does_not_break(conn):
    """La ingesta se puede lanzar dos veces el mismo dia. Sin proteccion, el
    segundo INSERT violaria la clave primaria y tumbaria la ingesta entera."""
    m.actualizar(conn, "SP100", ["AAA"], LUNES)
    m.actualizar(conn, "SP100", ["AAA", "BBB"], LUNES)
    m.actualizar(conn, "SP100", ["AAA", "BBB"], LUNES)
    assert _filas(conn) == [("AAA", LUNES, None), ("BBB", LUNES, None)]


def test_universes_do_not_interfere_with_each_other(conn):
    """Un ticker puede estar en el S&P 100 y no en el Nasdaq 100. Si el cierre
    no filtrara por universo, actualizar uno cerraria los intervalos del otro."""
    m.actualizar(conn, "SP100", ["AAA", "BBB"], LUNES)
    m.actualizar(conn, "NASDAQ100", ["AAA"], LUNES)
    m.actualizar(conn, "NASDAQ100", [], MARTES)
    assert _filas(conn, "SP100") == [("AAA", LUNES, None), ("BBB", LUNES, None)]
    assert _filas(conn, "NASDAQ100") == [("AAA", LUNES, MARTES)]


# ---------------------------------------------------------------------------
# La consulta con fecha
# ---------------------------------------------------------------------------
def test_asking_for_a_past_date_gives_the_members_of_that_date(conn):
    """El objetivo de todo el modulo: puntuar el pasado con quien estaba
    entonces, no con quien esta hoy."""
    m.actualizar(conn, "SP100", ["AAA", "BBB"], LUNES)
    m.actualizar(conn, "SP100", ["AAA", "CCC"], MIERCOLES)
    assert m.miembros_en(conn, LUNES) == {"AAA", "BBB"}
    assert m.miembros_en(conn, MIERCOLES) == {"AAA", "CCC"}


def test_the_day_a_member_leaves_it_is_already_out(conn):
    """`valid_to > fecha` y no `>=`: el intervalo se cierra el dia en que se
    detecta la salida, asi que ese mismo dia ya no forma parte."""
    m.actualizar(conn, "SP100", ["AAA", "BBB"], LUNES)
    m.actualizar(conn, "SP100", ["AAA"], MARTES)
    assert "BBB" in m.miembros_en(conn, LUNES)
    assert "BBB" not in m.miembros_en(conn, MARTES)


def test_a_date_before_everything_has_no_members(conn):
    """Y es la limitacion honesta del modulo: antes de la primera ingesta no
    hay composicion, y filtrar por ella dejaria el universo vacio."""
    m.actualizar(conn, "SP100", ["AAA"], LUNES)
    assert m.miembros_en(conn, LUNES - timedelta(days=1)) == set()


def test_you_can_ask_for_one_universe_only(conn):
    m.actualizar(conn, "SP100", ["AAA"], LUNES)
    m.actualizar(conn, "IBEX35", ["ZZZ"], LUNES)
    assert m.miembros_en(conn, LUNES, ["IBEX35"]) == {"ZZZ"}
    assert m.miembros_en(conn, LUNES) == {"AAA", "ZZZ"}


# ---------------------------------------------------------------------------
# Reparar lo que dejo el fallo
# ---------------------------------------------------------------------------
def test_compacting_collapses_the_daily_duplicates(conn):
    """Reproduce el estado que dejo el fallo —una fila por ticker y por dia,
    todas abiertas— y comprueba que se colapsa conservando la fecha de entrada
    correcta, que es la mas antigua."""
    for dia in (LUNES, MARTES, MIERCOLES):
        conn.execute("INSERT INTO universe_membership VALUES (?, ?, ?, NULL)",
                     ["SP100", "AAA", dia])
    assert m.compactar(conn) == 2
    assert _filas(conn) == [("AAA", LUNES, None)]


def test_compacting_is_idempotent(conn):
    """Corre en cada ingesta. Si borrara algo la segunda vez, cada ejecucion
    iria comiendose el historico."""
    for dia in (LUNES, MARTES):
        conn.execute("INSERT INTO universe_membership VALUES (?, ?, ?, NULL)",
                     ["SP100", "AAA", dia])
    m.compactar(conn)
    assert m.compactar(conn) == 0
    assert _filas(conn) == [("AAA", LUNES, None)]


def test_compacting_does_not_touch_closed_intervals(conn):
    """Un intervalo cerrado es historia de verdad y no se toca. Si se borrara,
    compactar destruiria justo lo que la tabla existe para guardar."""
    conn.execute("INSERT INTO universe_membership VALUES ('SP100','AAA',?,?)",
                 [LUNES, MARTES])
    conn.execute("INSERT INTO universe_membership VALUES ('SP100','AAA',?,NULL)",
                 [MIERCOLES])
    assert m.compactar(conn) == 0
    assert len(_filas(conn)) == 2


def test_compacting_corrupt_data_does_not_destroy_the_history(conn):
    """Compactar es una funcion de REPARACION, asi que se le pasan datos rotos
    por definicion. Lo unico que no puede hacer es empeorarlos.

    Aqui hay un solapamiento imposible: un intervalo abierto desde el lunes y
    otro cerrado que empieza el miercoles, cuando un intervalo abierto siempre
    deberia ser el ultimo. Con datos sanos este caso no ocurre —y por eso la
    guarda `valid_to IS NULL` parece de adorno—, pero es exactamente el tipo de
    estado que deja un fallo, y borrar el tramo cerrado seria perder historia
    real para siempre.
    """
    conn.execute("INSERT INTO universe_membership VALUES ('SP100','AAA',?,NULL)",
                 [LUNES])
    conn.execute("INSERT INTO universe_membership VALUES ('SP100','AAA',?,?)",
                 [MIERCOLES, MIERCOLES + timedelta(days=1)])
    m.compactar(conn)
    cerrados = [f for f in _filas(conn) if f[2] is not None]
    assert cerrados, "compactar se ha llevado por delante un intervalo cerrado"


# ---------------------------------------------------------------------------
# Decir la verdad sobre lo que se tiene
# ---------------------------------------------------------------------------
def test_the_coverage_counts_the_real_history(conn):
    m.actualizar(conn, "SP100", ["AAA"], LUNES)
    m.actualizar(conn, "SP100", ["AAA", "BBB"], MIERCOLES)
    cobertura = m.cobertura(conn)
    assert cobertura.iloc[0]["tickers"] == 2
    # `fetchdf` devuelve las fechas como Timestamp, no como date.
    assert cobertura.iloc[0]["desde"].date() == LUNES


def test_an_empty_table_reports_zero_years_and_does_not_crash(conn):
    assert m.anos_de_composicion(conn) == 0.0


def test_the_years_of_history_are_measured_and_not_guessed(conn):
    """El numero que la pantalla usa para decir la verdad sobre el sesgo. Si
    saliera de cualquier sitio menos de los datos, la frase honesta se
    convertiria en otra promesa."""
    m.actualizar(conn, "SP100", ["AAA"], date(2020, 1, 1))
    m.actualizar(conn, "SP100", [], date(2023, 1, 1))
    assert m.anos_de_composicion(conn) == pytest.approx(3.0, abs=0.01)


def test_a_single_day_of_history_is_almost_zero_years(conn):
    """Y no un ano por tener una fecha. Con 0,00 en pantalla queda claro que no
    hay composicion historica; con 1,00 pareceria que si."""
    m.actualizar(conn, "SP100", ["AAA"], LUNES)
    assert m.anos_de_composicion(conn) < 0.01


def test_the_coverage_does_not_grow_by_itself_without_ingesting(conn):
    """Un intervalo abierto significa "seguia dentro la ultima vez que se
    miro", no "sigue dentro hoy". Contando hasta la fecha de hoy, la cobertura
    creceria sola sin descargar nada: el numero que existe para no enganarse
    engordaria solo por dejar pasar el tiempo, que es la peor forma posible de
    que un dato honesto deje de serlo.

    El registro dice que la ultima comprobacion fue en 2020; la composicion
    cubre hasta ahi, no hasta hoy.
    """
    m.actualizar(conn, "SP100", ["AAA"], date(2020, 1, 1))
    conn.execute(
        "INSERT INTO ingest_log VALUES (?, ?, ?, 'universe', 'all', 'OK', 1, 0, '')",
        ["run-1", date(2020, 1, 1), date(2020, 1, 1)],
    )
    assert m.anos_de_composicion(conn) == pytest.approx(0.0, abs=0.01)


def test_the_coverage_reaches_the_last_time_the_universe_was_checked(conn):
    """Y la contraprueba: si la ingesta SI se ha ejecutado, esos anos cuentan
    aunque la composicion no haya cambiado en todo ese tiempo. Que nada cambie
    es informacion, no ausencia de informacion."""
    m.actualizar(conn, "SP100", ["AAA"], date(2020, 1, 1))
    conn.execute(
        "INSERT INTO ingest_log VALUES (?, ?, ?, 'universe', 'all', 'OK', 1, 0, '')",
        ["run-1", date(2024, 1, 1), date(2024, 1, 1)],
    )
    assert m.anos_de_composicion(conn) == pytest.approx(4.0, abs=0.01)


def test_the_coverage_is_never_negative(conn):
    """Puede pasar: un registro de ingesta antiguo junto a filas de composicion
    mas nuevas —por ejemplo tras reparar la tabla a mano—. Sin la proteccion,
    la pantalla mostraria "-2,3 anos de composicion real", que es un disparate
    visible y hace desconfiar de todo lo demas de la pagina."""
    m.actualizar(conn, "SP100", ["AAA"], date(2024, 1, 1))
    conn.execute(
        "INSERT INTO ingest_log VALUES (?, ?, ?, 'universe', 'all', 'OK', 1, 0, '')",
        ["run-viejo", date(2020, 1, 1), date(2020, 1, 1)],
    )
    assert m.anos_de_composicion(conn) == 0.0


def test_a_failed_ingest_does_not_count_as_a_check(conn):
    """Si la descarga fallo, no se comprobo nada. Contarla haria que un fallo
    de red aumentara la cobertura declarada."""
    m.actualizar(conn, "SP100", ["AAA"], date(2020, 1, 1))
    conn.execute(
        "INSERT INTO ingest_log VALUES (?, ?, ?, 'universe', 'all', 'FAILED', 0, 0, 'red')",
        ["run-1", date(2024, 1, 1), date(2024, 1, 1)],
    )
    assert m.anos_de_composicion(conn) == pytest.approx(0.0, abs=0.01)


def test_the_warning_without_history_does_not_promise_a_correction(conn):
    """Sin composicion guardada, la unica frase honesta es que el sesgo esta y
    no se sabe cuanto. Decir "se ira corrigiendo" invita a confiar en un numero
    que hoy no vale mas que ayer."""
    texto = m.aviso_de_supervivencia(0.0, 10.0)
    assert "sesgados al alza" in texto
    assert "no se sabe cuanto" in texto


def test_the_warning_with_partial_history_says_how_much_is_missing(conn):
    texto = m.aviso_de_supervivencia(0.03, 10.0)
    assert "0.0 anos" in texto or "0.03" in texto
    assert "10.0" in texto
    assert "sigue sesgada" in texto


def test_the_warning_with_full_history_still_does_not_claim_it_is_fixed():
    """Aunque la composicion cubra el periodo, los precios de las empresas
    desaparecidas no existen. Decir que el sesgo esta resuelto seria vender una
    correccion que no se ha hecho, y eso es peor que la situacion actual, donde
    al menos esta documentado."""
    texto = m.aviso_de_supervivencia(12.0, 10.0)
    assert "no desaparece" in texto
    assert "desaparecidas no estan disponibles" in texto


# ---------------------------------------------------------------------------
# Una lista de respaldo no puede dar de baja a nadie
# ---------------------------------------------------------------------------
def test_an_unreliable_list_never_closes_intervals(conn):
    """EL FALLO MAS GRAVE DE LA REVISION. Cuando la descarga de Wikipedia
    falla, `resolve_universe` devuelve la lista MANUAL de respaldo: unos 50
    tickers frente a los 450 reales. Sin proteccion, los 400 que faltan se leen
    como salidas del indice y se cierran sus intervalos.

    Un 403 de un minuto escribiria una baja masiva falsa y PERMANENTE en la
    unica tabla que guarda historia que no se puede reconstruir. Y no es
    hipotetico: es exactamente lo que pasa cuando falta lxml, que es lo que
    tuvo el CI en rojo una semana.
    """
    reales = [f"T{i:03d}" for i in range(450)]
    respaldo = reales[:50]

    m.actualizar(conn, "SP500", reales, LUNES)
    cambios = m.actualizar(conn, "SP500", respaldo, MARTES, fiable=False)

    assert cambios["salen"] == []
    assert len(cambios["sin_confirmar"]) == 400
    assert len(m.miembros_en(conn, MARTES)) == 450, \
        "la lista de respaldo ha dado de baja a 400 valores"


def test_an_unreliable_list_can_still_add_what_it_knows(conn):
    """Abrir si es seguro: el respaldo es un subconjunto curado de miembros de
    verdad. Asi la tabla solo puede ganar verdad, nunca perderla."""
    m.actualizar(conn, "SP500", ["AAA"], LUNES)
    m.actualizar(conn, "SP500", ["AAA", "NUEVA"], MARTES, fiable=False)
    assert m.miembros_en(conn, MARTES) == {"AAA", "NUEVA"}


def test_the_next_good_download_still_records_the_real_departure(conn):
    """El error se corrige solo, que es lo que no pasaba al reves: una baja
    falsa escrita en la tabla no la deshace ninguna descarga posterior."""
    m.actualizar(conn, "SP500", ["AAA", "BBB"], LUNES)
    m.actualizar(conn, "SP500", ["AAA"], MARTES, fiable=False)      # fallo
    cambios = m.actualizar(conn, "SP500", ["AAA"], MIERCOLES)       # ya va bien
    assert cambios["salen"] == ["BBB"]
    assert m.miembros_en(conn, MIERCOLES) == {"AAA"}


def test_a_reliable_list_is_the_default(conn):
    """Los universos manuales por configuracion —ETF_CORE, INDICES— tienen la
    lista buena. Si el defecto fuera "no fiable", no se cerraria nunca nada."""
    m.actualizar(conn, "ETF_CORE", ["AAA", "BBB"], LUNES)
    cambios = m.actualizar(conn, "ETF_CORE", ["AAA"], MARTES)
    assert cambios["salen"] == ["BBB"]
