# -*- coding: utf-8 -*-
"""
Sistema de gestão para empresa de caçambas — Bauru/SP
Execute:  pip install -r requirements.txt
Acesse:   http://127.0.0.1:5000
"""
import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
from flask import Flask, Response, flash, redirect, render_template, request, session, url_for

from database import CAPACIDADES_M3, DEFAULT_MOTORISTAS, VALORES_LOCACAO, get_conn, init_db, seed_if_empty
from erp_helpers import (
    brl as _brl,
    cache_clear,
    cache_get,
    cache_set,
    cnpj_valido,
    cpf_valido,
    load_config,
    paginate_query,
    valor_locacao as _valor_locacao,
    valores_locacao,
)
from geocode import geocodificar_obra, geocodificar_obra_bauru

load_dotenv()

_FLASK_ENV = os.environ.get("FLASK_ENV", "development").lower()
_IS_PRODUCTION = _FLASK_ENV == "production"

app = Flask(__name__)
_SECRET = os.environ.get("SECRET_KEY", "").strip()
if _IS_PRODUCTION and not _SECRET:
    raise RuntimeError("Defina SECRET_KEY no ambiente para rodar em produção.")
app.secret_key = _SECRET or "cacambas-dev-only-change-in-production"
if _IS_PRODUCTION:
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "1") == "1",
    )

APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()
ENABLE_RESET = os.environ.get("ENABLE_RESET", "0" if _IS_PRODUCTION else "1").strip() == "1"

BAURU_LAT = -22.3145
BAURU_LON = -49.0643
MOTORISTAS = DEFAULT_MOTORISTAS

init_db()
seed_if_empty()

STATUS_LABELS = {
    "pendente": "Pendente",
    "confirmado": "Confirmado",
    "no_endereco": "No endereço",
    "finalizado": "Finalizado",
    "cancelado": "Cancelado",
    "disponivel": "Disponível",
    "em_uso": "Em uso",
    "manutencao": "Manutenção",
}


# --- Helpers ---

def _registrar_historico(conn, pedido_id, acao, detalhes=""):
    conn.execute(
        """INSERT INTO historico_pedidos (pedido_id, acao, detalhes, created_at)
           VALUES (?,?,?,?)""",
        (pedido_id, acao, detalhes, datetime.now().strftime("%Y-%m-%d %H:%M")),
    )


@app.template_filter("status_label")
def status_label_filter(code):
    if not code:
        return "—"
    return STATUS_LABELS.get(code, str(code).replace("_", " ").title())


@app.template_filter("brl")
def brl_filter(value):
    return _brl(value)


def _digits(s):
    return "".join(c for c in (s or "") if c.isdigit())

def _cep_norm(s):
    return _digits(s)[:8]

def _cep_ok(s):
    return len(_cep_norm(s)) == 8

def _cpf_ok(cpf):
    return cpf_valido(cpf)

def _cnpj_ok(cnpj):
    return cnpj_valido(cnpj)

def _cap_ok(cap):
    try:
        return int(cap) in CAPACIDADES_M3
    except (TypeError, ValueError):
        return False

def _fmt_endereco(cep, rua, quadra, numero, bairro):
    partes = []
    if rua:    partes.append(rua)
    if quadra: partes.append(f"Quadra {quadra}")
    if numero: partes.append(f"nº {numero}")
    if bairro: partes.append(bairro)
    linha = " — ".join(partes) if partes else ""
    d = _cep_norm(cep)
    if len(d) == 8:
        linha += f" — CEP {d[:5]}-{d[5:]}"
    return linha or "—"

def _geocode_ped(ped):
    keys = list(ped.keys())
    bairro = (ped["obra_bairro"] or "").strip() if "obra_bairro" in keys else ""
    quadra = (ped["obra_quadra"] or "").strip() if "obra_quadra" in keys else ""
    coords = geocodificar_obra(
        ped["obra_rua"], ped["obra_numero"], ped["obra_cep"],
        bairro=bairro or None, quadra=quadra or None,
    )
    if coords:
        return coords
    txt = (ped["endereco_obra"] or "").strip()
    if txt:
        return geocodificar_obra_bauru(txt)
    return None

def _stats():
    cached = cache_get("dashboard_stats")
    if cached is not None:
        return cached
    conn = get_conn()
    hoje_iso = date.today().isoformat()
    clientes = conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]
    frota = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM cacambas GROUP BY status").fetchall()}
    pedidos = conn.execute(
        """SELECT
             SUM(CASE WHEN status IN ('pendente','confirmado','no_endereco') THEN 1 ELSE 0 END) AS pedidos_abertos,
             SUM(CASE WHEN pago=0 AND status NOT IN ('pendente','cancelado') THEN 1 ELSE 0 END) AS a_receber_qtd,
             SUM(CASE WHEN status='confirmado' THEN 1 ELSE 0 END) AS entregas_pendentes,
             SUM(CASE WHEN status='no_endereco' AND data_fim_prevista < ? THEN 1 ELSE 0 END) AS retiradas_vencidas,
             SUM(CASE WHEN status='pendente' THEN 1 ELSE 0 END) AS pendentes,
             COALESCE(SUM(CASE WHEN pago=0 AND status NOT IN ('pendente','cancelado') THEN valor_total ELSE 0 END), 0) AS valor_a_receber,
             COALESCE(SUM(CASE WHEN pago=1 AND status NOT IN ('pendente','cancelado') THEN valor_total ELSE 0 END), 0) AS valor_recebido_mes
           FROM pedidos""",
        (hoje_iso,),
    ).fetchone()
    s = {
        "clientes": clientes,
        "disponiveis": frota.get("disponivel", 0),
        "em_uso": frota.get("em_uso", 0),
        "manutencao": frota.get("manutencao", 0),
        "pedidos_abertos": pedidos["pedidos_abertos"] or 0,
        "a_receber": pedidos["a_receber_qtd"] or 0,
        "entregas_hoje": pedidos["entregas_pendentes"] or 0,
        "retiradas_vencidas": pedidos["retiradas_vencidas"] or 0,
        "pendentes": pedidos["pendentes"] or 0,
        "valor_a_receber": pedidos["valor_a_receber"] or 0,
        "valor_recebido_mes": pedidos["valor_recebido_mes"] or 0,
    }
    total_frota = s["disponiveis"] + s["em_uso"] + s["manutencao"]
    s["total_frota"] = total_frota
    s["taxa_ocupacao"] = round(s["em_uso"] / total_frota * 100) if total_frota > 0 else 0
    conn.close()
    cache_set("dashboard_stats", s)
    return s


def _cache_invalidate():
    cache_clear("dashboard_stats", "sidebar_stats")


def _get_config():
    return load_config()


def _dias_locacao(config=None):
    if config is None:
        config = _get_config()
    try:
        return max(1, min(int(config.get("dias_locacao","7")), 30))
    except ValueError:
        return 7


def _get_motoristas():
    conn = get_conn()
    rows = [r["nome"] for r in conn.execute("SELECT nome FROM motoristas ORDER BY nome").fetchall()]
    conn.close()
    return rows


@app.context_processor
def inject_global_context():
    cfg = load_config()
    cached_sb = cache_get("sidebar_stats")
    if cached_sb is None:
        conn = get_conn()
        hoje_iso = date.today().isoformat()
        stats_row = conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM pedidos WHERE status='no_endereco' AND data_fim_prevista < ?) AS vencidas,
                 (SELECT COUNT(*) FROM pedidos WHERE status='pendente') AS pendentes,
                 (SELECT COUNT(*) FROM pedidos WHERE status='confirmado') AS confirmar""",
            (hoje_iso,),
        ).fetchone()
        conn.close()
        cached_sb = {
            "vencidas": stats_row["vencidas"] or 0,
            "pendentes": stats_row["pendentes"] or 0,
            "confirmar": stats_row["confirmar"] or 0,
        }
        cache_set("sidebar_stats", cached_sb)
    motoristas = cache_get("motoristas")
    if motoristas is None:
        conn = get_conn()
        motoristas = [r["nome"] for r in conn.execute("SELECT nome FROM motoristas ORDER BY nome").fetchall()]
        conn.close()
        cache_set("motoristas", motoristas)
    return {
        "empresa_nome": cfg.get("empresa_nome", "Caçambas Bauru"),
        "empresa_fone": cfg.get("empresa_fone", ""),
        "dias_locacao": _dias_locacao(cfg),
        "motoristas": motoristas,
        "stats_sidebar": cached_sb,
        "status_labels": STATUS_LABELS,
        "enable_reset": ENABLE_RESET,
        "auth_enabled": bool(APP_PASSWORD),
        "valores_locacao": valores_locacao(cfg),
    }


@app.before_request
def _auth_gate():
    if not APP_PASSWORD:
        return
    if request.endpoint in (None, "static", "login"):
        return
    if session.get("authed"):
        return
    return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not APP_PASSWORD:
        return redirect(url_for("index"))
    if request.method == "POST":
        if request.form.get("senha", "") == APP_PASSWORD:
            session["authed"] = True
            nxt = request.args.get("next") or url_for("index")
            if not nxt.startswith("/"):
                nxt = url_for("index")
            return redirect(nxt)
        flash("Senha incorreta.", "erro")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Sessão encerrada.", "ok")
    return redirect(url_for("login") if APP_PASSWORD else url_for("index"))


# --- DASHBOARD ---

@app.route("/")
def index():
    conn = get_conn()
    ultimos = conn.execute(
        """SELECT p.id, p.cliente_id, p.status, p.pago, p.endereco_obra,
                  p.obra_rua, p.obra_numero, p.obra_bairro, p.obra_quadra,
                  p.data_fim_prevista, p.capacidade_m3,
                  c.nome AS cliente_nome
           FROM pedidos p JOIN clientes c ON c.id=p.cliente_id
           ORDER BY p.id DESC LIMIT 10"""
    ).fetchall()
    hoje = date.today()
    amanha_iso = (hoje + timedelta(days=1)).isoformat()
    vencendo_amanha = conn.execute(
        "SELECT COUNT(*) FROM pedidos WHERE status='no_endereco' AND data_fim_prevista=?",
        (amanha_iso,)
    ).fetchone()[0]
    por_mes = conn.execute(
        """SELECT substr(COALESCE(NULLIF(data_fim_real,''), NULLIF(data_inicio,''), criado_em), 1, 7) AS mes,
                  COUNT(*) AS qtd,
                  COALESCE(SUM(CASE WHEN pago=1 THEN valor_total ELSE 0 END),0) AS valor_pago,
                  COALESCE(SUM(valor_total),0) AS valor_total
           FROM pedidos
           WHERE status NOT IN ('pendente','cancelado')
           GROUP BY mes
           ORDER BY mes ASC
           LIMIT 6"""
    ).fetchall()
    conn.close()
    return render_template("index.html",
        stats=_stats(), ultimos=ultimos,
        hoje=hoje.isoformat(), vencendo_amanha=vencendo_amanha,
        por_mes=por_mes)


# --- CLIENTES ---

@app.route("/clientes")
def clientes():
    busca = request.args.get("busca", "").strip()
    conn = get_conn()
    like = f"%{busca}%"
    # Normaliza busca: remove acentos para NORM(), e strip prefix Q./Quadra para quadra
    import re as _re, unicodedata as _ud
    def _norm(s): return _ud.normalize("NFD", str(s)).encode("ascii","ignore").decode("ascii").lower()
    busca_norm = _norm(busca)
    norm_like = f"%{busca_norm}%"
    busca_quadra = _re.sub(r"(?i)^(quadra\s+|q\.?\s*)", "", busca).strip()
    like_quadra = f"%{busca_quadra}%" if busca_quadra else like
    if busca:
        # Busca nos campos principais do cliente (com NORM para acentos)
        rows = conn.execute(
            """SELECT id,tipo_pessoa,nome,cpf,cnpj,razao_social,telefone,email,rua,numero
               FROM clientes
               WHERE NORM(nome) LIKE ? OR cpf LIKE ? OR cnpj LIKE ? OR NORM(razao_social) LIKE ?
                  OR NORM(rua) LIKE ? OR NORM(email) LIKE ?
               ORDER BY nome""",
            (norm_like, like, like, norm_like, norm_like, norm_like),
        ).fetchall()
        # Busca nos endereços secundários: rua, quadra (normalizada), bairro e apelido
        ids_end = [r[0] for r in conn.execute(
            """SELECT DISTINCT cliente_id FROM enderecos_cliente
               WHERE NORM(rua) LIKE ? OR NORM(bairro) LIKE ? OR quadra LIKE ? OR NORM(apelido) LIKE ?""",
            (norm_like, norm_like, like_quadra, norm_like),
        ).fetchall()]
        existentes = {r["id"] for r in rows}
        if ids_end:
            placeholders = ','.join('?' for _ in ids_end)
            extras = conn.execute(
                f"SELECT id,tipo_pessoa,nome,cpf,cnpj,razao_social,telefone,email,rua,numero"
                f" FROM clientes WHERE id IN ({placeholders}) ORDER BY nome",
                ids_end,
            ).fetchall()
            rows = list(rows) + [r for r in extras if r["id"] not in existentes]
    else:
        rows = conn.execute(
            "SELECT id,tipo_pessoa,nome,cpf,cnpj,razao_social,telefone,email,rua,numero"
            " FROM clientes ORDER BY nome"
        ).fetchall()
    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT cliente_id, COUNT(*) FROM pedidos GROUP BY cliente_id"
    ).fetchall()}
    # Carrega todos os enderecos_cliente para exibir na listagem (rua, quadra, bairro)
    all_ids = [r["id"] for r in rows]
    enderecos_map = {}
    if all_ids:
        ph = ','.join('?' for _ in all_ids)
        for e in conn.execute(
            f"SELECT cliente_id,apelido,rua,quadra,numero,bairro FROM enderecos_cliente"
            f" WHERE cliente_id IN ({ph}) ORDER BY id",
            all_ids,
        ).fetchall():
            enderecos_map.setdefault(e["cliente_id"], []).append(dict(e))
    conn.close()
    # Pré-calcula texto de busca para cada cliente (usado no dropdown JS)
    rows_final = []
    for r in rows:
        d = dict(r)
        ends = enderecos_map.get(r["id"], [])
        ends_txt = ' '.join(
            ' '.join(filter(None, [e.get('rua',''), e.get('quadra',''), e.get('bairro',''), e.get('apelido','')]))
            for e in ends
        )
        d['busca_txt'] = ' '.join(filter(None, [
            r['nome'], r['cpf'] or '', r['cnpj'] or '',
            r['razao_social'] or '',
            r['email'] or '', r['rua'] or '', r['numero'] or '',
            ends_txt
        ]))
        # Endereço completo para mostrar no dropdown (principal + secundários)
        # Monta endereços completos para o dropdown (usa enderecos_cliente que tem quadra)
        end_parts = []
        if ends:
            # Usa enderecos_cliente (mais completo — tem quadra e bairro)
            for e in ends:
                p = e.get('rua','')
                if e.get('quadra'): p += ', Q.' + e['quadra']
                if e.get('numero'): p += ', nº ' + e['numero']
                if e.get('bairro'): p += ' — ' + e['bairro']
                lbl = ('[' + e['apelido'] + '] ') if e.get('apelido') else ''
                if p.strip(): end_parts.append(lbl + p)
        elif r['rua']:
            # Fallback: endereço do cadastro principal (sem quadra)
            p = r['rua']
            if r['numero']: p += ', nº ' + r['numero']
            end_parts.append(p)
        d['end_full'] = ' | '.join(end_parts)
        rows_final.append(d)
    return render_template("clientes.html", clientes=rows_final, busca=busca,
                           counts=counts, enderecos_map=enderecos_map)


@app.route("/clientes/exportar.csv")
def clientes_exportar_csv():
    conn = get_conn()
    rows = conn.execute(
        """SELECT nome, tipo_pessoa, cpf, cnpj, razao_social, telefone, email, rua, numero
           FROM clientes ORDER BY nome"""
    ).fetchall()
    conn.close()
    linhas = ["Nome,Tipo,CPF/CNPJ,Razão social,Telefone,E-mail,Rua,Número"]
    for r in rows:
        doc = r["cpf"] if r["tipo_pessoa"] == "pf" else r["cnpj"]
        campos = [
            r["nome"], r["tipo_pessoa"].upper(), doc or "", r["razao_social"] or "",
            r["telefone"] or "", r["email"] or "", r["rua"] or "", r["numero"] or "",
        ]
        linhas.append(",".join('"' + str(c or "").replace('"', '""') + '"' for c in campos))
    return Response(
        "\ufeff" + "\n".join(linhas),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=clientes-cacambas.csv"},
    )


@app.route("/clientes/novo", methods=["GET","POST"])
def cliente_novo():
    if request.method == "POST":
        tipo   = request.form.get("tipo_pessoa","pf")
        nome   = request.form.get("nome","").strip()
        tel    = request.form.get("telefone","").strip()
        email  = request.form.get("email","").strip()
        cpf    = request.form.get("cpf","").strip()
        cnpj   = request.form.get("cnpj","").strip()
        razao  = request.form.get("razao_social","").strip()
        cep    = _cep_norm(request.form.get("cep",""))
        rua    = request.form.get("rua","").strip()
        quadra = request.form.get("quadra","").strip()
        numero = request.form.get("numero","").strip()
        bairro = request.form.get("bairro","").strip()
        comp   = request.form.get("complemento","").strip()
        apel   = request.form.get("apelido","").strip()

        erros = []
        if not nome:    erros.append("Nome obrigatório.")
        if not tel:     erros.append("Telefone obrigatório.")
        if not rua:     erros.append("Rua do endereço obrigatória.")
        if cep and not _cep_ok(cep): erros.append("CEP inválido.")
        if tipo == "pf" and not _cpf_ok(cpf):  erros.append("CPF inválido (11 dígitos).")
        if tipo == "pj" and not _cnpj_ok(cnpj): erros.append("CNPJ inválido (14 dígitos).")
        for e in erros:
            flash(e, "erro")
        if erros:
            return render_template("cliente_novo.html")

        cpf_s  = _digits(cpf)  if tipo == "pf" else ""
        cnpj_s = _digits(cnpj) if tipo == "pj" else ""

        conn = get_conn()
        # Bloquear duplicatas de CPF/CNPJ
        if cpf_s:
            dup = conn.execute("SELECT id,nome FROM clientes WHERE cpf=? AND cpf!=''", (cpf_s,)).fetchone()
            if dup:
                conn.close()
                flash(f"CPF já cadastrado para o cliente: {dup['nome']}.", "erro")
                return render_template("cliente_novo.html")
        if cnpj_s:
            dup = conn.execute("SELECT id,nome FROM clientes WHERE cnpj=? AND cnpj!=''", (cnpj_s,)).fetchone()
            if dup:
                conn.close()
                flash(f"CNPJ já cadastrado para o cliente: {dup['nome']}.", "erro")
                return render_template("cliente_novo.html")
        conn.execute(
            """INSERT INTO clientes
               (tipo_pessoa,nome,cpf,cnpj,razao_social,telefone,email,
                endereco,cep,rua,numero,complemento)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tipo,nome,cpf_s,cnpj_s,razao,tel,email,"",cep,rua,numero,comp),
        )
        nid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO enderecos_cliente
               (cliente_id,cep,rua,quadra,numero,bairro,complemento,apelido)
               VALUES (?,?,?,?,?,?,?,?)""",
            (nid,cep,rua,quadra,numero,bairro,comp,apel or "Principal"),
        )
        conn.commit()
        conn.close()
        flash("Cliente cadastrado com sucesso.", "ok")
        return redirect(url_for("cliente_detalhe", cid=nid))
    return render_template("cliente_novo.html")


@app.route("/clientes/<int:cid>")
def cliente_detalhe(cid):
    conn = get_conn()
    c = conn.execute(
        "SELECT id,tipo_pessoa,nome,cpf,cnpj,razao_social,telefone,email,cep,rua,numero,complemento"
        " FROM clientes WHERE id=?", (cid,)
    ).fetchone()
    if not c:
        conn.close()
        flash("Cliente não encontrado.","erro")
        return redirect(url_for("clientes"))
    enderecos = conn.execute(
        "SELECT id,apelido,cep,rua,quadra,numero,bairro,complemento"
        " FROM enderecos_cliente WHERE cliente_id=? ORDER BY id", (cid,)
    ).fetchall()
    pedidos = conn.execute(
        """SELECT p.id,p.status,p.pago,p.capacidade_m3,p.endereco_obra,
                  p.obra_rua,p.obra_quadra,p.obra_numero,p.obra_bairro,
                  p.data_inicio,p.data_fim_prevista,p.data_fim_real,p.motorista_entrega,
                  p.motorista_retirada,p.observacoes,p.criado_em,
                  ca.codigo AS cacamba_codigo
           FROM pedidos p LEFT JOIN cacambas ca ON ca.id=p.cacamba_id
           WHERE p.cliente_id=? ORDER BY p.id DESC""", (cid,)
    ).fetchall()
    hoje = date.today().isoformat()
    # Resumo rápido do cliente
    ativos = [p for p in pedidos if p["status"] not in ("finalizado", "cancelado")]
    pendente_pagamento = [p for p in pedidos if not p["pago"] and p["status"] not in ("pendente", "cancelado")]
    conn.close()
    return render_template("cliente_detalhe.html",
        cliente=c, enderecos=enderecos, pedidos=pedidos,
        hoje=hoje,
        ativos=ativos,
        pendente_pagamento=pendente_pagamento)


@app.route("/clientes/<int:cid>/editar", methods=["GET","POST"])
def cliente_editar(cid):
    conn = get_conn()
    c = conn.execute(
        "SELECT id,tipo_pessoa,nome,cpf,cnpj,razao_social,telefone,email,cep,rua,numero,complemento"
        " FROM clientes WHERE id=?", (cid,)
    ).fetchone()
    if not c:
        conn.close()
        flash("Cliente não encontrado.","erro")
        return redirect(url_for("clientes"))
    if request.method == "POST":
        tipo   = request.form.get("tipo_pessoa","pf")
        nome   = request.form.get("nome","").strip()
        tel    = request.form.get("telefone","").strip()
        email  = request.form.get("email","").strip()
        cpf    = request.form.get("cpf","").strip()
        cnpj   = request.form.get("cnpj","").strip()
        razao  = request.form.get("razao_social","").strip()
        cep    = _cep_norm(request.form.get("cep",""))
        rua    = request.form.get("rua","").strip()
        numero = request.form.get("numero","").strip()
        comp   = request.form.get("complemento","").strip()

        erros = []
        if not nome: erros.append("Nome obrigatório.")
        if not tel:  erros.append("Telefone obrigatório.")
        if tipo=="pf" and not _cpf_ok(cpf):  erros.append("CPF inválido.")
        if tipo=="pj" and not _cnpj_ok(cnpj): erros.append("CNPJ inválido.")
        for e in erros: flash(e,"erro")
        if erros:
            conn.close()
            return render_template("cliente_editar.html", cliente=c)

        cpf_edit  = _digits(cpf)  if tipo=="pf" else ""
        cnpj_edit = _digits(cnpj) if tipo=="pj" else ""
        # Bloquear duplicatas (exceto o próprio cliente)
        if cpf_edit:
            dup = conn.execute("SELECT id,nome FROM clientes WHERE cpf=? AND cpf!='' AND id!=?", (cpf_edit, cid)).fetchone()
            if dup:
                conn.close()
                flash(f"CPF já cadastrado para o cliente: {dup['nome']}.", "erro")
                return render_template("cliente_editar.html", cliente=c)
        if cnpj_edit:
            dup = conn.execute("SELECT id,nome FROM clientes WHERE cnpj=? AND cnpj!='' AND id!=?", (cnpj_edit, cid)).fetchone()
            if dup:
                conn.close()
                flash(f"CNPJ já cadastrado para o cliente: {dup['nome']}.", "erro")
                return render_template("cliente_editar.html", cliente=c)
        conn.execute(
            """UPDATE clientes SET tipo_pessoa=?,nome=?,cpf=?,cnpj=?,razao_social=?,
               telefone=?,email=?,cep=?,rua=?,numero=?,complemento=? WHERE id=?""",
            (tipo,nome,cpf_edit,cnpj_edit,razao,tel,email,cep,rua,numero,comp,cid),
        )
        conn.commit()
        conn.close()
        flash("Dados atualizados.","ok")
        return redirect(url_for("cliente_detalhe", cid=cid))
    conn.close()
    return render_template("cliente_editar.html", cliente=c)


@app.route("/clientes/<int:cid>/excluir", methods=["POST"])
def cliente_excluir(cid):
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM pedidos WHERE cliente_id=?",(cid,)).fetchone()[0]
    if n:
        conn.close()
        flash("Não é possível excluir cliente com locações registradas.","erro")
        return redirect(url_for("cliente_detalhe", cid=cid))
    conn.execute("DELETE FROM enderecos_cliente WHERE cliente_id=?",(cid,))
    conn.execute("DELETE FROM clientes WHERE id=?",(cid,))
    conn.commit()
    conn.close()
    flash("Cliente excluído.","ok")
    return redirect(url_for("clientes"))


@app.route("/clientes/<int:cid>/endereco/novo", methods=["POST"])
def cliente_endereco_novo(cid):
    cep    = _cep_norm(request.form.get("cep",""))
    rua    = request.form.get("rua","").strip()
    quadra = request.form.get("quadra","").strip()
    numero = request.form.get("numero","").strip()
    bairro = request.form.get("bairro","").strip()
    comp   = request.form.get("complemento","").strip()
    apel   = request.form.get("apelido","").strip()
    if not rua:
        flash("Rua obrigatória.","erro")
        return redirect(url_for("cliente_detalhe", cid=cid))
    conn = get_conn()
    conn.execute(
        "INSERT INTO enderecos_cliente (cliente_id,cep,rua,quadra,numero,bairro,complemento,apelido)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (cid,cep,rua,quadra,numero,bairro,comp,apel),
    )
    conn.commit()
    conn.close()
    flash("Endereço adicionado.","ok")
    return redirect(url_for("cliente_detalhe", cid=cid))


@app.route("/clientes/<int:cid>/endereco/<int:eid>/excluir", methods=["POST"])
def cliente_endereco_excluir(cid, eid):
    conn = get_conn()
    conn.execute("DELETE FROM enderecos_cliente WHERE id=? AND cliente_id=?",(eid,cid))
    conn.commit()
    conn.close()
    flash("Endereço removido.","ok")
    return redirect(url_for("cliente_detalhe", cid=cid))


# --- CAÇAMBAS ---

@app.route("/cacambas")
def cacambas():
    f = request.args.get("f","")
    conn = get_conn()
    q = "SELECT id,codigo,capacidade_m3,status FROM cacambas"
    params = ()
    if f in ("disponivel","em_uso","manutencao"):
        q += " WHERE status=?"
        params = (f,)
    q += " ORDER BY CASE WHEN codigo GLOB '[0-9]*' THEN CAST(codigo AS INTEGER) ELSE 999999 END,codigo"
    rows = conn.execute(q, params).fetchall()
    tots = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM cacambas GROUP BY status"
    ).fetchall()}
    total = conn.execute("SELECT COUNT(*) FROM cacambas").fetchone()[0]
    conn.close()
    return render_template("cacambas.html", cacambas=rows, filtro=f,
        n_disp=tots.get("disponivel",0), n_uso=tots.get("em_uso",0),
        n_manut=tots.get("manutencao",0), total=total)


@app.route("/cacambas/nova", methods=["GET","POST"])
def cacamba_nova():
    if request.method == "POST":
        cod = request.form.get("codigo","").strip()
        cap = request.form.get("capacidade_m3","").strip()
        if not cod:
            flash("Informe o número da caçamba.","erro")
            return render_template("cacamba_nova.html")
        if not _cap_ok(cap):
            flash("Capacidade inválida (3 ou 4 m³).","erro")
            return render_template("cacamba_nova.html")
        try:
            conn = get_conn()
            conn.execute("INSERT INTO cacambas (codigo,capacidade_m3,status) VALUES (?,?,'disponivel')",(cod,int(cap)))
            conn.commit()
            conn.close()
            flash(f"Caçamba nº {cod} cadastrada.","ok")
            return redirect(url_for("cacambas"))
        except sqlite3.IntegrityError:
            flash("Já existe uma caçamba com esse código.","erro")
    return render_template("cacamba_nova.html")


@app.route("/cacambas/<int:cid>/manutencao", methods=["POST"])
def cacamba_manutencao(cid):
    conn = get_conn()
    conn.execute("UPDATE cacambas SET status='manutencao' WHERE id=? AND status='disponivel'",(cid,))
    conn.commit()
    conn.close()
    flash("Caçamba marcada para manutenção.","ok")
    return redirect(url_for("cacambas"))


@app.route("/cacambas/<int:cid>/disponivel", methods=["POST"])
def cacamba_disponivel(cid):
    conn = get_conn()
    conn.execute("UPDATE cacambas SET status='disponivel' WHERE id=?",(cid,))
    conn.commit()
    conn.close()
    flash("Caçamba liberada.","ok")
    return redirect(url_for("cacambas"))


# --- PEDIDOS ---

_PEDIDO_SELECT = """
    SELECT p.id,p.status,p.pago,p.capacidade_m3,p.endereco_obra,
           p.obra_cep,p.obra_rua,p.obra_quadra,p.obra_numero,p.obra_bairro,
           p.data_inicio,p.data_fim_prevista,p.data_fim_real,p.criado_em,
           p.motorista_entrega,p.motorista_retirada,p.latitude,p.longitude,
           p.valor_total,
           c.id AS cliente_id, c.nome AS cliente_nome, c.telefone,
           ca.codigo AS cacamba_codigo
    FROM pedidos p
    JOIN clientes c ON c.id=p.cliente_id
    LEFT JOIN cacambas ca ON ca.id=p.cacamba_id
"""

@app.route("/pedidos")
def pedidos():
    s   = request.args.get("s","")
    pg  = request.args.get("p","")
    q   = request.args.get("q","").strip()
    data_ini = request.args.get("ini","").strip()
    data_fim = request.args.get("fim","").strip()
    conn = get_conn()
    where, params = [], []
    if s in ("pendente", "confirmado", "no_endereco", "finalizado", "cancelado"):
        where.append("p.status=?")
        params.append(s)
    if pg in ("0","1"):
        where.append("p.pago=?")
        params.append(int(pg))
    if data_ini:
        where.append("COALESCE(NULLIF(p.data_inicio,''), p.criado_em) >= ?")
        params.append(data_ini)
    if data_fim:
        where.append("COALESCE(NULLIF(p.data_inicio,''), p.criado_em) <= ?")
        params.append(data_fim)
    if q:
        import unicodedata as _ud2
        def _norm2(s): return _ud2.normalize("NFD", str(s)).encode("ascii","ignore").decode("ascii").lower()
        pedido_id = int(q) if q.isdigit() else -1
        tokens = [_norm2(t) for t in q.split() if t]

        # Para nome: todos os tokens devem aparecer no nome
        nome_cond   = " AND ".join(["NORM(c.nome) LIKE ?" for _ in tokens])
        nome_params = [f"%{t}%" for t in tokens]

        # Para telefone: todos os tokens
        tel_cond    = " AND ".join(["c.telefone LIKE ?" for _ in tokens])
        tel_params  = [f"%{t}%" for t in tokens]

        # Para endereço: concatena rua+quadra+numero+bairro e cada token deve aparecer
        end_concat  = ("NORM(COALESCE(p.obra_rua,'') || ' ' || COALESCE(p.obra_quadra,'') "
                       "|| ' ' || COALESCE(p.obra_numero,'') || ' ' || COALESCE(p.obra_bairro,''))")
        end_cond    = " AND ".join([f"{end_concat} LIKE ?" for _ in tokens])
        end_params  = [f"%{t}%" for t in tokens]

        where.append(f"(p.id = ? OR ({nome_cond}) OR ({tel_cond}) OR ({end_cond}))")
        params.extend([pedido_id] + nome_params + tel_params + end_params)
    base = _PEDIDO_SELECT
    if where:
        base += " WHERE " + " AND ".join(where)
    # Exportar CSV
    exportar = request.args.get("export","").strip() == "csv"
    if exportar:
        export_rows = conn.execute(base + " ORDER BY p.id DESC", tuple(params)).fetchall()
        conn.close()
        linhas = ["pedido,cliente,telefone,status,pago,valor,data_inicio,data_fim_prevista,data_fim_real,cacamba,endereco,bairro"]
        for ep in export_rows:
            campos = [
                ep["id"], ep["cliente_nome"], ep["telefone"], ep["status"],
                "sim" if ep["pago"] else "nao",
                f"{ep['valor_total'] or 0:.2f}",
                ep["data_inicio"], ep["data_fim_prevista"], ep["data_fim_real"],
                ep["cacamba_codigo"] or "",
                ep["obra_rua"] or ep["endereco_obra"] or "",
                ep["obra_bairro"] or "",
            ]
            linhas.append(",".join('"' + str(c or "").replace('"', '""') + '"' for c in campos))
        return Response("\n".join(linhas), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=pedidos-cacambas.csv"})
    page = request.args.get("page", 1, type=int)
    rows, total, page, pages, per_page = paginate_query(
        conn,
        "SELECT COUNT(*) FROM pedidos p JOIN clientes c ON c.id=p.cliente_id" + (" WHERE " + " AND ".join(where) if where else ""),
        base + " ORDER BY p.id DESC",
        tuple(params),
        page,
        25,
    )
    conn.close()
    pagination_args = {k: v for k, v in (("s", s), ("p", pg), ("q", q), ("ini", data_ini), ("fim", data_fim)) if v}
    return render_template(
        "pedidos.html",
        pedidos=rows,
        filtro_status=s,
        filtro_pago=pg,
        busca=q,
        data_ini=data_ini,
        data_fim=data_fim,
        hoje=date.today().isoformat(),
        total=total,
        page=page,
        pages=pages,
        per_page=per_page,
        motoristas=_get_motoristas(),
        pagination_endpoint="pedidos",
        pagination_args=pagination_args,
    )


def _fetch_pedido(conn, pid):
    return conn.execute(_PEDIDO_SELECT + " WHERE p.id=?", (pid,)).fetchone()


def _historico_pedido(conn, pid):
    return conn.execute(
        """SELECT acao, detalhes, created_at FROM historico_pedidos
           WHERE pedido_id=? ORDER BY id DESC""",
        (pid,),
    ).fetchall()


@app.route("/pedidos/<int:pid>")
def pedido_detalhe(pid):
    conn = get_conn()
    p = _fetch_pedido(conn, pid)
    if not p:
        conn.close()
        flash("Pedido não encontrado.", "erro")
        return redirect(url_for("pedidos"))
    historico = _historico_pedido(conn, pid)
    conn.close()
    hoje = date.today().isoformat()
    vencida = (
        p["data_fim_prevista"]
        and p["data_fim_prevista"] < hoje
        and p["status"] not in ("finalizado", "cancelado")
    )
    return render_template(
        "pedido_detalhe.html",
        p=p,
        historico=historico,
        hoje=hoje,
        vencida=vencida,
        motoristas=_get_motoristas(),
        dias_locacao=_dias_locacao(),
    )


@app.route("/pedidos/novo", methods=["GET","POST"])
def pedido_novo():
    pre = request.args.get("cliente_id","")
    conn = get_conn()
    clientes_list = conn.execute("SELECT id FROM clientes").fetchall()

    if request.method == "POST":
        cid   = request.form.get("cliente_id","").strip()
        cap   = request.form.get("capacidade","").strip()
        end_id = request.form.get("endereco_id","").strip()
        obs   = request.form.get("observacoes","").strip()

        if not cid or not cap:
            flash("Cliente e tamanho são obrigatórios.","erro")
            conn.close()
            return render_template("pedido_novo.html", clientes=clientes_list, pre_cliente_id=pre)
        if not cid.isdigit():
            flash("Cliente inválido.","erro")
            conn.close()
            return render_template("pedido_novo.html", clientes=clientes_list, pre_cliente_id=pre)
        if not _cap_ok(cap):
            flash("Tamanho inválido.","erro")
            conn.close()
            return render_template("pedido_novo.html", clientes=clientes_list, pre_cliente_id=pre)
        if not end_id:
            flash("Selecione ou cadastre o endereço da obra.","erro")
            conn.close()
            return render_template("pedido_novo.html", clientes=clientes_list, pre_cliente_id=pre)
        if end_id != "novo" and not end_id.isdigit():
            flash("Endereço inválido.","erro")
            conn.close()
            return render_template("pedido_novo.html", clientes=clientes_list, pre_cliente_id=pre)
        # Validate cliente exists
        cliente_obj = conn.execute("SELECT id FROM clientes WHERE id=?", (int(cid),)).fetchone()
        if not cliente_obj:
            flash("Cliente não encontrado.","erro")
            conn.close()
            return render_template("pedido_novo.html", clientes=clientes_list, pre_cliente_id=pre)

        if end_id and end_id != "novo":
            end = conn.execute(
                "SELECT * FROM enderecos_cliente WHERE id=? AND cliente_id=?",
                (int(end_id), int(cid))
            ).fetchone()
            if not end:
                flash("Endereço inválido.","erro")
                conn.close()
                return render_template("pedido_novo.html", clientes=clientes_list, pre_cliente_id=pre)
            obra_cep    = end["cep"]
            obra_rua    = end["rua"]
            obra_quadra = end["quadra"] or ""
            obra_numero = end["numero"]
            obra_bairro = end["bairro"] or ""
        elif end_id == "novo":
            obra_cep    = _cep_norm(request.form.get("obra_cep",""))
            obra_rua    = request.form.get("obra_rua","").strip()
            obra_quadra = request.form.get("obra_quadra","").strip()
            obra_numero = request.form.get("obra_numero","").strip()
            obra_bairro = request.form.get("obra_bairro","").strip()
            end_comp    = request.form.get("endereco_complemento","").strip()
            end_apel    = request.form.get("endereco_apelido","").strip()
            if not obra_rua or not obra_numero:
                flash("Preencha rua e número do novo endereço.","erro")
                conn.close()
                return render_template("pedido_novo.html", clientes=clientes_list, pre_cliente_id=pre)
            conn.execute(
                "INSERT INTO enderecos_cliente (cliente_id,cep,rua,quadra,numero,bairro,complemento,apelido)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (int(cid),obra_cep,obra_rua,obra_quadra,obra_numero,obra_bairro,end_comp,end_apel or obra_rua),
            )
        else:
            flash("Selecione ou cadastre o endereço da obra.","erro")
            conn.close()
            return render_template("pedido_novo.html", clientes=clientes_list, pre_cliente_id=pre)

        linha = _fmt_endereco(obra_cep, obra_rua, obra_quadra, obra_numero, obra_bairro)
        agora = datetime.now().strftime("%Y-%m-%d %H:%M")
        dias = _dias_locacao()
        data_fim = request.form.get("data_fim_prevista", "").strip()
        if not data_fim:
            data_fim = (date.today() + timedelta(days=dias)).isoformat()
        valor = _valor_locacao(cap)
        cur = conn.execute(
            """INSERT INTO pedidos
               (cliente_id,cacamba_id,capacidade_m3,valor_total,endereco_obra,
                obra_cep,obra_rua,obra_quadra,obra_numero,obra_bairro,
                data_inicio,data_fim_prevista,status,pago,criado_em,observacoes)
               VALUES (?,NULL,?,?,?,?,?,?,?,?,?,'pendente',0,?,?)""",
            (int(cid), int(cap), valor, linha, obra_cep, obra_rua, obra_quadra,
             obra_numero, obra_bairro, "", data_fim, agora, obs),
        )
        novo_id = cur.lastrowid
        _registrar_historico(conn, novo_id, "criado", f"Capacidade {cap} m³ — retirada prevista {data_fim}")
        conn.commit()
        conn.close()
        _cache_invalidate()
        flash("Solicitação registrada.","ok")
        return redirect(url_for("cliente_detalhe", cid=int(cid)) if pre else url_for("pedidos"))

    conn.close()
    return render_template(
        "pedido_novo.html",
        clientes=clientes_list,
        pre_cliente_id=pre,
    )


@app.route("/pedidos/<int:pid>/confirmar", methods=["POST"])
def pedido_confirmar(pid):
    conn = get_conn()
    p = conn.execute("SELECT id,status FROM pedidos WHERE id=?",(pid,)).fetchone()
    if not p or p["status"] != "pendente":
        conn.close()
        flash("Pedido não encontrado ou já processado.","erro")
        return redirect(url_for("pedidos"))
    conn.execute("UPDATE pedidos SET status='confirmado' WHERE id=?", (pid,))
    _registrar_historico(conn, pid, "confirmado")
    conn.commit()
    conn.close()
    _cache_invalidate()
    flash("Pedido confirmado.","ok")
    return redirect(request.referrer or url_for("pedidos"))


@app.route("/pedidos/<int:pid>/entregar", methods=["POST"])
def pedido_entregar(pid):
    cacamba_id = request.form.get("cacamba_id","").strip()
    motorista  = request.form.get("motorista_entrega","").strip()
    conn = get_conn()
    ped = conn.execute(
        "SELECT id,status,cacamba_id,capacidade_m3,endereco_obra,"
        "obra_cep,obra_rua,obra_quadra,obra_numero,obra_bairro FROM pedidos WHERE id=?",(pid,)
    ).fetchone()

    if not ped or ped["status"] != "confirmado":
        conn.close()
        flash("Só é possível entregar pedidos confirmados.","erro")
        return redirect(url_for("pedidos"))
    if not cacamba_id or not str(cacamba_id).isdigit():
        conn.close()
        flash("Selecione a caçamba.","erro")
        return redirect(url_for("pedidos"))
    motoristas = _get_motoristas()
    if motorista not in motoristas:
        conn.close()
        flash("Selecione o motorista.","erro")
        return redirect(url_for("pedidos"))

    cab = conn.execute("SELECT id,capacidade_m3,status FROM cacambas WHERE id=?",(int(cacamba_id),)).fetchone()
    if not cab:
        conn.close()
        flash("Caçamba não encontrada.","erro")
        return redirect(url_for("pedidos"))
    if cab["capacidade_m3"] != ped["capacidade_m3"]:
        conn.close()
        flash("Capacidade da caçamba não corresponde ao pedido.","erro")
        return redirect(url_for("pedidos"))
    if cab["status"] != "disponivel":
        conn.close()
        flash("Caçamba não está disponível.","erro")
        return redirect(url_for("pedidos"))

    lat, lon = _geocode_ped(ped)
    inicio = date.today()
    fim    = inicio + timedelta(days=_dias_locacao() - 1)

    try:
        if ped["cacamba_id"]:
            conn.execute("UPDATE cacambas SET status='disponivel' WHERE id=?",(ped["cacamba_id"],))
        conn.execute("UPDATE cacambas SET status='em_uso' WHERE id=?",(int(cacamba_id),))
        conn.execute(
            """UPDATE pedidos SET status='no_endereco',cacamba_id=?,motorista_entrega=?,
               data_inicio=?,data_fim_prevista=?,latitude=?,longitude=? WHERE id=?""",
            (int(cacamba_id), motorista, inicio.isoformat(), fim.isoformat(), lat, lon, pid),
        )
        _registrar_historico(conn, pid, "entregue", f"Caçamba {cacamba_id} — {motorista}")
        conn.commit()
        _cache_invalidate()
        flash(f"Entrega registrada por {motorista}. Retirada prevista: {fim.strftime('%d/%m/%Y')}.","ok")
    except Exception as e:
        conn.rollback()
        flash(f"Erro ao registrar entrega: {e}","erro")
    finally:
        conn.close()
    return redirect(request.referrer or url_for("pedidos"))


@app.route("/pedidos/<int:pid>/finalizar", methods=["POST"])
def pedido_finalizar(pid):
    motorista_retirada = request.form.get("motorista_retirada","").strip()
    data_retirada = request.form.get("data_retirada","").strip()
    conn = get_conn()
    p = conn.execute("SELECT id,status,cacamba_id FROM pedidos WHERE id=?",(pid,)).fetchone()
    if not p or p["status"] != "no_endereco":
        conn.close()
        flash("Só é possível finalizar pedidos com caçamba no endereço.","erro")
        return redirect(url_for("pedidos"))
    if not motorista_retirada or not data_retirada:
        conn.close()
        flash("Selecione o motorista e a data de retirada.","erro")
        return redirect(url_for("pedidos"))
    motoristas = _get_motoristas()
    if motorista_retirada not in motoristas:
        conn.close()
        flash("Selecione um motorista válido.","erro")
        return redirect(url_for("pedidos"))
    try:
        data_ret = datetime.strptime(data_retirada, "%Y-%m-%d").date()
        if data_ret > date.today():
            conn.close()
            flash("Data de retirada não pode ser futura.","erro")
            return redirect(url_for("pedidos"))
    except:
        conn.close()
        flash("Data inválida.","erro")
        return redirect(url_for("pedidos"))
    try:
        if p["cacamba_id"]:
            conn.execute("UPDATE cacambas SET status='disponivel' WHERE id=?",(p["cacamba_id"],))
        conn.execute(
            "UPDATE pedidos SET status='finalizado', motorista_retirada=?, data_fim_real=? WHERE id=?",
            (motorista_retirada, data_retirada, pid),
        )
        _registrar_historico(conn, pid, "finalizado", f"Retirada {data_retirada} — {motorista_retirada}")
        conn.commit()
        _cache_invalidate()
        flash("Retirada registrada. Caçamba liberada.","ok")
    except Exception as e:
        conn.rollback()
        flash(f"Erro ao finalizar pedido: {e}","erro")
    finally:
        conn.close()
    return redirect(request.referrer or url_for("pedidos"))


@app.route("/pedidos/<int:pid>/cancelar", methods=["POST"])
def pedido_cancelar(pid):
    conn = get_conn()
    p = conn.execute("SELECT id,status,cacamba_id FROM pedidos WHERE id=?",(pid,)).fetchone()
    if not p or p["status"] not in ("pendente", "confirmado", "no_endereco"):
        conn.close()
        flash("Não é possível cancelar este pedido.","erro")
        return redirect(url_for("pedidos"))
    if p["cacamba_id"]:
        conn.execute("UPDATE cacambas SET status='disponivel' WHERE id=?", (p["cacamba_id"],))
    fim_real = date.today().isoformat()
    conn.execute("UPDATE pedidos SET status='cancelado', data_fim_real=? WHERE id=?", (fim_real, pid))
    _registrar_historico(conn, pid, "cancelado")
    conn.commit()
    conn.close()
    _cache_invalidate()
    flash("Pedido cancelado. Caçamba liberada.","ok")
    return redirect(request.referrer or url_for("pedidos"))


@app.route("/pedidos/<int:pid>/pagar", methods=["POST"])
def pedido_pagar(pid):
    conn = get_conn()
    p = conn.execute("SELECT id, status FROM pedidos WHERE id=?", (pid,)).fetchone()
    if not p or p["status"] in ("pendente", "cancelado"):
        conn.close()
        flash("Não é possível registrar pagamento deste pedido.", "erro")
        return redirect(request.referrer or url_for("pedidos"))
    conn.execute("UPDATE pedidos SET pago=1 WHERE id=?", (pid,))
    _registrar_historico(conn, pid, "pago")
    conn.commit()
    conn.close()
    _cache_invalidate()
    flash("Pagamento registrado.","ok")
    origem = request.form.get("origem","")
    if origem.startswith("cliente_"):
        try: return redirect(url_for("cliente_detalhe", cid=int(origem.split("_")[1])))
        except: pass
    return redirect(request.referrer or url_for("pedidos"))


@app.route("/pedidos/<int:pid>/pagamento/desfazer", methods=["POST"])
def pedido_desfazer_pagamento(pid):
    conn = get_conn()
    conn.execute("UPDATE pedidos SET pago=0 WHERE id=?", (pid,))
    _registrar_historico(conn, pid, "pagamento_desfeito")
    conn.commit()
    conn.close()
    _cache_invalidate()
    flash("Pagamento marcado como pendente.", "ok")
    return redirect(request.referrer or url_for("pedido_detalhe", pid=pid))


@app.route("/pedidos/<int:pid>/observacao", methods=["POST"])
def pedido_observacao(pid):
    obs = request.form.get("observacoes","").strip()
    conn = get_conn()
    conn.execute("UPDATE pedidos SET observacoes=? WHERE id=?", (obs, pid))
    _registrar_historico(conn, pid, "observacao", obs[:200])
    conn.commit()
    conn.close()
    _cache_invalidate()
    flash("Observação salva.", "ok")
    return redirect(url_for("pedido_detalhe", pid=pid))


# --- OPERAÇÕES (entregas + retiradas do dia) ---

@app.route("/operacoes")
def operacoes():
    conn = get_conn()
    hoje = date.today()
    hoje_iso = hoje.isoformat()
    amanha_iso = (hoje + timedelta(days=1)).isoformat()
    filtro = request.args.get("f", "")

    filtro_sql = {
        "entregar": ("p.status='confirmado'", []),
        "hoje": ("p.status='no_endereco' AND p.data_fim_prevista=?", [hoje_iso]),
        "amanha": ("p.status='no_endereco' AND p.data_fim_prevista=?", [amanha_iso]),
        "atrasadas": ("p.status='no_endereco' AND p.data_fim_prevista<?", [hoje_iso]),
    }
    if filtro in filtro_sql:
        cond, extra = filtro_sql[filtro]
        if filtro == "entregar":
            entregas = conn.execute(_PEDIDO_SELECT + f" WHERE {cond} ORDER BY p.id", extra).fetchall()
            no_end_base = []
        else:
            entregas = []
            no_end_base = conn.execute(
                _PEDIDO_SELECT + f" WHERE {cond} ORDER BY p.data_fim_prevista", extra
            ).fetchall()
    else:
        entregas = conn.execute(
            _PEDIDO_SELECT + " WHERE p.status='confirmado' ORDER BY p.id"
        ).fetchall()
        no_end_base = conn.execute(
            _PEDIDO_SELECT + " WHERE p.status='no_endereco' ORDER BY p.data_fim_prevista"
        ).fetchall()

    # Calcula dias no local para cada pedido
    def _dias_no_local(p):
        try:
            inicio = datetime.strptime(p["data_inicio"], "%Y-%m-%d").date()
            return (hoje - inicio).days
        except:
            return 0

    no_end = []
    for p in no_end_base:
        d = dict(p)
        d["dias_no_local"] = _dias_no_local(p)
        no_end.append(d)

    stats_no_end = conn.execute(
        _PEDIDO_SELECT + " WHERE p.status='no_endereco' ORDER BY p.data_fim_prevista"
    ).fetchall()
    vencidas = [
        dict(p)
        for p in stats_no_end
        if p["data_fim_prevista"] and p["data_fim_prevista"] < hoje_iso
    ]
    retiradas_hoje = [dict(p) for p in stats_no_end if p["data_fim_prevista"] == hoje_iso]
    retiradas_ama = [dict(p) for p in stats_no_end if p["data_fim_prevista"] == amanha_iso]
    total_no_end_stats = len(stats_no_end)

    if filtro in filtro_sql:
        entregas_view = entregas
        no_end_view = no_end
    else:
        entregas_view = entregas
        no_end_view = no_end

    conn.close()
    return render_template("operacoes.html",
        entregas=entregas_view, no_end=no_end_view,
        vencidas=vencidas, retiradas_hoje=retiradas_hoje, retiradas_amanha=retiradas_ama,
        total_no_end=total_no_end_stats, total_entregas=len(entregas),
        filtro=filtro,
        motoristas=_get_motoristas(),
        hoje=hoje_iso, amanha=amanha_iso)


# --- FINANCEIRO ---

@app.route("/financeiro")
def financeiro():
    f = request.args.get("f","")
    q = request.args.get("q","").strip()
    data_ini = request.args.get("ini","").strip()
    data_fim = request.args.get("fim","").strip()
    exportar = request.args.get("export","").strip() == "csv"
    conn = get_conn()

    where, params = [], []
    where.append("p.status != 'pendente'")
    if f == "pago":
        where.append("p.pago=1")
    elif f == "pendente":
        where.append("p.pago=0")
    elif f == "vencido":
        where.append("p.pago=0 AND p.status='no_endereco' AND p.data_fim_prevista < ?")
        params.append(date.today().isoformat())
    elif f == "mes":
        inicio_mes = date.today().replace(day=1).isoformat()
        where.append("COALESCE(NULLIF(p.data_fim_real,''), NULLIF(p.data_inicio,''), p.criado_em) >= ?")
        params.append(inicio_mes)
    if data_ini:
        where.append("COALESCE(NULLIF(p.data_fim_real,''), NULLIF(p.data_inicio,''), p.criado_em) >= ?")
        params.append(data_ini)
    if data_fim:
        where.append("COALESCE(NULLIF(p.data_fim_real,''), NULLIF(p.data_inicio,''), p.criado_em) <= ?")
        params.append(data_fim)
    if q:
        tokens = [_digits(t) or t for t in q.split() if t]
        if tokens:
            search_parts = []
            for t in tokens:
                like = f"%{t}%"
                search_parts.append(
                    "(NORM(c.nome) LIKE NORM(?) OR c.telefone LIKE ? OR CAST(p.id AS TEXT)=? "
                    "OR NORM(COALESCE(p.obra_rua,'') || ' ' || COALESCE(p.obra_bairro,'') || ' ' || COALESCE(p.endereco_obra,'')) LIKE NORM(?))"
                )
                params.extend([like, like, t, like])
            where.append("(" + " AND ".join(search_parts) + ")")

    where_sql = " WHERE " + " AND ".join(where)
    page = request.args.get("page", 1, type=int)
    rows, total_rows, page, pages, per_page = paginate_query(
        conn,
        "SELECT COUNT(*) FROM pedidos p JOIN clientes c ON c.id=p.cliente_id" + where_sql,
        _PEDIDO_SELECT + where_sql + " ORDER BY p.pago ASC, p.id DESC",
        tuple(params),
        page,
        30,
    )

    resumo = conn.execute(
        """SELECT
             COUNT(*) AS total,
             SUM(CASE WHEN pago=1 THEN 1 ELSE 0 END) AS pagos,
             SUM(CASE WHEN pago=0 AND status!='pendente' THEN 1 ELSE 0 END) AS pendentes,
             COALESCE(SUM(valor_total),0) AS valor_total,
             COALESCE(SUM(CASE WHEN pago=1 THEN valor_total ELSE 0 END),0) AS valor_pago,
             COALESCE(SUM(CASE WHEN pago=0 AND status!='pendente' THEN valor_total ELSE 0 END),0) AS valor_pendente,
             COALESCE(SUM(CASE WHEN pago=0 AND status='no_endereco' AND data_fim_prevista < ? THEN valor_total ELSE 0 END),0) AS valor_vencido,
             SUM(CASE WHEN pago=0 AND status='no_endereco' AND data_fim_prevista < ? THEN 1 ELSE 0 END) AS vencidos
           FROM pedidos WHERE status != 'pendente'"""
        , (date.today().isoformat(), date.today().isoformat())
    ).fetchone()
    por_status = conn.execute(
        """SELECT status, COUNT(*) AS qtd, COALESCE(SUM(valor_total),0) AS valor
           FROM pedidos
           WHERE status != 'pendente'
           GROUP BY status
           ORDER BY valor DESC"""
    ).fetchall()
    por_mes = conn.execute(
        """SELECT substr(COALESCE(NULLIF(data_fim_real,''), NULLIF(data_inicio,''), criado_em), 1, 7) AS mes,
                  COUNT(*) AS qtd,
                  COALESCE(SUM(valor_total),0) AS valor
           FROM pedidos
           WHERE status != 'pendente'
           GROUP BY mes
           ORDER BY mes DESC
           LIMIT 6"""
    ).fetchall()
    ticket_medio = (resumo["valor_total"] or 0) / (resumo["total"] or 1)

    if exportar:
        export_rows = conn.execute(
            _PEDIDO_SELECT + where_sql + " ORDER BY p.pago ASC, p.id DESC", tuple(params)
        ).fetchall()
        linhas = ["pedido,cliente,telefone,status,pago,valor,data_inicio,data_fim_prevista,data_fim_real,endereco"]
        for p in export_rows:
            campos = [
                p["id"], p["cliente_nome"], p["telefone"], p["status"],
                "sim" if p["pago"] else "nao", f"{p['valor_total'] or 0:.2f}",
                p["data_inicio"], p["data_fim_prevista"], p["data_fim_real"],
                p["obra_rua"] or p["endereco_obra"] or "",
            ]
            linhas.append(",".join('"' + str(c or "").replace('"', '""') + '"' for c in campos))
        conn.close()
        return Response("\n".join(linhas), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=financeiro-cacambas.csv"})

    conn.close()
    pagination_args = {k: v for k, v in (("f", f), ("q", q), ("ini", data_ini), ("fim", data_fim)) if v}
    return render_template(
        "financeiro.html",
        pedidos=rows,
        filtro=f,
        busca=q,
        totais=resumo,
        por_status=por_status,
        por_mes=por_mes,
        ticket_medio=ticket_medio,
        data_ini=data_ini,
        data_fim=data_fim,
        hoje=date.today().isoformat(),
        page=page,
        pages=pages,
        per_page=per_page,
        total_rows=total_rows,
        pagination_endpoint="financeiro",
        pagination_args=pagination_args,
    )


# --- MAPA ---

@app.route("/mapa")
def mapa():
    conn = get_conn()
    rows = conn.execute(
        """SELECT p.id,p.status,p.endereco_obra,p.data_fim_prevista,
                  p.latitude,p.longitude,ca.codigo AS cacamba_codigo,
                  c.nome AS cliente_nome
           FROM pedidos p JOIN clientes c ON c.id=p.cliente_id
           LEFT JOIN cacambas ca ON ca.id=p.cacamba_id
           WHERE p.status='no_endereco' ORDER BY p.id"""
    ).fetchall()
    conn.close()
    hoje = date.today()
    marcadores = []
    for r in rows:
        lat, lon = r["latitude"], r["longitude"]
        if lat is None or lon is None:
            lat, lon = _geocode_ped(r)
        if lat is None or lon is None:
            continue
        try: venc = datetime.strptime(r["data_fim_prevista"],"%Y-%m-%d").date()
        except: venc = hoje
        marcadores.append({
            "id": r["id"], "lat": lat, "lon": lon,
            "cacamba": r["cacamba_codigo"] or "?",
            "cliente": r["cliente_nome"],
            "endereco": r["endereco_obra"],
            "status": r["status"],
            "data_fim": r["data_fim_prevista"],
            "vencido": venc < hoje,
        })
    return render_template("mapa.html", marcadores=marcadores,
        bauru_lat=BAURU_LAT, bauru_lon=BAURU_LON,
        hoje_iso=hoje.isoformat())


# --- CONFIGURAÇÕES ---

@app.route("/configuracoes", methods=["GET","POST"])
def configuracoes():
    conn = get_conn()
    config = {r["chave"]: r["valor"] for r in conn.execute("SELECT chave,valor FROM config").fetchall()}
    conn.close()

    if request.method == "POST":
        dias = request.form.get("dias_locacao","7").strip()
        empresa = request.form.get("empresa_nome","").strip()
        fone_empresa = request.form.get("empresa_fone","").strip()
        try:
            dias_n = max(1, min(int(dias), 30))
        except ValueError:
            dias_n = 7
        v3 = request.form.get("valor_locacao_3", "").strip().replace(",", ".")
        v4 = request.form.get("valor_locacao_4", "").strip().replace(",", ".")
        conn = get_conn()
        pairs = [
            ("dias_locacao", str(dias_n)),
            ("empresa_nome", empresa),
            ("empresa_fone", fone_empresa),
        ]
        try:
            pairs.append(("valor_locacao_3m3", str(max(0, float(v3)))))
        except ValueError:
            pass
        try:
            pairs.append(("valor_locacao_4m3", str(max(0, float(v4)))))
        except ValueError:
            pass
        for k, v in pairs:
            conn.execute("INSERT OR REPLACE INTO config (chave,valor) VALUES (?,?)", (k, v))
        conn.commit()
        conn.close()
        cache_clear("config", "dashboard_stats", "sidebar_stats")
        flash("Configurações salvas.", "ok")
        return redirect(url_for("configuracoes"))

    conn = get_conn()
    motoristas_db = conn.execute("SELECT id, nome FROM motoristas ORDER BY nome").fetchall()
    caps = conn.execute(
        "SELECT capacidade_m3, COUNT(*) AS n FROM cacambas GROUP BY capacidade_m3 ORDER BY capacidade_m3"
    ).fetchall()
    conn.close()
    return render_template("configuracoes.html",
        config=config, motoristas=motoristas_db,
        capacidades=caps, motoristas_padrao=MOTORISTAS)


@app.route("/configuracoes/motorista/novo", methods=["POST"])
def motorista_novo():
    nome = request.form.get("nome","").strip()
    if nome:
        conn = get_conn()
        try:
            conn.execute("INSERT INTO motoristas (nome) VALUES (?)",(nome,))
            conn.commit()
            flash(f"Motorista {nome} adicionado.","ok")
            cache_clear("motoristas")
        except sqlite3.IntegrityError:
            flash("Motorista já cadastrado.","erro")
        finally:
            conn.close()
    return redirect(url_for("configuracoes"))


@app.route("/configuracoes/motorista/<int:mid>/excluir", methods=["POST"])
def motorista_excluir(mid):
    conn = get_conn()
    conn.execute("DELETE FROM motoristas WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    cache_clear("motoristas")
    flash("Motorista removido.", "ok")
    return redirect(url_for("configuracoes"))


# --- RESET ---

@app.route("/reset", methods=["GET","POST"])
def reset_banco():
    if not ENABLE_RESET:
        flash("Reset do banco desabilitado neste ambiente.", "erro")
        return redirect(url_for("index"))
    if request.method == "POST":
        token = request.form.get("confirmacao", "").strip()
        if token != "RESETAR":
            flash('Digite RESETAR no campo de confirmação.', "erro")
            return render_template("reset.html")
        conn = get_conn()
        for t in (
            "historico_pedidos", "pedidos", "enderecos_cliente", "clientes",
            "cacambas", "motoristas", "config",
        ):
            conn.execute("DROP TABLE IF EXISTS " + t)
        conn.commit()
        conn.close()
        init_db()
        seed_if_empty()
        flash("Banco resetado.","ok")
        return redirect(url_for("index"))
    return render_template("reset.html")


# --- APIs JSON ---


@app.route("/pedidos/<int:pid>/trocar", methods=["POST"])
def pedido_trocar(pid):
    """Finaliza a caçamba atual e abre automaticamente novo pedido no mesmo endereço."""
    motorista  = request.form.get("motorista_retirada", "").strip()
    cacamba_id = request.form.get("cacamba_id", "").strip()
    motoristas = _get_motoristas()
    conn = get_conn()
    try:
        p = conn.execute(
            "SELECT * FROM pedidos WHERE id=?", (pid,)
        ).fetchone()
        if not p or p["status"] != "no_endereco":
            flash("Só é possível trocar caçamba em pedidos ativos no endereço.", "erro")
            return redirect(request.referrer or url_for("pedidos"))
        if not motorista or motorista not in motoristas:
            flash("Selecione o motorista.", "erro")
            return redirect(request.referrer or url_for("pedidos"))
        if not cacamba_id or not str(cacamba_id).isdigit():
            flash("Selecione a caçamba substituta.", "erro")
            return redirect(request.referrer or url_for("pedidos"))

        cab_nova = conn.execute(
            "SELECT id,capacidade_m3,status FROM cacambas WHERE id=?", (int(cacamba_id),)
        ).fetchone()
        if not cab_nova or cab_nova["status"] != "disponivel":
            flash("Caçamba selecionada não está disponível.", "erro")
            return redirect(request.referrer or url_for("pedidos"))
        if cab_nova["capacidade_m3"] != p["capacidade_m3"]:
            flash("Capacidade da caçamba nova não corresponde ao pedido.", "erro")
            return redirect(request.referrer or url_for("pedidos"))

        hoje = date.today()
        fim_real = hoje.isoformat()
        fim_prev = (hoje + timedelta(days=_dias_locacao() - 1)).isoformat()
        agora    = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Finaliza pedido atual
        conn.execute(
            "UPDATE cacambas SET status='disponivel' WHERE id=?", (p["cacamba_id"],)
        )
        conn.execute(
            "UPDATE pedidos SET status='finalizado', motorista_retirada=?, data_fim_real=? WHERE id=?",
            (motorista, fim_real, pid),
        )
        # Ocupa caçamba nova
        conn.execute("UPDATE cacambas SET status='em_uso' WHERE id=?", (int(cacamba_id),))
        # Cria novo pedido automaticamente
        valor = _valor_locacao(p["capacidade_m3"])
        cur = conn.execute(
            """INSERT INTO pedidos
               (cliente_id,cacamba_id,capacidade_m3,valor_total,endereco_obra,
                obra_cep,obra_rua,obra_quadra,obra_numero,obra_bairro,
                data_inicio,data_fim_prevista,status,pago,criado_em,observacoes,
                motorista_entrega,latitude,longitude)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'no_endereco',0,?,?,?,?,?)""",
            (p["cliente_id"], int(cacamba_id), p["capacidade_m3"], valor,
             p["endereco_obra"], p["obra_cep"], p["obra_rua"],
             p["obra_quadra"], p["obra_numero"], p["obra_bairro"],
             hoje.isoformat(), fim_prev, agora,
             f"Troca da caçamba do pedido #{pid}",
             motorista, p["latitude"], p["longitude"]),
        )
        novo_pid = cur.lastrowid
        _registrar_historico(conn, pid, "troca", f"Novo pedido #{novo_pid}")
        _registrar_historico(conn, novo_pid, "criado", f"Troca do pedido #{pid}")
        conn.commit()
        _cache_invalidate()
        flash(f"Troca registrada! Pedido #{novo_pid} criado com a caçamba nova. Retirada prevista: {fim_prev}.", "ok")
    except Exception as e:
        conn.rollback()
        flash(f"Erro ao processar troca: {e}", "erro")
    finally:
        conn.close()
    return redirect(request.referrer or url_for("operacoes"))


@app.route("/pedidos/<int:pid>/comprovante")
def pedido_comprovante(pid):
    """Retorna texto formatado do comprovante para copiar/WhatsApp."""
    conn = get_conn()
    p = conn.execute(
        """SELECT p.*, c.nome AS cliente_nome, c.telefone,
                  ca.codigo AS cacamba_codigo
           FROM pedidos p
           JOIN clientes c ON c.id=p.cliente_id
           LEFT JOIN cacambas ca ON ca.id=p.cacamba_id
           WHERE p.id=?""", (pid,)
    ).fetchone()
    conn.close()
    if not p:
        return Response("Pedido não encontrado", status=404)
    cfg = _get_config()
    empresa = cfg.get("empresa_nome", "Caçambas Bauru")
    fone    = cfg.get("empresa_fone", "")
    end = p["obra_rua"] or ""
    if p["obra_quadra"]: end += f", Q.{p['obra_quadra']}"
    if p["obra_numero"]: end += f", nº {p['obra_numero']}"
    if p["obra_bairro"]: end += f" — {p['obra_bairro']}"
    if p["obra_cep"] and len(p["obra_cep"]) == 8:
        cep = p["obra_cep"]
        end += f"\n  CEP: {cep[:5]}-{cep[5:]}"
    linhas = [
        f"*{empresa}* — Comprovante de Locação",
        f"{'─'*35}",
        f"*Pedido:*       #{p['id']}",
        f"*Cliente:*      {p['cliente_nome']}",
        f"*Caçamba:*      Nº {p['cacamba_codigo'] or '(a confirmar)'} — {p['capacidade_m3']} m³",
        f"*Endereço:*     {end}",
        f"*Data entrega:* {p['data_inicio'] or 'na entrega'}",
        f"*Ret. prevista:* {p['data_fim_prevista'] or '—'}",
        f"{'─'*35}",
    ]
    if fone:
        linhas.append(f"Telefone {empresa}: {fone}")
    linhas.append("Guarde este comprovante. Em caso de dúvidas, entre em contato.")
    texto = "\n".join(linhas)
    return Response(json.dumps({"texto": texto, "telefone": p["telefone"]}),
                    mimetype="application/json")

@app.route("/api/cacambas-disponiveis")
def api_cacambas_disponiveis():
    cap = request.args.get("capacidade","")
    conn = get_conn()
    if cap.isdigit():
        rows = conn.execute(
            "SELECT id,codigo,capacidade_m3 FROM cacambas"
            " WHERE status='disponivel' AND capacidade_m3=?"
            " ORDER BY codigo",(int(cap),)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id,codigo,capacidade_m3 FROM cacambas WHERE status='disponivel'"
            " ORDER BY codigo"
        ).fetchall()
    conn.close()
    return Response(json.dumps([dict(r) for r in rows]), mimetype="application/json")


@app.route("/api/clientes/<int:cid>/enderecos")
def api_cliente_enderecos(cid):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id,cep,rua,quadra,numero,bairro,complemento,apelido"
        " FROM enderecos_cliente WHERE cliente_id=? ORDER BY id",(cid,)
    ).fetchall()
    conn.close()
    return Response(json.dumps([dict(r) for r in rows]), mimetype="application/json")


@app.route("/api/clientes/<int:cid>/info")
def api_cliente_info(cid):
    conn = get_conn()
    row = conn.execute("SELECT id, nome FROM clientes WHERE id=?", (cid,)).fetchone()
    conn.close()
    if not row:
        return Response(json.dumps({}), status=404, mimetype="application/json")
    return Response(json.dumps({"id": row["id"], "nome": row["nome"]}), mimetype="application/json")


@app.route("/api/clientes/search")
def api_clientes_search():
    q = request.args.get("q", "").strip()
    conn = get_conn()
    if q:
        like = f"%{q}%"
        rows = conn.execute(
            """SELECT c.id, c.nome, c.cpf, c.cnpj, c.tipo_pessoa, c.telefone,
                      e.rua, e.numero, e.bairro
               FROM clientes c
               LEFT JOIN enderecos_cliente e ON e.id = (
                   SELECT id FROM enderecos_cliente WHERE cliente_id=c.id ORDER BY id LIMIT 1
               )
               WHERE c.nome LIKE ? OR c.cpf LIKE ? OR c.cnpj LIKE ? OR c.telefone LIKE ?
               ORDER BY c.nome LIMIT 20""",
            (like, like, like, like)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT c.id, c.nome, c.cpf, c.cnpj, c.tipo_pessoa, c.telefone,
                      e.rua, e.numero, e.bairro
               FROM clientes c
               LEFT JOIN enderecos_cliente e ON e.id = (
                   SELECT id FROM enderecos_cliente WHERE cliente_id=c.id ORDER BY id LIMIT 1
               )
               ORDER BY c.nome LIMIT 20"""
        ).fetchall()
    results = []
    for r in rows:
        # Buscar primeiro endereço do cliente
        doc = ""
        if r["tipo_pessoa"] == "pf" and r["cpf"] and len(r["cpf"]) == 11:
            c = r["cpf"]
            doc = f"{c[0:3]}.{c[3:6]}.{c[6:9]}-{c[9:11]}"
        elif r["tipo_pessoa"] == "pj" and r["cnpj"] and len(r["cnpj"]) == 14:
            c = r["cnpj"]
            doc = f"{c[0:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:14]}"
        endereco_str = ""
        end = r
        if r["rua"]:
            endereco_str = r["rua"]
            if r["numero"]: endereco_str += f", {r['numero']}"
            if end["bairro"]:  endereco_str += f" — {end['bairro']}"
        results.append({
            "id": r["id"],
            "text": r["nome"],
            "doc": doc,
            "endereco": endereco_str,
            "telefone": r["telefone"] or "",
        })
    conn.close()
    return Response(json.dumps(results), mimetype="application/json")


@app.route("/api/stats")
def api_stats():
    return Response(json.dumps(_stats()), mimetype="application/json")


@app.route("/api/financeiro/resumo")
def api_financeiro_resumo():
    hoje_iso = date.today().isoformat()
    conn = get_conn()
    resumo = conn.execute(
        """SELECT
             COUNT(*) AS total,
             COALESCE(SUM(valor_total),0) AS valor_total,
             COALESCE(SUM(CASE WHEN pago=1 THEN valor_total ELSE 0 END),0) AS valor_pago,
             COALESCE(SUM(CASE WHEN pago=0 AND status!='pendente' THEN valor_total ELSE 0 END),0) AS valor_pendente,
             COALESCE(SUM(CASE WHEN pago=0 AND status='no_endereco' AND data_fim_prevista < ? THEN valor_total ELSE 0 END),0) AS valor_vencido
           FROM pedidos WHERE status != 'pendente'""",
        (hoje_iso,),
    ).fetchone()
    meses = conn.execute(
        """SELECT substr(COALESCE(NULLIF(data_fim_real,''), NULLIF(data_inicio,''), criado_em), 1, 7) AS mes,
                  COUNT(*) AS qtd,
                  COALESCE(SUM(valor_total),0) AS valor
           FROM pedidos
           WHERE status != 'pendente'
           GROUP BY mes
           ORDER BY mes DESC
           LIMIT 6"""
    ).fetchall()
    conn.close()
    return Response(json.dumps({
        "resumo": dict(resumo),
        "meses": [dict(r) for r in meses],
    }), mimetype="application/json")

@app.route("/api/pedidos/enderecos")
def api_pedidos_enderecos():
    """Autocomplete de endereços de obra dos pedidos."""
    import unicodedata as _ud3
    def _norm3(s):
        return _ud3.normalize("NFD", str(s)).encode("ascii","ignore").decode("ascii").lower()

    q = request.args.get("q", "").strip()
    conn = get_conn()

    sql = (
        "SELECT DISTINCT obra_rua, obra_quadra, obra_numero, obra_bairro "
        "FROM pedidos WHERE obra_rua IS NOT NULL AND obra_rua != '' "
    )
    params = []
    if q:
        tokens = [_norm3(t) for t in q.split() if t]
        for t in tokens:
            sql += " AND NORM(COALESCE(obra_rua,'') || ' ' || COALESCE(obra_quadra,'') || ' ' || COALESCE(obra_numero,'') || ' ' || COALESCE(obra_bairro,'')) LIKE NORM(?)"
            params.append(f"%{t}%")
    sql += " ORDER BY obra_rua LIMIT 12"
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    results = []
    for r in rows:
        rua = (r["obra_rua"] or "").strip()
        quadra = (r["obra_quadra"] or "").strip()
        numero = (r["obra_numero"] or "").strip()
        bairro = (r["obra_bairro"] or "").strip()
        label = rua
        if quadra:
            label += f", Q.{quadra}"
        if numero:
            label += f", nº {numero}"
        if bairro:
            label += f" — {bairro}"
        results.append({"label": label, "rua": rua, "quadra": quadra, "numero": numero, "bairro": bairro})

    return Response(json.dumps(results), mimetype="application/json")


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "1" if not _IS_PRODUCTION else "0") == "1"
    print(f"Servidor: http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)
