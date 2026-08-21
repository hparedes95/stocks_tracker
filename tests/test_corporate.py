"""Splits y dividendos: la tabla que estaba en el esquema y no llenaba nadie.

`corporate_actions` existia desde el primer dia y ninguna linea de codigo
escribia en ella. Los splits y dividendos llegaban implicitos dentro del
`adj_close` de Yahoo y se perdian ahi dentro, asi que no habia forma de:

- separar el retorno del PRECIO del retorno TOTAL, ni
- comprobar que un split conserve el valor economico.

Lo segundo es lo que mas duele. Un split 4:1 que el proveedor aplica al precio
pero no al historico deja un salto del 75 % en la serie que NINGUNA comprobacion
de coherencia OHLC detecta: cada barra suelta es perfectamente valida y el salto
esta ENTRE dos barras. Ese salto se propaga a los retornos, a la volatilidad, al
drawdown y a cualquier senal de momentum, y un backtest sobre esa serie
encuentra una oportunidad espectacular que nunca existio.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from stocks_tracker.core import corporate, db

INICIO = date(2026, 1, 5)


def serie(ticker: str = "AAA", dias: int = 30, precio: float = 100.0,
          deriva: float = 0.0, parte_en: int | None = None,
          ratio: float = 1.0) -> pd.DataFrame:
    """Precios diarios; opcionalmente con el precio partido a partir de un dia."""
    filas = []
    for i in range(dias):
        p = precio * (1 + deriva) ** i
        if parte_en is not None and i >= parte_en:
            p /= ratio
        filas.append({
            "ticker": ticker, "date": INICIO + timedelta(days=i),
            "close": round(p, 4), "adj_close": round(p, 4),
        })
    return pd.DataFrame(filas)


def evento(tipo: str, valor: float, dia: int, ticker: str = "AAA") -> pd.DataFrame:
    return pd.DataFrame([{
        "ticker": ticker, "date": INICIO + timedelta(days=dia),
        "action_type": tipo, "value": valor,
    }])


# ---------------------------------------------------------------------------
# Splits
# ---------------------------------------------------------------------------

def test_un_split_bien_aplicado_no_da_hallazgo():
    precios = serie(parte_en=10, ratio=4.0)
    acciones = evento("split", 4.0, 10)

    assert corporate.comprobar_splits(precios, acciones) == []


def test_un_split_que_el_proveedor_no_aplico_se_detecta():
    """El caso que ninguna comprobacion de coherencia OHLC ve: la accion
    anuncia un 4:1 y el precio sigue igual."""
    precios = serie(parte_en=None)          # el precio NO se parte
    acciones = evento("split", 4.0, 10)

    malos = corporate.comprobar_splits(precios, acciones)

    assert len(malos) == 1
    assert malos[0].tipo == "split_mal_aplicado"
    assert "4:1" in malos[0].detalle
    assert "descargar" in malos[0].detalle, "no se dice que hacer"


def test_un_split_aplicado_dos_veces_se_detecta():
    """Tan roto como no aplicarlo, y mas dificil de ver: el precio baja, que es
    lo que se esperaba, solo que demasiado."""
    precios = serie(parte_en=10, ratio=16.0)   # 4:1 aplicado dos veces
    acciones = evento("split", 4.0, 10)

    assert len(corporate.comprobar_splits(precios, acciones)) == 1


def test_el_movimiento_normal_del_dia_no_dispara_el_aviso():
    """Un split se aplica a un precio que ademas se mueve ese dia por razones
    de mercado. Con una tolerancia fina, todos los splits saldrian mal."""
    precios = serie(parte_en=10, ratio=4.0)
    # El dia del split, el precio ademas cae un 5 % por su cuenta.
    dia = INICIO + timedelta(days=10)
    fila = precios["date"] == dia
    precios.loc[fila, "close"] = precios.loc[fila, "close"] * 0.95

    assert corporate.comprobar_splits(precios, acciones=evento("split", 4.0, 10)) == []


def test_un_split_anterior_al_historico_no_se_declara_correcto():
    """Decir que "pasa" algo que no se ha podido mirar es la mentira que este
    proyecto evita en todas partes."""
    precios = serie()
    acciones = evento("split", 4.0, -30)     # antes del primer precio

    assert corporate.comprobar_splits(precios, acciones) == []


def test_se_mira_el_cierre_y_no_el_ajustado():
    """El ajustado reescribe todo el historico hacia atras con cada split, asi
    que ahi el salto NO EXISTE por construccion.

    El escenario es el de un split BIEN aplicado, que es donde se nota la
    diferencia: el cierre da el salto que toca —correcto, no hay hallazgo— y el
    ajustado no da ninguno, lo que desde el ajustado se lee como "el proveedor
    no aplico el split" y produce un falso positivo.

    Mi primera version de este test ponia el ajustado como un multiplo constante
    del cierre, y entonces las dos columnas contaban la misma historia: pasaba
    igual mirando una que otra, y no probaba nada.
    """
    precios = serie(parte_en=10, ratio=4.0)      # el cierre SI se parte
    # Y el ajustado, reescrito hacia atras, es continuo: sin salto ninguno.
    precios["adj_close"] = [100.0] * len(precios)
    acciones = evento("split", 4.0, 10)

    assert corporate.comprobar_splits(precios, acciones) == [], (
        "la comprobacion se ha pasado al ajustado y ahora inventa un fallo "
        "sobre un split que estaba bien aplicado"
    )


def test_sin_splits_no_hay_nada_que_comprobar():
    assert corporate.comprobar_splits(serie(), evento("dividend", 1.2, 5)) == []
    assert corporate.comprobar_splits(serie(), pd.DataFrame(
        columns=["ticker", "date", "action_type", "value"])) == []


# ---------------------------------------------------------------------------
# Retorno del precio contra retorno total
# ---------------------------------------------------------------------------

def test_los_dos_retornos_son_distintos_cuando_hay_dividendos():
    """Confundirlos no da error: da un numero que parece razonable. Una
    electrica que reparte el 6 % lleva seis puntos de diferencia al ano."""
    precios = serie(dias=250, deriva=0.0002)
    precios["adj_close"] = precios["close"] * 0.94   # dividendos descontados

    precio = corporate.retorno_precio(precios)
    total = corporate.retorno_total(precios)

    assert precio == pytest.approx(total), (
        "un factor constante NO cambia el retorno: el escenario esta mal"
    )


def test_el_retorno_total_incluye_lo_cobrado():
    """El escenario de verdad: el ajustado se separa del cierre POR EL CAMINO,
    a medida que se pagan dividendos."""
    precios = serie(dias=100)
    # El ajustado arranca un 5 % por debajo y converge al cierre: es lo que hace
    # Yahoo al descontar hacia atras los dividendos que se van pagando.
    factores = [0.95 + 0.05 * i / 99 for i in range(100)]
    precios["adj_close"] = precios["close"] * factores

    assert corporate.retorno_total(precios) > corporate.retorno_precio(precios)


def test_el_retorno_del_proveedor_tiene_que_cuadrar_con_los_dividendos():
    """La unica comprobacion independiente que se puede hacer del ajustado sin
    una segunda fuente: el `adj_close` y la lista de dividendos los calcula el
    mismo proveedor por caminos distintos."""
    precios = serie(dias=100)
    precios["adj_close"] = precios["close"] * [
        0.95 + 0.05 * i / 99 for i in range(100)
    ]
    # Un dividendo coherente con ese 5 %: 5 sobre un precio de 100.
    acciones = evento("dividend", 5.0, 50)

    assert corporate.comprobar_retorno(precios, acciones, "AAA") is None


def test_un_ajustado_que_no_cuadra_con_los_dividendos_se_detecta():
    precios = serie(dias=100)
    precios["adj_close"] = precios["close"] * [
        0.95 + 0.05 * i / 99 for i in range(100)
    ]
    # El proveedor dice que solo pago 0,10 y su ajustado implica un 5 %.
    hallazgo = corporate.comprobar_retorno(precios, evento("dividend", 0.10, 50),
                                           "AAA")

    assert hallazgo is not None
    assert hallazgo.tipo == "retorno_no_cuadra"


def test_con_un_split_de_por_medio_no_se_compara_el_retorno():
    """Un split parte el `close` y NO el `adj_close`, que se reescribe hacia
    atras. Sin la excepcion, el retorno del precio sale del -75 % y el total
    del 0 %, y esta comprobacion gritaria en cada split de la historia.

    El escenario tiene que llevar el ajustado CONTINUO —que es como llega de
    verdad— o las dos medidas coinciden y el test no prueba nada. Mi primera
    version dejaba `adj_close` igual al cierre partido, y pasaba con la
    excepcion y sin ella.
    """
    precios = serie(dias=100, parte_en=50, ratio=4.0)
    precios["adj_close"] = [100.0] * len(precios)
    acciones = pd.concat([evento("split", 4.0, 50), evento("dividend", 0.1, 20)],
                         ignore_index=True)

    assert corporate.retorno_precio(precios) < -0.5, "el escenario no parte nada"
    assert corporate.retorno_total(precios) == pytest.approx(0.0)

    assert corporate.comprobar_retorno(precios, acciones, "AAA") is None


def test_los_dividendos_de_fuera_de_la_ventana_no_cuentan():
    acciones = pd.concat([evento("dividend", 1.0, -10), evento("dividend", 2.0, 5),
                          evento("dividend", 3.0, 99)], ignore_index=True)

    cobrado = corporate.dividendos_cobrados(
        acciones, "AAA", INICIO, INICIO + timedelta(days=30))

    assert cobrado == pytest.approx(2.0)


def test_una_serie_de_un_solo_dia_no_tiene_retorno():
    assert corporate.retorno_precio(serie(dias=1)) is None


# ---------------------------------------------------------------------------
# Guardar
# ---------------------------------------------------------------------------

@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub


def test_guardar_no_duplica(warehouse):
    """La ingesta reprocesa las mismas fechas todos los dias."""
    eventos = pd.concat([evento("dividend", 1.2, 5), evento("split", 4.0, 10)],
                        ignore_index=True)
    with db.connect() as conn:
        primera = corporate.guardar(conn, eventos)
        segunda = corporate.guardar(conn, eventos)

    assert primera == 2
    assert segunda == 0
    assert len(db.query("SELECT * FROM corporate_actions")) == 2


def test_guardar_admite_que_no_haya_nada(warehouse):
    with db.connect() as conn:
        assert corporate.guardar(conn, None) == 0
        assert corporate.guardar(conn, pd.DataFrame()) == 0


# ---------------------------------------------------------------------------
# Que la descarga los traiga
# ---------------------------------------------------------------------------

def test_la_descarga_pide_los_eventos():
    """Guardarrail. Con `actions=False` la tabla se queda vacia para siempre y
    todo lo de este fichero deja de tener con que trabajar, sin que falle nada.
    """
    from stocks_tracker.core.config import project_root

    src = (project_root()
           / "src/stocks_tracker/providers/yfinance_provider.py").read_text("utf-8")

    # Solo la descarga HISTORICA. La de cotizaciones intradia sigue con
    # `actions=False` y esta bien: un dividendo no es un evento de un minuto.
    historica = src[src.index("def fetch_ohlcv"):src.index("def _reshape")]

    assert "actions=True" in historica
    assert "actions=False" not in historica, (
        "la descarga historica sigue pidiendo los precios sin dividendos ni splits"
    )


def test_los_dias_sin_evento_no_se_guardan():
    """Yahoo sirve las dos columnas a CERO casi todos los dias. Guardarlos
    todos llenaria la tabla de nada y ademas un split se filtra por "> 0", no
    por "distinto de 1": los dias sin split llegan como 0,0."""
    from stocks_tracker.providers.yfinance_provider import _extraer_acciones

    frame = pd.DataFrame({
        "ticker": ["AAA"] * 4,
        "date": [INICIO + timedelta(days=i) for i in range(4)],
        "dividends": [0.0, 1.2, 0.0, 0.0],
        "stock_splits": [0.0, 0.0, 4.0, 0.0],
    })

    salida = _extraer_acciones([frame])

    # Lista de diccionarios y NO un DataFrame: viaja dentro de `df.attrs`, y
    # un DataFrame ahi dentro tumba la descarga entera en el primer merge.
    # Ver tests/test_attrs_no_tumban_la_descarga.py.
    assert len(salida) == 2
    assert {e["action_type"] for e in salida} == {"dividend", "split"}
    assert next(e["value"] for e in salida if e["action_type"] == "split") == 4.0


def test_sin_columnas_de_eventos_no_revienta():
    """Los indices y las divisas no las traen."""
    from stocks_tracker.providers.yfinance_provider import _extraer_acciones

    frame = pd.DataFrame({"ticker": ["^GSPC"], "date": [INICIO], "close": [4000.0]})

    assert _extraer_acciones([frame]) == []
    assert _extraer_acciones([]) == []
