"""Los siete hallazgos de la auditoria financiera, con sus cifras.

Cada test lleva el caso numerico que demostro el fallo. No son ejemplos
ilustrativos: son las cifras con las que se reprodujo, y si el arreglo se
deshace vuelven a salir.

El orden es el de la auditoria: precision monetaria, coste y P&L, operaciones
societarias, divisas, metricas.
"""

from __future__ import annotations

import datetime as dt
import pathlib

import duckdb
import pandas as pd
import pytest

from stocks_tracker.core import advice, corporate, fx

_ESQUEMA = (pathlib.Path(__file__).resolve().parents[1]
            / "src/stocks_tracker/core/schema.sql")

# 1 EUR = 1,17 USD. El mismo numero en toda la bateria.
EURUSD = 1.17


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute(_ESQUEMA.read_text())
    return conn


# ---------------------------------------------------------------------------
# CRITICO 1 - El tamano de compra mezclaba euros con dolares
# ---------------------------------------------------------------------------
def _recomendar(tipo_cambio: float) -> advice.Recomendacion:
    """AAPL a 230 USD con ATR14 de 4,60 USD y una cartera de 20.000 EUR."""
    return advice.sobre_un_candidato(
        "AAPL", percentil=0.97, cobertura=0.9,
        precio=230.0, atr14=4.60,
        equity=20000.0, caja=20000.0,
        tipo_cambio=tipo_cambio,
    )


def test_el_importe_de_una_compra_en_dolares_sale_en_euros():
    """EL FALLO: `(risk_amount_EUR / stop_distance_USD) * price_USD`.

    DONDE MUERDE, QUE NO ES DONDE PARECIA. Al escribir este test asumi que la
    cifra mala era `importe_eur`, y no lo es: aqui manda el tope por regimen,
    que se calcula sobre la equity y ya estaba en euros (2.400). Lo que salia
    mal eran los TITULOS que se compraban con ese importe.

        Antes:  titulos = 2.400 / 230 USD     = 10,4348
                gastas 2.400 USD = 2.051,28 EUR, no los 2.400 EUR del tope.
                Se invierte un 14,5 % MENOS de lo que las reglas permiten.

        Ahora:  titulos = 2.400 / (230/1,17)  = 12,2087
                gastas 2.808 USD = 2.400,00 EUR exactos.

    El invariante que lo fija: titulos x precio, pasado a euros, es el importe.
    Sin el, cualquier reaparicion del fallo pasa desapercibida porque el
    importe -el numero que se mira- no cambia.
    """
    r = _recomendar(EURUSD)

    assert r.veredicto is advice.Veredicto.COMPRAR
    assert r.titulos == pytest.approx(12.2087, abs=1e-4)
    assert r.titulos * 230.0 / EURUSD == pytest.approx(r.importe_eur, rel=1e-9)


def test_el_riesgo_declarado_es_el_riesgo_real():
    """El otro lado del mismo fallo, y el que importa para no arruinarse.

    `risk_per_trade_pct` es 1,5 %, o sea 300 EUR sobre 20.000. Antes se
    dimensionaba con un stop en dolares contra un presupuesto en euros y el
    riesgo REAL quedaba un 14,5 % por debajo del configurado: se arriesgaba
    menos de lo previsto, que suena inofensivo hasta que el error va en el
    sentido contrario con otra divisa.

    Ahora los titulos tienen que cuadrar: perder el stop cuesta exactamente lo
    que dice `riesgo_eur`.
    """
    r = _recomendar(EURUSD)

    stop_local = r.stop
    perdida_usd = (230.0 - stop_local) * r.titulos
    assert perdida_usd / EURUSD == pytest.approx(r.riesgo_eur, rel=1e-9)


def test_el_stop_se_devuelve_en_la_divisa_del_valor():
    """Un stop en euros para un valor que cotiza en dolares no sirve de nada:
    es un precio que se mira en el grafico y se teclea en el broker.

    Stop = 230 - 2,5 x 4,60 = 218,50 USD.
    """
    r = _recomendar(EURUSD)
    assert r.stop == pytest.approx(218.50, abs=0.01)


def test_un_valor_en_euros_no_cambia_de_tamano():
    """Contrapeso. El arreglo no puede mover lo que ya estaba bien: con
    `tipo_cambio=1.0` el resultado tiene que ser identico al de antes."""
    r = _recomendar(1.0)
    assert r.titulos == pytest.approx(10.4348, abs=1e-4)
    assert r.importe_eur == pytest.approx(2400.0, abs=0.01)
    assert r.stop == pytest.approx(218.50, abs=0.01)


def test_un_tipo_de_cambio_ausente_no_rompe_el_consejo():
    """Sin tipo se cae a 1,0. Es lo que ya pasaba, y quedarse sin consejo por
    no tener un tipo de cambio seria peor que darlo sin convertir."""
    for malo in (None, 0.0, -1.0, float("nan")):
        r = advice.sobre_un_candidato(
            "AAPL", percentil=0.97, cobertura=0.9, precio=230.0, atr14=4.60,
            equity=20000.0, caja=20000.0, tipo_cambio=malo)
        assert r.veredicto is advice.Veredicto.COMPRAR


# ---------------------------------------------------------------------------
# CRITICO 2 - Nada ajustaba la posicion cuando habia un split
# ---------------------------------------------------------------------------
def test_un_split_no_convierte_una_ganancia_en_un_desplome():
    """EL CASO NVDA, CON SUS CIFRAS.

        Compras 10 a 900 USD          -> coste 9.000
        Split 10:1. Yahoo sirve 95 USD.
        Sin ajustar:  10 x 95 =   950 contra 9.000  ->  -89,4 %
        Ajustado:    100 x 95 = 9.500 contra 9.000  ->   +5,6 %

    Error de signo y de dos ordenes de magnitud en la cifra por la que se
    decide si una posicion va bien.
    """
    posiciones = pd.DataFrame([{
        "ticker": "NVDA", "qty": 10.0, "avg_cost": 900.0,
        "opened_at": pd.Timestamp("2024-01-15"),
    }])
    splits = pd.DataFrame([{
        "ticker": "NVDA", "date": pd.Timestamp("2024-06-10"), "factor": 10.0,
    }])

    ajustada = corporate.ajustar_por_splits(posiciones, splits).iloc[0]

    assert ajustada["qty"] == pytest.approx(100.0)
    assert ajustada["avg_cost"] == pytest.approx(90.0)
    # El valor economico del coste NO cambia: es la misma compra en otra escala.
    assert ajustada["qty"] * ajustada["avg_cost"] == pytest.approx(9000.0)
    # Y ahora el resultado tiene el signo correcto.
    assert (ajustada["qty"] * 95.0) / 9000.0 - 1.0 == pytest.approx(0.0556, abs=1e-4)


def test_un_split_anterior_a_la_compra_no_se_aplica():
    """Ya esta recogido en el precio que pago el usuario. Aplicarlo otra vez lo
    contaria dos veces y multiplicaria la posicion por diez sin motivo."""
    posiciones = pd.DataFrame([{
        "ticker": "NVDA", "qty": 10.0, "avg_cost": 90.0,
        "opened_at": pd.Timestamp("2024-08-01"),
    }])
    splits = pd.DataFrame([{
        "ticker": "NVDA", "date": pd.Timestamp("2024-06-10"), "factor": 10.0,
    }])

    ajustada = corporate.ajustar_por_splits(posiciones, splits).iloc[0]
    assert ajustada["qty"] == pytest.approx(10.0)
    assert ajustada["avg_cost"] == pytest.approx(90.0)


def test_un_split_del_MISMO_dia_de_la_compra_tampoco():
    """La frontera. Comprar el dia del split ya es comprar a precio nuevo, asi
    que la comparacion es estrictamente `>` y no `>=`."""
    posiciones = pd.DataFrame([{
        "ticker": "NVDA", "qty": 10.0, "avg_cost": 90.0,
        "opened_at": pd.Timestamp("2024-06-10"),
    }])
    splits = pd.DataFrame([{
        "ticker": "NVDA", "date": pd.Timestamp("2024-06-10"), "factor": 10.0,
    }])
    assert corporate.ajustar_por_splits(posiciones, splits).iloc[0]["qty"] == 10.0


def test_dos_splits_se_acumulan():
    """4:1 y despues 2:1 son 8:1 en total, no 4 ni 2."""
    posiciones = pd.DataFrame([{
        "ticker": "AAA", "qty": 5.0, "avg_cost": 800.0,
        "opened_at": pd.Timestamp("2023-01-01"),
    }])
    splits = pd.DataFrame([
        {"ticker": "AAA", "date": pd.Timestamp("2023-06-01"), "factor": 4.0},
        {"ticker": "AAA", "date": pd.Timestamp("2024-06-01"), "factor": 2.0},
    ])
    ajustada = corporate.ajustar_por_splits(posiciones, splits).iloc[0]
    assert ajustada["qty"] == pytest.approx(40.0)
    assert ajustada["avg_cost"] == pytest.approx(100.0)


def test_los_factores_salen_del_almacen():
    """El lector, contra la tabla de verdad. Un dividendo NO es un split y no
    puede colarse: multiplicaria la posicion por el importe del dividendo."""
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO corporate_actions (ticker, date, action_type, value) "
            "VALUES ('AAA', DATE '2024-06-01', 'split', 4.0), "
            "       ('AAA', DATE '2024-03-01', 'dividend', 0.25)")
        salida = corporate.factores_de_split(conn, ["AAA"])
    finally:
        conn.close()

    assert list(salida["factor"]) == [4.0]


# ---------------------------------------------------------------------------
# ALTO 4 - El resultado en euros eliminaba el efecto divisa
# ---------------------------------------------------------------------------
def test_el_coste_se_convierte_al_cambio_del_dia_de_la_compra():
    """EL CASO CON SUS CIFRAS.

        Compras 1.000 USD con el EUR/USD a 1,05  ->  952,38 EUR de verdad
        Hoy valen 1.100 USD con el cambio a 1,17 ->  940,17 EUR

        Con el tipo de HOY en los dos lados:  940,17 - 854,70 = +85,47 (+10 %)
        Con el tipo de CADA fecha:            940,17 - 952,38 = -12,21 (-1,3 %)

    Se declaraba una ganancia donde hay una perdida. Y la segunda cifra es la
    que cuenta para Hacienda, que calcula la ganancia patrimonial en euros al
    tipo de cada fecha (punto a confirmar con un asesor fiscal).
    """
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO prices_daily (ticker, date, close) VALUES "
            "('EURUSD=X', DATE '2024-01-10', 1.05), "
            "('EURUSD=X', DATE '2026-08-24', 1.17)")
        tabla = fx.tipos_en(conn, [dt.date(2024, 1, 10)])
    finally:
        conn.close()

    coste_eur = fx.a_base_en_fecha(
        pd.Series([1000.0]), pd.Series(["USD"]),
        pd.Series([pd.Timestamp("2024-01-10")]), tabla)

    assert coste_eur.iloc[0] == pytest.approx(952.38, abs=0.01)

    valor_eur = 1100.0 / 1.17
    assert valor_eur - coste_eur.iloc[0] == pytest.approx(-12.21, abs=0.01)


def test_sin_tipo_de_aquel_dia_el_coste_sale_vacio():
    """Un NaN que llega al total es correcto: `fx.total` no lo salta y la
    pantalla pinta una raya, que se pregunta. Rellenar con el tipo de hoy seria
    volver al fallo por la puerta de atras."""
    conn = _conn()
    try:
        conn.execute("INSERT INTO prices_daily (ticker, date, close) "
                     "VALUES ('EURUSD=X', DATE '2026-08-24', 1.17)")
        tabla = fx.tipos_en(conn, [dt.date(2020, 1, 10)])
    finally:
        conn.close()

    salida = fx.a_base_en_fecha(
        pd.Series([1000.0]), pd.Series(["USD"]),
        pd.Series([pd.Timestamp("2020-01-10")]), tabla)
    assert pd.isna(salida.iloc[0])


def test_los_euros_no_necesitan_tipo_de_cambio():
    """Contrapeso: una cartera en euros no puede quedarse sin coste porque
    falte un par que no le hace ninguna falta."""
    salida = fx.a_base_en_fecha(
        pd.Series([1000.0]), pd.Series(["EUR"]),
        pd.Series([pd.Timestamp("2020-01-10")]), pd.DataFrame())
    assert salida.iloc[0] == pytest.approx(1000.0)


def test_el_tipo_vigente_es_el_ultimo_anterior_no_el_exacto():
    """Se compra un lunes festivo y no hay cotizacion de ese dia. Exigir la
    fecha exacta dejaria sin coste en euros a media cartera."""
    conn = _conn()
    try:
        conn.execute("INSERT INTO prices_daily (ticker, date, close) "
                     "VALUES ('EURUSD=X', DATE '2024-01-05', 1.05)")
        tabla = fx.tipos_en(conn, [dt.date(2024, 1, 8)])
    finally:
        conn.close()

    assert float(tabla["tipo"].iloc[0]) == pytest.approx(1.05)


# ---------------------------------------------------------------------------
# ALTO 3 - La divisa de la posicion no se contrastaba con la del valor
# ---------------------------------------------------------------------------
@pytest.fixture
def almacen(tmp_path, monkeypatch):
    """Un almacen real apuntado por la configuracion, para leer de punta a punta."""
    from stocks_tracker.core import db

    class Stub:
        warehouse_path = tmp_path / "w.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}
        ui: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    import streamlit as st

    st.cache_data.clear()
    return Stub


def test_manda_la_divisa_de_cotizacion_y_se_conserva_la_declarada(almacen):
    """EL FALLO: dos puertas de entrada con defaults CONTRADICTORIOS.

    `add_position` pone "USD" por defecto y el importador manual pone "EUR".
    `instruments.currency` trae la de Yahoo, que es la de los precios con los
    que se valora, y nadie la comparaba.

        SAN.MC cotiza en EUR. Importada como USD:
        1.000 acciones a 5,00 -> 5.000 EUR reales, pero se dividia entre 1,17
        y salian 4.274 EUR.

    Ese error entra en el total, en el peso de la posicion y de ahi en la regla
    de concentracion del asesor: una posicion al 22,5 % se ve al 19,2 % y el
    REDUCIR por concentracion NO salta.
    """
    from stocks_tracker.app import data_access as da
    from stocks_tracker.core import db

    with db.connect() as conn:
        conn.execute("INSERT INTO instruments (ticker, name, asset_class, currency) "
                     "VALUES ('SAN.MC', 'Santander', 'equity', 'EUR')")
        conn.execute(
            "INSERT INTO positions (id, ticker, qty, avg_cost, currency, opened_at) "
            "VALUES ('p1', 'SAN.MC', 1000, 5.0, 'USD', DATE '2024-01-10')")

    fila = da.get_positions().iloc[0]
    assert fila["currency"] == "EUR", "manda la divisa en la que cotiza"
    assert fila["currency_declarada"] == "USD", (
        "y la declarada se conserva para poder avisar de la discrepancia"
    )


def test_sin_divisa_en_el_instrumento_se_usa_la_declarada(almacen):
    """Un almacen antiguo puede no tener `instruments.currency`. Quedarse sin
    divisa dejaria la posicion sin valorar, que es peor que fiarse de lo que
    dijo el usuario."""
    from stocks_tracker.app import data_access as da
    from stocks_tracker.core import db

    with db.connect() as conn:
        conn.execute("INSERT INTO instruments (ticker, name, asset_class) "
                     "VALUES ('AAA', 'Ejemplo', 'equity')")
        conn.execute(
            "INSERT INTO positions (id, ticker, qty, avg_cost, currency, opened_at) "
            "VALUES ('p1', 'AAA', 10, 100.0, 'GBP', DATE '2024-01-10')")

    assert da.get_positions().iloc[0]["currency"] == "GBP"


def test_la_cartera_llega_a_la_pantalla_con_el_split_ya_aplicado(almacen):
    """De punta a punta: `get_positions` es por donde entra la cartera en TODAS
    las pantallas, asi que es donde tiene que estar el ajuste. Probar solo
    `ajustar_por_splits` dejaria pasar que nadie la llame."""
    from stocks_tracker.app import data_access as da
    from stocks_tracker.core import db

    with db.connect() as conn:
        conn.execute("INSERT INTO instruments (ticker, name, asset_class, currency) "
                     "VALUES ('NVDA', 'Nvidia', 'equity', 'USD')")
        conn.execute(
            "INSERT INTO positions (id, ticker, qty, avg_cost, currency, opened_at) "
            "VALUES ('p1', 'NVDA', 10, 900.0, 'USD', DATE '2024-01-15')")
        conn.execute("INSERT INTO corporate_actions (ticker, date, action_type, value) "
                     "VALUES ('NVDA', DATE '2024-06-10', 'split', 10.0)")

    fila = da.get_positions().iloc[0]
    assert fila["qty"] == pytest.approx(100.0)
    assert fila["avg_cost"] == pytest.approx(90.0)


def test_el_coste_en_euros_de_la_cartera_usa_el_cambio_de_la_compra(almacen):
    """El mismo caso de arriba, pero llegando por donde llega a la pantalla."""
    from stocks_tracker.app import data_access as da
    from stocks_tracker.core import db

    with db.connect() as conn:
        conn.execute("INSERT INTO instruments (ticker, name, asset_class, currency) "
                     "VALUES ('AAPL', 'Apple', 'equity', 'USD')")
        conn.execute(
            "INSERT INTO positions (id, ticker, qty, avg_cost, currency, opened_at) "
            "VALUES ('p1', 'AAPL', 10, 100.0, 'USD', DATE '2024-01-10')")
        conn.execute("INSERT INTO prices_daily (ticker, date, close) VALUES "
                     "('EURUSD=X', DATE '2024-01-10', 1.05), "
                     "('EURUSD=X', DATE '2026-08-24', 1.17)")

    # 1.000 USD al cambio de la COMPRA (1,05) son 952,38 EUR.
    # Al de hoy (1,17) serian 854,70, y ahi se perderia el efecto divisa.
    coste = da.get_positions().iloc[0]["coste_eur_compra"]
    assert coste == pytest.approx(952.38, abs=0.01)


# ---------------------------------------------------------------------------
# Lo que encontro la revision de codigo SOBRE los arreglos de arriba
# ---------------------------------------------------------------------------
def test_el_indice_se_convierte_a_euros_igual_que_el_valor(almacen):
    """UN FALLO QUE YO MISMO INTRODUJE AL ARREGLAR EL ANTERIOR.

    `_divisas` solo recibia los tickers recomendados, asi que
    `divisas.get(benchmark)` no acertaba NUNCA y el indice se daba por euros
    mientras el valor si se convertia. Eso no arreglaba el sesgo de divisa: lo
    INVERTIA, y en el caso mas comun —un valor estadounidense contra el
    S&P 500—.

    Aqui el valor y el indice hacen EXACTAMENTE lo mismo (+10 % los dos, en
    dolares), asi que el exceso tiene que ser cero se mueva como se mueva el
    euro. Con el fallo puesto salian -11,28 pp.
    """
    from stocks_tracker.core import advice_store, db

    with db.connect() as conn:
        for t, div in (("AAPL", "USD"), ("^GSPC", "USD")):
            conn.execute("INSERT INTO instruments (ticker, name, asset_class, "
                         "currency) VALUES (?, ?, 'equity', ?)", [t, t, div])
        conn.execute(
            "INSERT INTO recommendations (fecha, ticker, weights_hash, "
            "veredicto, conviccion, precio, horizonte_meses) VALUES "
            "(DATE '2025-01-10', 'AAPL', 'w1', 'comprar', 'alta', 100.0, 6)")
        conn.execute("INSERT INTO prices_daily (ticker, date, close) VALUES "
                     "('AAPL', DATE '2025-01-10', 100.0), "
                     "('AAPL', DATE '2026-08-24', 110.0), "
                     "('^GSPC', DATE '2025-01-10', 5000.0), "
                     "('^GSPC', DATE '2026-08-24', 5500.0), "
                     "('EURUSD=X', DATE '2025-01-10', 1.05), "
                     "('EURUSD=X', DATE '2026-08-24', 1.17)")
        salida = advice_store.puntuar(conn, hasta=dt.date(2026, 8, 24))

    assert len(salida) == 1
    assert float(salida["exceso"].iloc[0]) == pytest.approx(0.0, abs=1e-9), (
        "valor e indice en la misma divisa y con el mismo movimiento: el "
        "exceso es cero, y cualquier otra cosa la ha puesto el tipo de cambio"
    )


def test_un_extracto_reimportado_no_cuenta_el_split_dos_veces():
    """EL SEGUNDO FALLO DE MI ARREGLO, Y EL PEOR.

    `positions` no es un libro de operaciones: es una FOTO.
    `replace_positions` la reescribe con las cifras del extracto de hoy —ya
    ajustadas por el broker— pero CONSERVA `opened_at`. Y la pantalla nueva de
    "poner la fecha real de compra" retrasa esa fecha sin tocar las cifras.

    Con `opened_at` como referencia, las dos cosas hacen que el split se cuente
    dos veces: 100 NVDA reimportadas se convertian en 1.000.

    La referencia buena es `updated_at`: cuando las cifras se supieron ciertas.
    """
    reimportada = pd.DataFrame([{
        "ticker": "NVDA", "qty": 100.0, "avg_cost": 90.0,
        "opened_at": pd.Timestamp("2024-01-15"),      # compra real, pre-split
        "updated_at": pd.Timestamp("2026-08-01"),     # extracto de hoy
    }])
    splits = pd.DataFrame([{
        "ticker": "NVDA", "date": pd.Timestamp("2024-06-10"), "factor": 10.0,
    }])

    ajustada = corporate.ajustar_por_splits(reimportada, splits).iloc[0]
    assert ajustada["qty"] == pytest.approx(100.0), "el broker ya lo aplico"
    assert ajustada["avg_cost"] == pytest.approx(90.0)


def test_sin_updated_at_se_cae_a_opened_at():
    """Filas de antes de que existiera la columna. Quedarse sin referencia
    dejaria de ajustar nada, que es el fallo original."""
    vieja = pd.DataFrame([{
        "ticker": "NVDA", "qty": 10.0, "avg_cost": 900.0,
        "opened_at": pd.Timestamp("2024-01-15"), "updated_at": None,
    }])
    splits = pd.DataFrame([{
        "ticker": "NVDA", "date": pd.Timestamp("2024-06-10"), "factor": 10.0,
    }])
    assert corporate.ajustar_por_splits(vieja, splits).iloc[0]["qty"] == 100.0


def test_el_comando_de_consejos_pesa_la_cartera_como_la_pantalla(almacen):
    """LOS ARREGLOS SOLO HABIAN ENTRADO POR STREAMLIT.

    Pero los consejos se calculan en el COMANDO, y es `_cartera` la que decide
    `peso_pct`, que es lo que dispara el REDUCIR por concentracion. Arreglarlo
    solo en la pantalla dejaba la cifra buena a la vista y la mala decidiendo.

    SAN.MC declarada como USD: 1.000 x 5 = 5.000 EUR reales. Con la divisa
    declarada se dividian entre 1,17 y salian 4.274.
    """
    from stocks_tracker.compute.run_advice import _cartera
    from stocks_tracker.core import db

    with db.connect() as conn:
        conn.execute("INSERT INTO instruments (ticker, name, asset_class, "
                     "currency) VALUES ('SAN.MC', 'Santander', 'equity', 'EUR')")
        conn.execute(
            "INSERT INTO positions (id, ticker, qty, avg_cost, currency, "
            "opened_at, updated_at) VALUES "
            "('p1', 'SAN.MC', 1000, 5.0, 'USD', DATE '2024-01-10', "
            " TIMESTAMP '2024-01-10 00:00:00')")
        conn.execute("INSERT INTO indicators_daily (ticker, date, close) "
                     "VALUES ('SAN.MC', DATE '2026-08-24', 5.0)")
        conn.execute("INSERT INTO prices_daily (ticker, date, close) "
                     "VALUES ('EURUSD=X', DATE '2026-08-24', 1.17)")
        posiciones, _, _ = _cartera(conn)

    assert float(posiciones["valor_eur"].iloc[0]) == pytest.approx(5000.0)


def test_el_comando_de_consejos_ajusta_los_splits(almacen):
    """Lo mismo con el otro arreglo: el peso de una posicion con split salia
    dividido entre diez, y con el una posicion concentrada parecia pequena."""
    from stocks_tracker.compute.run_advice import _cartera
    from stocks_tracker.core import db

    with db.connect() as conn:
        conn.execute("INSERT INTO instruments (ticker, name, asset_class, "
                     "currency) VALUES ('NVDA', 'Nvidia', 'equity', 'EUR')")
        conn.execute(
            "INSERT INTO positions (id, ticker, qty, avg_cost, currency, "
            "opened_at, updated_at) VALUES "
            "('p1', 'NVDA', 10, 900.0, 'EUR', DATE '2024-01-15', "
            " TIMESTAMP '2024-01-15 00:00:00')")
        conn.execute("INSERT INTO corporate_actions (ticker, date, "
                     "action_type, value) VALUES "
                     "('NVDA', DATE '2024-06-10', 'split', 10.0)")
        conn.execute("INSERT INTO indicators_daily (ticker, date, close) "
                     "VALUES ('NVDA', DATE '2026-08-24', 95.0)")
        posiciones, _, _ = _cartera(conn)

    assert float(posiciones["valor_eur"].iloc[0]) == pytest.approx(9500.0)


def test_sin_tipo_de_cambio_no_se_inventa_un_tamano():
    """`tipos_cambio.get(divisa, 1.0)` restauraba el fallo original en silencio
    cuando `fx.tipos` descarta un par por llevar mas de una semana sin
    actualizarse —o sea, cuando el programa lleva dias sin descargar—.

    Y contradecia la regla que el propio `fx.py` documenta: lo que no se puede
    convertir sale vacio, nunca relleno con uno.
    """
    from stocks_tracker.core import advice_build

    ranking = pd.DataFrame([{
        "ticker": "AAPL", "composite_pctile": 0.97, "coverage": 0.9,
        "currency": "USD", "close": 230.0, "atr_pct": 2.0,
        "gics_sector": "Tech",
    }])

    r = advice_build.de_los_candidatos(
        ranking, equity=20000.0, caja=20000.0, tipos_cambio={})[0]

    assert r.veredicto is advice.Veredicto.SIN_OPINION
    assert r.importe_eur is None
    assert "tipo de cambio" in " ".join(r.motivos).lower()


def test_con_tipo_si_se_dimensiona():
    """Contrapeso: el guardia no puede dejar sin consejo a quien tiene el
    tipo."""
    from stocks_tracker.core import advice_build

    ranking = pd.DataFrame([{
        "ticker": "AAPL", "composite_pctile": 0.97, "coverage": 0.9,
        "currency": "USD", "close": 230.0, "atr_pct": 2.0,
        "gics_sector": "Tech",
    }])
    r = advice_build.de_los_candidatos(
        ranking, equity=20000.0, caja=20000.0,
        tipos_cambio={"USD": EURUSD})[0]

    assert r.veredicto is advice.Veredicto.COMPRAR
    assert r.titulos == pytest.approx(12.2087, abs=1e-4)
