import io
import os
import json
import time
import uuid
import shutil
import zipfile
import secrets
import calendar
import sqlite3
import tempfile
import threading
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask import (Flask, request, jsonify, send_from_directory, session, redirect,
                   send_file, has_request_context)

DB_PATH = os.environ.get("DB_PATH", "/data/orcamento.db")
COMPROVANTES_DIR = os.environ.get("COMPROVANTES_DIR", "/data/comprovantes")
FOTOS_DIR = os.environ.get("FOTOS_DIR", "/data/fotos")
BACKUPS_DIR = os.environ.get("BACKUPS_DIR", "/data/backups")
DEMO_DB_PATH = os.environ.get("DEMO_DB_PATH", "/data/orcamento-demo.db")

EXTENSOES_IMAGEM = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
TAMANHO_MAX_UPLOAD = 8 * 1024 * 1024  # 8 MB

app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = TAMANHO_MAX_UPLOAD


def obter_secret_key():
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    caminho = os.path.join(os.path.dirname(DB_PATH), ".secret_key")
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    if os.path.exists(caminho):
        with open(caminho) as f:
            chave = f.read().strip()
            if chave:
                return chave
    nova_chave = secrets.token_hex(32)
    with open(caminho, "w") as f:
        f.write(nova_chave)
    return nova_chave


app.secret_key = obter_secret_key()

ROTAS_PUBLICAS = {"/login", "/api/login", "/manifest.json", "/sw.js"}


@app.before_request
def exigir_login():
    if request.path.startswith("/static/") or request.path in ROTAS_PUBLICAS:
        return None
    if request.path == "/":
        if not session.get("usuario_id"):
            return redirect("/login")
        return None
    if request.path.startswith("/api/"):
        if not session.get("usuario_id"):
            return jsonify({"erro": "não autenticado"}), 401
        return None
    return None


def em_demo():
    """Modo demonstração: usa um banco separado com dados fictícios,
    então os dados reais ficam intocados enquanto ele está ligado.
    Fora de uma requisição (ex: init_db na subida do app) não existe sessão,
    e aí o banco correto é sempre o real."""
    if not has_request_context():
        return False
    return bool(session.get("demo"))


def caminho_banco_atual():
    return DEMO_DB_PATH if em_demo() else DB_PATH


def uid():
    """ID do usuário logado. Cada usuário só enxerga os próprios dados."""
    return 1 if em_demo() else session["usuario_id"]


def pertence_ao_usuario(conn, tabela, item_id):
    row = conn.execute(f"SELECT usuario_id FROM {tabela} WHERE id = ?", (item_id,)).fetchone()
    return row is not None and row["usuario_id"] == uid()


CATEGORIAS_RECEITA_PADRAO = ["Salário", "Vale/Benefícios", "Freelance", "Pix recebido",
                              "Venda de produtos", "Rendimentos", "Outros"]
CATEGORIAS_DESPESA_PADRAO = ["Mercado", "Combustível", "Aluguel", "Energia", "Água",
                              "Internet", "Alimentação", "Lazer", "Compras", "Transporte", "Outros"]


def get_db():
    conn = sqlite3.connect(caminho_banco_atual())
    conn.row_factory = sqlite3.Row
    return conn


def add_col_if_missing(conn, tabela, coluna, ddl):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({tabela})").fetchall()]
    if coluna not in cols:
        conn.execute(ddl)


def init_db(caminho=None, criar_usuario_inicial=True):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(COMPROVANTES_DIR, exist_ok=True)
    os.makedirs(FOTOS_DIR, exist_ok=True)
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    if caminho:
        conn = sqlite3.connect(caminho)
        conn.row_factory = sqlite3.Row
    else:
        conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lancamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mes TEXT NOT NULL,
            tipo TEXT NOT NULL,               -- renda | despesa
            descricao TEXT NOT NULL,
            valor REAL NOT NULL,
            vencimento TEXT,
            categoria TEXT,
            conta TEXT,
            recorrente INTEGER DEFAULT 0,
            grupo_parcela TEXT,
            parcela_num INTEGER,
            parcela_total INTEGER,
            pago INTEGER DEFAULT 0,
            data_pagamento TEXT,
            comprovante TEXT,
            observacao TEXT,
            criado_em TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cartoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            limite REAL DEFAULT 0,
            fatura_atual REAL DEFAULT 0,
            dia_vencimento INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            tipo TEXT NOT NULL,   -- receita | despesa
            UNIQUE(nome, tipo)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            saldo_inicial REAL DEFAULT 0,
            criado_em TEXT,
            usuario_id INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            username TEXT NOT NULL UNIQUE,
            senha_hash TEXT NOT NULL,
            criado_em TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recorrencias_puladas (
            grupo_recorrencia TEXT NOT NULL,
            mes TEXT NOT NULL,
            PRIMARY KEY (grupo_recorrencia, mes)
        )
        """
    )
    conn.commit()
    ja_tem_categorias = conn.execute("SELECT COUNT(*) as n FROM categorias").fetchone()["n"]
    if not ja_tem_categorias:
        for nome in CATEGORIAS_RECEITA_PADRAO:
            conn.execute("INSERT OR IGNORE INTO categorias (nome, tipo) VALUES (?, 'receita')", (nome,))
        for nome in CATEGORIAS_DESPESA_PADRAO:
            conn.execute("INSERT OR IGNORE INTO categorias (nome, tipo) VALUES (?, 'despesa')", (nome,))
        conn.commit()
    for coluna, ddl in [
        ("categoria", "ALTER TABLE lancamentos ADD COLUMN categoria TEXT"),
        ("conta", "ALTER TABLE lancamentos ADD COLUMN conta TEXT"),
        ("grupo_parcela", "ALTER TABLE lancamentos ADD COLUMN grupo_parcela TEXT"),
        ("parcela_num", "ALTER TABLE lancamentos ADD COLUMN parcela_num INTEGER"),
        ("parcela_total", "ALTER TABLE lancamentos ADD COLUMN parcela_total INTEGER"),
        ("pago", "ALTER TABLE lancamentos ADD COLUMN pago INTEGER DEFAULT 0"),
        ("data_pagamento", "ALTER TABLE lancamentos ADD COLUMN data_pagamento TEXT"),
        ("comprovante", "ALTER TABLE lancamentos ADD COLUMN comprovante TEXT"),
        ("observacao", "ALTER TABLE lancamentos ADD COLUMN observacao TEXT"),
        ("conta_id", "ALTER TABLE lancamentos ADD COLUMN conta_id INTEGER"),
        ("eh_transferencia", "ALTER TABLE lancamentos ADD COLUMN eh_transferencia INTEGER DEFAULT 0"),
        ("grupo_transferencia", "ALTER TABLE lancamentos ADD COLUMN grupo_transferencia TEXT"),
        ("grupo_recorrencia", "ALTER TABLE lancamentos ADD COLUMN grupo_recorrencia TEXT"),
        # Último mês da recorrência (YYYY-MM). NULL = repete para sempre.
        ("recorrencia_ate", "ALTER TABLE lancamentos ADD COLUMN recorrencia_ate TEXT"),
    ]:
        add_col_if_missing(conn, "lancamentos", coluna, ddl)
    for coluna, ddl in [
        ("conta_id", "ALTER TABLE cartoes ADD COLUMN conta_id INTEGER"),
        ("fatura_paga", "ALTER TABLE cartoes ADD COLUMN fatura_paga INTEGER DEFAULT 0"),
    ]:
        add_col_if_missing(conn, "cartoes", coluna, ddl)
    add_col_if_missing(conn, "categorias", "cor", "ALTER TABLE categorias ADD COLUMN cor TEXT")
    add_col_if_missing(conn, "usuarios", "foto", "ALTER TABLE usuarios ADD COLUMN foto TEXT")
    # Cada usuário tem seus próprios lançamentos, contas, cartões e dívidas.
    # Categorias, de propósito, continuam compartilhadas entre todos.
    add_col_if_missing(conn, "contas", "usuario_id", "ALTER TABLE contas ADD COLUMN usuario_id INTEGER")
    add_col_if_missing(conn, "lancamentos", "usuario_id", "ALTER TABLE lancamentos ADD COLUMN usuario_id INTEGER")
    add_col_if_missing(conn, "cartoes", "usuario_id", "ALTER TABLE cartoes ADD COLUMN usuario_id INTEGER")
    conn.commit()
    migrar_contas_de_texto_livre(conn)
    if criar_usuario_inicial:
        bootstrap_usuario_inicial(conn)
    migrar_contas_nome_unico_por_usuario(conn)
    migrar_series_de_recorrencia(conn)
    conn.close()


def migrar_series_de_recorrencia(conn):
    """Lançamentos recorrentes que já existiam não tinham identificador de série.
    Agrupa os antigos por (usuário, tipo, descrição) para que as ocorrências do
    mesmo item em meses diferentes sejam reconhecidas como uma série só."""
    grupos = conn.execute(
        "SELECT DISTINCT usuario_id, tipo, descricao FROM lancamentos "
        "WHERE recorrente = 1 AND grupo_recorrencia IS NULL"
    ).fetchall()
    for g in grupos:
        conn.execute(
            "UPDATE lancamentos SET grupo_recorrencia = ? "
            "WHERE recorrente = 1 AND grupo_recorrencia IS NULL "
            "AND usuario_id IS ? AND tipo = ? AND descricao = ?",
            (str(uuid.uuid4()), g["usuario_id"], g["tipo"], g["descricao"]),
        )
    conn.commit()


def migrar_contas_nome_unico_por_usuario(conn):
    """Antes do multiusuário o nome da conta era único globalmente, o que impediria
    dois usuários de terem, por exemplo, um 'Nubank' cada. Recria a tabela sem esse
    UNIQUE global e passa a exigir nome único apenas dentro de cada usuário."""
    schema = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='contas'"
    ).fetchone()
    precisa_recriar = schema and "UNIQUE" in schema["sql"].upper()

    if precisa_recriar:
        colunas = [r["name"] for r in conn.execute("PRAGMA table_info(contas)").fetchall()]
        tem_usuario = "usuario_id" in colunas
        conn.execute("BEGIN")
        try:
            conn.execute(
                """CREATE TABLE contas_nova (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT NOT NULL,
                    saldo_inicial REAL DEFAULT 0,
                    criado_em TEXT,
                    usuario_id INTEGER
                )"""
            )
            if tem_usuario:
                conn.execute(
                    "INSERT INTO contas_nova (id, nome, saldo_inicial, criado_em, usuario_id) "
                    "SELECT id, nome, saldo_inicial, criado_em, usuario_id FROM contas"
                )
            else:
                conn.execute(
                    "INSERT INTO contas_nova (id, nome, saldo_inicial, criado_em) "
                    "SELECT id, nome, saldo_inicial, criado_em FROM contas"
                )
            conn.execute("DROP TABLE contas")
            conn.execute("ALTER TABLE contas_nova RENAME TO contas")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_contas_nome_usuario ON contas (nome, usuario_id)"
    )
    conn.commit()


def bootstrap_usuario_inicial(conn):
    """Cria o primeiro usuário no primeiro start, e garante que todo dado tenha um dono."""
    ja_tem_usuarios = conn.execute("SELECT COUNT(*) as n FROM usuarios").fetchone()["n"]
    if not ja_tem_usuarios:
        admin_user = os.environ.get("ADMIN_USERNAME", "admin")
        admin_pass = os.environ.get("ADMIN_PASSWORD")
        senha_foi_gerada = not admin_pass
        if not admin_pass:
            admin_pass = secrets.token_urlsafe(9)
        conn.execute(
            "INSERT INTO usuarios (nome, username, senha_hash, criado_em) VALUES (?, ?, ?, ?)",
            ("Administrador", admin_user, generate_password_hash(admin_pass), datetime.now().isoformat()),
        )
        conn.commit()
        if senha_foi_gerada:
            print("=" * 64)
            print(f"USUÁRIO INICIAL CRIADO — usuário: {admin_user}")
            print(f"SENHA GERADA AUTOMATICAMENTE: {admin_pass}")
            print("Troque essa senha assim que fizer o primeiro login.")
            print("(Defina ADMIN_USERNAME e ADMIN_PASSWORD no docker-compose.yml")
            print(" se preferir escolher suas próprias credenciais.)")
            print("=" * 64, flush=True)
    primeiro_usuario = conn.execute("SELECT id FROM usuarios ORDER BY id LIMIT 1").fetchone()
    if primeiro_usuario:
        # Dados que já existiam antes do multiusuário pertencem ao primeiro usuário.
        uid = primeiro_usuario["id"]
        conn.execute("UPDATE contas SET usuario_id = ? WHERE usuario_id IS NULL", (uid,))
        conn.execute("UPDATE cartoes SET usuario_id = ? WHERE usuario_id IS NULL", (uid,))
        # Lançamentos herdam o dono da conta vinculada; os sem conta ficam com o primeiro usuário.
        conn.execute(
            "UPDATE lancamentos SET usuario_id = ("
            "  SELECT contas.usuario_id FROM contas WHERE contas.id = lancamentos.conta_id"
            ") WHERE usuario_id IS NULL AND conta_id IS NOT NULL"
        )
        conn.execute("UPDATE lancamentos SET usuario_id = ? WHERE usuario_id IS NULL", (uid,))
        conn.commit()


def migrar_contas_de_texto_livre(conn):
    """Cria contas de verdade a partir dos textos livres já usados em lancamentos.conta,
    e vincula os lançamentos existentes a elas, sem apagar nada."""
    nomes = conn.execute(
        "SELECT DISTINCT conta FROM lancamentos WHERE conta IS NOT NULL AND TRIM(conta) != '' AND conta_id IS NULL"
    ).fetchall()
    for row in nomes:
        nome = row["conta"].strip()
        conn.execute(
            "INSERT OR IGNORE INTO contas (nome, saldo_inicial, criado_em) VALUES (?, 0, ?)",
            (nome, datetime.now().isoformat()),
        )
        conta = conn.execute("SELECT id FROM contas WHERE nome = ?", (nome,)).fetchone()
        if conta:
            conn.execute(
                "UPDATE lancamentos SET conta_id = ? WHERE conta = ? AND conta_id IS NULL",
                (conta["id"], row["conta"]),
            )
    conn.commit()


def mes_anterior(mes):
    ano, m = map(int, mes.split("-"))
    if m == 1:
        return f"{ano-1}-12"
    return f"{ano}-{m-1:02d}"


def mes_seguinte(mes):
    ano, m = map(int, mes.split("-"))
    if m == 12:
        return f"{ano+1}-01"
    return f"{ano}-{m+1:02d}"


def somar_meses(mes, n):
    for _ in range(n):
        mes = mes_seguinte(mes)
    return mes


def vencimento_no_mes(vencimento_original, mes_destino):
    """Mantém o dia do vencimento no novo mês, ajustando quando o dia não existe
    (ex: dia 31 em fevereiro vira o último dia do mês)."""
    if not vencimento_original:
        return ""
    try:
        dia = int(vencimento_original.split("-")[2])
    except (IndexError, ValueError):
        return ""
    ano, m = map(int, mes_destino.split("-"))
    ultimo_dia = calendar.monthrange(ano, m)[1]
    return f"{ano}-{m:02d}-{min(dia, ultimo_dia):02d}"


def garantir_recorrentes(conn, mes, usuario_id):
    """Materializa no mês pedido os lançamentos recorrentes que ainda não estão lá.
    Roda sempre que o mês é aberto, então não é preciso 'importar' nada na mão.
    Usa o mês em que a série começou (e não o último) para que meses no meio também
    sejam preenchidos caso o usuário pule para frente e depois volte."""
    series = conn.execute(
        "SELECT grupo_recorrencia, MIN(mes) as primeiro_mes FROM lancamentos "
        "WHERE usuario_id = ? AND recorrente = 1 AND grupo_recorrencia IS NOT NULL "
        "GROUP BY grupo_recorrencia HAVING primeiro_mes < ?",
        (usuario_id, mes),
    ).fetchall()

    criados = 0
    for s in series:
        grupo = s["grupo_recorrencia"]
        ja_existe = conn.execute(
            "SELECT 1 FROM lancamentos WHERE grupo_recorrencia = ? AND mes = ?", (grupo, mes)
        ).fetchone()
        if ja_existe:
            continue
        # Respeita exclusões pontuais ("apagar só neste mês").
        pulado = conn.execute(
            "SELECT 1 FROM recorrencias_puladas WHERE grupo_recorrencia = ? AND mes = ?",
            (grupo, mes),
        ).fetchone()
        if pulado:
            continue
        # Copia da ocorrência mais recente anterior a este mês (pega alterações de valor).
        modelo = conn.execute(
            "SELECT * FROM lancamentos WHERE grupo_recorrencia = ? AND mes < ? "
            "ORDER BY mes DESC LIMIT 1",
            (grupo, mes),
        ).fetchone()
        if not modelo:
            continue
        # Recorrência com prazo (ex: dívida em 6x) para de gerar depois do último mês.
        if modelo["recorrencia_ate"] and mes > modelo["recorrencia_ate"]:
            continue
        conn.execute(
            """INSERT INTO lancamentos
               (mes, tipo, descricao, valor, vencimento, categoria, conta, conta_id, recorrente,
                pago, data_pagamento, observacao, criado_em, usuario_id, grupo_recorrencia,
                recorrencia_ate)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, '', ?, ?, ?, ?, ?)""",
            (mes, modelo["tipo"], modelo["descricao"], modelo["valor"],
             vencimento_no_mes(modelo["vencimento"], mes), modelo["categoria"],
             modelo["conta"], modelo["conta_id"], modelo["observacao"],
             datetime.now().isoformat(), usuario_id, grupo, modelo["recorrencia_ate"]),
        )
        criados += 1

    if criados:
        conn.commit()
    return criados


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/login")
def login_page():
    if session.get("usuario_id"):
        return redirect("/")
    return send_from_directory("static", "login.html")


@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json")


@app.route("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")


# ---------------- Autenticação ----------------

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip().lower()
    senha = data.get("senha") or ""
    conn = get_db()
    row = conn.execute("SELECT * FROM usuarios WHERE username = ?", (username,)).fetchone()
    conn.close()
    if not row or not check_password_hash(row["senha_hash"], senha):
        return jsonify({"erro": "usuário ou senha inválidos"}), 401
    session.clear()
    session["usuario_id"] = row["id"]
    session["usuario_nome"] = row["nome"]
    session.permanent = True
    return jsonify({"ok": True, "usuario": {"id": row["id"], "nome": row["nome"], "username": row["username"]}})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/usuario-atual", methods=["GET"])
def usuario_atual():
    conn = get_db()
    row = conn.execute("SELECT id, nome, username, foto FROM usuarios WHERE id = ?", (uid(),)).fetchone()
    conn.close()
    if not row:
        session.clear()
        return jsonify({"erro": "não autenticado"}), 401
    return jsonify(dict(row))


@app.route("/api/usuarios", methods=["GET"])
def listar_usuarios():
    conn = get_db()
    rows = conn.execute("SELECT id, nome, username, criado_em, foto FROM usuarios ORDER BY nome").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/usuarios", methods=["POST"])
def criar_usuario():
    data = request.get_json(force=True)
    nome = data.get("nome", "").strip()
    username = (data.get("username") or "").strip().lower()
    senha = data.get("senha") or ""
    if not nome or not username or len(senha) < 4:
        return jsonify({"erro": "nome, usuário e senha (mín. 4 caracteres) são obrigatórios"}), 400
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO usuarios (nome, username, senha_hash, criado_em) VALUES (?, ?, ?, ?)",
            (nome, username, generate_password_hash(senha), datetime.now().isoformat()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"erro": "esse usuário já existe"}), 400
    conn.close()
    return jsonify({"ok": True}), 201


@app.route("/api/usuarios/<int:item_id>/senha", methods=["PUT"])
def trocar_senha(item_id):
    if item_id != session.get("usuario_id"):
        return jsonify({"erro": "só é possível trocar a própria senha"}), 403
    data = request.get_json(force=True)
    senha_atual = data.get("senha_atual") or ""
    nova_senha = data.get("nova_senha") or ""
    if len(nova_senha) < 4:
        return jsonify({"erro": "a nova senha precisa ter pelo menos 4 caracteres"}), 400
    conn = get_db()
    row = conn.execute("SELECT * FROM usuarios WHERE id = ?", (item_id,)).fetchone()
    if not row or not check_password_hash(row["senha_hash"], senha_atual):
        conn.close()
        return jsonify({"erro": "senha atual incorreta"}), 400
    conn.execute("UPDATE usuarios SET senha_hash = ? WHERE id = ?", (generate_password_hash(nova_senha), item_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


def remover_foto_do_disco(nome_arquivo):
    if not nome_arquivo:
        return
    caminho = os.path.join(FOTOS_DIR, nome_arquivo)
    if os.path.exists(caminho):
        os.remove(caminho)


@app.route("/api/usuarios/<int:item_id>/foto", methods=["POST"])
def enviar_foto_perfil(item_id):
    if item_id != session.get("usuario_id"):
        return jsonify({"erro": "só é possível trocar a própria foto"}), 403
    if "arquivo" not in request.files:
        return jsonify({"erro": "nenhum arquivo enviado"}), 400
    arquivo = request.files["arquivo"]
    if arquivo.filename == "":
        return jsonify({"erro": "arquivo sem nome"}), 400

    extensao = os.path.splitext(arquivo.filename)[1].lower()
    if extensao not in EXTENSOES_IMAGEM:
        return jsonify({"erro": "envie uma imagem (jpg, png, gif ou webp)"}), 400

    os.makedirs(FOTOS_DIR, exist_ok=True)
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    nome_final = f"u{item_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}{extensao}"
    arquivo.save(os.path.join(FOTOS_DIR, nome_final))

    conn = get_db()
    antiga = conn.execute("SELECT foto FROM usuarios WHERE id = ?", (item_id,)).fetchone()
    if antiga:
        remover_foto_do_disco(antiga["foto"])
    conn.execute("UPDATE usuarios SET foto = ? WHERE id = ?", (nome_final, item_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "foto": nome_final})


@app.route("/api/usuarios/<int:item_id>/foto", methods=["DELETE"])
def remover_foto_perfil(item_id):
    if item_id != session.get("usuario_id"):
        return jsonify({"erro": "só é possível remover a própria foto"}), 403
    conn = get_db()
    row = conn.execute("SELECT foto FROM usuarios WHERE id = ?", (item_id,)).fetchone()
    if row:
        remover_foto_do_disco(row["foto"])
    conn.execute("UPDATE usuarios SET foto = NULL WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/usuario-foto/<path:nome_arquivo>", methods=["GET"])
def baixar_foto_perfil(nome_arquivo):
    conn = get_db()
    existe = conn.execute("SELECT id FROM usuarios WHERE foto = ?", (nome_arquivo,)).fetchone()
    conn.close()
    if not existe:
        return jsonify({"erro": "foto não encontrada"}), 404
    return send_from_directory(FOTOS_DIR, nome_arquivo)


@app.route("/api/categorias", methods=["GET"])
def categorias():
    conn = get_db()
    rows = conn.execute("SELECT * FROM categorias ORDER BY nome").fetchall()
    conn.close()
    receita = [dict(r) for r in rows if r["tipo"] == "receita"]
    despesa = [dict(r) for r in rows if r["tipo"] == "despesa"]
    return jsonify({
        "receita": [r["nome"] for r in receita],
        "despesa": [r["nome"] for r in despesa],
        "receita_full": receita,
        "despesa_full": despesa,
    })


@app.route("/api/categorias", methods=["POST"])
def criar_categoria():
    data = request.get_json(force=True)
    nome = data.get("nome", "").strip()
    tipo = data.get("tipo")
    cor = data.get("cor") or None
    if not nome or tipo not in ("receita", "despesa"):
        return jsonify({"erro": "nome e tipo (receita/despesa) são obrigatórios"}), 400
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO categorias (nome, tipo, cor) VALUES (?, ?, ?)", (nome, tipo, cor))
    conn.commit()
    conn.close()
    return jsonify({"ok": True}), 201


@app.route("/api/categorias/<int:item_id>", methods=["PUT"])
def editar_categoria(item_id):
    data = request.get_json(force=True)
    nome = data.get("nome", "").strip()
    cor = data.get("cor") or None
    if not nome:
        return jsonify({"erro": "nome é obrigatório"}), 400
    conn = get_db()
    try:
        conn.execute("UPDATE categorias SET nome = ?, cor = ? WHERE id = ?", (nome, cor, item_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"erro": "já existe uma categoria com esse nome"}), 400
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/categorias/<int:item_id>", methods=["DELETE"])
def deletar_categoria(item_id):
    conn = get_db()
    conn.execute("DELETE FROM categorias WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/meses", methods=["GET"])
def listar_meses():
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT mes FROM lancamentos WHERE usuario_id = ? ORDER BY mes DESC", (uid(),)
    ).fetchall()
    conn.close()
    meses = [r["mes"] for r in rows]
    mes_atual = datetime.now().strftime("%Y-%m")
    if mes_atual not in meses:
        meses.insert(0, mes_atual)
    meses = sorted(set(meses), reverse=True)
    return jsonify(meses)


@app.route("/api/lancamentos", methods=["GET"])
def listar_lancamentos():
    mes = request.args.get("mes", datetime.now().strftime("%Y-%m"))
    conn = get_db()
    garantir_recorrentes(conn, mes, uid())
    rows = conn.execute(
        "SELECT * FROM lancamentos WHERE mes = ? AND usuario_id = ? ORDER BY tipo, id", (mes, uid())
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


def resolver_conta(conn, conta_id):
    """Resolve a conta informada, aceitando apenas contas do próprio usuário."""
    if not conta_id:
        return None, ""
    try:
        conta_id = int(conta_id)
    except (TypeError, ValueError):
        return None, ""
    row = conn.execute(
        "SELECT id, nome FROM contas WHERE id = ? AND usuario_id = ?", (conta_id, uid())
    ).fetchone()
    if not row:
        return None, ""
    return row["id"], row["nome"]


@app.route("/api/lancamentos", methods=["POST"])
def criar_lancamento():
    data = request.get_json(force=True)
    mes = data.get("mes") or datetime.now().strftime("%Y-%m")
    tipo = data.get("tipo")
    descricao = data.get("descricao", "").strip()
    valor = float(data.get("valor", 0) or 0)
    vencimento = data.get("vencimento", "")
    categoria = data.get("categoria", "")
    observacao = data.get("observacao", "")
    recorrente = 1 if data.get("recorrente") else 0
    parcelas = int(data.get("parcelas") or 1)
    pago = 1 if data.get("pago") else 0
    data_pagamento = data.get("data_pagamento") or (datetime.now().strftime("%Y-%m-%d") if pago else "")

    if tipo not in ("renda", "despesa"):
        return jsonify({"erro": "tipo inválido"}), 400
    if not descricao:
        return jsonify({"erro": "descrição obrigatória"}), 400

    conn = get_db()
    conta_id, conta = resolver_conta(conn, data.get("conta_id"))

    if parcelas > 1:
        grupo = str(uuid.uuid4())
        dia = None
        if vencimento:
            dia = int(vencimento.split("-")[2])
        for i in range(parcelas):
            mes_parcela = somar_meses(mes, i)
            venc_parcela = ""
            if dia:
                ano_p, mes_p = map(int, mes_parcela.split("-"))
                ultimo_dia = calendar.monthrange(ano_p, mes_p)[1]
                dia_ajustado = min(dia, ultimo_dia)
                venc_parcela = f"{ano_p}-{mes_p:02d}-{dia_ajustado:02d}"
            desc_parcela = f"{descricao} ({i+1}/{parcelas})"
            conn.execute(
                """INSERT INTO lancamentos
                   (mes, tipo, descricao, valor, vencimento, categoria, conta, conta_id, recorrente,
                    grupo_parcela, parcela_num, parcela_total, pago, data_pagamento, observacao,
                    criado_em, usuario_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 0, '', ?, ?, ?)""",
                (mes_parcela, tipo, desc_parcela, valor, venc_parcela, categoria, conta, conta_id,
                 grupo, i + 1, parcelas, observacao, datetime.now().isoformat(), uid()),
            )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "parcelas_criadas": parcelas}), 201

    # Recorrente vira uma "série": é ela que se propaga sozinha para os meses seguintes.
    grupo_recorrencia = str(uuid.uuid4()) if recorrente else None
    # Repetições limitadas (ex: dívida em 6x) viram o mês em que a série termina.
    recorrencia_ate = None
    if recorrente:
        try:
            vezes = int(data.get("recorrencia_vezes") or 0)
        except (TypeError, ValueError):
            vezes = 0
        if vezes > 1:
            recorrencia_ate = somar_meses(mes, vezes - 1)
        elif vezes == 1:
            recorrencia_ate = mes
    cur = conn.execute(
        """INSERT INTO lancamentos
           (mes, tipo, descricao, valor, vencimento, categoria, conta, conta_id, recorrente,
            pago, data_pagamento, observacao, criado_em, usuario_id, grupo_recorrencia,
            recorrencia_ate)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (mes, tipo, descricao, valor, vencimento, categoria, conta, conta_id, recorrente,
         pago, data_pagamento, observacao, datetime.now().isoformat(), uid(), grupo_recorrencia,
         recorrencia_ate),
    )
    conn.commit()
    novo_id = cur.lastrowid
    conn.close()
    return jsonify({"ok": True, "id": novo_id}), 201


@app.route("/api/lancamentos/<int:item_id>", methods=["DELETE"])
def deletar_lancamento(item_id):
    # escopo=todos apaga a recorrência inteira; o padrão apaga só esta ocorrência.
    escopo = request.args.get("escopo", "este")
    conn = get_db()
    row = conn.execute(
        "SELECT comprovante, eh_transferencia, grupo_transferencia, grupo_recorrencia, mes, usuario_id "
        "FROM lancamentos WHERE id = ?", (item_id,)
    ).fetchone()
    if not row or row["usuario_id"] != uid():
        conn.close()
        return jsonify({"erro": "lançamento não encontrado"}), 404

    def apagar_comprovante(nome):
        if not nome:
            return
        caminho = os.path.join(COMPROVANTES_DIR, nome)
        if os.path.exists(caminho):
            os.remove(caminho)

    if escopo == "todos" and row["grupo_recorrencia"]:
        grupo = row["grupo_recorrencia"]
        for r in conn.execute(
            "SELECT comprovante FROM lancamentos WHERE grupo_recorrencia = ? AND usuario_id = ?",
            (grupo, uid()),
        ).fetchall():
            apagar_comprovante(r["comprovante"])
        conn.execute(
            "DELETE FROM lancamentos WHERE grupo_recorrencia = ? AND usuario_id = ?", (grupo, uid())
        )
        # A série deixou de existir, então os pulos dela não fazem mais sentido.
        conn.execute("DELETE FROM recorrencias_puladas WHERE grupo_recorrencia = ?", (grupo,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "escopo": "todos"})

    apagar_comprovante(row["comprovante"])
    conn.execute("DELETE FROM lancamentos WHERE id = ?", (item_id,))
    # Sem isso a recorrência voltaria sozinha na próxima vez que o mês fosse aberto.
    if row["grupo_recorrencia"]:
        conn.execute(
            "INSERT OR IGNORE INTO recorrencias_puladas (grupo_recorrencia, mes) VALUES (?, ?)",
            (row["grupo_recorrencia"], row["mes"]),
        )
    # Transferência é um evento único: apagar uma perna apaga a outra,
    # mesmo que a outra perna pertença ao outro usuário.
    if row["eh_transferencia"] and row["grupo_transferencia"]:
        conn.execute(
            "DELETE FROM lancamentos WHERE grupo_transferencia = ? AND id != ?",
            (row["grupo_transferencia"], item_id),
        )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "escopo": "este"})


@app.route("/api/lancamentos/<int:item_id>", methods=["PUT"])
def editar_lancamento(item_id):
    data = request.get_json(force=True)
    conn = get_db()
    if not pertence_ao_usuario(conn, "lancamentos", item_id):
        conn.close()
        return jsonify({"erro": "lançamento não encontrado"}), 404
    conta_id, conta = resolver_conta(conn, data.get("conta_id"))
    conn.execute(
        """UPDATE lancamentos SET descricao = ?, valor = ?, vencimento = ?, categoria = ?,
           conta = ?, conta_id = ?, recorrente = ?, observacao = ? WHERE id = ?""",
        (
            data.get("descricao"), float(data.get("valor", 0) or 0), data.get("vencimento", ""),
            data.get("categoria", ""), conta, conta_id,
            1 if data.get("recorrente") else 0, data.get("observacao", ""), item_id,
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/lancamentos/<int:item_id>/pagamento", methods=["PUT"])
def marcar_pagamento(item_id):
    data = request.get_json(force=True)
    pago = 1 if data.get("pago") else 0
    data_pagamento = data.get("data_pagamento") or ""
    if pago and not data_pagamento:
        data_pagamento = datetime.now().strftime("%Y-%m-%d")
    if not pago:
        data_pagamento = ""
    conn = get_db()
    if not pertence_ao_usuario(conn, "lancamentos", item_id):
        conn.close()
        return jsonify({"erro": "lançamento não encontrado"}), 404
    conn.execute("UPDATE lancamentos SET pago = ?, data_pagamento = ? WHERE id = ?",
                 (pago, data_pagamento, item_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/lancamentos/<int:item_id>/comprovante", methods=["POST"])
def enviar_comprovante(item_id):
    if "arquivo" not in request.files:
        return jsonify({"erro": "nenhum arquivo enviado"}), 400
    arquivo = request.files["arquivo"]
    if arquivo.filename == "":
        return jsonify({"erro": "arquivo sem nome"}), 400
    conn = get_db()
    if not pertence_ao_usuario(conn, "lancamentos", item_id):
        conn.close()
        return jsonify({"erro": "lançamento não encontrado"}), 404
    nome_seguro = secure_filename(arquivo.filename)
    nome_final = f"{item_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{nome_seguro}"
    os.makedirs(COMPROVANTES_DIR, exist_ok=True)
    arquivo.save(os.path.join(COMPROVANTES_DIR, nome_final))
    row = conn.execute("SELECT comprovante FROM lancamentos WHERE id = ?", (item_id,)).fetchone()
    if row and row["comprovante"]:
        antigo = os.path.join(COMPROVANTES_DIR, row["comprovante"])
        if os.path.exists(antigo):
            os.remove(antigo)
    conn.execute("UPDATE lancamentos SET comprovante = ? WHERE id = ?", (nome_final, item_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "comprovante": nome_final})


@app.route("/api/comprovante/<path:nome_arquivo>", methods=["GET"])
def baixar_comprovante(nome_arquivo):
    conn = get_db()
    dono = conn.execute(
        "SELECT usuario_id FROM lancamentos WHERE comprovante = ?", (nome_arquivo,)
    ).fetchone()
    conn.close()
    if not dono or dono["usuario_id"] != uid():
        return jsonify({"erro": "comprovante não encontrado"}), 404
    return send_from_directory(COMPROVANTES_DIR, nome_arquivo)


# ---------------- Contas ----------------

def contas_com_saldo(conn, mes):
    contas = conn.execute(
        "SELECT contas.*, usuarios.nome as usuario_nome FROM contas "
        "LEFT JOIN usuarios ON usuarios.id = contas.usuario_id "
        "WHERE contas.usuario_id = ? ORDER BY contas.nome",
        (uid(),),
    ).fetchall()
    resultado = []
    for c in contas:
        def soma(pago, tipo):
            row = conn.execute(
                "SELECT COALESCE(SUM(valor),0) as total FROM lancamentos WHERE conta_id = ? AND pago = ? AND tipo = ?",
                (c["id"], pago, tipo),
            ).fetchone()
            return row["total"]

        def soma_mes(tipo):
            row = conn.execute(
                "SELECT COALESCE(SUM(valor),0) as total FROM lancamentos WHERE conta_id = ? AND mes = ? AND tipo = ?",
                (c["id"], mes, tipo),
            ).fetchone()
            return row["total"]

        saldo_atual = c["saldo_inicial"] + soma(1, "renda") - soma(1, "despesa")
        pendente_liquido = soma(0, "renda") - soma(0, "despesa")
        resultado.append({
            "id": c["id"],
            "nome": c["nome"],
            "saldo_inicial": c["saldo_inicial"],
            "saldo_atual": saldo_atual,
            "saldo_previsto": saldo_atual + pendente_liquido,
            "receitas_mes": soma_mes("renda"),
            "despesas_mes": soma_mes("despesa"),
            "usuario_id": c["usuario_id"],
            "usuario_nome": c["usuario_nome"],
        })
    return resultado


@app.route("/api/contas", methods=["GET"])
def listar_contas():
    mes = request.args.get("mes", datetime.now().strftime("%Y-%m"))
    conn = get_db()
    resultado = contas_com_saldo(conn, mes)
    conn.close()
    return jsonify(resultado)


@app.route("/api/contas", methods=["POST"])
def criar_conta():
    data = request.get_json(force=True)
    nome = data.get("nome", "").strip()
    if not nome:
        return jsonify({"erro": "nome é obrigatório"}), 400
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO contas (nome, saldo_inicial, criado_em, usuario_id) VALUES (?, ?, ?, ?)",
            (nome, float(data.get("saldo_inicial", 0) or 0), datetime.now().isoformat(), uid()),
        )
        conn.commit()
        novo_id = cur.lastrowid
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"erro": "já existe uma conta com esse nome"}), 400
    conn.close()
    return jsonify({"ok": True, "id": novo_id}), 201


@app.route("/api/contas/<int:item_id>", methods=["PUT"])
def editar_conta(item_id):
    data = request.get_json(force=True)
    nome = data.get("nome", "").strip()
    if not nome:
        return jsonify({"erro": "nome é obrigatório"}), 400
    conn = get_db()
    if not pertence_ao_usuario(conn, "contas", item_id):
        conn.close()
        return jsonify({"erro": "conta não encontrada"}), 404
    try:
        conn.execute(
            "UPDATE contas SET nome = ?, saldo_inicial = ? WHERE id = ?",
            (nome, float(data.get("saldo_inicial", 0) or 0), item_id),
        )
        conn.execute("UPDATE lancamentos SET conta = ? WHERE conta_id = ?", (nome, item_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"erro": "já existe uma conta com esse nome"}), 400
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/contas/<int:item_id>", methods=["DELETE"])
def deletar_conta(item_id):
    conn = get_db()
    if not pertence_ao_usuario(conn, "contas", item_id):
        conn.close()
        return jsonify({"erro": "conta não encontrada"}), 404
    conn.execute("UPDATE lancamentos SET conta_id = NULL, conta = '' WHERE conta_id = ?", (item_id,))
    conn.execute("UPDATE cartoes SET conta_id = NULL WHERE conta_id = ?", (item_id,))
    conn.execute("DELETE FROM contas WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/transferencias", methods=["POST"])
def criar_transferencia():
    data = request.get_json(force=True)
    conta_origem_id = data.get("conta_origem_id")
    conta_destino_id = data.get("conta_destino_id")
    valor = float(data.get("valor", 0) or 0)
    data_transf = data.get("data") or datetime.now().strftime("%Y-%m-%d")
    descricao = data.get("descricao", "").strip() or "Transferência"

    if not conta_origem_id or not conta_destino_id:
        return jsonify({"erro": "conta de origem e destino são obrigatórias"}), 400
    if str(conta_origem_id) == str(conta_destino_id):
        return jsonify({"erro": "as contas de origem e destino devem ser diferentes"}), 400
    if valor <= 0:
        return jsonify({"erro": "valor deve ser maior que zero"}), 400

    conn = get_db()
    # A origem tem que ser sua; o destino pode ser a conta de outro usuário
    # (ex: transferir da sua conta para a da sua esposa).
    origem = conn.execute(
        "SELECT id, nome, usuario_id FROM contas WHERE id = ? AND usuario_id = ?",
        (conta_origem_id, uid()),
    ).fetchone()
    destino = conn.execute(
        "SELECT id, nome, usuario_id FROM contas WHERE id = ?", (conta_destino_id,)
    ).fetchone()
    if not origem:
        conn.close()
        return jsonify({"erro": "conta de origem inválida"}), 400
    if not destino:
        conn.close()
        return jsonify({"erro": "conta de destino inválida"}), 400

    mes = data_transf[:7]
    grupo = str(uuid.uuid4())
    agora = datetime.now().isoformat()

    # Cada perna pertence ao dono da respectiva conta, então cada um vê só o seu lado.
    conn.execute(
        """INSERT INTO lancamentos
           (mes, tipo, descricao, valor, vencimento, categoria, conta, conta_id, pago, data_pagamento,
            observacao, eh_transferencia, grupo_transferencia, criado_em, usuario_id)
           VALUES (?, 'despesa', ?, ?, ?, 'Transferência', ?, ?, 1, ?, ?, 1, ?, ?, ?)""",
        (mes, f"{descricao} → {destino['nome']}", valor, data_transf, origem["nome"], origem["id"],
         data_transf, f"Transferência para {destino['nome']}", grupo, agora, origem["usuario_id"]),
    )
    conn.execute(
        """INSERT INTO lancamentos
           (mes, tipo, descricao, valor, vencimento, categoria, conta, conta_id, pago, data_pagamento,
            observacao, eh_transferencia, grupo_transferencia, criado_em, usuario_id)
           VALUES (?, 'renda', ?, ?, ?, 'Transferência', ?, ?, 1, ?, ?, 1, ?, ?, ?)""",
        (mes, f"{descricao} de {origem['nome']}", valor, data_transf, destino["nome"], destino["id"],
         data_transf, f"Transferência de {origem['nome']}", grupo, agora, destino["usuario_id"]),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True}), 201


@app.route("/api/contas-transferencia", methods=["GET"])
def contas_para_transferencia():
    """Contas disponíveis numa transferência: as suas (origem/destino) e as dos
    outros usuários (só destino). Expõe apenas id, nome e dono — nenhum saldo."""
    conn = get_db()
    rows = conn.execute(
        "SELECT contas.id, contas.nome, contas.usuario_id, usuarios.nome as usuario_nome "
        "FROM contas LEFT JOIN usuarios ON usuarios.id = contas.usuario_id "
        "ORDER BY usuarios.nome, contas.nome"
    ).fetchall()
    conn.close()
    meu_id = uid()
    return jsonify([
        {**dict(r), "minha": r["usuario_id"] == meu_id} for r in rows
    ])


# ---------------- Cartões de crédito ----------------

@app.route("/api/cartoes", methods=["GET"])
def listar_cartoes():
    conn = get_db()
    rows = conn.execute(
        "SELECT cartoes.*, contas.nome as conta_nome FROM cartoes "
        "LEFT JOIN contas ON contas.id = cartoes.conta_id "
        "WHERE cartoes.usuario_id = ? ORDER BY cartoes.id",
        (uid(),),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/cartoes", methods=["POST"])
def criar_cartao():
    data = request.get_json(force=True)
    conn = get_db()
    conta_id, _ = resolver_conta(conn, data.get("conta_id"))
    conn.execute(
        "INSERT INTO cartoes (nome, limite, fatura_atual, dia_vencimento, conta_id, usuario_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (data.get("nome", "").strip(), float(data.get("limite", 0) or 0),
         float(data.get("fatura_atual", 0) or 0), int(data.get("dia_vencimento") or 0) or None,
         conta_id, uid()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True}), 201


@app.route("/api/cartoes/<int:item_id>", methods=["PUT"])
def editar_cartao(item_id):
    data = request.get_json(force=True)
    conn = get_db()
    if not pertence_ao_usuario(conn, "cartoes", item_id):
        conn.close()
        return jsonify({"erro": "cartão não encontrado"}), 404
    conta_id, _ = resolver_conta(conn, data.get("conta_id"))
    conn.execute(
        "UPDATE cartoes SET nome = ?, limite = ?, fatura_atual = ?, dia_vencimento = ?, conta_id = ? WHERE id = ?",
        (data.get("nome", "").strip(), float(data.get("limite", 0) or 0),
         float(data.get("fatura_atual", 0) or 0), int(data.get("dia_vencimento") or 0) or None,
         conta_id, item_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/cartoes/<int:item_id>/fatura-pagamento", methods=["PUT"])
def marcar_fatura_cartao(item_id):
    data = request.get_json(force=True)
    conn = get_db()
    if not pertence_ao_usuario(conn, "cartoes", item_id):
        conn.close()
        return jsonify({"erro": "cartão não encontrado"}), 404
    conn.execute("UPDATE cartoes SET fatura_paga = ? WHERE id = ?", (1 if data.get("pago") else 0, item_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/cartoes/<int:item_id>", methods=["DELETE"])
def deletar_cartao(item_id):
    conn = get_db()
    if not pertence_ao_usuario(conn, "cartoes", item_id):
        conn.close()
        return jsonify({"erro": "cartão não encontrado"}), 404
    conn.execute("DELETE FROM cartoes WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ---------------- Dashboard ----------------

@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    mes = request.args.get("mes", datetime.now().strftime("%Y-%m"))
    hoje = datetime.now().strftime("%Y-%m-%d")
    hoje_dt = datetime.now()
    ano, m = map(int, mes.split("-"))
    ultimo_dia_mes = calendar.monthrange(ano, m)[1]
    e_mes_atual = mes == hoje_dt.strftime("%Y-%m")
    dias_restantes = (ultimo_dia_mes - hoje_dt.day + 1) if e_mes_atual else ultimo_dia_mes

    conn = get_db()
    garantir_recorrentes(conn, mes, uid())

    def totais_do_mes(mes_ref):
        rows = conn.execute(
            "SELECT tipo, pago, COALESCE(SUM(valor),0) as total FROM lancamentos "
            "WHERE mes = ? AND eh_transferencia = 0 AND usuario_id = ? GROUP BY tipo, pago",
            (mes_ref, uid()),
        ).fetchall()
        t = {"receita_total": 0.0, "receita_recebida": 0.0, "despesa_total": 0.0, "despesa_paga": 0.0}
        for r in rows:
            if r["tipo"] == "renda":
                t["receita_total"] += r["total"]
                if r["pago"]:
                    t["receita_recebida"] += r["total"]
            else:
                t["despesa_total"] += r["total"]
                if r["pago"]:
                    t["despesa_paga"] += r["total"]
        return t

    atual = totais_do_mes(mes)
    anterior = totais_do_mes(mes_anterior(mes))

    receita_total = atual["receita_total"]
    receita_recebida = atual["receita_recebida"]
    despesa_total = atual["despesa_total"]
    despesa_paga = atual["despesa_paga"]
    despesa_pendente = despesa_total - despesa_paga
    receita_pendente = receita_total - receita_recebida

    saldo_atual = receita_recebida - despesa_paga
    disponivel = receita_total - despesa_total
    previsao_fim_mes = saldo_atual + receita_pendente - despesa_pendente

    livre_para_gastar = saldo_atual - despesa_pendente
    gasto_diario = round(max(livre_para_gastar, 0) / dias_restantes, 2) if dias_restantes > 0 else 0

    limite_alerta = (hoje_dt + timedelta(days=7)).strftime("%Y-%m-%d")
    vencendo = conn.execute(
        "SELECT * FROM lancamentos WHERE tipo = 'despesa' AND pago = 0 AND vencimento != '' "
        "AND vencimento BETWEEN ? AND ? AND usuario_id = ? ORDER BY vencimento",
        (hoje, limite_alerta, uid()),
    ).fetchall()

    parcelas_futuras = conn.execute(
        "SELECT * FROM lancamentos WHERE parcela_total > 1 AND mes > ? AND usuario_id = ? "
        "ORDER BY mes, id LIMIT 20",
        (mes, uid()),
    ).fetchall()

    por_categoria = conn.execute(
        "SELECT COALESCE(NULLIF(categoria,''),'Sem categoria') as categoria, SUM(valor) as total "
        "FROM lancamentos WHERE mes = ? AND tipo = 'despesa' AND eh_transferencia = 0 AND usuario_id = ? "
        "GROUP BY categoria ORDER BY total DESC",
        (mes, uid()),
    ).fetchall()

    cartoes = conn.execute(
        "SELECT cartoes.*, contas.nome as conta_nome FROM cartoes "
        "LEFT JOIN contas ON contas.id = cartoes.conta_id "
        "WHERE cartoes.usuario_id = ? ORDER BY cartoes.id",
        (uid(),),
    ).fetchall()
    contas = contas_com_saldo(conn, mes)
    saldo_total_contas = sum(c["saldo_atual"] for c in contas)

    conn.close()

    return jsonify({
        "saldo_atual": saldo_atual,
        "disponivel": disponivel,
        "receita_total": receita_total,
        "receita_recebida": receita_recebida,
        "despesa_total": despesa_total,
        "despesa_paga": despesa_paga,
        "despesa_pendente": despesa_pendente,
        "ainda_pode_gastar": max(disponivel, 0),
        "previsao_fim_mes": previsao_fim_mes,
        "gasto_diario_disponivel": gasto_diario,
        "dias_restantes": dias_restantes,
        "contas_vencendo": [dict(r) for r in vencendo],
        "parcelas_futuras": [dict(r) for r in parcelas_futuras],
        "grafico_categoria": [dict(r) for r in por_categoria],
        "cartoes": [dict(r) for r in cartoes],
        "contas": contas,
        "saldo_total_contas": saldo_total_contas,
        "mes_anterior": {"receita": anterior["receita_total"], "despesa": anterior["despesa_total"]},
        "mes_atual_grafico": {"receita": receita_total, "despesa": despesa_total},
    })


@app.route("/api/resumo", methods=["GET"])
def resumo():
    mes = request.args.get("mes", datetime.now().strftime("%Y-%m"))
    conn = get_db()
    rows = conn.execute(
        "SELECT tipo, SUM(valor) as total FROM lancamentos "
        "WHERE mes = ? AND eh_transferencia = 0 AND usuario_id = ? GROUP BY tipo",
        (mes, uid()),
    ).fetchall()
    pendentes = conn.execute(
        "SELECT COUNT(*) as n, COALESCE(SUM(valor),0) as total FROM lancamentos "
        "WHERE mes = ? AND tipo = 'despesa' AND pago = 0 AND usuario_id = ?", (mes, uid())
    ).fetchone()
    conn.close()
    totais = {"renda": 0.0, "despesa": 0.0}
    for r in rows:
        totais[r["tipo"]] = r["total"] or 0.0
    saldo = totais["renda"] - totais["despesa"]
    return jsonify({
        "totais": totais, "saldo": saldo,
        "pendentes": {"quantidade": pendentes["n"], "total": pendentes["total"]},
    })


# ---------------- Modo demonstração ----------------

def _semear_demo():
    """Preenche o banco de demonstração com uma vida financeira fictícia:
    salário, dois aluguéis recebidos, contas de casa, cartões e parcelamentos."""
    if os.path.exists(DEMO_DB_PATH):
        os.remove(DEMO_DB_PATH)
    init_db(DEMO_DB_PATH, criar_usuario_inicial=False)

    conn = sqlite3.connect(DEMO_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("DELETE FROM usuarios")
    conn.execute(
        "INSERT INTO usuarios (id, nome, username, senha_hash, criado_em) VALUES (1, ?, ?, ?, ?)",
        ("Visitante (demo)", "demo", generate_password_hash(secrets.token_urlsafe(16)),
         datetime.now().isoformat()),
    )

    # Saldos escolhidos para que, depois dos meses de histórico abaixo,
    # o saldo somado das contas fique em torno de R$ 20 mil.
    contas = [("Nubank", 3500.0), ("Itaú", 2200.0), ("Carteira", 2400.0)]
    ids_conta = {}
    for nome, saldo in contas:
        cur = conn.execute(
            "INSERT INTO contas (nome, saldo_inicial, criado_em, usuario_id) VALUES (?, ?, ?, 1)",
            (nome, saldo, datetime.now().isoformat()),
        )
        ids_conta[nome] = cur.lastrowid

    conn.execute(
        "INSERT INTO cartoes (nome, limite, fatura_atual, dia_vencimento, conta_id, fatura_paga, usuario_id) "
        "VALUES (?, ?, ?, ?, ?, 0, 1)", ("Nubank", 15000.0, 3180.45, 10, ids_conta["Nubank"]))
    conn.execute(
        "INSERT INTO cartoes (nome, limite, fatura_atual, dia_vencimento, conta_id, fatura_paga, usuario_id) "
        "VALUES (?, ?, ?, ?, ?, 1, 1)", ("Itaú Platinum", 8000.0, 942.80, 17, ids_conta["Itaú"]))

    hoje = datetime.now()
    mes_atual = hoje.strftime("%Y-%m")
    meses = [mes_anterior(mes_anterior(mes_atual)), mes_anterior(mes_atual), mes_atual]

    # (descricao, valor, categoria, conta, dia, recorrente)
    receitas = [
        ("Salário", 7800.00, "Salário", "Nubank", 5, True),
        ("Aluguel recebido — Apto Centro", 2000.00, "Rendimentos", "Itaú", 10, True),
        ("Aluguel recebido — Kitnet", 2000.00, "Rendimentos", "Itaú", 10, True),
    ]
    despesas = [
        ("Aluguel", 1000.00, "Aluguel", "Nubank", 5, True),
        ("Condomínio", 480.00, "Aluguel", "Nubank", 5, True),
        ("Energia", 212.40, "Energia", "Nubank", 15, True),
        ("Água", 96.80, "Água", "Nubank", 15, True),
        ("Internet", 129.90, "Internet", "Nubank", 17, True),
        ("Celular", 89.90, "Internet", "Nubank", 17, True),
        ("Plano de saúde", 618.00, "Outros", "Itaú", 8, True),
        ("Escola das crianças", 890.00, "Outros", "Itaú", 10, True),
        ("Seguro do carro", 268.00, "Transporte", "Itaú", 12, True),
        ("Parcela do carro", 1180.00, "Transporte", "Itaú", 12, True),
        ("Fatura do cartão", 1850.00, "Compras", "Nubank", 10, True),
        ("Mercado do mês", 1340.75, "Mercado", "Nubank", 12, False),
        ("Padaria e lanches", 318.60, "Alimentação", "Carteira", 25, False),
        ("Combustível", 420.00, "Combustível", "Carteira", 20, False),
        ("Academia", 119.90, "Lazer", "Nubank", 6, True),
        ("Streaming", 55.90, "Lazer", "Nubank", 22, True),
        ("Pet shop", 165.00, "Outros", "Nubank", 14, False),
    ]

    def dia_valido(mes, dia):
        ano, m = map(int, mes.split("-"))
        return f"{ano}-{m:02d}-{min(dia, calendar.monthrange(ano, m)[1]):02d}"

    agora = datetime.now().isoformat()
    for indice_mes, mes in enumerate(meses):
        ultimo = indice_mes == len(meses) - 1
        for descricao, valor, categoria, conta, dia, recorrente in receitas + despesas:
            tipo = "renda" if (descricao, valor, categoria, conta, dia, recorrente) in receitas else "despesa"
            vencimento = dia_valido(mes, dia)
            # No mês atual, o que já venceu aparece como pago; o resto fica pendente.
            pago = 0 if (ultimo and vencimento > hoje.strftime("%Y-%m-%d")) else 1
            grupo = f"demo-{descricao}" if recorrente else None
            conn.execute(
                """INSERT INTO lancamentos
                   (mes, tipo, descricao, valor, vencimento, categoria, conta, conta_id, recorrente,
                    pago, data_pagamento, observacao, criado_em, usuario_id, grupo_recorrencia)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, 1, ?)""",
                (mes, tipo, descricao, valor, vencimento, categoria, conta, ids_conta[conta],
                 1 if recorrente else 0, pago, vencimento if pago else "", agora, grupo),
            )

    # Uma compra parcelada em 10x, começando dois meses atrás.
    grupo_parcela = str(uuid.uuid4())
    for i in range(10):
        mes_p = somar_meses(meses[0], i)
        venc = dia_valido(mes_p, 18)
        conn.execute(
            """INSERT INTO lancamentos
               (mes, tipo, descricao, valor, vencimento, categoria, conta, conta_id, recorrente,
                grupo_parcela, parcela_num, parcela_total, pago, data_pagamento, observacao,
                criado_em, usuario_id)
               VALUES (?, 'despesa', ?, 489.90, ?, 'Compras', 'Nubank', ?, 0, ?, ?, 10, ?, ?, '', ?, 1)""",
            (mes_p, f"Notebook novo ({i+1}/10)", venc, ids_conta["Nubank"], grupo_parcela, i + 1,
             1 if venc <= hoje.strftime("%Y-%m-%d") else 0,
             venc if venc <= hoje.strftime("%Y-%m-%d") else "", agora),
        )

    # Extras só do mês atual, para o dashboard ficar variado.
    extras = [
        ("renda", "Freelance — site institucional", 1250.00, "Freelance", "Nubank", 1),
        ("despesa", "Jantar de aniversário", 268.40, "Alimentação", "Nubank", 1),
        ("despesa", "Farmácia", 87.30, "Outros", "Carteira", 1),
        ("despesa", "Presente", 150.00, "Compras", "Nubank", 0),
    ]
    for tipo, descricao, valor, categoria, conta, pago in extras:
        venc = dia_valido(mes_atual, min(hoje.day, 26))
        conn.execute(
            """INSERT INTO lancamentos
               (mes, tipo, descricao, valor, vencimento, categoria, conta, conta_id, recorrente,
                pago, data_pagamento, observacao, criado_em, usuario_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, '', ?, 1)""",
            (mes_atual, tipo, descricao, valor, venc, categoria, conta, ids_conta[conta],
             pago, venc if pago else "", agora),
        )

    # Uma transferência entre contas próprias.
    grupo_transf = str(uuid.uuid4())
    venc_transf = dia_valido(mes_atual, min(hoje.day, 15))
    for tipo, conta, sinal in (("despesa", "Nubank", "→ Itaú"), ("renda", "Itaú", "de Nubank")):
        conn.execute(
            """INSERT INTO lancamentos
               (mes, tipo, descricao, valor, vencimento, categoria, conta, conta_id, pago,
                data_pagamento, observacao, eh_transferencia, grupo_transferencia, criado_em, usuario_id)
               VALUES (?, ?, ?, 1500.0, ?, 'Transferência', ?, ?, 1, ?, '', 1, ?, ?, 1)""",
            (mes_atual, tipo, f"Reserva {sinal}", venc_transf, conta, ids_conta[conta],
             venc_transf, grupo_transf, agora),
        )

    cores = {"Salário": "#3ecf8e", "Rendimentos": "#4dd0e1", "Aluguel": "#ff6b6b",
             "Mercado": "#f5c451", "Lazer": "#a78bfa", "Energia": "#f0932b"}
    for nome, cor in cores.items():
        conn.execute("UPDATE categorias SET cor = ? WHERE nome = ?", (cor, nome))

    conn.commit()
    conn.close()


@app.route("/api/demo", methods=["GET"])
def status_demo():
    return jsonify({"ativo": em_demo()})


@app.route("/api/demo", methods=["POST"])
def alternar_demo():
    data = request.get_json(force=True)
    ativar = bool(data.get("ativo"))
    if ativar:
        _semear_demo()
        session["demo"] = True
    else:
        session.pop("demo", None)
    return jsonify({"ok": True, "ativo": em_demo()})


# ---------------- Backup e restauração ----------------

ARQUIVO_BANCO_NO_ZIP = "orcamento.db"

PERIODOS_BACKUP = {
    "desligado": 0,
    "diario": 24 * 3600,
    "semanal": 7 * 24 * 3600,
    "mensal": 30 * 24 * 3600,
}
CONFIG_BACKUP_PADRAO = {"periodo": "semanal", "manter": 5}


def copia_consistente_do_banco(destino):
    """Copia o banco usando a API de backup do SQLite: seguro mesmo com o app
    em uso, ao contrário de copiar o arquivo direto."""
    origem = sqlite3.connect(DB_PATH)
    try:
        alvo = sqlite3.connect(destino)
        try:
            origem.backup(alvo)
        finally:
            alvo.close()
    finally:
        origem.close()


def escrever_zip_backup(destino_stream_ou_caminho):
    """Monta o .zip com banco + comprovantes + fotos."""
    with tempfile.TemporaryDirectory() as tmp:
        copia_banco = os.path.join(tmp, ARQUIVO_BANCO_NO_ZIP)
        copia_consistente_do_banco(copia_banco)
        with zipfile.ZipFile(destino_stream_ou_caminho, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(copia_banco, ARQUIVO_BANCO_NO_ZIP)
            for pasta, prefixo in ((COMPROVANTES_DIR, "comprovantes"), (FOTOS_DIR, "fotos")):
                if not os.path.isdir(pasta):
                    continue
                for nome in os.listdir(pasta):
                    caminho = os.path.join(pasta, nome)
                    if os.path.isfile(caminho):
                        zf.write(caminho, f"{prefixo}/{nome}")


def ler_config_backup():
    caminho = os.path.join(os.path.dirname(DB_PATH), "backup-config.json")
    if os.path.exists(caminho):
        try:
            with open(caminho) as f:
                salvo = json.load(f)
            return {**CONFIG_BACKUP_PADRAO, **salvo}
        except (ValueError, OSError):
            pass
    return dict(CONFIG_BACKUP_PADRAO)


def gravar_config_backup(config):
    caminho = os.path.join(os.path.dirname(DB_PATH), "backup-config.json")
    with open(caminho, "w") as f:
        json.dump(config, f)


def listar_backups_automaticos():
    if not os.path.isdir(BACKUPS_DIR):
        return []
    arquivos = []
    for nome in os.listdir(BACKUPS_DIR):
        caminho = os.path.join(BACKUPS_DIR, nome)
        if nome.endswith(".zip") and os.path.isfile(caminho):
            arquivos.append({
                "nome": nome,
                "tamanho_bytes": os.path.getsize(caminho),
                "criado_em": datetime.fromtimestamp(os.path.getmtime(caminho)).isoformat(),
                "criado_em_ts": os.path.getmtime(caminho),
            })
    return sorted(arquivos, key=lambda a: a["criado_em_ts"], reverse=True)


def criar_backup_automatico():
    """Gera um backup em disco e apaga os mais antigos além do limite."""
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    nome = f"backup-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.zip"
    caminho = os.path.join(BACKUPS_DIR, nome)
    escrever_zip_backup(caminho)

    manter = max(int(ler_config_backup().get("manter", 5)), 1)
    for antigo in listar_backups_automaticos()[manter:]:
        try:
            os.remove(os.path.join(BACKUPS_DIR, antigo["nome"]))
        except OSError:
            pass
    return nome


def _loop_backup_automatico():
    """Roda em segundo plano verificando se já passou o intervalo configurado."""
    while True:
        try:
            config = ler_config_backup()
            intervalo = PERIODOS_BACKUP.get(config.get("periodo"), 0)
            if intervalo:
                existentes = listar_backups_automaticos()
                ultimo = existentes[0]["criado_em_ts"] if existentes else 0
                if (datetime.now().timestamp() - ultimo) >= intervalo:
                    nome = criar_backup_automatico()
                    print(f"[backup] backup automático criado: {nome}", flush=True)
        except Exception as e:
            print(f"[backup] falha ao gerar backup automático: {e}", flush=True)
        time.sleep(600)  # confere a cada 10 minutos


def iniciar_agendador_backup():
    t = threading.Thread(target=_loop_backup_automatico, daemon=True)
    t.start()


@app.route("/api/backup", methods=["GET"])
def baixar_backup():
    memoria = io.BytesIO()
    escrever_zip_backup(memoria)
    memoria.seek(0)
    nome_arquivo = f"backup-financeiro-{datetime.now().strftime('%Y-%m-%d-%H%M')}.zip"
    return send_file(memoria, mimetype="application/zip",
                     as_attachment=True, download_name=nome_arquivo)


@app.route("/api/backups", methods=["GET"])
def listar_backups():
    return jsonify({
        "config": ler_config_backup(),
        "periodos": list(PERIODOS_BACKUP.keys()),
        "backups": [
            {k: v for k, v in b.items() if k != "criado_em_ts"}
            for b in listar_backups_automaticos()
        ],
    })


@app.route("/api/backups/config", methods=["PUT"])
def salvar_config_backup():
    data = request.get_json(force=True)
    periodo = data.get("periodo")
    if periodo not in PERIODOS_BACKUP:
        return jsonify({"erro": "período inválido"}), 400
    try:
        manter = int(data.get("manter") or CONFIG_BACKUP_PADRAO["manter"])
    except (TypeError, ValueError):
        return jsonify({"erro": "quantidade inválida"}), 400
    manter = max(1, min(manter, 30))
    gravar_config_backup({"periodo": periodo, "manter": manter})
    return jsonify({"ok": True, "config": ler_config_backup()})


@app.route("/api/backups/agora", methods=["POST"])
def gerar_backup_agora():
    nome = criar_backup_automatico()
    return jsonify({"ok": True, "nome": nome}), 201


def caminho_backup_valido(nome_arquivo):
    """Impede que o nome escape da pasta de backups."""
    nome = os.path.basename(nome_arquivo)
    if not nome.endswith(".zip"):
        return None
    caminho = os.path.join(BACKUPS_DIR, nome)
    if not os.path.isfile(caminho):
        return None
    return caminho


@app.route("/api/backups/<path:nome_arquivo>", methods=["GET"])
def baixar_backup_salvo(nome_arquivo):
    caminho = caminho_backup_valido(nome_arquivo)
    if not caminho:
        return jsonify({"erro": "backup não encontrado"}), 404
    return send_from_directory(BACKUPS_DIR, os.path.basename(caminho), as_attachment=True)


@app.route("/api/backups/<path:nome_arquivo>", methods=["DELETE"])
def excluir_backup_salvo(nome_arquivo):
    caminho = caminho_backup_valido(nome_arquivo)
    if not caminho:
        return jsonify({"erro": "backup não encontrado"}), 404
    os.remove(caminho)
    return jsonify({"ok": True})


@app.route("/api/backups/<path:nome_arquivo>/restaurar", methods=["POST"])
def restaurar_backup_salvo(nome_arquivo):
    if em_demo():
        return jsonify({"erro": "desligue o modo demonstração antes de restaurar"}), 400
    caminho = caminho_backup_valido(nome_arquivo)
    if not caminho:
        return jsonify({"erro": "backup não encontrado"}), 404
    with open(caminho, "rb") as f:
        erro = aplicar_backup(f)
    if erro:
        return jsonify({"erro": erro}), 400
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/backup/info", methods=["GET"])
def info_backup():
    def tamanho_pasta(pasta):
        if not os.path.isdir(pasta):
            return 0, 0
        arquivos = [os.path.join(pasta, n) for n in os.listdir(pasta)]
        arquivos = [a for a in arquivos if os.path.isfile(a)]
        return len(arquivos), sum(os.path.getsize(a) for a in arquivos)

    n_comp, bytes_comp = tamanho_pasta(COMPROVANTES_DIR)
    n_fotos, bytes_fotos = tamanho_pasta(FOTOS_DIR)
    bytes_banco = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    return jsonify({
        "comprovantes": n_comp,
        "fotos": n_fotos,
        "tamanho_total_bytes": bytes_banco + bytes_comp + bytes_fotos,
    })


def aplicar_backup(origem_arquivo):
    """Valida e aplica um backup. Retorna None se deu certo, ou a mensagem de erro.
    Usado tanto pelo upload manual quanto pela restauração de um backup automático."""
    with tempfile.TemporaryDirectory() as tmp:
        caminho_zip = os.path.join(tmp, "backup.zip")
        if hasattr(origem_arquivo, "save"):
            origem_arquivo.save(caminho_zip)
        else:
            with open(caminho_zip, "wb") as destino:
                shutil.copyfileobj(origem_arquivo, destino)

        if not zipfile.is_zipfile(caminho_zip):
            return "arquivo inválido ou corrompido"

        extraido = os.path.join(tmp, "conteudo")
        with zipfile.ZipFile(caminho_zip) as zf:
            nomes = zf.namelist()
            if ARQUIVO_BANCO_NO_ZIP not in nomes:
                return "esse zip não parece um backup do app"
            # Evita zip-slip: nada pode escapar da pasta de destino.
            for nome in nomes:
                if os.path.isabs(nome) or ".." in nome.replace("\\", "/").split("/"):
                    return "arquivo de backup inválido"
            zf.extractall(extraido)

        banco_novo = os.path.join(extraido, ARQUIVO_BANCO_NO_ZIP)
        try:
            teste = sqlite3.connect(banco_novo)
            tabelas = {r[0] for r in teste.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            teste.close()
        except sqlite3.Error:
            return "o banco dentro do backup está corrompido"
        if not {"lancamentos", "usuarios"} <= tabelas:
            return "esse zip não parece um backup do app"

        # Guarda o estado atual antes de sobrescrever, por segurança.
        carimbo = datetime.now().strftime("%Y%m%d%H%M%S")
        if os.path.exists(DB_PATH):
            shutil.copy2(DB_PATH, f"{DB_PATH}.antes-de-restaurar-{carimbo}")
        shutil.copy2(banco_novo, DB_PATH)

        for prefixo, pasta in (("comprovantes", COMPROVANTES_DIR), ("fotos", FOTOS_DIR)):
            origem = os.path.join(extraido, prefixo)
            if not os.path.isdir(origem):
                continue
            os.makedirs(pasta, exist_ok=True)
            for nome in os.listdir(origem):
                caminho = os.path.join(origem, nome)
                if os.path.isfile(caminho):
                    shutil.copy2(caminho, os.path.join(pasta, os.path.basename(nome)))

    init_db()
    return None


@app.route("/api/restaurar", methods=["POST"])
def restaurar_backup():
    if em_demo():
        return jsonify({"erro": "desligue o modo demonstração antes de restaurar"}), 400
    if "arquivo" not in request.files:
        return jsonify({"erro": "envie o arquivo .zip do backup"}), 400
    enviado = request.files["arquivo"]
    if not enviado.filename.lower().endswith(".zip"):
        return jsonify({"erro": "o backup precisa ser um arquivo .zip"}), 400
    erro = aplicar_backup(enviado)
    if erro:
        return jsonify({"erro": erro}), 400
    session.clear()  # o backup pode ter outros usuários/senhas
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    iniciar_agendador_backup()
    app.run(host="0.0.0.0", port=5000)
