"""Leer y escribir el diario de decisiones.

Unico modulo que toca `decision_journal`. Lo escrito NO se modifica nunca: la
revision anade columnas aparte. Si se pudiera editar la tesis despues de saber
el resultado, el diario dejaria de servir para lo unico que sirve —y se
editaria, porque la tentacion de "aclarar lo que queria decir" es exactamente
el sesgo que esto viene a frenar—.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

from ..core.db import connect, migrate
from ..core.ids import ulid
from ..core.journal import Accion, Entrada, Veredicto
from ..core.timeutils import utcnow

# Proxy de mercado contra el que se descuenta la marea al revisar. El mismo que
# usa la atribucion: dos referencias distintas darian dos veredictos distintos
# sobre la misma decision.
MERCADO = "SPY"

CAMPOS = (
    "id", "created_at", "ticker", "accion", "tesis", "que_me_haria_salir",
    "horizonte_dias", "conviccion", "precio", "precio_mercado",
    "composite_pctile", "rsi14", "drawdown", "above_sma200",
    "revisado_at", "veredicto", "nota_revision",
)


def _asegurar_tabla() -> None:
    """Crea la tabla si el almacen es anterior a esta version.

    `migrate()` es idempotente y barato. Sin esto, quien actualice el programa
    sin volver a descargar datos abriria el diario y veria un error de tabla
    inexistente, que no explica nada.
    """
    migrate()


@st.cache_data(ttl=30, show_spinner=False)
def leer() -> pd.DataFrame:
    """Todo el diario, lo mas reciente primero.

    Si la tabla todavia no existe se devuelve vacio en vez de reventar: la
    página dira que aun no hay decisiones, que es la verdad.
    """
    try:
        with connect(read_only=True) as conn:
            return conn.execute(
                f"SELECT {', '.join(CAMPOS)} FROM decision_journal "
                "ORDER BY created_at DESC"
            ).fetchdf()
    except Exception:  # noqa: BLE001 — almacen viejo o sin crear
        return pd.DataFrame(columns=list(CAMPOS))


def _texto(valor) -> str:
    return "" if valor is None or pd.isna(valor) else str(valor)


def _numero(valor) -> float | None:
    if valor is None or pd.isna(valor):
        return None
    return float(valor)


def a_entradas(datos: pd.DataFrame) -> list[Entrada]:
    """Pasa las filas al tipo del nucleo, saltando las que no se entienden.

    Una accion o un veredicto desconocidos —de una version futura, o de una
    edicion a mano— no pueden tumbar la página entera.
    """
    fuera: list[Entrada] = []
    for _, fila in datos.iterrows():
        try:
            accion = Accion(_texto(fila.get("accion")))
        except ValueError:
            continue
        crudo = _texto(fila.get("veredicto"))
        try:
            veredicto = Veredicto(crudo) if crudo else None
        except ValueError:
            veredicto = None
        creada = fila.get("created_at")
        fuera.append(Entrada(
            id=_texto(fila.get("id")),
            created_at=(creada.to_pydatetime() if hasattr(creada, "to_pydatetime")
                        else creada),
            ticker=_texto(fila.get("ticker")),
            accion=accion,
            tesis=_texto(fila.get("tesis")),
            que_me_haria_salir=_texto(fila.get("que_me_haria_salir")),
            horizonte_dias=int(_numero(fila.get("horizonte_dias")) or 0),
            conviccion=int(_numero(fila.get("conviccion")) or 3),
            precio=_numero(fila.get("precio")),
            precio_mercado=_numero(fila.get("precio_mercado")),
            veredicto=veredicto,
            nota_revision=_texto(fila.get("nota_revision")),
        ))
    return fuera


def anotar(*, ticker: str, accion: Accion, tesis: str,
           que_me_haria_salir: str = "", horizonte_dias: int = 90,
           conviccion: int = 3, foto: dict | None = None) -> str:
    """Guarda una decision con la foto del momento.

    `foto` la rellena quien llama con lo que hay en pantalla ese dia; no se
    pide por teclado a proposito, porque lo que se recuerda haber sabido no es
    lo que se sabia.
    """
    _asegurar_tabla()
    foto = foto or {}
    # ULID y no UUID: ordena por tiempo, asi que el propio identificador dice
    # en que orden se tomaron las decisiones aunque se pierda la fecha.
    nuevo = ulid()
    with connect() as conn:
        conn.execute(
            "INSERT INTO decision_journal "
            "(id, created_at, ticker, accion, tesis, que_me_haria_salir, "
            " horizonte_dias, conviccion, precio, precio_mercado, "
            " composite_pctile, rsi14, drawdown, above_sma200) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [nuevo, utcnow(), ticker, str(accion), tesis, que_me_haria_salir,
             int(horizonte_dias), int(conviccion),
             foto.get("precio"), foto.get("precio_mercado"),
             foto.get("composite_pctile"), foto.get("rsi14"),
             foto.get("drawdown"), foto.get("above_sma200")],
        )
    leer.clear()
    return nuevo


def revisar(entrada_id: str, veredicto: Veredicto, nota: str = "") -> None:
    """Anade la revision SIN tocar lo que se escribio en su dia.

    El `UPDATE` toca solo las tres columnas de revision. Poder reescribir la
    tesis despues de conocer el resultado convertiria el diario en una cronica
    de aciertos, que es en lo que se convierte la memoria si se la deja.
    """
    _asegurar_tabla()
    with connect() as conn:
        conn.execute(
            "UPDATE decision_journal SET revisado_at = ?, veredicto = ?, "
            "nota_revision = ? WHERE id = ?",
            [utcnow(), str(veredicto), nota, entrada_id],
        )
    leer.clear()


def precios_para(entradas: list[Entrada], mercado: str = MERCADO) -> dict:
    """Los precios que hacen falta para calcular resultados, mercado incluido.

    El proxy de mercado casi nunca está en el diario —nadie anota una decision
    sobre SPY—, asi que pedir solo los tickers anotados dejaba la comparacion
    contra el mercado sin precio y esa metrica no aparecia NUNCA en pantalla.
    No daba error: simplemente faltaba.
    """
    return precios_hoy(tuple(sorted({e.ticker for e in entradas} | {mercado})))


@st.cache_data(ttl=60, show_spinner=False)
def precios_hoy(tickers: tuple[str, ...]) -> dict:
    """Ultimo precio de cada valor del diario, para calcular el resultado."""
    if not tickers:
        return {}
    huecos = ", ".join("?" for _ in tickers)
    with connect(read_only=True) as conn:
        filas = conn.execute(
            f"""
            SELECT ticker, LAST(adj_close ORDER BY date) AS cierre
            FROM prices_daily WHERE ticker IN ({huecos})
            GROUP BY ticker
            """,
            list(tickers),
        ).fetchall()
    return {t: float(c) for t, c in filas if c is not None}


def _hash_estilo() -> str:
    """El estilo de puntuacion vigente.

    Toda consulta a `factor_scores` tiene que filtrar por el: los scores de
    todos los estilos conviven en la misma tabla y sin el filtro cada valor
    aparece una vez por estilo. Con `LIMIT 1` no se duplicaban filas, que es
    peor: se guardaba el percentil de un estilo cualquiera y la decision
    quedaba anotada con un dato que no era el que se estaba mirando.
    """
    from . import data_access as da

    return da._preset_hash(None)


def foto_de(ticker: str, mercado: str = "SPY") -> dict:
    """Lo que se sabe hoy de un valor, para guardarlo con la decision."""
    try:
        with connect(read_only=True) as conn:
            fila = conn.execute(
                """
                SELECT i.close AS precio, i.rsi14, i.drawdown, i.above_sma200,
                       f.composite_pctile
                FROM indicators_daily i
                LEFT JOIN factor_scores f
                       ON f.ticker = i.ticker AND f.date = i.date
                      AND f.weights_hash = ?
                WHERE i.ticker = ?
                ORDER BY i.date DESC LIMIT 1
                """,
                [_hash_estilo(), ticker],
            ).fetchdf()
            bench = conn.execute(
                "SELECT LAST(adj_close ORDER BY date) FROM prices_daily "
                "WHERE ticker = ?", [mercado],
            ).fetchone()
    except Exception:  # noqa: BLE001 — sin datos, se guarda la decision igual
        return {}

    foto: dict = {"precio_mercado": float(bench[0]) if bench and bench[0] else None}
    if not fila.empty:
        f = fila.iloc[0]
        foto.update({
            "precio": _numero(f.get("precio")),
            "rsi14": _numero(f.get("rsi14")),
            "drawdown": _numero(f.get("drawdown")),
            "composite_pctile": _numero(f.get("composite_pctile")),
            "above_sma200": (None if pd.isna(f.get("above_sma200"))
                             else bool(f.get("above_sma200"))),
        })
    return foto


def hoy() -> date:
    return datetime.now().date()
