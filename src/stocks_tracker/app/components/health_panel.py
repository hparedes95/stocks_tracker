"""El semaforo de deterioro de la cartera, posicion a posicion.

Va en la pagina de la cartera, justo debajo del resumen y por encima del
detalle. Lo importante es que se vea sin buscarlo: una posicion que gana un
15 % con el margen desplomandose es exactamente la que no se mira, porque el
numero verde de al lado dice que todo va bien.

Se ordena por gravedad y no por peso ni por alfabeto: lo que hay que mirar,
primero.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ...core.deterioration import ETIQUETA, Nivel, diagnosticar

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


def _fila(datos: pd.Series) -> dict:
    """Parte la fila ancha en el 'hoy' y el 'entonces' que espera el nucleo."""
    hoy, entonces = {}, {}
    for col, valor in datos.items():
        nombre = str(col)
        if nombre.endswith("_entonces"):
            entonces[nombre[: -len("_entonces")]] = valor
        else:
            hoy[nombre] = valor
    return {"hoy": hoy, "entonces": entonces}


def diagnosticos(salud: pd.DataFrame) -> list:
    """Un diagnostico por posicion, ordenados por lo que hay que mirar antes."""
    fuera = []
    for _, fila in salud.iterrows():
        partes = _fila(fila)
        fuera.append(diagnosticar(
            str(fila["ticker"]),
            fund_hoy=partes["hoy"], fund_entonces=partes["entonces"],
            ind_hoy=partes["hoy"], ind_entonces=partes["entonces"],
            comparado_con=partes["hoy"].get("opened_at"),
        ))
    return sorted(fuera, key=lambda d: (ORDEN[d.nivel], -d.puntos, d.ticker))


def render_health_panel(salud: pd.DataFrame, nombres: dict | None = None) -> None:
    if salud.empty:
        return

    diags = diagnosticos(salud)
    nombres = nombres or {}

    st.subheader("Que ha cambiado desde que compraste")
    st.caption(
        "Compara cada posicion con el dia en que la compraste, no con unos "
        "umbrales generales. **No predice nada y no dice que vendas**: dice que "
        "ha cambiado y cuanto, con el numero delante, para que decidas mirando "
        "el motivo y no el color. Verde significa que se ha mirado y no hay "
        "nada; **gris significa que no habia datos para mirar**."
    )

    cuenta = {nivel: sum(1 for d in diags if d.nivel is nivel) for nivel in Nivel}
    resumen = st.columns(4)
    for col, nivel in zip(resumen, (Nivel.ROJO, Nivel.AMBAR, Nivel.VERDE, Nivel.GRIS),
                          strict=False):
        col.metric(ETIQUETA[nivel], cuenta[nivel])

    pendientes = [d for d in diags if d.senales]
    if not pendientes:
        st.success(
            "Ninguna posicion ha empeorado de forma medible desde que la "
            "compraste. Que no haya senales no significa que no pueda caer: "
            "significa que no hay nada que estas comprobaciones sepan ver.",
            icon=":material/check_circle:",
        )

    for d in diags:
        nombre = nombres.get(d.ticker, "")
        titulo = f"{d.ticker}" + (f" · {nombre}" if nombre else "")
        cabecera = f"{titulo} — {ETIQUETA[d.nivel]}"
        if d.senales:
            cabecera += f" ({len(d.senales)})"

        with st.expander(cabecera, expanded=d.nivel is Nivel.ROJO,
                         icon=ICONO[d.nivel]):
            if d.nivel is Nivel.GRIS:
                if d.hay_datos and not d.comparado:
                    st.info(
                        "Hay datos de hoy pero ninguno del dia en que "
                        "compraste, asi que **no se ha podido comparar**. No "
                        "es que no haya cambiado nada: es que no se sabe. Pasa "
                        "con las posiciones compradas antes de que el programa "
                        "empezara a guardar el historico, y se arregla solo con "
                        "el tiempo.",
                        icon=":material/help:",
                    )
                else:
                    st.info(
                        "No hay datos para comprobar esta posicion. No es que "
                        "este bien: es que no se ha podido mirar. Suele pasar "
                        "con indices, ETF y cripto, que no publican "
                        "fundamentales, y con valores recien anadidos. Se "
                        "rellena con `stocks.ps1 update`.",
                        icon=":material/help:",
                    )
                continue

            if not d.senales:
                st.caption("Sin cambios a peor en lo que se puede comprobar.")
                continue

            for senal in d.senales:
                escribir = st.error if senal.grave else st.warning
                escribir(senal.texto, icon=":material/priority_high:"
                         if senal.grave else ":material/visibility:")

            if d.comparado_con is not None and pd.notna(d.comparado_con):
                st.caption(
                    f"Comparado con el {pd.Timestamp(d.comparado_con):%d/%m/%Y}, "
                    "el dia de la compra."
                )
            else:
                st.caption(
                    "Sin fecha de compra registrada: solo se ha podido "
                    "comprobar lo que se mira con los datos de hoy."
                )
