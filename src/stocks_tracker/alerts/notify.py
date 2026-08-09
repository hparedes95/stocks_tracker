"""Entrega de alertas por los canales configurados.

Reglas que atraviesan el modulo:

- **Los secretos nunca se registran.** Ni en logs, ni en mensajes de error, ni
  truncados. Si un token aparece en un fichero de log, esta comprometido.
- **Un canal que falla no tumba a los demas.** Si Telegram no responde, el
  fichero local sigue recibiendo la alerta: perder el aviso por un problema de
  red seria el peor resultado posible.
- **El fichero local siempre esta disponible.** Es el canal que no depende de
  nada externo, y por eso es el que va activado por defecto.
"""

from __future__ import annotations

import json
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

import requests

from ..core.config import project_root
from .evaluate import Alert
from .rules import ChannelConfig, get_channels

_TIMEOUT = 20

SEVERITY_ICON = {
    "baja": "·", "media": "•", "alta": "!", "critica": "!!",
}


@dataclass
class DeliveryResult:
    channel: str
    sent: int
    ok: bool
    detail: str = ""


def _redact(text: str) -> str:
    """Elimina posibles secretos de un mensaje de error antes de mostrarlo."""
    out = text
    for name in ("TELEGRAM_BOT_TOKEN", "SMTP_PASSWORD", "SMTP_USER"):
        value = os.environ.get(name, "")
        if value and len(value) > 4:
            out = out.replace(value, f"<{name}>")
    return out


def format_alert(alert: Alert) -> str:
    icon = SEVERITY_ICON.get(alert.severity, "•")
    return f"{icon} {alert.message}"


def format_digest(alerts: list[Alert]) -> str:
    """Resumen agrupado por gravedad. Un mensaje, no veinte."""
    if not alerts:
        return "Sin alertas."

    by_severity: dict[str, list[Alert]] = {}
    for alert in alerts:
        by_severity.setdefault(alert.severity, []).append(alert)

    lines = [f"*Stocks Tracker* · {len(alerts)} alertas"]
    for severity in ("critica", "alta", "media", "baja"):
        group = by_severity.get(severity)
        if not group:
            continue
        lines.append(f"\n*{severity.upper()}* ({len(group)})")
        lines.extend(f"  {format_alert(a)}" for a in group)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Canales
# --------------------------------------------------------------------------
def deliver_file(alerts: list[Alert], settings: dict) -> DeliveryResult:
    """Escribe una linea JSON por alerta. Siempre disponible, sin dependencias."""
    path = project_root() / settings.get("path", "data/alerts.jsonl")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for alert in alerts:
                fh.write(
                    json.dumps(
                        {
                            "id": alert.id, "rule_id": alert.rule_id,
                            "ticker": alert.ticker, "severity": alert.severity,
                            "triggered_at": alert.triggered_at.isoformat(),
                            "message": alert.message, "payload": alert.payload,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        return DeliveryResult("file", len(alerts), True, str(path))
    except OSError as exc:
        return DeliveryResult("file", 0, False, _redact(str(exc)))


def deliver_telegram(alerts: list[Alert], settings: dict) -> DeliveryResult:
    """Un unico mensaje con el resumen, no uno por alerta."""
    token = os.environ.get(settings.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), "").strip()
    chat_id = os.environ.get(settings.get("chat_id_env", "TELEGRAM_CHAT_ID"), "").strip()

    if not token or not chat_id:
        return DeliveryResult(
            "telegram", 0, False,
            "Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en el entorno.",
        )

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": format_digest(alerts),
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=_TIMEOUT,
        )
        if response.status_code != 200:
            # El cuerpo de la respuesta puede repetir el token de la URL.
            return DeliveryResult(
                "telegram", 0, False,
                f"Telegram devolvio {response.status_code}",
            )
        return DeliveryResult("telegram", len(alerts), True)
    except requests.RequestException as exc:
        return DeliveryResult("telegram", 0, False, _redact(str(exc)))


def deliver_email(alerts: list[Alert], settings: dict) -> DeliveryResult:
    host = os.environ.get(settings.get("smtp_host_env", "SMTP_HOST"), "").strip()
    user = os.environ.get(settings.get("user_env", "SMTP_USER"), "").strip()
    password = os.environ.get(settings.get("password_env", "SMTP_PASSWORD"), "")
    to_address = os.environ.get(settings.get("to_env", "ALERT_EMAIL"), "").strip()
    port = int(settings.get("smtp_port", 587))

    if not (host and user and password and to_address):
        return DeliveryResult(
            "email", 0, False,
            "Faltan SMTP_HOST, SMTP_USER, SMTP_PASSWORD o ALERT_EMAIL.",
        )

    message = EmailMessage()
    message["Subject"] = f"Stocks Tracker · {len(alerts)} alertas"
    message["From"] = user
    message["To"] = to_address
    message.set_content(format_digest(alerts).replace("*", ""))

    try:
        with smtplib.SMTP(host, port, timeout=_TIMEOUT) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(message)
        return DeliveryResult("email", len(alerts), True)
    except (smtplib.SMTPException, OSError) as exc:
        return DeliveryResult("email", 0, False, _redact(str(exc)))


_DELIVERERS = {
    "file": deliver_file,
    "telegram": deliver_telegram,
    "email": deliver_email,
}


def deliver(alerts: list[Alert],
            channels: tuple[ChannelConfig, ...] | None = None) -> list[DeliveryResult]:
    """Entrega por todos los canales activos.

    Se recorren todos aunque uno falle: perder un aviso porque Telegram estaba
    caido, teniendo el fichero local disponible, seria absurdo.
    """
    if not alerts:
        return []

    results: list[DeliveryResult] = []
    for channel in channels if channels is not None else get_channels():
        if not channel.enabled:
            continue
        deliverer = _DELIVERERS.get(channel.name)
        if deliverer is None:
            results.append(
                DeliveryResult(channel.name, 0, False, "Canal no implementado")
            )
            continue
        results.append(deliverer(alerts, channel.settings))
    return results


def test_channel(name: str) -> DeliveryResult:
    """Envia un mensaje de prueba por un canal. Util desde la interfaz."""
    from ..core.timeutils import utcnow

    probe = Alert(
        id="prueba", rule_id="prueba", ticker=None, triggered_at=utcnow(),
        message="Mensaje de prueba de Stocks Tracker. Si lo recibes, el canal funciona.",
        severity="baja", payload={},
    )
    for channel in get_channels():
        if channel.name == name:
            deliverer = _DELIVERERS.get(name)
            if deliverer is None:
                return DeliveryResult(name, 0, False, "Canal no implementado")
            return deliverer([probe], channel.settings)
    return DeliveryResult(name, 0, False, "Canal no configurado en alerts.yaml")


def channel_status() -> list[dict]:
    """Estado de cada canal, sin revelar ningun secreto."""
    out = []
    for channel in get_channels():
        missing: list[str] = []
        if channel.name == "telegram":
            for key in ("bot_token_env", "chat_id_env"):
                env_name = channel.settings.get(key, "")
                if env_name and not os.environ.get(env_name):
                    missing.append(env_name)
        elif channel.name == "email":
            for key in ("smtp_host_env", "user_env", "password_env", "to_env"):
                env_name = channel.settings.get(key, "")
                if env_name and not os.environ.get(env_name):
                    missing.append(env_name)
        # El canal de fichero no necesita nada: escribe en el propio proyecto.

        out.append(
            {
                "canal": channel.name,
                "activo": channel.enabled,
                "listo": channel.enabled and not missing,
                "faltan": ", ".join(missing) if missing else "",
            }
        )
    return out
