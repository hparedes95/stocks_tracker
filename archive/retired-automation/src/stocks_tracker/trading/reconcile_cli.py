"""Comando de reconciliacion: pregunta al broker y compara.

El modulo de comparacion (`core/reconcile`) no sabe hablar con nadie: recibe dos
diccionarios y devuelve las diferencias. Aqui esta lo unico que falta, que es ir
a buscar el primero de los dos.

CON EL BROKER SIMULADO NO SIRVE DE NADA, Y SE DICE

En modo simulado las posiciones del "broker" salen del mismo sitio que las del
programa. Compararlas es comparar una cosa consigo misma: da siempre que cuadra,
y ese "cuadra" no vale nada. Se avisa y se sale con un codigo propio en lugar de
ensenar un verde que nadie ha ganado. Es la misma regla que gobierna el panel de
integridad: no comprobado no es lo mismo que comprobado y bien.
"""

from __future__ import annotations

import argparse

from rich.console import Console

from ..core import reconcile
from ..core.db import connect, migrate
from ..core.ids import ulid
from .brokers.base import BrokerError, BrokerMode

console = Console()

# Codigos propios. Que la reconciliacion falle no es lo mismo que que no se haya
# podido hacer, y quien llama —una tarea programada, el instalador— necesita
# distinguirlo para decidir si avisar al usuario o callarse.
EXIT_DIFIERE = 79
EXIT_SIN_BROKER_REAL = 80


def del_broker(broker) -> tuple[dict[str, dict], float | None]:
    """Lo que dice el broker, en el formato que espera `reconcile.comparar`.

    Un fallo al preguntar NO se convierte en una cartera vacia: eso se leeria
    como "el broker no tiene nada" y produciria una posicion fantasma por cada
    valor que si tienes. Se propaga.
    """
    posiciones = {
        p.symbol: {"qty": p.qty, "avg_cost": p.avg_entry_price}
        for p in broker.get_positions()
    }
    try:
        efectivo = float(broker.get_account().cash)
    except BrokerError as exc:
        # El efectivo si puede faltar sin invalidar el resto: se compara lo que
        # hay y se dice que del efectivo no se sabe.
        console.print(f"[yellow]El broker no ha dado el efectivo: {exc}[/]")
        efectivo = None
    return posiciones, efectivo


def reconciliar(broker, efectivo_propio: float | None = None
                ) -> list[reconcile.Diferencia]:
    suyas, efectivo_broker = del_broker(broker)
    with connect(read_only=True) as conn:
        nuestras = reconcile.posiciones_del_almacen(conn)
        if efectivo_propio is None:
            fila = conn.execute(
                "SELECT cash FROM portfolio_snapshots "
                "ORDER BY snapshot_at DESC LIMIT 1"
            ).fetchone()
            efectivo_propio = float(fila[0]) if fila and fila[0] is not None else None

    return reconcile.comparar(suyas, nuestras,
                              efectivo_broker=efectivo_broker,
                              efectivo_propio=efectivo_propio)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Contrasta la cartera del broker con la del programa"
    )
    parser.add_argument("--venue", default=None)
    args = parser.parse_args(argv)

    migrate()

    from .run_bot import config_for

    venue = args.venue or "default"
    cfg = config_for(args.venue)
    modo = str(getattr(cfg, "mode", "simulated"))

    if modo == "simulated":
        console.print(
            "[yellow]En modo simulado no hay nada que reconciliar.[/] Las "
            "posiciones del broker salen del mismo sitio que las del programa, "
            "asi que compararlas es comparar una cosa consigo misma: daria "
            "'cuadra' siempre y ese 'cuadra' no significa nada.\n"
            "[dim]Conecta un broker real en config/trading.yaml para que esta "
            "comprobacion sirva de algo.[/]"
        )
        return EXIT_SIN_BROKER_REAL

    try:
        broker = _broker_real(args.venue)
    except (BrokerError, ValueError, KeyError) as exc:
        console.print(f"[bold red]No se ha podido hablar con el broker:[/] {exc}")
        return EXIT_SIN_BROKER_REAL

    try:
        diferencias = reconciliar(broker)
    except BrokerError as exc:
        # Sin la lista de posiciones del broker NO se compara nada. Tratar el
        # fallo como "cartera vacia" produciria una posicion fantasma por cada
        # valor que si tienes, y un rojo aparatoso que no describe la realidad.
        console.print(f"[bold red]El broker no ha dado sus posiciones:[/] {exc}")
        console.print("[dim]No se compara nada: media comparacion es peor que "
                      "ninguna.[/]")
        return EXIT_SIN_BROKER_REAL

    with connect(read_only=True) as conn:
        n = len(reconcile.posiciones_del_almacen(conn))
    with connect() as conn:
        reconcile.guardar(conn, diferencias, venue, ulid(), n)

    if not diferencias:
        console.print(f"[green]{reconcile.resumen(diferencias, n)}[/]")
        return 0

    console.print(f"[bold red]{reconcile.resumen(diferencias, n)}[/]")
    for d in diferencias:
        console.print(f"  [red]{d.campo}:[/] {d.detalle}")
    console.print(
        "\n[dim]No se corrige nada automaticamente. Copiar los numeros del "
        "broker borraria la prueba de que hubo un desajuste, y con ella la "
        "pregunta de por que lo hubo: un desajuste tiene una causa y esa causa "
        "se repite.[/]"
    )
    return EXIT_DIFIERE


def _broker_real(venue: str | None):
    """El adaptador del venue, en modo real o papel; nunca el simulado."""
    from .brokers.kraken import KrakenBroker

    if venue == "kraken":
        broker = KrakenBroker()
        if broker.mode is BrokerMode.SIMULATED:
            raise ValueError("el adaptador de Kraken esta en modo simulado")
        return broker
    raise ValueError(
        f"no hay adaptador de broker real para '{venue or 'acciones'}'. "
        "La reconciliacion necesita uno: es lo que la distingue de comparar el "
        "programa consigo mismo."
    )


if __name__ == "__main__":
    raise SystemExit(main())
