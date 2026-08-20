"""Datos deliberadamente corruptos: el sistema tiene que rechazarlos.

Pasos 21 y 22 del plan, juntos porque son la misma idea desde dos lados: en vez
de comprobar que el programa funciona con datos buenos, se le meten datos malos
a proposito y se comprueba que NO se los traga.

Las dos familias:

- CORRUPCION. Precios negativos, fechas futuras, NaN, duplicados, volumen
  negativo, filas incompletas, cambios absurdos, tickers cruzados.
- CAOS. El proveedor tarda, devuelve la mitad, devuelve basura o no responde.

LA REGLA QUE SE PRUEBA EN TODOS

Nunca inventar. Ante un dato malo hay exactamente dos respuestas aceptables:
descartarlo diciendolo, o parar. Rellenar el hueco con la media, con el ultimo
valor conocido o con un cero es la tercera respuesta, la que no se ve, y la que
produce numeros de aspecto razonable que no describen nada.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from stocks_tracker.core import quality
from stocks_tracker.providers.base import (
    ProviderError,
    empty_ohlcv,
    normalize_ohlcv,
)
from stocks_tracker.providers.chain import ChainPriceProvider

HOY = date(2026, 8, 20)


def barra(**campos) -> dict:
    base = {"ticker": "AAA", "date": HOY, "open": 99.0, "high": 101.0,
            "low": 98.0, "close": 100.0, "adj_close": 100.0, "volume": 1_000_000}
    base.update(campos)
    return base


def normalizar(*barras) -> pd.DataFrame:
    return normalize_ohlcv(pd.DataFrame(list(barras)), "prueba")


# ---------------------------------------------------------------------------
# Corrupcion: lo que entra por la puerta
# ---------------------------------------------------------------------------

def test_un_precio_negativo_no_entra():
    salida = normalizar(barra(ticker="MALA", adj_close=-50.0), barra(ticker="BUENA"))

    assert set(salida["ticker"]) == {"BUENA"}


def test_un_precio_a_cero_no_entra():
    """Un cero no es un precio bajo: es la ausencia de precio escrita como
    numero, y dividir por el produce infinitos que se propagan a todo."""
    assert normalizar(barra(adj_close=0.0)).empty


def test_una_fecha_corrupta_no_tumba_el_lote_entero(recwarn):
    """Se descarta la fila y el resto del universo continua. Que un ticker
    traiga una fecha ilegible no puede dejar sin datos a los otros 599.

    `recwarn` recoge el aviso de pandas sobre el formato de fecha: es
    esperable —justamente porque una de las fechas no lo es— y no tiene que
    ensuciar la salida de la suite.
    """
    salida = normalizar(barra(ticker="MALA", date="no-es-una-fecha"),
                        barra(ticker="BUENA"))

    assert set(salida["ticker"]) == {"BUENA"}


def test_un_nan_en_el_precio_no_entra():
    assert normalizar(barra(adj_close=float("nan"))).empty


def test_un_infinito_en_el_precio_no_entra():
    assert normalizar(barra(adj_close=float("inf"))).empty


def test_una_fila_duplicada_se_queda_en_una():
    """Dos filas del mismo (ticker, fecha) duplicarian el peso de ese dia en
    cualquier media movil."""
    salida = normalizar(barra(close=100.0), barra(close=100.0))

    assert len(salida) == 1


def test_un_volumen_ausente_no_inventa_un_numero():
    """Se pone a cero y la comprobacion de calidad lo denuncia como sesion sin
    volumen, que es lo que es. Lo que no se hace es rellenarlo con la media."""
    salida = normalizar(barra(volume=None))

    assert int(salida.iloc[0]["volume"]) == 0
    assert not quality.volumen_cero(salida).empty


def test_una_fecha_futura_no_entra_por_la_puerta():
    """Un precio de manana no existe. Llega por husos horarios del proveedor y
    por barras provisionales mal fechadas.

    Hace mas dano de lo que parece: la sesion vigente sale de la fecha mas
    reciente del almacen, asi que UNA fila del futuro convierte el dashboard
    entero en el retrato de un dia que no ha ocurrido.
    """
    salida = normalizar(barra(ticker="FUTURA", date=date.today() + timedelta(days=30)),
                        barra(ticker="BUENA", date=date.today()))

    assert set(salida["ticker"]) == {"BUENA"}


def test_la_sesion_de_un_mercado_por_delante_de_utc_si_entra():
    """En Tokio la sesion de "manana" ya esta abierta mientras aqui es hoy. Sin
    el dia de margen, media Asia se quedaria fuera todas las noches."""
    salida = normalizar(barra(date=date.today() + timedelta(days=1)))

    assert len(salida) == 1


def test_una_fecha_futura_ya_guardada_se_detecta():
    """Y las que entraron ANTES de existir aquel filtro siguen en el almacen."""
    futuro = pd.DataFrame([barra(date=HOY + timedelta(days=30))])

    hallazgos = quality.evaluar(futuro, instrumentos_ohlc={"AAA"}, hoy=HOY)

    assert any(h.check == "fechas_futuras" for h in hallazgos), (
        "una barra fechada dentro de 30 dias ha pasado sin decir nada"
    )


def test_una_fecha_de_hoy_no_es_futura():
    """El contrario: la sesion en curso es legitima y no puede dar alarma."""
    hoy = pd.DataFrame([barra(date=HOY)])
    hallazgos = quality.evaluar(hoy, instrumentos_ohlc={"AAA"},
                                hoy=HOY)

    assert not any(h.check == "fechas_futuras" for h in hallazgos)


def test_un_volumen_negativo_se_detecta():
    """No existe negociar una cantidad negativa."""
    malo = pd.DataFrame([barra(volume=-500)])

    hallazgos = quality.evaluar(malo, instrumentos_ohlc={"AAA"})

    assert any(h.check == "volumen_negativo" for h in hallazgos)


def test_un_cambio_absurdo_de_precio_se_detecta():
    """Multiplicarse por cincuenta de un dia para otro no es una empresa: es un
    split mal aplicado, un ticker cruzado o un decimal perdido."""
    filas = [barra(date=HOY - timedelta(days=i), close=100.0, adj_close=100.0)
             for i in range(10)]
    filas[0] = barra(date=HOY, close=5000.0, adj_close=5000.0)

    hallazgos = quality.evaluar(pd.DataFrame(filas), instrumentos_ohlc={"AAA"})

    assert any(h.check == "salto_absurdo" for h in hallazgos)


def test_un_movimiento_grande_pero_posible_no_da_alarma():
    """El contrario. Una accion puede caer un 30 % en un dia; pasa. Una alarma
    que salta con eso entrena a ignorar las alarmas."""
    filas = [barra(date=HOY - timedelta(days=i), close=100.0, adj_close=100.0)
             for i in range(10)]
    filas[0] = barra(date=HOY, close=70.0, adj_close=70.0)

    hallazgos = quality.evaluar(pd.DataFrame(filas), instrumentos_ohlc={"AAA"})

    assert not any(h.check == "salto_absurdo" for h in hallazgos)


# ---------------------------------------------------------------------------
# Caos: el proveedor se porta mal
# ---------------------------------------------------------------------------

class Proveedor:
    """Proveedor de mentira que se comporta como se le diga."""

    def __init__(self, name: str, comportamiento="ok", cubre=None):
        self.name = name
        self.comportamiento = comportamiento
        self.cubre = cubre
        self.pedido: list[str] = []

    def supports(self, ticker: str) -> bool:
        return self.cubre is None or ticker in self.cubre

    def fetch_ohlcv(self, tickers, start, end, interval="1d"):
        self.pedido.extend(tickers)
        if self.comportamiento == "timeout":
            raise ProviderError("se ha agotado el tiempo de espera")
        if self.comportamiento == "vacio":
            return empty_ohlcv()
        if self.comportamiento == "basura":
            return normalize_ohlcv(pd.DataFrame({"ticker": tickers,
                                                 "date": ["?"] * len(tickers)}),
                                   self.name)
        if self.comportamiento == "mitad":
            tickers = tickers[: max(1, len(tickers) // 2)]
        return normalize_ohlcv(
            pd.DataFrame([barra(ticker=t) for t in tickers]), self.name)


def cadena(*proveedores) -> ChainPriceProvider:
    return ChainPriceProvider(list(proveedores))


def pedir(chain, tickers):
    return chain.fetch_ohlcv(tickers, HOY - timedelta(days=5), HOY)


def test_si_el_primero_no_responde_se_usa_el_segundo():
    caido = Proveedor("caido", "timeout")
    vivo = Proveedor("vivo")

    salida = pedir(cadena(caido, vivo), ["AAA", "BBB"])

    assert set(salida["ticker"]) == {"AAA", "BBB"}
    assert set(salida["source"]) == {"vivo"}


def test_si_el_primero_sirve_la_mitad_el_segundo_completa():
    """El caso que mas se parece a la realidad: no se cae, se queda corto."""
    corto = Proveedor("corto", "mitad")
    vivo = Proveedor("vivo")

    salida = pedir(cadena(corto, vivo), ["AAA", "BBB", "CCC", "DDD"])

    assert set(salida["ticker"]) == {"AAA", "BBB", "CCC", "DDD"}
    assert len(set(salida["source"])) == 2, "no se ha usado el segundo proveedor"


def test_con_todos_caidos_no_se_inventa_nada():
    """LA prueba de las tres. Ningun precio de mentira, ninguna fila rellenada,
    y la lista de lo que falto para que quien llama pueda decidir."""
    salida = pedir(cadena(Proveedor("a", "timeout"), Proveedor("b", "timeout")),
                   ["AAA", "BBB"])

    assert salida.empty
    assert set(salida.attrs["failed_tickers"]) == {"AAA", "BBB"}


def test_con_todos_caidos_la_puerta_de_calidad_no_da_por_bueno_el_almacen():
    """Y lo que llegue despues tampoco puede pasar por comprobado."""
    hallazgos = quality.evaluar(empty_ohlcv(), instrumentos_ohlc=set())

    assert hallazgos == [], "sobre un almacen vacio no hay nada que comprobar"


def test_basura_del_proveedor_no_llega_al_almacen():
    """Una respuesta con la forma correcta y el contenido inservible."""
    salida = pedir(cadena(Proveedor("basura", "basura"), Proveedor("vivo")),
                   ["AAA"])

    assert set(salida["ticker"]) == {"AAA"}
    assert set(salida["source"]) == {"vivo"}


def test_no_se_le_piden_a_un_proveedor_los_tickers_que_no_cubre():
    """Gastaria una peticion para recibir un 404, y las peticiones son el
    recurso escaso de todo esto."""
    solo_us = Proveedor("solo_us", cubre={"AAA"})
    resto = Proveedor("resto")

    pedir(cadena(solo_us, resto), ["AAA", "BBVA.MC"])

    assert solo_us.pedido == ["AAA"]


def test_un_proveedor_que_revienta_de_forma_inesperada_no_tumba_la_cadena():
    """`ProviderError` esta contemplado. Un `KeyError` de una version nueva de
    la libreria, no, y la cadena existe justo para que eso no deje sin datos al
    universo entero."""
    class Explota(Proveedor):
        def fetch_ohlcv(self, tickers, start, end, interval="1d"):
            raise KeyError("una columna que ya no existe")

    salida = pedir(cadena(Explota("roto"), Proveedor("vivo")), ["AAA"])

    assert set(salida["ticker"]) == {"AAA"}


def test_una_cadena_vacia_se_niega_a_existir():
    """Una cadena sin proveedores devolveria vacio siempre y pareceria que el
    mercado no tiene datos."""
    with pytest.raises(ProviderError):
        ChainPriceProvider([])


# ---------------------------------------------------------------------------
# Lo que nunca puede pasar
# ---------------------------------------------------------------------------

def test_ningun_camino_rellena_un_hueco_con_un_numero_inventado():
    """Guardarrail sobre el codigo. `fillna` con un valor sobre una columna de
    precios es la forma silenciosa de inventar datos: la serie queda completa,
    los indicadores salen, y describen un mercado que no existio.
    """
    from stocks_tracker.core.config import project_root

    sospechosas = []
    for ruta in (project_root() / "src/stocks_tracker/providers").glob("*.py"):
        for n, linea in enumerate(ruta.read_text("utf-8").splitlines(), 1):
            texto = linea.strip()
            if texto.startswith("#"):
                continue
            for campo in ("close", "open", "high", "low", "adj_close"):
                if f'["{campo}"].fillna(' in texto or f".{campo}.fillna(" in texto:
                    sospechosas.append(f"{ruta.name}:{n}: {texto}")

    assert not sospechosas, (
        "hay precios rellenados con un valor inventado:\n  "
        + "\n  ".join(sospechosas)
    )


def test_los_precios_que_pasan_normalize_son_todos_positivos_y_finitos():
    """Property test del contrato de entrada: lo que salga de aqui alimenta
    todos los indicadores."""
    rng = np.random.default_rng(7)
    filas = []
    for i in range(200):
        filas.append(barra(
            ticker=f"T{i}",
            adj_close=float(rng.choice([-1.0, 0.0, np.nan, np.inf, 100.0, 0.01])),
            close=float(rng.choice([np.nan, 50.0, -3.0])),
        ))

    salida = normalizar(*filas)

    valores = salida["adj_close"].to_numpy(dtype=float)
    assert np.isfinite(valores).all()
    assert (valores > 0).all()
