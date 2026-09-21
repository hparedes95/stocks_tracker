from __future__ import annotations

from datetime import date

import pytest

from stocks_tracker.providers.base import ProviderError
from stocks_tracker.providers.ecb_provider import EcbFxProvider, interpretar_csv

CSV = """KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-09-18,1.1742
EXR.D.GBP.EUR.SP00.A,D,GBP,EUR,SP00,A,2026-09-18,0.8631
"""


def test_interpreta_tipos_oficiales_con_la_convencion_del_programa():
    frame = interpretar_csv(CSV, ["EURUSD=X", "EURGBP=X"])

    assert set(frame["ticker"]) == {"EURUSD=X", "EURGBP=X"}
    assert frame.set_index("ticker").loc["EURUSD=X", "close"] == pytest.approx(1.1742)
    assert (frame["open"] == frame["close"]).all()
    assert (frame["adj_close"] == frame["close"]).all()
    assert (frame["volume"] == 0).all()


def test_no_devuelve_divisas_no_solicitadas():
    frame = interpretar_csv(CSV, ["EURUSD=X"])

    assert frame["ticker"].tolist() == ["EURUSD=X"]


def test_un_cambio_de_esquema_del_bce_no_pasa_como_respuesta_vacia():
    with pytest.raises(ProviderError, match="ha cambiado"):
        interpretar_csv("fecha,valor\n2026-09-18,1.17\n", ["EURUSD=X"])


def test_solo_declara_los_pares_eur_que_realmente_cubre():
    provider = EcbFxProvider()

    assert provider.supports("EURUSD=X")
    assert provider.supports("EURGBP=X")
    assert not provider.supports("AAPL")
    assert not provider.supports("USDEUR=X")


class _Response:
    status_code = 200
    text = CSV


class _Session:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response()


def test_agrupa_varias_divisas_en_una_sola_peticion():
    session = _Session()
    provider = EcbFxProvider(session=session)

    frame = provider.fetch_ohlcv(
        ["EURUSD=X", "AAPL", "EURGBP=X"],
        date(2026, 9, 1), date(2026, 9, 20),
    )

    assert len(session.calls) == 1
    assert "D.USD+GBP.EUR.SP00.A" in session.calls[0][0]
    assert set(frame["ticker"]) == {"EURUSD=X", "EURGBP=X"}
    assert frame.attrs["requests_used"] == 1

