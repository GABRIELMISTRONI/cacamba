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

from flask import Flask, Response, flash, redirect, render_template, request, url_for

from database import CAPACIDADES_M3, get_conn, init_db, seed_if_empty
from geocode import geocodificar_obra, geocodificar_obra_bauru

app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY", "cacambas-bauru-2024-prod-dev-only")

BAURU_LAT = -22.3145
BAURU_LON = -49.0643
MAX_DIAS_LOCACAO = 7
MOTORISTAS = ("Roberto", "Cicero")

init_db()
seed_if_empty()


# ╔════════════════════════════════════════════════════════╗
# ║  HELPERS                                                 ║
# ╚══════════════════════════════════════════════════════════╝

def _digits(s):
    return "".join(c for c in (s or "") if c.isdigit())

def _cep_norm(s):
    return _digits(s)[:8]

def _cep_ok(s):
    return len(_cep_norm(s)) == 8

def _cpf_ok(cpf):
    n = _digits(cpf)
    return len(n) == 11 and n != n[0] * 11

def _cnpj_ok(cnpj):
    return len(_digits(cnpj)) == 14

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
    pid = ped["id"]
    return (BAURU_LAT + ((pid % 9) - 4) * 0.003,
            BAURU_LON + ((pid % 11) - 5) * 0.003)

def _stats():
    conn = get_conn()
    s = {
        "clientes":      conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0],
        "disponiveis":   conn.execute("SELECT COUNT(*) FROM cacambas WHERE status='disponivel'").fetchone()[0],
        "em_uso":        conn.execute("SELECT COUNT(*) FROM cacambas WHERE status='em_uso'").fetchone()[0],
        "manutencao":    conn.execute("SELECT COUNT(*) FROM cacambas WHERE status='manutencao'").fetchone()[0],
        "pedidos_abertos": conn.execute(
            "SELECT COUNT(*) FROM pedidos WHERE status IN ('pendente','confirmado','no_endereco')"
        ).fetchone()[0],
        "a_receber":     conn.execute(
            "SELECT COUNT(*) FROM pedidos WHERE pago=0 AND status!='pendente'"
        ).fetchone()[0],
        "entregas_hoje": conn.execute(
            "SELECT COUNT(*) FROM pedidos WHERE status='confirmado'"
        ).fetchone()[0],
        "retiradas_vencidas": conn.execute(
            f"SELECT COUNT(*) FROM pedidos WHERE status='no_endereco' AND data_fim_prevista < '{date.today().isoformat()}'"
        ).fetchone()[0],
    }
    conn.close()
    return s


def _get_config():
    conn = get_conn()
    cfg = {r["chave"]: r["valor"] for r in conn.execute("SELECT chave,valor FROM config").fetchall()}
    conn.close()
    return cfg


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
    cfg = _get_config()
    try:
        conn = get_conn()
        hoje_iso = date.today().isoformat()
        vencidas  = conn.execute("SELECT COUNT(*) FROM pedidos WHERE status='no_endereco' AND data_fim_prevista < ?", (hoje_iso,)).fetchone()[0]
        pendentes = conn.execute("SELECT COUNT(*) FROM pedidos WHERE status='pendente'").fetchone()[0]
        confirmar = conn.execute("SELECT COUNT(*) FROM pedidos WHERE status='confirmado'").fetchone()[0]
        conn.close()
        stats_sidebar = {"vencidas": vencidas, "pendentes": pendentes, "confirmar": confirmar}
    except Exception:
        stats_sidebar = {"vencidas": 0, "pendentes": 0, "confirmar": 0}
    return {
        "empresa_nome": cfg.get("empresa_nome", "Caçambas Bauru"),
        "empresa_fone": cfg.get("empresa_fone", ""),
        "dias_locacao": _dias_locacao(cfg),
        "motoristas": _get_motoristas(),
        "stats_sidebar": stats_sidebar,
    }


# ╔════════════════════════════════════════════════════════╗
# ║  DASHBOARD                                               ║
# ╚══════════════════════════════════════════════════════════╝

@app.route("/")
def index():
    conn = get_conn()
    ultimos = conn.execute(
        """SELECT p.id, p.status, p.pago, p.endereco_obra,
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
    conn.close()
    return render_template("index.html",
        stats=_stats(), ultimos=ultimos,
        hoje=hoje.isoformat(), vencendo_amanha=vencendo_amanha)


# ╔════════════════════════════════════════════════════════╗
# ║  CLIENTES                                                ║
# ╚══════════════════════════════════════════════════════════╝

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
    conn.close()
    return render_template("cliente_detalhe.html",
        cliente=c, enderecos=enderecos, pedidos=pedidos,
        hoje=date.today().isoformat())


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


# ╔════════════════════════════════════════════════════════╗
# ║  CAÇAMBAS                                                ║
# ╚══════════════════════════════════════════════════════════╝

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


# ╔════════════════════════════════════════════════════════╗
# ║  PEDIDOS                                                 ║
# ╚══════════════════════════════════════════════════════════╝

_PEDIDO_SELECT = """
    SELECT p.id,p.status,p.pago,p.capacidade_m3,p.endereco_obra,
           p.obra_cep,p.obra_rua,p.obra_quadra,p.obra_numero,p.obra_bairro,
           p.data_inicio,p.data_fim_prevista,p.data_fim_real,p.criado_em,
           p.motorista_entrega,p.motorista_retirada,p.latitude,p.longitude,
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
    conn = get_conn()
    where, params = [], []
    if s in ("pendente","confirmado","no_endereco","finalizado"):
        where.append("p.status=?")
        params.append(s)
    if pg in ("0","1"):
        where.append("p.pago=?")
        params.append(int(pg))
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
    sql = _PEDIDO_SELECT
    if where: sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY p.id DESC"
    rows = conn.execute(sql, params).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM pedidos").fetchone()[0]
    conn.close()
    return render_template("pedidos.html", pedidos=rows,
        filtro_status=s, filtro_pago=pg, busca=q, total=total,
        motoristas=_get_motoristas(), hoje=date.today().isoformat())


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
        # Data prevista: vinda do form (editável), senão 7 dias a partir de hoje
        data_fim = request.form.get("data_fim_prevista","").strip()
        if not data_fim:
            from datetime import timedelta
            data_fim = (date.today() + timedelta(days=7)).isoformat()
        conn.execute(
            """INSERT INTO pedidos
               (cliente_id,cacamba_id,capacidade_m3,endereco_obra,
                obra_cep,obra_rua,obra_quadra,obra_numero,obra_bairro,
                data_inicio,data_fim_prevista,status,pago,criado_em,observacoes)
               VALUES (?,NULL,?,?,?,?,?,?,?,?,?,'pendente',0,?,?)""",
            (int(cid),int(cap),linha,obra_cep,obra_rua,obra_quadra,
             obra_numero,obra_bairro,agora,data_fim,agora,obs),
        )
        conn.commit()
        conn.close()
        flash("Solicitação registrada.","ok")
        return redirect(url_for("cliente_detalhe", cid=int(cid)) if pre else url_for("pedidos"))

    conn.close()
    return render_template("pedido_novo.html", clientes=clientes_list, pre_cliente_id=pre)


@app.route("/pedidos/<int:pid>/confirmar", methods=["POST"])
def pedido_confirmar(pid):
    conn = get_conn()
    p = conn.execute("SELECT id,status FROM pedidos WHERE id=?",(pid,)).fetchone()
    if not p or p["status"] != "pendente":
        conn.close()
        flash("Pedido não encontrado ou já processado.","erro")
        return redirect(url_for("pedidos"))
    conn.execute("UPDATE pedidos SET status='confirmado' WHERE id=?",(pid,))
    conn.commit()
    conn.close()
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
            (int(cacamba_id),motorista,inicio.isoformat(),fim.isoformat(),lat,lon,pid),
        )
        conn.commit()
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
    if not p or p["status"] not in ("confirmado","no_endereco"):
        conn.close()
        flash("Não é possível finalizar este pedido.","erro")
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
        conn.execute("UPDATE pedidos SET status='finalizado', motorista_retirada=?, data_fim_real=? WHERE id=?",(motorista_retirada, data_retirada, pid))
        conn.commit()
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
    if not p or p["status"] not in ("confirmado","no_endereco"):
        conn.close()
        flash("Não é possível cancelar este pedido.","erro")
        return redirect(url_for("pedidos"))
    if p["cacamba_id"]:
        conn.execute("UPDATE cacambas SET status='disponivel' WHERE id=?",(p["cacamba_id"],))
    fim_real = date.today().isoformat()
    conn.execute("UPDATE pedidos SET status='cancelado', data_fim_real=? WHERE id=?",(fim_real, pid))
    conn.commit()
    conn.close()
    flash("Pedido cancelado. Caçamba liberada.","ok")
    return redirect(request.referrer or url_for("pedidos"))


@app.route("/pedidos/<int:pid>/pagar", methods=["POST"])
def pedido_pagar(pid):
    conn = get_conn()
    conn.execute("UPDATE pedidos SET pago=1 WHERE id=?",(pid,))
    conn.commit()
    conn.close()
    flash("Pagamento registrado.","ok")
    origem = request.form.get("origem","")
    if origem.startswith("cliente_"):
        try: return redirect(url_for("cliente_detalhe", cid=int(origem.split("_")[1])))
        except: pass
    return redirect(request.referrer or url_for("pedidos"))


@app.route("/pedidos/<int:pid>/observacao", methods=["POST"])
def pedido_observacao(pid):
    obs = request.form.get("observacoes","").strip()
    conn = get_conn()
    conn.execute("UPDATE pedidos SET observacoes=? WHERE id=?",(obs,pid))
    conn.commit()
    conn.close()
    flash("Observação salva.","ok")
    return redirect(request.referrer or url_for("pedidos"))


# ╔════════════════════════════════════════════════════════╗
# ║  OPERAÇÕES (entregas + retiradas do dia)                 ║
# ╚══════════════════════════════════════════════════════════╝

@app.route("/operacoes")
def operacoes():
    conn = get_conn()
    hoje = date.today()
    hoje_iso = hoje.isoformat()
    amanha_iso = (hoje + timedelta(days=1)).isoformat()
    filtro = request.args.get("f", "")

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

    vencidas        = [p for p in no_end if p["data_fim_prevista"] and p["data_fim_prevista"] < hoje_iso]
    retiradas_hoje  = [p for p in no_end if p["data_fim_prevista"] == hoje_iso]
    retiradas_ama   = [p for p in no_end if p["data_fim_prevista"] == amanha_iso]

    # Aplicar filtro rápido
    if filtro == "entregar":
        no_end_view = []
        entregas_view = entregas
    elif filtro == "hoje":
        no_end_view = retiradas_hoje
        entregas_view = []
    elif filtro == "atrasadas":
        no_end_view = vencidas
        entregas_view = []
    elif filtro == "amanha":
        no_end_view = retiradas_ama
        entregas_view = []
    else:
        no_end_view   = no_end
        entregas_view = entregas

    conn.close()
    return render_template("operacoes.html",
        entregas=entregas_view, no_end=no_end_view,
        vencidas=vencidas, retiradas_hoje=retiradas_hoje, retiradas_amanha=retiradas_ama,
        total_no_end=len(no_end), total_entregas=len(entregas),
        filtro=filtro,
        motoristas=_get_motoristas(),
        hoje=hoje_iso, amanha=amanha_iso)


# ╔════════════════════════════════════════════════════════╗
# ║  FINANCEIRO                                              ║
# ╚══════════════════════════════════════════════════════════╝

@app.route("/financeiro")
def financeiro():
    f = request.args.get("f","")
    q = request.args.get("q","").strip()
    conn = get_conn()

    where, params = [], []
    where.append("p.status != 'pendente'")
    if f == "pago":    where.append("p.pago=1")
    if f == "pendente": where.append("p.pago=0")
    if q: where.append("c.nome LIKE ?"); params.append(f"%{q}%")

    sql = (_PEDIDO_SELECT + " WHERE " + " AND ".join(where) +
           " ORDER BY p.pago ASC, p.id DESC")
    rows = conn.execute(sql, params).fetchall()

    totais = conn.execute(
        """SELECT
             COUNT(*) AS total,
             SUM(CASE WHEN pago=1 THEN 1 ELSE 0 END) AS pagos,
             SUM(CASE WHEN pago=0 AND status!='pendente' THEN 1 ELSE 0 END) AS pendentes
           FROM pedidos WHERE status != 'pendente'"""
    ).fetchone()
    conn.close()
    return render_template("financeiro.html",
        pedidos=rows, filtro=f, busca=q, totais=totais,
        hoje=date.today().isoformat())


# ╔════════════════════════════════════════════════════════╗
# ║  MAPA                                                    ║
# ╚══════════════════════════════════════════════════════════╝

@app.route("/mapa")
def mapa():
    conn = get_conn()
    rows = conn.execute(
        """SELECT p.id,p.status,p.endereco_obra,p.data_fim_prevista,
                  p.latitude,p.longitude,ca.codigo AS cacamba_codigo,
                  c.nome AS cliente_nome
           FROM pedidos p JOIN clientes c ON c.id=p.cliente_id
           LEFT JOIN cacambas ca ON ca.id=p.cacamba_id
           WHERE p.status IN ('confirmado','no_endereco') ORDER BY p.id"""
    ).fetchall()
    conn.close()
    hoje = date.today()
    marcadores = []
    for r in rows:
        lat, lon = r["latitude"], r["longitude"]
        if lat is None or lon is None:
            lat, lon = _geocode_ped(r)
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


# ╔════════════════════════════════════════════════════════╗
# ║  CONFIGURAÇÕES                                           ║
# ╚══════════════════════════════════════════════════════════╝

@app.route("/configuracoes", methods=["GET","POST"])
def configuracoes():
    conn = get_conn()
    config = {r["chave"]: r["valor"] for r in conn.execute("SELECT chave,valor FROM config").fetchall()}
    conn.close()

    if request.method == "POST":
        dias = request.form.get("dias_locacao","7").strip()
        empresa = request.form.get("empresa_nome","").strip()
        fone_empresa = request.form.get("empresa_fone","").strip()
        try: dias_n = max(1, min(int(dias), 30))
        except: dias_n = 7
        conn = get_conn()
        for k, v in [("dias_locacao", str(dias_n)),
                     ("empresa_nome", empresa),
                     ("empresa_fone", fone_empresa)]:
            conn.execute("INSERT OR REPLACE INTO config (chave,valor) VALUES (?,?)",(k,v))
        conn.commit()
        conn.close()
        flash("Configurações salvas.","ok")
        return redirect(url_for("configuracoes"))

    conn = get_conn()
    motoristas_db = [r[0] for r in conn.execute("SELECT nome FROM motoristas ORDER BY nome").fetchall()]
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
        except sqlite3.IntegrityError:
            flash("Motorista já cadastrado.","erro")
        finally:
            conn.close()
    return redirect(url_for("configuracoes"))


@app.route("/configuracoes/motorista/<int:mid>/excluir", methods=["POST"])
def motorista_excluir(mid):
    conn = get_conn()
    conn.execute("DELETE FROM motoristas WHERE id=?",(mid,))
    conn.commit()
    conn.close()
    flash("Motorista removido.","ok")
    return redirect(url_for("configuracoes"))


# ╔════════════════════════════════════════════════════════╗
# ║  RESET                                                   ║
# ╚══════════════════════════════════════════════════════════╝

@app.route("/reset", methods=["GET","POST"])
def reset_banco():
    if request.method == "POST":
        conn = get_conn()
        for t in ("pedidos","enderecos_cliente","clientes","cacambas","motoristas","config"):
            conn.execute("DROP TABLE IF EXISTS " + t)  # t is from hardcoded tuple, safe
        conn.commit()
        conn.close()
        init_db()
        seed_if_empty()
        flash("Banco resetado.","ok")
        return redirect(url_for("index"))
    return render_template("reset.html")


# ╔════════════════════════════════════════════════════════╗
# ║  APIs JSON                                               ║
# ╚══════════════════════════════════════════════════════════╝


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
        conn.execute(
            """INSERT INTO pedidos
               (cliente_id,cacamba_id,capacidade_m3,endereco_obra,
                obra_cep,obra_rua,obra_quadra,obra_numero,obra_bairro,
                data_inicio,data_fim_prevista,status,pago,criado_em,observacoes,
                motorista_entrega,latitude,longitude)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,'no_endereco',0,?,?,?,?,?)""",
            (p["cliente_id"], int(cacamba_id), p["capacidade_m3"],
             p["endereco_obra"], p["obra_cep"], p["obra_rua"],
             p["obra_quadra"], p["obra_numero"], p["obra_bairro"],
             hoje.isoformat(), fim_prev, agora,
             f"Troca da caçamba do pedido #{pid}",
             motorista, p["latitude"], p["longitude"]),
        )
        novo_pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
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
        linhas.append(f"📞 {empresa}: {fone}")
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
            " ORDER BY CAST(codigo AS INTEGER),codigo",(int(cap),)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id,codigo,capacidade_m3 FROM cacambas WHERE status='disponivel'"
            " ORDER BY CAST(codigo AS INTEGER),codigo"
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
            """SELECT id, nome, cpf, cnpj, tipo_pessoa, telefone
               FROM clientes
               WHERE nome LIKE ? OR cpf LIKE ? OR cnpj LIKE ? OR telefone LIKE ?
               ORDER BY nome LIMIT 20""",
            (like, like, like, like)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, nome, cpf, cnpj, tipo_pessoa, telefone FROM clientes ORDER BY nome LIMIT 20"
        ).fetchall()
    results = []
    for r in rows:
        # Buscar primeiro endereço do cliente
        end = conn.execute(
            "SELECT rua, numero, bairro FROM enderecos_cliente WHERE cliente_id=? ORDER BY id LIMIT 1",
            (r["id"],)
        ).fetchone()
        doc = ""
        if r["tipo_pessoa"] == "pf" and r["cpf"] and len(r["cpf"]) == 11:
            c = r["cpf"]
            doc = f"{c[0:3]}.{c[3:6]}.{c[6:9]}-{c[9:11]}"
        elif r["tipo_pessoa"] == "pj" and r["cnpj"] and len(r["cnpj"]) == 14:
            c = r["cnpj"]
            doc = f"{c[0:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:14]}"
        endereco_str = ""
        if end:
            endereco_str = end["rua"]
            if end["numero"]: endereco_str += f", {end['numero']}"
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


if __name__ == "__main__":
    app.run()

@app.route("/api/pedidos/enderecos")
def api_pedidos_enderecos():
    """Autocomplete de endereços de obra dos pedidos."""
    import unicodedata as _ud3
    def _norm3(s):
        return _ud3.normalize("NFD", str(s)).encode("ascii","ignore").decode("ascii").lower()

    q = request.args.get("q", "").strip()
    conn = get_conn()

    rows = conn.execute(
        "SELECT DISTINCT obra_rua, obra_quadra, obra_numero, obra_bairro "
        "FROM pedidos WHERE obra_rua IS NOT NULL AND obra_rua != '' "
        "ORDER BY obra_rua, obra_quadra, obra_numero"
    ).fetchall()
    conn.close()

    results = []
    seen = set()
    tokens = [_norm3(t) for t in q.split() if t] if q else []

    for r in rows:
        rua    = (r["obra_rua"]    or "").strip()
        quadra = (r["obra_quadra"] or "").strip()
        numero = (r["obra_numero"] or "").strip()
        bairro = (r["obra_bairro"] or "").strip()

        campo_busca = _norm3(f"{rua} {quadra} {numero} {bairro}")

        if tokens and not all(t in campo_busca for t in tokens):
            continue

        label = rua
        if quadra: label += f", Q.{quadra}"
        if numero: label += f", nº {numero}"
        if bairro: label += f" — {bairro}"

        if label not in seen:
            seen.add(label)
            results.append({"label": label, "rua": rua, "quadra": quadra, "numero": numero, "bairro": bairro})
        if len(results) >= 10:
            break

    return Response(json.dumps(results), mimetype="application/json")