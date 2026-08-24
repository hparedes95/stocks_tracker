"""Los totales de la cartera sumaban dolares y euros como si valieran lo mismo.

EL FALLO, ENCONTRADO EN LA AUDITORIA FINANCIERA

`5_watchlist.py` calculaba el valor de cada posicion en SU divisa

    positions["valor"] = positions["qty"] * positions["close"]

y despues sumaba la columna entera. Con EUR/USD a 1,17:

    10 AAPL a 200 USD  = 2.000 USD = 1.709,40 EUR
    10 SAN a 5 EUR     =                50,00 EUR
    -----------------------------------------------
    Total correcto                  1.759,40 EUR
    Total que salia                 2.050,00 EUR   (+16,5 %)

Y el peso de cada posicion salia del mismo total, asi que AAPL se veia con un
97,6 % de la cartera cuando de verdad es el 97,2 %... en este ejemplo poco, pero
con la cartera al reves —lo grande en euros y lo pequeno en dolares— el sesgo va
en la direccion contraria y es igual de grande. Es la cifra con la que se decide
si una posicion pesa demasiado.

Habia un aviso en pantalla: "los totales se suman sin convertir, trata las
cifras como orientativas". Un aviso no arregla una cifra. Se lee una vez y
despues se mira el numero, que sigue estando mal.

LO QUE NO SE ARREGLA, Y SE DICE

El coste en euros se convierte al cambio de HOY, no al del dia de la compra. Eso
no se puede saber sin guardar el tipo de cada operacion, y el extracto del broker
no lo trae. Es una aproximacion declarada en pantalla, no un dato.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from stocks_tracker.core import db, fx

HOY = date.today()


@pytest.fixture
def almacen(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}
        ui: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub


def _guardar_tipo(ticker: str, valor: float, cuando: date = HOY) -> None:
    with db.connect() as conn:
        db.upsert_df(conn, "prices_daily", pd.DataFrame([
            {"ticker": ticker, "date": cuando, "open": valor, "high": valor,
             "low": valor, "close": valor, "adj_close": valor, "volume": 0,
             "source": "yfinance"},
        ]), keys=["ticker", "date"])


def _leer_tipos() -> dict[str, float]:
    with db.connect(read_only=True) as conn:
        return fx.tipos(conn)


# ---------------------------------------------------------------------------
# Leer los tipos del almacen
# ---------------------------------------------------------------------------
def test_el_tipo_se_lee_del_almacen(almacen):
    _guardar_tipo("EURUSD=X", 1.17)

    assert _leer_tipos() == {"USD": pytest.approx(1.17)}


def test_una_divisa_sin_par_guardado_no_aparece(almacen):
    """Y no aparece con un 1,0 puesto a mano. Ver el test de abajo: lo que no
    se sabe tiene que salir vacio."""
    _guardar_tipo("EURUSD=X", 1.17)

    assert "GBP" not in _leer_tipos()


def test_un_tipo_viejo_no_se_da_por_vigente(almacen):
    """Un tipo de hace un mes no describe el dia que se esta valorando. Mejor
    no dar cifra que darla vieja sin decirlo."""
    _guardar_tipo("EURUSD=X", 1.17)
    _guardar_tipo("EURGBP=X", 0.85, HOY - timedelta(days=40))

    assert "GBP" not in _leer_tipos()
    assert "USD" in _leer_tipos()


def test_se_coge_el_tipo_mas_reciente_y_no_uno_cualquiera(almacen):
    _guardar_tipo("EURUSD=X", 1.05, HOY - timedelta(days=3))
    _guardar_tipo("EURUSD=X", 1.17, HOY)

    assert _leer_tipos()["USD"] == pytest.approx(1.17)


def test_si_TODOS_los_tipos_son_viejos_no_vale_ninguno(almacen):
    """FALLO ENCONTRADO EN REVISION. La antiguedad se medía contra la fecha mas
    nueva de los PROPIOS tipos, asi que si todos estaban igual de viejos el mas
    nuevo era su propia referencia —antiguedad cero— y la guarda no saltaba
    nunca.

    Justo el caso que importa: el programa lleva dias sin descargar y la cartera
    se valora con tipos de hace un mes sin que nada lo diga. Ahora la referencia
    es hoy.
    """
    _guardar_tipo("EURUSD=X", 1.17, HOY - timedelta(days=40))
    _guardar_tipo("EURGBP=X", 0.85, HOY - timedelta(days=40))

    assert _leer_tipos() == {}, "se esta valorando con tipos de hace mes y medio"


# ---------------------------------------------------------------------------
# Convertir
# ---------------------------------------------------------------------------
def test_los_dolares_se_dividen_por_el_tipo():
    """Yahoo publica EURUSD=X como "cuantos USD vale 1 EUR", asi que de USD a
    EUR se DIVIDE. Multiplicar da 2.340 en vez de 1.709: el error mas facil de
    cometer aqui y el que no se nota mirando el numero."""
    euros = fx.a_base(pd.Series([2000.0]), pd.Series(["USD"]), {"USD": 1.17})

    assert euros.iloc[0] == pytest.approx(1709.4017, rel=1e-6)


def test_los_euros_se_quedan_igual():
    euros = fx.a_base(pd.Series([50.0]), pd.Series(["EUR"]), {"USD": 1.17})

    assert euros.iloc[0] == pytest.approx(50.0)


def test_el_total_de_una_cartera_mixta():
    """EL CASO EXACTO DE LA AUDITORIA. 2.000 USD + 50 EUR no son 2.050."""
    euros = fx.a_base(pd.Series([2000.0, 50.0]), pd.Series(["USD", "EUR"]),
                      {"USD": 1.17})

    assert float(euros.sum()) == pytest.approx(1759.4017, rel=1e-6)


def test_una_divisa_sin_tipo_sale_vacia_y_no_a_la_par():
    """Lo que NO puede pasar. Tratar una libra como un euro es el mismo fallo
    que se esta arreglando, solo que sin aviso: 1.000 GBP contados como 1.000
    EUR son 150 EUR de mas que nadie va a ver."""
    euros = fx.a_base(pd.Series([1000.0]), pd.Series(["GBP"]), {"USD": 1.17})

    assert pd.isna(euros.iloc[0]), "una divisa desconocida se esta tratando a la par"


def test_el_hueco_arrastra_el_total_a_vacio():
    """Y el NaN tiene que llegar al total.

    Este test encontro un fallo REAL en el arreglo: `Series.sum()` salta los NaN
    por defecto, asi que la primera version sumaba 1.709 EUR —correcto para la
    posicion en dolares, sin rastro de los 1.000 GBP— y lo presentaba como el
    total de la cartera. Un numero mas pequeno, redondo y con toda la pinta de
    estar bien. De ahi `fx.total`.
    """
    euros = fx.a_base(pd.Series([2000.0, 1000.0]), pd.Series(["USD", "GBP"]),
                      {"USD": 1.17})

    assert pd.isna(fx.total(euros))
    assert not pd.isna(float(euros.sum())), (
        "si pandas cambia y sum() ya contagia, este test deja de probar nada"
    )


def test_la_divisa_se_lee_sin_importar_las_mayusculas():
    euros = fx.a_base(pd.Series([100.0]), pd.Series(["usd"]), {"USD": 1.25})

    assert euros.iloc[0] == pytest.approx(80.0)


def test_se_dice_que_divisas_se_han_quedado_fuera():
    """Sin esto, la pantalla solo puede decir "algo falta"."""
    faltan = fx.sin_tipo(pd.Series(["USD", "GBP", "EUR", "CHF"]), {"USD": 1.17})

    assert faltan == ["CHF", "GBP"]


def test_una_posicion_sin_divisa_tambien_se_nombra():
    """FALLO ENCONTRADO EN REVISION. `sin_tipo` hacia `dropna()` mientras
    `a_base` hacia `fillna("")`: una posicion sin divisa anulaba el total pero
    NO aparecia en la lista de lo que falta.

    Y como la pantalla elige entre el aviso y la nota con
    `if faltan / elif hay varias divisas`, el resultado era un total vacio sin
    una sola linea que dijera por que.
    """
    divisas = pd.Series(["USD", None])

    assert pd.isna(fx.a_base(pd.Series([100.0, 50.0]), divisas, {"USD": 1.0}).iloc[1])
    assert fx.SIN_DIVISA in fx.sin_tipo(divisas, {"USD": 1.0}), (
        "una posicion sin divisa vacia el total sin aparecer en el aviso"
    )


def test_una_divisa_vacia_cuenta_igual_que_una_ausente():
    assert fx.SIN_DIVISA in fx.sin_tipo(pd.Series(["EUR", ""]), {})


# ---------------------------------------------------------------------------
# Que la pantalla lo use de verdad
# ---------------------------------------------------------------------------
def test_los_pares_declarados_se_descargan():
    """Un par en `fx.PARES` que no este en el universo no se descarga nunca, y
    la conversion sale vacia sin que nadie entienda por que."""
    from stocks_tracker.core.config import get_active_universes, get_universes

    universos = get_universes()
    declarados = {t for clave in get_active_universes()
                  if (spec := universos.get(clave))
                  for t in spec.tickers}

    faltan = sorted(set(fx.PARES.values()) - declarados)
    assert not faltan, f"pares que nunca se descargan: {faltan}"


def test_la_pagina_de_cartera_se_pinta_con_dos_divisas(almacen):
    """La rama que se ha tocado, PINTADA de verdad.

    El smoke test de las paginas no siembra cartera, asi que todo el bloque de
    la cartera —el que suma, convierte y calcula pesos— no se ejecutaba en
    ningun test. Una pagina que revienta con dos divisas dentro pasaria en
    verde entera.
    """
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    from stocks_tracker.app import data_access as da
    from stocks_tracker.core.config import project_root

    _guardar_tipo("EURUSD=X", 1.17)
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "AAA", "name": "Alfa", "asset_class": "equity",
             "currency": "USD", "gics_sector": "Tecnologia", "is_active": True},
            {"ticker": "BBB", "name": "Beta", "asset_class": "equity",
             "currency": "EUR", "gics_sector": "Banca", "is_active": True},
        ]), keys=["ticker"])
        db.upsert_df(conn, "prices_daily", pd.DataFrame([
            {"ticker": t, "date": HOY, "open": p, "high": p, "low": p,
             "close": p, "adj_close": p, "volume": 1_000, "source": "yfinance"}
            for t, p in (("AAA", 200.0), ("BBB", 5.0))
        ]), keys=["ticker", "date"])
        # `get_positions` saca el precio de `indicators_daily` y no de
        # `prices_daily`. Sin esto, `close` llega NULL, el valor de cada
        # posicion sale NaN y la pagina se pinta "sin reventar" sin haber
        # sumado nada: el test pasaria sin probar la conversion.
        db.upsert_df(conn, "indicators_daily", pd.DataFrame([
            {"ticker": t, "date": HOY, "close": p, "ret_1d": 0.01}
            for t, p in (("AAA", 200.0), ("BBB", 5.0))
        ]), keys=["ticker", "date"])
    da.add_position("AAA", 10, 180.0, "USD")
    da.add_position("BBB", 10, 4.0, "EUR")
    st.cache_data.clear()

    ruta = project_root() / "src/stocks_tracker/app/pages/5_watchlist.py"
    prueba = AppTest.from_file(str(ruta), default_timeout=120)
    prueba.run()

    assert not prueba.exception, (
        f"la cartera con dos divisas revienta: {prueba.exception[0].message}"
    )

    # 10 AAA a 200 USD = 1.709,40 EUR; 10 BBB a 5 EUR = 50 EUR. Total 1.759,40.
    # Sin convertir salian 2.050, un 16,5 % de mas. Se comprueba la CIFRA que
    # sale por pantalla, no que la funcion de conversion sepa dividir.
    valores = [str(m.value) for m in prueba.metric]
    assert any("1 759" in v for v in valores), (
        f"el valor de la cartera no esta convertido a euros: {valores}"
    )
    # Y con el simbolo del euro. `format_money` pone el dolar por defecto, asi
    # que el total salia como "1 759.40 $": la cifra bien y la etiqueta falsa,
    # que es el mismo fallo que se esta arreglando en otra parte de la pantalla.
    assert any("1 759" in v and "€" in v for v in valores), (
        f"el total en euros lleva otro simbolo: {valores}"
    )
    st.cache_data.clear()


def test_una_posicion_sin_tipo_no_deja_pintar_un_reparto_incompleto(almacen):
    """FALLO ENCONTRADO EN REVISION, y es de los que hacen dano callando.

    Con una posicion sin tipo de cambio:

    - `groupby(...)["valor_eur"].sum()` la contaba como CERO. Su sector
      desaparecia del grafico Y del denominador, asi que el aviso de
      concentracion podia decir "Tech pesa el 97 %" siendo Auto el mayor.
    - `peso.fillna(0)` dejaba el perfil factorial con ceros en todos los ejes,
      que se lee como "esta cartera no tiene ningun sesgo factorial".

    Dos afirmaciones falsas dichas con un grafico. Ahora no se pinta ninguna de
    las dos y se dice por que.
    """
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    from stocks_tracker.app import data_access as da
    from stocks_tracker.core.config import project_root

    _guardar_tipo("EURUSD=X", 1.17)          # hay USD, NO hay GBP
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "AAA", "name": "Alfa", "asset_class": "equity",
             "currency": "USD", "gics_sector": "Tecnologia", "is_active": True},
            {"ticker": "CCC", "name": "Ceta", "asset_class": "equity",
             "currency": "GBP", "gics_sector": "Automocion", "is_active": True},
        ]), keys=["ticker"])
        for tabla in ("prices_daily", "indicators_daily"):
            db.upsert_df(conn, tabla, pd.DataFrame([
                {"ticker": t, "date": HOY, "open": p, "high": p, "low": p,
                 "close": p, "adj_close": p, "volume": 1_000,
                 "source": "yfinance", "ret_1d": 0.0}
                for t, p in (("AAA", 200.0), ("CCC", 500.0))
            ]).drop(columns=(["ret_1d"] if tabla == "prices_daily"
                             else ["open", "high", "low", "adj_close",
                                   "volume", "source"])),
                keys=["ticker", "date"])
    da.add_position("AAA", 10, 180.0, "USD")
    da.add_position("CCC", 10, 400.0, "GBP")
    st.cache_data.clear()

    ruta = project_root() / "src/stocks_tracker/app/pages/5_watchlist.py"
    prueba = AppTest.from_file(str(ruta), default_timeout=120)
    prueba.run()

    assert not prueba.exception, prueba.exception[0].message

    textos = " ".join(str(e.value) for e in prueba.info) + " ".join(
        str(e.value) for e in prueba.warning)
    assert "GBP" in textos, "no se dice que divisa falta"
    assert "no se puede repartir el peso" in textos.lower(), (
        "se esta pintando un reparto al que le falta una posicion"
    )
    st.cache_data.clear()


def test_el_stress_test_cuenta_las_posiciones_que_deja_fuera():
    """FALLO ENCONTRADO EN REVISION. El filtro `pd.notna(valor_eur)` descartaba
    las posiciones sin valorar SIN HACER RUIDO, y todo lo que sale del panel es
    un peso relativo: la perdida en euros y las apuestas independientes se
    calculaban sobre una cartera parcial y se presentaban como la cartera.

    El numero de descartadas sale por la puerta principal para que la pantalla
    no pueda callarlo por descuido.
    """
    from stocks_tracker.app.components.stress_panel import cartera_en_euros

    posiciones = pd.DataFrame([
        {"ticker": "AAA", "valor_eur": 1000.0, "gics_sector": "Tecnologia"},
        {"ticker": "CCC", "valor_eur": float("nan"), "gics_sector": "Automocion"},
    ])

    cartera, fuera = cartera_en_euros(posiciones)

    assert [p["ticker"] for p in cartera] == ["AAA"]
    assert fuera == 1, "la posicion sin valorar se cae sin que nadie lo cuente"


def test_el_stress_test_pesa_en_euros_y_no_en_la_divisa_de_cada_valor():
    from stocks_tracker.app.components.stress_panel import cartera_en_euros

    posiciones = pd.DataFrame([
        {"ticker": "AAA", "valor": 2000.0, "valor_eur": 1709.40,
         "gics_sector": "Tecnologia"},
    ])

    cartera, _ = cartera_en_euros(posiciones)

    assert cartera[0]["valor"] == pytest.approx(1709.40)


def test_la_atribucion_pondera_en_euros(almacen):
    """FALLO ENCONTRADO EN REVISION. `get_attribution_inputs` devolvia el coste
    en la divisa de cada valor y `attribution` lo usa como PESO: una posicion en
    dolares pesaba un 17 % mas de lo que le toca y el reparto entre mercado,
    sector y seleccion salia escorado hacia lo comprado fuera del euro. En la
    misma pagina que este cambio convierte.

    10 AAA a 180 USD son 1.800 USD = 1.538,46 EUR, no 1.800.
    """
    import streamlit as st

    from stocks_tracker.app import data_access as da

    _guardar_tipo("EURUSD=X", 1.17)
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "AAA", "name": "Alfa", "asset_class": "equity",
             "currency": "USD", "gics_sector": "Tecnologia", "is_active": True},
        ]), keys=["ticker"])
        db.upsert_df(conn, "prices_daily", pd.DataFrame([
            {"ticker": t, "date": HOY, "open": p, "high": p, "low": p,
             "close": p, "adj_close": p, "volume": 1_000, "source": "yfinance"}
            for t, p in (("AAA", 200.0), (da.MERCADO_TICKER, 5000.0))
        ]), keys=["ticker", "date"])
    da.add_position("AAA", 10, 180.0, "USD")
    st.cache_data.clear()

    datos = da.get_attribution_inputs()

    assert len(datos) == 1
    assert float(datos.iloc[0]["coste"]) == pytest.approx(1538.4615, rel=1e-4), (
        "la atribucion vuelve a ponderar con el coste sin convertir"
    )
    st.cache_data.clear()


def test_en_la_atribucion_una_divisa_sin_tipo_conserva_su_cifra(almacen):
    """LA DECISION QUE NO ES OBVIA, fijada para que nadie la cambie sin querer.

    En la cartera, una posicion que no se puede convertir sale NaN y arrastra el
    total: ahi el NaN es una celda vacia que se ve y se pregunta.

    Aqui NO. El coste es un peso relativo dentro de un reparto, y un NaN no deja
    una celda vacia: saca la posicion entera de la atribucion sin decirlo, y el
    reparto entre mercado, sector y seleccion se calcula sobre una cartera a la
    que le falta un trozo. De las dos aproximaciones malas, quedarse con la
    cifra sin convertir es la que menos miente.
    """
    import streamlit as st

    from stocks_tracker.app import data_access as da

    _guardar_tipo("EURUSD=X", 1.17)          # hay USD, NO hay GBP
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "CCC", "name": "Ceta", "asset_class": "equity",
             "currency": "GBP", "gics_sector": "Automocion", "is_active": True},
        ]), keys=["ticker"])
        db.upsert_df(conn, "prices_daily", pd.DataFrame([
            {"ticker": t, "date": HOY, "open": p, "high": p, "low": p,
             "close": p, "adj_close": p, "volume": 1_000, "source": "yfinance"}
            for t, p in (("CCC", 500.0), (da.MERCADO_TICKER, 5000.0))
        ]), keys=["ticker", "date"])
    da.add_position("CCC", 10, 400.0, "GBP")
    st.cache_data.clear()

    datos = da.get_attribution_inputs()

    assert len(datos) == 1, "la posicion sin tipo ha desaparecido del reparto"
    assert float(datos.iloc[0]["coste"]) == pytest.approx(4000.0)
    st.cache_data.clear()


def test_la_cartera_suma_en_euros():
    """Se comprueba sobre el codigo real de la pagina, no sobre un doble: el
    fallo estaba justo en que la pagina sumaba la columna sin convertir."""
    from pathlib import Path

    import stocks_tracker

    pagina = (Path(stocks_tracker.__file__).parent
              / "app" / "pages" / "5_watchlist.py").read_text(encoding="utf-8")

    assert 'fx.total(positions["valor_eur"])' in pagina, (
        "el valor total vuelve a sumarse sin convertir"
    )
    assert 'fx.total(positions["coste_eur"])' in pagina
    assert 'float(positions["valor"].sum())' not in pagina
