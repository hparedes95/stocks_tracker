"""Ejecutable de alertas: evalua, guarda y entrega.

    python -m stocks_tracker.alerts.run_alerts

Pensado para ejecutarse desde cron al final del ciclo diario, despues de la
ingesta y el calculo.
"""

from __future__ import annotations

import argparse

from rich.console import Console

from ..core.db import migrate
from . import evaluate as ev
from . import notify as nt
from .rules import get_rules, reload, severity_rank

console = Console()


def run(dry_run: bool = False, min_severity: str = "baja") -> int:
    """Evalua las reglas y entrega las alertas nuevas."""
    migrate()
    reload()

    rules = get_rules()
    if not rules:
        console.print("[yellow]No hay reglas configuradas en alerts.yaml[/]")
        return 0

    alerts = ev.evaluate_rules(rules)

    threshold = severity_rank(min_severity)
    alerts = [a for a in alerts if severity_rank(a.severity) >= threshold]

    if not alerts:
        console.print("[green]Sin alertas nuevas.[/] Nada que avisar hoy.")
        return 0

    console.print(f"[cyan]{len(alerts)} alertas nuevas:[/]")
    for alert in sorted(alerts, key=lambda a: -severity_rank(a.severity)):
        color = {
            "critica": "red", "alta": "yellow", "media": "cyan", "baja": "dim",
        }.get(alert.severity, "white")
        console.print(f"  [{color}]{nt.format_alert(alert)}[/]")

    if dry_run:
        console.print("\n[dim]Modo de prueba: no se guarda ni se envia nada.[/]")
        return len(alerts)

    ev.persist(alerts)

    results = nt.deliver(alerts)
    if not results:
        console.print(
            "[yellow]Ningun canal activo.[/] Revisa `channels` en config/alerts.yaml"
        )
        return len(alerts)

    for result in results:
        if result.ok:
            console.print(f"  [green]{result.channel}: {result.sent} entregadas[/]")
            ev.mark_delivered([a.id for a in alerts], result.channel)
        else:
            console.print(f"  [red]{result.channel}: {result.detail}[/]")

    return len(alerts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluacion y envio de alertas")
    parser.add_argument("--dry-run", action="store_true",
                        help="muestra las alertas sin guardarlas ni enviarlas")
    parser.add_argument("--min-severity", default="baja",
                        choices=["baja", "media", "alta", "critica"])
    parser.add_argument("--test-channel", default=None,
                        help="envia un mensaje de prueba por un canal")
    parser.add_argument("--purge-days", type=int, default=None,
                        help="borra alertas mas antiguas que N dias")
    args = parser.parse_args()

    if args.test_channel:
        result = nt.test_channel(args.test_channel)
        if result.ok:
            console.print(f"[green]Prueba enviada por {result.channel}.[/]")
        else:
            console.print(f"[red]{result.channel}: {result.detail}[/]")
        return

    if args.purge_days is not None:
        migrate()
        removed = ev.purge_older_than(args.purge_days)
        console.print(f"[green]{removed} alertas antiguas eliminadas.[/]")
        return

    run(dry_run=args.dry_run, min_severity=args.min_severity)


if __name__ == "__main__":
    from ..core.db import arrancar

    arrancar(main)
