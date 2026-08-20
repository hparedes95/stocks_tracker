"""Twelve Data como tercera fuente. Lo unico que se puede probar sin red.

AVISO QUE VALE PARA TODO EL FICHERO

Estos tests comprueban que el modulo interpreta bien las respuestas que Twelve
Data DOCUMENTA. No comprueban que la API se comporte como su documentacion, y
esa no es una suposicion pequena: los proveedores gratuitos cambian formatos sin
avisar, y ese es literalmente el motivo por el que existe un tercer proveedor.

Por eso el modulo se declara NO COMPROBADO hasta que sirva datos de verdad una
vez, y el panel lo pinta en gris. Un proveedor de contraste en el que no se
puede confiar todavia es peor que no tenerlo, porque su voto cuenta.
"""

from __future__ import annotations

import re

import pytest

from stocks_tracker.providers.base import ProviderError, RateLimitError
from stocks_tracker.providers.twelve_data_provider import (
    TwelveDataProvider,
    interpretar,
)

RESPUESTA_OK = {
    "meta": {"symbol": "AAPL", "interval": "1day", "currency": "USD"},
    "values": [
        {"datetime": "2026-08-19", "open": "230.10", "high": "232.40",
         "low": "229.55", "close": "231.50", "volume": "48213900"},
        {"datetime": "2026-08-18", "open": "228.00", "high": "230.90",
         "low": "227.80", "close": "230.05", "volume": "39112400"},
    ],
    "status": "ok",
}


# ---------------------------------------------------------------------------
# Interpretar la respuesta
# ---------------------------------------------------------------------------

def test_una_respuesta_normal_se_interpreta():
    frame = interpretar(RESPUESTA_OK, "AAPL")

    assert len(frame) == 2
    assert frame.iloc[0]["close"] == pytest.approx(231.50)
    assert frame.iloc[0]["date"] == "2026-08-19"


def test_los_numeros_llegan_como_texto_y_se_convierten():
    """Twelve Data manda los precios entre comillas. Sin convertirlos, "231.50"
    entra en el almacen como texto y cualquier comparacion posterior falla o,
    peor, compara alfabeticamente."""
    frame = interpretar(RESPUESTA_OK, "AAPL")

    assert frame["close"].dtype.kind == "f"


def test_el_ajustado_se_copia_del_cierre():
    """El plan gratuito no da precio ajustado. Se copia para cumplir el
    esquema, igual que Stooq, y por eso el consenso compara `close`: este
    `adj_close` no es comparable con el de Yahoo."""
    frame = interpretar(RESPUESTA_OK, "AAPL")

    assert (frame["adj_close"] == frame["close"]).all()


# ---------------------------------------------------------------------------
# El error que viene con codigo 200
# ---------------------------------------------------------------------------

def test_un_error_dentro_de_un_200_no_se_lee_como_respuesta_vacia():
    """LA trampa de esta API. Devuelve los errores DENTRO del JSON con HTTP 200.

    Fiarse del codigo HTTP hace que un "has agotado la cuota" se lea como "no
    hay datos", y una cuota agotada tratada como respuesta vacia es un
    DEGRADADO silencioso justo cuando hacia falta el contraste.
    """
    error = {"code": 400, "message": "symbol not found", "status": "error"}

    with pytest.raises(ProviderError, match="symbol not found"):
        interpretar(error, "NOEXISTE")


def test_la_cuota_agotada_se_distingue_de_los_demas_errores():
    """Insistir cuando ya estan limitando gasta la cuota del dia siguiente."""
    error = {"code": 429, "message": "API credits limit reached", "status": "error"}

    with pytest.raises(RateLimitError):
        interpretar(error, "AAPL")


def test_una_respuesta_sin_valores_es_vacia_y_no_un_error():
    """Un dia sin sesion no es un fallo del proveedor."""
    assert interpretar({"status": "ok", "values": []}, "AAPL").empty


def test_una_respuesta_sin_la_columna_de_fecha_se_rechaza():
    """Sin fecha no hay nada que hacer con esas filas, y aceptarlas en silencio
    dejaria un DataFrame que revienta mas adelante y mas lejos."""
    with pytest.raises(ProviderError, match="datetime"):
        interpretar({"status": "ok", "values": [{"close": "1"}]}, "AAPL")


def test_una_respuesta_que_no_es_un_objeto_se_rechaza():
    with pytest.raises(ProviderError):
        interpretar(["no", "esperado"], "AAPL")


# ---------------------------------------------------------------------------
# Sin clave
# ---------------------------------------------------------------------------

def test_sin_clave_el_proveedor_no_dice_soportar_nada(monkeypatch):
    """Asi la cadena y la auditoria lo saltan sin gastar una peticion."""
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    p = TwelveDataProvider()

    assert not p.configurado
    assert not p.supports("AAPL")


def test_sin_clave_se_dice_que_falta_y_no_se_finge_un_fallo_de_red(monkeypatch):
    """Un "no responde" mandaria a mirar la conexion cuando lo que falta es una
    linea en el .env."""
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)

    with pytest.raises(ProviderError, match="TWELVE_DATA_API_KEY"):
        TwelveDataProvider().fetch_ohlcv(["AAPL"], __import__("datetime").date(2026, 8, 1),
                                         __import__("datetime").date(2026, 8, 20))


def test_con_clave_si_soporta_acciones(monkeypatch):
    monkeypatch.setenv("TWELVE_DATA_API_KEY", "una-clave")
    p = TwelveDataProvider()

    assert p.configurado
    assert p.supports("AAPL")
    assert not p.supports("^GSPC"), "los indices gastan cuota para dar error"
    assert not p.supports("EURUSD=X")


# ---------------------------------------------------------------------------
# Configurado no es comprobado
# ---------------------------------------------------------------------------

def test_una_clave_escrita_no_demuestra_que_la_api_funcione(monkeypatch):
    """La distincion que gobierna todo este modulo. Una clave en el .env no
    demuestra que la API responda, ni que su formato siga siendo el que este
    codigo entiende. `ha_respondido` es lo unico que lo demuestra."""
    monkeypatch.setenv("TWELVE_DATA_API_KEY", "una-clave")
    p = TwelveDataProvider()

    assert p.configurado
    assert not p.ha_respondido


def test_la_clave_nunca_sale_del_entorno():
    """Guardarrail. `config/` esta en el repositorio y el repositorio es publico.

    Lo que se prohibe es que la clave tenga VALOR en un YAML y que el codigo la
    lea de ahi. Nombrar la variable en un comentario —para decir precisamente
    que no va ahi— es lo contrario de un problema, y la primera version de este
    test lo trataba como uno.
    """
    from stocks_tracker.core.config import project_root

    raiz = project_root()
    con_valor = []
    for ruta in (raiz / "config").rglob("*.yaml"):
        for n, linea in enumerate(ruta.read_text("utf-8").splitlines(), 1):
            texto = linea.strip()
            if texto.startswith("#"):
                continue
            if re.search(r"TWELVE_DATA_API_KEY\s*[:=]\s*\S", texto):
                con_valor.append(f"{ruta.name}:{n}")

    assert not con_valor, f"la clave tiene valor en el repositorio: {con_valor}"

    src = (raiz / "src/stocks_tracker/providers/twelve_data_provider.py").read_text("utf-8")
    assert "os.environ.get" in src, "la clave ya no se lee del entorno"
    assert "get_settings" not in src, "la clave se lee del YAML"


# ---------------------------------------------------------------------------
# Y el panel lo dice
# ---------------------------------------------------------------------------

def test_el_panel_distingue_configurado_de_comprobado(tmp_path, monkeypatch):
    """Un proveedor con la clave escrita y que nunca ha servido una fila no
    puede salir en verde. Su voto CUENTA en el consenso: uno que se declara
    disponible y devuelve basura no es neutral, empuja veredictos."""
    from datetime import date

    import pandas as pd

    from stocks_tracker.core import db, integrity

    class Stub:
        warehouse_path = tmp_path / "t.duckdb"
        compute: dict = {}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    monkeypatch.setenv("TWELVE_DATA_API_KEY", "una-clave")
    db.migrate()
    with db.connect() as conn:
        db.upsert_df(conn, "prices_daily", pd.DataFrame([{
            "ticker": "AAA", "date": date(2026, 8, 20), "close": 100.0,
            "adj_close": 100.0, "open": 99.0, "high": 101.0, "low": 98.0,
            "volume": 1000, "source": "yfinance"}]), keys=["ticker", "date"])
        puntos = integrity.revisar(conn)

    p = [x for x in puntos if x.nombre == "Proveedores de datos"][0]

    assert p.estado == integrity.AVISO
    assert "SIN COMPROBAR" in p.detalle
    assert "twelve_data" in p.detalle


def test_sin_clave_el_panel_no_menciona_twelve_data(tmp_path, monkeypatch):
    """El contrario: un proveedor que ni siquiera esta configurado no es un
    pendiente. Un aviso que sale siempre entrena a ignorar los avisos."""
    from datetime import date

    import pandas as pd

    from stocks_tracker.core import db, integrity

    class Stub:
        warehouse_path = tmp_path / "t2.duckdb"
        compute: dict = {}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    db.migrate()
    with db.connect() as conn:
        db.upsert_df(conn, "prices_daily", pd.DataFrame([{
            "ticker": "AAA", "date": date(2026, 8, 20), "close": 100.0,
            "adj_close": 100.0, "open": 99.0, "high": 101.0, "low": 98.0,
            "volume": 1000, "source": "yfinance"}]), keys=["ticker", "date"])
        puntos = integrity.revisar(conn)

    p = [x for x in puntos if x.nombre == "Proveedores de datos"][0]

    assert p.estado == integrity.BIEN
    assert "twelve_data" not in p.detalle
