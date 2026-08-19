"""Cuanto de lo que parece funcionar es solo el numero de veces que miraste.

Estos tests cubren dos fallos distintos, y los dos producen el mismo sintoma
—una senal etiquetada como validada que no vale nada— por caminos que no se
parecen:

1. **Contar mal las observaciones.** Mil eventos en diez dias no son mil
   observaciones. Si se cuentan como tales, el estadistico sale inflado y
   cualquier ruido pasa el corte.
2. **No contar las pruebas.** Con cuarenta y cuatro contrastes al 5 %, dos
   pasan por azar aunque ninguna senal sirva.

Ninguno de los dos da error ni sale en pantalla. El unico rastro es un
backtest que parece bueno, que es el resultado mas facil de creerse.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stocks_tracker.backtest import engine as eng
from stocks_tracker.backtest import metrics as mx
from stocks_tracker.backtest import multiple_testing as mt


# ---------------------------------------------------------------------------
# Agrupar por fecha
# ---------------------------------------------------------------------------
def _mercado_sin_senal(n_valores: int = 50, n_dias: int = 400, semilla: int = 0):
    """Muchos valores que solo comparten el movimiento del mercado.

    Aqui NO hay senal: el exceso medio es cero por construccion y lo unico que
    comparten los eventos es el dia en que ocurren. Cualquier estadistico que
    declare esto significativo esta contando mal.
    """
    rng = np.random.default_rng(semilla)
    fechas = pd.bdate_range("2020-01-01", periods=n_dias)
    comun = rng.normal(0.0, 0.02, n_dias)
    filas = []
    for i in range(n_valores):
        idio = rng.normal(0.0, 0.005, n_dias)
        filas.append(pd.DataFrame({"ticker": f"T{i:02d}", "date": fechas,
                                   "ret": comun + idio}))
    return pd.concat(filas, ignore_index=True)


def test_events_on_the_same_day_are_not_independent_observations():
    """El hallazgo que motiva todo el modulo.

    Cincuenta valores durante cuatrocientos dias que solo comparten el
    movimiento del mercado. Tratando los 20.000 eventos como independientes, el
    t sale por encima de 4 y la senal se etiquetaria como validada. Agrupando
    por fecha, no llega a 2. La informacion es la misma; lo que cambia es si se
    cuenta bien.
    """
    datos = _mercado_sin_senal()
    ingenuo = abs(mx.hac_t_statistic(datos["ret"].to_numpy()))
    honesto, n_fechas = mx.clustered_hac_t(datos["ret"], datos["date"])

    assert ingenuo > 4.0, "el escenario tiene que enganar al estadistico ingenuo"
    assert abs(honesto) < 2.0
    assert n_fechas == 400


def test_the_effective_sample_size_is_dates_and_not_events():
    datos = _mercado_sin_senal(n_valores=20, n_dias=50)
    _, n_fechas = mx.clustered_hac_t(datos["ret"], datos["date"])
    assert len(datos) == 1000
    assert n_fechas == 50


def test_clustering_keeps_a_real_effect_visible():
    """La correccion no puede consistir en apagarlo todo. Con un efecto de
    verdad —presente todos los dias y no solo en unos pocos— el estadistico
    agrupado tiene que seguir detectandolo."""
    datos = _mercado_sin_senal(n_valores=30, n_dias=300, semilla=7)
    datos["ret"] = datos["ret"] + 0.01          # un punto porcentual cada dia
    t, _ = mx.clustered_hac_t(datos["ret"], datos["date"])
    assert t > 3.0


def test_the_order_of_the_rows_does_not_change_the_verdict():
    """El array llega de la base de datos agrupado por ticker, no por fecha, y
    un HAC sobre ese orden mide retardos que no existen. Al agrupar por fecha
    el orden de entrada deja de importar, que es justo lo que se quiere.

    Se incluye un barajado completo y no solo los dos ordenados: con los datos
    generados ticker a ticker, ordenar por ticker o por fecha deja las fechas
    apareciendo por primera vez en orden cronologico de todas formas, asi que
    esos dos casos no distinguirian una agrupacion que conserva el orden de una
    que no. Barajado si.
    """
    datos = _mercado_sin_senal(n_valores=10, n_dias=120, semilla=3)
    por_ticker = datos.sort_values(["ticker", "date"])
    por_fecha = datos.sort_values(["date", "ticker"])
    barajado = datos.sample(frac=1.0, random_state=11)

    a, _ = mx.clustered_hac_t(por_ticker["ret"], por_ticker["date"])
    b, _ = mx.clustered_hac_t(por_fecha["ret"], por_fecha["date"])
    c, _ = mx.clustered_hac_t(barajado["ret"], barajado["date"])
    assert a == pytest.approx(b)
    assert a == pytest.approx(c)


def test_without_dates_the_metrics_refuse_to_call_anything_significant():
    """Sin fechas no se puede agrupar, y entonces el t no es fiable. Se
    prefiere que eso se manifieste como "no significativo" antes que como un
    numero grande que nadie sabe que esta mal."""
    datos = _mercado_sin_senal(n_valores=30, n_dias=200, semilla=1)
    datos["ret"] = datos["ret"] + 0.02
    resumen = mx.summarize_event(datos["ret"])
    assert resumen.n_dates == 0
    assert not resumen.is_significant


# ---------------------------------------------------------------------------
# Intervalo de confianza
# ---------------------------------------------------------------------------
def test_the_interval_uses_the_critical_value_and_not_one_standard_error():
    """El semiancho es z x error estandar, y z es el valor critico del 95 %.

    Se comprueba con la identidad `semiancho / |centro| x |t| = z`, que fija el
    numero exacto. Comparar solo "el intervalo excluye el cero" con "|t| > 1,96"
    no basta: casi cualquier z da la misma respuesta salvo que el t caiga
    justo en la franja estrecha donde discrepan, y con datos aleatorios eso no
    pasa casi nunca.
    """
    datos = _mercado_sin_senal(n_valores=15, n_dias=150, semilla=4)
    datos["ret"] = datos["ret"] + 0.004
    r = mx.summarize_event(datos["ret"], dates=datos["date"])
    semiancho = (r.ci_high - r.ci_low) / 2
    centro = (r.ci_high + r.ci_low) / 2
    assert semiancho / abs(centro) * abs(r.t_stat) == pytest.approx(1.96, rel=1e-3)


def test_the_interval_and_the_t_statistic_cannot_disagree():
    """Salen del mismo error estandar a proposito. Si cada uno estimase el
    suyo, un intervalo que excluye el cero podria aparecer junto a un t de 1,5
    y no habria forma de saber cual creerse.

    Los desplazamientos estan elegidos para barrer t desde por debajo de 1
    hasta bien por encima de 2, pasando por la franja 1-1,96 donde un z mal
    puesto se delata.
    """
    vistos = []
    for i, desplazamiento in enumerate([0.0, 0.001, 0.002, 0.003, 0.005, 0.008]):
        datos = _mercado_sin_senal(n_valores=15, n_dias=150, semilla=i)
        datos["ret"] = datos["ret"] + desplazamiento
        r = mx.summarize_event(datos["ret"], dates=datos["date"])
        assert r.ci_excludes_zero == (abs(r.t_stat) > 1.96)
        vistos.append(abs(r.t_stat))
    assert any(1.0 < t < 1.96 for t in vistos), "el barrido no cruza la franja"
    assert any(t > 2.0 for t in vistos)


# ---------------------------------------------------------------------------
# Los minimos de muestra
# ---------------------------------------------------------------------------
def _metrics(**kwargs) -> mx.EventMetrics:
    base = dict(
        n_obs=500, hit_rate=0.6, hit_rate_vs_benchmark=0.6, avg_return=0.01,
        median_return=0.01, avg_excess=0.01, std_return=0.05, t_stat=4.0,
        best=0.2, worst=-0.1, benchmark_avg=0.0, n_dates=200, p_value=0.001,
        ci_low=0.005, ci_high=0.015,
    )
    return mx.EventMetrics(**{**base, **kwargs})


def test_a_huge_t_on_too_few_dates_is_not_significant():
    """El caso que motiva que el minimo sea sobre fechas y no sobre eventos:
    mil eventos con un t enorme, repartidos en diez dias. Son diez
    observaciones, y un HAC sobre diez puntos no sostiene nada."""
    assert not _metrics(n_obs=1000, n_dates=10, t_stat=9.0).is_significant


def test_a_huge_t_on_too_few_events_is_not_significant():
    assert not _metrics(n_obs=20, n_dates=200, t_stat=9.0).is_significant


def test_enough_of_both_with_a_big_t_is_significant():
    """La contraprueba: si nada pasara nunca, los dos tests de arriba pasarian
    por el motivo equivocado."""
    assert _metrics(n_obs=500, n_dates=200, t_stat=4.0).is_significant


def test_a_small_t_is_not_significant_however_much_data_there_is():
    assert not _metrics(n_obs=100_000, n_dates=5_000, t_stat=1.2).is_significant


def test_the_sample_minimums_are_not_accidentally_zero():
    """Con los minimos a cero no se aplicarian nunca y estarian de adorno."""
    assert mx.MIN_DATES >= 20
    assert mx.MIN_OBSERVATIONS >= 50


def test_the_interval_is_centred_on_the_average_excess():
    datos = _mercado_sin_senal(n_valores=10, n_dias=120, semilla=2)
    resumen = mx.summarize_event(datos["ret"], dates=datos["date"])
    centro = (resumen.ci_low + resumen.ci_high) / 2
    # El centro es la media POR FECHA, no la media de eventos. Coinciden aqui
    # porque todos los dias tienen el mismo numero de valores.
    assert centro == pytest.approx(resumen.avg_excess, abs=1e-9)


# ---------------------------------------------------------------------------
# Benjamini-Hochberg
# ---------------------------------------------------------------------------
def test_a_single_test_needs_no_correction():
    sobrevive, q = mt.benjamini_hochberg([0.04], q=0.10)
    assert sobrevive[0]
    assert q[0] == pytest.approx(0.04)


def test_the_same_p_value_stops_surviving_when_you_run_more_tests():
    """El corazon del asunto. Un p de 0,04 es una senal validada si es lo unico
    que probaste, y es ruido si es lo mejor de cuarenta intentos. La etiqueta
    de una senal depende de cuantas miraste, y eso no es una incoherencia: es
    lo que significa corregir."""
    solo, _ = mt.benjamini_hochberg([0.04], q=0.10)
    entre_muchas, _ = mt.benjamini_hochberg([0.04] + [0.5] * 39, q=0.10)
    assert solo[0]
    assert not entre_muchas[0]


def test_a_strong_result_survives_being_one_of_many():
    """Y al reves: la correccion no puede tumbarlo todo. Un p de 0,0001 entre
    cuarenta pruebas sigue siendo un hallazgo."""
    sobrevive, _ = mt.benjamini_hochberg([0.0001] + [0.5] * 39, q=0.10)
    assert sobrevive[0]


def test_everything_below_the_cut_survives_even_if_it_fails_its_own_threshold():
    """El error clasico al implementar Benjamini-Hochberg: comprobar cada p
    contra su propio umbral por separado. El procedimiento correcto busca el
    RANGO mas alto que cumple y acepta todo lo que este por debajo, aunque
    alguno de esos no cumpliera su desigualdad individual.

    Con estos cuatro p y q = 0,10, los umbrales son 0,025 / 0,05 / 0,075 / 0,10.
    El rango 1 (p = 0,03) NO cumple el suyo, pero el rango 4 (p = 0,09) si. El
    procedimiento correcto acepta los cuatro; el ingenuo rechaza el p MAS
    PEQUENO de todos mientras acepta los tres mayores, que es un disparate
    visible.
    """
    sobrevive, _ = mt.benjamini_hochberg([0.03, 0.04, 0.05, 0.09], q=0.10)
    assert list(sobrevive) == [True, True, True, True]


def test_the_q_value_never_decreases_as_the_p_value_grows():
    """Sin el minimo acumulado hacia atras, el q de una prueba podria salir
    menor que el de otra con p mas pequeno, y la tabla se leeria al reves.

    Los p estan elegidos para que el q SIN ajustar no sea monotono: el crudo
    sale 0,004 / 0,080 / 0,067 / 0,900, y el tercero es menor que el segundo.
    Con una serie cualquiera esto pasa desapercibido porque el crudo ya suele
    salir creciente.
    """
    p = np.array([0.001, 0.04, 0.05, 0.9])
    _, q = mt.benjamini_hochberg(p, q=0.10)
    assert np.all(np.diff(q) >= -1e-12)
    assert np.all(q <= 1.0)


def test_tests_without_a_p_value_do_not_penalise_the_others():
    """Una senal sin datos no compite por nada. Si contase, anadir una senal
    que no se puede evaluar endureceria el umbral de las demas.

    El p vale 0,05: con una sola prueba pasa (0,05 <= 0,10) y contando las
    cuatro no pasaria (0,05 > 1/4 x 0,10 = 0,025). Con un p mas pequeno el test
    pasaria de las dos formas y no comprobaria nada.
    """
    con_nan, _ = mt.benjamini_hochberg([0.05, np.nan, np.nan, np.nan], q=0.10)
    sin_nan, _ = mt.benjamini_hochberg([0.05], q=0.10)
    assert con_nan[0] == sin_nan[0] is np.True_
    assert not con_nan[1:].any()


def test_an_empty_family_is_not_an_error():
    sobrevive, q = mt.benjamini_hochberg([], q=0.10)
    assert len(sobrevive) == 0 and len(q) == 0


def test_the_expected_number_of_flukes_is_stated_plainly():
    assert mt.expected_false_positives(44, 0.05) == pytest.approx(2.2)
    assert mt.expected_false_positives(0) == 0.0


def test_the_accepted_false_discovery_rate_is_not_a_free_pass():
    """Con q = 1 pasaria todo y la correccion seria decorativa."""
    assert 0.0 < mt.FDR_Q < 0.25


# ---------------------------------------------------------------------------
# El embargo entre ventanas
# ---------------------------------------------------------------------------
def _detalle(n: int = 400) -> pd.DataFrame:
    fechas = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame({"date": fechas, "retorno": 0.01, "referencia": 0.0})


def test_the_embargo_drops_the_tail_of_each_window():
    """Con horizonte 63, los ultimos eventos de una ventana miden un trozo de
    mercado que ya pertenece a la siguiente. Sin quitarlos, "positiva en 2 de 3
    ventanas" no son dos comprobaciones separadas: comparten mercado."""
    detalle = _detalle()
    sin = eng.walk_forward(detalle, n_folds=3, embargo_days=0)
    con = eng.walk_forward(detalle, n_folds=3, embargo_days=eng.embargo_for(63))

    assert con[0].end < sin[0].end
    assert (sin[0].end - con[0].end).days >= 88


def test_the_last_window_is_not_trimmed():
    """Despues de la ultima no hay nada con lo que solaparse. Recortarla seria
    tirar datos buenos."""
    detalle = _detalle()
    sin = eng.walk_forward(detalle, n_folds=3, embargo_days=0)
    con = eng.walk_forward(detalle, n_folds=3, embargo_days=eng.embargo_for(63))
    assert con[-1].end == sin[-1].end
    assert con[-1].n_obs == sin[-1].n_obs


def test_no_embargo_leaves_the_windows_untouched():
    detalle = _detalle()
    sin = eng.walk_forward(detalle, n_folds=3, embargo_days=0)
    assert sum(f.n_obs for f in sin) == len(detalle)


def test_the_embargo_is_measured_in_calendar_days_not_sessions():
    """Las fechas de los eventos son naturales y el horizonte viene en
    sesiones. Sin convertir, un embargo de 63 dias dejaria pasar cinco semanas
    de solapamiento."""
    assert eng.embargo_for(5) == 7
    assert eng.embargo_for(63) == 89
    assert eng.embargo_for(0) == 0


def test_a_window_left_too_short_by_the_embargo_is_dropped():
    """Mejor ninguna ventana que una ventana de tres eventos: con tan pocos, el
    signo del exceso lo decide cualquier cosa."""
    detalle = _detalle(n=40)
    ventanas = eng.walk_forward(detalle, n_folds=3, embargo_days=eng.embargo_for(63))
    assert all(v.n_obs >= 10 for v in ventanas)


# ---------------------------------------------------------------------------
# El reetiquetado de la familia
# ---------------------------------------------------------------------------
def _resultado(signal_id: str, p: float, excess: float = 0.01) -> eng.ValidationResult:
    """Un resultado ya clasificado como validado, para probar la degradacion."""
    evento = mx.EventMetrics(
        n_obs=500, hit_rate=0.6, hit_rate_vs_benchmark=0.6, avg_return=excess,
        median_return=excess, avg_excess=excess, std_return=0.05,
        t_stat=3.0, best=0.2, worst=-0.1, benchmark_avg=0.0,
        n_dates=200, p_value=p, ci_low=excess / 2, ci_high=excess * 2,
    )
    ventanas = [
        eng.FoldResult(f"Ventana {i}", pd.Timestamp("2020-01-01"),
                       pd.Timestamp("2021-01-01"), 100, excess, 0.6,
                       float("nan"), float("nan"))
        for i in range(1, 4)
    ]
    return eng.ValidationResult(
        signal_id=signal_id, scope=eng.SCOPE_EQUITY_US, horizon_days=21,
        evidence=eng.VALIDATED, event=evento, ic_mean=float("nan"),
        ic_ir=float("nan"), folds=ventanas,
    )


def test_a_lone_signal_keeps_its_label():
    resultados = eng.apply_multiple_testing([_resultado("SOLA", 0.01)])
    assert resultados[0].evidence == eng.VALIDATED
    assert resultados[0].n_tests == 1


def test_the_same_signal_is_demoted_when_it_is_one_of_forty():
    """El caso que de verdad cambia lo que ves en pantalla."""
    familia = [_resultado("BUENA", 0.03)] + [
        _resultado(f"RUIDO{i}", 0.5) for i in range(39)
    ]
    resultados = eng.apply_multiple_testing(familia)
    buena = resultados[0]
    assert buena.evidence == eng.WEAK
    assert not buena.survives_fdr
    assert "azar" in buena.reason
    assert buena.n_tests == 40


def test_the_demotion_says_why_instead_of_just_lowering_the_label():
    """Una etiqueta que baja sin explicacion se lee como un fallo del programa
    y acaba ignorandose."""
    familia = [_resultado("BUENA", 0.03)] + [
        _resultado(f"RUIDO{i}", 0.5) for i in range(39)
    ]
    motivo = eng.apply_multiple_testing(familia)[0].reason
    assert "senales y horizontes probados" in motivo


def test_correcting_an_empty_family_does_not_crash():
    assert eng.apply_multiple_testing([]) == []


def test_a_family_where_nothing_can_be_evaluated_does_not_crash():
    sin_p = _resultado("SIN", float("nan"))
    resultados = eng.apply_multiple_testing([sin_p])
    assert resultados[0].n_tests == 0
