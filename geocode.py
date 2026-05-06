# -*- coding: utf-8 -*-
"""Geocodificação via OpenStreetMap Nominatim (gratuito; exige user-agent único)."""
try:
    from geopy.exc import GeocoderServiceError, GeocoderTimedOut, GeocoderUnavailable
    from geopy.geocoders import Nominatim
    GEOPY_OK = True
except ImportError:
    GEOPY_OK = False

USER_AGENT = "CacambasGestaoEducacional/1.0 (projeto_bauru_SP)"


def _só_digitos(s):
    return "".join(c for c in (s or "") if c.isdigit())


def _montar_consultas(rua, numero, cep, quadra=None, bairro=None):
    """Retorna lista de consultas em ordem de especificidade (melhor primeiro)."""
    consultas = []
    r = (rua or "").strip()
    n = (numero or "").strip()
    b = (bairro or "").strip()
    d = _só_digitos(cep)
    cep_fmt = f"{d[:5]}-{d[5:]}" if len(d) == 8 else ""

    # Consulta 1: mais completa — rua + número + bairro + CEP
    if r and n and b and cep_fmt:
        consultas.append(f"{r}, {n}, {b}, {cep_fmt}, Bauru, São Paulo, Brasil")
    # Consulta 2: rua + número + CEP (sem bairro)
    if r and n and cep_fmt:
        consultas.append(f"{r}, {n}, {cep_fmt}, Bauru, São Paulo, Brasil")
    # Consulta 3: só CEP (funciona bem no Nominatim para Bauru)
    if cep_fmt:
        consultas.append(f"{cep_fmt}, Bauru, São Paulo, Brasil")
    # Consulta 4: rua + bairro (sem número)
    if r and b:
        consultas.append(f"{r}, {b}, Bauru, São Paulo, Brasil")
    # Consulta 5: só rua
    if r:
        consultas.append(f"{r}, Bauru, São Paulo, Brasil")

    return consultas


def geocodificar_obra(rua, numero, cep, bairro=None, quadra=None):
    """Tenta múltiplas consultas em ordem de especificidade."""
    if not GEOPY_OK:
        return None
    consultas = _montar_consultas(rua, numero, cep, quadra=quadra, bairro=bairro)
    for consulta in consultas:
        resultado = _geocode_consulta(consulta)
        if resultado:
            return resultado
    return None


def geocodificar_obra_bauru(endereco_obra: str):
    """Compatível: uma única linha de endereço."""
    if not GEOPY_OK:
        return None
    linha = (endereco_obra or "").strip()
    if not linha:
        return None
    consulta = linha if "Bauru" in linha else f"{linha}, Bauru, São Paulo, Brasil"
    return _geocode_consulta(consulta)


def _geocode_consulta(consulta: str):
    if not GEOPY_OK:
        return None
    try:
        geo = Nominatim(user_agent=USER_AGENT, timeout=14)
        loc = geo.geocode(
            consulta,
            exactly_one=True,
            language="pt",
            country_codes="br",
        )
        if loc and loc.latitude is not None and loc.longitude is not None:
            return float(loc.latitude), float(loc.longitude)
    except Exception:
        return None
    return None
