"""SnapTrade Personal: GET allowlist only, no trading or paid refresh calls.

Contract: docs.snaptrade.com/docs/request-signatures and /reference/Account Information.
Personal authentication deliberately omits userId and userSecret.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from urllib.parse import urlencode

import requests


class SnapTradeError(RuntimeError):
    """Safe user-facing error; never contains response bodies or credentials."""


class Client:
    def __init__(self, client_id, consumer_key, *, session=None):
        if not all(isinstance(v, str) and 8 <= len(v.strip()) <= 512 for v in (client_id, consumer_key)):
            raise SnapTradeError('Introduce Client ID y Consumer Key de SnapTrade Personal.')
        self._id, self._key = client_id.strip(), consumer_key.strip()
        self._session = session or requests.Session()

    def get(self, path):
        if path not in ('/accounts/all', '/authorizations') and not re.fullmatch(
                r'/accounts/[0-9a-fA-F-]{36}/(?:positions/all|balances)', path):
            raise SnapTradeError('Consulta fuera de la lista permitida de solo lectura.')
        query = urlencode({'clientId': self._id, 'timestamp': int(time.time())})
        payload = json.dumps({'content': None, 'path': '/api/v1' + path, 'query': query},
                             sort_keys=True, separators=(',', ':'))
        signature = base64.b64encode(hmac.new(self._key.encode(), payload.encode(), hashlib.sha256).digest()).decode()
        try:
            # No redirects: credentials/signatures never travel to another origin.
            with self._session.get('https://api.snaptrade.com' + path + '?' + query,
                                   headers={'Signature': signature, 'Accept': 'application/json'},
                                   timeout=(10, 30), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    explanations = {401: 'Credenciales no aceptadas; revisa la clave Personal.',
                                    403: 'Acceso no autorizado; revisa permisos y cuenta Personal.',
                                    429: 'Límite de consultas alcanzado; espera unos minutos.',
                                    503: 'SnapTrade o el bróker no están disponibles temporalmente.'}
                    raise SnapTradeError(explanations.get(response.status_code, 'SnapTrade no completó la consulta.'))
                data = bytearray()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > 8 * 1024 * 1024:
                        raise SnapTradeError('Respuesta demasiado grande; no se modifica la cartera.')
                return json.loads(data)
        except (requests.RequestException, ValueError):
            raise SnapTradeError('No se pudo leer SnapTrade. Se conserva la última copia válida.') from None

    def accounts(self):
        connections = self.get('/authorizations')
        accounts = self.get('/accounts/all')
        if not isinstance(connections, list) or not isinstance(accounts, dict) or not isinstance(accounts.get('results'), list):
            raise SnapTradeError('Formato de cuentas no reconocido.')
        links = {c.get('id'): c for c in connections if isinstance(c, dict)}
        result = []
        for account in accounts['results']:
            if not isinstance(account, dict) or not isinstance(account.get('id'), str):
                raise SnapTradeError('Formato de cuenta no reconocido.')
            link = links.get(account.get('connection_id'), {})
            brokerage = link.get('brokerage') or {}
            if not isinstance(brokerage, dict):
                raise SnapTradeError('Formato de conexión no reconocido.')
            if (account.get('kind') != 'investment' or account.get('is_paper') is not False or
                    brokerage.get('slug') != 'ETORO'):
                continue
            result.append({**account, 'connection_read_only': link.get('type') == 'read',
                           'connection_enabled': link.get('disabled') is False})
        return result

    def snapshot(self, account_id):
        account = next((a for a in self.accounts() if a.get('id') == account_id), None)
        if not account:
            raise SnapTradeError('La cuenta eToro real seleccionada ya no está disponible.')
        if not account['connection_read_only']:
            raise SnapTradeError('Conecta eToro con permiso Read only antes de continuar.')
        if not account['connection_enabled']:
            raise SnapTradeError('Conexión caducada o deshabilitada. Reconéctala en SnapTrade.')
        return {'account': account, 'positions': self.get(f'/accounts/{account_id}/positions/all'),
                'balances': self.get(f'/accounts/{account_id}/balances')}
