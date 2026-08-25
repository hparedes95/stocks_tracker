"""La calibracion: medir lo que se puede y negarse a lo demas.

QUE PERSIGUEN ESTAS PRUEBAS

Que la calibracion no se convierta en el sello de aprobacion que el asesor
todavia no tiene. Hay cuatro formas de que eso pase y ninguna da error:

1. Calibrar un perfil con fundamentales y presentarlo como validado. No hay
   serie punto-en-el-tiempo de balances: puntuar 2019 con los de hoy es mirar
   el futuro, y lo que se mide entonces es la supervivencia.
2. Dar un numero con veinte observaciones. El intervalo es tan ancho que
   cualquier resultado cabe dentro, pero el numero se lee igual.
3. Usar el error estandar corriente con ventanas que se solapan. Una compra a
   seis meses comparte cinco con la del mes siguiente; ignorarlo infla el
   t-stat y convierte ruido en "significativo".
4. Callar que el intervalo incluye el cero. Un +2,1 % con intervalo de -4 % a
   +8 % no dice que la regla funcione, y omitirlo es la mentira mas comoda.

LOS DATOS DE ESTOS TESTS SON SINTETICOS Y NO DEMUESTRAN NADA SOBRE EL MERCADO.
Se comprueba la aritmetica y las negativas, no que el asesor acierte.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stocks_tracker.core import advice_calib as calib
from stocks_tracker.core.advice import UMBRAL_COMPRAR

HORIZONTE_MESES = 1
HORIZONTE = HORIZONTE_MESES * calib.SESIONES_POR_MES


def _mundo(n_tickers: int = 40, n_dias: int = 400, ventaja: float = 0.0,
           semilla: int = 7):
    """Un universo sintetico donde los mejores puntuados ganan `ventaja`.

    Con `ventaja=0` el percentil no aporta nada y la calibracion tiene que
    decirlo. Con una ventaja grande, tiene que detectarla: un medidor que no
    distingue los dos casos no mide.
    """
    rng = np.random.default_rng(semilla)
    fechas = pd.bdate_range("2020-01-01", periods=n_dias)
    tickers = [f"T{i:02d}" for i in range(n_tickers)]

    # Percentil fijo por ticker: asi la senal es limpia y lo que se prueba es
    # el medidor, no la capacidad de encontrar senal en el ruido.
    pctiles = {t: (i + 0.5) / n_tickers for i, t in enumerate(tickers)}

    filas_precio, filas_score = [], []
    for t in tickers:
        deriva = ventaja if pctiles[t] >= UMBRAL_COMPRAR else 0.0
        pasos = rng.normal(deriva / HORIZONTE, 0.01, n_dias)
        precio = 100.0 * np.exp(np.cumsum(pasos))
        for f, p in zip(fechas, precio, strict=True):
            filas_precio.append({"ticker": t, "date": f, "adj_close": p})
            filas_score.append({"ticker": t, "date": f,
                                "composite_pctile": pctiles[t]})

    bench = pd.Series(
        100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.008, n_dias))), index=fechas)
    return pd.DataFrame(filas_score), pd.DataFrame(filas_precio), bench


def _calibrar(**kw):
    scores, precios, bench = _mundo(**{k: v for k, v in kw.items()
                                       if k in {"ventaja", "n_dias", "semilla"}})
    return calib.calibrar(scores, precios, bench, preset="bot_core",
                          horizonte_meses=HORIZONTE_MESES)


# ---------------------------------------------------------------------------
# Las negativas, que son la mitad del valor de este modulo
# ---------------------------------------------------------------------------
def test_un_bloqueo_del_gate_impide_dar_ningun_numero():
    """Precios sinteticos o ranking contaminado: el resultado no es
    interpretable y no se da, en vez de darlo con una nota al pie."""
    scores, precios, bench = _mundo()

    c = calib.calibrar(scores, precios, bench,
                       bloqueos=("400.000 precios son SINTETICOS.",))

    assert c.exceso_medio_pct is None
    assert not c.concluyente
    assert "SINTETICOS" in calib.veredicto(c)


def test_un_perfil_con_fundamentales_se_marca_como_no_validable():
    """LA NEGATIVA QUE MAS IMPORTA. `balanced` es el perfil por defecto y lleva
    valor, crecimiento, calidad y dividendo: todos de balances que solo existen
    a dia de hoy.

    Si esto deja de avisar, la pantalla ensenara un t-stat bonito sobre el
    perfil que el usuario usa de verdad, y ese numero mide supervivencia.
    """
    assert not calib.factores_de_precio("balanced")
    assert calib.factores_de_precio("bot_core")

    scores, precios, bench = _mundo()
    c = calib.calibrar(scores, precios, bench, preset="balanced",
                       horizonte_meses=HORIZONTE_MESES)

    assert not c.solo_precio
    assert "NO valida el ranking que estas usando" in calib.veredicto(c)


def test_con_pocas_observaciones_no_hay_veredicto():
    """Veinte casos dan un intervalo en el que cabe cualquier cosa. El numero,
    en cambio, se lee igual de convincente."""
    scores, precios, bench = _mundo(n_tickers=40, n_dias=60)
    pocos = scores[scores["date"] < scores["date"].min() + pd.Timedelta(days=3)]

    c = calib.calibrar(pocos, precios, bench, preset="bot_core",
                       horizonte_meses=HORIZONTE_MESES)

    assert not c.concluyente
    assert "pocas para concluir" in calib.veredicto(c)


def test_pocas_observaciones_repartidas_en_muchas_fechas_tampoco_valen():
    """AGUJERO ENCONTRADO POR LA BATERIA DE MUTACION.

    El test de arriba tropieza con las DOS guardas a la vez —pocas
    observaciones y pocas fechas—, asi que bajar `MIN_OBSERVACIONES` a 1 pasaba
    en verde: la de fechas seguia parando el caso.

    Aqui hay fechas de sobra y observaciones justas: un solo valor por encima
    del liston. Con una empresa no se concluye nada por muchos dias que se
    miren, porque es una sola apuesta repetida.
    """
    scores, precios, bench = _mundo(n_tickers=6, n_dias=120)

    c = calib.calibrar(scores, precios, bench, preset="bot_core",
                       horizonte_meses=HORIZONTE_MESES)

    assert c.fechas >= calib.MIN_FECHAS
    assert c.observaciones < calib.MIN_OBSERVACIONES
    assert not c.concluyente
    assert "pocas para concluir" in calib.veredicto(c)


def test_muchas_observaciones_de_pocas_fechas_tampoco_valen():
    """Cien observaciones sacadas de tres dias no son cien datos: son tres,
    medidos sobre treinta empresas que se mueven juntas. Sin este minimo, un
    tramo corto y afortunado pasaria por evidencia."""
    scores, precios, bench = _mundo(n_tickers=400, n_dias=120)
    dias = sorted(scores["date"].unique())[:3]
    apretado = scores[scores["date"].isin(dias)]

    c = calib.calibrar(apretado, precios, bench, preset="bot_core",
                       horizonte_meses=HORIZONTE_MESES)

    assert c.observaciones >= calib.MIN_OBSERVACIONES
    assert c.fechas < calib.MIN_FECHAS
    assert not c.concluyente


def test_sin_ranking_historico_se_dice_como_generarlo():
    """El estado del primer dia. "Sin datos" a secas deja al usuario sin saber
    que hacer."""
    c = calib.calibrar(pd.DataFrame(), pd.DataFrame(), pd.Series(dtype=float))

    assert "--history" in calib.veredicto(c)


# ---------------------------------------------------------------------------
# Que el medidor mida
# ---------------------------------------------------------------------------
def test_sin_ventaja_el_intervalo_incluye_el_cero_y_se_dice():
    """LA MENTIRA MAS COMODA: dar el exceso medio y callar que el intervalo
    incluye el cero. Aqui el percentil no aporta nada por construccion."""
    c = _calibrar(ventaja=0.0)

    assert c.concluyente
    assert c.ic_bajo_pct <= 0 <= c.ic_alto_pct
    assert "incluye el cero" in calib.veredicto(c)
    assert "NO se puede afirmar" in calib.veredicto(c)


def test_con_ventaja_grande_se_detecta():
    """Contraprueba. Un medidor que nunca encuentra nada es tan inutil como uno
    que lo encuentra siempre, y los dos se leen igual de prudentes."""
    c = _calibrar(ventaja=0.60)

    assert c.concluyente
    assert c.exceso_medio_pct > 0
    assert c.t_stat > 2.0, "no detecta una ventaja construida a proposito"


def test_el_corte_es_el_del_asesor_y_no_un_decil_aproximado():
    """Medir el decil superior y luego aplicar el percentil 90 serian dos cosas
    distintas. La calibracion tiene que medir la regla que se usa de verdad."""
    scores, precios, bench = _mundo(n_tickers=40, n_dias=200)
    c = calib.calibrar(scores, precios, bench, preset="bot_core",
                       horizonte_meses=HORIZONTE_MESES)

    # 40 tickers, percentiles (i+0,5)/40: cuatro estan en 0,90 o por encima.
    por_fecha = c.observaciones / max(c.fechas, 1)
    assert 3.5 <= por_fecha <= 4.5, (
        f"el corte no coincide con el del asesor: {por_fecha:.1f} por fecha"
    )


def test_el_error_estandar_tiene_en_cuenta_el_solapamiento():
    """Las ventanas se solapan: una compra a seis meses comparte cinco con la
    del mes siguiente. Con el error estandar corriente, el t-stat sale inflado
    y convierte ruido en 'significativo'.

    Se comprueba contra el t-stat ingenuo sobre los MISMOS datos: el corregido
    tiene que ser mas conservador.
    """
    from stocks_tracker.backtest import metrics

    scores, precios, bench = _mundo(ventaja=0.30, n_dias=400)
    c = calib.calibrar(scores, precios, bench, preset="bot_core",
                       horizonte_meses=HORIZONTE_MESES)

    fwd = __import__("stocks_tracker.backtest.engine", fromlist=["x"])
    f = fwd.forward_returns(precios, horizons=(HORIZONTE,))
    b = fwd.benchmark_forward_returns(bench, horizons=(HORIZONTE,))
    datos = (scores.merge(f[["ticker", "date", f"fwd_{HORIZONTE}"]],
                          on=["ticker", "date"])
             .merge(b[["date", f"bench_{HORIZONTE}"]], on="date"))
    datos = datos[datos["composite_pctile"] >= UMBRAL_COMPRAR].dropna()
    exceso = (datos[f"fwd_{HORIZONTE}"] - datos[f"bench_{HORIZONTE}"]).to_numpy()

    ingenuo = abs(metrics.t_statistic(exceso))

    assert abs(c.t_stat) < ingenuo, (
        "el t-stat no esta corregido por solapamiento: sale inflado"
    )


def test_la_tasa_de_acierto_y_el_exceso_van_juntos():
    """Una tasa del 55 % con exceso medio negativo es posible —muchos aciertos
    pequenos y pocos fallos enormes— y ensenar solo una de las dos cifras deja
    esa historia sin contar."""
    c = _calibrar(ventaja=0.30)

    assert c.tasa_de_acierto is not None
    assert 0.0 <= c.tasa_de_acierto <= 1.0
    assert c.exceso_medio_pct is not None


def test_el_veredicto_siempre_lleva_el_intervalo():
    """Un exceso medio suelto invita a creer. Con su intervalo al lado dice la
    verdad."""
    c = _calibrar(ventaja=0.30)
    texto = calib.veredicto(c)

    assert "intervalo" in texto
    assert "t=" in texto
    assert "casos en" in texto
    assert pytest.approx(c.exceso_medio_pct, abs=0.01) == float(
        texto.split("rindio")[1].split("puntos")[0].strip().rstrip("+")
        .replace("+", ""))
