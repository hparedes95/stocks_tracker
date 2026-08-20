"""Piezas de interfaz compartidas: aviso legal, filtros, tarjetas, tablas."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ... import DISCLAIMER
from ...core.explain import Reasons
from .. import data_access as da
from .theme import STATUS, format_market_cap, format_pct, palette


def render_disclaimer(compact: bool = True) -> None:
    """Pie legal. Va en TODAS las páginas, sin excepción."""
    if compact:
        st.caption(f":grey[{DISCLAIMER}]")
    else:
        st.info(DISCLAIMER, icon=":material/info:")


def render_freshness_badge() -> None:
    """Estado de los datos. Si están viejos hay que decirlo, no disimularlo."""
    if da.data_origin()["synthetic"]:
        # El banner grande queda arriba y desaparece al bajar; esto acompana
        # siempre.
        st.error("DATOS DE PRUEBA", icon=":material/science:")
    info = da.data_freshness()
    last_date = info["last_price_date"]
    if last_date is None:
        st.warning(
            "No hay datos cargados. Ejecuta `make ingest` (datos reales) o "
            "`make ingest-demo` (datos sintéticos) y después `make compute`.",
            icon=":material/database_off:",
        )
        return

    hours = info["hours_since_run"]
    detail = f"Datos hasta el {last_date:%d/%m/%Y}"
    if hours is not None:
        detail += f" · última actualización hace {hours:.0f} h"

    if info["is_stale"]:
        st.warning(f"{detail}. Conviene actualizar.", icon=":material/schedule:")
    elif info["failures"]:
        st.caption(f":orange[{detail} · {info['failures']} descargas fallidas]")
    else:
        st.caption(f":grey[{detail}]")


def render_data_origin_banner() -> None:
    """Aviso permanente cuando los precios NO son reales.

    Va en `main.py`, fuera de la navegación, para que salga en TODAS las
    páginas y no se pueda cerrar. Es deliberadamente aparatoso: un número
    inventado con el mismo aspecto que uno real es peor que no tener número,
    porque invita a decidir con el.
    """
    origin = da.data_origin()

    if origin["empty"]:
        st.warning(
            "**No hay datos cargados.** Ejecuta la ingesta antes de usar el "
            "dashboard.",
            icon=":material/database_off:",
        )
        return

    if not origin["synthetic"]:
        return

    share = origin["synthetic_share"]
    mixed = share < 0.999

    st.error(
        (
            f"### DATOS DE PRUEBA — {'parte de los precios son' if mixed else 'los precios son'} INVENTADOS\n\n"
            + (f"Un **{share:.0%}** de los precios los ha generado el simulador, "
               "no el mercado. " if mixed else
               "Ninguno de los precios que ves viene del mercado real: los ha "
               "generado el simulador para que puedas probar la aplicación sin "
               "esperar la primera descarga. ")
            + "No coinciden con la realidad y **no sirven para decidir nada**.\n\n"
            "Para cargar precios reales, en la carpeta del programa:\n\n"
            "```\n"
            "Windows:  .\\scripts\\windows\\stocks.ps1 ingest\n"
            "          .\\scripts\\windows\\stocks.ps1 compute\n"
            "\n"
            "macOS:    make ingest && make compute\n"
            "```\n\n"
            "La primera descarga tarda varios minutos."
        ),
        icon=":material/science:",
    )


def render_integrity_badge(target) -> None:
    """Semaforo de integridad en la barra lateral, visible desde cualquier pagina.

    Va aqui y no solo en la pagina de estado porque el problema que resuelve es
    justo que nadie entra en esa pagina. Un rojo tiene que verse mientras miras
    el ranking, que es cuando estas a punto de decidir algo.

    Solo dice el estado y adonde ir. El detalle esta en su pagina: un panel
    completo en la barra lateral seria ilegible y competiria con lo que el
    usuario habia venido a mirar.
    """
    from stocks_tracker.core import integrity
    from stocks_tracker.core.db import connect

    try:
        with connect(read_only=True) as conn:
            puntos = integrity.revisar(conn)
    except Exception:  # noqa: BLE001
        # Un almacen que todavia no existe no es un fallo que merezca una
        # excepcion en pantalla: es la primera ejecucion.
        return

    veredicto = integrity.veredicto(puntos)
    if veredicto == integrity.BIEN:
        st.page_link(target, label="Integridad: todo comprobado",
                     icon=":material/verified_user:")
        return

    sin_verde = integrity.pendientes(puntos)
    etiqueta = "punto" if len(sin_verde) == 1 else "puntos"
    st.page_link(
        target,
        label=f"{integrity.SEMAFORO[veredicto]} Integridad: {len(sin_verde)} "
              f"{etiqueta} que revisar",
        icon=":material/shield:",
    )


def render_pending_alerts_badge(target) -> None:
    """Avisos sin revisar. Solo aparece si hay alguno: un contador a cero es ruido.

    `target` es el objeto `st.Page` de la página de alertas, no su ruta: con
    `st.navigation`, `st.page_link` solo entiende páginas ya registradas.
    """
    pending = da.count_pending_alerts()
    if not pending:
        return
    label = "aviso sin revisar" if pending == 1 else "avisos sin revisar"
    st.page_link(
        target, label=f"{pending} {label}", icon=":material/notifications_active:"
    )


def sidebar_filters(key_prefix: str = "") -> dict:
    """Filtros comunes de la barra lateral."""
    with st.sidebar:
        st.subheader("Filtros")
        universes = da.universe_options()
        universe = st.selectbox(
            "Mercado",
            options=list(universes),
            format_func=lambda k: universes[k],
            key=f"{key_prefix}_universe",
        )
        sectors = st.multiselect(
            "Sectores",
            options=da.get_sectors(),
            default=[],
            help="Vacío = todos los sectores",
            key=f"{key_prefix}_sectors",
        )
    return {"universe": universe, "sectors": tuple(sectors)}


def metric_row(items: list[dict], columns: int = 5) -> None:
    """Fila de indicadores clave.

    Cada variación lleva signo explicito: el color refuerza, no comunica.
    """
    cols = st.columns(min(columns, max(1, len(items))))
    for i, item in enumerate(items):
        with cols[i % len(cols)]:
            st.metric(
                label=item["label"],
                value=item["value"],
                delta=item.get("delta"),
                help=item.get("help"),
            )


def render_reasons(
    reasons: Reasons, show_signals: bool = True,
    evidence: dict[str, str] | None = None,
    signal_ids: list[str] | None = None,
) -> None:
    """Motivos a favor y en contra, en lenguaje llano.

    Si se pasa `evidence`, las señales sin validación histórica se muestran
    apagadas. Una señal que no ha demostrado nada no puede presentarse igual que
    una que si: sería dar la misma autoridad a las dos.
    """
    if reasons.pros:
        st.markdown("**A favor**")
        for phrase in reasons.pros:
            st.markdown(f"<span style='color:{STATUS['good']}'>✓</span> {phrase}",
                        unsafe_allow_html=True)
    if reasons.cons:
        st.markdown("**A vigilar**")
        for phrase in reasons.cons:
            st.markdown(f"<span style='color:{STATUS['warning']}'>▲</span> {phrase}",
                        unsafe_allow_html=True)
    if show_signals and reasons.signals:
        st.markdown("**Señales activas hoy**")
        st.markdown(render_signal_chips(reasons.signals, evidence, signal_ids),
                    unsafe_allow_html=True)


def render_signal_chips(
    names: list[str], evidence: dict[str, str] | None = None,
    signal_ids: list[str] | None = None,
) -> str:
    """Señales con su estado de validación.

    Sin validación histórica, una señal es una observación, no una razón. Se
    marcan en gris con un aviso para que no pesen igual que las validadas.
    """
    if not names:
        return "—"
    if not evidence or not signal_ids or len(signal_ids) != len(names):
        return " · ".join(f"`{n}`" for n in names)

    muted = palette()["muted"]
    parts = []
    for name, signal_id in zip(names, signal_ids, strict=False):
        label = evidence.get(signal_id)
        if label == "validada":
            parts.append(
                f"<span style='color:{STATUS['good']}'>●</span> {name}"
            )
        else:
            note = {
                "debil": "evidencia debil",
                "no_validada": "sin evidencia histórica",
                "sin_datos": "muestra insuficiente",
            }.get(label, "sin validar")
            parts.append(
                f"<span style='color:{muted}' title='{note}'>○ {name} "
                f"<em style='font-size:0.85em'>({note})</em></span>"
            )
    return "<br>".join(parts)


def render_flags(flags: list[str]) -> None:
    """Banderas rojas. Se muestran aunque el score sea alto."""
    if not flags:
        return
    for flag in flags:
        st.markdown(
            f"<span style='color:{STATUS['critical']}'>■</span> "
            f"<span style='font-size:0.9em'>{flag}</span>",
            unsafe_allow_html=True,
        )


ALTO_DE_FILA = 35
ALTO_DE_CABECERA = 38


def alto_ajustado(filas: int, maximo: int) -> int:
    """Alto justo para las filas que hay, sin pasar del maximo.

    Con un alto fijo, una tabla de tres filas se dibuja con siete huecos vacios
    debajo. Quien la mira no ve una tabla corta: ve una tabla rota.
    """
    return min(maximo, ALTO_DE_CABECERA + ALTO_DE_FILA * max(filas, 1))


def movers_table(df: pd.DataFrame, height: int = 320) -> None:
    """Tabla de movimientos con formato consistente."""
    if df.empty:
        st.caption("Sin datos.")
        return

    # Las columnas de porcentaje se pasan YA en escala 0-100. Streamlit formatea
    # el valor crudo: un 0,018 con formato "%+.2f%%" se imprime como "+0.02%",
    # que es cien veces menos de lo que es.
    percentil = pd.to_numeric(df.get("composite_pctile"), errors="coerce") * 100
    view = pd.DataFrame(
        {
            "Ticker": df["ticker"],
            # Sin nombre ni sector, una celda en blanco parece un fallo de la
            # tabla. La raya dice lo que pasa: ese dato aun no se ha descargado.
            "Nombre": df["name"].fillna("").replace("", "—"),
            "Sector": df["gics_sector"].fillna("").replace("", "—"),
            "Precio": df["close"],
            "Día": pd.to_numeric(df["ret_1d"], errors="coerce") * 100,
            "Vol. rel.": df.get("rel_volume_20"),
            "Percentil": percentil,
        }
    )
    # Una barra de progreso vacia en todas las filas no informa de nada y se lee
    # como un dato perdido. Si no hay ni un percentil, se quita la columna y se
    # dice por que.
    sin_percentil = bool(view["Percentil"].isna().all())
    if sin_percentil:
        view = view.drop(columns=["Percentil"])
    st.dataframe(
        view,
        hide_index=True,
        height=alto_ajustado(len(view), height),
        column_config={
            "Precio": st.column_config.NumberColumn(format="%.2f"),
            "Día": st.column_config.NumberColumn(format="%+.2f%%"),
            "Vol. rel.": st.column_config.NumberColumn(format="%.1fx"),
            "Percentil": st.column_config.ProgressColumn(
                min_value=0.0, max_value=100.0, format="%.0f%%"
            ),
        },
    )
    if sin_percentil:
        st.caption("Sin percentil: faltan los scores de factores (ejecuta el cálculo).")


def prepare_percent_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Pasa fracciones a puntos porcentuales, para el formato de las tablas."""
    out = df.copy()
    for col in columns:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce") * 100
    return out


def signal_chips(signals: list[str], labels: dict[str, str]) -> str:
    if not signals:
        return "—"
    return " · ".join(labels.get(s, s) for s in signals)


__all__ = [
    "render_disclaimer", "render_freshness_badge", "render_pending_alerts_badge",
    "sidebar_filters",
    "metric_row", "render_reasons", "render_flags", "movers_table", "alto_ajustado",
    "prepare_percent_columns", "signal_chips", "render_signal_chips",
    "format_pct", "format_market_cap",
]
