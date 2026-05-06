# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "cacambas.db"
CAPACIDADES_M3 = (3, 4)

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo_pessoa TEXT NOT NULL DEFAULT 'pf',
            nome TEXT NOT NULL,
            cpf TEXT NOT NULL DEFAULT '',
            cnpj TEXT NOT NULL DEFAULT '',
            razao_social TEXT NOT NULL DEFAULT '',
            telefone TEXT NOT NULL,
            email TEXT DEFAULT '',
            endereco TEXT DEFAULT '',
            cep TEXT NOT NULL DEFAULT '',
            rua TEXT NOT NULL DEFAULT '',
            numero TEXT NOT NULL DEFAULT '',
            complemento TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS enderecos_cliente (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER NOT NULL,
            cep TEXT NOT NULL DEFAULT '',
            rua TEXT NOT NULL DEFAULT '',
            quadra TEXT NOT NULL DEFAULT '',
            numero TEXT NOT NULL DEFAULT '',
            bairro TEXT NOT NULL DEFAULT '',
            complemento TEXT NOT NULL DEFAULT '',
            apelido TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (cliente_id) REFERENCES clientes(id)
        );
        CREATE TABLE IF NOT EXISTS cacambas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT NOT NULL UNIQUE,
            capacidade_m3 INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'disponivel'
        );
        CREATE TABLE IF NOT EXISTS pedidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER NOT NULL,
            cacamba_id INTEGER,
            capacidade_m3 INTEGER NOT NULL,
            motorista_entrega TEXT NOT NULL DEFAULT '',
            motorista_retirada TEXT NOT NULL DEFAULT '',
            endereco_obra TEXT NOT NULL DEFAULT '',
            obra_cep TEXT NOT NULL DEFAULT '',
            obra_rua TEXT NOT NULL DEFAULT '',
            obra_quadra TEXT NOT NULL DEFAULT '',
            obra_numero TEXT NOT NULL DEFAULT '',
            obra_bairro TEXT NOT NULL DEFAULT '',
            data_inicio TEXT NOT NULL DEFAULT '',
            data_fim_prevista TEXT NOT NULL DEFAULT '',
            data_fim_real TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pendente',
            pago INTEGER NOT NULL DEFAULT 0,
            criado_em TEXT NOT NULL DEFAULT '',
            latitude REAL,
            longitude REAL,
            observacoes TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (cliente_id) REFERENCES clientes(id),
            FOREIGN KEY (cacamba_id) REFERENCES cacambas(id)
        );
        CREATE TABLE IF NOT EXISTS motoristas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS config (
            chave TEXT PRIMARY KEY,
            valor TEXT NOT NULL DEFAULT ''
        );
    """)
    _migrate(conn)
    conn.commit()
    conn.close()

def _migrate(conn):
    # clientes
    cols = {r[1] for r in conn.execute("PRAGMA table_info(clientes)").fetchall()}
    for col, dfn in [("tipo_pessoa","TEXT NOT NULL DEFAULT 'pf'"),
                     ("cnpj","TEXT NOT NULL DEFAULT ''"),
                     ("razao_social","TEXT NOT NULL DEFAULT ''"),
                     ("cep","TEXT NOT NULL DEFAULT ''"),
                     ("rua","TEXT NOT NULL DEFAULT ''"),
                     ("numero","TEXT NOT NULL DEFAULT ''"),
                     ("complemento","TEXT NOT NULL DEFAULT ''")]:
        if col not in cols:
            conn.execute(f"ALTER TABLE clientes ADD COLUMN {col} {dfn}")

    # enderecos_cliente
    conn.execute("""CREATE TABLE IF NOT EXISTS enderecos_cliente (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cliente_id INTEGER NOT NULL,
        cep TEXT NOT NULL DEFAULT '',
        rua TEXT NOT NULL DEFAULT '',
        quadra TEXT NOT NULL DEFAULT '',
        numero TEXT NOT NULL DEFAULT '',
        bairro TEXT NOT NULL DEFAULT '',
        complemento TEXT NOT NULL DEFAULT '',
        apelido TEXT NOT NULL DEFAULT '',
        FOREIGN KEY (cliente_id) REFERENCES clientes(id)
    )""")
    ecols = {r[1] for r in conn.execute("PRAGMA table_info(enderecos_cliente)").fetchall()}
    if "quadra" not in ecols:
        conn.execute("ALTER TABLE enderecos_cliente ADD COLUMN quadra TEXT NOT NULL DEFAULT ''")

    # pedidos
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(pedidos)").fetchall()}
    for col, dfn in [("latitude","REAL"),("longitude","REAL"),
                     ("obra_cep","TEXT NOT NULL DEFAULT ''"),
                     ("obra_rua","TEXT NOT NULL DEFAULT ''"),
                     ("obra_quadra","TEXT NOT NULL DEFAULT ''"),
                     ("obra_numero","TEXT NOT NULL DEFAULT ''"),
                     ("obra_bairro","TEXT NOT NULL DEFAULT ''"),
                     ("motorista_entrega","TEXT NOT NULL DEFAULT ''"),
                     ("motorista_retirada","TEXT NOT NULL DEFAULT ''"),
                     ("pago","INTEGER NOT NULL DEFAULT 0"),
                     ("observacoes","TEXT NOT NULL DEFAULT ''"),
                     ("data_fim_real","TEXT NOT NULL DEFAULT ''")]:
        if col not in pcols:
            conn.execute(f"ALTER TABLE pedidos ADD COLUMN {col} {dfn}")

    # motoristas e config
    conn.execute("CREATE TABLE IF NOT EXISTS motoristas (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE)")
    conn.execute("CREATE TABLE IF NOT EXISTS config (chave TEXT PRIMARY KEY, valor TEXT NOT NULL DEFAULT '')")

    # config defaults
    for k, v in [("dias_locacao","7"),("empresa_nome","Caçambas Bauru"),("empresa_fone","")]:
        conn.execute("INSERT OR IGNORE INTO config (chave,valor) VALUES (?,?)",(k,v))

    # seed motoristas
    for m in ("Roberto","Cicero"):
        conn.execute("INSERT OR IGNORE INTO motoristas (nome) VALUES (?)",(m,))

def seed_if_empty():
    conn = get_conn()
    if conn.execute("SELECT COUNT(*) FROM cacambas").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO cacambas (codigo,capacidade_m3,status) VALUES (?,?,'disponivel')",
            [("1",3),("2",4),("3",3),("4",4),("5",3)]
        )
        conn.commit()
    conn.close()
