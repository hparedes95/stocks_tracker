"""El semáforo de deterioro de la cartera, posición a posición.

Va en la página de la cartera, justo debajo del resumen y por encima del
detalle. Lo importante es que se vea sin buscarlo: una posición que gana un
15 % con el margen desplomandose es exactamente la que no se mira, porque el
número verde de al lado dice que todo va bien.

Se ordena por gravedad y no por peso ni por alfabeto: lo que hay que mirar,
primero.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ...core.deterioration import ETIQUETA, Nivel, diagnosticar, partir

ICONO = {
    Nivel.ROJO: ":material/error:",
    Nivel.AMBAR: ":material/warning:",
    Nivel.VERDE: ":material/check_circle:",
    Nivel.GRIS: ":material/help:",
}

# Para ordenar: primero lo que hay que mirar. El gris va antes que el verde
# a proposito —"no se ha podido comprobar" merece mas atencion que "comprobado
# y sin novedad"—.
ORDEN = {Nivel.ROJO: 0, Nivel.AMBAR: 1, Nivel.GRIS: 2, Nivel.VERDE: 3}


def diagnosticos(salud: pd.DataFrame) -> list:
    """Un diagnóstico por posición, ordenados por lo que hay que mirar antes."""
    fuera = []
    for _, fila in salud.iterrows():
        hoy, entonces = partir(fila)
        fuera.append(diagnosticar(
            str(fila["ticker"]),
            fund_hoy=hoy, fund_entonces=entonces,
            ind_hoy=hoy, ind_entonces=entonces,
            comparado_con=hoy.get("opened_at"),
        ))
    return sorted(fuera, key=lambda d: (ORDEN[d.nivel], -d.puntos, d.ticker))


def _observaciones(d) -> None:
    """Lo que se ve hoy pero no se ha podido comparar con nada.

    Va aparte y en gris a proposito. Es informacion util —una caida del 45 %
    hay que verla— pero NO ha movido el veredicto, y meterla en la misma lista
    que lo que si lo mueve haria creer que la posicion se ha juzgado peor de lo
    que se ha podido juzgar.
    """
    if not d.observaciones:
        return
    st.caption("Lo que se ve hoy, sin poder decir si ha empeorado desde tu "
               "compra (no cuenta para el semáforo):")
    for senal in d.observaciones:
        st.caption(f"· {senal.texto}")


def render_health_panel(salud: pd.DataFrame, nombres: dict | None = None) -> None:
    if salud.empty:
        return

    diags = diagnosticos(salud)
    nombres = nombres or {}

    st.subheader("Qué ha cambiado desde que compraste")
    st.caption(
        "Compara cada posición con el día en que la compraste, no con unos "
        "umbrales generales. **No predice nada y no dice que vendas**: dice que "
        "ha cambiado y cuanto, con el número delante, para que decidas mirando "
        "el motivo y no el color. Verde significa que se ha mirado y no hay "
        "nada; **gris significa que no había datos para mirar**."
    )

    cuenta = {nivel: sum(1 for d in diags if d.nivel is nivel) for nivel in Nivel}
    resumen = st.columns(4)
    for col, nivel in zip(resumen, (Nivel.ROJO, Nivel.AMBAR, Nivel.VERDE, Nivel.GRIS),
                          strict=False):
        col.metric(ETIQUETA[nivel], cuenta[nivel])

    pendientes = [d for d in diags if d.comparadas]
    if not pendientes:
        st.success(
            "Ninguna posición ha empeorado de forma medible desde que la "
            "compraste. Que no haya señales no significa que no pueda caer: "
            "significa que no hay nada que estas comprobaciones sepan ver.",
            icon=":material/check_circle:",
        )

    for d in diags:
        nombre = nombres.get(d.ticker, "")
        titulo = f"{d.ticker}" + (f" · {nombre}" if nombre else "")
        cabecera = f"{titulo} — {ETIQUETA[d.nivel]}"
        if d.comparadas:
            cabecera += f" ({len(d.comparadas)})"

        with st.expander(cabecera, expanded=d.nivel is Nivel.ROJO,
                         icon=ICONO[d.nivel]):
            if d.nivel is Nivel.GRIS:
                if d.espejo:
                    # Este caso NO se arregla solo con el tiempo, al reves que
                    # el de abajo: se arregla poniendo la fecha real de compra.
                    # Por eso lleva aviso propio y no se mezcla con aquel.
                    st.warning(
                        "La fecha de compra que hay guardada de esta posición "
                        "es **la de hoy** —casi seguro la del día en que "
                        "importaste la cartera, no la de la compra real—. "
                        "Compararía hoy contra hoy, y de ahí solo puede salir "
                        "un verde automático. Corrige la fecha de compra más "
                        "abajo y esta posición pasa a juzgarse como las demás.",
                        icon=":material/event_busy:",
                    )
                elif d.hay_datos and not d.comparado:
                    st.info(
                        "Hay datos de hoy pero ninguno del día en que "
                        "compraste, así que **no se ha podido comparar**. No "
                        "es que no haya cambiado nada: es que no se sabe. Pasa "
                        "con las posiciones compradas antes de que el programa "
                        "empezara a guardar el histórico, y se arregla solo con "
                        "el tiempo.",
                        icon=":material/help:",
                    )
                else:
                    st.info(
                        "No hay datos para comprobar esta posición. No es que "
                        "este bien: es que no se ha podido mirar. Suele pasar "
                        "con índices, ETF y cripto, que no publican "
                        "fundamentales, y con valores recien añadidos. Se "
                        "rellena con `stocks.ps1 update`.",
                        icon=":material/help:",
                    )
                _observaciones(d)
                continue

            if not d.comparadas:
                st.caption("Sin cambios a peor en lo que se puede comprobar.")
                _observaciones(d)
                continue

            for senal in d.comparadas:
                escribir = st.error if senal.grave else st.warning
                escribir(senal.texto, icon=":material/priority_high:"
                         if senal.grave else ":material/visibility:")

            if d.comparado_con is not None and pd.notna(d.comparado_con):
                st.caption(
                    f"Comparado con el {pd.Timestamp(d.comparado_con):%d/%m/%Y}, "
                    "el día de la compra."
                )
            else:
                st.caption(
                    "Sin fecha de compra registrada: solo se ha podido "
                    "comprobar lo que se mira con los datos de hoy."
                )
