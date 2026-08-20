"""SEC EDGAR: fundamentales oficiales de empresas estadounidenses.

TRES ADVERTENCIAS ANTES DE NADA, Y LAS TRES IMPORTAN

1. SOLO CUBRE ESTADOS UNIDOS. La SEC regula a las empresas que cotizan alli.
   BBVA.MC, SAB.MC, UNI.MC y todo el IBEX no estan aqui y no van a estar. Esto
   resuelve como mucho la mitad del universo, y precisamente NO la mitad que
   peor se porta.

2. NO ESTA VERIFICADO CONTRA LA API REAL. Se escribio sin salida a internet, asi
   que lo comprobado es que interpreta bien la forma de respuesta que la SEC
   documenta. Que la API se comporte como su documentacion es una suposicion.

3. Y POR ESO NO ALIMENTA EL RANKING. Es la decision de diseno importante de este
   modulo, y va en contra de lo que parece obvio.

   Lo obvio seria: "la SEC es la fuente oficial, luego sus numeros deberian
   sustituir a los de Yahoo". El problema es COMO falla cada uno. Un dato malo
   de Yahoo suele ser evidente —un margen del 900 %, un PER de 3—. Un dato malo
   de aqui seria una etiqueta XBRL mal mapeada: `Revenues` frente a
   `RevenueFromContractWithCustomerExcludingAssessedTax` dan cifras distintas,
   las dos perfectamente plausibles, y ninguna comprobacion de rango las
   distingue. Seria un numero creible y equivocado alimentando el ranking, que
   es exactamente el fallo que este proyecto entero existe para evitar.

   Asi que se guarda con `source='sec'` y sirve para CONTRASTAR lo que dice
   Yahoo, que es la funcion que de verdad aporta: dos fuentes independientes
   sobre el mismo hecho contable. Conectarlo al ranking se hace despues de
   comprobar el mapeo contra unas cuantas cuentas anuales reales, a mano.

LO QUE SI APORTA Y NADIE MAS DA

La FECHA DE PUBLICACION. Yahoo da la foto de hoy y no dice desde cuando es
cierta; la SEC da cada dato con el dia en que se presento. Eso es lo unico que
permite construir fundamentales punto-en-el-tiempo de verdad, y sin ellos
cualquier backtest de factores fundamentales esta mirando el futuro.

LAS REGLAS DE LA CASA

La SEC exige un User-Agent con contacto real y limita a 10 peticiones por
segundo. No son sugerencias: bloquean por IP. Aqui se va muy por debajo.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime

import pandas as pd
import requests

from .base import ProviderError, RateLimitError

_BASE = "https://data.sec.gov/api/xbrl/companyconcept"
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_TIMEOUT = 30

# La SEC permite 10/s. Se va a 3/s: el margen no cuesta nada aqui —son unas
# decenas de valores, no cientos— y un bloqueo por IP se arregla escribiendo un
# correo a la SEC.
PAUSA_SEGUNDOS = 0.35

VARIABLE_DE_CONTACTO = "SEC_CONTACT_EMAIL"

# Que etiqueta XBRL corresponde a cada campo nuestro.
#
# Las alternativas van EN ORDEN y se coge la primera que exista. No es un
# detalle: la misma magnitud tiene etiquetas distintas segun el sector y segun
# el ano en que se presento, y coger la equivocada da una cifra plausible y
# falsa. Por eso este mapeo es lo primero que hay que verificar contra cuentas
# reales antes de conectar nada de esto al ranking.
CONCEPTOS: dict[str, tuple[str, ...]] = {
    "ingresos": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "beneficio_neto": ("NetIncomeLoss",),
    "activos": ("Assets",),
    "pasivos": ("Liabilities",),
    "fondos_propios": ("StockholdersEquity",),
    "flujo_operativo": ("NetCashProvidedByUsedInOperatingActivities",),
    "acciones": (
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
    ),
}


def contacto() -> str | None:
    """Correo de contacto para el User-Agent. La SEC lo exige."""
    valor = os.environ.get(VARIABLE_DE_CONTACTO, "").strip()
    return valor or None


class SecProvider:
    """Fundamentales oficiales de EE. UU. NO implementa PriceProvider."""

    name = "sec"

    def __init__(self, email: str | None = None) -> None:
        self._email = email or contacto()
        self.requests_used = 0
        self.ha_respondido = False
        self._session = requests.Session()
        self._cik: dict[str, str] = {}

    @property
    def configurado(self) -> bool:
        return self._email is not None

    def supports(self, ticker: str) -> bool:
        """Solo lo que puede estar registrado en la SEC.

        Se descarta por la FORMA del ticker: un sufijo de mercado (`.MC`, `.DE`)
        es una empresa europea con seguridad, y preguntar por ella gasta una
        peticion para recibir un 404. Que un ticker sin sufijo este de verdad
        registrado no se sabe hasta preguntar, y eso es correcto: aqui no se
        adivina.
        """
        if not self.configurado or not ticker:
            return False
        return "." not in ticker and "-" not in ticker and "=" not in ticker

    def _cabeceras(self) -> dict[str, str]:
        # La SEC bloquea por IP a quien no se identifica. No es una sugerencia
        # de su documentacion: es la condicion de uso.
        return {"User-Agent": f"stocks_tracker ({self._email})",
                "Accept-Encoding": "gzip, deflate"}

    def _pedir(self, url: str) -> dict:
        if not self.configurado:
            raise ProviderError(
                f"La SEC exige identificarse: falta {VARIABLE_DE_CONTACTO} en el "
                "entorno o en el .env. Sin eso bloquean por IP."
            )
        try:
            respuesta = self._session.get(url, headers=self._cabeceras(),
                                          timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise ProviderError(f"La SEC no responde: {exc}") from exc

        self.requests_used += 1
        if respuesta.status_code == 429:
            raise RateLimitError("La SEC esta limitando las peticiones.")
        if respuesta.status_code == 404:
            raise ProviderError(f"La SEC no tiene ese dato: {url}")
        if respuesta.status_code != 200:
            raise ProviderError(f"La SEC devolvio {respuesta.status_code}")
        try:
            return respuesta.json()
        except ValueError as exc:
            raise ProviderError("La SEC no devolvio JSON") from exc

    def cik_de(self, ticker: str) -> str | None:
        """El identificador de la SEC, que no es el ticker.

        El CIK es lo que la SEC usa para identificar a una empresa, y a
        diferencia del ticker no se reutiliza nunca. Se descarga la lista entera
        una vez —una peticion— y se guarda en memoria.
        """
        if not self._cik:
            datos = self._pedir(_TICKERS_URL)
            self._cik = {
                str(fila["ticker"]).upper(): f"CIK{int(fila['cik_str']):010d}"
                for fila in (datos or {}).values()
                if fila.get("ticker") and fila.get("cik_str") is not None
            }
        return self._cik.get(ticker.upper())

    def fetch_facts(self, ticker: str) -> pd.DataFrame:
        """Los hechos contables publicados, cada uno con SU fecha de publicacion."""
        cik = self.cik_de(ticker)
        if cik is None:
            raise ProviderError(f"{ticker} no esta registrado en la SEC.")

        filas: list[dict] = []
        for campo, etiquetas in CONCEPTOS.items():
            for etiqueta in etiquetas:
                try:
                    datos = self._pedir(f"{_BASE}/{cik}/us-gaap/{etiqueta}.json")
                except ProviderError:
                    # Que una etiqueta no exista para esta empresa es NORMAL:
                    # por eso hay alternativas en orden. Se prueba la siguiente.
                    time.sleep(PAUSA_SEGUNDOS)
                    continue
                filas.extend(interpretar_concepto(datos, ticker, campo, etiqueta))
                time.sleep(PAUSA_SEGUNDOS)
                break

        if filas:
            self.ha_respondido = True
        return pd.DataFrame(
            filas,
            columns=["ticker", "campo", "etiqueta", "valor", "fin_periodo",
                     "publicado", "formulario", "fiscal"],
        )


def interpretar_concepto(datos: dict, ticker: str, campo: str,
                         etiqueta: str) -> list[dict]:
    """Saca los hechos de una respuesta de `companyconcept`.

    LO QUE HACE ESTE MODULO Y NO HACE NINGUN OTRO PROVEEDOR: se queda con
    `filed`, el dia en que la empresa PRESENTO ese numero.

    Yahoo da la foto de hoy y no dice desde cuando es cierta. Sin la fecha de
    publicacion no se puede saber que se sabia en marzo de 2023, y un backtest
    de factores fundamentales sin eso esta mirando el futuro: puntua 2023 con
    balances que no existian hasta 2024.

    Se descartan los hechos SIN `filed`. Un dato oficial sin fecha de
    publicacion no sirve para lo unico que hace especial a esta fuente, y
    guardarlo con la fecha de hoy seria inventarsela.
    """
    unidades = (datos or {}).get("units") or {}
    filas: list[dict] = []

    for unidad, hechos in unidades.items():
        # Solo USD y numero de acciones. Las demas unidades de XBRL —USD por
        # accion, porcentajes— mezclan magnitudes distintas bajo el mismo campo.
        if unidad not in ("USD", "shares"):
            continue
        for hecho in hechos or []:
            publicado = hecho.get("filed")
            valor = hecho.get("val")
            if not publicado or valor is None:
                continue
            filas.append({
                "ticker": ticker,
                "campo": campo,
                "etiqueta": etiqueta,
                "valor": float(valor),
                "fin_periodo": _fecha(hecho.get("end")),
                "publicado": _fecha(publicado),
                "formulario": str(hecho.get("form") or ""),
                "fiscal": f"{hecho.get('fy') or ''}{hecho.get('fp') or ''}",
            })
    return filas


def _fecha(texto) -> date | None:
    if not texto:
        return None
    try:
        return datetime.strptime(str(texto), "%Y-%m-%d").date()
    except ValueError:
        return None


def lo_sabido_en(hechos: pd.DataFrame, cuando: date) -> pd.DataFrame:
    """Lo que se podia saber en una fecha: solo lo PUBLICADO antes de ella.

    Es la funcion por la que merece la pena todo lo demas. Filtra por
    `publicado` y NO por `fin_periodo`, y confundir las dos es el fallo clasico
    del backtest de fundamentales: el cierre del cuarto trimestre de 2023
    termina el 31 de diciembre, y no se publica hasta febrero de 2024. Filtrando
    por el fin del periodo, un backtest usaria en enero un balance que nadie
    conocia hasta seis semanas despues.

    De cada campo se queda el hecho mas reciente por fecha de publicacion, que
    es lo que un inversor tendria delante ese dia.
    """
    columnas = ["ticker", "campo", "valor", "fin_periodo", "publicado"]
    if hechos.empty:
        return pd.DataFrame(columns=columnas)

    p = hechos.copy()
    p["publicado"] = pd.to_datetime(p["publicado"], errors="coerce").dt.date
    conocidos = p[p["publicado"].notna() & (p["publicado"] <= cuando)]
    if conocidos.empty:
        return pd.DataFrame(columns=columnas)

    conocidos = conocidos.sort_values(["publicado", "fin_periodo"])
    ultimos = conocidos.groupby(["ticker", "campo"], as_index=False).tail(1)
    return ultimos[columnas].reset_index(drop=True)
