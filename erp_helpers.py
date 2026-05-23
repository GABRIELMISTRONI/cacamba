# -*- coding: utf-8 -*-
"""Funções compartilhadas do ERP (validação, paginação, formatação)."""
import re
import time
from datetime import date

from database import CAPACIDADES_M3, VALORES_LOCACAO, get_conn

_CACHE = {}
_CACHE_TTL = 45


def cache_get(key):
    item = _CACHE.get(key)
    if not item:
        return None
    if time.time() - item["ts"] > _CACHE_TTL:
        del _CACHE[key]
        return None
    return item["data"]


def cache_set(key, data):
    _CACHE[key] = {"ts": time.time(), "data": data}


def cache_clear(*prefixes):
    if not prefixes:
        _CACHE.clear()
        return
    for k in list(_CACHE.keys()):
        if any(k.startswith(p) for p in prefixes):
            del _CACHE[k]


def brl(value):
  try:
    n = float(value or 0)
  except (TypeError, ValueError):
    n = 0.0
  s = f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
  return f"R$ {s}"


def cpf_valido(cpf):
    n = re.sub(r"\D", "", cpf or "")
    if len(n) != 11 or n == n[0] * 11:
        return False
    for j in range(9, 11):
        soma = sum(int(n[i]) * ((j + 1) - i) for i in range(j))
        dig = (soma * 10 % 11) % 10
        if dig != int(n[j]):
            return False
    return True


def cnpj_valido(cnpj):
    n = re.sub(r"\D", "", cnpj or "")
    if len(n) != 14 or n == n[0] * 14:
        return False
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6] + pesos1
    d1 = sum(int(n[i]) * pesos1[i] for i in range(12))
    d1 = 11 - d1 % 11
    d1 = 0 if d1 >= 10 else d1
    if d1 != int(n[12]):
        return False
    d2 = sum(int(n[i]) * pesos2[i] for i in range(13))
    d2 = 11 - d2 % 11
    d2 = 0 if d2 >= 10 else d2
    return d2 == int(n[13])


def valores_locacao(cfg=None):
    if cfg is None:
        cfg = load_config()
    out = {}
    for cap in CAPACIDADES_M3:
        key = f"valor_locacao_{cap}m3"
        try:
            out[cap] = float(cfg.get(key, VALORES_LOCACAO.get(cap, 0)))
        except (TypeError, ValueError):
            out[cap] = float(VALORES_LOCACAO.get(cap, 0))
    return out


def valor_locacao(cap, cfg=None):
    try:
        return valores_locacao(cfg)[int(cap)]
    except (TypeError, ValueError, KeyError):
        return 0.0


def load_config():
    cached = cache_get("config")
    if cached is not None:
        return cached
    conn = get_conn()
    cfg = {r["chave"]: r["valor"] for r in conn.execute("SELECT chave, valor FROM config").fetchall()}
    conn.close()
    cache_set("config", cfg)
    return cfg


def paginate_query(conn, sql_count, sql_data, params, page, per_page=25):
    page = max(1, page)
    per_page = max(10, min(per_page, 100))
    total = conn.execute(sql_count, params).fetchone()[0]
    offset = (page - 1) * per_page
    rows = conn.execute(sql_data + " LIMIT ? OFFSET ?", params + (per_page, offset)).fetchall()
    pages = max(1, (total + per_page - 1) // per_page)
    return rows, total, page, pages, per_page
