# -*- coding: utf-8 -*-
"""
Sistema de gestão para empresa de caçambas — Bauru/SP
Execute:  pip install -r requirements.txt
Acesse:   http://127.0.0.1:5000
"""
import json
import sqlite3
from datetime import date, datetime, timedelta

from flask import Flask, Response, flash, redirect, render_template, request, url_for

from database import CAPACIDADES_M3, get_conn, init_db, seed_if_empty
from geocode import geocodificar_obra, geocodificar_obra_bauru

app = Flask(__name__)
app.secret_key = "cacambas-bauru-2024-prod"

BAURU_LAT = -22.3145
BAURU_LON = -49.0643
MAX_DIAS_LOCACAO = 7
MOTORISTAS = ("Roberto", "Cicero")

init_db()
seed_if_empty()


# ╔══════════════════════════════════════════════════════════╗
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
    keys = ped.keys()
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
    # fallback: dispersa pontos em volta de Bauru para visualização
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
    return {
        "empresa_nome": cfg.get("empresa_nome", "Caçambas Bauru"),
        "empresa_fone": cfg.get("empresa_fone", ""),
        "dias_locacao": _dias_locacao(cfg),
        "motoristas": _get_motoristas(),
    }


# ╔══════════════════════════════════════════════════════════╗
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
    conn.close()
    return render_template("index.html",
        stats=_stats(), ultimos=ultimos,
        hoje=date.today().isoformat())


# ╔══════════════════════════════════════════════════════════╗
# ║  CLIENTES                                                ║
# ╚══════════════════════════════════════════════════════════╝

@app.route("/clientes")
def clientes():
    busca = request.args.get("busca", "").strip()
    conn = get_conn()
    like = f"%{busca}%"
    if busca:
        rows = conn.execute(
            """SELECT id,tipo_pessoa,nome,cpf,cnpj,razao_social,telefone,email,rua,numero
               FROM clientes
               WHERE nome LIKE ? OR cpf LIKE ? OR cnpj LIKE ? OR razao_social LIKE ?
                  OR rua LIKE ? OR telefone LIKE ?
               ORDER BY nome""",
            (like,)*6,
        ).fetchall()
        # também enderecos_cliente
        ids_end = [r[0] for r in conn.execute(
            "SELECT DISTINCT cliente_id FROM enderecos_cliente WHERE rua LIKE ? OR bairro LIKE ?",
            (like, like),
        ).fetchall()]
        existentes = {r["id"] for r in rows}
        if ids_end:
            extras = conn.execute(
                f"SELECT id,tipo_pessoa,nome,cpf,cnpj,razao_social,telefone,email,rua,numero"
                f" FROM clientes WHERE id IN ({','.join('?'*len(ids_end))}) ORDER BY nome",
                ids_end,
            ).fetchall()
            rows = list(rows) + [r for r in extras if r["id"] not in existentes]
    else:
        rows = conn.execute(
            "SELECT id,tipo_pessoa,nome,cpf,cnpj,razao_social,telefone,email,rua,numero"
            " FROM clientes ORDER BY nome"
        ).fetchall()
    # Contagem de pedidos por cliente
    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT cliente_id, COUNT(*) FROM pedidos GROUP BY cliente_id"
    ).fetchall()}
    conn.close()
    return render_template("clientes.html", clientes=rows, busca=busca, counts=counts)


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
        conn.commit(); conn.close()
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
        conn.close(); flash("Cliente não encontrado.","erro")
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
        conn.close(); flash("Cliente não encontrado.","erro")
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

        conn.execute(
            """UPDATE clientes SET tipo_pessoa=?,nome=?,cpf=?,cnpj=?,razao_social=?,
               telefone=?,email=?,cep=?,rua=?,numero=?,complemento=? WHERE id=?""",
            (tipo,nome,_digits(cpf) if tipo=="pf" else "",
             _digits(cnpj) if tipo=="pj" else "",
             razao,tel,email,cep,rua,numero,comp,cid),
        )
        conn.commit(); conn.close()
        flash("Dados atualizados.","ok")
        return redirect(url_for("cliente_detalhe", cid=cid))
    conn.close()
    return render_template("cliente_editar.html", cliente=c)


@app.route("/clientes/<int:cid>/excluir", methods=["POST"])
def cliente_excluir(cid):
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM pedidos WHERE cliente_id=?",(cid,)).fetchone()[0]
    if n:
        conn.close(); flash("Não é possível excluir cliente com locações registradas.","erro")
        return redirect(url_for("cliente_detalhe", cid=cid))
    conn.execute("DELETE FROM enderecos_cliente WHERE cliente_id=?",(cid,))
    conn.execute("DELETE FROM clientes WHERE id=?",(cid,))
    conn.commit(); conn.close()
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
    conn.commit(); conn.close()
    flash("Endereço adicionado.","ok")
    return redirect(url_for("cliente_detalhe", cid=cid))


@app.route("/clientes/<int:cid>/endereco/<int:eid>/excluir", methods=["POST"])
def cliente_endereco_excluir(cid, eid):
    conn = get_conn()
    conn.execute("DELETE FROM enderecos_cliente WHERE id=? AND cliente_id=?",(eid,cid))
    conn.commit(); conn.close()
    flash("Endereço removido.","ok")
    return redirect(url_for("cliente_detalhe", cid=cid))


# ╔══════════════════════════════════════════════════════════╗
# ║  CAÇAMBAS                                                ║
# ╚══════════════════════════════════════════════════════════╝

@app.route("/cacambas")
def cacambas():
    f = request.args.get("f","")
    conn = get_conn()
    q = "SELECT id,codigo,capacidade_m3,status FROM cacambas"
    params = ()
    if f in ("disponivel","em_uso","manutencao"):
        q += " WHERE status=?"; params = (f,)
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
        if not cod: flash("Informe o número da caçamba.","erro"); return render_template("cacamba_nova.html")
        if not _cap_ok(cap): flash("Capacidade inválida (3 ou 4 m³).","erro"); return render_template("cacamba_nova.html")
        try:
            conn = get_conn()
            conn.execute("INSERT INTO cacambas (codigo,capacidade_m3,status) VALUES (?,?,'disponivel')",(cod,int(cap)))
            conn.commit(); conn.close()
            flash(f"Caçamba nº {cod} cadastrada.","ok")
            return redirect(url_for("cacambas"))
        except sqlite3.IntegrityError:
            flash("Já existe uma caçamba com esse código.","erro")
    return render_template("cacamba_nova.html")


@app.route("/cacambas/<int:cid>/manutencao", methods=["POST"])
def cacamba_manutencao(cid):
    conn = get_conn()
    conn.execute("UPDATE cacambas SET status='manutencao' WHERE id=? AND status='disponivel'",(cid,))
    conn.commit(); conn.close()
    flash("Caçamba marcada para manutenção.","ok")
    return redirect(url_for("cacambas"))


@app.route("/cacambas/<int:cid>/disponivel", methods=["POST"])
def cacamba_disponivel(cid):
    conn = get_conn()
    conn.execute("UPDATE cacambas SET status='disponivel' WHERE id=?",(cid,))
    conn.commit(); conn.close()
    flash("Caçamba liberada.","ok")
    return redirect(url_for("cacambas"))


# ╔══════════════════════════════════════════════════════════╗
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
    s   = request.args.get("s","")   # filtro status
    pg  = request.args.get("p","")   # filtro pago (0/1)
    q   = request.args.get("q","").strip()  # busca cliente
    conn = get_conn()
    where, params = [], []
    if s in ("pendente","confirmado","no_endereco","finalizado"):
        where.append("p.status=?"); params.append(s)
    if pg in ("0","1"):
        where.append("p.pago=?"); params.append(int(pg))
    if q:
        pedido_id = int(q) if q.isdigit() else -1
        where.append("(c.nome LIKE ? OR p.id = ? OR p.endereco_obra LIKE ? OR p.obra_rua LIKE ?)")
        params.extend([f"%{q}%", pedido_id, f"%{q}%", f"%{q}%"])
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
    clientes_list = conn.execute("SELECT id,nome FROM clientes ORDER BY nome").fetchall()

    if request.method == "POST":
        cid   = request.form.get("cliente_id","").strip()
        cap   = request.form.get("capacidade_m3","").strip()
        end_id = request.form.get("endereco_id","").strip()
        obs   = request.form.get("observacoes","").strip()

        if not cid or not cap:
            flash("Cliente e tamanho são obrigatórios.","erro")
            conn.close()
            return render_template("pedido_novo.html", clientes=clientes_list, pre_cliente_id=pre)
        if not _cap_ok(cap):
            flash("Tamanho inválido.","erro")
            conn.close()
            return render_template("pedido_novo.html", clientes=clientes_list, pre_cliente_id=pre)

        # Resolve endereço
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
            # Salva o novo endereço
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
        conn.execute(
            """INSERT INTO pedidos
               (cliente_id,cacamba_id,capacidade_m3,endereco_obra,
                obra_cep,obra_rua,obra_quadra,obra_numero,obra_bairro,
                data_inicio,data_fim_prevista,status,pago,criado_em,observacoes)
               VALUES (?,NULL,?,?,?,?,?,?,?,?,?,'pendente',0,?,?)""",
            (int(cid),int(cap),linha,obra_cep,obra_rua,obra_quadra,
             obra_numero,obra_bairro,"","",agora,obs),
        )
        conn.commit(); conn.close()
        flash("Solicitação registrada.","ok")
        return redirect(url_for("cliente_detalhe", cid=int(cid)) if pre else url_for("pedidos"))

    conn.close()
    return render_template("pedido_novo.html", clientes=clientes_list, pre_cliente_id=pre)


@app.route("/pedidos/<int:pid>/confirmar", methods=["POST"])
def pedido_confirmar(pid):
    conn = get_conn()
    p = conn.execute("SELECT id,status FROM pedidos WHERE id=?",(pid,)).fetchone()
    if not p or p["status"] != "pendente":
        conn.close(); flash("Pedido não encontrado ou já processado.","erro")
        return redirect(url_for("pedidos"))
    conn.execute("UPDATE pedidos SET status='confirmado' WHERE id=?",(pid,))
    conn.commit(); conn.close()
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
        conn.close(); flash("Só é possível entregar pedidos confirmados.","erro")
        return redirect(url_for("pedidos"))
    if not cacamba_id:
        conn.close(); flash("Selecione a caçamba.","erro")
        return redirect(url_for("pedidos"))
    motoristas = _get_motoristas()
    if motorista not in motoristas:
        conn.close(); flash("Selecione o motorista.","erro")
        return redirect(url_for("pedidos"))

    cab = conn.execute("SELECT id,capacidade_m3,status FROM cacambas WHERE id=?",(int(cacamba_id),)).fetchone()
    if not cab:
        conn.close(); flash("Caçamba não encontrada.","erro"); return redirect(url_for("pedidos"))
    if cab["capacidade_m3"] != ped["capacidade_m3"]:
        conn.close(); flash("Capacidade da caçamba não corresponde ao pedido.","erro"); return redirect(url_for("pedidos"))
    if cab["status"] != "disponivel":
        conn.close(); flash("Caçamba não está disponível.","erro"); return redirect(url_for("pedidos"))

    lat, lon = _geocode_ped(ped)
    inicio = date.today()
    fim    = inicio + timedelta(days=_dias_locacao() - 1)

    if ped["cacamba_id"]:
        conn.execute("UPDATE cacambas SET status='disponivel' WHERE id=?",(ped["cacamba_id"],))
    conn.execute("UPDATE cacambas SET status='em_uso' WHERE id=?",(int(cacamba_id),))
    conn.execute(
        """UPDATE pedidos SET status='no_endereco',cacamba_id=?,motorista_entrega=?,
           data_inicio=?,data_fim_prevista=?,latitude=?,longitude=? WHERE id=?""",
        (int(cacamba_id),motorista,inicio.isoformat(),fim.isoformat(),lat,lon,pid),
    )
    conn.commit(); conn.close()
    flash(f"Entrega registrada por {motorista}. Retirada prevista: {fim.strftime('%d/%m/%Y')}.","ok")
    return redirect(request.referrer or url_for("pedidos"))


@app.route("/pedidos/<int:pid>/finalizar", methods=["POST"])
def pedido_finalizar(pid):
    motorista_retirada = request.form.get("motorista_retirada","").strip()
    data_retirada = request.form.get("data_retirada","").strip()
    conn = get_conn()
    p = conn.execute("SELECT id,status,cacamba_id FROM pedidos WHERE id=?",(pid,)).fetchone()
    if not p or p["status"] not in ("confirmado","no_endereco"):
        conn.close(); flash("Não é possível finalizar este pedido.","erro"); return redirect(url_for("pedidos"))
    if not motorista_retirada or not data_retirada:
        conn.close(); flash("Selecione o motorista e a data de retirada.","erro"); return redirect(url_for("pedidos"))
    motoristas = _get_motoristas()
    if motorista_retirada not in motoristas:
        conn.close(); flash("Selecione um motorista válido.","erro"); return redirect(url_for("pedidos"))
    try:
        data_ret = datetime.strptime(data_retirada, "%Y-%m-%d").date()
        if data_ret > date.today():
            conn.close(); flash("Data de retirada não pode ser futura.","erro"); return redirect(url_for("pedidos"))
    except:
        conn.close(); flash("Data inválida.","erro"); return redirect(url_for("pedidos"))
    if p["cacamba_id"]:
        conn.execute("UPDATE cacambas SET status='disponivel' WHERE id=?",(p["cacamba_id"],))
    conn.execute("UPDATE pedidos SET status='finalizado', motorista_retirada=?, data_fim_real=? WHERE id=?",(motorista_retirada, data_retirada, pid,))
    conn.commit(); conn.close()
    flash("Retirada registrada. Caçamba liberada.","ok")
    return redirect(request.referrer or url_for("pedidos"))


@app.route("/pedidos/<int:pid>/cancelar", methods=["POST"])
def pedido_cancelar(pid):
    conn = get_conn()
    p = conn.execute("SELECT id,status,cacamba_id FROM pedidos WHERE id=?",(pid,)).fetchone()
    if not p or p["status"] not in ("confirmado","no_endereco"):
        conn.close(); flash("Não é possível cancelar este pedido.","erro"); return redirect(url_for("pedidos"))
    if p["cacamba_id"]:
        conn.execute("UPDATE cacambas SET status='disponivel' WHERE id=?",(p["cacamba_id"],))
    fim_real = date.today().isoformat()
    conn.execute("UPDATE pedidos SET status='cancelado', data_fim_real=? WHERE id=?",(fim_real, pid,))
    conn.commit(); conn.close()
    flash("Pedido cancelado. Caçamba liberada.","ok")
    return redirect(request.referrer or url_for("pedidos"))


@app.route("/pedidos/<int:pid>/pagar", methods=["POST"])
def pedido_pagar(pid):
    conn = get_conn()
    conn.execute("UPDATE pedidos SET pago=1 WHERE id=?",(pid,))
    conn.commit(); conn.close()
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
    conn.commit(); conn.close()
    flash("Observação salva.","ok")
    return redirect(request.referrer or url_for("pedidos"))


# ╔══════════════════════════════════════════════════════════╗
# ║  OPERAÇÕES (entregas + retiradas do dia)                 ║
# ╚══════════════════════════════════════════════════════════╝

@app.route("/operacoes")
def operacoes():
    conn = get_conn()
    hoje_iso = date.today().isoformat()
    # Entregas pendentes: confirmados esperando motorista
    entregas = conn.execute(
        _PEDIDO_SELECT +
        " WHERE p.status='confirmado' ORDER BY p.id"
    ).fetchall()
    # Caçambas no endereço
    no_end = conn.execute(
        _PEDIDO_SELECT +
        " WHERE p.status='no_endereco' ORDER BY p.data_fim_prevista"
    ).fetchall()
    # Vencidas (prazo ultrapassado)
    vencidas = [p for p in no_end if p["data_fim_prevista"] and p["data_fim_prevista"] < hoje_iso]
    # Retiradas hoje
    retiradas_hoje = [p for p in no_end if p["data_fim_prevista"] == hoje_iso]
    conn.close()
    return render_template("operacoes.html",
        entregas=entregas, no_end=no_end, vencidas=vencidas,
        retiradas_hoje=retiradas_hoje,
        motoristas=_get_motoristas(), hoje=hoje_iso)


# ╔══════════════════════════════════════════════════════════╗
# ║  FINANCEIRO                                              ║
# ╚══════════════════════════════════════════════════════════╝

@app.route("/financeiro")
def financeiro():
    f = request.args.get("f","")   # pago / pendente / todos
    q = request.args.get("q","").strip()
    conn = get_conn()

    where, params = [], []
    where.append("p.status != 'pendente'")   # só os que já foram confirmados
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


# ╔══════════════════════════════════════════════════════════╗
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


# ╔══════════════════════════════════════════════════════════╗
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
        conn.commit(); conn.close()
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
    conn.commit(); conn.close()
    flash("Motorista removido.","ok")
    return redirect(url_for("configuracoes"))


# ╔══════════════════════════════════════════════════════════╗
# ║  RESET                                                   ║
# ╚══════════════════════════════════════════════════════════╝

@app.route("/reset", methods=["GET","POST"])
def reset_banco():
    if request.method == "POST":
        conn = get_conn()
        for t in ("pedidos","enderecos_cliente","clientes","cacambas","motoristas","config"):
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.commit(); conn.close()
        init_db(); seed_if_empty()
        flash("Banco resetado.","ok")
        return redirect(url_for("index"))
    return render_template("reset.html")


# ╔══════════════════════════════════════════════════════════╗
# ║  APIs JSON                                               ║
# ╚══════════════════════════════════════════════════════════╝

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


@app.route("/api/stats")
def api_stats():
    return Response(json.dumps(_stats()), mimetype="application/json")


if __name__ == "__main__":
    app.run()
