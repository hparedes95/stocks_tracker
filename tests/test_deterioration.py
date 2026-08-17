"""El semaforo de deterioro.

Aqui los dos errores no valen lo mismo:

- Un ambar de mas hace mirar una posicion que estaba bien. Se pierde un minuto.
- Un verde de menos —porque falto un dato, porque un `NaN` se colo como
  numero— dice "todo en orden" sobre algo que se esta rompiendo. Y un verde se
  cree, que es justo el problema.

Por eso hay tantos tests de "no hay datos" como de deteccion, y por eso la
falta de datos tiene su propio color en vez de caer en verde.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from stocks_tracker.core import deterioration as det
from stocks_tracker.core.deterioration import Nivel, diagnosticar


def claves(d) -> set[str]:
    return {s.clave for s in d.senales}


# ---------------------------------------------------------------------------
# Fundamentales: lo que llega antes que el precio
# ---------------------------------------------------------------------------
def test_a_collapsing_margin_is_detected():
    """Del 22 % al 14 %: ocho puntos. No es la misma empresa que compraste, y
    esto se sabe trimestres antes de que el precio lo descuente del todo."""
    d = diagnosticar("AAA", fund_hoy={"profit_margin": 0.14},
                     fund_entonces={"profit_margin": 0.22})
    assert "margen" in claves(d)
    assert d.senales[0].grave


def test_a_small_margin_move_is_not_a_signal():
    """Medio punto es ruido de un trimestre. Un semaforo que se enciende con
    esto esta siempre encendido, y una luz siempre encendida no se mira."""
    d = diagnosticar("AAA", fund_hoy={"profit_margin": 0.215},
                     fund_entonces={"profit_margin": 0.22})
    assert claves(d) == set()


def test_the_margin_is_measured_in_points_not_in_percent():
    """Del 4 % al 3 % es un -25 % relativo y casi nada en absoluto: en un
    negocio de margen fino esa es su vida normal. Medirlo en relativo llenaria
    de rojos a los supermercados y las aerolineas."""
    d = diagnosticar("AAA", fund_hoy={"profit_margin": 0.03},
                     fund_entonces={"profit_margin": 0.04})
    assert "margen" not in claves(d)


def test_revenue_turning_negative_is_grave():
    """Crecer menos y dejar de crecer son cosas distintas. El cambio de signo
    es la senal fuerte."""
    d = diagnosticar("AAA", fund_hoy={"revenue_growth_yoy": -0.03},
                     fund_entonces={"revenue_growth_yoy": 0.12})
    senal = next(s for s in d.senales if s.clave == "ingresos")
    assert senal.grave


def test_growth_merely_slowing_is_only_a_watch():
    d = diagnosticar("AAA", fund_hoy={"revenue_growth_yoy": 0.05},
                     fund_entonces={"revenue_growth_yoy": 0.25})
    senal = next(s for s in d.senales if s.clave == "ingresos")
    assert not senal.grave


def test_debt_rising_from_a_low_level_is_not_a_signal():
    """De 0,3x a 1,3x es mucho en relativo y nada en riesgo real: sigue siendo
    una empresa poco endeudada."""
    d = diagnosticar("AAA", fund_hoy={"net_debt_to_ebitda": 1.3},
                     fund_entonces={"net_debt_to_ebitda": 0.3})
    assert "deuda" not in claves(d)


def test_debt_rising_from_an_already_high_level_is_grave():
    """De 3,2x a 4,4x. Con la deuda alta y subiendo, un mal trimestre deja de
    ser un mal trimestre."""
    d = diagnosticar("AAA", fund_hoy={"net_debt_to_ebitda": 4.4},
                     fund_entonces={"net_debt_to_ebitda": 3.2})
    assert "deuda" in claves(d)


def test_a_dividend_paid_out_of_thin_air_is_grave():
    """Repartir el 140 % de lo que ganas se paga con deuda o con caja. Esta no
    necesita comparacion con el pasado: es insostenible por si sola."""
    d = diagnosticar("AAA", fund_hoy={"payout_ratio": 1.4})
    assert "payout" in claves(d)
    assert d.senales[0].grave


def test_a_normal_payout_says_nothing():
    d = diagnosticar("AAA", fund_hoy={"payout_ratio": 0.45})
    assert claves(d) == set()


# ---------------------------------------------------------------------------
# Precio: lo unico que hay cuando no hay fundamentales
# ---------------------------------------------------------------------------
def test_losing_the_200_day_average_after_holding_it_is_a_signal():
    """Lo que importa es el CAMBIO. Un valor que ya estaba por debajo cuando lo
    compraste no ha empeorado por seguir estandolo."""
    d = diagnosticar("AAA", ind_hoy={"above_sma200": False},
                     ind_entonces={"above_sma200": True})
    assert "mm200" in claves(d)


def test_being_below_the_average_all_along_is_not_deterioration():
    """Comprado ya caido: es la tesis que elegiste, no algo que ha cambiado."""
    d = diagnosticar("AAA", ind_hoy={"above_sma200": False},
                     ind_entonces={"above_sma200": False})
    assert "mm200" not in claves(d)


def test_a_deep_drawdown_is_grave():
    d = diagnosticar("AAA", ind_hoy={"drawdown": -0.62})
    assert next(s for s in d.senales if s.clave == "caida").grave


def test_the_drawdown_message_says_how_much_it_must_rise_to_break_even():
    """Es la asimetria que casi nadie tiene en la cabeza: caer un 50 % exige
    subir un 100 % para volver al punto de partida, no un 50 %."""
    d = diagnosticar("AAA", ind_hoy={"drawdown": -0.50})
    assert "100 %" in next(s for s in d.senales if s.clave == "caida").texto


def test_lagging_the_index_is_a_signal():
    d = diagnosticar("AAA", ind_hoy={"rs_vs_bench_3m": -0.18})
    assert "relativa" in claves(d)


def test_beating_the_index_is_not():
    d = diagnosticar("AAA", ind_hoy={"rs_vs_bench_3m": 0.18})
    assert claves(d) == set()


def test_a_volatility_spike_is_a_signal():
    """Que el ultimo mes se mueva el doble que el ano suele significar que hay
    algo que el mercado todavia esta digiriendo."""
    d = diagnosticar("AAA", ind_hoy={"realized_vol_20": 0.60,
                                     "realized_vol_252": 0.25})
    assert "volatilidad" in claves(d)


def test_a_quiet_stock_is_not_flagged():
    d = diagnosticar("AAA", ind_hoy={"realized_vol_20": 0.22,
                                     "realized_vol_252": 0.25})
    assert claves(d) == set()


# ---------------------------------------------------------------------------
# Los colores
# ---------------------------------------------------------------------------
def test_nothing_wrong_is_green():
    d = diagnosticar("AAA", fund_hoy={"profit_margin": 0.22, "payout_ratio": 0.4},
                     fund_entonces={"profit_margin": 0.21},
                     ind_hoy={"drawdown": -0.05, "above_sma200": True},
                     ind_entonces={"above_sma200": True})
    assert d.nivel is Nivel.VERDE


def test_one_single_reason_is_amber_and_not_red():
    """Un motivo suelto merece mirarlo, no es una alarma. Si un solo motivo
    pintara rojo, el rojo dejaria de significar nada."""
    d = diagnosticar("AAA", ind_hoy={"rs_vs_bench_3m": -0.18})
    assert d.nivel is Nivel.AMBAR


def test_green_means_nothing_was_found_never_something_hidden():
    """El invariante que sostiene todo lo demas. Si el verde pudiera convivir
    con un motivo encontrado, el color estaria escondiendo justo lo que se
    queria ensenar, y ademas con el color que invita a no mirar."""
    for kwargs in (
        {"ind_hoy": {"rs_vs_bench_3m": -0.18}},
        {"ind_hoy": {"drawdown": -0.35}},
        {"fund_hoy": {"payout_ratio": 1.4}},
        {"fund_hoy": {"roe": 0.05}, "fund_entonces": {"roe": 0.20}},
    ):
        d = diagnosticar("AAA", **kwargs)
        assert d.senales, "el escenario no produce ninguna senal"
        assert d.nivel is not Nivel.VERDE


def test_two_grave_reasons_are_red():
    d = diagnosticar("AAA",
                     fund_hoy={"profit_margin": 0.10, "payout_ratio": 1.5},
                     fund_entonces={"profit_margin": 0.22})
    assert len(d.graves) == 2
    assert d.nivel is Nivel.ROJO


def test_one_grave_reason_plus_two_minor_ones_is_red():
    """Tres cosas a la vez es un patron, no una casualidad."""
    d = diagnosticar("AAA", fund_hoy={"payout_ratio": 1.5},
                     ind_hoy={"rs_vs_bench_3m": -0.18, "drawdown": -0.35})
    assert d.nivel is Nivel.ROJO


# ---------------------------------------------------------------------------
# Sin datos NO es verde
# ---------------------------------------------------------------------------
def test_no_data_at_all_is_grey_not_green():
    """El fallo mas peligroso de todos: un verde por no haber podido mirar.
    Verde significa "se ha mirado y no hay nada"; esto es "no se ha mirado"."""
    d = diagnosticar("AAA")
    assert d.nivel is Nivel.GRIS
    assert not d.hay_datos


def test_only_todays_data_still_diagnoses_what_it_can():
    """Sin la foto del dia de la compra no se puede comparar, pero lo que solo
    mira el presente —payout, caida desde maximos— sigue valiendo. Quedarse en
    gris seria tirar informacion que si esta."""
    d = diagnosticar("AAA", fund_hoy={"payout_ratio": 1.4})
    assert d.hay_datos
    assert "payout" in claves(d)


def test_a_nan_is_not_a_number():
    """`float('nan')` pasa por `is not None` y todas las comparaciones con el
    dan False, asi que un NaN colado produce un VERDE en silencio. Es el fallo
    exacto que este color esta puesto para evitar."""
    d = diagnosticar("AAA", fund_hoy={"profit_margin": float("nan")},
                     fund_entonces={"profit_margin": 0.22})
    assert claves(d) == set()
    assert d.nivel is Nivel.GRIS, "un NaN ha contado como dato valido"


def test_numpy_nan_is_treated_the_same():
    """DuckDB y pandas devuelven `np.nan`, no `float('nan')`."""
    d = diagnosticar("AAA", fund_hoy={"drawdown": np.nan})
    assert d.nivel is Nivel.GRIS


@pytest.mark.parametrize("infinito", [float("inf"), float("-inf")])
def test_an_infinity_is_not_a_number_either(infinito):
    """Un infinito no lo caza `pd.isna`, solo `np.isfinite`, y sale solo de una
    division rio arriba: un EBITDA de cero deja la deuda en infinito.

    Comparado contra cualquier umbral, el infinito positivo da True siempre y
    produce un motivo inventado; el negativo da False siempre y esconde uno
    real. Las dos formas de equivocarse en la misma linea.
    """
    d = diagnosticar("AAA", fund_hoy={"net_debt_to_ebitda": infinito},
                     fund_entonces={"net_debt_to_ebitda": 1.0})
    assert "deuda" not in claves(d)
    assert d.nivel is Nivel.GRIS


def test_a_missing_past_value_does_not_invent_a_comparison():
    """Sin margen de entonces no hay caida del margen. Tomar el ausente como
    cero diria que ha caido 22 puntos."""
    d = diagnosticar("AAA", fund_hoy={"profit_margin": 0.14},
                     fund_entonces={})
    assert "margen" not in claves(d)


def test_a_missing_today_value_does_not_invent_a_collapse():
    d = diagnosticar("AAA", fund_hoy={}, fund_entonces={"profit_margin": 0.22})
    assert "margen" not in claves(d)


def test_a_zero_roe_before_does_not_divide_by_zero():
    d = diagnosticar("AAA", fund_hoy={"roe": 0.05}, fund_entonces={"roe": 0.0})
    assert "roe" not in claves(d)


def test_a_zero_long_run_volatility_does_not_divide_by_zero():
    d = diagnosticar("AAA", ind_hoy={"realized_vol_20": 0.3,
                                     "realized_vol_252": 0.0})
    assert "volatilidad" not in claves(d)


def test_a_none_source_is_survived():
    """Un valor sin fundamentales —un indice, un ETF, una cripto— llega con
    `None` y no puede tumbar la pagina de la cartera."""
    assert diagnosticar("AAA", fund_hoy=None, ind_hoy=None).nivel is Nivel.GRIS


def test_a_source_that_is_not_a_mapping_is_survived():
    """Si algun dia llega una lista o una cadena por un cambio de consulta,
    tiene que salir gris, no reventar la pagina entera."""
    assert diagnosticar("AAA", fund_hoy=["nada"]).nivel is Nivel.GRIS


# ---------------------------------------------------------------------------
# Lo que se ensena
# ---------------------------------------------------------------------------
def test_every_signal_carries_its_number():
    """Un semaforo sin el motivo delante entrena a obedecer un color. El
    numero concreto es lo que permite discrepar con el."""
    d = diagnosticar("AAA", fund_hoy={"profit_margin": 0.14},
                     fund_entonces={"profit_margin": 0.22})
    texto = d.senales[0].texto
    assert "22.0 %" in texto and "14.0 %" in texto


def test_the_reference_date_travels_with_the_diagnosis():
    """Sin decir contra que fecha se compara, "ha bajado" no significa nada."""
    d = diagnosticar("AAA", fund_hoy={"payout_ratio": 1.4},
                     comparado_con=date(2024, 3, 1))
    assert d.comparado_con == date(2024, 3, 1)


def test_every_level_has_a_label():
    """La pantalla las usa todas; si faltara una saldria un KeyError."""
    assert set(det.ETIQUETA) == set(Nivel)


def test_the_thresholds_are_ordered_as_they_claim():
    """Si el umbral grave quedara por debajo del de vigilar, todo seria grave."""
    assert det.CAIDA_MARGEN_GRAVE_PP > det.CAIDA_MARGEN_VIGILAR_PP
    assert det.CAIDA_GRAVE < det.CAIDA_VIGILAR < 0
    assert det.PUNTOS_ROJO > det.PUNTOS_AMBAR > 0


def test_a_signal_is_worth_more_when_it_is_grave():
    assert det.Senal("x", True, "").puntos > det.Senal("x", False, "").puntos


@pytest.mark.parametrize("campo", ["profit_margin", "roe", "revenue_growth_yoy",
                                   "net_debt_to_ebitda", "payout_ratio"])
def test_no_fundamental_check_crashes_on_a_missing_field(campo):
    """Barrido: cada comprobacion con su campo y sin ningun otro."""
    diagnosticar("AAA", fund_hoy={campo: 0.1}, fund_entonces={campo: 0.5})


def test_no_data_from_the_purchase_day_is_grey_not_green():
    """El verde por falta de datos, colado por la puerta de atras.

    Con datos de HOY pero ninguno del dia de la compra, las comprobaciones que
    comparan no llegan a ejecutarse: solo corren las que miran el presente. Si
    esas no encuentran nada, decir "sin cambios a peor" afirma que no ha
    cambiado nada cuando lo unico cierto es que no se ha podido mirar.

    Le pasa a TODA posicion comprada antes de que el programa empezara a
    guardar el historico, o sea a la cartera entera de cualquiera que lo
    instale hoy.
    """
    d = diagnosticar("AAA", fund_hoy={"profit_margin": 0.22, "payout_ratio": 0.4},
                     fund_entonces={}, ind_hoy={"drawdown": -0.05},
                     ind_entonces={})
    assert d.hay_datos and not d.comparado
    assert d.senales == []
    assert d.nivel is Nivel.GRIS


def test_with_data_from_the_purchase_day_a_clean_position_is_green():
    """El contrario, para que el de arriba no pase por el motivo equivocado."""
    d = diagnosticar("AAA", fund_hoy={"profit_margin": 0.22},
                     fund_entonces={"profit_margin": 0.21},
                     ind_hoy={"drawdown": -0.05, "above_sma200": True},
                     ind_entonces={"above_sma200": True})
    assert d.comparado
    assert d.nivel is Nivel.VERDE


def test_a_real_signal_still_shows_even_without_the_purchase_snapshot():
    """Lo que si se puede comprobar con el presente no se pierde por no poder
    comparar: un dividendo sin cubrir sigue saliendo."""
    d = diagnosticar("AAA", fund_hoy={"payout_ratio": 1.4}, fund_entonces={})
    assert not d.comparado
    assert d.nivel is Nivel.ROJO or d.nivel is Nivel.AMBAR
    assert "payout" in claves(d)
