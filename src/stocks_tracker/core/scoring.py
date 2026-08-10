"""Factores de estilo y score compuesto explicable.

El pipeline, por fecha y por grupo de comparacion:
  sanear -> winsorizar -> z-score intra-sector -> agregar por factor
  -> penalizar por cobertura -> renormalizar pesos -> componer -> explicar

La pieza que importa no es el numero final sino `factor_contributions`: es lo
que permite decir *por que* aparece cada valor en el ranking.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

from .config import FactorConfig, SubmetricSpec, get_factor_config

FACTOR_ORDER = ["value", "growth", "quality", "momentum", "lowvol", "dividend", "technical"]


def weights_hash(weights: dict[str, float]) -> str:
    """Identificador estable del juego de pesos usado.

    Permite guardar varios rankings (uno por preset) sin pisarse, y saber a
    posteriori con que configuracion se genero cada score.
    """
    payload = json.dumps({k: round(float(v), 6) for k, v in sorted(weights.items())})
    return hashlib.blake2s(payload.encode(), digest_size=6).hexdigest()


# Nombres de los perfiles para la interfaz. La clave es la del YAML.
PRESET_LABELS = {
    "balanced": "Equilibrado",
    "value": "Valor",
    "growth": "Crecimiento",
    "dividend": "Dividendo",
    "momentum": "Momentum",
    "bot_core": "Nucleo del bot",
}

PRESET_DESCRIPTIONS = {
    "balanced": "Reparto parejo entre los siete factores. El punto de partida "
                "si no tienes una preferencia clara.",
    "value": "Prima lo barato respecto a sus beneficios y libros, con un filtro "
             "de calidad para no comprar empresas baratas por buenas razones.",
    "growth": "Prima el crecimiento de ventas y beneficios, y el momentum que "
              "suele acompanarlo. Es el perfil mas volatil.",
    "dividend": "Prima el reparto sostenible: rentabilidad por dividendo, "
                "calidad y baja volatilidad. Payout desbocado penaliza.",
    "momentum": "Prima lo que ya lo esta haciendo bien. Funciona en tendencia "
                "y sufre en los giros de mercado.",
    "bot_core": "El ranking que usa el bot de trading para elegir candidatos. "
                "Momentum atemperado con calidad, valor y dividendo.",
}


def preset_hash(preset: str) -> str:
    """Hash de pesos de un preset con nombre.

    Los scores de todos los perfiles conviven en la misma tabla, distinguidos
    por este hash. Cualquier lectura de `factor_scores` que no filtre por el
    devolveria una fila por perfil y multiplicaria los valores del ranking.
    """
    return weights_hash(get_factor_config().weights(preset))


def preset_names() -> list[str]:
    """Perfiles configurados, con `balanced` siempre el primero."""
    names = list(get_factor_config().presets)
    names.sort(key=lambda n: (n != "balanced", n))
    return names


def preset_label(preset: str) -> str:
    return PRESET_LABELS.get(preset, preset.capitalize())


def sanitize(series: pd.Series, spec: SubmetricSpec) -> pd.Series:
    """Descarta valores imposibles antes de puntuar.

    Un PER negativo no es "muy barato": significa que la empresa pierde dinero,
    y tratarlo como valor extremo bueno invertiria el sentido del factor.
    """
    out = pd.to_numeric(series, errors="coerce")
    if spec.min_valid is not None:
        out = out.where(out >= spec.min_valid)
    if spec.max_valid is not None:
        out = out.where(out <= spec.max_valid)
    return out.replace([np.inf, -np.inf], np.nan)


def winsorize(series: pd.Series, lower: float, upper: float) -> pd.Series:
    """Recorta las colas a percentiles dados. Limita outliers y errores de datos."""
    valid = series.dropna()
    if valid.empty:
        return series
    lo, hi = valid.quantile(lower), valid.quantile(upper)
    return series.clip(lower=lo, upper=hi)


# Los z-scores se recortan a este rango. Mas alla, la magnitud exacta es ruido:
# lo unico que dice un z de 30 es "es un outlier", igual que un z de 5, pero al
# entrar en una media ponderada arrasa con todos los demas factores.
Z_CLIP = 5.0


def zscore(series: pd.Series, robust: bool = True) -> pd.Series:
    """Z-score de una serie. `robust` usa mediana/MAD en vez de media/desviacion.

    En finanzas las colas son gruesas: un solo valor extremo desplaza la media
    y aplasta a todos los demas. La mediana no se inmuta.
    """
    valid = series.dropna()
    if len(valid) < 2:
        return pd.Series(np.nan, index=series.index)

    if robust:
        center = valid.median()
        mad = (valid - center).abs().median()
        scale = mad * 1.4826  # convierte MAD en equivalente a desviacion tipica
        # El MAD puede ser practicamente cero si medio grupo tiene el mismo
        # valor, y entonces dividir por el produce z-scores de 30. Cuando pasa,
        # la desviacion tipica es una escala mas informativa.
        if not np.isfinite(scale) or scale < 1e-9:
            scale = valid.std(ddof=0)
    else:
        center = valid.mean()
        scale = valid.std(ddof=0)

    # Escala nula o despreciable frente al nivel de la serie: no hay dispersion
    # real que medir y todo el grupo es equivalente.
    reference = max(abs(float(center)), 1e-9)
    if not np.isfinite(scale) or scale <= reference * 1e-9:
        return pd.Series(0.0, index=series.index).where(series.notna())

    return ((series - center) / scale).clip(-Z_CLIP, Z_CLIP)


def zscore_by_group(
    df: pd.DataFrame,
    value_col: str,
    group_col: str,
    winsor: tuple[float, float] = (0.02, 0.98),
    min_group: int = 8,
    robust: bool = True,
) -> pd.Series:
    """Z-score dentro de cada grupo, con fallback al universo completo.

    Comparar el PER de un banco con el de una tecnologica no dice nada. Pero un
    grupo de tres valores tampoco da un z-score informativo, asi que por debajo
    de `min_group` se compara contra todo el universo.
    """
    result = pd.Series(np.nan, index=df.index, dtype=float)
    groups = df[group_col].fillna("__SIN_GRUPO__")

    small: list[int] = []
    for _, idx in groups.groupby(groups).groups.items():
        idx = pd.Index(idx)
        n_valid = df.loc[idx, value_col].notna().sum()
        if n_valid < min_group:
            small.extend(idx)
            continue
        vals = winsorize(df.loc[idx, value_col], *winsor)
        result.loc[idx] = zscore(vals, robust=robust)

    if small:
        idx = pd.Index(small)
        vals = winsorize(df.loc[idx, value_col], *winsor)
        result.loc[idx] = zscore(vals, robust=robust)

    return result


def build_factor(
    df: pd.DataFrame, spec, cfg: FactorConfig, group_col: str
) -> tuple[pd.Series, pd.Series]:
    """Calcula el z-score de un factor y su cobertura de datos.

    Devuelve (z, coverage). `coverage` es la fraccion de sub-metricas
    disponibles: sirve para no fiarse igual de un valor con datos completos que
    de uno al que le faltan la mitad de los campos.
    """
    z_parts: list[pd.Series] = []
    available = pd.Series(0.0, index=df.index)

    for sub in spec.submetrics:
        if sub.field not in df.columns:
            continue
        clean = sanitize(df[sub.field], sub)
        if clean.notna().sum() == 0:
            continue
        tmp = df[[group_col]].copy()
        tmp["_v"] = clean * sub.sign
        z = zscore_by_group(
            tmp,
            "_v",
            group_col,
            winsor=cfg.winsorize,
            min_group=cfg.min_group_size,
            robust=cfg.robust_zscore,
        )
        z_parts.append(z)
        available += clean.notna().astype(float)

    n_subs = len(spec.submetrics)
    if not z_parts or n_subs == 0:
        return (
            pd.Series(np.nan, index=df.index),
            pd.Series(0.0, index=df.index),
        )

    z_matrix = pd.concat(z_parts, axis=1)
    factor_z = z_matrix.mean(axis=1, skipna=True)
    coverage = (available / n_subs).clip(0.0, 1.0)
    return factor_z, coverage


def compute_scores(
    df: pd.DataFrame,
    weights: dict[str, float],
    cfg: FactorConfig | None = None,
    group_col: str = "gics_sector",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Score compuesto por valor, mas el desglose de contribuciones.

    `df` es una foto de una fecha: una fila por ticker con indicadores y
    fundamentales ya unidos.

    Devuelve (scores, contribuciones).
    """
    cfg = cfg or get_factor_config()
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    work = df.copy()
    if group_col not in work.columns:
        work[group_col] = "__SIN_GRUPO__"
    work[group_col] = work[group_col].fillna("__SIN_GRUPO__")

    factor_z: dict[str, pd.Series] = {}
    factor_cov: dict[str, pd.Series] = {}

    for name, spec in cfg.factors.items():
        if name not in weights:
            continue
        z, cov = build_factor(work, spec, cfg, group_col)
        # Penalizacion por cobertura: media confianza, medio peso efectivo.
        z = z * np.sqrt(cov.clip(0.0, 1.0))
        # Por debajo del suelo, el factor no es informativo: se excluye.
        z = z.where(cov >= cfg.coverage_floor)
        factor_z[name] = z
        factor_cov[name] = cov

    if not factor_z:
        return pd.DataFrame(), pd.DataFrame()

    z_df = pd.DataFrame(factor_z, index=work.index)
    cov_df = pd.DataFrame(factor_cov, index=work.index)

    # Renormalizacion: si a un valor le falta un factor, su peso se reparte
    # entre los presentes. Asi no se penaliza dos veces por datos incompletos.
    w = pd.Series({k: float(v) for k, v in weights.items() if k in z_df.columns})
    present = z_df.notna()
    weight_matrix = present.mul(w, axis=1)
    weight_sum = weight_matrix.sum(axis=1).replace(0.0, np.nan)
    norm_weights = weight_matrix.div(weight_sum, axis=0)

    composite = (z_df.fillna(0.0) * norm_weights.fillna(0.0)).sum(axis=1)
    composite = composite.where(weight_sum.notna())

    scores = pd.DataFrame(index=work.index)
    scores["ticker"] = work["ticker"] if "ticker" in work.columns else work.index
    for name in FACTOR_ORDER:
        scores[f"{name}_z"] = z_df[name] if name in z_df.columns else np.nan
    scores["composite"] = composite
    scores["coverage"] = cov_df.mean(axis=1)
    scores["peer_group"] = work[group_col]

    # Percentil global y rango dentro del sector.
    scores["composite_pctile"] = composite.rank(pct=True)
    scores["composite_rank_sector"] = (
        composite.groupby(work[group_col]).rank(ascending=False, method="min").astype("Int64")
    )

    # Contribuciones: es lo que alimenta la explicacion de cada tarjeta.
    contrib_rows = []
    for name in z_df.columns:
        contrib_rows.append(
            pd.DataFrame(
                {
                    "ticker": scores["ticker"],
                    "factor": name,
                    "zscore": z_df[name],
                    "weight": norm_weights[name],
                    "contribution": z_df[name] * norm_weights[name],
                }
            )
        )
    contributions = (
        pd.concat(contrib_rows, ignore_index=True) if contrib_rows else pd.DataFrame()
    )
    if not contributions.empty:
        contributions = contributions.dropna(subset=["contribution"])

    return scores.reset_index(drop=True), contributions.reset_index(drop=True)


def apply_regime_multipliers(
    weights: dict[str, float], regime: str, cfg: FactorConfig | None = None
) -> dict[str, float]:
    """Ajusta los pesos segun el semaforo de riesgo y renormaliza a 1.

    En risk-off se prima estabilidad y calidad; en risk-on, momentum. El
    resultado siempre suma 1 para que los scores sigan siendo comparables.
    """
    cfg = cfg or get_factor_config()
    mult = cfg.regime_multipliers(regime)
    if not mult:
        return dict(weights)
    adjusted = {k: v * float(mult.get(k, 1.0)) for k, v in weights.items()}
    total = sum(adjusted.values())
    if total <= 0:
        return dict(weights)
    return {k: v / total for k, v in adjusted.items()}
