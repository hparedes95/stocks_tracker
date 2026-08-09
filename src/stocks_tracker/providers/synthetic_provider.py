"""Proveedor sintetico: series realistas generadas localmente.

Existe por tres razones, todas practicas:

1. Los tests corren sin red y sin depender de que Yahoo este en pie.
2. Permite desarrollar la interfaz en entornos con la salida a internet cerrada.
3. Mas adelante alimenta al `SimulatedBroker` del bot.

Genera un movimiento browniano geometrico con deriva y volatilidad propias por
ticker, mas algo de reversion a la media y regimenes de volatilidad, para que
los indicadores produzcan valores en rangos crebles. NO pretende reproducir el
mercado: sirve para probar la maquinaria, no para sacar conclusiones.
"""

from __future__ import annotations

import hashlib
from datetime import date

import numpy as np
import pandas as pd

from .base import FUNDAMENTALS_COLUMNS, normalize_ohlcv

# Sectores GICS, para repartir el universo sinteticamente.
GICS_SECTORS = [
    "Information Technology", "Financials", "Health Care", "Consumer Discretionary",
    "Communication Services", "Industrials", "Consumer Staples", "Energy",
    "Utilities", "Materials", "Real Estate",
]


# Indices que no son precios sino niveles acotados: (minimo, media, maximo).
_MEAN_REVERTING = {
    "^VIX": (9.0, 18.0, 80.0),
    "^VXN": (11.0, 22.0, 85.0),
}


def _seed_for(ticker: str) -> int:
    """Semilla estable por ticker: el mismo ticker da siempre la misma serie."""
    return int(hashlib.blake2s(ticker.encode(), digest_size=4).hexdigest(), 16)


def _business_days(start: date, end: date) -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, end=end)


class SyntheticProvider:
    """Implementa PriceProvider y FundamentalsProvider."""

    name = "synthetic"

    def supports(self, ticker: str) -> bool:  # noqa: ARG002
        return True

    # ------------------------------------------------------------------
    # Precios
    # ------------------------------------------------------------------
    def fetch_ohlcv(
        self, tickers: list[str], start: date, end: date, interval: str = "1d"
    ) -> pd.DataFrame:
        frames = [self._one_series(t, start, end) for t in tickers]
        frames = [f for f in frames if not f.empty]
        if not frames:
            return normalize_ohlcv(pd.DataFrame(), self.name)
        return normalize_ohlcv(pd.concat(frames, ignore_index=True), self.name)

    def _one_series(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        rng = np.random.default_rng(_seed_for(ticker))
        idx = _business_days(start, end)
        n = len(idx)
        if n == 0:
            return pd.DataFrame()

        # Parametros propios de cada ticker, deterministas.
        base_price = float(rng.uniform(8, 400))
        annual_drift = float(rng.normal(0.07, 0.13))
        annual_vol = float(rng.uniform(0.16, 0.48))

        dt = 1.0 / 252.0
        mu = annual_drift * dt
        sigma = annual_vol * np.sqrt(dt)

        # Regimenes de volatilidad: tramos tranquilos y tramos agitados, que es
        # lo que hace que indicadores como ATR o Bollinger tengan sentido.
        regime = np.ones(n)
        pos = 0
        while pos < n:
            length = int(rng.integers(30, 160))
            level = float(rng.choice([0.7, 1.0, 1.0, 1.6, 2.4], p=[0.2, 0.3, 0.25, 0.15, 0.1]))
            regime[pos : pos + length] = level
            pos += length

        shocks = rng.normal(0.0, 1.0, n) * sigma * regime
        log_ret = mu - 0.5 * (sigma * regime) ** 2 + shocks

        # Ligera reversion a la media, para evitar derivas absurdas a 10 anos.
        cum = np.cumsum(log_ret)
        trend = np.linspace(0, cum[-1] if n else 0.0, n)
        cum = cum - 0.25 * (cum - trend)

        close = base_price * np.exp(cum)

        # El VIX no es un precio: es un nivel acotado con reversion fuerte a la
        # media. Un paseo aleatorio libre lo lleva a 700, y entonces la pagina
        # de macro muestra una cifra imposible que hace dudar del resto de los
        # datos sinteticos con razon.
        if ticker in _MEAN_REVERTING:
            floor, mean, ceiling = _MEAN_REVERTING[ticker]
            level = np.full(n, mean)
            noise = rng.normal(0.0, 0.09, n)
            for i in range(1, n):
                pull = 0.04 * (mean - level[i - 1])
                level[i] = level[i - 1] * (1 + noise[i]) + pull
            close = np.clip(level, floor, ceiling)

        intraday = np.abs(rng.normal(0.0, 0.008, n)) + 0.002
        high = close * (1 + intraday)
        low = close * (1 - intraday)
        open_ = np.empty(n)
        open_[0] = close[0]
        open_[1:] = close[:-1] * (1 + rng.normal(0.0, 0.004, n - 1))
        open_ = np.clip(open_, low, high)

        base_volume = float(rng.uniform(3e5, 3e7))
        volume = base_volume * np.exp(rng.normal(0.0, 0.45, n)) * (1 + 0.6 * (regime - 1))

        return pd.DataFrame(
            {
                "ticker": ticker,
                "date": idx,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "adj_close": close,
                "volume": volume.astype("int64"),
            }
        )

    # ------------------------------------------------------------------
    # Fundamentales y metadatos
    # ------------------------------------------------------------------
    def fetch_snapshot(self, tickers: list[str]) -> pd.DataFrame:
        rows = []
        today = date.today()
        for ticker in tickers:
            rng = np.random.default_rng(_seed_for(ticker) + 7)
            sector = GICS_SECTORS[_seed_for(ticker) % len(GICS_SECTORS)]

            # Rangos por sector, para que los z-scores intra-sector tengan sentido.
            pe_center = {
                "Information Technology": 30, "Health Care": 22, "Financials": 12,
                "Energy": 11, "Utilities": 17, "Consumer Staples": 20,
            }.get(sector, 19)

            # Huecos deliberados: en Europa faltan campos y el sistema tiene que
            # saber convivir con ello (por eso existe `completeness`).
            is_european = "." in ticker
            missing_prob = 0.35 if is_european else 0.05

            # rng y missing_prob se pasan como argumentos por defecto para que
            # queden ligados a ESTA iteracion y no al ultimo valor del bucle.
            def maybe(value: float, _rng=rng, _p=missing_prob) -> float | None:
                return None if _rng.random() < _p else float(value)

            pe = float(np.clip(rng.normal(pe_center, pe_center * 0.35), 4, 90))
            margin = float(np.clip(rng.normal(0.12, 0.09), -0.15, 0.45))
            growth = float(np.clip(rng.normal(0.07, 0.14), -0.35, 0.70))
            div_yield = float(max(0.0, rng.normal(0.024, 0.018)))

            rows.append(
                {
                    "ticker": ticker,
                    "as_of": today,
                    "trailing_pe": pe,
                    "forward_pe": maybe(pe * float(rng.uniform(0.82, 1.05))),
                    "peg_ratio": maybe(pe / max(growth * 100, 1)),
                    "price_to_book": maybe(float(np.clip(rng.lognormal(0.9, 0.6), 0.3, 25))),
                    "price_to_sales": maybe(float(np.clip(rng.lognormal(0.7, 0.8), 0.2, 30))),
                    "ev_to_ebitda": maybe(float(np.clip(rng.normal(13, 5), 2, 45))),
                    "ev_to_revenue": maybe(float(np.clip(rng.normal(3.2, 2.0), 0.2, 20))),
                    "fcf_yield": maybe(float(np.clip(rng.normal(0.045, 0.035), -0.08, 0.18))),
                    "earnings_yield": 1.0 / pe,
                    "gross_margin": maybe(float(np.clip(rng.normal(0.42, 0.18), 0.02, 0.92))),
                    "operating_margin": maybe(float(np.clip(margin * 1.4, -0.2, 0.55))),
                    "profit_margin": margin,
                    "roe": maybe(float(np.clip(rng.normal(0.15, 0.12), -0.4, 0.75))),
                    "roa": maybe(float(np.clip(rng.normal(0.07, 0.06), -0.2, 0.35))),
                    "revenue_growth_yoy": growth,
                    "earnings_growth_yoy": maybe(float(np.clip(growth * rng.uniform(0.5, 2.0), -0.6, 1.5))),
                    "debt_to_equity": maybe(float(np.clip(rng.lognormal(0.2, 0.9), 0.0, 8))),
                    "net_debt_to_ebitda": maybe(float(np.clip(rng.normal(1.9, 1.7), -2.0, 9))),
                    "current_ratio": maybe(float(np.clip(rng.normal(1.6, 0.7), 0.2, 6))),
                    "dividend_yield": div_yield,
                    "payout_ratio": maybe(float(np.clip(rng.normal(0.45, 0.30), 0.0, 1.6))),
                    "shares_outstanding": float(rng.uniform(5e7, 8e9)),
                    "beta": float(np.clip(rng.normal(1.0, 0.35), 0.15, 2.6)),
                    "market_cap": float(rng.lognormal(23.5, 1.5)),
                    "currency": "EUR" if is_european else "USD",
                }
            )
        return pd.DataFrame(rows, columns=FUNDAMENTALS_COLUMNS)

    def fetch_metadata(self, tickers: list[str]) -> pd.DataFrame:
        rows = []
        for ticker in tickers:
            rng = np.random.default_rng(_seed_for(ticker) + 13)
            is_european = "." in ticker
            is_index = ticker.startswith("^")
            is_crypto = ticker.endswith("-USD")
            is_future = ticker.endswith("=F")
            is_fx = ticker.endswith("=X")

            if is_index:
                asset_class, sector = "index", None
            elif is_crypto:
                asset_class, sector = "crypto", None
            elif is_future:
                asset_class, sector = "commodity", None
            elif is_fx:
                asset_class, sector = "fx", None
            else:
                asset_class = "equity"
                sector = GICS_SECTORS[_seed_for(ticker) % len(GICS_SECTORS)]

            rows.append(
                {
                    "ticker": ticker,
                    "name": f"{ticker} (datos sinteticos)",
                    "asset_class": asset_class,
                    "exchange": "MCE" if is_european else "NMS",
                    "currency": "EUR" if is_european else "USD",
                    "country": "ES" if ticker.endswith(".MC") else ("US" if not is_european else "EU"),
                    "gics_sector": sector,
                    "gics_industry": None,
                    "market_cap": float(rng.lognormal(23.5, 1.5)) if asset_class == "equity" else None,
                }
            )
        return pd.DataFrame(rows)
