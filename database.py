# -*- coding: utf-8 -*-
import os
import sqlite3
import unicodedata
from pathlib import Path

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).resolve().parent / "cacambas.db"))
DEFAULT_MOTORISTAS = ("Roberto", "Cicero")
DEFAULT_CONFIG = (
    ("empresa_nome", "Caçambas Bauru"),
    ("empresa_fone", ""),
    ("dias_locacao", "7"),
    ("valor_locacao_3m3", "300"),
    ("valor_locacao_4m3", "330"),
)
CAPACIDADES_M3 = (3, 4)
VALORES_LOCACAO = {3: 300.00, 4: 330.00}  # Valores por m³

def _norm(s):
    """Remove acentos e converte para minúsculas — usado na função SQL NORM()."""
    if s is None:
        return ""
    return unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode("ascii").lower()

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.create_function("NORM", 1, _norm)
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
            valor_total REAL NOT NULL DEFAULT 0,
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
        CREATE TABLE IF NOT EXISTS historico_pedidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id INTEGER NOT NULL,
            acao TEXT NOT NULL,
            detalhes TEXT NOT NULL DEFAULT '',
            usuario TEXT NOT NULL DEFAULT 'sistema',
            created_at TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (pedido_id) REFERENCES pedidos(id)
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
    
    # Create indexes for performance
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_pedidos_cliente ON pedidos(cliente_id);
        CREATE INDEX IF NOT EXISTS idx_pedidos_cacamba ON pedidos(cacamba_id);
        CREATE INDEX IF NOT EXISTS idx_pedidos_status ON pedidos(status);
        CREATE INDEX IF NOT EXISTS idx_pedidos_pago ON pedidos(pago);
        CREATE INDEX IF NOT EXISTS idx_pedidos_status_pago ON pedidos(status,pago);
        CREATE INDEX IF NOT EXISTS idx_pedidos_fim_prevista ON pedidos(data_fim_prevista);
        CREATE INDEX IF NOT EXISTS idx_pedidos_inicio ON pedidos(data_inicio);
        CREATE INDEX IF NOT EXISTS idx_pedidos_criado ON pedidos(criado_em);
        CREATE INDEX IF NOT EXISTS idx_clientes_nome ON clientes(nome);
        CREATE INDEX IF NOT EXISTS idx_clientes_telefone ON clientes(telefone);
        CREATE INDEX IF NOT EXISTS idx_enderecos_cliente ON enderecos_cliente(cliente_id);
        CREATE INDEX IF NOT EXISTS idx_enderecos_busca ON enderecos_cliente(rua,bairro);
        CREATE INDEX IF NOT EXISTS idx_historico_pedido ON historico_pedidos(pedido_id);
    """)
    
    _migrate(conn)
    conn.commit()
    conn.close()

def _migrate(conn):
    # clientes
    cols = {r[1] for r in conn.execute("PRAGMA table_info(clientes)").fetchall()}
    for col, dfn in [("tipo_pessoa","TEXT NOT NULL DEFAULT 'pf'"),
                     ("cpf","TEXT NOT NULL DEFAULT ''"),
                     ("cnpj","TEXT NOT NULL DEFAULT ''"),
                     ("razao_social","TEXT NOT NULL DEFAULT ''"),
                     ("telefone","TEXT NOT NULL DEFAULT ''"),
                     ("email","TEXT NOT NULL DEFAULT ''"),
                     ("endereco","TEXT NOT NULL DEFAULT ''"),
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
    if "apelido" not in ecols:
        conn.execute("ALTER TABLE enderecos_cliente ADD COLUMN apelido TEXT NOT NULL DEFAULT ''")
    
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
                     ("data_fim_real","TEXT NOT NULL DEFAULT ''"),
                     ("valor_total","REAL NOT NULL DEFAULT 0")]:
        if col not in pcols:
            conn.execute(f"ALTER TABLE pedidos ADD COLUMN {col} {dfn}")
    
    # motoristas e config
    conn.execute("CREATE TABLE IF NOT EXISTS motoristas (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE)")
    conn.execute("CREATE TABLE IF NOT EXISTS config (chave TEXT PRIMARY KEY, valor TEXT NOT NULL DEFAULT '')")
    
    conn.execute(
        """UPDATE pedidos SET valor_total = CASE capacidade_m3
               WHEN 3 THEN ? WHEN 4 THEN ? ELSE valor_total END
           WHERE valor_total IS NULL OR valor_total = 0""",
        (VALORES_LOCACAO[3], VALORES_LOCACAO[4]),
    )
    if conn.execute("SELECT COUNT(*) FROM motoristas").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO motoristas (nome) VALUES (?)",
            [(n,) for n in DEFAULT_MOTORISTAS],
        )
    if conn.execute("SELECT COUNT(*) FROM config").fetchone()[0] == 0:
        for chave, valor in DEFAULT_CONFIG:
            conn.execute(
                "INSERT OR REPLACE INTO config (chave, valor) VALUES (?, ?)",
                (chave, valor),
            )
    else:
        for chave, valor in DEFAULT_CONFIG:
            if chave.startswith("valor_locacao_"):
                if not conn.execute("SELECT 1 FROM config WHERE chave=?", (chave,)).fetchone():
                    conn.execute(
                        "INSERT INTO config (chave, valor) VALUES (?, ?)",
                        (chave, valor),
                    )

    # historico_pedidos
    conn.execute("""CREATE TABLE IF NOT EXISTS historico_pedidos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pedido_id INTEGER NOT NULL,
        acao TEXT NOT NULL,
        detalhes TEXT NOT NULL DEFAULT '',
        usuario TEXT NOT NULL DEFAULT 'sistema',
        created_at TEXT NOT NULL DEFAULT '',
        FOREIGN KEY (pedido_id) REFERENCES pedidos(id)
    )""")

def seed_if_empty():
    conn = get_conn()
    if conn.execute("SELECT COUNT(*) FROM cacambas").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO cacambas (codigo,capacidade_m3,status) VALUES (?,?,'disponivel')",
            [("1", 3), ("2", 4), ("3", 3), ("4", 4), ("5", 3)],
        )
    if conn.execute("SELECT COUNT(*) FROM motoristas").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO motoristas (nome) VALUES (?)",
            [(n,) for n in DEFAULT_MOTORISTAS],
        )
    if conn.execute("SELECT COUNT(*) FROM config").fetchone()[0] == 0:
        for chave, valor in DEFAULT_CONFIG:
            conn.execute(
                "INSERT OR REPLACE INTO config (chave, valor) VALUES (?, ?)",
                (chave, valor),
            )
    conn.commit()
    conn.close()
