"""Informe diario portable en Markdown.

El dashboard es para explorar; este informe es una fotografia archivable. No
recalcula ni recomienda: resume exclusivamente lo que ya hay en DuckDB e
incluye origen, frescura y avisos para que un ranking nunca viaje sin contexto.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import project_root
from .db import connect
from .timeutils import utcnow


def _scalar(conn, sql: str, default="—"):
    row = conn.execute(sql).fetchone()
    return default if row is None or row[0] is None else row[0]


def _number(value, spec: str = ".2f") -> str:
    try:
        return format(float(value), spec)
    except (TypeError, ValueError):
        return "—"


def render() -> str:
    generated = utcnow()
    with connect(read_only=True) as conn:
        latest = _scalar(conn, "SELECT MAX(date) FROM prices_daily")
        rows = int(_scalar(conn, "SELECT COUNT(*) FROM prices_daily", 0))
        instruments = int(_scalar(conn, "SELECT COUNT(DISTINCT ticker) FROM prices_daily", 0))
        sources = conn.execute(
            "SELECT source, COUNT(*) FROM prices_daily GROUP BY source ORDER BY 2 DESC"
        ).fetchall()
        candidates = conn.execute(
            """
            WITH last_scores AS (
                SELECT * FROM factor_scores
                WHERE date = (SELECT MAX(date) FROM factor_scores)
            ), best AS (
                SELECT ticker, MAX(composite) AS composite,
                       MAX(composite_pctile) AS percentile,
                       MAX(coverage) AS coverage
                FROM last_scores GROUP BY ticker
            )
            SELECT b.ticker, COALESCE(i.name, b.ticker), b.composite,
                   b.percentile, b.coverage
            FROM best b LEFT JOIN instruments i USING (ticker)
            ORDER BY b.composite DESC NULLS LAST LIMIT 10
            """
        ).fetchall()
        quality = conn.execute(
            """
            SELECT severity, COUNT(*) FROM data_quality
            WHERE checked_at >= CURRENT_TIMESTAMP - INTERVAL 7 DAY
            GROUP BY severity ORDER BY severity
            """
        ).fetchall()
        alerts = conn.execute(
            """
            SELECT ticker, rule_id,
                   CASE WHEN delivered THEN 'entregada' ELSE 'pendiente' END,
                   message FROM alerts
            WHERE triggered_at >= CURRENT_TIMESTAMP - INTERVAL 1 DAY
            ORDER BY triggered_at DESC LIMIT 10
            """
        ).fetchall()

    lines = [
        f"# Stocks Tracker · informe {generated:%Y-%m-%d}",
        "",
        f"Generado: {generated.isoformat()}",
        f"Última sesión almacenada: {latest}",
        f"Cobertura: {instruments} instrumentos y {rows} velas.",
        "",
        "## Procedencia de datos",
        "",
    ]
    lines.extend(f"- {source or 'desconocida'}: {count} filas" for source, count in sources)
    if not sources:
        lines.append("- Sin precios almacenados.")

    lines.extend(["", "## Candidatos por puntuación", ""])
    if candidates:
        lines.extend([
            "| Ticker | Nombre | Score | Percentil | Cobertura |",
            "|---|---|---:|---:|---:|",
        ])
        for ticker, name, score, percentile, coverage in candidates:
            lines.append(
                f"| {ticker} | {str(name).replace('|', '/')} | {_number(score)} | "
                f"{_number(percentile, '.1%')} | {_number(coverage, '.1%')} |"
            )
    else:
        lines.append("Todavía no hay un ranking calculado.")

    lines.extend(["", "## Calidad (últimos 7 días)", ""])
    lines.extend(f"- {severity}: {count}" for severity, count in quality)
    if not quality:
        lines.append("- Sin comprobaciones registradas.")

    lines.extend(["", "## Alertas (últimas 24 horas)", ""])
    lines.extend(
        f"- **{ticker or 'mercado'} · {rule} · {state}** — {message}"
        for ticker, rule, state, message in alerts
    )
    if not alerts:
        lines.append("- Sin alertas.")

    lines.extend([
        "",
        "> Este documento resume datos y señales del programa; no es una recomendación de inversión.",
        "",
    ])
    return "\n".join(lines)


def write_report(output: Path | None = None) -> Path:
    target = output or (project_root() / "reports" / f"informe-{utcnow():%Y-%m-%d}.md")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(render(), encoding="utf-8")
    temporary.replace(target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporta la fotografia diaria a Markdown")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(write_report(args.output))


if __name__ == "__main__":
    main()
