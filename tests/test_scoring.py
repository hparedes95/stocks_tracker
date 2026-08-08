"""Tests del scoring: z-scores, cobertura, renormalizacion de pesos."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stocks_tracker.core.config import FactorConfig, SubmetricSpec
from stocks_tracker.core.scoring import (
    Z_CLIP,
    apply_regime_multipliers,
    compute_scores,
    sanitize,
    weights_hash,
    winsorize,
    zscore,
    zscore_by_group,
)


def test_zscore_is_centered_and_scaled():
    data = pd.Series(np.random.default_rng(0).normal(50, 10, 500))
    result = zscore(data, robust=False)
    assert abs(result.mean()) < 0.05
    assert abs(result.std(ddof=0) - 1.0) < 0.05


def test_robust_zscore_resists_outliers():
    """Un solo valor extremo no debe aplastar al resto.

    Es la razon de usar mediana/MAD. Con media y desviacion, el outlier infla la
    escala y comprime a TODOS los valores normales contra el cero: dejan de
    distinguirse entre si, que es justo lo que el z-score debia medir.
    """
    rng = np.random.default_rng(5)
    normal_values = rng.normal(10, 1, 50)
    data = pd.Series(np.append(normal_values, 100.0))

    robust = zscore(data, robust=True)
    classic = zscore(data, robust=False)

    # Dispersion de los valores NORMALES (se excluye el outlier).
    robust_spread = robust.iloc[:-1].std(ddof=0)
    classic_spread = classic.iloc[:-1].std(ddof=0)

    assert robust_spread == pytest.approx(1.0, abs=0.35)
    assert classic_spread < 0.2
    assert robust_spread > classic_spread * 3


def test_zscore_is_clipped():
    """Un z de 30 solo dice 'outlier', igual que un z de 5, pero arrasa la media."""
    data = pd.Series([10.0] * 40 + [1e9])
    result = zscore(data, robust=True)
    assert result.max() <= Z_CLIP + 1e-9
    assert result.min() >= -Z_CLIP - 1e-9


def test_zscore_with_no_dispersion_returns_zeros():
    """Si todo el grupo vale lo mismo, no hay nada que distinguir."""
    data = pd.Series([7.0] * 20)
    result = zscore(data)
    assert (result == 0.0).all()


def test_zscore_with_near_zero_mad_does_not_explode():
    """MAD practicamente cero: la desviacion tipica toma el relevo."""
    data = pd.Series([5.0] * 30 + [5.0001] * 2 + [9.0])
    result = zscore(data, robust=True)
    assert np.isfinite(result).all()
    assert result.abs().max() <= Z_CLIP + 1e-9


def test_winsorize_clips_tails():
    data = pd.Series(list(range(100)))
    result = winsorize(data, 0.05, 0.95)
    assert result.min() >= data.quantile(0.05)
    assert result.max() <= data.quantile(0.95)


def test_sanitize_discards_impossible_values():
    """Un PER negativo no es 'muy barato': la empresa pierde dinero."""
    spec = SubmetricSpec(field="trailing_pe", sign=-1, min_valid=0, max_valid=200)
    data = pd.Series([-15.0, 12.0, 500.0, np.inf, 25.0])
    result = sanitize(data, spec)
    assert pd.isna(result.iloc[0])   # negativo
    assert pd.isna(result.iloc[2])   # por encima del maximo
    assert pd.isna(result.iloc[3])   # infinito
    assert result.iloc[1] == 12.0
    assert result.iloc[4] == 25.0


def test_zscore_by_group_uses_sector_context(scoring_frame):
    """El PER se compara dentro del sector, no contra todo el mercado."""
    result = zscore_by_group(scoring_frame, "trailing_pe", "gics_sector", min_group=8)
    for sector in scoring_frame["gics_sector"].unique():
        mask = scoring_frame["gics_sector"] == sector
        assert abs(result[mask].mean()) < 0.6


def test_small_groups_fall_back_to_full_universe():
    """Un grupo de tres valores no da un z-score informativo."""
    df = pd.DataFrame(
        {
            "gics_sector": ["A"] * 3 + ["B"] * 20,
            "metric": list(np.random.default_rng(1).normal(10, 2, 23)),
        }
    )
    result = zscore_by_group(df, "metric", "gics_sector", min_group=8)
    assert result.notna().all()


def test_weights_hash_is_stable_and_order_independent():
    a = weights_hash({"value": 0.5, "momentum": 0.5})
    b = weights_hash({"momentum": 0.5, "value": 0.5})
    c = weights_hash({"value": 0.6, "momentum": 0.4})
    assert a == b
    assert a != c


def _config() -> FactorConfig:
    return FactorConfig(
        raw={
            "peer_group": "gics_sector",
            "min_group_size": 8,
            "winsorize": [0.02, 0.98],
            "robust_zscore": True,
            "coverage_floor": 0.4,
            "factors": {
                "value": {
                    "submetrics": [
                        {"field": "trailing_pe", "sign": -1, "min_valid": 0, "max_valid": 200},
                        {"field": "price_to_book", "sign": -1},
                    ]
                },
                "momentum": {
                    "submetrics": [
                        {"field": "mom_12_1", "sign": 1},
                        {"field": "roc_6m", "sign": 1},
                    ]
                },
            },
        }
    )


def test_compute_scores_produces_ranking(scoring_frame):
    weights = {"value": 0.5, "momentum": 0.5}
    scores, contributions = compute_scores(
        scoring_frame, weights, _config(), group_col="gics_sector"
    )

    assert len(scores) == len(scoring_frame)
    assert scores["composite"].notna().any()
    assert scores["composite_pctile"].between(0, 1).all()
    assert not contributions.empty
    assert set(contributions["factor"]) <= {"value", "momentum"}


def test_weights_renormalize_when_a_factor_is_missing(scoring_frame):
    """Sin datos de un factor, su peso se reparte; no se penaliza dos veces."""
    frame = scoring_frame.copy()
    frame["trailing_pe"] = np.nan
    frame["price_to_book"] = np.nan

    weights = {"value": 0.5, "momentum": 0.5}
    scores, contributions = compute_scores(
        frame, weights, _config(), group_col="gics_sector"
    )

    assert scores["composite"].notna().any()
    momentum = contributions[contributions["factor"] == "momentum"]
    # Al quedar solo, momentum debe absorber todo el peso.
    assert momentum["weight"].dropna().round(6).eq(1.0).all()


def test_contributions_sum_to_composite(scoring_frame):
    """La suma de las contribuciones debe reconstruir el score exactamente.

    Si no cuadran, la explicacion que se muestra al usuario no corresponde al
    numero que ve, que es peor que no explicar nada.
    """
    weights = {"value": 0.5, "momentum": 0.5}
    scores, contributions = compute_scores(
        scoring_frame, weights, _config(), group_col="gics_sector"
    )
    totals = contributions.groupby("ticker")["contribution"].sum()
    merged = scores.set_index("ticker")["composite"].dropna()
    common = totals.index.intersection(merged.index)
    assert len(common) > 0
    np.testing.assert_allclose(
        totals.loc[common].to_numpy(), merged.loc[common].to_numpy(), atol=1e-9
    )


def test_regime_multipliers_renormalize_to_one():
    cfg = FactorConfig(
        raw={"regime_multipliers": {"risk_off": {"lowvol": 1.5, "momentum": 0.6}}}
    )
    weights = {"lowvol": 0.3, "momentum": 0.4, "value": 0.3}
    adjusted = apply_regime_multipliers(weights, "risk_off", cfg)
    assert sum(adjusted.values()) == pytest.approx(1.0)
    # En risk-off la estabilidad debe pesar mas que antes y el momentum menos.
    assert adjusted["lowvol"] > weights["lowvol"]
    assert adjusted["momentum"] < weights["momentum"]


def test_unknown_regime_leaves_weights_untouched():
    weights = {"value": 0.5, "momentum": 0.5}
    assert apply_regime_multipliers(weights, "desconocido", FactorConfig(raw={})) == weights


def test_empty_frame_returns_empty():
    scores, contributions = compute_scores(pd.DataFrame(), {"value": 1.0})
    assert scores.empty and contributions.empty
