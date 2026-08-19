"""Lo que cuesta de verdad comprar un valor, en la ficha del valor.

Va aquí y no en una calculadora aparte porque es donde se decide comprar. Una
pantalla que hay que ir a buscar no se mira, y este es justo el número que
conviene ver ANTES y no al hacer la declaración.

Tres preguntas, en orden de lo que sorprende:

1. ¿Cuánto tiene que subir solo para no perder dinero? (comisión + divisa,
   ida y vuelta)
2. ¿Cuánto renta el dividendo DE VERDAD? (retención en origen)
3. ¿Recomprarlo ahora me deja una pérdida sin compensar? (regla de los dos meses)
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ...core import costs

# Margen sobre el que se considera "claramente una ganancia". El resultado de
# cada venta es una ESTIMACION: se calcula con el cierre del dia, que no es el
# precio de ejecucion, y `avg_cost` no incluye comisiones. Una venta estimada
# en +1 % pudo ser una perdida real, asi que por debajo de este margen se avisa
# igualmente. Callar un aviso que importa es peor que darlo de mas.
MARGEN_ESTIMACION_PCT = 2.0


def _ventas_recientes(ticker: str) -> tuple[list[dict], dict]:
    """Ventas de este valor que podrían activar la regla, y su estimación.

    Se leen del almacen y no se piden por pantalla: si hubiera que teclearlas,
    el aviso no aparecería nunca justo cuando hace falta.

    Devuelve las ventas en el formato que espera `costs.comprobar_dos_meses` y,
    aparte, el resultado estimado de cada una para poder enseñarlo. `None`
    significa "no se sabe", que no es lo mismo que "fue ganancia": esas también
    se avisan.
    """
    from .. import data_access as da

    try:
        filas = da.get_closed_sales(ticker)
    except Exception:  # noqa: BLE001 — sin almacen, sin avisos
        return [], {}

    ventas: list[dict] = []
    estimado: dict = {}
    for fila in filas.itertuples():
        pct = getattr(fila, "resultado_pct", None)
        conocido = pct is not None and pd.notna(pct)
        if conocido and float(pct) >= MARGEN_ESTIMACION_PCT:
            continue        # ganancia clara: la regla de los dos meses no aplica
        perdida = 0.0
        if conocido and fila.avg_cost and fila.qty:
            perdida = max(0.0, (float(fila.avg_cost) - float(fila.precio_estimado))
                          * float(fila.qty))
        # DuckDB devuelve las fechas como `Timestamp` al pasar por pandas.
        # `comprobar_dos_meses` las convierte a `date` por dentro, asi que la
        # clave de aqui tiene que convertirse tambien o no casaria con la del
        # aviso y el porcentaje no se ensenaria nunca —sin dar ningun error—.
        cuando = fila.closed_at
        if hasattr(cuando, "date"):
            cuando = cuando.date()
        ventas.append({"closed_at": cuando, "perdida_eur": perdida})
        estimado[cuando] = float(pct) if conocido else None
    return ventas, estimado


def render_cost_panel(ticker: str, currency: str = "EUR",
                      dividend_yield: float = 0.0, country: str = "") -> None:
    st.caption(
        "Estimación para comparar alternativas, **no es asesoramiento fiscal**. "
        "Los tipos cambian y tu situación puede tener minusvalias pendientes de "
        "otros ejercicios. Las tarifas salen de `config/costs.yaml`: cambialas "
        "por las de tu broker o los números no seran los tuyos."
    )

    importe = st.number_input(
        "Cuánto quieres invertir (EUR)", min_value=0.0, value=1000.0,
        step=100.0, key=f"coste_importe_{ticker}",
    )

    ventas, estimado = _ventas_recientes(ticker)
    resumen = costs.resumen(
        importe_eur=importe, moneda=currency,
        dividendo_bruto_pct=dividend_yield,
        pais=(country or "desconocido"),
        ventas_con_perdida=ventas,
        ticker=ticker,
    )
    op = resumen.operacion

    # --- 1. El liston -------------------------------------------------------
    st.subheader("Cuánto tiene que subir para no perder")
    col = st.columns(4)
    col[0].metric("Comisión", f"{op.comision:.2f} EUR")
    col[1].metric("Cambio de divisa", f"{op.cambio_divisa:.2f} EUR",
                  help="Se cobra sobre el importe entero, no sobre el "
                       "beneficio. Es cero si compras en euros.")
    # El porcentaje va en la etiqueta y no dentro del valor: en una columna de
    # cuatro, "3.50 EUR (0.35 %)" se corta y se lee "3.50 EUR (0.35…". En el
    # `delta` tampoco, porque Streamlit le pone una flecha hacia arriba y un
    # coste con flecha de subida parece que este subiendo.
    col[2].metric(f"Coste de la compra ({op.pct:.2f} % del importe)",
                  f"{op.total:.2f} EUR")
    col[3].metric("Ida y vuelta", f"{op.ida_y_vuelta_pct:.2f} %",
                  help="Comprar Y vender. Es lo que de verdad tienes que "
                       "recuperar: nadie compra para no vender nunca.")

    if op.ida_y_vuelta_pct >= 1.0:
        st.warning(
            f"Con este importe los costes se llevan un **{op.ida_y_vuelta_pct:.2f} %** "
            "entre comprar y vender. Una operación que suba menos que eso pierde "
            "dinero aunque la veas en verde. Con importes pequeños la comisión "
            "mínima pesa mucho: agrupar compras sale más barato que hacerlas "
            "sueltas.",
            icon=":material/warning:",
        )
    else:
        st.caption(
            f"El valor tiene que subir un {op.ida_y_vuelta_pct:.2f} % solo para "
            "cubrir los costes de entrar y salir."
        )

    # --- 2. El dividendo ----------------------------------------------------
    div = resumen.dividendo
    if div is not None and div.bruto_pct > 0:
        st.subheader("Lo que renta el dividendo de verdad")
        d = st.columns(3)
        d[0].metric("Bruto", f"{div.bruto_pct:.2f} %")
        # Sin `delta_color` por defecto: lo retenido es dinero que se pierde y
        # tiene que salir en rojo. Con "inverse" salia en VERDE, que es
        # exactamente lo contrario de lo que significa.
        d[1].metric("Neto en tu cuenta", f"{div.neto_inmediato_pct:.2f} %",
                    delta=f"-{div.retencion_pct:.1f} % retenido en origen")
        d[2].metric("No recuperable", f"{div.perdido_pct:.2f} %",
                    help="Lo retenido por encima del límite del convenio. Para "
                         "recuperarlo hay que reclamar al pais de origen, un "
                         "tramite que en cantidades pequeñas cuesta más de lo "
                         "que devuelve.")

        if div.pais == "US":
            st.caption(
                "EE. UU. retiene el **15 %** con el formulario W-8BEN "
                "presentado y el **30 %** sin el. Lo pide tu broker al abrir la "
                "cuenta y **caduca cada tres años**: cuando caduca, la "
                "retención sube sin avisar."
            )
        if div.perdido_pct > 5:
            st.warning(
                f"En {div.pais} se retiene el {div.retencion_pct:.1f} % y solo "
                f"se puede deducir hasta el {div.recuperable_pct:.1f} %: "
                f"**{div.perdido_pct:.1f} puntos no vuelven**. Un dividendo "
                "alto ahí puede rentar menos que uno más bajo en otro sitio.",
                icon=":material/paid:",
            )

    # --- 3. La regla de los dos meses ---------------------------------------
    if resumen.dos_meses.bloquea:
        st.subheader("Cuidado con la regla de los dos meses")
        for aviso in resumen.dos_meses.avisos:
            pct = estimado.get(aviso.vendido_el)
            if pct is None:
                cuanto = ("No hay precio de aquel día para estimar el resultado, "
                          "así que **puede que fuera con pérdidas**.")
            else:
                cuanto = (f"Estimamos que la cerraste en torno al **{pct:+.1f} %** "
                          "(con el cierre de aquel día, que no es el precio exacto "
                          "de tu venta).")
            st.error(
                f"Vendiste **{ticker}** el {aviso.vendido_el:%d/%m/%Y}, hace "
                f"{aviso.dias_desde_venta} días. {cuanto} **Si fue con pérdidas**, "
                "recomprar ahora te impide compensarla este ejercicio "
                f"(art. 33.5 LIRPF). Quedan **{aviso.dias_que_faltan} días**: a "
                f"partir del {aviso.libre_el:%d/%m/%Y} ya no aplica.\n\n"
                "La pérdida no se pierde, queda aplazada hasta que vendas lo "
                "que compres ahora. Pero si vendiste para hacer caja fiscal, "
                "recomprar ahora anula justo lo que buscabas.",
                icon=":material/gavel:",
            )
