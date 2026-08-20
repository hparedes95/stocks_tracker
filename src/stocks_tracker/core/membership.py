"""Quien estaba en cada indice y CUANDO. El sesgo de supervivencia, en serio.

EL PROBLEMA, Y POR QUE NO SE ARREGLA SOLO CON CODIGO

Un backtest que puntua el ano 2019 con los constituyentes de HOY sobreestima
los resultados, porque las empresas que quebraron, fueron absorbidas o salieron
del indice no aparecen por ninguna parte. Solo compiten las que llegaron vivas
hasta hoy. El efecto es grande y va siempre en la misma direccion: hacia
arriba.

La correccion evidente es guardar la composicion con fechas y consultarla como
estaba en cada momento. Eso es lo que hace este modulo. Pero hay que decir con
claridad lo que NO arregla, porque venderlo como resuelto seria peor que no
tenerlo:

1. La composicion historica de verdad no la tenemos. `universe_membership`
   empieza el dia que se ejecuto la ingesta por primera vez. Hacia atras no hay
   nada y no se puede inventar.
2. Aunque la reconstruyeramos —la lista de cambios del S&P 500 esta en
   Wikipedia—, **yfinance no da precios de las empresas desaparecidas**. Saber
   que una empresa estaba en el indice en 2015 no sirve de nada si su serie de
   precios ya no existe.

O sea: aplicar el universo punto-en-el-tiempo sin precios de las desaparecidas
no elimina el sesgo, solo hace el universo mas pequeno. Por eso la consulta con
fecha es OPCIONAL y viene acompanada de `cobertura()`, que dice cuantos dias de
composicion real hay. Un numero que se puede comprobar, en vez de una promesa.

EL FALLO QUE HABIA

`valid_to` no se rellenaba nunca. Cada ingesta insertaba una fila nueva con
`valid_from = hoy` y `valid_to = NULL`, asi que la tabla acumulaba un intervalo
abierto por ticker y por dia. Consecuencia: `WHERE valid_to IS NULL` —la
consulta que usaban todos los consumidores para pedir "los miembros de hoy"—
devolvia **todos los tickers que habian estado alguna vez**, incluido el que
salio del indice hace meses. Es decir, la tabla que existia para evitar el
sesgo de supervivencia lo estaba produciendo.
"""

from __future__ import annotations

from datetime import date

import pandas as pd


def actualizar(conn, universo: str, miembros: list[str], hoy: date,
               fiable: bool = True) -> dict:
    """Registra la composicion de hoy como intervalos, no como una foto diaria.

    Tres casos y solo tres:

    - **Entra**: no tenia intervalo abierto. Se abre uno con `valid_from = hoy`.
    - **Sigue**: ya tiene intervalo abierto. No se toca NADA. Insertar una fila
      cada dia es lo que convertia la tabla en un registro diario y hacia que
      `valid_from` significara "el dia que se miro" en vez de "el dia que
      entro", que es el unico dato que la hace util.
    - **Sale**: tenia intervalo abierto y ya no esta. Se cierra con
      `valid_to = hoy`.

    `fiable=False` desactiva el tercero, y es imprescindible. Cuando la
    descarga de Wikipedia falla —un 403, un timeout, o la falta de lxml que
    tuvo CI en rojo una semana—, `resolve_universe` devuelve la lista MANUAL de
    respaldo, que son unos 50 tickers frente a los 450 reales. Sin esta
    proteccion, esos 400 que faltan se leen como salidas del indice y se
    cierran sus intervalos: un fallo de red de un minuto escribe una baja
    masiva falsa, PERMANENTE, en la unica tabla que guarda historia que no se
    puede reconstruir. Comprobado: 400 intervalos cerrados en falso.

    Abrir si es seguro con una lista incompleta —el respaldo es un subconjunto
    curado de miembros de verdad—, asi que la tabla solo puede ganar verdad,
    nunca perderla. Y si algun valor salio de verdad ese dia, la siguiente
    descarga buena lo cierra: el error se corrige solo, que es justo lo que no
    pasaba al reves.

    Devuelve el recuento de cada caso, para poder decirlo en pantalla: que un
    valor salga del S&P 500 es una noticia y merece verse.
    """
    abiertos = {
        fila[0] for fila in conn.execute(
            "SELECT ticker FROM universe_membership "
            "WHERE universe = ? AND valid_to IS NULL", [universo],
        ).fetchall()
    }
    actuales = set(miembros)

    entran = sorted(actuales - abiertos)
    salen = sorted(abiertos - actuales) if fiable else []
    no_fiables = sorted(abiertos - actuales) if not fiable else []

    if salen:
        conn.execute(
            "UPDATE universe_membership SET valid_to = ? "
            "WHERE universe = ? AND valid_to IS NULL AND ticker IN "
            f"({', '.join('?' for _ in salen)})",
            [hoy, universo, *salen],
        )
    if entran:
        nuevos = pd.DataFrame([
            {"universe": universo, "ticker": t, "valid_from": hoy, "valid_to": None}
            for t in entran
        ])
        conn.register("_nuevos", nuevos)
        try:
            # `ANTI JOIN` contra la propia tabla: si ya existiera un intervalo
            # que empieza hoy —dos ingestas el mismo dia— insertar violaria la
            # clave primaria y tumbaria la ingesta entera.
            conn.execute(
                "INSERT INTO universe_membership "
                "SELECT n.universe, n.ticker, n.valid_from, n.valid_to FROM _nuevos n "
                "WHERE NOT EXISTS (SELECT 1 FROM universe_membership m "
                "  WHERE m.universe = n.universe AND m.ticker = n.ticker "
                "    AND m.valid_from = n.valid_from)"
            )
        finally:
            conn.unregister("_nuevos")

    return {"entran": entran, "salen": salen,
            "siguen": len(actuales & abiertos),
            # Los que habrian salido si la lista fuera de fiar. Se devuelven
            # para poder decirlo en pantalla: un silencio aqui haria pensar que
            # la composicion no cambio, cuando lo que pasa es que no se sabe.
            "sin_confirmar": no_fiables}


def compactar(conn) -> int:
    """Colapsa los intervalos duplicados que dejo el fallo anterior.

    Antes se insertaba una fila por ticker y por dia, todas con `valid_to` a
    NULL. Se conserva la de `valid_from` mas antiguo, que es la fecha correcta
    de entrada, y se borran las demas.

    Limitacion, dicha aqui porque no se puede arreglar: si un ticker hubiera
    salido y vuelto durante ese periodo, esto lo deja como un unico intervalo
    continuo. Como las salidas nunca se registraron, esa informacion no existe
    en la tabla y no hay de donde sacarla. Con los pocos dias de historico que
    dejo el fallo, la diferencia es teorica.

    Es idempotente: ejecutarlo dos veces no borra nada la segunda.
    """
    antes = conn.execute("SELECT COUNT(*) FROM universe_membership").fetchone()[0]
    conn.execute(
        """
        DELETE FROM universe_membership
        WHERE valid_to IS NULL AND valid_from > (
            SELECT MIN(m.valid_from) FROM universe_membership m
            WHERE m.universe = universe_membership.universe
              AND m.ticker = universe_membership.ticker
              AND m.valid_to IS NULL
        )
        """
    )
    despues = conn.execute("SELECT COUNT(*) FROM universe_membership").fetchone()[0]
    return int(antes - despues)


def condicion_vigente(campo_fecha: str, alias: str = "") -> str:
    """La regla de intervalos, escrita UNA sola vez en todo el programa.

    `valid_to > fecha` y no `>=`: el intervalo se cierra el dia en que se
    detecta la salida, asi que ese mismo dia el valor ya no forma parte.

    POR QUE ESTO ES UNA FUNCION Y NO UN COMENTARIO EN DOS SITIOS

    Antes habia dos escrituras de esta regla: un `miembros_en()` en Python que
    no llamaba nadie, y este mismo JOIN copiado dentro de la consulta del
    backtest. Los tests comprobaban la de Python; la que recorta el universo de
    verdad era la otra.

    Dos copias de una regla no son redundancia util: son una que se corrige y
    otra que no. La que nadie ejecuta no falla nunca, asi que nada avisa el dia
    que se quedan distintas. Ahora hay una, y quien la prueba prueba la que
    corre.
    """
    p = f"{alias}." if alias else ""
    return (f"{p}valid_from <= {campo_fecha} "
            f"AND ({p}valid_to IS NULL OR {p}valid_to > {campo_fecha})")


def join_vigente(tabla: str) -> str:
    """Recorta una tabla con `ticker` y `date` al universo de ESE dia."""
    return (f"JOIN universe_membership m ON m.ticker = {tabla}.ticker\n"
            f"                 AND {condicion_vigente(f'{tabla}.date', alias='m')}")


def ultima_comprobacion(conn) -> date | None:
    """Hasta cuando SABEMOS quien estaba en el universo.

    No es hoy, y la diferencia importa. Un intervalo abierto significa "seguia
    dentro la ultima vez que se miro", no "sigue dentro ahora": si hace un ano
    que no se ejecuta la ingesta, no hay un ano mas de composicion, hay una
    foto de hace un ano.

    Y no se puede deducir de la propia tabla, justamente porque lo correcto es
    NO escribir nada cuando la composicion no cambia: "sin cambios desde hace
    un ano" y "sin comprobar desde hace un ano" dejan el mismo rastro. Por eso
    se pregunta a `ingest_log`, que es quien sabe cuando se miro de verdad.
    """
    try:
        fila = conn.execute(
            "SELECT MAX(finished_at) FROM ingest_log "
            "WHERE task = 'universe' AND status IN ('OK', 'PARTIAL')"
        ).fetchone()
        if fila and fila[0] is not None:
            return pd.Timestamp(fila[0]).date()
    except Exception:  # noqa: BLE001 — almacen anterior a ingest_log
        pass
    # Sin registro de ingesta, lo ultimo que consta es el ultimo cambio anotado.
    # Es una cota inferior: quiza se comprobo despues y no cambio nada.
    fila = conn.execute(
        "SELECT MAX(GREATEST(valid_from, COALESCE(valid_to, valid_from))) "
        "FROM universe_membership"
    ).fetchone()
    return pd.Timestamp(fila[0]).date() if fila and fila[0] is not None else None


def cobertura(conn) -> pd.DataFrame:
    """Cuanta composicion real hay guardada, por universo.

    El numero que permite decir la verdad en pantalla en vez de una promesa:
    "hay 3 dias de composicion real" se comprueba, "el sesgo de supervivencia
    esta corregido" no.
    """
    hasta = ultima_comprobacion(conn)
    frame = conn.execute(
        """
        SELECT universe AS universo,
               MIN(valid_from) AS desde,
               COUNT(DISTINCT ticker) AS tickers,
               COUNT(valid_to) AS salidas_registradas
        FROM universe_membership
        GROUP BY universe ORDER BY universe
        """
    ).fetchdf()
    if not frame.empty:
        frame.insert(2, "hasta", pd.Timestamp(hasta) if hasta else pd.NaT)
    return frame


def anos_de_composicion(conn) -> float:
    """Anos de composicion real acumulada. Cero si no hay tabla o esta vacia.

    El extremo superior es la ultima comprobacion y NO la fecha de hoy. Usar
    hoy hace que el numero crezca solo sin haber descargado nada, que es
    exactamente la clase de dato que hace que una advertencia honesta deje de
    serlo.
    """
    fila = conn.execute("SELECT MIN(valid_from) FROM universe_membership").fetchone()
    if not fila or fila[0] is None:
        return 0.0
    hasta = ultima_comprobacion(conn)
    if hasta is None:
        return 0.0
    return max(0.0, (hasta - pd.Timestamp(fila[0]).date()).days / 365.25)


def aviso_de_supervivencia(anos: float, anos_backtest: float) -> str:
    """La frase honesta sobre el sesgo, con los numeros de esta instalacion.

    Se escribe aqui y no en la pagina para que diga lo mismo en la consola y en
    pantalla. Dos versiones de la misma advertencia acaban divergiendo, y la
    que se relaja es siempre la que ve el usuario.
    """
    if anos <= 0:
        return (
            "El universo son los constituyentes de HOY. Las empresas que "
            "quebraron o salieron del indice no aparecen, asi que estos "
            "resultados estan sesgados al alza y no se sabe cuanto."
        )
    if anos < anos_backtest:
        return (
            f"Hay {anos:.1f} anos de composicion real guardada, y el periodo "
            f"analizado son {anos_backtest:.1f}. Para el tramo anterior se usan "
            "los constituyentes de hoy, asi que esa parte sigue sesgada al "
            "alza. La cobertura crece sola con cada ingesta."
        )
    return (
        f"Hay {anos:.1f} anos de composicion real guardada, suficiente para "
        f"cubrir los {anos_backtest:.1f} del periodo analizado. Aun asi, los "
        "precios de las empresas desaparecidas no estan disponibles, asi que "
        "el sesgo se reduce pero no desaparece."
    )
