"""Contraste de fundamentales.

Un fundamental equivocado no da ningun error. Entra en el ranking, sube al
valor a los primeros puestos y ahi se queda con la misma pinta que los datos
buenos: un PER de 3 parece una ganga y un margen del 900 % parece un negocio
excepcional.

Los dos fallos que se vigilan aqui no son simetricos. Avisar de mas molesta;
callar un dato roto lo convierte en una recomendacion de compra. Por eso el
modulo avisa incluso cuando no sabe cual de los dos datos es el equivocado, y
por eso "no hay fundamentales" NO cuenta como fiable.
"""

from __future__ import annotations

import numpy as np
import pytest

from stocks_tracker.core import consistency as c
from stocks_tracker.core.config import project_root
from stocks_tracker.core.consistency import Gravedad, beta_desde_precios, revisar

RAIZ = project_root()


def campos(rev) -> set[str]:
    return rev.campos_sospechosos


# ---------------------------------------------------------------------------
# 1. Contra nuestros propios precios
# ---------------------------------------------------------------------------
def test_a_market_cap_that_does_not_match_price_times_shares_is_flagged():
    """El aviso que caza el error mas caro: un ticker cruzado con otra empresa.
    Todo lo demas cuadra entre si porque viene del mismo sitio equivocado; lo
    unico que no cuadra es contra NUESTRO precio."""
    rev = revisar("AAA", {"market_cap": 1e12, "shares_outstanding": 1e9},
                  precio=100.0)
    assert "market_cap" in campos(rev)


def test_a_market_cap_that_matches_is_not_flagged():
    rev = revisar("AAA", {"market_cap": 1e11, "shares_outstanding": 1e9},
                  precio=100.0)
    assert campos(rev) == set()


def test_a_small_difference_in_market_cap_is_tolerated():
    """Las acciones en circulacion se publican con retraso y cambian con las
    recompras. Una tolerancia fina llenaria la pantalla de avisos de nadie."""
    rev = revisar("AAA", {"market_cap": 1.05e11, "shares_outstanding": 1e9},
                  precio=100.0)
    assert campos(rev) == set()


def test_a_declared_beta_far_from_the_computed_one_is_flagged():
    rev = revisar("AAA", {"beta": 0.3}, beta_calculada=1.8)
    assert "beta" in campos(rev)


def test_a_beta_that_differs_a_lot_in_absolute_but_little_in_relative_passes():
    """1,20 frente a 1,90 difiere en 0,70 y aun asi es el mismo valor medido de
    dos formas. Yahoo la calcula a cinco anos con datos mensuales y aqui se usa
    un ano de datos diarios: con solo la diferencia absoluta, el aviso saltaba
    en tres de cada cuatro valores y dejaba de distinguir nada."""
    rev = revisar("AAA", {"beta": 1.20}, beta_calculada=1.90)
    assert campos(rev) == set()


def test_a_declared_beta_close_to_the_computed_one_is_fine():
    """Se calculan sobre periodos distintos: exigir que coincidan daria un
    aviso en todos los valores y no serviria para distinguir ninguno."""
    rev = revisar("AAA", {"beta": 1.10}, beta_calculada=1.35)
    assert campos(rev) == set()


def test_without_our_own_price_there_is_no_comparison():
    """Nada que contrastar no es lo mismo que contrastado y correcto."""
    rev = revisar("AAA", {"market_cap": 1e12, "shares_outstanding": 1e9})
    assert "market_cap" not in campos(rev)


# ---------------------------------------------------------------------------
# 2. Contra si mismos
# ---------------------------------------------------------------------------
def test_a_margin_above_one_hundred_percent_is_broken():
    """Ganar mas de lo que vendes. No es un dato extremo: es un dato roto."""
    rev = revisar("AAA", {"profit_margin": 9.0})
    assert rev.rotos
    assert rev.rotos[0].gravedad is Gravedad.ROTO


def test_a_high_but_possible_margin_is_not_flagged():
    """Hay empresas con un 40 % de margen neto. Confundirlas con un error
    haria desconfiar justo de los mejores negocios."""
    assert campos(revisar("AAA", {"profit_margin": 0.42})) == set()


def test_a_negative_margin_is_possible_and_not_flagged():
    """Perder dinero es normal; el umbral inferior existe para -900 %, no para
    una empresa en perdidas."""
    assert campos(revisar("AAA", {"profit_margin": -0.35})) == set()


def test_net_margin_cannot_beat_gross_margin():
    rev = revisar("AAA", {"gross_margin": 0.30, "profit_margin": 0.45})
    assert "profit_margin" in campos(rev)


def test_operating_margin_cannot_beat_gross_margin():
    rev = revisar("AAA", {"gross_margin": 0.30, "operating_margin": 0.40})
    assert "operating_margin" in campos(rev)


def test_a_normal_margin_ladder_passes():
    rev = revisar("AAA", {"gross_margin": 0.55, "operating_margin": 0.30,
                          "profit_margin": 0.22})
    assert campos(rev) == set()


def test_the_pe_is_not_checked_against_the_earnings_yield():
    """Parecia una identidad contable util y no lo es: nuestro proveedor obtiene
    el earnings yield dividiendo uno entre el PER.

    Son el mismo dato dos veces, asi que la comprobacion se cumplia siempre por
    construccion y un PER equivocado se validaba a si mismo. Peor que no
    tenerla: daba la impresion de que ese numero estaba contrastado.
    """
    incoherentes = revisar("AAA", {"trailing_pe": 20.0, "earnings_yield": 0.25})
    assert "trailing_pe" not in campos(incoherentes)

    fuente = (RAIZ / "src/stocks_tracker/providers/yfinance_provider.py").read_text(
        encoding="utf-8")
    assert '1.0 / info["trailingPE"]' in fuente, (
        "si el proveedor pasa a dar el earnings yield por su cuenta, vuelve a "
        "ser una identidad que si puede detectar algo"
    )


def test_the_check_does_not_depend_on_which_value_comes_first():
    """Dividir por uno de los dos daria una discrepancia distinta segun el
    orden, y el aviso saltaria o no por donde se escribiera la resta."""
    assert c._discrepancia(10.0, 5.0) == c._discrepancia(5.0, 10.0)


def test_a_leveraged_company_with_roe_below_roa_is_suspicious():
    """`debt_to_equity` llega en PORCENTAJE: 150 son 1,5 veces los fondos
    propios. El escenario usaba 1,5 —o sea un 1,5 %, casi sin deuda— y pasaba
    igual porque el umbral tambien estaba leido como veces."""
    rev = revisar("AAA", {"roe": 0.04, "roa": 0.09, "debt_to_equity": 150.0})
    assert "roe" in campos(rev)


def test_a_barely_indebted_company_is_not_compared(warehouse_free=None):
    """Un 1,5 % de deuda sobre fondos propios no explica nada de la diferencia
    entre ROE y ROA. Con el umbral leido como veces, esta empresa entraba."""
    rev = revisar("AAA", {"roe": 0.04, "roa": 0.09, "debt_to_equity": 1.5})
    assert "roe" not in campos(rev)


def test_a_debt_free_company_can_have_roe_close_to_roa():
    """Sin deuda las dos casi coinciden, asi que ahi no dice nada."""
    rev = revisar("AAA", {"roe": 0.08, "roa": 0.09, "debt_to_equity": 0.0})
    assert "roe" not in campos(rev)


def test_a_dividend_that_does_not_match_the_payout_is_flagged():
    """Repartir el 40 % de un beneficio del 5 % da un 2 %, no un 12 %."""
    rev = revisar("AAA", {"payout_ratio": 0.40, "earnings_yield": 0.05,
                          "dividend_yield": 0.12})
    assert "dividend_yield" in campos(rev)


def test_a_dividend_consistent_with_the_payout_passes():
    rev = revisar("AAA", {"payout_ratio": 0.40, "earnings_yield": 0.05,
                          "dividend_yield": 0.02})
    assert campos(rev) == set()


def test_a_dividend_that_only_differs_a_bit_is_tolerated():
    """El dividendo declarado suele mirar a los proximos doce meses y el payout
    a los doce anteriores: la identidad nunca cuadra fina, y exigirlo marcaba a
    un tercio del universo."""
    rev = revisar("AAA", {"payout_ratio": 0.40, "earnings_yield": 0.05,
                          "dividend_yield": 0.03})
    assert campos(rev) == set()


# ---------------------------------------------------------------------------
# 3. Contra el pasado
# ---------------------------------------------------------------------------
def test_a_ratio_that_multiplies_by_ten_overnight_is_the_data_not_the_company():
    rev = revisar("AAA", {"trailing_pe": 180.0}, anterior={"trailing_pe": 18.0})
    assert "trailing_pe" in campos(rev)


def test_a_ratio_that_collapses_is_just_as_suspicious():
    """Dividirse por diez es tan raro como multiplicarse por diez, y ademas es
    el que convierte un valor normal en una ganga aparente."""
    rev = revisar("AAA", {"trailing_pe": 1.8}, anterior={"trailing_pe": 18.0})
    assert "trailing_pe" in campos(rev)


def test_a_normal_move_between_snapshots_is_not_flagged():
    rev = revisar("AAA", {"trailing_pe": 21.0}, anterior={"trailing_pe": 18.0})
    assert campos(rev) == set()


def test_genuinely_volatile_fields_are_not_compared():
    """El crecimiento de ingresos pasa de +2 % a +30 % sin que nada este roto;
    avisarlo entrenaria a ignorar los avisos.

    Lo que protege es la lista BLANCA de campos estables, no un filtro sobre
    una lista negra. Se comprueba la lista ademas del comportamiento: con solo
    lo segundo, el test pasaba porque el campo no estaba en la lista y no
    porque nada lo excluyera —habia un filtro de campos volatiles que jamas se
    ejecutaba, y este test parecia cubrirlo—.
    """
    rev = revisar("AAA", {"revenue_growth_yoy": 0.30},
                  anterior={"revenue_growth_yoy": 0.02})
    assert campos(rev) == set()
    for volatil in ("revenue_growth_yoy", "earnings_growth_yoy", "fcf_yield"):
        assert volatil not in c.ESTABLES, (
            f"{volatil} se mueve solo y avisaria en cada descarga"
        )


def test_the_stable_list_still_holds_the_ones_that_matter():
    """Si la lista se vaciara sin querer, el contraste temporal dejaria de
    hacer nada y no fallaria ningun test de comportamiento."""
    assert {"trailing_pe", "profit_margin", "market_cap"} <= set(c.ESTABLES)


def test_without_a_previous_snapshot_nothing_is_compared():
    assert campos(revisar("AAA", {"trailing_pe": 180.0})) == set()


def test_a_previous_zero_does_not_divide_by_zero():
    rev = revisar("AAA", {"trailing_pe": 18.0}, anterior={"trailing_pe": 0.0})
    assert "trailing_pe" not in campos(rev)


def test_a_sign_flip_is_measured_in_absolute_value():
    """De -20 a +19 el cociente sale negativo y ninguna comparacion con un
    umbral positivo salta: un cambio de signo pasaria desapercibido."""
    rev = revisar("AAA", {"roe": 0.19}, anterior={"roe": -0.20})
    assert campos(rev) == set(), "un cambio de signo con magnitud parecida no es un salto"
    rev = revisar("AAA", {"roe": 2.0}, anterior={"roe": -0.20})
    assert "roe" in campos(rev)


# ---------------------------------------------------------------------------
# Ausencias
# ---------------------------------------------------------------------------
def test_no_fundamentals_at_all_is_not_reliable():
    """"No se ha encontrado nada porque no habia nada que mirar" no es lo mismo
    que "no se ha encontrado nada"."""
    rev = revisar("AAA", None)
    assert not rev.fiable


def test_a_clean_snapshot_is_reliable():
    rev = revisar("AAA", {"trailing_pe": 18.0, "profit_margin": 0.2})
    assert rev.fiable


def test_a_nan_is_not_a_value():
    """Un `nan` comparado con cualquier umbral da False y se cuela como bueno,
    o peor, contamina una identidad y produce un aviso inventado."""
    rev = revisar("AAA", {"profit_margin": float("nan"),
                          "gross_margin": 0.3})
    assert campos(rev) == set()


def test_missing_halves_of_an_identity_produce_no_warning():
    assert campos(revisar("AAA", {"trailing_pe": 20.0})) == set()


def test_a_source_that_is_not_a_mapping_is_survived():
    assert campos(revisar("AAA", ["esto no es un diccionario"])) == set()


# ---------------------------------------------------------------------------
# La beta calculada con nuestros datos
# ---------------------------------------------------------------------------
def test_a_stock_that_moves_twice_the_market_has_beta_two():
    mercado = np.random.default_rng(1).normal(0, 0.01, 300)
    assert beta_desde_precios(mercado * 2.0, mercado) == pytest.approx(2.0)


def test_a_stock_that_ignores_the_market_has_beta_zero():
    rng = np.random.default_rng(2)
    mercado, valor = rng.normal(0, 0.01, 300), rng.normal(0, 0.01, 300)
    assert abs(beta_desde_precios(valor, mercado)) < 0.3


def test_too_few_sessions_give_no_beta():
    """Con veinte sesiones la beta es ruido, y un numero ruidoso comparado con
    el declarado produce avisos falsos en cadena."""
    mercado = np.random.default_rng(3).normal(0, 0.01, 20)
    assert beta_desde_precios(mercado * 2, mercado) is None


def test_a_long_series_that_is_mostly_holes_is_also_too_short():
    """300 sesiones de las que 280 estan vacias son 20 sesiones. Comprobar el
    minimo ANTES de tirar los huecos deja pasar justo este caso, que es el
    normal en un valor recien anadido al universo."""
    mercado = np.random.default_rng(5).normal(0, 0.01, 300)
    valor = mercado * 2.0
    valor[20:] = np.nan
    assert beta_desde_precios(valor, mercado) is None


def test_a_flat_market_gives_no_beta():
    assert beta_desde_precios(np.ones(300), np.zeros(300)) is None


def test_series_of_different_lengths_give_no_beta():
    assert beta_desde_precios(np.ones(300), np.ones(200)) is None


def test_nans_do_not_poison_the_beta():
    mercado = np.random.default_rng(4).normal(0, 0.01, 300)
    valor = mercado * 2.0
    valor[5] = np.nan
    assert beta_desde_precios(valor, mercado) == pytest.approx(2.0, rel=0.05)


# ---------------------------------------------------------------------------
# La forma de los avisos
# ---------------------------------------------------------------------------
def test_a_broken_value_is_graded_worse_than_a_contradiction():
    """Un margen del 900 % no puede ser cierto; un PER que no cuadra con el
    earnings yield significa que uno de los dos falla, sin saber cual."""
    roto = revisar("AAA", {"profit_margin": 9.0})
    dudoso = revisar("AAA", {"roe": 0.04, "roa": 0.09, "debt_to_equity": 150.0})
    assert roto.rotos and not dudoso.rotos
    assert not dudoso.fiable


def test_the_warning_names_the_field_so_the_screen_can_mark_it():
    rev = revisar("AAA", {"profit_margin": 9.0})
    assert rev.avisos[0].campo == "profit_margin"


def test_the_impossible_ranges_are_ordered():
    """Con el minimo por encima del maximo, todo seria un dato roto."""
    for campo, (minimo, maximo) in c.IMPOSIBLES.items():
        assert minimo < maximo, campo


def test_no_threshold_is_set_so_high_that_it_can_never_fire():
    """`_discrepancia` de dos numeros del mismo signo SIEMPRE es menor que 1.

    Un umbral de 1,0 o mas deja la comprobacion muerta y no falla nada: la
    pantalla se queda en silencio y parece que no hay nada que avisar. Ya paso
    una vez con el del dividendo.
    """
    assert c._discrepancia(0.001, 1000.0) < 1.0
    for nombre in ("TOLERANCIA_CAPITALIZACION", "DISCREPANCIA_BETA",
                   "TOLERANCIA_DIVIDENDO"):
        umbral = getattr(c, nombre)
        assert 0 < umbral < 1.0, f"{nombre} = {umbral} no puede saltar nunca"
