"""Cuatro decisiones personales, no cientos de parámetros técnicos."""
import json
import math

DEFAULTS = {"horizon_months": 6, "roundtrip_pct": 1.0,
            "max_loss_pct": 20.0, "max_cluster_pct": 35.0, "confirmed": False,
            "recommendation_mode": "rules"}


def load(conn) -> dict:
    row = conn.execute("SELECT preferences FROM investor_preferences WHERE singleton=1").fetchone()
    return {**DEFAULTS, **json.loads(row[0])} if row else dict(DEFAULTS)


def save(conn, profile: dict) -> None:
    if profile.get('horizon_months') not in (1, 3, 6):
        raise ValueError("Elige horizonte de 1, 3 o 6 meses.")
    for key, maximum in (("roundtrip_pct", 20), ("max_loss_pct", 60), ("max_cluster_pct", 100)):
        val = profile.get(key)
        if not isinstance(val, (int, float)) or not math.isfinite(val) or not 0 < val <= maximum:
            raise ValueError(f"Valor inválido: {key}.")
    clean = {key: profile.get(key, default) for key, default in DEFAULTS.items()}
    if clean['recommendation_mode'] not in ('rules', 'validated'):
        raise ValueError("Modo del asesor inválido.")
    clean['confirmed'] = bool(clean['confirmed'])
    conn.execute("INSERT OR REPLACE INTO investor_preferences VALUES (1, ?)", [json.dumps(clean)])
