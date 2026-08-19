"""Descubrimiento y confirmacion: que se probo, no solo lo que funciono.

El agujero que esto tapa no lo tapa ninguna estadistica. Benjamini-Hochberg
corrige las pruebas que se REGISTRAN; si se prueban veinte variantes y solo se
valida la mejor, el contador dice 1. Y pasa sin mala fe: alguien eligio que
senales incluir, que horizontes y contra que medir, VIENDO resultados.

Las dos unicas defensas que funcionan son guardar un tramo de historico que no
se mire hasta que la estrategia este escrita, y anotar todos los intentos. Los
tests de aqui vigilan sobre todo que esas dos cosas no se puedan saltar, porque
un procedimiento de este tipo se rompe siempre por el mismo sitio: repetir
hasta que salga.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from stocks_tracker.backtest import experiments as exp


@pytest.fixture
def conn(tmp_path, monkeypatch):
    from stocks_tracker.core import db

    class Stub:
        warehouse_path = tmp_path / "test.duckdb"

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    with db.connect() as c:
        yield c


CORTE = date(2023, 1, 1)


def spec(**kwargs) -> exp.Spec:
    base = {"signal_id": "GOLDEN_CROSS", "scope": "equity_us", "horizon_days": 21}
    return exp.Spec(**{**base, **kwargs})


# ---------------------------------------------------------------------------
# La identidad de una estrategia
# ---------------------------------------------------------------------------
def test_the_same_definition_is_the_same_experiment():
    assert spec().spec_hash == spec().spec_hash


@pytest.mark.parametrize("cambio", [
    {"signal_id": "DEATH_CROSS"},
    {"horizon_days": 63},
    {"scope": "equity_eu"},
    {"cost_bps": 25.0},
    {"benchmark": "SPY"},
    {"universe": "pit"},
    {"params": {"umbral": 30}},
])
def test_changing_anything_makes_it_a_different_experiment(cambio):
    """La regla que impide ir retocando hasta que el tramo reservado tambien
    salga bien. Si cambiar el coste o el horizonte conservara la identidad, se
    podria agotar el tramo reservado a base de "ajustes" sin que el contador de
    intentos se moviera."""
    assert spec(**cambio).spec_hash != spec().spec_hash


# ---------------------------------------------------------------------------
# La escalera
# ---------------------------------------------------------------------------
def test_discovery_cannot_reach_confirmed_however_good_the_number_is():
    """El tope que hace que la escalera signifique algo: por espectacular que
    salga, sale sobre los mismos datos con los que se eligio la senal."""
    estado = exp.peldano(hay_datos=True, significativa=True, estable=True,
                         fase=exp.DESCUBRIMIENTO)
    assert estado == exp.ESTABLE


def test_the_ladder_does_not_skip_steps():
    assert exp.peldano(hay_datos=False, significativa=True, estable=True,
                       fase=exp.DESCUBRIMIENTO) == exp.SIN_DATOS
    assert exp.peldano(hay_datos=True, significativa=False, estable=True,
                       fase=exp.DESCUBRIMIENTO) == exp.DESCUBIERTA
    assert exp.peldano(hay_datos=True, significativa=True, estable=False,
                       fase=exp.DESCUBRIMIENTO) == exp.SIGNIFICATIVA


def test_confirmation_has_only_two_outcomes():
    """Ni una senal que falla vuelve a peldanos de abajo ni una que acierta se
    queda a medias. Gastar el tramo reservado tiene que costar algo."""
    assert exp.peldano(hay_datos=True, significativa=True, estable=True,
                       fase=exp.CONFIRMACION,
                       repite_fuera_de_muestra=True) == exp.CONFIRMADA
    assert exp.peldano(hay_datos=True, significativa=True, estable=True,
                       fase=exp.CONFIRMACION,
                       repite_fuera_de_muestra=False) == exp.REFUTADA


def test_a_failed_confirmation_does_not_fall_back_to_discovered():
    """EL PUNTO CENTRAL. Si al fallar volviera a "descubierta", el intento no
    dejaria cicatriz y podria repetirse manana como si fuera la primera vez."""
    estado = exp.peldano(hay_datos=True, significativa=False, estable=False,
                         fase=exp.CONFIRMACION, repite_fuera_de_muestra=False)
    assert estado == exp.REFUTADA
    assert estado != exp.DESCUBIERTA


def test_refuted_does_not_count_as_reaching_any_step():
    """`alcanza` decide si una senal sirve. Si `refutada` contara como un
    peldano cualquiera, una senal desmentida fuera de muestra podria colarse
    donde se pida un minimo bajo."""
    assert not exp.alcanza(exp.REFUTADA, exp.DESCUBIERTA)
    assert not exp.alcanza(exp.REFUTADA, exp.CONFIRMADA)
    assert exp.alcanza(exp.CONFIRMADA, exp.ESTABLE)
    assert not exp.alcanza(exp.ESTABLE, exp.CONFIRMADA)


def test_refuted_is_not_a_rung_of_the_ladder():
    """Lo que impide que `refutada` cuente es que no esta en `ORDEN`, no una
    comprobacion aparte. Anadirla ahi la convertiria en un peldano y una senal
    desmentida fuera de muestra pasaria cualquier minimo bajo.

    Se fija aqui porque es una propiedad de la estructura de datos, y esas se
    rompen sin querer al "ordenar" una lista de constantes.
    """
    assert exp.REFUTADA not in exp.ORDEN
    assert exp.ORDEN.index(exp.CONFIRMADA) == len(exp.ORDEN) - 1
    assert list(exp.ORDEN) == sorted(
        exp.ORDEN, key=lambda e: (exp.SIN_DATOS, exp.DESCUBIERTA,
                                  exp.SIGNIFICATIVA, exp.ESTABLE,
                                  exp.CONFIRMADA).index(e)
    ), "los peldanos tienen que ir de menos a mas exigente"


# ---------------------------------------------------------------------------
# Congelar
# ---------------------------------------------------------------------------
def test_freezing_twice_keeps_the_original_date(conn):
    """Si volver a congelar moviera la fecha, bastaria con recongelar para
    borrar el rastro de cuando se decidio, que es el dato que hace util la
    congelacion."""
    primera = exp.congelar(conn, spec())
    segunda = exp.congelar(conn, spec())
    assert primera == segunda


def test_confirmation_is_refused_without_a_freeze(conn):
    """Si se pudiera mirar el tramo reservado y DESPUES decidir la
    especificacion, ese tramo deja de estar fuera de muestra en el mismo
    momento en que se mira."""
    with pytest.raises(exp.ContaminacionError, match="no esta congelada"):
        exp.comprobar_confirmacion(conn, spec())


def test_confirmation_is_allowed_after_freezing(conn):
    """La contraprueba: si nunca dejara pasar, el mecanismo entero seria un
    muro y no un procedimiento."""
    exp.congelar(conn, spec())
    assert exp.comprobar_confirmacion(conn, spec()) is not None


def test_a_refuted_specification_cannot_try_again(conn):
    """Repetir hasta que salga es exactamente lo que esto viene a impedir."""
    exp.congelar(conn, spec())
    exp.registrar(conn, spec(), fase=exp.CONFIRMACION, estado=exp.REFUTADA,
                  split_at=CORTE)
    with pytest.raises(exp.ContaminacionError, match="ya fallo la confirmacion"):
        exp.comprobar_confirmacion(conn, spec())


def test_a_different_specification_may_try_after_a_refutation(conn):
    """Investigar esta permitido; lo que no esta permitido es que no se note.
    Cambiar algo produce otro experimento, y el contador de intentos sube."""
    exp.congelar(conn, spec())
    exp.registrar(conn, spec(), fase=exp.CONFIRMACION, estado=exp.REFUTADA,
                  split_at=CORTE)
    otra = spec(horizon_days=63)
    exp.congelar(conn, otra)
    assert exp.comprobar_confirmacion(conn, otra) is not None


# ---------------------------------------------------------------------------
# El contador de intentos
# ---------------------------------------------------------------------------
def test_every_experiment_is_recorded_even_the_useless_ones(conn):
    """Un registro que solo guarda los que funcionaron es un album de aciertos,
    y para lo unico que existe es para contar cuantas veces se miro."""
    for estado in (exp.SIN_DATOS, exp.DESCUBIERTA, exp.ESTABLE):
        exp.registrar(conn, spec(params={"v": estado}), fase=exp.DESCUBRIMIENTO,
                      estado=estado, split_at=CORTE)
    assert len(exp.historial(conn)) == 3


def test_the_attempt_counter_counts_distinct_specifications(conn):
    """Repetir el mismo experimento no es mirar otra vez: es la misma mirada.
    Cambiar el horizonte o el coste SI lo es."""
    for _ in range(3):
        exp.registrar(conn, spec(), fase=exp.DESCUBRIMIENTO,
                      estado=exp.DESCUBIERTA, split_at=CORTE)
    assert exp.intentos(conn, "GOLDEN_CROSS", "equity_us") == 1

    exp.registrar(conn, spec(horizon_days=63), fase=exp.DESCUBRIMIENTO,
                  estado=exp.DESCUBIERTA, split_at=CORTE)
    exp.registrar(conn, spec(cost_bps=25.0), fase=exp.DESCUBRIMIENTO,
                  estado=exp.DESCUBIERTA, split_at=CORTE)
    assert exp.intentos(conn, "GOLDEN_CROSS", "equity_us") == 3


def test_the_counter_does_not_mix_signals_or_scopes(conn):
    exp.registrar(conn, spec(), fase=exp.DESCUBRIMIENTO, estado=exp.DESCUBIERTA,
                  split_at=CORTE)
    exp.registrar(conn, spec(signal_id="DEATH_CROSS"), fase=exp.DESCUBRIMIENTO,
                  estado=exp.DESCUBIERTA, split_at=CORTE)
    exp.registrar(conn, spec(scope="crypto"), fase=exp.DESCUBRIMIENTO,
                  estado=exp.DESCUBIERTA, split_at=CORTE)
    assert exp.intentos(conn, "GOLDEN_CROSS", "equity_us") == 1


# ---------------------------------------------------------------------------
# Quien puede gastar muestra reservada
# ---------------------------------------------------------------------------
def test_only_stable_signals_become_candidates(conn):
    """Llevar al tramo reservado una senal que ni siquiera fue significativa
    gastaria muestra —que no se repone— para contestar algo que ya tenia
    respuesta."""
    exp.registrar(conn, spec(), fase=exp.DESCUBRIMIENTO, estado=exp.ESTABLE,
                  split_at=CORTE)
    exp.registrar(conn, spec(horizon_days=63), fase=exp.DESCUBRIMIENTO,
                  estado=exp.SIGNIFICATIVA, split_at=CORTE)
    exp.registrar(conn, spec(horizon_days=5), fase=exp.DESCUBRIMIENTO,
                  estado=exp.DESCUBIERTA, split_at=CORTE)

    elegibles = exp.candidatas(conn, "equity_us")
    assert elegibles == {spec().spec_hash}


def test_reaching_stable_in_confirmation_does_not_make_a_candidate(conn):
    """La candidatura sale del DESCUBRIMIENTO. Si un resultado de confirmacion
    pudiera crear candidatos, el tramo reservado se realimentaria a si mismo."""
    exp.registrar(conn, spec(), fase=exp.CONFIRMACION, estado=exp.ESTABLE,
                  split_at=CORTE)
    assert exp.candidatas(conn, "equity_us") == set()


# ---------------------------------------------------------------------------
# La frontera
# ---------------------------------------------------------------------------
def test_the_split_is_a_fixed_date_and_not_a_fraction():
    """Con una fraccion del historico, la frontera se desplazaria sola segun
    entran datos: el tramo reservado hoy formaria parte del descubrimiento el
    mes que viene, sin que nadie se entere."""
    import pandas as pd

    from stocks_tracker.backtest.run_backtest import frontera
    assert isinstance(frontera(), pd.Timestamp)
    assert frontera() == frontera()


def test_discovery_and_confirmation_do_not_share_a_single_row():
    """La propiedad que sostiene todo lo demas. Un solo dia compartido y el
    tramo reservado deja de serlo."""
    import pandas as pd

    from stocks_tracker.backtest.run_backtest import recortar

    fechas = pd.bdate_range("2020-01-01", periods=1500)
    precios = pd.DataFrame({"date": fechas, "ticker": "AAA", "adj_close": 100.0})
    senales = pd.DataFrame({"date": fechas, "ticker": "AAA", "signal_id": "S"})
    corte = pd.Timestamp("2023-01-01")

    p_desc, s_desc = recortar(precios, senales, exp.DESCUBRIMIENTO, corte)
    p_conf, s_conf = recortar(precios, senales, exp.CONFIRMACION, corte)

    assert set(p_desc["date"]) & set(p_conf["date"]) == set()
    assert set(s_desc["date"]) & set(s_conf["date"]) == set()
    assert len(p_desc) + len(p_conf) == len(precios), "no se puede perder ninguna"
    assert p_desc["date"].max() < corte <= p_conf["date"].min()


def test_discovery_prices_stop_at_the_split_too():
    """Los precios se recortan igual que las senales. Si en descubrimiento se
    dejaran completos, `forward_returns` mediria el resultado de una senal de
    diciembre de 2022 con precios de 2023: o sea, con el tramo reservado."""
    import pandas as pd

    from stocks_tracker.backtest.run_backtest import recortar

    fechas = pd.bdate_range("2022-11-01", periods=120)
    precios = pd.DataFrame({"date": fechas, "ticker": "AAA", "adj_close": 100.0})
    corte = pd.Timestamp("2023-01-01")
    p, _ = recortar(precios, pd.DataFrame(columns=["date"]),
                    exp.DESCUBRIMIENTO, corte)
    assert p["date"].max() < corte


# ---------------------------------------------------------------------------
# Lo que se guarda
# ---------------------------------------------------------------------------
def test_the_record_carries_what_it_takes_to_reproduce_it(conn):
    exp.registrar(conn, spec(), fase=exp.DESCUBRIMIENTO, estado=exp.ESTABLE,
                  split_at=CORTE, data_from=date(2016, 1, 1),
                  data_to=date(2022, 12, 31), n_obs=500, n_dates=200,
                  avg_excess=0.01, t_stat=3.1, p_value=0.002, q_value=0.02,
                  motivo="exceso positivo y estable")
    fila = conn.execute(
        "SELECT signal_id, horizon_days, cost_bps, split_at, n_obs, q_value, "
        "estado, motivo FROM experiments"
    ).fetchone()
    assert fila == ("GOLDEN_CROSS", 21, 10.0, CORTE, 500, 0.02, exp.ESTABLE,
                    "exceso positivo y estable")


def test_the_freeze_stores_what_exactly_was_frozen(conn):
    """Sin el JSON congelado no se puede saber que se fijo, solo que se fijo
    algo. Y entonces la congelacion no se puede auditar."""
    exp.congelar(conn, spec(cost_bps=12.5), nota="prueba")
    guardado = conn.execute("SELECT spec, note FROM strategy_freezes").fetchone()
    assert '"cost_bps": 12.5' in guardado[0]
    assert guardado[1] == "prueba"


def test_the_freeze_records_which_code_froze_it(conn):
    exp.congelar(conn, spec())
    commit = conn.execute("SELECT git_commit FROM strategy_freezes").fetchone()[0]
    assert commit and isinstance(commit, str)


def test_freezing_returns_a_timestamp(conn):
    assert isinstance(exp.congelar(conn, spec()), datetime)
