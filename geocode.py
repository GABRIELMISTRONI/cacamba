# -*- coding: utf-8 -*-
"""Geocodificação via OpenStreetMap Nominatim (gratuito; exige user-agent único)."""
import time

try:
    from geopy.exc import GeocoderServiceError, GeocoderTimedOut, GeocoderUnavailable
    from geopy.geocoders import Nominatim
    GEOPY_OK = True
except ImportError:
    GEOPY_OK = False

USER_AGENT = "CacambasGestaoEducacional/1.0 (projeto_bauru_SP)"
_GEOCODER = None
_LAST_CALL = 0.0


def _geocoder():
    global _GEOCODER
    if not GEOPY_OK:
        return None
    if _GEOCODER is None:
        _GEOCODER = Nominatim(user_agent=USER_AGENT, timeout=14)
    return _GEOCODER


def _throttle():
    global _LAST_CALL
    elapsed = time.time() - _LAST_CALL
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    _LAST_CALL = time.time()


def _só_digitos(s):
    return "".join(c for c in (s or "") if c.isdigit())


def _montar_consultas(rua, numero, cep, quadra=None, bairro=None):
    consultas = []
    r = (rua or "").strip()
    n = (numero or "").strip()
    b = (bairro or "").strip()
    d = _só_digitos(cep)
    cep_fmt = f"{d[:5]}-{d[5:]}" if len(d) == 8 else ""

    if r and n and b and cep_fmt:
        consultas.append(f"{r}, {n}, {b}, {cep_fmt}, Bauru, São Paulo, Brasil")
    if r and n and cep_fmt:
        consultas.append(f"{r}, {n}, {cep_fmt}, Bauru, São Paulo, Brasil")
    if cep_fmt:
        consultas.append(f"{cep_fmt}, Bauru, São Paulo, Brasil")
    if r and b:
        consultas.append(f"{r}, {b}, Bauru, São Paulo, Brasil")
    if r:
        consultas.append(f"{r}, Bauru, São Paulo, Brasil")
    return consultas


def geocodificar_obra(rua, numero, cep, bairro=None, quadra=None):
    if not GEOPY_OK:
        return None
    for consulta in _montar_consultas(rua, numero, cep, quadra=quadra, bairro=bairro):
        resultado = _geocode_consulta(consulta)
        if resultado:
            return resultado
    return None


def geocodificar_obra_bauru(endereco_obra: str):
    if not GEOPY_OK:
        return None
    linha = (endereco_obra or "").strip()
    if not linha:
        return None
    consulta = linha if "Bauru" in linha else f"{linha}, Bauru, São Paulo, Brasil"
    return _geocode_consulta(consulta)


def _geocode_consulta(consulta: str):
    geo = _geocoder()
    if not geo:
        return None
    try:
        _throttle()
        loc = geo.geocode(
            consulta,
            exactly_one=True,
            language="pt",
            country_codes="br",
        )
        if loc and loc.latitude is not None and loc.longitude is not None:
            return float(loc.latitude), float(loc.longitude)
    except (GeocoderTimedOut, GeocoderUnavailable, GeocoderServiceError, Exception):
        return None
    return None
