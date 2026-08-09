"""Cadena de proveedores con relevo en tiempo de descarga.

El fallback que habia antes solo actuaba al CONSTRUIR el proveedor: si
yfinance se importaba bien, nunca se probaba el siguiente. Pero esa no es la
forma en que Yahoo se rompe. La forma real es que el import funciona, la
llamada funciona, y lo que vuelve esta vacio o le faltan la mitad de los
valores. Con el relevo solo en construccion, eso se traducia en un almacen sin
actualizar y nadie enterandose.

Aqui el relevo ocurre por ticker y despues de intentarlo: lo que el primero no
consigue traer se le pide al siguiente.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .base import ProviderError, empty_ohlcv, normalize_ohlcv


class ChainPriceProvider:
    """Recorre varios proveedores hasta cubrir todos los tickers."""

    def __init__(self, providers: list) -> None:
        if not providers:
            raise ProviderError("La cadena de proveedores esta vacia.")
        self._providers = providers
        self.name = "+".join(p.name for p in providers)

    @property
    def primary(self):
        return self._providers[0]

    def supports(self, ticker: str) -> bool:
        return any(p.supports(ticker) for p in self._providers)

    def fetch_ohlcv(
        self, tickers: list[str], start: date, end: date, interval: str = "1d"
    ) -> pd.DataFrame:
        pending = list(tickers)
        frames: list[pd.DataFrame] = []
        requests_used = 0
        by_source: dict[str, int] = {}
        relayed: dict[str, str] = {}

        for provider in self._providers:
            if not pending:
                break

            # No se le piden a un proveedor los tickers que declara no cubrir:
            # gastaria una peticion para recibir un 404.
            askable = [t for t in pending if provider.supports(t)]
            if not askable:
                continue

            try:
                df = provider.fetch_ohlcv(askable, start, end, interval)
            except ProviderError:
                # Un proveedor caido no interrumpe la cadena: para eso existe.
                continue

            requests_used += int(df.attrs.get("requests_used", 0))
            if df.empty:
                continue

            served = set(df["ticker"].unique())
            if provider is not self._providers[0]:
                relayed.update({t: provider.name for t in served})
            by_source[provider.name] = len(served)
            frames.append(df)
            pending = [t for t in pending if t not in served]

        result = (
            normalize_ohlcv(pd.concat(frames, ignore_index=True), "chain")
            if frames
            else empty_ohlcv()
        )
        # `normalize_ohlcv` sobrescribiria la procedencia real de cada fila con
        # el nombre de la cadena, y perder eso haria imposible detectar despues
        # las series con fuentes mezcladas.
        if frames and "source" in result.columns:
            origin = pd.concat(frames, ignore_index=True)[["ticker", "date", "source"]]
            result = result.drop(columns=["source"]).merge(
                origin.drop_duplicates(subset=["ticker", "date"]),
                on=["ticker", "date"], how="left",
            )

        result.attrs["failed_tickers"] = pending
        result.attrs["requests_used"] = requests_used
        result.attrs["rows_by_source"] = by_source
        result.attrs["relayed_tickers"] = relayed
        return result

    # ------------------------------------------------------------------
    # Fundamentales: el primero que sepa servirlos
    # ------------------------------------------------------------------
    def fetch_snapshot(self, tickers: list[str]) -> pd.DataFrame:
        return self._first_that_can("fetch_snapshot", tickers)

    def fetch_metadata(self, tickers: list[str]) -> pd.DataFrame:
        return self._first_that_can("fetch_metadata", tickers)

    def _first_that_can(self, method: str, tickers: list[str]) -> pd.DataFrame:
        errors: list[str] = []
        for provider in self._providers:
            fn = getattr(provider, method, None)
            if fn is None:
                continue
            try:
                df = fn(tickers)
            except ProviderError as exc:
                errors.append(f"{provider.name}: {exc}")
                continue
            if df is not None and not df.empty:
                return df
        if errors:
            raise ProviderError("; ".join(errors))
        return pd.DataFrame()
