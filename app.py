# FinanCerto — controle financeiro pessoal self-hosted.
# Copyright (C) 2026 Thiago Cruz
#
# Este programa é software livre: você pode redistribuí-lo e/ou modificá-lo sob
# os termos da GNU Affero General Public License, conforme publicada pela Free
# Software Foundation, na versão 3 da licença ou, a seu critério, qualquer
# versão posterior.
#
# Este programa é distribuído na esperança de ser útil, mas SEM NENHUMA
# GARANTIA; sem sequer a garantia implícita de COMERCIALIZAÇÃO ou ADEQUAÇÃO A
# UM FIM ESPECÍFICO. Consulte a GNU Affero General Public License para mais
# detalhes. O texto oficial e integral está no arquivo LICENSE, em inglês, e é
# ele que prevalece — este aviso é apenas um resumo.
#
# Você deve ter recebido uma cópia da licença junto com este programa. Se não,
# veja <https://www.gnu.org/licenses/>.

import io
import re
import csv
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
import unicodedata
import requests
from urllib.parse import quote, urlparse
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename, safe_join
from werkzeug.security import generate_password_hash, check_password_hash
from flask import (Flask, request, jsonify, send_from_directory, session, redirect,
                   send_file, has_request_context)
from pypdf import PdfReader
import fitz
import pytesseract
from PIL import Image, ImageOps, ImageFilter
from pyzbar.pyzbar import decode as zbar_decode
from authlib.integrations.flask_client import OAuth

DB_PATH = os.environ.get("DB_PATH", "/data/orcamento.db")
COMPROVANTES_DIR = os.environ.get("COMPROVANTES_DIR", "/data/comprovantes")
FOTOS_DIR = os.environ.get("FOTOS_DIR", "/data/fotos")
BACKUPS_DIR = os.environ.get("BACKUPS_DIR", "/data/backups")
HOLERITES_DIR = os.environ.get("HOLERITES_DIR", "/data/holerites")
DEMO_DB_PATH = os.environ.get("DEMO_DB_PATH", "/data/orcamento-demo.db")
BRAPI_TOKEN = os.environ.get("BRAPI_TOKEN", "")

EXTENSOES_IMAGEM = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


# A cor escolhida para uma categoria ou uma meta é gravada como texto e a
# tela a coloca dentro de um atributo style. Um valor com aspa fecharia o
# atributo e abriria outro no lugar, então o que não for uma cor deixa de
# ser gravado como se fosse — o app volta a decidir a cor sozinho.
RE_COR = re.compile(r"^#[0-9A-Fa-f]{3,8}$")


def cor_valida(cor):
    cor = (cor or "").strip()
    return cor if RE_COR.match(cor) else None



def caminho_em(pasta, nome):
    """Devolve o caminho de `nome` dentro de `pasta`, ou None se ele escapar.

    Todo arquivo que o app grava ou serve — comprovante, foto, holerite,
    backup — tem o nome montado a partir de algo que veio de fora. O
    basename já corta o "../", mas ele sozinho é uma regra espalhada por
    vários pontos do código, e basta um esquecer. Aqui a conferência é
    posicional: resolve o caminho e só aceita se ele continuar debaixo da
    pasta, o que também pega link simbólico apontando para fora.
    """
    nome = os.path.basename(nome or "")
    if not nome or nome in (".", ".."):
        return None
    # Quem monta o caminho é o safe_join do werkzeug, que devolve None em vez de
    # um caminho quando o nome tenta sair da pasta. A conferência posicional
    # continua depois porque o safe_join olha o texto, e não onde o caminho
    # realmente vai dar — link simbólico apontando para fora passaria por ele.
    caminho = safe_join(pasta, nome)
    if not caminho:
        return None
    base = os.path.realpath(pasta)
    alvo = os.path.realpath(caminho)
    if alvo == base or os.path.commonpath([base, alvo]) != base:
        return None
    return alvo

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

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
# Precisa bater exatamente com o URI cadastrado no Google Cloud Console.
# Sem padrão de propósito: cada instalação usa o próprio domínio, definido
# em GOOGLE_REDIRECT_URI (veja o .env.example).
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "")
# O redirect URI entra na conta: sem ele o fluxo quebraria só no meio do
# caminho, com um erro do Google difícil de entender. Melhor o botão nem
# aparecer enquanto a configuração estiver incompleta.
GOOGLE_LOGIN_HABILITADO = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI)

oauth = OAuth(app)
if GOOGLE_LOGIN_HABILITADO:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

ROTAS_PUBLICAS = {
    "/login", "/api/login", "/registro", "/api/registro", "/manifest.json", "/sw.js", "/favicon.ico",
    "/api/auth/google/login", "/api/auth/google/callback", "/api/auth/google/status",
}


# Rotas de escrita liberadas pra usuário somente-leitura: alternar modo demo,
# sair, e mexer na própria conta (senha/foto) — não conta como "editar dados".
# A conta própria entra por sufixo, não pelo prefixo /api/usuarios/: liberar a
# subárvore inteira deixava a conta somente-leitura chamar /somente-leitura em si
# mesma e se promover a escrita total. As rotas de senha e foto já conferem
# sozinhas que o alvo é o próprio usuário.
PREFIXOS_ESCRITA_LIBERADOS_LEITURA = ("/api/demo", "/api/logout")
SUFIXOS_CONTA_PROPRIA_LIBERADOS = ("/senha", "/foto")


def escrita_liberada_para_leitura(caminho):
    if caminho.startswith(PREFIXOS_ESCRITA_LIBERADOS_LEITURA):
        return True
    return caminho.startswith("/api/usuarios/") and caminho.endswith(SUFIXOS_CONTA_PROPRIA_LIBERADOS)


@app.before_request
def exigir_login():
    if request.path.startswith("/static/") or request.path in ROTAS_PUBLICAS:
        return None
    # O webhook é chamado pela Pluggy, que não tem sessão. Ele não fica aberto:
    # a própria URL carrega um segredo, conferido na rota em tempo constante.
    if request.path.startswith("/api/pluggy/webhook/"):
        return None
    if request.path == "/":
        if not session.get("usuario_id"):
            return redirect("/login")
        return None
    if request.path.startswith("/api/"):
        if not session.get("usuario_id"):
            return jsonify({"erro": msg("não autenticado")}), 401
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and not em_demo()
            and session.get("somente_leitura")
            and not escrita_liberada_para_leitura(request.path)
        ):
            return jsonify({"erro": msg("acesso somente leitura — essa conta não pode fazer alterações")}), 403
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


# ---------------- Idioma das mensagens do servidor ----------------
#
# As mensagens de erro nascem em português no código e chegam à tela como
# vieram. A tela não pode traduzi-las: ela recebe uma frase pronta, e casar
# frase por frase no cliente daria uma tabela duplicada que sai de sincronia na
# primeira vez que alguém mudar o texto aqui.
#
# Então quem traduz é o servidor. O cliente manda o idioma no cabeçalho
# X-Idioma; sem ele, tudo continua em português, que é o padrão.

MENSAGENS_EN = {
    "não autenticado": "not authenticated",
    "acesso somente leitura — essa conta não pode fazer alterações":
        "read-only access — this account cannot make changes",
    "usuário ou senha inválidos": "wrong username or password",
    "esse usuário já existe": "that username already exists",
    "nome, usuário e senha (mín. 4 caracteres) são obrigatórios":
        "name, username and password (min. 4 characters) are required",
    "nome da casa, seu nome, usuário e senha (mín. 4 caracteres) são obrigatórios":
        "household name, your name, username and password (min. 4 characters) are required",
    "a nova senha precisa ter pelo menos 4 caracteres":
        "the new password must be at least 4 characters",
    "só o administrador pode adicionar usuários": "only the administrator can add users",
    "só o administrador pode redefinir a senha de outro usuário":
        "only the administrator can reset another user's password",
    "nome é obrigatório": "name is required",
    "conta não encontrada": "account not found",
    "cartão não encontrado": "card not found",
    "categoria não encontrada": "category not found",
    "meta não encontrada": "goal not found",
    "consignado não encontrado": "loan not found",
    "lançamento não encontrado": "entry not found",
    "regra não encontrada": "rule not found",
    "backup não encontrado": "backup not found",
    "comprovante não encontrado": "receipt not found",
    "casa não encontrada": "household not found",
    "já existe uma conta com esse nome": "an account with that name already exists",
    "as contas de origem e destino devem ser diferentes":
        "source and destination accounts must be different",
    "conta de origem e destino são obrigatórias":
        "source and destination accounts are required",
    "conta de origem inválida": "invalid source account",
    "conta de destino inválida": "invalid destination account",
    "conta inválida": "invalid account",
    "categoria e limite são obrigatórios": "category and limit are required",
    "valor alvo inválido": "invalid target amount",
    "nome e valor alvo são obrigatórios": "name and target amount are required",
    "informe um valor": "enter an amount",
    "estado inválido": "invalid status",
    "arquivo sem nome": "file has no name",
    # Open Finance
    "esta casa ainda não cadastrou as credenciais do Meu Pluggy":
        "this household has not set up its Meu Pluggy credentials yet",
    "a Pluggy recusou as credenciais — confira o Client ID e o Client Secret":
        "Pluggy rejected the credentials — check the Client ID and Client Secret",
    "informe o Client ID e o Client Secret do Meu Pluggy":
        "enter the Meu Pluggy Client ID and Client Secret",
    "só o administrador da casa pode configurar o Open Finance":
        "only the household administrator can set up Open Finance",
    "só o administrador da casa pode remover o Open Finance":
        "only the household administrator can remove Open Finance",
    "só o administrador da casa pode configurar o webhook":
        "only the household administrator can set up the webhook",
    "informe o Item ID da conexão": "enter the connection's Item ID",
    "a Pluggy não encontrou esse Item ID": "Pluggy could not find that Item ID",
    "esse Item já está registrado em outra casa":
        "that Item is already registered to another household",
    "conexão não encontrada nesta casa": "connection not found in this household",
    "conta não encontrada nesta casa": "account not found in this household",
    "essa conta não é desta casa": "that account does not belong to this household",
    "esse cartão não é desta casa": "that card does not belong to this household",
    "escolha uma conta ou um cartão, não os dois":
        "choose an account or a card, not both",
    "essa conta é sincronizada pelo Open Finance — o saldo inicial é calculado a partir do extrato":
        "this account is synced through Open Finance — the starting balance is calculated from the statement",
    "o modo demonstração não conecta em banco de verdade":
        "demo mode does not connect to a real bank",
    "a Pluggy só aceita HTTPS e recusa localhost":
        "Pluggy only accepts HTTPS and rejects localhost",
    "não autorizado": "not authorised",
    "nada para classificar": "nothing to classify",
    "escolha uma categoria, ou marque como transferência":
        "choose a category, or mark it as a transfer",
    "informe nome, categoria e ao menos uma condição":
        "enter a name, a category and at least one condition",
    "nenhum desses lançamentos é desta casa":
        "none of those entries belong to this household",
    "desligue o modo demonstração antes de restaurar":
        "turn demo mode off before restoring",
}


def idioma_da_requisicao():
    """Idioma pedido pelo cliente. Fora de uma requisição — nos ciclos de
    segundo plano — não há cabeçalho, e português é o padrão."""
    if not has_request_context():
        return "pt"
    return "en" if (request.headers.get("X-Idioma") or "").lower() == "en" else "pt"


def msg(texto):
    """Traduz uma mensagem do servidor. Sem tradução, devolve o original —
    mensagem em português é melhor que mensagem faltando."""
    if idioma_da_requisicao() != "en":
        return texto
    return MENSAGENS_EN.get(texto, texto)


def caminho_banco_atual():
    return DEMO_DB_PATH if em_demo() else DB_PATH


def uid():
    """ID do usuário logado. Cada usuário só enxerga os próprios dados."""
    return 1 if em_demo() else session["usuario_id"]


def pertence_ao_usuario(conn, tabela, item_id):
    row = conn.execute(f"SELECT usuario_id FROM {tabela} WHERE id = ?", (item_id,)).fetchone()
    return row is not None and row["usuario_id"] == uid()


def minha_casa_id(conn):
    row = conn.execute("SELECT casa_id FROM usuarios WHERE id = ?", (uid(),)).fetchone()
    return row["casa_id"] if row else None


def eh_administrador(conn):
    """O 'administrador' de uma casa é o primeiro usuário dela (id mais baixo) —
    mesma convenção já usada no bootstrap inicial do app."""
    row = conn.execute(
        "SELECT MIN(id) as menor FROM usuarios WHERE casa_id = ?", (minha_casa_id(conn),)
    ).fetchone()
    return row is not None and row["menor"] == uid()


CATEGORIAS_RECEITA_PADRAO = ["Salário", "Vale/Benefícios", "Freelance", "Pix recebido",
                              "Venda de produtos", "Rendimentos", "Outros"]
CATEGORIAS_DESPESA_PADRAO = ["Mercado", "Combustível", "Aluguel", "Energia", "Água",
                              "Internet", "Alimentação", "Lazer", "Compras", "Transporte", "Outros"]


def get_db():
    # timeout de 30s (o padrão são 5): os dois ciclos de investimento rodam em
    # threads próprias e escrevem em rajada, e no SQLite quem escreve tranca o
    # banco inteiro. Sem essa folga, uma requisição que caísse junto de uma
    # gravação longa morria com "database is locked" em vez de só esperar.
    conn = sqlite3.connect(caminho_banco_atual(), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")


def aplicar_migracoes(conn):
    """Aplica em ordem os arquivos .sql de migrations/ que ainda não rodaram nesse
    banco, registrando cada um numa tabela de controle (schema_migrations) — assim
    dá pra saber exatamente em que versão de schema o banco está, e cada mudança
    futura vira um arquivo novo em vez de mexer direto no código já existente."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL UNIQUE,
            aplicada_em TEXT NOT NULL
        )"""
    )
    conn.commit()
    if not os.path.isdir(MIGRATIONS_DIR):
        return
    ja_aplicadas = {r["nome"] for r in conn.execute("SELECT nome FROM schema_migrations").fetchall()}
    for nome in sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql")):
        if nome in ja_aplicadas:
            continue
        with open(os.path.join(MIGRATIONS_DIR, nome), encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.execute(
            "INSERT INTO schema_migrations (nome, aplicada_em) VALUES (?, ?)",
            (nome, datetime.now().isoformat()),
        )
        conn.commit()


def init_db(caminho=None, criar_usuario_inicial=True):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(COMPROVANTES_DIR, exist_ok=True)
    os.makedirs(FOTOS_DIR, exist_ok=True)
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    os.makedirs(HOLERITES_DIR, exist_ok=True)
    if caminho:
        conn = sqlite3.connect(caminho)
        conn.row_factory = sqlite3.Row
    else:
        conn = get_db()
    aplicar_migracoes(conn)
    migrar_categorias_por_casa(conn)
    remover_recorrentes_duplicados(conn)
    migrar_contas_de_texto_livre(conn)
    if criar_usuario_inicial:
        bootstrap_usuario_inicial(conn)
    garantir_categorias_padrao(conn)
    migrar_contas_nome_unico_por_usuario(conn)
    migrar_series_de_recorrencia(conn)
    migrar_config_consignados_para_casas(conn)
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


def migrar_categorias_por_casa(conn):
    """Categorias eram globais (compartilhadas por todo mundo no app); agora cada
    casa tem as próprias. Precisa recriar a tabela porque o SQLite não altera um
    UNIQUE já existente — mesma técnica usada em migrar_contas_nome_unico_por_usuario."""
    schema = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='categorias'"
    ).fetchone()
    precisa_recriar = schema and "casa_id" not in schema["sql"]
    if not precisa_recriar:
        return

    primeira_casa = conn.execute("SELECT id FROM casas ORDER BY id LIMIT 1").fetchone()
    casa_id_padrao = primeira_casa["id"] if primeira_casa else None

    conn.execute("BEGIN")
    try:
        conn.execute(
            """CREATE TABLE categorias_nova (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                tipo TEXT NOT NULL,
                cor TEXT,
                casa_id INTEGER,
                UNIQUE(nome, tipo, casa_id)
            )"""
        )
        conn.execute(
            "INSERT INTO categorias_nova (id, nome, tipo, cor, casa_id) "
            "SELECT id, nome, tipo, cor, ? FROM categorias",
            (casa_id_padrao,),
        )
        conn.execute("DROP TABLE categorias")
        conn.execute("ALTER TABLE categorias_nova RENAME TO categorias")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def garantir_categorias_padrao(conn):
    """Toda casa (nova ou recém-migrada) recebe seu próprio conjunto de
    categorias padrão, uma única vez."""
    casas_sem_categoria = conn.execute(
        "SELECT id FROM casas WHERE id NOT IN ("
        "  SELECT DISTINCT casa_id FROM categorias WHERE casa_id IS NOT NULL"
        ")"
    ).fetchall()
    for c in casas_sem_categoria:
        for nome in CATEGORIAS_RECEITA_PADRAO:
            conn.execute(
                "INSERT OR IGNORE INTO categorias (nome, tipo, casa_id) VALUES (?, 'receita', ?)",
                (nome, c["id"]),
            )
        for nome in CATEGORIAS_DESPESA_PADRAO:
            conn.execute(
                "INSERT OR IGNORE INTO categorias (nome, tipo, casa_id) VALUES (?, 'despesa', ?)",
                (nome, c["id"]),
            )
    conn.commit()


def migrar_config_consignados_para_casas(conn):
    """A configuração de Consignados era um arquivo .json solto (valia pro app
    inteiro); agora é uma coluna por casa. Lê o arquivo antigo uma vez, se existir,
    e aplica o valor na primeira casa — depois o arquivo não é mais usado."""
    caminho = os.path.join(os.path.dirname(DB_PATH), "consignados-config.json")
    if not os.path.exists(caminho):
        return
    try:
        with open(caminho) as f:
            config_antiga = json.load(f)
    except (ValueError, OSError):
        config_antiga = {}
    primeira_casa = conn.execute("SELECT id FROM casas ORDER BY id LIMIT 1").fetchone()
    if primeira_casa:
        conn.execute(
            "UPDATE casas SET consignados_habilitado = ? WHERE id = ?",
            (1 if config_antiga.get("habilitado") else 0, primeira_casa["id"]),
        )
        conn.commit()
    os.remove(caminho)


def bootstrap_usuario_inicial(conn):
    """Cria o primeiro usuário no primeiro start, e garante que todo dado tenha um dono."""
    ja_tem_usuarios = conn.execute("SELECT COUNT(*) as n FROM usuarios").fetchone()["n"]
    if not ja_tem_usuarios:
        admin_user = os.environ.get("ADMIN_USERNAME", "admin")
        admin_pass = os.environ.get("ADMIN_PASSWORD")
        senha_foi_gerada = not admin_pass
        if not admin_pass:
            admin_pass = secrets.token_urlsafe(9)
        cur = conn.execute(
            "INSERT INTO casas (nome, criado_em) VALUES (?, ?)",
            ("Minha Casa", datetime.now().isoformat()),
        )
        casa_id = cur.lastrowid
        conn.execute(
            "INSERT INTO usuarios (nome, username, senha_hash, casa_id, criado_em) VALUES (?, ?, ?, ?, ?)",
            ("Administrador", admin_user, generate_password_hash(admin_pass), casa_id, datetime.now().isoformat()),
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


def remover_recorrentes_duplicados(conn):
    """Bug já corrigido: uma corrida entre requisições paralelas podia duplicar a
    materialização de um lançamento recorrente no mesmo mês (garantir_recorrentes
    fazia 'verifica se existe, senão insere' sem trava). Mantém a ocorrência mais
    antiga de cada grupo/mês/usuário e remove as repetidas."""
    duplicados = conn.execute(
        "SELECT grupo_recorrencia, mes, usuario_id FROM lancamentos "
        "WHERE grupo_recorrencia IS NOT NULL "
        "GROUP BY grupo_recorrencia, mes, usuario_id HAVING COUNT(*) > 1"
    ).fetchall()
    for d in duplicados:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM lancamentos WHERE grupo_recorrencia = ? AND mes = ? AND usuario_id IS ? "
            "ORDER BY criado_em, id",
            (d["grupo_recorrencia"], d["mes"], d["usuario_id"]),
        ).fetchall()]
        for id_extra in ids[1:]:
            conn.execute("DELETE FROM lancamentos WHERE id = ?", (id_extra,))
    conn.commit()


def mes_anterior(mes):
    ano, m = map(int, mes.split("-"))
    if m == 1:
        return f"{ano-1}-12"
    return f"{ano}-{m-1:02d}"


MESES_ABREV = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}


def _sem_acento(texto):
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def _num_br(texto):
    """Converte um número no formato brasileiro ('8.990,46') para float."""
    texto = texto.strip().strip(".,")
    try:
        return round(float(texto.replace(".", "").replace(",", ".")), 2)
    except ValueError:
        return None


def _extrair_valor_apos(texto_norm, rotulo, padrao_valor, distancia=60):
    """Procura um rótulo (ex: 'total liquido a receber') e captura o valor
    que aparece logo em seguida no texto extraído do PDF."""
    m = re.search(re.escape(rotulo) + r"[\s\S]{0,%d}?(%s)" % (distancia, padrao_valor), texto_norm)
    return m.group(1) if m else None


# Rótulos de linhas de recapitulação/base de cálculo que aparecem misturadas com os
# itens de verdade na tabela do contracheque, mas não são um provento ou desconto —
# variam entre folha mensal e folha de férias, então a lista cobre os dois casos.
LABELS_HOLERITE_IGNORAR = (
    "base inss", "base fgts", "base liquida irrf", "base calc",
    "salario contratual", "remuneracao fixa mensal", "remuneracao extra",
    "res adicional assiduidade", "fgts mes", "fgts 13 salario",
    "ind deducao simplificada", "num dias", "num meses", "qtd adicional",
    "ano do calculo", "ano da atual", "valor nao descontado parcela credito",
    "ao orto custo", "am custo empresa", "am saldo devedor", "sal contrib inss",
    "reducao do irrf",
)
PALAVRAS_HOLERITE_DESCONTO = ("desconto", "inss", "irrf", "consignado", "contribuicao", "seguro", "amil", "afeb")
PADRAO_VALOR_LINHA_HOLERITE = re.compile(r"^(.+?)\s+(\d[\d.]*,\d{2})\s*$")


def _extrair_itens_holerite(texto):
    """Lê as linhas de proventos/descontos da tabela do contracheque (não só os
    totais), pra dar pra ver depois o que exatamente compôs cada valor."""
    norm = _sem_acento(texto).lower()
    inicio = norm.find("descricao")
    fim = norm.find("total de proventos")
    if inicio == -1 or fim == -1 or fim <= inicio:
        return []
    linhas_orig = texto[inicio:fim].splitlines()
    linhas_norm = norm[inicio:fim].splitlines()

    itens = []
    for orig, normed in zip(linhas_orig, linhas_norm):
        normed = normed.strip()
        if not normed or normed.startswith("descricao"):
            continue
        if any(rotulo in normed for rotulo in LABELS_HOLERITE_IGNORAR):
            continue
        # "Valor Não Descontado Parcela Crédito Consignado N" quebra em duas linhas —
        # a continuação ("Consignado 4", "Consignado 5"...) não é um desconto de verdade,
        # é só informativo. Diferente de "Desconto Credito eConsignado N" (item real),
        # que nunca começa a linha com a palavra solta "consignado".
        if normed.startswith("consignado"):
            continue
        m = PADRAO_VALOR_LINHA_HOLERITE.match(orig.strip())
        if not m:
            continue
        valor = _num_br(m.group(2))
        if valor is None:
            continue
        tipo = "desconto" if any(p in normed for p in PALAVRAS_HOLERITE_DESCONTO) else "provento"
        itens.append({"descricao": " ".join(m.group(1).split()), "valor": valor, "tipo": tipo})
    return itens


def extrair_dados_holerite(texto):
    """Lê os campos principais de um contracheque em PDF. O parsing é best-effort —
    a tela de importação sempre deixa o usuário revisar e corrigir os valores."""
    norm = _sem_acento(texto).lower()
    dados = {"referencia": None, "recebido_em": None, "total_proventos": None,
             "total_descontos": None, "total_liquido": None, "adiantamento": None,
             "eh_ferias": False, "itens": []}

    v = _extrair_valor_apos(norm, "folha", r"\S+")
    if v and "feria" in v:
        dados["eh_ferias"] = True

    v = _extrair_valor_apos(norm, "referencia", r"[a-z]{3}/\d{4}")
    if v:
        abrev, ano = v.split("/")
        num_mes = MESES_ABREV.get(abrev)
        if num_mes:
            dados["referencia"] = f"{ano}-{num_mes:02d}"

    v = _extrair_valor_apos(norm, "recebido em", r"\d{2}/\d{2}/\d{4}")
    if v:
        dia, mes_v, ano = v.split("/")
        dados["recebido_em"] = f"{ano}-{mes_v}-{dia}"

    v = _extrair_valor_apos(norm, "total de proventos", r"[\d\.,]+")
    if v:
        dados["total_proventos"] = _num_br(v)

    v = _extrair_valor_apos(norm, "total de descontos", r"[\d\.,]+")
    if v:
        dados["total_descontos"] = _num_br(v)

    v = _extrair_valor_apos(norm, "total liquido a receber", r"[\d\.,]+")
    if v:
        dados["total_liquido"] = _num_br(v)

    v = _extrair_valor_apos(norm, "adiantamento quinzenal", r"[\d\.,]+")
    if v:
        dados["adiantamento"] = _num_br(v)

    dados["itens"] = _extrair_itens_holerite(texto)

    return dados


# O DANFE-NFC-e imprime cada item em duas linhas (padrão comum a muitos PDVs/ERPs no
# Brasil, por causa da Lei 12.741/2012 — "Trib:" aparece em quase toda nota fiscal do país):
#   1 16364 PAO DE ALHO COPACOL 400GR TRA
#   1UN X 15,99  Trib: 0,00                                15,99
# Em cupom térmico fotografado o OCR quase sempre erra a coluna do número/código do
# item (primeira linha) — por isso o regex só exige a segunda linha (qtd/preço), que
# sai muito mais estável, e pega a descrição da linha anterior por posição.
PADRAO_LINHA_PRECO_ITEM = re.compile(
    r"(?P<qtd>[\d]*[.,]?[\d]*)\s*[a-zA-Z]{1,3}\s*[xX]\s*(?P<valor_unit>[\d]+[.,][\d]+)"
    r"\s+trib:?\s*\S+\s+(?P<valor_total>[\d]+[.,][\d]+)",
    re.IGNORECASE,
)


def _limpar_descricao_item(linha):
    return re.sub(r"^[^A-Za-zÀ-ÿ]*\d*\s*", "", linha).strip()


def extrair_dados_nota_fiscal(texto, chave_acesso_qr=None):
    """Lê loja, data, valor total e itens de um cupom fiscal (NFC-e) a partir do
    texto reconhecido por OCR. Assim como no holerite, é best-effort — a tela de
    importação sempre deixa o usuário revisar e corrigir tudo antes de lançar."""
    linhas = [l.strip() for l in texto.splitlines() if l.strip()]
    loja = linhas[0] if linhas else None

    m = re.search(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", texto)
    cnpj = m.group(0) if m else None

    m = re.search(r"\d{2}/\d{2}/\d{4}", texto)
    data = None
    if m:
        dia, mes_v, ano = m.group(0).split("/")
        data = f"{ano}-{mes_v}-{dia}"

    norm = _sem_acento(texto).lower()
    v = _extrair_valor_apos(norm, "valor total r$", r"[\d\.,]+")
    valor_total = _num_br(v) if v else None

    itens = []
    for i, linha in enumerate(linhas):
        item = PADRAO_LINHA_PRECO_ITEM.search(linha)
        if not item:
            continue
        descricao = _limpar_descricao_item(linhas[i - 1]) if i > 0 else ""
        itens.append({
            "descricao": descricao or "Item",
            "qtd": item.group("qtd") or "1",
            "valor_unitario": _num_br(item.group("valor_unit")),
            "valor_total": _num_br(item.group("valor_total")),
        })

    if valor_total is None and itens:
        valor_total = round(sum(i["valor_total"] or 0 for i in itens), 2)

    chave_acesso = chave_acesso_qr
    if not chave_acesso:
        m = re.search(r"chave de acesso[\s\S]{0,20}?((?:\d[\s.]*){44})", norm)
        if m:
            digitos = re.sub(r"\D", "", m.group(1))
            if len(digitos) == 44:
                chave_acesso = digitos

    return {
        "loja": loja, "cnpj": cnpj, "data": data, "valor_total": valor_total,
        "chave_acesso": chave_acesso, "itens": itens,
    }


def totais_do_mes(conn, mes_ref, usuario_id):
    rows = conn.execute(
        "SELECT tipo, pago, COALESCE(SUM(valor),0) as total FROM lancamentos "
        "WHERE mes = ? AND eh_transferencia = 0 AND usuario_id = ? GROUP BY tipo, pago",
        (mes_ref, usuario_id),
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
            """INSERT OR IGNORE INTO lancamentos
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


@app.route("/registro")
def registro_page():
    if session.get("usuario_id"):
        return redirect("/")
    return send_from_directory("static", "registro.html")


@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json")


@app.route("/favicon.ico")
def favicon():
    """Navegador pede /favicon.ico sozinho, mesmo com os <link> declarados —
    sem esta rota o pedido virava 404 e alguns caíam no ícone antigo em cache."""
    return send_from_directory("static/icones", "icone-32.png", mimetype="image/png")


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
        return jsonify({"erro": msg("usuário ou senha inválidos")}), 401
    session.clear()
    session["usuario_id"] = row["id"]
    session["usuario_nome"] = row["nome"]
    session["somente_leitura"] = bool(row["somente_leitura"])
    session.permanent = True
    return jsonify({
        "ok": True,
        "usuario": {
            "id": row["id"], "nome": row["nome"], "username": row["username"],
            "somente_leitura": bool(row["somente_leitura"]),
        },
    })


@app.route("/api/registro", methods=["POST"])
def registro():
    """Cria uma casa nova (isolada de todas as outras) e o primeiro usuário dela,
    que já nasce administrador. Rota pública — não exige login."""
    data = request.get_json(force=True)
    nome_casa = (data.get("nome_casa") or "").strip()
    nome = (data.get("nome") or "").strip()
    username = (data.get("username") or "").strip().lower()
    senha = data.get("senha") or ""
    if not nome_casa or not nome or not username or len(senha) < 4:
        return jsonify({"erro": msg("nome da casa, seu nome, usuário e senha (mín. 4 caracteres) são obrigatórios")}), 400

    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO casas (nome, criado_em) VALUES (?, ?)",
            (nome_casa, datetime.now().isoformat()),
        )
        casa_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO usuarios (nome, username, senha_hash, casa_id, criado_em) VALUES (?, ?, ?, ?, ?)",
            (nome, username, generate_password_hash(senha), casa_id, datetime.now().isoformat()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"erro": msg("esse usuário já existe")}), 400

    garantir_categorias_padrao(conn)
    usuario_id = cur.lastrowid
    conn.close()

    session.clear()
    session["usuario_id"] = usuario_id
    session["usuario_nome"] = nome
    session["somente_leitura"] = False
    session.permanent = True
    return jsonify({"ok": True}), 201


def gerar_username_disponivel(conn, base):
    base = re.sub(r"[^a-z0-9]", "", base.lower()) or "usuario"
    username = base
    sufixo = 1
    while conn.execute("SELECT 1 FROM usuarios WHERE username = ?", (username,)).fetchone():
        sufixo += 1
        username = f"{base}{sufixo}"
    return username


@app.route("/api/auth/google/status")
def auth_google_status():
    return jsonify({"habilitado": GOOGLE_LOGIN_HABILITADO})


@app.route("/api/auth/google/login")
def auth_google_login():
    if not GOOGLE_LOGIN_HABILITADO:
        return jsonify({"erro": msg("login com Google não está configurado nesse servidor")}), 501
    return oauth.google.authorize_redirect(GOOGLE_REDIRECT_URI)


@app.route("/api/auth/google/vincular")
def auth_google_vincular():
    """Pra quem já está logado (ex: com usuário/senha) e quer poder entrar
    também pelo Google, na MESMA conta — não cria casa nova nenhuma."""
    if not session.get("usuario_id"):
        return redirect("/login")
    if not GOOGLE_LOGIN_HABILITADO:
        return redirect("/?erro=google_indisponivel")
    session["google_vincular_usuario_id"] = session["usuario_id"]
    return oauth.google.authorize_redirect(GOOGLE_REDIRECT_URI)


@app.route("/api/auth/google/callback")
def auth_google_callback():
    if not GOOGLE_LOGIN_HABILITADO:
        return redirect("/login?erro=google_indisponivel")
    try:
        token = oauth.google.authorize_access_token()
        dados_google = token.get("userinfo") or oauth.google.userinfo(token=token)
    except Exception:
        import traceback
        print("[google-login] falhou:", flush=True)
        traceback.print_exc()
        return redirect("/login?erro=google_falhou")

    google_id = dados_google.get("sub")
    email = dados_google.get("email")
    nome = dados_google.get("name") or email or "Usuário Google"
    if not google_id:
        return redirect("/login?erro=google_falhou")

    conn = get_db()
    vincular_usuario_id = session.pop("google_vincular_usuario_id", None)

    if vincular_usuario_id:
        # Fluxo de "vincular": já estava logado, só associa essa conta Google
        # à conta que já existe — não cria usuário nem casa nova.
        em_uso_por_outro = conn.execute(
            "SELECT id FROM usuarios WHERE google_id = ? AND id != ?",
            (google_id, vincular_usuario_id),
        ).fetchone()
        if em_uso_por_outro:
            conn.close()
            return redirect("/?erro=google_ja_vinculado")
        conn.execute(
            "UPDATE usuarios SET google_id = ?, email = ? WHERE id = ?",
            (google_id, email, vincular_usuario_id),
        )
        conn.commit()
        conn.close()
        return redirect("/?vinculado=google")

    usuario = conn.execute("SELECT * FROM usuarios WHERE google_id = ?", (google_id,)).fetchone()

    if usuario:
        usuario_id, usuario_nome, somente_leitura = usuario["id"], usuario["nome"], bool(usuario["somente_leitura"])
        conn.close()
    else:
        # Primeiro login com essa conta Google: nasce uma casa nova, exatamente
        # como no cadastro por usuário/senha — ninguém entra "dentro" da casa de
        # outra pessoa sem ser convidado explicitamente por lá dentro.
        username = gerar_username_disponivel(conn, (email or nome).split("@")[0])
        cur = conn.execute(
            "INSERT INTO casas (nome, criado_em) VALUES (?, ?)",
            (f"Casa de {nome}", datetime.now().isoformat()),
        )
        casa_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO usuarios (nome, username, senha_hash, google_id, email, casa_id, criado_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (nome, username, generate_password_hash(secrets.token_urlsafe(32)), google_id, email,
             casa_id, datetime.now().isoformat()),
        )
        conn.commit()
        garantir_categorias_padrao(conn)
        usuario_id, usuario_nome, somente_leitura = cur.lastrowid, nome, False
        conn.close()

    session.clear()
    session["usuario_id"] = usuario_id
    session["usuario_nome"] = usuario_nome
    session["somente_leitura"] = somente_leitura
    session.permanent = True
    return redirect("/")


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/usuario-atual", methods=["GET"])
def usuario_atual():
    conn = get_db()
    row = conn.execute(
        "SELECT id, nome, username, foto, somente_leitura, google_id, email FROM usuarios WHERE id = ?",
        (uid(),),
    ).fetchone()
    if not row:
        conn.close()
        session.clear()
        return jsonify({"erro": msg("não autenticado")}), 401
    d = dict(row)
    d["somente_leitura"] = bool(d["somente_leitura"])
    d["eh_administrador"] = eh_administrador(conn)
    conn.close()
    return jsonify(d)


@app.route("/api/usuarios", methods=["GET"])
def listar_usuarios():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, nome, username, criado_em, foto, somente_leitura FROM usuarios "
        "WHERE casa_id = ? ORDER BY nome",
        (minha_casa_id(conn),),
    ).fetchall()
    conn.close()
    usuarios = [dict(r) for r in rows]
    for u in usuarios:
        u["somente_leitura"] = bool(u["somente_leitura"])
    return jsonify(usuarios)


@app.route("/api/usuarios", methods=["POST"])
def criar_usuario():
    data = request.get_json(force=True)
    nome = data.get("nome", "").strip()
    username = (data.get("username") or "").strip().lower()
    senha = data.get("senha") or ""
    somente_leitura = bool(data.get("somente_leitura"))
    if not nome or not username or len(senha) < 4:
        return jsonify({"erro": msg("nome, usuário e senha (mín. 4 caracteres) são obrigatórios")}), 400
    conn = get_db()
    # Quem entra na casa decide quem mais entra: sem isso, qualquer conta com
    # escrita podia criar outra pessoa com acesso aos dados da família.
    if not eh_administrador(conn):
        conn.close()
        return jsonify({"erro": msg("só o administrador pode adicionar usuários")}), 403
    try:
        conn.execute(
            "INSERT INTO usuarios (nome, username, senha_hash, casa_id, criado_em, somente_leitura) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (nome, username, generate_password_hash(senha), minha_casa_id(conn),
             datetime.now().isoformat(), int(somente_leitura)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"erro": msg("esse usuário já existe")}), 400
    conn.close()
    return jsonify({"ok": True}), 201


@app.route("/api/usuarios/<int:item_id>/somente-leitura", methods=["PUT"])
def alternar_somente_leitura(item_id):
    data = request.get_json(force=True)
    somente_leitura = bool(data.get("somente_leitura"))
    conn = get_db()
    if not eh_administrador(conn):
        conn.close()
        return jsonify({"erro": msg("só o administrador pode alterar essa configuração")}), 403
    alvo = conn.execute("SELECT casa_id FROM usuarios WHERE id = ?", (item_id,)).fetchone()
    if not alvo or alvo["casa_id"] != minha_casa_id(conn):
        conn.close()
        return jsonify({"erro": msg("usuário não encontrado")}), 404
    # O administrador não pode se limitar: esta rota é a única forma de desfazer e
    # ela mesma é escrita, então ele ficaria trancado fora da própria casa.
    if item_id == uid():
        conn.close()
        return jsonify({"erro": msg("o administrador não pode limitar a própria conta")}), 403
    conn.execute("UPDATE usuarios SET somente_leitura = ? WHERE id = ?", (int(somente_leitura), item_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/usuarios/<int:item_id>/senha", methods=["PUT"])
def trocar_senha(item_id):
    if item_id != session.get("usuario_id"):
        return jsonify({"erro": msg("só é possível trocar a própria senha")}), 403
    data = request.get_json(force=True)
    senha_atual = data.get("senha_atual") or ""
    nova_senha = data.get("nova_senha") or ""
    if len(nova_senha) < 4:
        return jsonify({"erro": msg("a nova senha precisa ter pelo menos 4 caracteres")}), 400
    conn = get_db()
    row = conn.execute("SELECT * FROM usuarios WHERE id = ?", (item_id,)).fetchone()
    if not row or not check_password_hash(row["senha_hash"], senha_atual):
        conn.close()
        return jsonify({"erro": msg("senha atual incorreta")}), 400
    conn.execute("UPDATE usuarios SET senha_hash = ? WHERE id = ?", (generate_password_hash(nova_senha), item_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/usuarios/<int:item_id>/senha-admin", methods=["PUT"])
def trocar_senha_como_admin(item_id):
    """O administrador redefine a senha de outro membro da casa — diferente de
    /senha, não pede a senha atual porque quem está trocando não é o dono dela
    (ex: esqueceu a senha e pediu pro administrador resetar)."""
    data = request.get_json(force=True)
    nova_senha = data.get("nova_senha") or ""
    if len(nova_senha) < 4:
        return jsonify({"erro": msg("a nova senha precisa ter pelo menos 4 caracteres")}), 400
    conn = get_db()
    if not eh_administrador(conn):
        conn.close()
        return jsonify({"erro": msg("só o administrador pode redefinir a senha de outro usuário")}), 403
    if not pertence_a_minha_casa(conn, "usuarios", item_id):
        conn.close()
        return jsonify({"erro": msg("usuário não encontrado")}), 404
    conn.execute("UPDATE usuarios SET senha_hash = ? WHERE id = ?", (generate_password_hash(nova_senha), item_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/casa", methods=["GET"])
def minha_casa():
    """Dados da aba Casa: nome da casa e quem são os membros — visível a
    qualquer usuário da casa, não só ao administrador. As ações de gestão
    (trocar senha alheia, ver contas de outro membro, convidar) é que ficam
    restritas ao administrador, cada uma na própria rota."""
    conn = get_db()
    casa_id = minha_casa_id(conn)
    casa = conn.execute("SELECT id, nome FROM casas WHERE id = ?", (casa_id,)).fetchone()
    membros = conn.execute(
        "SELECT id, nome, username, foto, somente_leitura FROM usuarios "
        "WHERE casa_id = ? ORDER BY id",
        (casa_id,),
    ).fetchall()
    conn.close()
    if not membros:
        return jsonify({"erro": msg("casa não encontrada")}), 404
    admin_id = membros[0]["id"]
    resultado = {
        "id": casa["id"],
        "nome": casa["nome"],
        "membros": [
            {
                "id": m["id"],
                "nome": m["nome"],
                "username": m["username"],
                "foto": m["foto"],
                "somente_leitura": bool(m["somente_leitura"]),
                "eh_administrador": m["id"] == admin_id,
                "eh_voce": m["id"] == uid(),
            }
            for m in membros
        ],
    }
    return jsonify(resultado)


@app.route("/api/casa/usuarios/<int:item_id>/contas", methods=["GET"])
def contas_de_membro_da_casa(item_id):
    """O administrador confere as contas de qualquer membro da própria casa —
    mesmo resumo de saldo que a aba Contas mostra pro dono, sem lançamentos."""
    conn = get_db()
    if not eh_administrador(conn):
        conn.close()
        return jsonify({"erro": msg("só o administrador pode ver as contas de outro usuário")}), 403
    if not pertence_a_minha_casa(conn, "usuarios", item_id):
        conn.close()
        return jsonify({"erro": msg("usuário não encontrado")}), 404
    mes = request.args.get("mes", datetime.now().strftime("%Y-%m"))
    resultado = contas_com_saldo(conn, mes, usuario_id=item_id)
    conn.close()
    return jsonify(resultado)


def remover_foto_do_disco(nome_arquivo):
    if not nome_arquivo:
        return
    caminho = os.path.join(FOTOS_DIR, nome_arquivo)
    if os.path.exists(caminho):
        os.remove(caminho)


@app.route("/api/usuarios/<int:item_id>/foto", methods=["POST"])
def enviar_foto_perfil(item_id):
    if item_id != session.get("usuario_id"):
        return jsonify({"erro": msg("só é possível trocar a própria foto")}), 403
    if "arquivo" not in request.files:
        return jsonify({"erro": msg("nenhum arquivo enviado")}), 400
    arquivo = request.files["arquivo"]
    if arquivo.filename == "":
        return jsonify({"erro": msg("arquivo sem nome")}), 400

    extensao = os.path.splitext(arquivo.filename)[1].lower()
    if extensao not in EXTENSOES_IMAGEM:
        return jsonify({"erro": msg("envie uma imagem (jpg, png, gif ou webp)")}), 400

    os.makedirs(FOTOS_DIR, exist_ok=True)
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    nome_final = f"u{item_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}{extensao}"
    destino = caminho_em(FOTOS_DIR, nome_final)
    if not destino:
        return jsonify({"erro": msg("nome de arquivo inválido")}), 400
    arquivo.save(destino)

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
        return jsonify({"erro": msg("só é possível remover a própria foto")}), 403
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
        return jsonify({"erro": msg("foto não encontrada")}), 404
    return send_from_directory(FOTOS_DIR, nome_arquivo)


def pertence_a_minha_casa(conn, tabela, item_id):
    row = conn.execute(f"SELECT casa_id FROM {tabela} WHERE id = ?", (item_id,)).fetchone()
    return row is not None and row["casa_id"] == minha_casa_id(conn)


@app.route("/api/categorias", methods=["GET"])
def categorias():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM categorias WHERE casa_id = ? ORDER BY nome", (minha_casa_id(conn),)
    ).fetchall()
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
    cor = cor_valida(data.get("cor"))
    if not nome or tipo not in ("receita", "despesa"):
        return jsonify({"erro": msg("nome e tipo (receita/despesa) são obrigatórios")}), 400
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO categorias (nome, tipo, cor, casa_id) VALUES (?, ?, ?, ?)",
        (nome, tipo, cor, minha_casa_id(conn)),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True}), 201


@app.route("/api/categorias/<int:item_id>", methods=["PUT"])
def editar_categoria(item_id):
    data = request.get_json(force=True)
    nome = data.get("nome", "").strip()
    cor = cor_valida(data.get("cor"))
    if not nome:
        return jsonify({"erro": msg("nome é obrigatório")}), 400
    conn = get_db()
    if not pertence_a_minha_casa(conn, "categorias", item_id):
        conn.close()
        return jsonify({"erro": msg("categoria não encontrada")}), 404
    try:
        conn.execute("UPDATE categorias SET nome = ?, cor = ? WHERE id = ?", (nome, cor, item_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"erro": msg("já existe uma categoria com esse nome")}), 400
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/categorias/<int:item_id>", methods=["DELETE"])
def deletar_categoria(item_id):
    conn = get_db()
    if not pertence_a_minha_casa(conn, "categorias", item_id):
        conn.close()
        return jsonify({"erro": msg("categoria não encontrada")}), 404
    conn.execute("DELETE FROM categorias WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ---------------- Orçamento por categoria ----------------

@app.route("/api/orcamentos", methods=["GET"])
def listar_orcamentos():
    mes = request.args.get("mes", datetime.now().strftime("%Y-%m"))
    conn = get_db()
    orcamentos = conn.execute(
        "SELECT categoria, limite FROM orcamentos WHERE usuario_id = ?", (uid(),)
    ).fetchall()
    gastos = conn.execute(
        "SELECT COALESCE(NULLIF(categoria,''),'Sem categoria') as categoria, COALESCE(SUM(valor),0) as gasto "
        "FROM lancamentos WHERE mes = ? AND tipo = 'despesa' AND eh_transferencia = 0 AND usuario_id = ? "
        "GROUP BY categoria",
        (mes, uid()),
    ).fetchall()
    conn.close()
    gasto_por_categoria = {r["categoria"]: r["gasto"] for r in gastos}
    resultado = []
    for o in orcamentos:
        resultado.append({
            "categoria": o["categoria"],
            "limite": o["limite"],
            "gasto": gasto_por_categoria.get(o["categoria"], 0.0),
        })
    return jsonify(resultado)


@app.route("/api/orcamentos", methods=["POST"])
def definir_orcamento():
    data = request.get_json(force=True)
    categoria = (data.get("categoria") or "").strip()
    limite = data.get("limite")
    if not categoria or limite is None:
        return jsonify({"erro": msg("categoria e limite são obrigatórios")}), 400
    try:
        limite = float(limite)
    except (TypeError, ValueError):
        return jsonify({"erro": msg("limite inválido")}), 400
    conn = get_db()
    conn.execute(
        "INSERT INTO orcamentos (categoria, limite, usuario_id) VALUES (?, ?, ?) "
        "ON CONFLICT(categoria, usuario_id) DO UPDATE SET limite = excluded.limite",
        (categoria, limite, uid()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True}), 201


@app.route("/api/orcamentos/<path:categoria>", methods=["DELETE"])
def remover_orcamento(categoria):
    conn = get_db()
    conn.execute("DELETE FROM orcamentos WHERE categoria = ? AND usuario_id = ?", (categoria, uid()))
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
    meses = {r["mes"] for r in rows}

    # Além dos meses que já têm lançamento, oferece uma janela ao redor de hoje.
    # Sem isso, quem acabou de entrar recebia UM mês só — e no celular as setas
    # de navegação são escondidas justamente porque "dá pra usar o seletor",
    # então a pessoa ficava sem nenhuma forma de sair do mês atual. Vale também
    # para quem já usa: dá pra abrir um mês futuro antes de lançar algo nele.
    hoje = datetime.now()
    for delta in range(-12, 13):
        ano = hoje.year + (hoje.month - 1 + delta) // 12
        mes = (hoje.month - 1 + delta) % 12 + 1
        meses.add(f"{ano:04d}-{mes:02d}")

    return jsonify(sorted(meses, reverse=True))


@app.route("/api/lancamentos", methods=["GET"])
def listar_lancamentos():
    mes = request.args.get("mes", datetime.now().strftime("%Y-%m"))
    conn = get_db()
    garantir_recorrentes(conn, mes, uid())
    # Além do mês pedido, traz despesas de meses anteriores que ainda não foram pagas,
    # pra elas não ficarem "escondidas" num mês passado que ninguém mais abre.
    # Para séries recorrentes (ex: Francisco todo mês), só a ocorrência não paga mais
    # antiga aparece — senão cada mês sem pagar acumularia mais uma cópia na lista.
    rows = conn.execute(
        "SELECT * FROM lancamentos WHERE mes = ? AND usuario_id = ? "
        "UNION "
        "SELECT * FROM lancamentos WHERE mes < ? AND usuario_id = ? "
        "AND tipo = 'despesa' AND pago = 0 AND eh_transferencia = 0 "
        "AND ("
        "  grupo_recorrencia IS NULL"
        "  OR mes = (SELECT MIN(mes) FROM lancamentos l2"
        "            WHERE l2.grupo_recorrencia = lancamentos.grupo_recorrencia"
        "            AND l2.pago = 0 AND l2.usuario_id = lancamentos.usuario_id)"
        ") "
        "ORDER BY tipo, id",
        (mes, uid(), mes, uid()),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/ultimos-lancamentos", methods=["GET"])
def ultimos_lancamentos():
    """Os ultimos lancamentos, receita e despesa na mesma lista.

    Ordena por vencimento, e nao por criado_em, porque o que interessa e quando
    o dinheiro andou e nao quando alguem digitou: um lancamento retroativo
    (extrato importado, holerite de mes passado) entraria no topo se a ordem
    fosse a de digitacao, e nao e "ultimo" coisa nenhuma.

    Pelo mesmo motivo corta o que ainda vai vencer: parcela de dezembro nao e
    ultimo lancamento, e ela ja tem secao propria no painel.

    Transferencia entre contas fica de fora, como no Money Map. Ela existe aos
    pares (uma saida e uma entrada do mesmo valor) e encheria a lista com
    movimento que nao e ganho nem gasto.
    """
    try:
        limite = int(request.args.get("limite", 10))
    except (TypeError, ValueError):
        limite = 10
    # O limite vem da tela, entao e conferido aqui e nao la: um pedido montado
    # a mao levaria a base inteira embora se o numero seguisse direto pro SQL.
    if limite not in (5, 10, 30):
        limite = 10
    conn = get_db()
    rows = conn.execute(
        # SELECT * porque a tela monta o item completo (marcar como pago,
        # editar, anexar, excluir) e ele le recorrencia, parcela, comprovante,
        # observacao e origem. Devolver so o resumo obrigaria um segundo
        # pedido por lancamento na hora de editar.
        "SELECT * FROM lancamentos "
        "WHERE usuario_id = ? AND eh_transferencia = 0 AND vencimento <= ? "
        "ORDER BY vencimento DESC, id DESC LIMIT ?",
        (uid(), datetime.now().strftime("%Y-%m-%d"), limite),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/sugestoes", methods=["GET"])
def sugestoes_lancamento():
    """Sugere descrição, categoria, valor e conta a partir do histórico.

    A ideia é que lançamentos repetidos (a padaria de todo dia, o posto de
    sempre) não precisem ser reclassificados na mão: agrupa pelo texto da
    descrição e devolve, pra cada uma, a categoria mais usada e o valor mais
    recente. Ordena por quantas vezes apareceu, então o que é rotina vem antes.
    """
    termo = (request.args.get("q") or "").strip()
    if len(termo) < 2:
        return jsonify([])
    tipo = request.args.get("tipo") or ""
    conn = get_db()
    sql = (
        "SELECT descricao, COUNT(*) AS vezes, MAX(vencimento) AS ultima "
        "FROM lancamentos WHERE usuario_id = ? AND eh_transferencia = 0 "
        "AND descricao LIKE ? COLLATE NOCASE "
    )
    params = [uid(), f"%{termo}%"]
    if tipo in ("renda", "despesa"):
        sql += "AND tipo = ? "
        params.append(tipo)
    sql += "GROUP BY descricao COLLATE NOCASE ORDER BY vezes DESC, ultima DESC LIMIT 6"
    grupos = conn.execute(sql, params).fetchall()

    saida = []
    for g in grupos:
        # Categoria mais frequente para essa descrição (ignora as sem categoria).
        cat = conn.execute(
            "SELECT categoria, COUNT(*) AS n FROM lancamentos "
            "WHERE usuario_id = ? AND descricao = ? COLLATE NOCASE "
            "AND categoria IS NOT NULL AND categoria != '' "
            "GROUP BY categoria ORDER BY n DESC LIMIT 1",
            (uid(), g["descricao"]),
        ).fetchone()
        # Valor e conta do lançamento mais recente com essa descrição.
        ult = conn.execute(
            "SELECT valor, conta_id, conta, tipo FROM lancamentos "
            "WHERE usuario_id = ? AND descricao = ? COLLATE NOCASE "
            "ORDER BY vencimento DESC, id DESC LIMIT 1",
            (uid(), g["descricao"]),
        ).fetchone()
        saida.append({
            "descricao": g["descricao"],
            "vezes": g["vezes"],
            "categoria": cat["categoria"] if cat else None,
            "valor": ult["valor"] if ult else None,
            "conta_id": ult["conta_id"] if ult else None,
            "conta": ult["conta"] if ult else None,
            "tipo": ult["tipo"] if ult else None,
        })
    conn.close()
    return jsonify(saida)


@app.route("/api/busca", methods=["GET"])
def buscar_lancamentos():
    """Procura em todos os meses, não só no que está aberto na tela.

    Casa por descrição, categoria ou conta; se o termo for um número, também
    casa pelo valor (com uma folga de um centavo pra arredondamento).
    """
    termo = (request.args.get("q") or "").strip()
    if len(termo) < 2:
        return jsonify([])
    like = f"%{termo}%"
    try:
        valor = float(termo.replace(".", "").replace(",", "."))
    except ValueError:
        valor = None
    conn = get_db()
    sql = (
        "SELECT * FROM lancamentos WHERE usuario_id = ? AND ("
        "  descricao LIKE ? COLLATE NOCASE"
        "  OR COALESCE(categoria,'') LIKE ? COLLATE NOCASE"
        "  OR COALESCE(conta,'') LIKE ? COLLATE NOCASE"
    )
    params = [uid(), like, like, like]
    if valor is not None:
        sql += " OR ABS(valor - ?) < 0.01"
        params.append(valor)
    sql += ") ORDER BY vencimento DESC, id DESC LIMIT 40"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/lancamentos/exportar-csv", methods=["GET"])
def exportar_lancamentos_csv():
    mes = request.args.get("mes", datetime.now().strftime("%Y-%m"))
    conn = get_db()
    garantir_recorrentes(conn, mes, uid())
    rows = conn.execute(
        "SELECT tipo, descricao, valor, vencimento, categoria, conta, pago, data_pagamento, observacao "
        "FROM lancamentos WHERE mes = ? AND usuario_id = ? AND eh_transferencia = 0 ORDER BY tipo, vencimento",
        (mes, uid()),
    ).fetchall()
    conn.close()

    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    escritor.writerow(["Tipo", "Descrição", "Valor", "Vencimento", "Categoria", "Conta", "Pago", "Data pagamento", "Observação"])
    for r in rows:
        escritor.writerow([
            "Receita" if r["tipo"] == "renda" else "Despesa",
            r["descricao"],
            f"{r['valor']:.2f}".replace(".", ","),
            r["vencimento"] or "",
            r["categoria"] or "",
            r["conta"] or "",
            "Sim" if r["pago"] else "Não",
            r["data_pagamento"] or "",
            r["observacao"] or "",
        ])

    conteudo = "﻿" + saida.getvalue()  # BOM pra acentuação abrir certo no Excel
    return app.response_class(
        conteudo,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="lancamentos-{mes}.csv"'},
    )


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
    previsto = 1 if data.get("previsto") else 0
    data_pagamento = data.get("data_pagamento") or (datetime.now().strftime("%Y-%m-%d") if pago else "")

    if tipo not in ("renda", "despesa"):
        return jsonify({"erro": msg("tipo inválido")}), 400
    if not descricao:
        return jsonify({"erro": msg("descrição obrigatória")}), 400

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
            recorrencia_ate, previsto)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (mes, tipo, descricao, valor, vencimento, categoria, conta, conta_id, recorrente,
         pago, data_pagamento, observacao, datetime.now().isoformat(), uid(), grupo_recorrencia,
         recorrencia_ate, previsto),
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
        return jsonify({"erro": msg("lançamento não encontrado")}), 404

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
    # Aporte/resgate/provento de investimento tem uma operação espelhando esse
    # lançamento — apagar um dos dois lados sem o outro deixaria a carteira
    # de investimentos com um movimento fantasma.
    conn.execute("DELETE FROM investimento_operacoes WHERE lancamento_id = ?", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "escopo": "este"})


@app.route("/api/lancamentos/<int:item_id>", methods=["PUT"])
def editar_lancamento(item_id):
    data = request.get_json(force=True)
    conn = get_db()
    if not pertence_ao_usuario(conn, "lancamentos", item_id):
        conn.close()
        return jsonify({"erro": msg("lançamento não encontrado")}), 404
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
        return jsonify({"erro": msg("lançamento não encontrado")}), 404
    conn.execute("UPDATE lancamentos SET pago = ?, data_pagamento = ? WHERE id = ?",
                 (pago, data_pagamento, item_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/lancamentos/<int:item_id>/comprovante", methods=["POST"])
def enviar_comprovante(item_id):
    if "arquivo" not in request.files:
        return jsonify({"erro": msg("nenhum arquivo enviado")}), 400
    arquivo = request.files["arquivo"]
    if arquivo.filename == "":
        return jsonify({"erro": msg("arquivo sem nome")}), 400
    conn = get_db()
    if not pertence_ao_usuario(conn, "lancamentos", item_id):
        conn.close()
        return jsonify({"erro": msg("lançamento não encontrado")}), 404
    nome_seguro = secure_filename(arquivo.filename)
    nome_final = f"{item_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{nome_seguro}"
    os.makedirs(COMPROVANTES_DIR, exist_ok=True)
    destino = caminho_em(COMPROVANTES_DIR, nome_final)
    if not destino:
        conn.close()
        return jsonify({"erro": msg("nome de arquivo inválido")}), 400
    arquivo.save(destino)
    row = conn.execute("SELECT comprovante FROM lancamentos WHERE id = ?", (item_id,)).fetchone()
    if row and row["comprovante"]:
        antigo = caminho_em(COMPROVANTES_DIR, row["comprovante"])
        if antigo and os.path.exists(antigo):
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
        return jsonify({"erro": msg("comprovante não encontrado")}), 404
    return send_from_directory(COMPROVANTES_DIR, nome_arquivo)


# ---------------- Contas ----------------

def conta_sincronizada(conn, conta_id):
    """(sincronizada, ultimo_sync, extrato_ate) de uma conta do Open Finance.

    `ultimo_sync` é quando NÓS lemos a Pluggy. `extrato_ate` é a data da
    transação mais recente que chegou — e é essa que responde "meus dados
    estão atualizados?". O conector do Meu Pluggy sincroniza com os bancos por
    conta própria, então o primeiro carimbo pode ser de agora enquanto o
    extrato ainda para dias atrás.
    """
    row = conn.execute(
        "SELECT i.ultimo_sync, pc.account_id FROM pluggy_contas pc "
        "JOIN pluggy_itens i ON i.item_id = pc.item_id "
        "WHERE pc.conta_id = ? AND pc.ignorada = 0 LIMIT 1",
        (conta_id,),
    ).fetchone()
    if not row:
        return (False, None, None)
    ate = conn.execute(
        "SELECT MAX(data) d FROM pluggy_transacoes WHERE account_id = ?",
        (row["account_id"],),
    ).fetchone()["d"]
    return (True, row["ultimo_sync"], ate)


def contas_com_saldo(conn, mes, usuario_id=None):
    contas = conn.execute(
        "SELECT contas.*, usuarios.nome as usuario_nome FROM contas "
        "LEFT JOIN usuarios ON usuarios.id = contas.usuario_id "
        "WHERE contas.usuario_id = ? ORDER BY contas.nome",
        (usuario_id or uid(),),
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
        sincronizada, ultimo_sync, extrato_ate = conta_sincronizada(conn, c["id"])
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
            # A tela usa para esconder o campo de saldo inicial e dizer de
            # quando é o último extrato.
            "sincronizada": sincronizada,
            "ultimo_sync": ultimo_sync,
            "extrato_ate": extrato_ate,
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
        return jsonify({"erro": msg("nome é obrigatório")}), 400
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
        return jsonify({"erro": msg("já existe uma conta com esse nome")}), 400
    conn.close()
    return jsonify({"ok": True, "id": novo_id}), 201


@app.route("/api/contas/<int:item_id>", methods=["PUT"])
def editar_conta(item_id):
    data = request.get_json(force=True)
    nome = data.get("nome", "").strip()
    if not nome:
        return jsonify({"erro": msg("nome é obrigatório")}), 400
    conn = get_db()
    if not pertence_ao_usuario(conn, "contas", item_id):
        conn.close()
        return jsonify({"erro": msg("conta não encontrada")}), 404
    sincronizada, _, _ = conta_sincronizada(conn, item_id)
    try:
        if sincronizada:
            # O saldo_inicial dessa conta é calculado a partir do extrato: ele
            # é a diferença entre o que o banco reporta e a soma dos
            # lançamentos. Deixar editar faria o saldo parar de bater sem
            # ninguém perceber. O nome continua livre.
            if "saldo_inicial" in data:
                atual = conn.execute("SELECT saldo_inicial FROM contas WHERE id = ?",
                                     (item_id,)).fetchone()["saldo_inicial"]
                if abs(float(data.get("saldo_inicial") or 0) - atual) > 0.005:
                    conn.close()
                    return jsonify({"erro": "essa conta é sincronizada pelo Open Finance — "
                                            "o saldo inicial é calculado a partir do extrato"}), 400
            conn.execute("UPDATE contas SET nome = ? WHERE id = ?", (nome, item_id))
        else:
            conn.execute(
                "UPDATE contas SET nome = ?, saldo_inicial = ? WHERE id = ?",
                (nome, float(data.get("saldo_inicial", 0) or 0), item_id),
            )
        conn.execute("UPDATE lancamentos SET conta = ? WHERE conta_id = ?", (nome, item_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"erro": msg("já existe uma conta com esse nome")}), 400
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/contas/<int:item_id>", methods=["DELETE"])
def deletar_conta(item_id):
    conn = get_db()
    if not pertence_ao_usuario(conn, "contas", item_id):
        conn.close()
        return jsonify({"erro": msg("conta não encontrada")}), 404
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
        return jsonify({"erro": msg("conta de origem e destino são obrigatórias")}), 400
    if str(conta_origem_id) == str(conta_destino_id):
        return jsonify({"erro": msg("as contas de origem e destino devem ser diferentes")}), 400
    if valor <= 0:
        return jsonify({"erro": msg("valor deve ser maior que zero")}), 400

    conn = get_db()
    # A origem tem que ser sua; o destino pode ser a conta de outro usuário,
    # mas só se for da mesma casa (ex: transferir da sua conta pra da sua esposa —
    # nunca pra conta de alguém de fora da sua casa).
    origem = conn.execute(
        "SELECT id, nome, usuario_id FROM contas WHERE id = ? AND usuario_id = ?",
        (conta_origem_id, uid()),
    ).fetchone()
    destino = conn.execute(
        "SELECT contas.id, contas.nome, contas.usuario_id FROM contas "
        "JOIN usuarios ON usuarios.id = contas.usuario_id "
        "WHERE contas.id = ? AND usuarios.casa_id = ?",
        (conta_destino_id, minha_casa_id(conn)),
    ).fetchone()
    if not origem:
        conn.close()
        return jsonify({"erro": msg("conta de origem inválida")}), 400
    if not destino:
        conn.close()
        return jsonify({"erro": msg("conta de destino inválida")}), 400

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
    outros usuários da mesma casa (só destino). Expõe apenas id, nome e dono —
    nenhum saldo, e nada de fora da sua casa."""
    conn = get_db()
    rows = conn.execute(
        "SELECT contas.id, contas.nome, contas.usuario_id, usuarios.nome as usuario_nome "
        "FROM contas LEFT JOIN usuarios ON usuarios.id = contas.usuario_id "
        "WHERE usuarios.casa_id = ? "
        "ORDER BY usuarios.nome, contas.nome",
        (minha_casa_id(conn),),
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
        return jsonify({"erro": msg("cartão não encontrado")}), 404
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
        return jsonify({"erro": msg("cartão não encontrado")}), 404
    conn.execute("UPDATE cartoes SET fatura_paga = ? WHERE id = ?", (1 if data.get("pago") else 0, item_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/cartoes/<int:item_id>", methods=["DELETE"])
def deletar_cartao(item_id):
    conn = get_db()
    if not pertence_ao_usuario(conn, "cartoes", item_id):
        conn.close()
        return jsonify({"erro": msg("cartão não encontrado")}), 404
    conn.execute("DELETE FROM cartoes WHERE id = ?", (item_id,))
    conn.execute("DELETE FROM cartao_transacoes WHERE cartao_id = ?", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


def recalcular_fatura_cartao(conn, cartao_id):
    """Mantém cartoes.fatura_atual em sincronia com a soma das transações —
    é essa soma que aparece na aba Cartões, nunca a lista de lancamentos."""
    total = conn.execute(
        "SELECT COALESCE(SUM(valor), 0) FROM cartao_transacoes WHERE cartao_id = ?",
        (cartao_id,),
    ).fetchone()[0]
    conn.execute("UPDATE cartoes SET fatura_atual = ? WHERE id = ?", (total, cartao_id))


@app.route("/api/cartoes/<int:cartao_id>/transacoes", methods=["GET"])
def listar_transacoes_cartao(cartao_id):
    conn = get_db()
    if not pertence_ao_usuario(conn, "cartoes", cartao_id):
        conn.close()
        return jsonify({"erro": msg("cartão não encontrado")}), 404
    rows = conn.execute(
        "SELECT * FROM cartao_transacoes WHERE cartao_id = ? AND usuario_id = ? ORDER BY data DESC, id DESC",
        (cartao_id, uid()),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/cartoes/<int:cartao_id>/transacoes", methods=["POST"])
def criar_transacao_cartao(cartao_id):
    conn = get_db()
    if not pertence_ao_usuario(conn, "cartoes", cartao_id):
        conn.close()
        return jsonify({"erro": msg("cartão não encontrado")}), 404
    data = request.get_json(force=True)
    descricao = (data.get("descricao") or "").strip()
    if not descricao or data.get("valor") in (None, ""):
        conn.close()
        return jsonify({"erro": msg("descrição e valor são obrigatórios")}), 400
    try:
        valor = float(data.get("valor"))
    except (TypeError, ValueError):
        conn.close()
        return jsonify({"erro": msg("valor inválido")}), 400
    conn.execute(
        "INSERT INTO cartao_transacoes (cartao_id, descricao, valor, data, categoria, usuario_id, criado_em) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cartao_id, descricao, valor, data.get("data") or None, (data.get("categoria") or "").strip() or None,
         uid(), datetime.now().isoformat()),
    )
    recalcular_fatura_cartao(conn, cartao_id)
    conn.commit()
    conn.close()
    return jsonify({"ok": True}), 201


@app.route("/api/cartoes/transacoes/<int:item_id>", methods=["DELETE"])
def deletar_transacao_cartao(item_id):
    conn = get_db()
    if not pertence_ao_usuario(conn, "cartao_transacoes", item_id):
        conn.close()
        return jsonify({"erro": msg("lançamento não encontrado")}), 404
    row = conn.execute("SELECT cartao_id FROM cartao_transacoes WHERE id = ?", (item_id,)).fetchone()
    conn.execute("DELETE FROM cartao_transacoes WHERE id = ?", (item_id,))
    if row:
        recalcular_fatura_cartao(conn, row["cartao_id"])
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ---------------- Metas de economia ----------------

@app.route("/api/metas", methods=["GET"])
def listar_metas():
    conn = get_db()
    estado = request.args.get("estado")
    sql = "SELECT * FROM metas WHERE usuario_id = ?"
    p = [uid()]
    if estado and estado != "todas":
        sql += " AND estado = ?"; p.append(estado)
    # Arquivada some da lista padrão: ela existe para tirar da vista sem apagar.
    elif not estado:
        sql += " AND estado <> 'arquivada'"
    rows = conn.execute(sql + " ORDER BY criado_em", p).fetchall()
    conn.close()
    return jsonify([_situacao_meta(dict(r)) for r in rows])


@app.route("/api/metas", methods=["POST"])
def criar_meta():
    data = request.get_json(force=True)
    nome = (data.get("nome") or "").strip()
    valor_alvo = data.get("valor_alvo")
    prazo = data.get("prazo") or None
    if not nome or not valor_alvo:
        return jsonify({"erro": msg("nome e valor alvo são obrigatórios")}), 400
    try:
        valor_alvo = float(valor_alvo)
    except (TypeError, ValueError):
        return jsonify({"erro": msg("valor alvo inválido")}), 400
    conn = get_db()
    conn.execute(
        "INSERT INTO metas (nome, valor_alvo, valor_atual, prazo, criado_em, usuario_id, "
        "estado, inicio, icone, cor) VALUES (?, ?, 0, ?, ?, ?, 'ativa', ?, ?, ?)",
        (nome, valor_alvo, prazo, datetime.now().isoformat(), uid(),
         datetime.now().strftime("%Y-%m"), (data.get("icone") or "").strip() or None,
         cor_valida(data.get("cor"))),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True}), 201


@app.route("/api/metas/<int:item_id>", methods=["PUT"])
def editar_meta(item_id):
    data = request.get_json(force=True)
    conn = get_db()
    if not pertence_ao_usuario(conn, "metas", item_id):
        conn.close()
        return jsonify({"erro": msg("meta não encontrada")}), 404
    campos, valores = [], []
    if "nome" in data:
        campos.append("nome = ?")
        valores.append((data.get("nome") or "").strip())
    if "valor_alvo" in data:
        campos.append("valor_alvo = ?")
        valores.append(float(data["valor_alvo"]))
    if "valor_atual" in data:
        campos.append("valor_atual = ?")
        valores.append(float(data["valor_atual"]))
    if "prazo" in data:
        campos.append("prazo = ?")
        valores.append(data.get("prazo") or None)
    if campos:
        valores.append(item_id)
        conn.execute(f"UPDATE metas SET {', '.join(campos)} WHERE id = ?", valores)
        conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/metas/<int:item_id>", methods=["DELETE"])
def deletar_meta(item_id):
    conn = get_db()
    if not pertence_ao_usuario(conn, "metas", item_id):
        conn.close()
        return jsonify({"erro": msg("meta não encontrada")}), 404
    conn.execute("DELETE FROM metas WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ---------------- Consignados ----------------

@app.route("/api/consignados/config", methods=["GET"])
def obter_config_consignados():
    conn = get_db()
    row = conn.execute(
        "SELECT consignados_habilitado FROM casas WHERE id = ?", (minha_casa_id(conn),)
    ).fetchone()
    conn.close()
    return jsonify({"habilitado": bool(row["consignados_habilitado"]) if row else False})


@app.route("/api/consignados/config", methods=["PUT"])
def definir_config_consignados():
    conn = get_db()
    if not eh_administrador(conn):
        conn.close()
        return jsonify({"erro": msg("só o administrador pode alterar essa configuração")}), 403
    data = request.get_json(force=True)
    conn.execute(
        "UPDATE casas SET consignados_habilitado = ? WHERE id = ?",
        (1 if data.get("habilitado") else 0, minha_casa_id(conn)),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/consignados", methods=["GET"])
def listar_consignados():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM consignados WHERE usuario_id = ? ORDER BY ativo DESC, criado_em", (uid(),)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/consignados", methods=["POST"])
def criar_consignado():
    data = request.get_json(force=True)
    nome = (data.get("nome") or "").strip()
    valor_parcela = data.get("valor_parcela")
    if not nome or not valor_parcela:
        return jsonify({"erro": msg("nome e valor da parcela são obrigatórios")}), 400
    try:
        valor_parcela = float(valor_parcela)
    except (TypeError, ValueError):
        return jsonify({"erro": msg("valor da parcela inválido")}), 400
    conn = get_db()
    conn.execute(
        """INSERT INTO consignados
           (usuario_id, nome, valor_parcela, parcela_atual, parcela_total, ativo, observacao, criado_em)
           VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
        (uid(), nome, valor_parcela, data.get("parcela_atual") or None, data.get("parcela_total") or None,
         (data.get("observacao") or "").strip(), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True}), 201


@app.route("/api/consignados/<int:item_id>", methods=["PUT"])
def editar_consignado(item_id):
    data = request.get_json(force=True)
    conn = get_db()
    if not pertence_ao_usuario(conn, "consignados", item_id):
        conn.close()
        return jsonify({"erro": msg("consignado não encontrado")}), 404
    campos, valores = [], []
    if "nome" in data:
        campos.append("nome = ?")
        valores.append((data.get("nome") or "").strip())
    if "valor_parcela" in data:
        campos.append("valor_parcela = ?")
        valores.append(float(data["valor_parcela"]))
    if "parcela_atual" in data:
        campos.append("parcela_atual = ?")
        valores.append(data.get("parcela_atual") or None)
    if "parcela_total" in data:
        campos.append("parcela_total = ?")
        valores.append(data.get("parcela_total") or None)
    if "ativo" in data:
        campos.append("ativo = ?")
        valores.append(1 if data.get("ativo") else 0)
    if "observacao" in data:
        campos.append("observacao = ?")
        valores.append((data.get("observacao") or "").strip())
    if campos:
        valores.append(item_id)
        conn.execute(f"UPDATE consignados SET {', '.join(campos)} WHERE id = ?", valores)
        conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/consignados/<int:item_id>", methods=["DELETE"])
def deletar_consignado(item_id):
    conn = get_db()
    if not pertence_ao_usuario(conn, "consignados", item_id):
        conn.close()
        return jsonify({"erro": msg("consignado não encontrado")}), 404
    conn.execute("DELETE FROM consignados WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ---------------- Investimentos ----------------

CLASSES_COM_TICKER = {"acao", "fii", "etf", "bdr", "cripto", "stock", "reit", "etf_internacional"}

# O ticker é escolhido por quem cadastra o investimento e vai parar dentro da
# URL da brapi, do Yahoo e da CoinGecko. Um valor com barra, dois-pontos ou
# interrogação deixa de ser um trecho do caminho e vira outro endereço, então
# ele é restrito na entrada e não só escapado na saída. O formato cobre o que
# as fontes usam: B3 (PETR4, MXRF11), CoinGecko (bitcoin, usd-coin) e
# americanos com ponto ou hífen (BRK.B, BRK-B).
RE_TICKER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")




def ticker_seguro(ticker):
    """Devolve o ticker se ele for um ticker, e None se não for.

    Devolver o texto que saiu do próprio match, em vez de um sim/não sobre o
    original, é de propósito: o que segue para dentro da URL é o valor que
    passou pela conferência, e não uma cópia que só passou perto dela.
    """
    m = RE_TICKER.match(ticker or "")
    return m.group(0) if m else None

CLASSES_VALIDAS = CLASSES_COM_TICKER | {"renda_fixa", "fundo", "outro"}
CLASSES_YAHOO = {"stock", "reit", "etf_internacional"}  # tickers americanos, cotados em USD
TIPOS_OPERACAO_INVESTIMENTO = {"aporte", "resgate", "provento", "reavaliacao"}
DATA_BASE_INDEXADOR = "2015-01-01"


def normalizar_ticker(classe, valor):
    """CoinGecko exige o id em minúsculas (ex: "bitcoin") — as outras classes
    com ticker usam maiúsculas. Usado sempre que um ticker é salvo, pra não
    gravar cripto de um jeito que a cotação nunca mais vai encontrar."""
    valor = ticker_seguro((valor or "").strip())
    if not valor:
        return None
    return valor.lower() if classe == "cripto" else valor.upper()


def _bcb_serie(codigo, data_inicial, data_final):
    """Busca a série do Banco Central (SGS) num intervalo — API pública, sem
    chave. Cada item vem como {"data": "dd/mm/aaaa", "valor": "0.123"}.
    A própria API recusa (406) uma janela de mais de 10 anos numa série
    diária, então quem chama precisa garantir um intervalo dentro desse limite."""
    resp = requests.get(
        f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados",
        params={
            "dataInicial": data_inicial.strftime("%d/%m/%Y"),
            "dataFinal": data_final.strftime("%d/%m/%Y"),
            "formato": "json",
        },
        # janelas maiores (série diária de vários anos) demoram bem mais que os
        # 15s usados nas outras chamadas dessa seção — o BCB é lento pra montar
        # uma resposta grande, não é sinal de que o serviço esteja fora do ar.
        timeout=60,
    )
    # "Não existe ponto nesse intervalo" acontece todo dia, já que o CDI de
    # hoje só é publicado à noite. O SGS sinaliza isso com 404 e um corpo
    # {"erro": {...}} — mas nem sempre com 404: o mesmo corpo já veio com 200.
    # Por isso quem decide é o formato da resposta, não o status: só uma lista
    # é série de verdade. Devolver vazio deixa o ciclo seguir em silêncio.
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    dados = resp.json()
    return dados if isinstance(dados, list) else []


def atualizar_indexador(conn, indexador, codigo_bcb):
    """Busca só os dias/meses novos desde a última linha cacheada em
    indexador_serie e completa o fator acumulado incrementalmente — evita
    reprocessar a série inteira (que pode ter milhares de pontos) a cada ciclo.
    Busca em janelas de até 10 anos, porque é o máximo que a API do Banco
    Central aceita numa série diária (ela recusa o resto com HTTP 406)."""
    ultima = conn.execute(
        "SELECT data, fator_acumulado FROM indexador_serie WHERE indexador = ? "
        "ORDER BY data DESC LIMIT 1",
        (indexador,),
    ).fetchone()
    fator = ultima["fator_acumulado"] if ultima else 1.0
    inicio = (
        datetime.strptime(ultima["data"], "%Y-%m-%d") + timedelta(days=1)
        if ultima else datetime.strptime(DATA_BASE_INDEXADOR, "%Y-%m-%d")
    )
    hoje = datetime.now()
    while inicio.date() <= hoje.date():
        # janelas de 5 anos (não os 10 que a API permite): fica mais rápido por
        # chamada e, se uma janela falhar no meio de uma carga inicial grande,
        # as janelas já processadas (gravadas abaixo a cada volta) não se perdem.
        fim_janela = min(inicio + timedelta(days=1825), hoje)
        linhas = []
        for item in _bcb_serie(codigo_bcb, inicio, fim_janela):
            data_iso = datetime.strptime(item["data"], "%d/%m/%Y").strftime("%Y-%m-%d")
            taxa = float(str(item["valor"]).replace(",", "."))
            fator *= (1 + taxa / 100)
            linhas.append((indexador, data_iso, fator))
        if linhas:
            conn.executemany(
                "INSERT INTO indexador_serie (indexador, data, fator_acumulado) VALUES (?, ?, ?) "
                "ON CONFLICT(indexador, data) DO UPDATE SET fator_acumulado = excluded.fator_acumulado",
                linhas,
            )
            conn.commit()
        inicio = fim_janela + timedelta(days=1)


def fator_indexador_em(conn, indexador, data_iso):
    row = conn.execute(
        "SELECT fator_acumulado FROM indexador_serie WHERE indexador = ? AND data <= ? "
        "ORDER BY data DESC LIMIT 1",
        (indexador, data_iso),
    ).fetchone()
    return row["fator_acumulado"] if row else 1.0


def buscar_cotacoes_brapi(tickers):
    """Cotação de ações/FIIs/ETFs da B3 via brapi.dev. Sem BRAPI_TOKEN configurado
    só funciona pros tickers de teste gratuitos da própria brapi — o resto fica
    sem atualizar (o card mostra "cotação não configurada"). O plano gratuito só
    aceita 1 ativo por requisição (pedir vários de uma vez dá 400 e derruba a
    atualização inteira), então busca um de cada vez — cada falha isolada só
    deixa aquele ticker desatualizado, não trava o resto."""
    if not tickers:
        return {}
    params = {"token": BRAPI_TOKEN} if BRAPI_TOKEN else {}
    resultado = {}
    for ticker in tickers:
        seguro = ticker_seguro(ticker)
        if not seguro:
            continue
        try:
            resp = requests.get(
                f"https://brapi.dev/api/quote/{quote(seguro, safe='')}", params=params, timeout=15
            )
            resp.raise_for_status()
            for item in resp.json().get("results", []):
                preco = item.get("regularMarketPrice")
                if preco is not None:
                    resultado[item["symbol"]] = preco
        except Exception as e:
            print(f"[investimentos] falha ao buscar cotação B3 de {ticker}: {e}", flush=True)
    return resultado


def buscar_cotacoes_cripto(tickers):
    """Cotação de cripto via CoinGecko — gratuita, sem chave. `tickers` são ids
    da CoinGecko (ex: bitcoin, ethereum), não o símbolo curto (BTC, ETH)."""
    if not tickers:
        return {}
    resp = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": ",".join(tickers), "vs_currencies": "brl"}, timeout=15,
    )
    resp.raise_for_status()
    dados = resp.json()
    return {t: dados[t]["brl"] for t in tickers if t in dados and "brl" in dados[t]}


def buscar_cotacoes_yahoo(tickers):
    """Cotação de stocks/REITs/ETFs internacionais via Yahoo Finance (endpoint
    não-oficial, sem chave, só precisa de um User-Agent). Uma chamada por
    ticker — carteira pessoal tem poucos ativos americanos, não compensa a
    complicação de tentar buscar em lote."""
    resultado = {}
    for ticker in tickers:
        seguro = ticker_seguro(ticker)
        if not seguro:
            continue
        try:
            resp = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(seguro, safe='')}",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=15,
            )
            resp.raise_for_status()
            preco = resp.json()["chart"]["result"][0]["meta"].get("regularMarketPrice")
            if preco is not None:
                resultado[ticker] = preco
        except Exception as e:
            print(f"[investimentos] falha ao buscar cotação Yahoo de {ticker}: {e}", flush=True)
    return resultado


def buscar_cambio_usd_brl():
    """Dólar comercial (venda) via Banco Central — série 1 do SGS, mesma API
    pública já usada pro CDI/IPCA."""
    linhas = _bcb_serie(1, datetime.now() - timedelta(days=7), datetime.now())
    return float(str(linhas[-1]["valor"]).replace(",", ".")) if linhas else None


# ------------------- Histórico mensal de preço -------------------
# O módulo nasceu em agosto/2026, mas as compras do usuário são bem mais
# antigas. Sem preço do passado, "Evolução do Patrimônio" teria uma barra só e
# "Rentabilidade comparada com o CDI" ficaria sem linha nenhuma. As três fontes
# já usadas pra cotação do dia também servem o fechamento mês a mês, então a
# reconstrução histórica não traz dependência externa nova.

def _historico_para_meses(pontos):
    """Converte [(timestamp_unix, preço)] no fechamento de cada mês. Quando o
    mesmo mês aparece mais de uma vez fica valendo o ponto mais recente."""
    por_mes = {}
    for ts, valor in pontos:
        if valor is None:
            continue
        mes = datetime.fromtimestamp(ts).strftime("%Y-%m")
        por_mes[mes] = float(valor)
    return por_mes


def buscar_historico_cripto(ticker):
    """Histórico de cripto em reais. A CoinGecko gratuita só devolve 365 dias
    (mais que isso é 401), então ela entra como plano B — o principal é o
    Yahoo, que tem a série longa mas cota em dólar e por isso precisa do
    câmbio mês a mês. `ticker` é o id da CoinGecko (ex.: "bitcoin"); o
    símbolo curto que o Yahoo quer (BTC) vem do catálogo local."""
    resp = requests.get(
        f"https://api.coingecko.com/api/v3/coins/{ticker}/market_chart",
        params={"vs_currency": "brl", "days": "365"}, timeout=25,
    )
    resp.raise_for_status()
    # a CoinGecko devolve o timestamp em milissegundos
    return _historico_para_meses([(p[0] / 1000, p[1]) for p in resp.json().get("prices", [])])


def simbolo_cripto(conn, ticker):
    """Símbolo curto (BTC) do id da CoinGecko (bitcoin), pra montar o par que
    o Yahoo entende. Sai do catálogo local — que já é atualizado todo dia
    pela busca com autocompletar —, sem chamada externa nova."""
    row = conn.execute(
        "SELECT simbolo FROM ativo_catalogo WHERE classe = 'cripto' AND ticker = ?", (ticker,)
    ).fetchone()
    return (row["simbolo"] or "").upper() if row and row["simbolo"] else None


def buscar_historico_yahoo(ticker):
    """Histórico mensal do Yahoo Finance. Vale também pra B3, com o sufixo
    ".SA" — o plano gratuito da brapi.dev só libera `range` até 3 meses (fora
    os poucos tickers de demonstração), o que não desenha ano nenhum. O Yahoo
    devolve 10 anos em reais sem chave, então quem manda no histórico é ele;
    a brapi.dev continua sendo a fonte da cotação do dia e dos proventos."""
    resp = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
        params={"range": "10y", "interval": "1mo"},
        headers={"User-Agent": "Mozilla/5.0"}, timeout=25,
    )
    resp.raise_for_status()
    r = resp.json()["chart"]["result"][0]
    fechamentos = r["indicators"]["quote"][0]["close"]
    return _historico_para_meses(list(zip(r["timestamp"], fechamentos)))


def atualizar_historico_cotacoes(conn, somente_faltantes=False):
    """Preenche o histórico mensal de cada ticker que alguém tem na carteira.
    Roda no ciclo diário: preço de mês fechado não muda mais, então só os
    meses ainda ausentes precisam de rede — quem já tem série completa custa
    uma requisição por dia, não uma por hora. Com `somente_faltantes` pula
    quem já tem série gravada, que é o que o botão "Atualizar cotações"
    precisa: buscar o passado de um ativo recém-cadastrado sem refazer o de
    todos os outros e deixar o usuário esperando."""
    agora = datetime.now().isoformat()

    def gravar_serie(chave, serie):
        if not serie:
            return
        conn.executemany(
            "INSERT INTO investimento_cotacao_historico (chave, mes, valor, atualizado_em) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chave, mes) DO UPDATE SET valor = excluded.valor, atualizado_em = excluded.atualizado_em",
            [(chave, mes, valor, agora) for mes, valor in serie.items()],
        )
        conn.commit()

    # O câmbio vem primeiro porque a cripto depende dele: a série longa do
    # Yahoo é em dólar e precisa virar real mês a mês antes de ser gravada.
    # A fonte é o próprio Banco Central (série 1 do SGS), a mesma do dólar do
    # dia, o que mantém passado e presente coerentes.
    cambio_por_mes = {}
    try:
        linhas = _bcb_serie(1, datetime.now() - timedelta(days=365 * 10), datetime.now())
        for linha in linhas:
            _, mes, ano = linha["data"].split("/")
            cambio_por_mes[f"{ano}-{mes}"] = float(str(linha["valor"]).replace(",", "."))
        gravar_serie("USD_BRL", cambio_por_mes)
    except Exception as e:
        print(f"[investimentos] falha no histórico do câmbio: {e}", flush=True)

    alvos = conn.execute(
        "SELECT DISTINCT ticker, classe FROM investimentos WHERE ticker IS NOT NULL"
    ).fetchall()
    for alvo in alvos:
        ticker, classe = alvo["ticker"], alvo["classe"]
        if somente_faltantes and conn.execute(
            "SELECT 1 FROM investimento_cotacao_historico WHERE chave = ? LIMIT 1", (ticker,)
        ).fetchone():
            continue
        try:
            if classe == "cripto":
                simbolo = simbolo_cripto(conn, ticker)
                serie = {}
                if simbolo and cambio_por_mes:
                    em_dolar = buscar_historico_yahoo(f"{simbolo}-USD")
                    serie = {
                        mes: valor * cambio_por_mes[mes]
                        for mes, valor in em_dolar.items() if mes in cambio_por_mes
                    }
                if not serie:
                    serie = buscar_historico_cripto(ticker)  # último ano, já em reais
            elif classe in CLASSES_YAHOO:
                serie = buscar_historico_yahoo(ticker)
            elif classe in CLASSES_COM_DIVIDENDO_B3:
                serie = buscar_historico_yahoo(f"{ticker}.SA")
            else:
                continue
        except Exception as e:
            print(f"[investimentos] falha no histórico de {ticker}: {e}", flush=True)
            continue
        gravar_serie(ticker, serie)


def buscar_e_cachear_yahoo(conn, classe, q):
    """Busca de stock/REIT/ETF internacional: sem uma API de "listar tudo"
    gratuita pros EUA, essa classe funciona por cache sob demanda — só chama
    o Yahoo Search na primeira vez que alguém procura por esse termo (em
    qualquer casa), guarda o resultado em ativo_catalogo, e as buscas
    seguintes pelo mesmo termo já saem do banco local."""
    linhas = conn.execute(
        "SELECT ticker, nome FROM ativo_catalogo WHERE classe = ? AND (ticker LIKE ? OR nome LIKE ?) LIMIT 8",
        (classe, f"{q}%", f"%{q}%"),
    ).fetchall()
    if len(linhas) >= 5:
        return [dict(r) for r in linhas]
    try:
        resp = requests.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": q, "quotesCount": 8, "newsCount": 0},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10,
        )
        resp.raise_for_status()
        novos = [
            (classe, item["symbol"], item.get("shortname") or item.get("longname") or item["symbol"])
            for item in resp.json().get("quotes", [])
            if item.get("quoteType") in ("EQUITY", "ETF") and item.get("symbol")
        ]
        if novos:
            conn.executemany(
                "INSERT INTO ativo_catalogo (classe, ticker, nome) VALUES (?, ?, ?) "
                "ON CONFLICT(classe, ticker) DO UPDATE SET nome = excluded.nome",
                novos,
            )
            conn.commit()
    except Exception as e:
        print(f"[investimentos] falha na busca Yahoo de '{q}': {e}", flush=True)
    linhas = conn.execute(
        "SELECT ticker, nome FROM ativo_catalogo WHERE classe = ? AND (ticker LIKE ? OR nome LIKE ?) "
        "ORDER BY (ticker LIKE ?) DESC, ticker LIMIT 8",
        (classe, f"{q}%", f"%{q}%", f"{q}%"),
    ).fetchall()
    return [dict(r) for r in linhas]


def atualizar_todas_cotacoes(conn):
    tickers_b3 = [r["ticker"] for r in conn.execute(
        "SELECT DISTINCT ticker FROM investimentos WHERE ticker IS NOT NULL "
        "AND classe IN ('acao','fii','etf','bdr')"
    ).fetchall()]
    tickers_cripto = [r["ticker"] for r in conn.execute(
        "SELECT DISTINCT ticker FROM investimentos WHERE ticker IS NOT NULL AND classe = 'cripto'"
    ).fetchall()]
    tickers_yahoo = [r["ticker"] for r in conn.execute(
        "SELECT DISTINCT ticker FROM investimentos WHERE ticker IS NOT NULL "
        "AND classe IN ('stock','reit','etf_internacional')"
    ).fetchall()]
    agora = datetime.now().isoformat()

    def gravar(chave, valor):
        conn.execute(
            "INSERT INTO investimento_cotacoes (chave, valor, atualizado_em) VALUES (?, ?, ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor, atualizado_em = excluded.atualizado_em",
            (chave, valor, agora),
        )

    try:
        for ticker, preco in buscar_cotacoes_brapi(tickers_b3).items():
            gravar(ticker, preco)
    except Exception as e:
        print(f"[investimentos] falha ao buscar cotação B3: {e}", flush=True)
    try:
        for ticker, preco in buscar_cotacoes_cripto(tickers_cripto).items():
            gravar(ticker, preco)
    except Exception as e:
        print(f"[investimentos] falha ao buscar cotação cripto: {e}", flush=True)
    try:
        for ticker, preco in buscar_cotacoes_yahoo(tickers_yahoo).items():
            gravar(ticker, preco)
    except Exception as e:
        print(f"[investimentos] falha ao buscar cotação Yahoo: {e}", flush=True)
    try:
        cambio = buscar_cambio_usd_brl()
        if cambio:
            gravar("USD_BRL", cambio)
    except Exception as e:
        print(f"[investimentos] falha ao buscar câmbio USD/BRL: {e}", flush=True)
    try:
        atualizar_indexador(conn, "cdi", 12)
    except Exception as e:
        print(f"[investimentos] falha ao atualizar CDI: {e}", flush=True)
    try:
        atualizar_indexador(conn, "ipca", 433)
    except Exception as e:
        print(f"[investimentos] falha ao atualizar IPCA: {e}", flush=True)
    conn.commit()


def ultimo_dia_do_mes(mes):
    ano, m = int(mes[:4]), int(mes[5:7])
    return f"{mes}-{calendar.monthrange(ano, m)[1]:02d}"


def preco_historico(conn, chave, mes, cache=None):
    """Fechamento do ticker naquele mês. Mês sem pregão registrado (feriado,
    ativo recém-listado, buraco na série) cai no fechamento anterior mais
    próximo, em vez de zerar o patrimônio daquele mês no gráfico."""
    if cache is not None and (chave, mes) in cache:
        return cache[(chave, mes)]
    row = conn.execute(
        "SELECT valor FROM investimento_cotacao_historico WHERE chave = ? AND mes <= ? ORDER BY mes DESC LIMIT 1",
        (chave, mes),
    ).fetchone()
    valor = row["valor"] if row else None
    if cache is not None:
        cache[(chave, mes)] = valor
    return valor


def patrimonio_em_mes(conn, invs_com_ops, mes, cache_preco=None):
    """Quanto a carteira valia no fim de um mês qualquer — a soma, ativo a
    ativo, da posição que existia naquela data avaliada ao preço daquela data.
    É o que dá história ao gráfico de patrimônio e à comparação com o CDI."""
    fim = ultimo_dia_do_mes(mes)
    investido_total = atual_total = 0.0
    for inv, operacoes in invs_com_ops:
        ops = [o for o in operacoes if o["data"] <= fim]
        if not ops:
            continue
        investido = sum(o["valor"] for o in ops if o["tipo"] == "aporte") - sum(
            o["valor"] for o in ops if o["tipo"] == "resgate"
        )
        if inv["classe"] in CLASSES_COM_TICKER:
            quantidade, custo = 0.0, 0.0
            for o in ops:
                if o["tipo"] == "aporte":
                    quantidade += o["quantidade"] or 0
                    custo += o["valor"]
                elif o["tipo"] == "resgate":
                    qtd = o["quantidade"] or 0
                    preco_medio = (custo / quantidade) if quantidade else 0
                    custo -= preco_medio * qtd
                    quantidade -= qtd
            preco = preco_historico(conn, inv["ticker"], mes, cache_preco)
            fator_cambio = 1.0
            if inv["classe"] in CLASSES_YAHOO:
                fator_cambio = preco_historico(conn, "USD_BRL", mes, cache_preco) or 1.0
            atual = quantidade * preco * fator_cambio if (preco and quantidade) else custo
        elif inv["classe"] == "renda_fixa":
            atual = valor_atual_renda_fixa(conn, inv, ops, ate=fim)
        else:
            reavaliacoes = [o for o in ops if o["tipo"] == "reavaliacao"]
            atual = reavaliacoes[-1]["valor"] if reavaliacoes else investido
        investido_total += investido
        atual_total += atual
    return round(investido_total, 2), round(atual_total, 2)


def atualizar_snapshots_patrimonio(conn, somente_mes_atual=False):
    """Reconstrói o retrato mensal do patrimônio de cada usuário desde a
    primeira compra, usando o histórico de preço de cada ativo. Recalcula
    tudo em vez de só carimbar o mês atual: uma compra antiga lançada hoje
    (ou um histórico de preço que só chegou agora) muda o passado do gráfico,
    e ficar com o retrato velho seria mostrar um patrimônio que nunca houve.
    O mês corrente sai do valor de mercado de agora, não do fechamento.

    Com `somente_mes_atual` refaz só o mês corrente. É o que o ciclo horário
    precisa — o passado só muda quando chega histórico novo, e refazê-lo com
    o histórico ainda pela metade (logo que o app sobe, antes do ciclo diário
    terminar) gravaria meses passados no valor de custo em vez do de mercado."""
    agora = datetime.now().isoformat()
    mes_atual = datetime.now().strftime("%Y-%m")
    usuarios = conn.execute("SELECT DISTINCT usuario_id FROM investimentos").fetchall()
    for u in usuarios:
        usuario_id = u["usuario_id"]
        invs = conn.execute("SELECT * FROM investimentos WHERE usuario_id = ?", (usuario_id,)).fetchall()
        if not invs:
            continue
        invs_com_ops = [
            (inv, conn.execute(
                "SELECT * FROM investimento_operacoes WHERE investimento_id = ? ORDER BY data, id", (inv["id"],)
            ).fetchall())
            for inv in invs
        ]
        primeira = conn.execute(
            "SELECT MIN(data) as data FROM investimento_operacoes WHERE usuario_id = ?", (usuario_id,)
        ).fetchone()
        if not primeira or not primeira["data"]:
            continue

        cache_preco = {}
        linhas = []
        mes = mes_atual if somente_mes_atual else primeira["data"][:7]
        while mes <= mes_atual:
            if mes == mes_atual:
                investido = sum(investimento_computado(conn, i)["valor_investido"] for i in invs)
                atual = sum(investimento_computado(conn, i)["valor_atual"] for i in invs)
            else:
                investido, atual = patrimonio_em_mes(conn, invs_com_ops, mes, cache_preco)
            linhas.append((usuario_id, mes, round(investido, 2), round(atual, 2), agora))
            mes = mes_seguinte(mes)

        conn.executemany(
            "INSERT INTO investimento_snapshot_mensal (usuario_id, mes, valor_investido, valor_atual, atualizado_em) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(usuario_id, mes) DO UPDATE SET "
            "valor_investido = excluded.valor_investido, valor_atual = excluded.valor_atual, "
            "atualizado_em = excluded.atualizado_em",
            linhas,
        )
    conn.commit()


def _loop_atualizar_cotacoes():
    """Roda em segundo plano, mesmo padrão do backup automático — carteira
    pessoal não precisa de cotação em tempo real, então o ciclo é de 1h."""
    while True:
        try:
            conn = get_db()
            atualizar_todas_cotacoes(conn)
            atualizar_snapshots_patrimonio(conn, somente_mes_atual=True)
            conn.close()
        except Exception as e:
            print(f"[investimentos] falha no ciclo de cotações: {e}", flush=True)
        time.sleep(3600)


def iniciar_agendador_cotacoes():
    t = threading.Thread(target=_loop_atualizar_cotacoes, daemon=True)
    t.start()


def atualizar_catalogo_ativos(conn):
    """Catálogo local de tickers pra busca com autocompletar ao cadastrar um
    investimento — sem isso, cada letra digitada bateria na API externa."""
    try:
        if BRAPI_TOKEN:
            resp = requests.get(
                "https://brapi.dev/api/quote/list", params={"token": BRAPI_TOKEN}, timeout=30,
            )
            resp.raise_for_status()
            linhas = []
            for item in resp.json().get("stocks", []):
                sub = item.get("subType")
                if sub == "fii":
                    classe = "fii"
                elif sub == "etf":
                    classe = "etf"
                elif item.get("type") == "bdr" or sub == "bdr":
                    classe = "bdr"
                elif item.get("type") == "stock" or sub in ("stock", "unit"):
                    classe = "acao"
                else:
                    continue  # fi-infra, fi-agro, fip, fidc etc. não têm classe correspondente aqui
                linhas.append((classe, item["stock"], item.get("name") or item["stock"]))
            conn.execute("DELETE FROM ativo_catalogo WHERE classe IN ('acao','fii','etf','bdr')")
            conn.executemany(
                "INSERT INTO ativo_catalogo (classe, ticker, nome) VALUES (?, ?, ?) "
                "ON CONFLICT(classe, ticker) DO UPDATE SET nome = excluded.nome",
                linhas,
            )
            conn.commit()
    except Exception as e:
        print(f"[investimentos] falha ao atualizar catálogo B3: {e}", flush=True)

    try:
        # As 250 maiores por valor de mercado bastam pra uma carteira pessoal —
        # a lista completa da CoinGecko tem mais de 10 mil moedas, quase todas
        # obscuras demais pra valer a pena cachear.
        resp = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency": "brl", "order": "market_cap_desc", "per_page": 250, "page": 1},
            timeout=30,
        )
        resp.raise_for_status()
        linhas = [
            (item["id"], f'{item["name"]} ({item["symbol"].upper()})', item["symbol"].upper(), item.get("image"))
            for item in resp.json()
        ]
        conn.execute("DELETE FROM ativo_catalogo WHERE classe = 'cripto'")
        conn.executemany(
            "INSERT INTO ativo_catalogo (classe, ticker, nome, simbolo, logo_url) VALUES ('cripto', ?, ?, ?, ?) "
            "ON CONFLICT(classe, ticker) DO UPDATE SET nome = excluded.nome, simbolo = excluded.simbolo, "
            "logo_url = excluded.logo_url",
            linhas,
        )
        conn.commit()
    except Exception as e:
        print(f"[investimentos] falha ao atualizar catálogo cripto: {e}", flush=True)


# ------------------- Logo dos ativos -------------------
# Guardada localmente (tabela ativo_logo) e servida pelo próprio app: a página
# nunca aponta pra um CDN de terceiro, então nada vaza sobre a carteira de
# quem abre o app, e a carteira continua desenhando igual se a fonte sair do ar.
# Quem não tem logo cai nas iniciais coloridas que a tela já sabe desenhar —
# é o caso da maioria dos FIIs, que não têm logo em fonte nenhuma (o próprio
# Investidor10 mostra um ícone genérico de prédio pra eles).

LOGO_TAMANHO_MAXIMO = 512 * 1024
# Ticker vem da URL: só letras e números, pra não montar requisição externa
# nem caminho de arquivo com o que o cliente mandar.
RE_CHAVE_LOGO = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


# A logo só pode vir destes lugares. A do CDN da brapi é montada aqui com o
# ticker; a de cripto vem da coluna logo_url do catálogo, que é preenchida com o
# que a CoinGecko responde — dado de fora, e portanto não é destino confiável só
# por já estar gravado no banco.
HOSTS_LOGO = {"icons.brapi.dev", "assets.coingecko.com", "coin-images.coingecko.com"}


def _origem_de_logo_permitida(url):
    partes = urlparse(url or "")
    return partes.scheme == "https" and partes.hostname in HOSTS_LOGO


def _baixar_logo(url):
    if not _origem_de_logo_permitida(url):
        return None, None
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    tipo = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
    # O CDN responde a ticker inexistente com uma página HTML de erro em vez
    # de 404, então o content-type é o que separa logo de "não existe".
    if not tipo.startswith("image/") or len(resp.content) > LOGO_TAMANHO_MAXIMO:
        return None, None
    return resp.content, tipo


def buscar_e_cachear_logo(conn, classe, ticker):
    """Baixa a logo de um ativo e guarda no banco. Devolve (conteudo, tipo) ou
    (None, None). Ações, BDRs, ETFs e stocks vêm do CDN de ícones da brapi.dev
    (aberto, não precisa do token); cripto vem da própria CoinGecko, que já
    entrega a URL da imagem junto do catálogo."""
    url = None
    if classe == "cripto":
        row = conn.execute(
            "SELECT logo_url FROM ativo_catalogo WHERE classe = 'cripto' AND ticker = ?", (ticker,)
        ).fetchone()
        url = row["logo_url"] if row else None
    elif classe in CLASSES_COM_TICKER and ticker_seguro(ticker):
        url = f"https://icons.brapi.dev/icons/{quote(ticker_seguro(ticker).upper(), safe='')}.svg"

    conteudo = tipo = None
    if url:
        try:
            conteudo, tipo = _baixar_logo(url)
        except Exception as e:
            print(f"[investimentos] falha ao baixar logo de {ticker}: {e}", flush=True)

    # Grava mesmo quando falha: é o registro de "já tentei, não tem" que evita
    # martelar a fonte externa a cada vez que a carteira é aberta.
    agora = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO ativo_logo (chave, conteudo, tipo, atualizado_em, tentado_em) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(chave) DO UPDATE SET conteudo = excluded.conteudo, tipo = excluded.tipo, "
        "atualizado_em = excluded.atualizado_em, tentado_em = excluded.tentado_em",
        (ticker, conteudo, tipo, agora if conteudo else None, agora),
    )
    conn.commit()
    return conteudo, tipo


def atualizar_logos(conn):
    """Busca a logo do que está na carteira e ainda não foi tentado. Roda no
    ciclo diário, então quando alguém abre a aba a imagem já está no banco."""
    alvos = conn.execute(
        "SELECT DISTINCT i.ticker, i.classe FROM investimentos i "
        "LEFT JOIN ativo_logo l ON l.chave = i.ticker "
        "WHERE i.ticker IS NOT NULL AND l.chave IS NULL"
    ).fetchall()
    for alvo in alvos:
        buscar_e_cachear_logo(conn, alvo["classe"], alvo["ticker"])


CLASSES_COM_DIVIDENDO_B3 = {"acao", "fii", "bdr", "etf"}
LABEL_DIVIDENDO_PARA_TIPO_PAGAMENTO = {"DIVIDENDO": "dividendo", "JCP": "jscp", "RENDIMENTO": "rendimento"}


def buscar_dividendos_brapi(ticker):
    """Histórico de proventos já declarados de um ticker B3 — mesma API da
    cotação, só pedindo o módulo de dividendos a mais. Só funciona com
    BRAPI_TOKEN configurado (o teste gratuito sem token não libera esse módulo)."""
    seguro = ticker_seguro(ticker)
    if not BRAPI_TOKEN or not seguro:
        return []
    resp = requests.get(
        f"https://brapi.dev/api/quote/{quote(seguro, safe='')}",
        params={"token": BRAPI_TOKEN, "dividends": "true"}, timeout=20,
    )
    resp.raise_for_status()
    resultados = resp.json().get("results", [])
    if not resultados:
        return []
    return resultados[0].get("dividendsData", {}).get("cashDividends") or []


def buscar_dividendos_yahoo(ticker_b3):
    """Plano B pro histórico de proventos. O módulo de dividendos da brapi.dev
    é pago fora dos poucos tickers de demonstração — sem ele a aba Proventos
    ficaria vazia pra quase toda a carteira. O Yahoo entrega menos: só a data
    ex-dividendo e o valor por cota, sem separar dividendo de JCP e sem a data
    de pagamento. Então o que vem daqui é tratado como dividendo pago na
    própria data-com, e é substituído se um dia a brapi.dev responder pelo
    mesmo provento."""
    resp = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_b3}",
        params={"range": "10y", "interval": "1mo", "events": "div"},
        headers={"User-Agent": "Mozilla/5.0"}, timeout=25,
    )
    resp.raise_for_status()
    eventos = (resp.json()["chart"]["result"][0].get("events") or {}).get("dividends") or {}
    return [
        {
            "lastDatePrior": datetime.fromtimestamp(e["date"]).strftime("%Y-%m-%d"),
            "paymentDate": datetime.fromtimestamp(e["date"]).strftime("%Y-%m-%d"),
            "rate": e["amount"], "label": "DIVIDENDO",
        }
        for e in eventos.values() if e.get("date") and e.get("amount")
    ]


def quantidade_em_data(conn, investimento_id, data_limite):
    """Quantas cotas/ações o investimento tinha numa data específica — soma
    os aportes e subtrai os resgates até aquela data, pra saber quem tinha
    direito a um provento declarado na "data base" dele."""
    ops = conn.execute(
        "SELECT tipo, quantidade FROM investimento_operacoes WHERE investimento_id = ? AND data <= ? "
        "AND tipo IN ('aporte', 'resgate') ORDER BY data, id",
        (investimento_id, data_limite),
    ).fetchall()
    quantidade = 0.0
    for o in ops:
        quantidade += (o["quantidade"] or 0) if o["tipo"] == "aporte" else -(o["quantidade"] or 0)
    return quantidade


def _registrar_provento_importado(conn, inv, data_pagamento, data_com, tipo_pagamento, valor_bruto, valor_liquido,
                                  pago, quantidade=None, valor_por_cota=None):
    # Não dá pra usar resolver_conta aqui: ela valida contra o uid() da sessão
    # e a importação também roda no ciclo de segundo plano, fora de qualquer
    # request. O dono é o do próprio investimento.
    conta = conn.execute(
        "SELECT nome FROM contas WHERE id = ? AND usuario_id = ?", (inv["conta_id"], inv["usuario_id"])
    ).fetchone()
    conta_nome = conta["nome"] if conta else ""
    mes = data_pagamento[:7]
    agora = datetime.now().isoformat()
    if pago:
        pago_val, data_pagamento_val, vencimento_val = 1, data_pagamento, ""
    else:
        pago_val, data_pagamento_val, vencimento_val = 0, "", data_pagamento
    cur = conn.execute(
        "INSERT INTO lancamentos (mes, tipo, descricao, valor, vencimento, categoria, conta, conta_id, "
        "pago, data_pagamento, observacao, eh_transferencia, criado_em, usuario_id) "
        "VALUES (?, 'renda', ?, ?, ?, 'Proventos', ?, ?, ?, ?, ?, 0, ?, ?)",
        (mes, f"Provento — {inv['nome']}", valor_liquido, vencimento_val, conta_nome, inv["conta_id"],
         pago_val, data_pagamento_val, "Importado automaticamente (brapi.dev)", agora, inv["usuario_id"]),
    )
    lancamento_id = cur.lastrowid
    # quantidade/preco_unitario aqui são as cotas na data base e o valor por
    # cota anunciado — é o que a tabela "Meus proventos" mostra, e guardar
    # agora evita ter que reconstituir depois a posição daquela data.
    conn.execute(
        "INSERT INTO investimento_operacoes (investimento_id, usuario_id, tipo, valor, data, lancamento_id, "
        "observacao, criado_em, tipo_pagamento, data_com, valor_bruto, quantidade, preco_unitario, origem) "
        "VALUES (?, ?, 'provento', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'auto_brapi')",
        (inv["id"], inv["usuario_id"], valor_liquido, data_pagamento, lancamento_id,
         "Importado automaticamente", agora, tipo_pagamento, data_com, valor_bruto, quantidade, valor_por_cota),
    )


def importar_proventos_automaticos(conn):
    """Busca o histórico de proventos já declarados dos ativos com ticker que
    alguém tem cadastrado e cria a operação sozinho — sem repetir o que já foi
    importado antes. Só ação/FII/ETF/BDR têm esse dado na B3; cripto e os
    ativos americanos não entram aqui. Data de pagamento no passado nasce
    "Pago" (é fato já ocorrido, oficial da B3, não depende de confirmação);
    no futuro nasce "A receber".

    Fonte preferida é a brapi.dev, que traz tipo do pagamento, data-com e data
    de pagamento separadas. Quando ela recusa — o módulo de dividendos é pago
    e o plano gratuito só libera os tickers de demonstração — cai pro Yahoo,
    que tem menos detalhe mas cobre a carteira inteira."""
    invs = conn.execute(
        "SELECT * FROM investimentos WHERE classe IN ({}) AND ticker IS NOT NULL".format(
            ",".join("?" * len(CLASSES_COM_DIVIDENDO_B3))
        ),
        tuple(CLASSES_COM_DIVIDENDO_B3),
    ).fetchall()
    hoje = datetime.now().strftime("%Y-%m-%d")
    for inv in invs:
        dividendos = []
        try:
            dividendos = buscar_dividendos_brapi(inv["ticker"])
        except Exception as e:
            print(f"[investimentos] proventos de {inv['ticker']} pela brapi falharam ({e}); tentando Yahoo", flush=True)
        if not dividendos:
            try:
                dividendos = buscar_dividendos_yahoo(f"{inv['ticker']}.SA")
            except Exception as e:
                print(f"[investimentos] falha ao buscar proventos de {inv['ticker']}: {e}", flush=True)
                continue
        for d in dividendos:
            payment_date = (d.get("paymentDate") or "")[:10]
            data_com = (d.get("lastDatePrior") or "")[:10] or None
            rate = d.get("rate")
            if not payment_date or not rate:
                continue
            tipo_pagamento = LABEL_DIVIDENDO_PARA_TIPO_PAGAMENTO.get((d.get("label") or "").upper(), "rendimento")
            quantidade = quantidade_em_data(conn, inv["id"], data_com or payment_date)
            if quantidade <= 0:
                continue  # não tinha o ativo na data base, não faz jus a esse provento
            valor_bruto = round(quantidade * rate, 2)
            # Um provento é único pela data-base e pelo valor por cota anunciado.
            # Comparar por isso (e não pela data de pagamento) é o que evita
            # duplicar quando o mesmo provento chega pelas duas fontes: o Yahoo
            # não sabe a data de pagamento e usa a própria data-com no lugar.
            ja_existe = conn.execute(
                "SELECT id FROM investimento_operacoes WHERE investimento_id = ? AND tipo = 'provento' AND ("
                "  (COALESCE(data_com, data) = ? AND ABS(COALESCE(preco_unitario, -1) - ?) < 0.000001)"
                # os proventos importados antes de o valor por cota passar a ser
                # gravado só dão pra reconhecer pela data de pagamento e pelo valor
                "  OR (preco_unitario IS NULL AND data = ? AND ABS(COALESCE(valor_bruto, 0) - ?) < 0.01)"
                ")",
                (inv["id"], data_com or payment_date, rate, payment_date, valor_bruto),
            ).fetchone()
            if ja_existe:
                continue
            valor_liquido = round(valor_bruto * 0.85, 2) if tipo_pagamento == "jscp" else valor_bruto
            try:
                _registrar_provento_importado(
                    conn, inv, payment_date, data_com, tipo_pagamento, valor_bruto, valor_liquido,
                    pago=payment_date <= hoje, quantidade=quantidade, valor_por_cota=rate,
                )
                conn.commit()
            except Exception as e:
                print(f"[investimentos] falha ao importar provento de {inv['ticker']}: {e}", flush=True)


def _loop_atualizar_catalogo():
    """A lista de tickers muda bem menos que a cotação — 1x por dia basta.
    O histórico de dividendos também: uma empresa não declara provento novo
    a cada hora, então importar proventos entra nesse mesmo ciclo diário."""
    while True:
        try:
            conn = get_db()
            atualizar_catalogo_ativos(conn)
            atualizar_logos(conn)
            atualizar_historico_cotacoes(conn)
            importar_proventos_automaticos(conn)
            atualizar_snapshots_patrimonio(conn)
            conn.close()
        except Exception as e:
            print(f"[investimentos] falha no ciclo do catálogo: {e}", flush=True)
        time.sleep(86400)


def iniciar_agendador_catalogo():
    t = threading.Thread(target=_loop_atualizar_catalogo, daemon=True)
    t.start()


def valor_atual_renda_fixa(conn, inv, operacoes, ate=None):
    """Cada aporte rende de forma independente a partir da própria data —
    aproximação razoável pra um app de finanças pessoais, não segue a
    metodologia exata de cálculo de CETIP/B3 (dias úteis, base 252 etc.).
    `ate` (AAAA-MM-DD) permite calcular quanto valia numa data passada, o que
    a reconstrução histórica do patrimônio usa."""
    indexador = inv["indexador"]
    taxa = inv["taxa"] or 0
    data_ref = ate or datetime.now().strftime("%Y-%m-%d")
    momento_ref = datetime.strptime(data_ref, "%Y-%m-%d")
    total = 0.0
    for op in operacoes:
        if op["tipo"] in ("provento", "reavaliacao") or op["data"] > data_ref:
            continue
        sinal = 1 if op["tipo"] == "aporte" else -1
        dias = max((momento_ref - datetime.strptime(op["data"], "%Y-%m-%d")).days, 0)
        if indexador == "prefixado":
            fator = (1 + taxa / 100) ** (dias / 365)
        elif indexador in ("cdi", "ipca"):
            fator_indice = fator_indexador_em(conn, indexador, data_ref) / fator_indexador_em(conn, indexador, op["data"])
            if indexador == "cdi":
                fator = 1 + (fator_indice - 1) * (taxa / 100)  # taxa = % do CDI, ex: 110
            else:
                fator = fator_indice * (1 + taxa / 100) ** (dias / 365)  # taxa = adicional fixo a.a. sobre o IPCA
        else:
            fator = 1.0
        total += sinal * op["valor"] * fator
    return round(total, 2)


def investimento_computado(conn, inv):
    operacoes = conn.execute(
        "SELECT * FROM investimento_operacoes WHERE investimento_id = ? ORDER BY data, id",
        (inv["id"],),
    ).fetchall()
    d = dict(inv)
    aportes = sum(o["valor"] for o in operacoes if o["tipo"] == "aporte")
    resgates = sum(o["valor"] for o in operacoes if o["tipo"] == "resgate")
    d["valor_investido"] = round(aportes - resgates, 2)

    if inv["classe"] in CLASSES_COM_TICKER:
        quantidade, custo = 0.0, 0.0
        for o in operacoes:
            if o["tipo"] == "aporte":
                quantidade += o["quantidade"] or 0
                custo += o["valor"]
            elif o["tipo"] == "resgate":
                qtd = o["quantidade"] or 0
                preco_medio_atual = (custo / quantidade) if quantidade else 0
                custo -= preco_medio_atual * qtd
                quantidade -= qtd
        cot = conn.execute(
            "SELECT valor, atualizado_em FROM investimento_cotacoes WHERE chave = ?", (inv["ticker"],)
        ).fetchone()
        fator_cambio = 1.0
        if inv["classe"] in CLASSES_YAHOO:
            cambio = conn.execute(
                "SELECT valor FROM investimento_cotacoes WHERE chave = 'USD_BRL'"
            ).fetchone()
            fator_cambio = cambio["valor"] if cambio else 1.0
        d["quantidade"] = round(quantidade, 8)
        d["preco_medio"] = round(custo / quantidade, 4) if quantidade else 0
        d["cotacao_atual"] = round(cot["valor"] * fator_cambio, 4) if cot else None
        d["cotacao_atualizada_em"] = cot["atualizado_em"] if cot else None
        if cot and quantidade:
            d["valor_atual"] = round(quantidade * cot["valor"] * fator_cambio, 2)
        else:
            # sem cotação em cache ainda (token não configurado ou 1º ciclo não rodou):
            # usa o custo em carteira como aproximação, em vez de deixar em branco.
            d["valor_atual"] = round(custo, 2)
    elif inv["classe"] == "renda_fixa":
        d["valor_atual"] = valor_atual_renda_fixa(conn, inv, operacoes)
    else:  # fundo | outro — sem ticker nem indexador, valor atualizado à mão
        reavaliacoes = [o for o in operacoes if o["tipo"] == "reavaliacao"]
        d["valor_atual"] = reavaliacoes[-1]["valor"] if reavaliacoes else d["valor_investido"]

    base = d["valor_investido"]
    d["rentabilidade_valor"] = round(d["valor_atual"] - base, 2)
    d["rentabilidade_pct"] = round((d["rentabilidade_valor"] / base) * 100, 2) if base > 0 else None
    return d


@app.route("/api/investimentos", methods=["GET"])
def listar_investimentos():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM investimentos WHERE usuario_id = ? ORDER BY criado_em", (uid(),)
    ).fetchall()
    resultado = [investimento_computado(conn, r) for r in rows]
    conn.close()
    return jsonify(resultado)


@app.route("/api/investimentos", methods=["POST"])
def criar_investimento():
    data = request.get_json(force=True)
    nome = (data.get("nome") or "").strip()
    classe = data.get("classe")
    if not nome or classe not in CLASSES_VALIDAS:
        return jsonify({"erro": msg("nome e classe são obrigatórios")}), 400
    conn = get_db()
    conta_id, _ = resolver_conta(conn, data.get("conta_id"))
    if not conta_id:
        conn.close()
        return jsonify({"erro": msg("conta é obrigatória")}), 400
    ticker = normalizar_ticker(classe, data.get("ticker")) if classe in CLASSES_COM_TICKER else None
    if classe in CLASSES_COM_TICKER and not ticker:
        conn.close()
        return jsonify({"erro": msg("ticker é obrigatório pra essa classe")}), 400
    indexador = (data.get("indexador") or None) if classe == "renda_fixa" else None
    taxa = data.get("taxa") if classe == "renda_fixa" else None
    if classe == "renda_fixa" and (not indexador or taxa in (None, "")):
        conn.close()
        return jsonify({"erro": msg("indexador e taxa são obrigatórios pra renda fixa")}), 400
    cur = conn.execute(
        "INSERT INTO investimentos (usuario_id, nome, classe, ticker, conta_id, indexador, taxa, "
        "vencimento, criado_em, emissor, tipo_investimento, liquidez_diaria) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (uid(), nome, classe, ticker, conta_id, indexador,
         float(taxa) if taxa not in (None, "") else None,
         data.get("vencimento") or None, datetime.now().isoformat(),
         (data.get("emissor") or "").strip() or None if classe == "renda_fixa" else None,
         (data.get("tipo_investimento") or "").strip() or None if classe == "renda_fixa" else None,
         1 if (classe == "renda_fixa" and data.get("liquidez_diaria")) else 0),
    )
    conn.commit()
    novo_id = cur.lastrowid
    # Busca a logo já aqui: são ~100ms uma vez só, e assim ela está pronta
    # quando a carteira for desenhada logo em seguida — em vez de o ativo
    # aparecer com as iniciais e trocar de cara um instante depois.
    if ticker:
        try:
            buscar_e_cachear_logo(conn, classe, ticker)
        except Exception as e:
            print(f"[investimentos] falha ao buscar logo de {ticker}: {e}", flush=True)
    conn.close()
    return jsonify({"ok": True, "id": novo_id}), 201


@app.route("/api/investimentos/<int:item_id>", methods=["PUT"])
def editar_investimento(item_id):
    data = request.get_json(force=True)
    conn = get_db()
    row_atual = conn.execute("SELECT classe FROM investimentos WHERE id = ? AND usuario_id = ?", (item_id, uid())).fetchone()
    if not row_atual:
        conn.close()
        return jsonify({"erro": msg("investimento não encontrado")}), 404
    campos, valores = [], []
    if "nome" in data:
        campos.append("nome = ?")
        valores.append((data.get("nome") or "").strip())
    if "ticker" in data:
        campos.append("ticker = ?")
        valores.append(normalizar_ticker(row_atual["classe"], data.get("ticker")))
    if "conta_id" in data:
        conta_id, _ = resolver_conta(conn, data.get("conta_id"))
        if not conta_id:
            conn.close()
            return jsonify({"erro": msg("conta inválida")}), 400
        campos.append("conta_id = ?")
        valores.append(conta_id)
    if "indexador" in data:
        campos.append("indexador = ?")
        valores.append(data.get("indexador") or None)
    if "taxa" in data:
        campos.append("taxa = ?")
        valores.append(float(data["taxa"]) if data.get("taxa") not in (None, "") else None)
    if "vencimento" in data:
        campos.append("vencimento = ?")
        valores.append(data.get("vencimento") or None)
    if "emissor" in data:
        campos.append("emissor = ?")
        valores.append((data.get("emissor") or "").strip() or None)
    if "tipo_investimento" in data:
        campos.append("tipo_investimento = ?")
        valores.append((data.get("tipo_investimento") or "").strip() or None)
    if "liquidez_diaria" in data:
        campos.append("liquidez_diaria = ?")
        valores.append(1 if data.get("liquidez_diaria") else 0)
    if campos:
        valores.append(item_id)
        conn.execute(f"UPDATE investimentos SET {', '.join(campos)} WHERE id = ?", valores)
        conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/investimentos/<int:item_id>", methods=["DELETE"])
def deletar_investimento(item_id):
    conn = get_db()
    if not pertence_ao_usuario(conn, "investimentos", item_id):
        conn.close()
        return jsonify({"erro": msg("investimento não encontrado")}), 404
    lancamentos_vinculados = [
        r["lancamento_id"] for r in conn.execute(
            "SELECT lancamento_id FROM investimento_operacoes "
            "WHERE investimento_id = ? AND lancamento_id IS NOT NULL",
            (item_id,),
        ).fetchall()
    ]
    for lid in lancamentos_vinculados:
        conn.execute("DELETE FROM lancamentos WHERE id = ?", (lid,))
    conn.execute("DELETE FROM investimento_operacoes WHERE investimento_id = ?", (item_id,))
    conn.execute("DELETE FROM investimentos WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/investimentos/<int:investimento_id>/operacoes", methods=["GET"])
def listar_operacoes_investimento(investimento_id):
    conn = get_db()
    if not pertence_ao_usuario(conn, "investimentos", investimento_id):
        conn.close()
        return jsonify({"erro": msg("investimento não encontrado")}), 404
    rows = conn.execute(
        "SELECT * FROM investimento_operacoes WHERE investimento_id = ? ORDER BY data DESC, id DESC",
        (investimento_id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/investimentos/<int:investimento_id>/operacoes", methods=["POST"])
def criar_operacao_investimento(investimento_id):
    data = request.get_json(force=True)
    tipo = data.get("tipo")
    if tipo not in TIPOS_OPERACAO_INVESTIMENTO:
        return jsonify({"erro": msg("tipo de operação inválido")}), 400
    conn = get_db()
    inv = conn.execute(
        "SELECT * FROM investimentos WHERE id = ? AND usuario_id = ?", (investimento_id, uid())
    ).fetchone()
    if not inv:
        conn.close()
        return jsonify({"erro": msg("investimento não encontrado")}), 404
    if tipo == "reavaliacao" and inv["classe"] != "outro":
        conn.close()
        return jsonify({"erro": msg("reavaliação manual só vale pra classe 'outro'")}), 400

    data_op = data.get("data") or datetime.now().strftime("%Y-%m-%d")
    quantidade = preco_unitario = None
    custos_extras = 0.0
    tipo_pagamento = data_com = valor_bruto = None
    if inv["classe"] in CLASSES_COM_TICKER and tipo in ("aporte", "resgate"):
        try:
            quantidade = float(data.get("quantidade"))
            preco_unitario = float(data.get("preco_unitario"))
            custos_extras = float(data.get("custos_extras") or 0)
        except (TypeError, ValueError):
            conn.close()
            return jsonify({"erro": msg("quantidade e preço unitário são obrigatórios")}), 400
        if quantidade <= 0 or preco_unitario <= 0 or custos_extras < 0:
            conn.close()
            return jsonify({"erro": msg("quantidade e preço devem ser maiores que zero")}), 400
        bruto = quantidade * preco_unitario
        # Compra: o custo extra soma ao que sai da conta. Venda: desconta do
        # que entra (corretagem reduz o valor líquido recebido na venda).
        valor = round(bruto + custos_extras, 2) if tipo == "aporte" else round(max(bruto - custos_extras, 0), 2)
    elif tipo == "provento":
        try:
            valor_bruto = float(data.get("valor"))
        except (TypeError, ValueError):
            conn.close()
            return jsonify({"erro": msg("valor é obrigatório")}), 400
        if valor_bruto <= 0:
            conn.close()
            return jsonify({"erro": msg("valor deve ser maior que zero")}), 400
        tipo_pagamento = data.get("tipo_pagamento") or None
        data_com = data.get("data_com") or None
        # JSCP tem 15% de IR retido na fonte — Dividendo e Rendimento não têm
        # imposto. O usuário digita o valor bruto anunciado; o que de fato cai
        # na conta (e vira o lançamento) é o líquido.
        valor = round(valor_bruto * 0.85, 2) if tipo_pagamento == "jscp" else round(valor_bruto, 2)
        # Mesma informação que o provento importado guarda: cotas na data base
        # e valor por cota, pras colunas de "Meus proventos".
        if inv["classe"] in CLASSES_COM_TICKER:
            quantidade = quantidade_em_data(conn, investimento_id, data_com or data_op) or None
            if quantidade:
                preco_unitario = round(valor_bruto / quantidade, 6)
    else:
        try:
            valor = float(data.get("valor"))
        except (TypeError, ValueError):
            conn.close()
            return jsonify({"erro": msg("valor é obrigatório")}), 400
        if valor <= 0:
            conn.close()
            return jsonify({"erro": msg("valor deve ser maior que zero")}), 400

    agora = datetime.now().isoformat()
    observacao = (data.get("observacao") or "").strip()
    lancamento_id = None
    if tipo != "reavaliacao":
        _, conta_nome = resolver_conta(conn, inv["conta_id"])
        mes = data_op[:7]
        if tipo == "aporte":
            tipo_lanc, eh_transf, categoria = "despesa", 1, "Investimentos"
            descricao = f"Aporte — {inv['nome']}"
        elif tipo == "resgate":
            tipo_lanc, eh_transf, categoria = "renda", 1, "Investimentos"
            descricao = f"Resgate — {inv['nome']}"
        else:  # provento
            tipo_lanc, eh_transf, categoria = "renda", 0, "Proventos"
            descricao = f"Provento — {inv['nome']}"
        # Só provento pode nascer "a receber" (data futura, ainda sem cair na
        # conta) — aporte/resgate são sempre uma ação já feita, não dá pra
        # "agendar" uma compra que ainda não aconteceu.
        pago_operacao = bool(data.get("pago", True)) if tipo == "provento" else True
        if pago_operacao:
            pago_val, data_pagamento_val, vencimento_val = 1, data_op, ""
        else:
            pago_val, data_pagamento_val, vencimento_val = 0, "", data_op
        cur = conn.execute(
            "INSERT INTO lancamentos (mes, tipo, descricao, valor, vencimento, categoria, conta, conta_id, "
            "pago, data_pagamento, observacao, eh_transferencia, criado_em, usuario_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mes, tipo_lanc, descricao, valor, vencimento_val, categoria, conta_nome, inv["conta_id"],
             pago_val, data_pagamento_val, observacao, eh_transf, agora, uid()),
        )
        lancamento_id = cur.lastrowid

    conn.execute(
        "INSERT INTO investimento_operacoes (investimento_id, usuario_id, tipo, quantidade, preco_unitario, "
        "valor, data, lancamento_id, observacao, criado_em, custos_extras, tipo_pagamento, data_com, valor_bruto) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (investimento_id, uid(), tipo, quantidade, preco_unitario, valor, data_op, lancamento_id,
         observacao, agora, custos_extras, tipo_pagamento, data_com, valor_bruto),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True}), 201


@app.route("/api/investimentos/operacoes/<int:item_id>", methods=["DELETE"])
def deletar_operacao_investimento(item_id):
    conn = get_db()
    if not pertence_ao_usuario(conn, "investimento_operacoes", item_id):
        conn.close()
        return jsonify({"erro": msg("operação não encontrada")}), 404
    row = conn.execute(
        "SELECT lancamento_id FROM investimento_operacoes WHERE id = ?", (item_id,)
    ).fetchone()
    if row and row["lancamento_id"]:
        conn.execute("DELETE FROM lancamentos WHERE id = ?", (row["lancamento_id"],))
    conn.execute("DELETE FROM investimento_operacoes WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/investimentos/atualizar-cotacoes", methods=["POST"])
def atualizar_cotacoes_agora():
    conn = get_db()
    atualizar_todas_cotacoes(conn)
    # Ativo recém-cadastrado ainda não tem passado nenhum — sem isso ele
    # entraria no gráfico de patrimônio só a partir de hoje, como se tivesse
    # sido comprado agora.
    atualizar_historico_cotacoes(conn, somente_faltantes=True)
    atualizar_logos(conn)
    atualizar_snapshots_patrimonio(conn)
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/investimentos/importar-proventos", methods=["POST"])
def importar_proventos_agora():
    conn = get_db()
    importar_proventos_automaticos(conn)
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/investimentos/resumo", methods=["GET"])
def resumo_investimentos():
    conn = get_db()
    invs = conn.execute("SELECT * FROM investimentos WHERE usuario_id = ?", (uid(),)).fetchall()
    patrimonio_investido = sum(investimento_computado(conn, i)["valor_investido"] for i in invs)
    patrimonio_atual = sum(investimento_computado(conn, i)["valor_atual"] for i in invs)

    try:
        n_meses = max(2, min(24, int(request.args.get("meses", 12))))
    except (TypeError, ValueError):
        n_meses = 12
    meses = []
    m = datetime.now().strftime("%Y-%m")
    for _ in range(n_meses):
        meses.append(m)
        m = mes_anterior(m)
    meses.reverse()
    snapshots = {
        r["mes"]: r for r in conn.execute(
            "SELECT * FROM investimento_snapshot_mensal WHERE usuario_id = ? AND mes IN ({})".format(
                ",".join("?" * len(meses))
            ),
            [uid(), *meses],
        ).fetchall()
    }
    hoje = datetime.now().strftime("%Y-%m")
    evolucao = []
    for m in meses:
        if m == hoje:
            evolucao.append({"mes": m, "valor_investido": patrimonio_investido, "valor_atual": patrimonio_atual})
        elif m in snapshots:
            evolucao.append({
                "mes": m, "valor_investido": snapshots[m]["valor_investido"], "valor_atual": snapshots[m]["valor_atual"],
            })

    variacao_pct_mes = None
    mes_ant = mes_anterior(hoje)
    if mes_ant in snapshots and snapshots[mes_ant]["valor_atual"] > 0:
        variacao_pct_mes = round(
            (patrimonio_atual - snapshots[mes_ant]["valor_atual"]) / snapshots[mes_ant]["valor_atual"] * 100, 2
        )

    # Lucro total = ganho de capital (valorização) + dividendos já recebidos —
    # os dois juntos, igual o card "Lucro total" do print.
    ganho_capital = patrimonio_atual - patrimonio_investido
    dividendos_recebidos_total = conn.execute(
        "SELECT COALESCE(SUM(io.valor), 0) as total FROM investimento_operacoes io "
        "JOIN lancamentos l ON l.id = io.lancamento_id "
        "WHERE io.usuario_id = ? AND io.tipo = 'provento' AND l.pago = 1",
        (uid(),),
    ).fetchone()["total"]

    mes_12m_atras = hoje
    for _ in range(11):
        mes_12m_atras = mes_anterior(mes_12m_atras)
    proventos_recebidos_12m = conn.execute(
        "SELECT COALESCE(SUM(io.valor), 0) as total FROM investimento_operacoes io "
        "JOIN lancamentos l ON l.id = io.lancamento_id "
        "WHERE io.usuario_id = ? AND io.tipo = 'provento' AND l.pago = 1 AND l.mes >= ?",
        (uid(), mes_12m_atras),
    ).fetchone()["total"]

    _, retorno_por_mes = retornos_mensais(conn, uid(), patrimonio_atual)
    rentabilidade_pct_12m = compor_retornos(retorno_por_mes, [m for m in retorno_por_mes if m >= mes_12m_atras])

    rentabilidade_pct_total = (
        round(ganho_capital / patrimonio_investido * 100, 2) if patrimonio_investido > 0 else None
    )

    conn.close()
    return jsonify({
        "patrimonio_investido": round(patrimonio_investido, 2),
        "patrimonio_atual": round(patrimonio_atual, 2),
        "variacao_pct_mes": variacao_pct_mes,
        "evolucao": evolucao,
        "ganho_capital": round(ganho_capital, 2),
        "dividendos_recebidos_total": round(dividendos_recebidos_total, 2),
        "lucro_total": round(ganho_capital + dividendos_recebidos_total, 2),
        "proventos_recebidos_12m": round(proventos_recebidos_12m, 2),
        "rentabilidade_pct_12m": rentabilidade_pct_12m,
        "rentabilidade_pct_total": rentabilidade_pct_total,
    })


def _rentabilidade_cdi_periodo(conn, data_inicio, data_fim):
    """Quanto o CDI puro rendeu entre duas datas, em % — pra comparar com a
    rentabilidade da carteira (aba Rentabilidade)."""
    fator_inicio = fator_indexador_em(conn, "cdi", data_inicio)
    fator_fim = fator_indexador_em(conn, "cdi", data_fim)
    if not fator_inicio:
        return None
    return round((fator_fim / fator_inicio - 1) * 100, 2)


def retornos_mensais(conn, usuario_id, patrimonio_atual):
    """Quanto a carteira rendeu em cada mês, isoladamente. Descontar o dinheiro
    que entrou/saiu no mês é o que separa "rendeu" de "aportei mais": sem isso
    um aporte grande apareceria como rentabilidade altíssima. É a fórmula de
    Dietz simplificada — assume o fluxo no começo do mês em vez de ponderar
    por dia, aproximação suficiente pro nível deste app."""
    snapshots = conn.execute(
        "SELECT mes, valor_investido, valor_atual FROM investimento_snapshot_mensal "
        "WHERE usuario_id = ? ORDER BY mes",
        (usuario_id,),
    ).fetchall()
    fluxos = {
        r["mes"]: r["fluxo"] for r in conn.execute(
            "SELECT substr(data, 1, 7) as mes, "
            "SUM(CASE WHEN tipo = 'aporte' THEN valor WHEN tipo = 'resgate' THEN -valor ELSE 0 END) as fluxo "
            "FROM investimento_operacoes WHERE usuario_id = ? AND tipo IN ('aporte', 'resgate') "
            "GROUP BY substr(data, 1, 7)",
            (usuario_id,),
        ).fetchall()
    }
    valores_por_mes = {s["mes"]: s["valor_atual"] for s in snapshots}
    valores_por_mes[datetime.now().strftime("%Y-%m")] = patrimonio_atual

    retorno_por_mes = {}
    meses_ordenados = sorted(valores_por_mes.keys())
    for i, mes in enumerate(meses_ordenados):
        anterior = valores_por_mes[meses_ordenados[i - 1]] if i > 0 else 0.0
        base = anterior + fluxos.get(mes, 0.0)
        if base > 0:
            retorno_por_mes[mes] = round((valores_por_mes[mes] - base) / base * 100, 2)
    return snapshots, retorno_por_mes


def compor_retornos(retorno_por_mes, meses_janela):
    """Rentabilidade de um período inteiro a partir dos retornos mês a mês —
    juros compostos, não soma simples."""
    fator, achou = 1.0, False
    for m in meses_janela:
        if m in retorno_por_mes:
            fator *= 1 + retorno_por_mes[m] / 100
            achou = True
    return round((fator - 1) * 100, 2) if achou else None


@app.route("/api/investimentos/rentabilidade", methods=["GET"])
def rentabilidade_investimentos():
    conn = get_db()
    invs = conn.execute("SELECT * FROM investimentos WHERE usuario_id = ?", (uid(),)).fetchall()
    patrimonio_investido = sum(investimento_computado(conn, i)["valor_investido"] for i in invs)
    patrimonio_atual = sum(investimento_computado(conn, i)["valor_atual"] for i in invs)

    hoje = datetime.now().strftime("%Y-%m-%d")
    hoje_mes = hoje[:7]

    primeira_op = conn.execute(
        "SELECT MIN(data) as data FROM investimento_operacoes WHERE usuario_id = ? AND tipo = 'aporte'",
        (uid(),),
    ).fetchone()
    data_inicio_total = primeira_op["data"] if primeira_op and primeira_op["data"] else hoje

    rentabilidade_total_pct = (
        round((patrimonio_atual - patrimonio_investido) / patrimonio_investido * 100, 2)
        if patrimonio_investido > 0 else None
    )
    cdi_total_pct = _rentabilidade_cdi_periodo(conn, data_inicio_total, hoje)

    mes_12m_atras = hoje_mes
    for _ in range(11):
        mes_12m_atras = mes_anterior(mes_12m_atras)
    cdi_12m_pct = _rentabilidade_cdi_periodo(conn, f"{mes_12m_atras}-01", hoje)
    cdi_mes_pct = _rentabilidade_cdi_periodo(conn, f"{hoje_mes}-01", hoje)

    snapshots, retorno_por_mes = retornos_mensais(conn, uid(), patrimonio_atual)

    # Série acumulada mês a mês, pra desenhar junto com o CDI no gráfico —
    # é a rentabilidade acumulada desde o início até aquele ponto, não o
    # ganho só daquele mês (mesma leitura do "Rentabilidade Total" do card).
    serie = [
        {
            "mes": s["mes"],
            "rentabilidade_pct": round((s["valor_atual"] - s["valor_investido"]) / s["valor_investido"] * 100, 2)
            if s["valor_investido"] > 0 else None,
            "cdi_pct": _rentabilidade_cdi_periodo(conn, data_inicio_total, f"{s['mes']}-01"),
        }
        for s in snapshots
    ]
    if not snapshots or snapshots[-1]["mes"] != hoje_mes:
        serie.append({"mes": hoje_mes, "rentabilidade_pct": rentabilidade_total_pct, "cdi_pct": cdi_total_pct})

    tabela = []
    acumulado_fator = 1.0
    for ano in sorted({m[:4] for m in retorno_por_mes}):
        meses_do_ano = {m[5:7]: r for m, r in retorno_por_mes.items() if m[:4] == ano}
        fator_ano = 1.0
        for m in sorted(meses_do_ano):
            fator_ano *= 1 + meses_do_ano[m] / 100
        acumulado_fator *= fator_ano
        tabela.append({
            "ano": ano, "meses": meses_do_ano,
            "retorno_anual": round((fator_ano - 1) * 100, 2),
            "acumulado": round((acumulado_fator - 1) * 100, 2),
        })
    tabela.reverse()

    # Os cards de 12 meses e do mês compõem os retornos mensais em vez de
    # comparar patrimônio com patrimônio: quem aportou no meio do período
    # veria o próprio aporte contado como rendimento.
    rentabilidade_12m_pct = compor_retornos(retorno_por_mes, [m for m in retorno_por_mes if m >= mes_12m_atras])
    rentabilidade_mes_pct = retorno_por_mes.get(hoje_mes)

    conn.close()
    return jsonify({
        "rentabilidade_total_pct": rentabilidade_total_pct, "cdi_total_pct": cdi_total_pct,
        "rentabilidade_12m_pct": rentabilidade_12m_pct, "cdi_12m_pct": cdi_12m_pct,
        "rentabilidade_mes_pct": rentabilidade_mes_pct, "cdi_mes_pct": cdi_mes_pct,
        "serie": serie,
        "tabela": tabela,
    })


@app.route("/api/investimentos/proventos", methods=["GET"])
def resumo_proventos():
    try:
        n_meses = max(2, min(24, int(request.args.get("meses", 12))))
    except (TypeError, ValueError):
        n_meses = 12
    conn = get_db()
    linhas = conn.execute(
        "SELECT io.*, i.nome as investimento_nome, i.ticker as ticker, i.classe as classe, "
        "l.pago as pago, l.mes as lancamento_mes "
        "FROM investimento_operacoes io "
        "JOIN investimentos i ON i.id = io.investimento_id "
        "LEFT JOIN lancamentos l ON l.id = io.lancamento_id "
        "WHERE io.usuario_id = ? AND io.tipo = 'provento' ORDER BY io.data DESC, io.id DESC",
        (uid(),),
    ).fetchall()
    conn.close()

    hoje = datetime.now().strftime("%Y-%m")
    total_a_receber = sum(r["valor"] for r in linhas if not r["pago"])
    total_carteira = sum(r["valor"] for r in linhas if r["pago"])

    mes_12m_atras = hoje
    for _ in range(11):
        mes_12m_atras = mes_anterior(mes_12m_atras)

    evolucao_por_mes = {}
    por_ativo_12m = {}
    for r in linhas:
        mes = (r["lancamento_mes"] or r["data"][:7])
        bucket = evolucao_por_mes.setdefault(mes, {"recebido": 0.0, "a_receber": 0.0})
        if r["pago"]:
            bucket["recebido"] += r["valor"]
        else:
            bucket["a_receber"] += r["valor"]
        # A rosca "Distribuição de proventos" é da mesma janela de 12 meses do
        # card de média mensal ao lado dela — misturar tudo desde sempre daria
        # uma fatia enorme pro ativo mais antigo, não pro que mais paga hoje.
        if mes >= mes_12m_atras and r["pago"]:
            chave = r["ticker"] or r["investimento_nome"]
            por_ativo_12m[chave] = por_ativo_12m.get(chave, 0.0) + r["valor"]

    meses = []
    m = hoje
    for _ in range(n_meses):
        meses.append(m)
        m = mes_anterior(m)
    meses.reverse()
    evolucao = [
        {"mes": m, "recebido": round(evolucao_por_mes.get(m, {}).get("recebido", 0), 2),
         "a_receber": round(evolucao_por_mes.get(m, {}).get("a_receber", 0), 2)}
        for m in meses
    ]

    evolucao_anual_dict = {}
    for m, v in evolucao_por_mes.items():
        bucket = evolucao_anual_dict.setdefault(m[:4], {"recebido": 0.0, "a_receber": 0.0})
        bucket["recebido"] += v["recebido"]
        bucket["a_receber"] += v["a_receber"]
    evolucao_anual = [
        {"ano": ano, "recebido": round(v["recebido"], 2), "a_receber": round(v["a_receber"], 2)}
        for ano, v in sorted(evolucao_anual_dict.items())
    ]

    total_12m = round(sum(e["recebido"] for e in evolucao), 2)
    media_mensal_12m = round(total_12m / 12, 2)

    tabela_mes_atual = [dict(r) for r in linhas if (r["lancamento_mes"] or r["data"][:7]) == hoje]

    # Lista completa — é a tabela "Meus proventos": um provento por linha, com
    # o que a corretora informa (data com, data de pagamento, cotas na data
    # base, valor por cota) e o status do pagamento.
    detalhados = [
        {
            "id": r["id"], "investimento_id": r["investimento_id"], "ativo": r["investimento_nome"],
            "ticker": r["ticker"], "classe": r["classe"], "pago": bool(r["pago"]),
            "tipo_pagamento": r["tipo_pagamento"], "data_com": r["data_com"], "data_pagamento": r["data"],
            "quantidade": r["quantidade"], "valor_por_cota": r["preco_unitario"],
            "valor_bruto": r["valor_bruto"] if r["valor_bruto"] is not None else r["valor"],
            "valor_liquido": r["valor"], "lancamento_id": r["lancamento_id"],
            "automatico": r["origem"] == "auto_brapi",
        }
        for r in linhas
    ]

    # Histórico ano×mês: uma linha por ano, uma coluna por mês (01..12) —
    # varre tudo que já existe (evolucao_por_mes não tem limite de meses,
    # diferente de `evolucao` acima), não só a janela de n_meses.
    mes_atual_num = int(hoje[5:7])
    ano_atual = hoje[:4]
    por_ano = {}
    for m, v in evolucao_por_mes.items():
        if v["recebido"] <= 0:
            continue
        ano, mes_num = m.split("-")
        por_ano.setdefault(ano, {})[mes_num] = round(v["recebido"], 2)
    historico_anual = []
    for ano in sorted(por_ano.keys(), reverse=True):
        meses_do_ano = por_ano[ano]
        total_ano = round(sum(meses_do_ano.values()), 2)
        divisor = mes_atual_num if ano == ano_atual else 12
        historico_anual.append({
            "ano": ano, "meses": meses_do_ano, "total": total_ano,
            "media": round(total_ano / divisor, 2) if divisor else 0,
        })

    return jsonify({
        "total_a_receber": round(total_a_receber, 2),
        "total_carteira": round(total_carteira, 2),
        "total_12m": total_12m,
        "media_mensal_12m": media_mensal_12m,
        "evolucao": evolucao,
        "evolucao_anual": evolucao_anual,
        "por_ativo": [{"nome": k, "valor": round(v, 2)} for k, v in sorted(por_ativo_12m.items(), key=lambda x: -x[1])],
        "tabela_mes_atual": tabela_mes_atual,
        "detalhados": detalhados,
        "historico_anual": historico_anual,
    })


@app.route("/api/investimentos/alocacao-ideal", methods=["GET"])
def obter_alocacao_ideal():
    conn = get_db()
    rows = conn.execute(
        "SELECT classe, percentual FROM investimento_alocacao_ideal WHERE usuario_id = ?", (uid(),)
    ).fetchall()
    conn.close()
    return jsonify({r["classe"]: r["percentual"] for r in rows})


@app.route("/api/investimentos/alocacao-ideal", methods=["PUT"])
def definir_alocacao_ideal():
    data = request.get_json(force=True) or {}
    conn = get_db()
    conn.execute("DELETE FROM investimento_alocacao_ideal WHERE usuario_id = ?", (uid(),))
    linhas = []
    for classe, percentual in data.items():
        if classe not in CLASSES_VALIDAS:
            continue
        try:
            percentual = float(percentual)
        except (TypeError, ValueError):
            continue
        if percentual > 0:
            linhas.append((uid(), classe, percentual))
    if linhas:
        conn.executemany(
            "INSERT INTO investimento_alocacao_ideal (usuario_id, classe, percentual) VALUES (?, ?, ?)", linhas
        )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/investimentos/buscar-ativos", methods=["GET"])
def buscar_ativos_catalogo():
    """Autocompletar por classe, servido do catálogo local (ativo_catalogo) —
    não bate na B3/CoinGecko a cada letra digitada, só no ciclo diário."""
    classe = request.args.get("classe", "")
    q = (request.args.get("q") or "").strip()
    if classe not in CLASSES_COM_TICKER or len(q) < 2:
        return jsonify([])
    conn = get_db()
    if classe in CLASSES_YAHOO:
        resultado = buscar_e_cachear_yahoo(conn, classe, q)
    else:
        rows = conn.execute(
            "SELECT ticker, nome FROM ativo_catalogo WHERE classe = ? AND (ticker LIKE ? OR nome LIKE ?) "
            "ORDER BY (ticker LIKE ?) DESC, ticker LIMIT 8",
            (classe, f"{q}%", f"%{q}%", f"{q}%"),
        ).fetchall()
        resultado = [dict(r) for r in rows]
    conn.close()
    return jsonify(resultado)


@app.route("/api/investimentos/logo/<chave>", methods=["GET"])
def logo_ativo(chave):
    """Serve a logo do cache local. Nunca redireciona pro CDN de origem — a
    página não pode apontar pra fora, senão a lista de ativos de quem abre o
    app vazaria pro dono do CDN em forma de requisições."""
    if not RE_CHAVE_LOGO.match(chave):
        return "", 404
    conn = get_db()
    row = conn.execute("SELECT conteudo, tipo FROM ativo_logo WHERE chave = ?", (chave,)).fetchone()
    if row is None:
        # Primeira vez que alguém pede esse ativo: busca agora, em vez de
        # esperar o ciclo diário. A classe sai do investimento, e se ele ainda
        # não existe (é o autocompletar mostrando a marca antes de o usuário
        # escolher), sai do catálogo — que é público, não é dado de ninguém.
        origem = conn.execute(
            "SELECT classe FROM investimentos WHERE ticker = ? AND usuario_id = ? LIMIT 1", (chave, uid())
        ).fetchone() or conn.execute(
            "SELECT classe FROM ativo_catalogo WHERE ticker = ? LIMIT 1", (chave,)
        ).fetchone()
        if origem:
            buscar_e_cachear_logo(conn, origem["classe"], chave)
            row = conn.execute("SELECT conteudo, tipo FROM ativo_logo WHERE chave = ?", (chave,)).fetchone()
    conn.close()
    if not row or not row["conteudo"]:
        return "", 404  # a tela desenha as iniciais coloridas no lugar
    resp = app.response_class(bytes(row["conteudo"]), mimetype=row["tipo"] or "image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=604800"  # logo não muda de semana pra semana
    return resp


@app.route("/api/investimentos/cotacao", methods=["GET"])
def cotacao_ativo_avulsa():
    """Cotação atual de um ticker específico, já convertida pra R$ — usada pra
    pré-preencher o preço no modal assim que o usuário escolhe o ativo. Se
    ainda não tiver em cache (ex: primeira vez que alguém compra esse ticker —
    o ciclo de 1h só atualiza quem já é um investimento existente, e esse
    ainda não é), busca ao vivo agora em vez de deixar o campo em branco."""
    classe = request.args.get("classe", "")
    ticker_bruto = (request.args.get("ticker") or "").strip()
    # Esta rota busca a cotação ao vivo, então o ticker vai direto da query
    # string para a URL da fonte externa — é o caminho mais curto entre o
    # cliente e uma requisição de saída, e o que mais precisa da conferência.
    ticker_bruto = ticker_seguro(ticker_bruto)
    if classe not in CLASSES_COM_TICKER or not ticker_bruto:
        return jsonify({"preco": None})
    # CoinGecko exige o id em minúsculas (ex: "bitcoin") — as outras classes
    # usam o ticker em maiúsculas, convenção já seguida no resto do módulo.
    ticker = ticker_bruto.lower() if classe == "cripto" else ticker_bruto.upper()

    conn = get_db()

    def preco_cache(chave):
        row = conn.execute(
            "SELECT valor, atualizado_em FROM investimento_cotacoes WHERE chave = ?", (chave,)
        ).fetchone()
        return (row["valor"], row["atualizado_em"]) if row else (None, None)

    def gravar(chave, valor):
        agora = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO investimento_cotacoes (chave, valor, atualizado_em) VALUES (?, ?, ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor, atualizado_em = excluded.atualizado_em",
            (chave, valor, agora),
        )
        conn.commit()
        return agora

    valor, atualizado_em = preco_cache(ticker)
    if valor is None:
        try:
            if classe in CLASSES_YAHOO:
                valor = buscar_cotacoes_yahoo([ticker]).get(ticker)
            elif classe == "cripto":
                valor = buscar_cotacoes_cripto([ticker]).get(ticker)
            else:
                valor = buscar_cotacoes_brapi([ticker]).get(ticker)
        except Exception as e:
            print(f"[investimentos] falha ao buscar cotação avulsa de {ticker}: {e}", flush=True)
            valor = None
        if valor is not None:
            atualizado_em = gravar(ticker, valor)

    if valor is None:
        conn.close()
        return jsonify({"preco": None})

    fator_cambio = 1.0
    if classe in CLASSES_YAHOO:
        fator_cambio, _ = preco_cache("USD_BRL")
        if fator_cambio is None:
            try:
                fator_cambio = buscar_cambio_usd_brl()
            except Exception as e:
                print(f"[investimentos] falha ao buscar câmbio avulso: {e}", flush=True)
                fator_cambio = None
            if fator_cambio:
                gravar("USD_BRL", fator_cambio)
            else:
                fator_cambio = 1.0

    conn.close()
    return jsonify({"preco": round(valor * fator_cambio, 4), "atualizado_em": atualizado_em})


# ---------------- Holerites ----------------

@app.route("/api/holerites", methods=["GET"])
def listar_holerites():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM holerites WHERE usuario_id = ? ORDER BY COALESCE(referencia,'') DESC, criado_em DESC",
        (uid(),),
    ).fetchall()
    resultado = []
    for r in rows:
        h = dict(r)
        ids = [i for i in (h.get("lancamento_id"), h.get("lancamento_adiantamento_id")) if i]
        lancamentos = []
        if ids:
            placeholders = ",".join("?" * len(ids))
            lancamentos = [dict(x) for x in conn.execute(
                f"SELECT id, descricao, valor, data_pagamento, pago FROM lancamentos "
                f"WHERE id IN ({placeholders}) ORDER BY data_pagamento",
                ids,
            ).fetchall()]
        h["lancamentos"] = lancamentos
        try:
            h["itens"] = json.loads(h.pop("itens_json") or "[]")
        except ValueError:
            h["itens"] = []
        resultado.append(h)
    conn.close()
    return jsonify(resultado)


@app.route("/api/holerites", methods=["POST"])
def enviar_holerite():
    if "arquivo" not in request.files:
        return jsonify({"erro": msg("nenhum arquivo enviado")}), 400
    arquivo = request.files["arquivo"]
    if arquivo.filename == "":
        return jsonify({"erro": msg("arquivo sem nome")}), 400
    if not arquivo.filename.lower().endswith(".pdf"):
        return jsonify({"erro": msg("envie um arquivo PDF")}), 400

    nome_seguro = secure_filename(arquivo.filename)
    nome_final = f"{uid()}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{nome_seguro}"
    os.makedirs(HOLERITES_DIR, exist_ok=True)
    caminho = caminho_em(HOLERITES_DIR, nome_final)
    if not caminho:
        return jsonify({"erro": msg("nome de arquivo inválido")}), 400
    arquivo.save(caminho)

    try:
        leitor = PdfReader(caminho)
        texto = "\n".join((pagina.extract_text() or "") for pagina in leitor.pages)
    except Exception:
        texto = ""
    dados = extrair_dados_holerite(texto)

    lancar_receita = request.form.get("lancar_receita", "true").lower() != "false"
    lancamento_id = None
    lancamento_adiantamento_id = None
    conn = get_db()
    if lancar_receita and dados["referencia"]:
        ano_r, mes_r = dados["referencia"].split("-")
        rotulo = "Férias" if dados["eh_ferias"] else "Salário"
        # Remove lançamentos de renda "previstos" (salário/adiantamento lançados à mão antes do
        # holerite chegar) desse mês, pra não duplicar a renda real que estamos importando agora.
        previstos = conn.execute(
            "SELECT id, comprovante FROM lancamentos WHERE usuario_id = ? AND mes = ? "
            "AND tipo = 'renda' AND categoria = 'Salário' AND previsto = 1",
            (uid(), dados["referencia"]),
        ).fetchall()
        for p in previstos:
            if p["comprovante"]:
                caminho = os.path.join(COMPROVANTES_DIR, p["comprovante"])
                if os.path.exists(caminho):
                    os.remove(caminho)
            conn.execute("DELETE FROM lancamentos WHERE id = ?", (p["id"],))
        if dados["total_liquido"]:
            cur = conn.execute(
                """INSERT INTO lancamentos
                   (mes, tipo, descricao, valor, vencimento, categoria, pago, data_pagamento, criado_em, usuario_id)
                   VALUES (?, 'renda', ?, ?, '', 'Salário', 1, ?, ?, ?)""",
                (dados["referencia"], f"{rotulo} ({mes_r}/{ano_r})", dados["total_liquido"],
                 dados["recebido_em"] or "", datetime.now().isoformat(), uid()),
            )
            lancamento_id = cur.lastrowid
        if dados["adiantamento"]:
            cur = conn.execute(
                """INSERT INTO lancamentos
                   (mes, tipo, descricao, valor, vencimento, categoria, pago, data_pagamento, criado_em, usuario_id)
                   VALUES (?, 'renda', ?, ?, '', 'Salário', 1, ?, ?, ?)""",
                (dados["referencia"], f"Adiantamento quinzenal ({mes_r}/{ano_r})", dados["adiantamento"],
                 f"{dados['referencia']}-15", datetime.now().isoformat(), uid()),
            )
            lancamento_adiantamento_id = cur.lastrowid

    cur = conn.execute(
        """INSERT INTO holerites
           (usuario_id, referencia, recebido_em, total_proventos, total_descontos, total_liquido,
            adiantamento, itens_json, arquivo, lancamento_id, lancamento_adiantamento_id, criado_em)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (uid(), dados["referencia"], dados["recebido_em"], dados["total_proventos"],
         dados["total_descontos"], dados["total_liquido"], dados["adiantamento"],
         json.dumps(dados["itens"]), nome_final, lancamento_id, lancamento_adiantamento_id,
         datetime.now().isoformat()),
    )
    conn.commit()
    novo_id = cur.lastrowid
    row = dict(conn.execute("SELECT * FROM holerites WHERE id = ?", (novo_id,)).fetchone())
    row["itens"] = json.loads(row.pop("itens_json") or "[]")
    conn.close()
    return jsonify(row), 201


@app.route("/api/holerites/<int:item_id>", methods=["PUT"])
def editar_holerite(item_id):
    data = request.get_json(force=True)
    conn = get_db()
    if not pertence_ao_usuario(conn, "holerites", item_id):
        conn.close()
        return jsonify({"erro": msg("holerite não encontrado")}), 404
    campos, valores = [], []
    for campo in ("referencia", "recebido_em"):
        if campo in data:
            campos.append(f"{campo} = ?")
            valores.append(data.get(campo) or None)
    for campo in ("total_proventos", "total_descontos", "total_liquido", "adiantamento"):
        if campo in data:
            campos.append(f"{campo} = ?")
            valores.append(float(data[campo]) if data[campo] not in (None, "") else None)
    if campos:
        valores.append(item_id)
        conn.execute(f"UPDATE holerites SET {', '.join(campos)} WHERE id = ?", valores)
        row = conn.execute("SELECT * FROM holerites WHERE id = ?", (item_id,)).fetchone()
        if row["lancamento_id"] and "total_liquido" in data:
            conn.execute(
                "UPDATE lancamentos SET valor = ?, mes = ?, data_pagamento = ? WHERE id = ? AND usuario_id = ?",
                (row["total_liquido"], row["referencia"], row["recebido_em"] or "", row["lancamento_id"], uid()),
            )
        if row["lancamento_adiantamento_id"] and "adiantamento" in data:
            conn.execute(
                "UPDATE lancamentos SET valor = ?, mes = ? WHERE id = ? AND usuario_id = ?",
                (row["adiantamento"], row["referencia"], row["lancamento_adiantamento_id"], uid()),
            )
        conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/holerites/<int:item_id>", methods=["DELETE"])
def deletar_holerite(item_id):
    conn = get_db()
    if not pertence_ao_usuario(conn, "holerites", item_id):
        conn.close()
        return jsonify({"erro": msg("holerite não encontrado")}), 404
    row = conn.execute("SELECT * FROM holerites WHERE id = ?", (item_id,)).fetchone()
    if row["lancamento_id"]:
        conn.execute("DELETE FROM lancamentos WHERE id = ? AND usuario_id = ?", (row["lancamento_id"], uid()))
    if row["lancamento_adiantamento_id"]:
        conn.execute("DELETE FROM lancamentos WHERE id = ? AND usuario_id = ?", (row["lancamento_adiantamento_id"], uid()))
    conn.execute("DELETE FROM holerites WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    caminho = os.path.join(HOLERITES_DIR, row["arquivo"])
    if os.path.exists(caminho):
        os.remove(caminho)
    return jsonify({"ok": True})


@app.route("/api/notas-fiscais/analisar", methods=["POST"])
def analisar_nota_fiscal():
    """Lê uma foto/PDF de cupom fiscal via OCR e devolve os dados encontrados —
    não grava nada; a criação da despesa é um POST normal em /api/lancamentos."""
    if "arquivo" not in request.files:
        return jsonify({"erro": msg("nenhum arquivo enviado")}), 400
    arquivo = request.files["arquivo"]
    if arquivo.filename == "":
        return jsonify({"erro": msg("arquivo sem nome")}), 400

    conteudo = arquivo.read()
    nome = arquivo.filename.lower()
    try:
        if nome.endswith(".pdf"):
            doc = fitz.open(stream=conteudo, filetype="pdf")
            pix = doc[0].get_pixmap(dpi=500, colorspace=fitz.csRGB, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        else:
            img = Image.open(io.BytesIO(conteudo)).convert("RGB")
    except Exception:
        return jsonify({"erro": msg("não foi possível ler o arquivo — envie um PDF ou uma foto (JPG/PNG)")}), 400

    chave_acesso_qr = None
    try:
        for resultado in zbar_decode(img):
            m = re.search(r"p=(\d{44})\|", resultado.data.decode("utf-8", "ignore"))
            if m:
                chave_acesso_qr = m.group(1)
                break
    except Exception as e:
        print(f"[nota-fiscal] não deu para ler o QR code: {e}", flush=True)

    # Cupons térmicos fotografados saem com contraste baixo — preto e branco puro
    # (com um leve realce de nitidez) ajuda bastante o OCR a acertar os dígitos.
    try:
        preparada = ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=1)
        preparada = preparada.point(lambda p: 255 if p > 150 else 0).filter(ImageFilter.SHARPEN)
        texto = pytesseract.image_to_string(preparada, lang="por", config="--psm 6")
    except Exception:
        texto = ""

    dados = extrair_dados_nota_fiscal(texto, chave_acesso_qr)
    dados["texto_bruto"] = texto[:3000]
    return jsonify(dados)


@app.route("/api/holerites/<int:item_id>/arquivo", methods=["GET"])
def baixar_holerite(item_id):
    conn = get_db()
    row = conn.execute(
        "SELECT arquivo FROM holerites WHERE id = ? AND usuario_id = ?", (item_id, uid())
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({"erro": msg("holerite não encontrado")}), 404
    return send_from_directory(HOLERITES_DIR, row["arquivo"])


# ---------------- Dashboard ----------------

@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    mes = request.args.get("mes", datetime.now().strftime("%Y-%m"))
    hoje_dt = datetime.now()
    ano, m = map(int, mes.split("-"))
    ultimo_dia_mes = calendar.monthrange(ano, m)[1]
    e_mes_atual = mes == hoje_dt.strftime("%Y-%m")
    dias_restantes = (ultimo_dia_mes - hoje_dt.day + 1) if e_mes_atual else ultimo_dia_mes

    conn = get_db()
    garantir_recorrentes(conn, mes, uid())

    atual = totais_do_mes(conn, mes, uid())
    anterior = totais_do_mes(conn, mes_anterior(mes), uid())

    receita_total = atual["receita_total"]
    receita_recebida = atual["receita_recebida"]
    despesa_total = atual["despesa_total"]
    despesa_paga = atual["despesa_paga"]
    despesa_pendente = despesa_total - despesa_paga
    receita_pendente = receita_total - receita_recebida

    saldo_atual = receita_recebida - despesa_paga
    disponivel = receita_total - despesa_total

    # Conta a renda que ainda vai entrar no mês (ex: salário do dia 30), não só o que já foi recebido —
    # senão esse número contradiz o "Dinheiro disponível" mostrado logo acima, que já conta o mês inteiro.
    livre_para_gastar = saldo_atual + receita_pendente - despesa_pendente
    gasto_diario = round(max(livre_para_gastar, 0) / dias_restantes, 2) if dias_restantes > 0 else 0

    # Inclui também contas já vencidas (vencimento antes de hoje) e não só as dos
    # próximos 7 dias — senão uma conta atrasada podia sumir desse aviso.
    limite_alerta = (hoje_dt + timedelta(days=7)).strftime("%Y-%m-%d")
    vencendo = conn.execute(
        "SELECT * FROM lancamentos WHERE tipo = 'despesa' AND pago = 0 AND vencimento != '' "
        "AND vencimento <= ? AND usuario_id = ? ORDER BY vencimento",
        (limite_alerta, uid()),
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
    # Parte do saldo real das contas (não só das receitas/despesas já "pagas" deste mês),
    # senão esse número sempre bate exatamente com "disponivel" e vira um card duplicado.
    previsao_fim_mes = saldo_total_contas + receita_pendente - despesa_pendente

    investimentos = conn.execute(
        "SELECT * FROM investimentos WHERE usuario_id = ?", (uid(),)
    ).fetchall()
    patrimonio_investido = sum(investimento_computado(conn, i)["valor_atual"] for i in investimentos)

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
        "patrimonio_investido": round(patrimonio_investido, 2),
        "patrimonio_total": round(saldo_total_contas + patrimonio_investido, 2),
        "mes_anterior": {"receita": anterior["receita_total"], "despesa": anterior["despesa_total"]},
        "mes_atual_grafico": {"receita": receita_total, "despesa": despesa_total},
    })


@app.route("/api/tendencia", methods=["GET"])
def tendencia():
    """Receita, despesa e saldo dos últimos N meses (padrão 6), pro gráfico de tendência."""
    mes_final = request.args.get("mes", datetime.now().strftime("%Y-%m"))
    try:
        n_meses = max(2, min(24, int(request.args.get("meses", 6))))
    except (TypeError, ValueError):
        n_meses = 6

    meses = []
    m = mes_final
    for _ in range(n_meses):
        meses.append(m)
        m = mes_anterior(m)
    meses.reverse()

    conn = get_db()
    resultado = []
    for m in meses:
        t = totais_do_mes(conn, m, uid())
        resultado.append({
            "mes": m,
            "receita": t["receita_total"],
            "despesa": t["despesa_total"],
            "saldo": t["receita_total"] - t["despesa_total"],
        })
    conn.close()
    return jsonify(resultado)


@app.route("/api/fluxo-caixa", methods=["GET"])
def fluxo_caixa():
    """Entradas, saídas e saldo acumulado dia a dia no mês, pro gráfico de fluxo de caixa."""
    mes = request.args.get("mes", datetime.now().strftime("%Y-%m"))
    ano, m = map(int, mes.split("-"))
    ultimo_dia = calendar.monthrange(ano, m)[1]

    conn = get_db()
    rows = conn.execute(
        "SELECT tipo, valor, COALESCE(NULLIF(data_pagamento,''), vencimento) as data_efetiva "
        "FROM lancamentos WHERE mes = ? AND eh_transferencia = 0 AND usuario_id = ?",
        (mes, uid()),
    ).fetchall()
    conn.close()

    entradas_dia = [0.0] * (ultimo_dia + 1)
    saidas_dia = [0.0] * (ultimo_dia + 1)
    for r in rows:
        if not r["data_efetiva"]:
            continue
        try:
            dia = int(r["data_efetiva"].split("-")[2])
        except (ValueError, IndexError):
            continue
        if dia < 1 or dia > ultimo_dia:
            continue
        if r["tipo"] == "renda":
            entradas_dia[dia] += r["valor"]
        else:
            saidas_dia[dia] += r["valor"]

    resultado = []
    saldo = 0.0
    for dia in range(1, ultimo_dia + 1):
        saldo += entradas_dia[dia] - saidas_dia[dia]
        resultado.append({
            "dia": dia,
            "entradas": round(entradas_dia[dia], 2),
            "saidas": round(saidas_dia[dia], 2),
            "saldo_acumulado": round(saldo, 2),
        })
    return jsonify(resultado)


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

def _copiar_catalogo_para_demo(conn_demo):
    """Leva pro banco de demonstração o que é informação pública e não dado de
    ninguém: a lista de tickers, as logos já baixadas e a série do CDI/IPCA.
    Sem isso o modo demo teria que ir à rede pra funcionar — e o ciclo
    automático só atualiza o banco real, nunca o demo.

    Cada uma resolve um buraco visível: sem o catálogo a busca de ativo não
    acha nada; sem as logos a carteira aparece só com iniciais; sem a série do
    indexador a aba Rentabilidade mostra "CDI no período: 0,00%", como se o
    CDI não tivesse rendido nada."""
    if not os.path.exists(DB_PATH):
        return
    conn_real = sqlite3.connect(DB_PATH)
    linhas = conn_real.execute(
        "SELECT classe, ticker, nome, simbolo, logo_url FROM ativo_catalogo"
    ).fetchall()
    logos = conn_real.execute("SELECT chave, conteudo, tipo, atualizado_em, tentado_em FROM ativo_logo").fetchall()
    indexadores = conn_real.execute("SELECT indexador, data, fator_acumulado FROM indexador_serie").fetchall()
    conn_real.close()
    if linhas:
        conn_demo.executemany(
            "INSERT OR REPLACE INTO ativo_catalogo (classe, ticker, nome, simbolo, logo_url) "
            "VALUES (?, ?, ?, ?, ?)", linhas
        )
    if logos:
        conn_demo.executemany(
            "INSERT OR REPLACE INTO ativo_logo (chave, conteudo, tipo, atualizado_em, tentado_em) "
            "VALUES (?, ?, ?, ?, ?)", logos
        )
    if indexadores:
        conn_demo.executemany(
            "INSERT OR REPLACE INTO indexador_serie (indexador, data, fator_acumulado) VALUES (?, ?, ?)",
            indexadores,
        )


def _semear_demo():
    """Preenche o banco de demonstração com uma vida financeira fictícia:
    salário, dois aluguéis recebidos, contas de casa, cartões e parcelamentos."""
    if os.path.exists(DEMO_DB_PATH):
        os.remove(DEMO_DB_PATH)
    init_db(DEMO_DB_PATH, criar_usuario_inicial=False)

    conn = sqlite3.connect(DEMO_DB_PATH)
    conn.row_factory = sqlite3.Row
    _copiar_catalogo_para_demo(conn)
    conn.execute("DELETE FROM usuarios")
    cur = conn.execute(
        "INSERT INTO casas (nome, criado_em) VALUES (?, ?)",
        ("Casa demo", datetime.now().isoformat()),
    )
    casa_demo_id = cur.lastrowid
    conn.execute(
        "INSERT INTO usuarios (id, nome, username, senha_hash, casa_id, criado_em) VALUES (1, ?, ?, ?, ?, ?)",
        ("Visitante (demo)", "demo", generate_password_hash(secrets.token_urlsafe(16)),
         casa_demo_id, datetime.now().isoformat()),
    )
    conn.commit()
    garantir_categorias_padrao(conn)

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

    _semear_investimentos_demo(conn, ids_conta["Itaú"])

    conn.commit()
    conn.close()


def _semear_investimentos_demo(conn, conta_id):
    """Carteira fictícia pra aba de Investimentos não ficar vazia na
    demonstração — é a maior função do app e é ela que os prints do README
    mostram.

    Tudo é escrito à mão, sem tocar em API nenhuma: ligar o modo demo não pode
    depender de rede nem gastar requisição, e o ciclo automático só atualiza o
    banco real. Por isso a cotação, o histórico de preço e os proventos entram
    já calculados, com preços plausíveis mas inventados."""
    agora = datetime.now().isoformat()
    hoje = datetime.now()
    mes_atual = hoje.strftime("%Y-%m")

    # (nome, classe, ticker, quantidade, preço de compra, preço "de hoje", meses atrás)
    carteira = [
        ("Petrobras PN", "acao", "PETR4", 400, 29.50, 44.30, 30),
        ("Itaú Unibanco PN", "acao", "ITUB4", 300, 31.50, 37.50, 22),
        ("Vale ON", "acao", "VALE3", 150, 62.00, 74.00, 18),
        ("Maxi Renda", "fii", "MXRF11", 500, 9.80, 9.22, 14),
        ("iShares Ibovespa", "etf", "BOVA11", 40, 112.50, 165.00, 12),
        ("Bitcoin", "cripto", "bitcoin", 0.05, 280000.00, 377000.00, 24),
    ]

    def mes_atras(n):
        m = mes_atual
        for _ in range(n):
            m = mes_anterior(m)
        return m

    for nome, classe, ticker, qtd, preco_compra, preco_hoje, meses_atras in carteira:
        cur = conn.execute(
            "INSERT INTO investimentos (usuario_id, nome, classe, ticker, conta_id, criado_em) "
            "VALUES (1, ?, ?, ?, ?, ?)",
            (nome, classe, ticker, conta_id, agora),
        )
        inv_id = cur.lastrowid
        data_compra = f"{mes_atras(meses_atras)}-08"
        valor = round(qtd * preco_compra, 2)

        lanc = conn.execute(
            "INSERT INTO lancamentos (mes, tipo, descricao, valor, vencimento, categoria, conta, conta_id, "
            "pago, data_pagamento, observacao, eh_transferencia, criado_em, usuario_id) "
            "VALUES (?, 'despesa', ?, ?, '', 'Investimentos', 'Itaú', ?, 1, ?, '', 1, ?, 1)",
            (data_compra[:7], f"Aporte — {nome}", valor, conta_id, data_compra, agora),
        )
        conn.execute(
            "INSERT INTO investimento_operacoes (investimento_id, usuario_id, tipo, quantidade, "
            "preco_unitario, valor, data, lancamento_id, criado_em, custos_extras) "
            "VALUES (?, 1, 'aporte', ?, ?, ?, ?, ?, ?, 0)",
            (inv_id, qtd, preco_compra, valor, data_compra, lanc.lastrowid, agora),
        )

        conn.execute(
            "INSERT OR REPLACE INTO investimento_cotacoes (chave, valor, atualizado_em) VALUES (?, ?, ?)",
            (ticker, preco_hoje, agora),
        )
        # Histórico de preço em linha reta do preço de compra até o de hoje —
        # o bastante pro gráfico de patrimônio ter forma, sem fingir uma
        # volatilidade que não existiria em dado inventado.
        for i in range(meses_atras + 1):
            m = mes_atras(meses_atras - i)
            fracao = i / meses_atras if meses_atras else 1
            conn.execute(
                "INSERT OR REPLACE INTO investimento_cotacao_historico (chave, mes, valor, atualizado_em) "
                "VALUES (?, ?, ?, ?)",
                (ticker, m, round(preco_compra + (preco_hoje - preco_compra) * fracao, 4), agora),
            )

        # Proventos trimestrais nos ativos que pagam, sempre no passado.
        if classe in ("acao", "fii"):
            por_cota = 0.10 if classe == "fii" else 0.55
            passo = 1 if classe == "fii" else 3
            for i in range(passo, meses_atras, passo):
                m = mes_atras(i)
                bruto = round(qtd * por_cota, 2)
                lanc_p = conn.execute(
                    "INSERT INTO lancamentos (mes, tipo, descricao, valor, vencimento, categoria, conta, conta_id, "
                    "pago, data_pagamento, observacao, eh_transferencia, criado_em, usuario_id) "
                    "VALUES (?, 'renda', ?, ?, '', 'Proventos', 'Itaú', ?, 1, ?, '', 0, ?, 1)",
                    (m, f"Provento — {nome}", bruto, conta_id, f"{m}-15", agora),
                )
                conn.execute(
                    "INSERT INTO investimento_operacoes (investimento_id, usuario_id, tipo, valor, data, "
                    "lancamento_id, criado_em, tipo_pagamento, data_com, valor_bruto, quantidade, "
                    "preco_unitario, origem) VALUES (?, 1, 'provento', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'demo')",
                    (inv_id, bruto, f"{m}-15", lanc_p.lastrowid, agora,
                     "rendimento" if classe == "fii" else "dividendo",
                     f"{m}-01", bruto, qtd, por_cota),
                )

    # Um provento ainda a receber, pra demonstração mostrar também esse estado
    # (data de pagamento no futuro, lançamento não pago) — é o que o app faz
    # de diferente e ficaria invisível se tudo estivesse quitado.
    futuro = (hoje + timedelta(days=21)).strftime("%Y-%m-%d")
    inv_petr = conn.execute(
        "SELECT id, nome FROM investimentos WHERE ticker = 'PETR4' AND usuario_id = 1"
    ).fetchone()
    if inv_petr:
        lanc_f = conn.execute(
            "INSERT INTO lancamentos (mes, tipo, descricao, valor, vencimento, categoria, conta, conta_id, "
            "pago, data_pagamento, observacao, eh_transferencia, criado_em, usuario_id) "
            "VALUES (?, 'renda', ?, 187.00, ?, 'Proventos', 'Itaú', ?, 0, '', '', 0, ?, 1)",
            (futuro[:7], f"Provento — {inv_petr['nome']}", futuro, conta_id, agora),
        )
        conn.execute(
            "INSERT INTO investimento_operacoes (investimento_id, usuario_id, tipo, valor, data, "
            "lancamento_id, criado_em, tipo_pagamento, data_com, valor_bruto, quantidade, "
            "preco_unitario, origem) VALUES (?, 1, 'provento', 187.00, ?, ?, ?, 'jscp', ?, 220.0, 400, 0.55, 'demo')",
            (inv_petr["id"], futuro, lanc_f.lastrowid, agora, hoje.strftime("%Y-%m-%d")),
        )

    # Distribuição ideal preenchida, pra coluna "Comprar?" ter o que comparar.
    conn.executemany(
        "INSERT INTO investimento_alocacao_ideal (usuario_id, classe, percentual) VALUES (1, ?, ?)",
        [("acao", 45), ("fii", 20), ("etf", 15), ("cripto", 20)],
    )
    conn.commit()

    # O snapshot mensal sai do histórico acima, então o gráfico de patrimônio
    # e a rentabilidade contra o CDI já nascem com anos de história.
    atualizar_snapshots_patrimonio(conn)


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
    """Monta o .zip com banco + comprovantes + fotos + holerites."""
    with tempfile.TemporaryDirectory() as tmp:
        copia_banco = os.path.join(tmp, ARQUIVO_BANCO_NO_ZIP)
        copia_consistente_do_banco(copia_banco)
        with zipfile.ZipFile(destino_stream_ou_caminho, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(copia_banco, ARQUIVO_BANCO_NO_ZIP)
            for pasta, prefixo in ((COMPROVANTES_DIR, "comprovantes"), (FOTOS_DIR, "fotos"), (HOLERITES_DIR, "holerites")):
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
    conn = get_db()
    n_casas = conn.execute("SELECT COUNT(*) as n FROM casas").fetchone()["n"]
    conn.close()
    if n_casas > 1:
        return None
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    nome = f"backup-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.zip"
    caminho = os.path.join(BACKUPS_DIR, nome)
    escrever_zip_backup(caminho)

    manter = max(int(ler_config_backup().get("manter", 5)), 1)
    for antigo in listar_backups_automaticos()[manter:]:
        try:
            os.remove(os.path.join(BACKUPS_DIR, antigo["nome"]))
        except OSError as e:
            # Não apagar um backup velho não estraga a publicação em andamento,
            # mas se o disco estiver cheio ou sem permissão é aqui que aparece.
            print(f"[backup] não deu para apagar {antigo['nome']}: {e}", flush=True)
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
                    if nome:
                        print(f"[backup] backup automático criado: {nome}", flush=True)
        except Exception as e:
            print(f"[backup] falha ao gerar backup automático: {e}", flush=True)
        time.sleep(600)  # confere a cada 10 minutos


def iniciar_agendador_backup():
    t = threading.Thread(target=_loop_backup_automatico, daemon=True)
    t.start()


MENSAGEM_BACKUP_BLOQUEADO = (
    "esse servidor tem mais de uma casa cadastrada — o backup completo do banco "
    "incluiria dados de outras casas, então está desligado por segurança. "
    "Use a exportação de CSV pra levar seus lançamentos."
)


def backup_bloqueado_multi_casa():
    conn = get_db()
    n_casas = conn.execute("SELECT COUNT(*) as n FROM casas").fetchone()["n"]
    conn.close()
    return n_casas > 1


@app.route("/api/backup", methods=["GET"])
def baixar_backup():
    if backup_bloqueado_multi_casa():
        return jsonify({"erro": MENSAGEM_BACKUP_BLOQUEADO}), 403
    memoria = io.BytesIO()
    escrever_zip_backup(memoria)
    memoria.seek(0)
    nome_arquivo = f"backup-financeiro-{datetime.now().strftime('%Y-%m-%d-%H%M')}.zip"
    return send_file(memoria, mimetype="application/zip",
                     as_attachment=True, download_name=nome_arquivo)


@app.route("/api/backups", methods=["GET"])
def listar_backups():
    if backup_bloqueado_multi_casa():
        return jsonify({
            "config": ler_config_backup(), "periodos": list(PERIODOS_BACKUP.keys()),
            "backups": [], "erro": MENSAGEM_BACKUP_BLOQUEADO,
        })
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
        return jsonify({"erro": msg("período inválido")}), 400
    try:
        manter = int(data.get("manter") or CONFIG_BACKUP_PADRAO["manter"])
    except (TypeError, ValueError):
        return jsonify({"erro": msg("quantidade inválida")}), 400
    manter = max(1, min(manter, 30))
    gravar_config_backup({"periodo": periodo, "manter": manter})
    return jsonify({"ok": True, "config": ler_config_backup()})


@app.route("/api/backups/agora", methods=["POST"])
def gerar_backup_agora():
    if backup_bloqueado_multi_casa():
        return jsonify({"erro": MENSAGEM_BACKUP_BLOQUEADO}), 403
    nome = criar_backup_automatico()
    return jsonify({"ok": True, "nome": nome}), 201


def caminho_backup_valido(nome_arquivo):
    """Impede que o nome escape da pasta de backups."""
    nome = os.path.basename(nome_arquivo or "")
    if not nome.endswith(".zip"):
        return None
    caminho = caminho_em(BACKUPS_DIR, nome)
    if not caminho or not os.path.isfile(caminho):
        return None
    return caminho


@app.route("/api/backups/<path:nome_arquivo>", methods=["GET"])
def baixar_backup_salvo(nome_arquivo):
    if backup_bloqueado_multi_casa():
        return jsonify({"erro": MENSAGEM_BACKUP_BLOQUEADO}), 403
    caminho = caminho_backup_valido(nome_arquivo)
    if not caminho:
        return jsonify({"erro": msg("backup não encontrado")}), 404
    return send_from_directory(BACKUPS_DIR, os.path.basename(caminho), as_attachment=True)


@app.route("/api/backups/<path:nome_arquivo>", methods=["DELETE"])
def excluir_backup_salvo(nome_arquivo):
    if backup_bloqueado_multi_casa():
        return jsonify({"erro": MENSAGEM_BACKUP_BLOQUEADO}), 403
    caminho = caminho_backup_valido(nome_arquivo)
    if not caminho:
        return jsonify({"erro": msg("backup não encontrado")}), 404
    os.remove(caminho)
    return jsonify({"ok": True})


@app.route("/api/backups/<path:nome_arquivo>/restaurar", methods=["POST"])
def restaurar_backup_salvo(nome_arquivo):
    if em_demo():
        return jsonify({"erro": msg("desligue o modo demonstração antes de restaurar")}), 400
    if backup_bloqueado_multi_casa():
        return jsonify({"erro": MENSAGEM_BACKUP_BLOQUEADO}), 403
    caminho = caminho_backup_valido(nome_arquivo)
    if not caminho:
        return jsonify({"erro": msg("backup não encontrado")}), 404
    with open(caminho, "rb") as f:
        erro = aplicar_backup(f)
    if erro:
        return jsonify({"erro": erro}), 400
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/backup/info", methods=["GET"])
def info_backup():
    if backup_bloqueado_multi_casa():
        return jsonify({"comprovantes": 0, "fotos": 0, "holerites": 0, "tamanho_total_bytes": 0,
                         "erro": MENSAGEM_BACKUP_BLOQUEADO})

    def tamanho_pasta(pasta):
        if not os.path.isdir(pasta):
            return 0, 0
        arquivos = [os.path.join(pasta, n) for n in os.listdir(pasta)]
        arquivos = [a for a in arquivos if os.path.isfile(a)]
        return len(arquivos), sum(os.path.getsize(a) for a in arquivos)

    n_comp, bytes_comp = tamanho_pasta(COMPROVANTES_DIR)
    n_fotos, bytes_fotos = tamanho_pasta(FOTOS_DIR)
    n_holerites, bytes_holerites = tamanho_pasta(HOLERITES_DIR)
    bytes_banco = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    return jsonify({
        "comprovantes": n_comp,
        "fotos": n_fotos,
        "holerites": n_holerites,
        "tamanho_total_bytes": bytes_banco + bytes_comp + bytes_fotos + bytes_holerites,
    })


def aplicar_backup(origem_arquivo):
    """Valida e aplica um backup. Retorna None se deu certo, ou a mensagem de erro.
    Usado tanto pelo upload manual quanto pela restauração de um backup automático."""
    conn = get_db()
    n_casas = conn.execute("SELECT COUNT(*) as n FROM casas").fetchone()["n"]
    conn.close()
    if n_casas > 1:
        return ("esse servidor tem mais de uma casa cadastrada — restaurar um backup "
                "substituiria os dados de todas elas, então está desligado por segurança")
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

        for prefixo, pasta in (("comprovantes", COMPROVANTES_DIR), ("fotos", FOTOS_DIR), ("holerites", HOLERITES_DIR)):
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
        return jsonify({"erro": msg("desligue o modo demonstração antes de restaurar")}), 400
    if "arquivo" not in request.files:
        return jsonify({"erro": msg("envie o arquivo .zip do backup")}), 400
    enviado = request.files["arquivo"]
    if not enviado.filename.lower().endswith(".zip"):
        return jsonify({"erro": msg("o backup precisa ser um arquivo .zip")}), 400
    erro = aplicar_backup(enviado)
    if erro:
        return jsonify({"erro": erro}), 400
    session.clear()  # o backup pode ter outros usuários/senhas
    return jsonify({"ok": True})


# ---------------- Open Finance (Pluggy) ----------------
#
# Esta é a segunda exceção deliberada à regra de "sem chamada externa" do app
# (a primeira é a cotação de investimentos), e é de outra ordem: manda o
# extrato bancário por um terceiro. Vale registrar por que ainda assim é
# defensável: é o desenho do próprio Open Finance, o banco é quem autoriza, e
# a senha do banco nunca passa pelo FinanCerto — quem coleta é o widget da
# Pluggy, direto na página da instituição.
#
# A credencial é por casa. O plano gratuito (Meu Pluggy) só vale para uso
# pessoal e contas no próprio nome de quem cadastrou, então uma credencial
# central atendendo todas as casas seria uso comercial.

PLUGGY_API = "https://api.pluggy.ai"

# A chave que cifra o client_secret mora separada da SECRET_KEY de propósito:
# trocar a chave do cookie de sessão é uma operação corriqueira, e não pode
# ter como efeito colateral tornar ilegível o acesso bancário de todo mundo.
PLUGGY_KEY_PATH = os.path.join(os.path.dirname(DB_PATH), ".pluggy_key")


def _pluggy_fernet():
    """Chave de cifra do client_secret, criada uma vez e guardada em /data
    (portanto dentro do backup automático, junto do banco que ela decifra)."""
    from cryptography.fernet import Fernet

    if os.path.exists(PLUGGY_KEY_PATH):
        with open(PLUGGY_KEY_PATH, "rb") as f:
            chave = f.read().strip()
            if chave:
                return Fernet(chave)
    chave = Fernet.generate_key()
    os.makedirs(os.path.dirname(PLUGGY_KEY_PATH), exist_ok=True)
    with open(PLUGGY_KEY_PATH, "wb") as f:
        f.write(chave)
    os.chmod(PLUGGY_KEY_PATH, 0o600)
    return Fernet(chave)


def _pluggy_cifrar(texto):
    return _pluggy_fernet().encrypt(texto.encode("utf-8")).decode("ascii")


def _pluggy_decifrar(cifrado):
    return _pluggy_fernet().decrypt(cifrado.encode("ascii")).decode("utf-8")


def pluggy_credencial_da_casa(conn, casa_id):
    """Devolve (client_id, client_secret) da casa, ou None se ela ainda não
    cadastrou. Nunca devolve o segredo cifrado para fora daqui."""
    row = conn.execute(
        "SELECT client_id, client_secret_cifrado FROM pluggy_credenciais WHERE casa_id = ?",
        (casa_id,),
    ).fetchone()
    if not row:
        return None
    try:
        return row["client_id"], _pluggy_decifrar(row["client_secret_cifrado"])
    except Exception:
        # Chave trocada ou perdida: melhor pedir para cadastrar de novo do que
        # estourar erro genérico numa tela qualquer.
        return None


# A apiKey da Pluggy vale 2 horas. Pedir uma nova a cada chamada seria um
# request extra em tudo, então fica em cache por casa, renovada 5 minutos
# antes de vencer. É cache em memória: reiniciar o app só custa um /auth.
_pluggy_api_keys = {}
_pluggy_api_keys_lock = threading.Lock()


class PluggyErro(Exception):
    """Erro vindo da Pluggy que a tela precisa mostrar em português."""


def pluggy_api_key(conn, casa_id):
    agora = time.time()
    with _pluggy_api_keys_lock:
        cache = _pluggy_api_keys.get(casa_id)
        if cache and cache["expira_em"] - agora > 300:
            return cache["api_key"]

    cred = pluggy_credencial_da_casa(conn, casa_id)
    if not cred:
        raise PluggyErro("esta casa ainda não cadastrou as credenciais do Meu Pluggy")
    client_id, client_secret = cred

    try:
        resp = requests.post(
            f"{PLUGGY_API}/auth",
            json={"clientId": client_id, "clientSecret": client_secret},
            timeout=20,
        )
    except requests.RequestException as e:
        print(f"[pluggy] falha de rede ao autenticar: {e}", flush=True)
        raise PluggyErro("não foi possível falar com a Pluggy — tente de novo em instantes")

    # Credencial errada volta como 400 (foi o que a Pluggy respondeu em teste),
    # e 403 aparece quando a credencial existe mas não tem acesso — os dois
    # casos são "confira o que você colou", não erro genérico.
    if resp.status_code in (400, 401, 403):
        raise PluggyErro("a Pluggy recusou as credenciais — confira o Client ID e o Client Secret")
    if resp.status_code >= 400:
        raise PluggyErro(f"a Pluggy respondeu {resp.status_code} ao autenticar")

    dados = resp.json()
    api_key = dados.get("apiKey")
    if not api_key:
        raise PluggyErro("a Pluggy autenticou mas não devolveu apiKey")

    with _pluggy_api_keys_lock:
        _pluggy_api_keys[casa_id] = {"api_key": api_key, "expira_em": agora + 7200}
    return api_key


def _caminho_pluggy_seguro(caminho):
    """O caminho da chamada é montado com id vindo do banco. Um id que trouxesse
    "://", "//" ou uma quebra de linha faria a URL final apontar para outro
    lugar, e a chave da Pluggy iria junto no cabeçalho — então o caminho é
    conferido antes de ser concatenado, e não depois."""
    if (
        not caminho.startswith("/")
        or caminho.startswith("//")
        or "://" in caminho
        or any(c in caminho for c in "\r\n \t")
    ):
        raise PluggyErro("caminho inválido na chamada à Pluggy")
    return caminho


def pluggy_pedir(conn, casa_id, metodo, caminho, **kwargs):
    """Chamada autenticada na Pluggy. Se a apiKey em cache tiver sido
    invalidada do outro lado, tenta uma vez com uma chave nova antes de
    desistir — senão o app ficaria travado até o cache vencer sozinho."""
    kwargs.setdefault("timeout", 30)

    caminho = _caminho_pluggy_seguro(caminho)

    def _chamar(api_key):
        return requests.request(
            metodo,
            f"{PLUGGY_API}{caminho}",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            **kwargs,
        )

    try:
        resp = _chamar(pluggy_api_key(conn, casa_id))
        if resp.status_code in (401, 403):
            with _pluggy_api_keys_lock:
                _pluggy_api_keys.pop(casa_id, None)
            resp = _chamar(pluggy_api_key(conn, casa_id))
    except requests.RequestException as e:
        print(f"[pluggy] falha de rede em {caminho}: {e}", flush=True)
        raise PluggyErro("não foi possível falar com a Pluggy — tente de novo em instantes")

    if resp.status_code >= 400:
        print(f"[pluggy] resposta {resp.status_code} em {caminho}", flush=True)
        raise PluggyErro(f"a Pluggy respondeu {resp.status_code}")
    return resp.json() if resp.content else {}


@app.route("/api/pluggy/credenciais", methods=["GET"])
def pluggy_ver_credenciais():
    """Diz se a casa já configurou, e mostra só o Client ID. O segredo nunca
    volta para a tela, nem mascarado — não há motivo para ele sair do banco."""
    conn = get_db()
    try:
        casa_id = minha_casa_id(conn)
        row = conn.execute(
            "SELECT client_id, atualizado_em FROM pluggy_credenciais WHERE casa_id = ?",
            (casa_id,),
        ).fetchone()
        return jsonify({
            "configurado": bool(row),
            "client_id": row["client_id"] if row else None,
            "atualizado_em": row["atualizado_em"] if row else None,
            "pode_configurar": eh_administrador(conn),
        })
    finally:
        conn.close()


@app.route("/api/pluggy/credenciais", methods=["PUT"])
def pluggy_salvar_credenciais():
    if em_demo():
        return jsonify({"erro": msg("o modo demonstração não conecta em banco de verdade")}), 400
    conn = get_db()
    try:
        # Mesma regra das outras rotas que mexem na casa: esconder o botão é
        # conforto, quem barra é a checagem aqui dentro.
        if not eh_administrador(conn):
            return jsonify({"erro": msg("só o administrador da casa pode configurar o Open Finance")}), 403

        dados = request.get_json(silent=True) or {}
        client_id = (dados.get("client_id") or "").strip()
        client_secret = (dados.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            return jsonify({"erro": msg("informe o Client ID e o Client Secret do Meu Pluggy")}), 400

        casa_id = minha_casa_id(conn)
        agora = datetime.now().isoformat()
        conn.execute(
            """INSERT INTO pluggy_credenciais
                   (casa_id, client_id, client_secret_cifrado, criado_em, atualizado_em)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(casa_id) DO UPDATE SET
                   client_id = excluded.client_id,
                   client_secret_cifrado = excluded.client_secret_cifrado,
                   atualizado_em = excluded.atualizado_em""",
            (casa_id, client_id, _pluggy_cifrar(client_secret), agora, agora),
        )
        conn.commit()

        # Credencial trocada invalida a apiKey em cache da casa.
        with _pluggy_api_keys_lock:
            _pluggy_api_keys.pop(casa_id, None)

        # Autentica na hora: melhor dizer "credencial errada" agora do que na
        # primeira vez que a pessoa tentar conectar um banco.
        try:
            pluggy_api_key(conn, casa_id)
        except PluggyErro as e:
            return jsonify({"ok": True, "aviso": str(e)})
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/pluggy/credenciais", methods=["DELETE"])
def pluggy_apagar_credenciais():
    conn = get_db()
    try:
        if not eh_administrador(conn):
            return jsonify({"erro": msg("só o administrador da casa pode remover o Open Finance")}), 403
        casa_id = minha_casa_id(conn)
        conn.execute("DELETE FROM pluggy_credenciais WHERE casa_id = ?", (casa_id,))
        conn.commit()
        with _pluggy_api_keys_lock:
            _pluggy_api_keys.pop(casa_id, None)
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/pluggy/connect-token", methods=["POST"])
def pluggy_connect_token():
    """Token de sessão do widget da Pluggy Connect. É ele — e não o
    client_secret — que vai para o navegador; dura pouco e só serve para abrir
    o widget."""
    if em_demo():
        return jsonify({"erro": msg("o modo demonstração não conecta em banco de verdade")}), 400
    conn = get_db()
    try:
        casa_id = minha_casa_id(conn)
        corpo = {"clientUserId": f"casa{casa_id}-usuario{uid()}"}

        # Com itemId o widget abre em modo de reconexão, que é o caminho
        # quando o consentimento do Open Finance expira.
        dados = request.get_json(silent=True) or {}
        item_id = (dados.get("item_id") or "").strip()
        if item_id:
            dono = conn.execute(
                "SELECT casa_id FROM pluggy_itens WHERE item_id = ?", (item_id,)
            ).fetchone()
            if not dono or dono["casa_id"] != casa_id:
                return jsonify({"erro": msg("conexão não encontrada nesta casa")}), 404
            corpo["itemId"] = item_id

        try:
            resposta = pluggy_pedir(conn, casa_id, "POST", "/connect_token", json=corpo)
        except PluggyErro as e:
            return jsonify({"erro": str(e)}), 400
        return jsonify({"access_token": resposta.get("accessToken")})
    finally:
        conn.close()


# ---- Fase 2: registrar a conexão (Item) e vincular as contas ----
#
# No caminho gratuito (Meu Pluggy) quem conecta o banco é o portal da Pluggy, e
# não existe endpoint para listar itens — testado: /items, /v2/items e
# /applications devolvem 401/403. Por isso o Item ID é colado pela pessoa, e não
# descoberto pelo app.


def _pluggy_sincronizar_contas(conn, casa_id, usuario_id, item_id):
    """Traz as contas do Item e guarda em pluggy_contas, preservando o vínculo
    já escolhido. Devolve quantas viu."""
    dados = pluggy_pedir(conn, casa_id, "GET", "/accounts", params={"itemId": item_id})
    vistas = 0
    for c in dados.get("results", []):
        credito = c.get("creditData") or {}
        conn.execute(
            """INSERT INTO pluggy_contas
                   (account_id, item_id, casa_id, usuario_id, tipo, subtipo, nome,
                    numero, saldo, moeda, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(account_id) DO UPDATE SET
                   tipo = excluded.tipo,
                   subtipo = excluded.subtipo,
                   nome = excluded.nome,
                   numero = excluded.numero,
                   saldo = excluded.saldo,
                   moeda = excluded.moeda""",
            (c.get("id"), item_id, casa_id, usuario_id, c.get("type"), c.get("subtype"),
             c.get("name"), c.get("number"), c.get("balance"), c.get("currencyCode"),
             datetime.now().isoformat()),
        )
        vistas += 1
        # Guardado só para a tela mostrar limite e vencimento do cartão; o
        # vínculo com `cartoes` continua sendo escolhido à mão.
        if credito:
            conn.execute(
                "UPDATE pluggy_contas SET subtipo = COALESCE(subtipo, ?) WHERE account_id = ?",
                (credito.get("brand"), c.get("id")),
            )
    conn.commit()
    return vistas


@app.route("/api/pluggy/itens", methods=["POST"])
def pluggy_registrar_item():
    """Registra uma conexão a partir do Item ID que a pessoa colou."""
    if em_demo():
        return jsonify({"erro": msg("o modo demonstração não conecta em banco de verdade")}), 400
    dados = request.get_json(silent=True) or {}
    item_id = (dados.get("item_id") or "").strip()
    if not item_id:
        return jsonify({"erro": msg("informe o Item ID da conexão")}), 400

    conn = get_db()
    try:
        casa_id = minha_casa_id(conn)

        # Um Item já registrado por outra casa não pode ser sequestrado: o
        # dono é quem registrou primeiro.
        dono = conn.execute(
            "SELECT casa_id FROM pluggy_itens WHERE item_id = ?", (item_id,)
        ).fetchone()
        if dono and dono["casa_id"] != casa_id:
            return jsonify({"erro": msg("esse Item já está registrado em outra casa")}), 409

        try:
            item = pluggy_pedir(conn, casa_id, "GET", f"/items/{quote(str(item_id), safe='')}")
        except PluggyErro as e:
            return jsonify({"erro": str(e)}), 400
        if not item.get("id"):
            return jsonify({"erro": msg("a Pluggy não encontrou esse Item ID")}), 404

        conector = item.get("connector") or {}
        conn.execute(
            """INSERT INTO pluggy_itens
                   (item_id, casa_id, usuario_id, conector_id, conector_nome,
                    conector_logo, status, status_detalhe, ultimo_sync, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(item_id) DO UPDATE SET
                   conector_nome = excluded.conector_nome,
                   conector_logo = excluded.conector_logo,
                   status = excluded.status,
                   status_detalhe = excluded.status_detalhe,
                   ultimo_sync = excluded.ultimo_sync""",
            (item_id, casa_id, uid(), conector.get("id"), conector.get("name"),
             conector.get("imageUrl"), item.get("status"),
             json.dumps(item.get("statusDetail")) if item.get("statusDetail") else None,
             item.get("lastUpdatedAt"), datetime.now().isoformat()),
        )
        conn.commit()

        try:
            vistas = _pluggy_sincronizar_contas(conn, casa_id, uid(), item_id)
        except PluggyErro as e:
            return jsonify({"ok": True, "aviso": f"conexão salva, mas as contas não vieram: {e}"})

        return jsonify({"ok": True, "banco": conector.get("name"), "contas": vistas})
    finally:
        conn.close()


@app.route("/api/pluggy/itens", methods=["GET"])
def pluggy_listar_itens():
    """Conexões da casa, com as contas de cada uma e o vínculo atual."""
    conn = get_db()
    try:
        casa_id = minha_casa_id(conn)
        itens = []
        for it in conn.execute(
            "SELECT * FROM pluggy_itens WHERE casa_id = ? ORDER BY id", (casa_id,)
        ).fetchall():
            d = dict(it)
            contas = []
            for c in conn.execute(
                """SELECT pc.*, ct.nome AS conta_nome, ca.nome AS cartao_nome
                   FROM pluggy_contas pc
                   LEFT JOIN contas ct ON ct.id = pc.conta_id
                   LEFT JOIN cartoes ca ON ca.id = pc.cartao_id
                   WHERE pc.item_id = ? ORDER BY pc.id""",
                (it["item_id"],),
            ).fetchall():
                cd = dict(c)
                cd["ignorada"] = bool(cd["ignorada"])
                contas.append(cd)
            d["contas"] = contas
            itens.append(d)
        return jsonify(itens)
    finally:
        conn.close()


@app.route("/api/pluggy/itens/<item_id>/sincronizar", methods=["POST"])
def pluggy_sincronizar_item(item_id):
    conn = get_db()
    try:
        casa_id = minha_casa_id(conn)
        dono = conn.execute(
            "SELECT casa_id FROM pluggy_itens WHERE item_id = ?", (item_id,)
        ).fetchone()
        if not dono or dono["casa_id"] != casa_id:
            return jsonify({"erro": msg("conexão não encontrada nesta casa")}), 404
        try:
            item = pluggy_pedir(conn, casa_id, "GET", f"/items/{quote(str(item_id), safe='')}")
            conn.execute(
                "UPDATE pluggy_itens SET status = ?, ultimo_sync = ? WHERE item_id = ?",
                (item.get("status"), item.get("lastUpdatedAt"), item_id),
            )
            conn.commit()
            vistas = _pluggy_sincronizar_contas(conn, casa_id, uid(), item_id)
        except PluggyErro as e:
            return jsonify({"erro": str(e)}), 400
        return jsonify({"ok": True, "status": item.get("status"), "contas": vistas})
    finally:
        conn.close()


@app.route("/api/pluggy/itens/<item_id>", methods=["DELETE"])
def pluggy_remover_item(item_id):
    """Tira a conexão do FinanCerto. NÃO revoga o consentimento no banco —
    isso se faz no app da instituição, e a tela precisa dizer isso."""
    conn = get_db()
    try:
        casa_id = minha_casa_id(conn)
        dono = conn.execute(
            "SELECT casa_id FROM pluggy_itens WHERE item_id = ?", (item_id,)
        ).fetchone()
        if not dono or dono["casa_id"] != casa_id:
            return jsonify({"erro": msg("conexão não encontrada nesta casa")}), 404
        conn.execute("DELETE FROM pluggy_transacoes WHERE account_id IN "
                     "(SELECT account_id FROM pluggy_contas WHERE item_id = ?)", (item_id,))
        conn.execute("DELETE FROM pluggy_contas WHERE item_id = ?", (item_id,))
        conn.execute("DELETE FROM pluggy_itens WHERE item_id = ?", (item_id,))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/pluggy/contas/<int:item_id>/vinculo", methods=["PUT"])
def pluggy_vincular_conta(item_id):
    """Liga uma conta da Pluggy a uma conta ou cartão do FinanCerto — ou marca
    para ignorar. Enquanto não houver vínculo, nada daquela conta é importado."""
    conn = get_db()
    try:
        casa_id = minha_casa_id(conn)
        linha = conn.execute(
            "SELECT casa_id FROM pluggy_contas WHERE id = ?", (item_id,)
        ).fetchone()
        if not linha or linha["casa_id"] != casa_id:
            return jsonify({"erro": msg("conta não encontrada nesta casa")}), 404

        dados = request.get_json(silent=True) or {}
        conta_id = dados.get("conta_id")
        cartao_id = dados.get("cartao_id")
        ignorar = bool(dados.get("ignorada"))

        if conta_id and cartao_id:
            return jsonify({"erro": msg("escolha uma conta ou um cartão, não os dois")}), 400

        # O alvo tem que ser da mesma casa, senão o extrato de um cairia no
        # financeiro de outro.
        if conta_id:
            ok = conn.execute(
                "SELECT 1 FROM contas ct JOIN usuarios u ON u.id = ct.usuario_id "
                "WHERE ct.id = ? AND u.casa_id = ?", (conta_id, casa_id)
            ).fetchone()
            if not ok:
                return jsonify({"erro": msg("essa conta não é desta casa")}), 400
        if cartao_id:
            ok = conn.execute(
                "SELECT 1 FROM cartoes ca JOIN usuarios u ON u.id = ca.usuario_id "
                "WHERE ca.id = ? AND u.casa_id = ?", (cartao_id, casa_id)
            ).fetchone()
            if not ok:
                return jsonify({"erro": msg("esse cartão não é desta casa")}), 400

        conn.execute(
            "UPDATE pluggy_contas SET conta_id = ?, cartao_id = ?, ignorada = ? WHERE id = ?",
            (conta_id, cartao_id, int(ignorar), item_id),
        )
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


# ---------------- Versão do app Android ----------------
#
# Quem decide qual é a versão atual é o SERVIDOR, não o app. Assim dá para
# anunciar uma versão nova sem precisar publicar um APK novo só para avisar que
# existe um APK novo — que seria o problema do ovo e da galinha.
#
# Os valores são variáveis de ambiente porque o repositório é público: cada
# instalação aponta para o próprio APK, e ninguém herda o endereço de release
# de outra pessoa.
# `or` em vez do segundo argumento do get(): o docker-compose passa as
# variaveis nao preenchidas como string VAZIA, e vazio sobrescreveria o padrao
# — foi assim que a URL do APK ficou em branco e o botao "Baixar" apontaria
# para lugar nenhum.
APP_ANDROID_VERSAO = os.environ.get("APP_ANDROID_VERSAO") or "3.1"
APP_ANDROID_MINIMA = os.environ.get("APP_ANDROID_MINIMA") or ""
# Pagina da release, para quem vai olhar com os proprios olhos.
APP_ANDROID_URL = os.environ.get("APP_ANDROID_URL") or "https://github.com/financerto/financerto-app/releases/latest"

# O GitHub e a fonte oficial do APK para todo mundo, inclusive para quem
# publica: uma fonte so evita duas versoes da verdade. O app precisa do
# ARQUIVO, nao da pagina — baixar a pagina traria HTML, e o instalador do
# Android engasgaria com uma mensagem que nao explica nada. O endereco e
# montado a partir da versao anunciada, entao o que se anuncia e o que se
# baixa sao sempre a mesma coisa.
APP_ANDROID_REPO = os.environ.get("APP_ANDROID_REPO") or "financerto/financerto-app"


def url_do_apk():
    do_ambiente = os.environ.get("APP_ANDROID_APK_URL")
    if do_ambiente:
        return do_ambiente
    v = APP_ANDROID_VERSAO
    return (f"https://github.com/{APP_ANDROID_REPO}/releases/download"
            f"/v{v}/FinanCerto-{v}.apk")
APP_ANDROID_NOTAS = os.environ.get("APP_ANDROID_NOTAS") or ""


def _versao_como_lista(texto):
    """"3.10.2" -> [3, 10, 2]. Comparar como texto diria que "3.9" > "3.10",
    que é errado; comparar número a número resolve. Pedaço não numérico
    (ex.: "3.1-beta") vira 0 em vez de explodir."""
    partes = []
    for pedaco in str(texto or "").strip().split("."):
        digitos = "".join(c for c in pedaco if c.isdigit())
        partes.append(int(digitos) if digitos else 0)
    return partes or [0]


def versao_menor_que(a, b):
    """a < b, comparando por posição e tratando ausência como zero
    (ex.: "3.1" < "3.1.1")."""
    la, lb = _versao_como_lista(a), _versao_como_lista(b)
    for i in range(max(len(la), len(lb))):
        va = la[i] if i < len(la) else 0
        vb = lb[i] if i < len(lb) else 0
        if va != vb:
            return va < vb
    return False


@app.route("/api/versao-app", methods=["GET"])
def versao_app():
    """Diz qual é a versão publicada do app Android e, se a instalada foi
    informada, se ela está atrasada."""
    instalada = (request.args.get("instalada") or "").strip()

    # Sem `instalada` a rota ainda serve: a tela usa para mostrar qual é a
    # versão publicada mesmo fora do app.
    desatualizado = bool(instalada) and versao_menor_que(instalada, APP_ANDROID_VERSAO)
    # "Obrigatória" existe para o caso de uma versão quebrada ou insegura: aí o
    # aviso não é dispensável. Fica desligado enquanto a variável estiver vazia.
    obrigatorio = bool(
        instalada and APP_ANDROID_MINIMA and versao_menor_que(instalada, APP_ANDROID_MINIMA)
    )

    return jsonify({
        "versao_atual": APP_ANDROID_VERSAO,
        "versao_minima": APP_ANDROID_MINIMA or None,
        "instalada": instalada or None,
        "desatualizado": desatualizado,
        "obrigatorio": obrigatorio,
        # `url` e a pagina (para abrir no navegador) e `apk` e o arquivo
        # (para o app baixar sozinho). Quem consome escolhe.
        "url": APP_ANDROID_URL,
        "apk": url_do_apk(),
        "notas": APP_ANDROID_NOTAS or None,
    })


# ---- Importação do extrato para lançamentos ----
#
# O que o extrato traz NÃO vira lançamento cegamente. Cada transação passa por
# três destinos possíveis:
#   1. já importada antes  -> ignorada (o id da Pluggy é único)
#   2. casa com lançamento -> marcada como conciliada, nada é criado
#   3. sobrou              -> vira lançamento novo, com origem='open_finance'
#
# O passo 2 é o que impede duplicar. Conferindo com dado real, o saldo que a
# Pluggy reporta batia com o do app, ou seja, os lançamentos manuais já
# estavam certos — importar por cima teria duplicado quase tudo.

# Categoria da Pluggy (inglês) -> categoria da casa (português). O que não
# estiver aqui entra sem categoria, para a pessoa classificar: chutar
# "Outros" esconderia o que precisa de atenção.
PLUGGY_CATEGORIAS = {
    "Taxi and ride-hailing": "Transporte",
    "Transportation": "Transporte",
    "Automotive": "Transporte",
    "Vehicle maintenance": "Transporte",
    "Car rental": "Transporte",
    "Parking": "Transporte",
    "Gas stations": "Combustível",
    "Gas": "Combustível",
    "Groceries": "Mercado",
    "Eating out": "Alimentação",
    "Food delivery": "Alimentação",
    "Food and drinks": "Alimentação",
    "Insurance": "Seguro",
    "Shopping": "Compras",
    "Online shopping": "Compras",
    "Clothing": "Compras",
    "Electronics": "Compras",
    "Housing": "Moradia",
    "Internet": "Internet",
    "Telecommunications": "Internet",
    "Loans": "Emprestimos",
    "Loans and financing": "Emprestimos",
    "Gyms and fitness centers": "Lazer",
    "Cinema, theater and concerts": "Lazer",
    "Office supplies": "Negócios",
    "Pharmacy": "Saúde",
    "Healthcare": "Saúde",
    "University": "Educação",
    "Education": "Educação",
    "Bookstore": "Educação",
    "Interests charged": "Tarifas e juros",
    "Tax on financial operations": "Tarifas e juros",
    "Mutual funds": "Investimentos",
    "Investments": "Investimentos",
    "Pet supplies and vet": "Pets",
    "Mobile": "Internet",
    "Electricity": "Energia",
    "Houseware": "Moradia",
    "Leisure": "Lazer",
    "Services": "Serviços",
    "Digital services": "Serviços",
    "Credit card payment": "Fatura de cartão",
    "Public transportation": "Transporte",
    "Vehicle ownership taxes and fees": "Transporte",
    "Tickets": "Lazer",
    "Water": "Água",
}

# Estas dependem do sentido: o mesmo rótulo da Pluggy vira coisas diferentes
# conforme o dinheiro entra ou sai. Sobrepõem o mapa acima.
PLUGGY_CATEGORIAS_CREDITO = {
    "Transfer - PIX": "Pix recebido",
    "Transfers": "Transferência recebida",
    "Transfer - TED": "Transferência recebida",
    "Transfer - Bank Slip": "Transferência recebida",
    "Cashback": "Rendimentos",
}

PLUGGY_CATEGORIAS_DEBITO = {
    "Transfer - PIX": "Pix enviado",
    "Transfers": "Transferência enviada",
    "Transfer - TED": "Transferência enviada",
    "Transfer - Bank Slip": "Transferência enviada",
}


def categoria_do_pluggy(categoria_pluggy, tipo):
    """Categoria da casa para uma categoria da Pluggy. `tipo` é renda ou
    despesa. Devolve None quando não há correspondência — sem categoria é
    melhor que "Outros" no chute, porque deixa visível o que precisa de olho."""
    if not categoria_pluggy:
        return None
    especifico = (PLUGGY_CATEGORIAS_CREDITO if tipo == "renda"
                  else PLUGGY_CATEGORIAS_DEBITO)
    return especifico.get(categoria_pluggy) or PLUGGY_CATEGORIAS.get(categoria_pluggy)


# Categorias que o mapa acima usa e que não existem no conjunto padrão do app.
# São criadas na primeira sincronização de cada casa, para o lançamento
# importado não apontar para uma categoria inexistente.
CATEGORIAS_DO_OPEN_FINANCE = [
    ("Saúde", "despesa", "#f97066"),
    ("Educação", "despesa", "#7a5af8"),
    ("Tarifas e juros", "despesa", "#f79009"),
    ("Investimentos", "despesa", "#12b76a"),
    ("Pets", "despesa", "#f670c7"),
    ("Serviços", "despesa", "#06aed4"),
    ("Fatura de cartão", "despesa", "#ee46bc"),
    ("Pix enviado", "despesa", "#6172f3"),
    ("Transferência enviada", "despesa", "#8098f9"),
    ("Transferência recebida", "receita", "#32d583"),
]


def garantir_categorias_do_open_finance(conn, casa_id):
    for nome, tipo, cor in CATEGORIAS_DO_OPEN_FINANCE:
        existe = conn.execute(
            "SELECT 1 FROM categorias WHERE casa_id = ? AND nome = ? AND tipo = ?",
            (casa_id, nome, tipo),
        ).fetchone()
        if not existe:
            conn.execute(
                "INSERT INTO categorias (nome, tipo, cor, casa_id) VALUES (?, ?, ?, ?)",
                (nome, tipo, cor, casa_id),
            )
    conn.commit()

# Transferência entre contas do próprio titular: move saldo mas não é ganho
# nem gasto, então não pode entrar nos totais do mês. É a mesma marcação que a
# transferência manual entre contas já usa.
PLUGGY_CATEGORIAS_TRANSFERENCIA = {
    "Same person transfer",
    "Same person transfer - CASH",
}


def _pluggy_extrato(conn, casa_id, account_id, desde=None):
    """Todas as transações da conta, seguindo o cursor da v2.

    Filtra por createdAtFrom e não por dateFrom: `date` é quando a transação
    aconteceu, e filtrar por ele perde silenciosamente o que a Pluggy ingere
    depois mas data para trás (fatura de cartão, lojista que liquida tarde).
    """
    from urllib.parse import parse_qs

    tudo, depois = [], None
    while True:
        parametros = {"accountId": account_id}
        if desde:
            parametros["createdAtFrom"] = desde
        if depois:
            parametros["after"] = depois
        dados = pluggy_pedir(conn, casa_id, "GET", "/v2/transactions", params=parametros)
        resultados = dados.get("results", [])
        if not resultados:
            break
        tudo += resultados
        proximo = dados.get("next")
        novo = parse_qs(urlparse(proximo).query).get("after", [None])[0] if proximo else None
        if not novo or novo == depois:
            break
        depois = novo
    return tudo


def _pluggy_casa_com_lancamento(conn, usuario_id, conta_id, tipo, valor, data_iso, dias=3):
    """Procura um lançamento que já represente essa transação: mesmo tipo,
    mesmo valor e data próxima. A folga de 3 dias existe porque a data que a
    pessoa digita raramente é exatamente a que o banco registra."""
    from datetime import datetime as _dt, timedelta as _td

    try:
        d = _dt.fromisoformat(data_iso[:10])
    except ValueError:
        return None
    inicio = (d - _td(days=dias)).strftime("%Y-%m-%d")
    fim = (d + _td(days=dias)).strftime("%Y-%m-%d")

    row = conn.execute(
        """SELECT id FROM lancamentos
           WHERE usuario_id = ? AND conta_id = ? AND tipo = ?
             AND ROUND(ABS(valor), 2) = ROUND(?, 2)
             AND COALESCE(data_pagamento, vencimento, mes || '-15') BETWEEN ? AND ?
             AND id NOT IN (SELECT lancamento_id FROM pluggy_transacoes
                            WHERE lancamento_id IS NOT NULL)
           LIMIT 1""",
        (usuario_id, conta_id, tipo, round(abs(valor), 2), inicio, fim),
    ).fetchone()
    return row["id"] if row else None


def pluggy_importar_conta(conn, casa_id, pluggy_conta, mes_de=None, mes_ate=None, criar=True):
    """Importa o extrato de uma conta da Pluggy já vinculada.

    mes_de/mes_ate no formato AAAA-MM limitam o período (inclusive). `criar`
    False faz uma simulação: registra o staging e o casamento, mas não cria
    lançamento nenhum.
    """
    account_id = pluggy_conta["account_id"]
    conta_id = pluggy_conta["conta_id"]
    usuario_id = pluggy_conta["usuario_id"]
    if not conta_id:
        raise PluggyErro("essa conta da Pluggy ainda não foi vinculada a uma conta do app")

    garantir_categorias_do_open_finance(conn, casa_id)

    relatorio = {"vistas": 0, "fora_do_periodo": 0, "ja_importadas": 0,
                 "conciliadas": 0, "criadas": 0, "transferencias": 0, "sem_categoria": 0}

    for t in _pluggy_extrato(conn, casa_id, account_id):
        relatorio["vistas"] += 1
        data = (t.get("date") or "")[:10]
        mes = data[:7]
        if (mes_de and mes < mes_de) or (mes_ate and mes > mes_ate):
            relatorio["fora_do_periodo"] += 1
            continue

        transacao_id = t.get("id")
        if conn.execute("SELECT 1 FROM pluggy_transacoes WHERE transacao_id = ?",
                        (transacao_id,)).fetchone():
            relatorio["ja_importadas"] += 1
            continue

        bruto = t.get("amount") or 0
        pluggy_tipo = (t.get("type") or "").upper()
        tipo = "renda" if pluggy_tipo == "CREDIT" else "despesa"
        valor = abs(bruto)
        categoria_pluggy = t.get("category")
        descricao = (t.get("description") or "Sem descrição").strip()
        cc = t.get("creditCardMetadata") or {}

        lancamento_id = _pluggy_casa_com_lancamento(
            conn, usuario_id, conta_id, tipo, valor, data)
        estado = "conciliada" if lancamento_id else "pendente"

        if lancamento_id:
            relatorio["conciliadas"] += 1
        elif criar:
            eh_transf = 1 if categoria_pluggy in PLUGGY_CATEGORIAS_TRANSFERENCIA else 0
            # Regra primeiro: ela olha a descrição, onde está o nome do
            # estabelecimento. A categoria da Pluggy é genérica e entra só
            # quando nenhuma regra casou.
            categoria, transf_regra, _ = aplicar_regras(
                conn, casa_id, descricao, valor, tipo)
            if transf_regra:
                eh_transf = 1
            if not categoria:
                categoria = categoria_do_pluggy(categoria_pluggy, tipo)
            if eh_transf:
                relatorio["transferencias"] += 1
            elif not categoria:
                relatorio["sem_categoria"] += 1

            cur = conn.execute(
                """INSERT INTO lancamentos
                       (mes, tipo, descricao, valor, vencimento, categoria, conta, conta_id,
                        pago, data_pagamento, eh_transferencia, usuario_id, criado_em, origem)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 'open_finance')""",
                (mes, tipo, descricao, valor, data, categoria,
                 conn.execute("SELECT nome FROM contas WHERE id = ?", (conta_id,)).fetchone()["nome"],
                 conta_id, data, eh_transf, usuario_id, datetime.now().isoformat()),
            )
            lancamento_id = cur.lastrowid
            estado = "aprovada"
            relatorio["criadas"] += 1

        conn.execute(
            """INSERT INTO pluggy_transacoes
                   (transacao_id, account_id, casa_id, usuario_id, descricao, valor, tipo,
                    data, categoria_pluggy, situacao, parcela_num, parcela_total,
                    compra_em, fatura_id, estado, lancamento_id, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (transacao_id, account_id, casa_id, usuario_id, descricao, bruto, pluggy_tipo,
             data, categoria_pluggy, t.get("status"), cc.get("installmentNumber"),
             cc.get("totalInstallments"), (cc.get("purchaseDate") or "")[:10] or None,
             str(cc.get("billId")) if cc.get("billId") else None,
             estado, lancamento_id, datetime.now().isoformat()),
        )

    conn.commit()
    return relatorio


# ---- Sincronização automática do Open Finance ----
#
# Sem isto o saldo do app congela no valor da última importação e vai se
# afastando do banco em silêncio — foi o que aconteceu com o C6, que ficou
# marcando 513,85 quando o real já era 158,47.


def pluggy_sincronizar_tudo(conn, casa_id, criar=True):
    """Atualiza itens e contas pela Pluggy, importa o que chegou de novo e
    recalibra o saldo inicial de cada conta vinculada.

    O recálculo é o que mantém o saldo do app igual ao do banco. Ele é um
    tampão: absorve qualquer diferença, inclusive a que vier de transação que
    a Pluggy ainda não reportou. Por isso `descompasso` volta no relatório —
    diferença grande é sinal de extrato incompleto, não de conta certa.
    """
    resumo = {"itens": 0, "contas": 0, "criadas": 0, "descompasso": {}}

    for it in conn.execute(
        "SELECT item_id FROM pluggy_itens WHERE casa_id = ?", (casa_id,)
    ).fetchall():
        try:
            item = pluggy_pedir(conn, casa_id, "GET", f"/items/{quote(str(it['item_id']), safe='')}")
            conn.execute(
                "UPDATE pluggy_itens SET status = ?, ultimo_sync = ? WHERE item_id = ?",
                (item.get("status"), item.get("lastUpdatedAt"), it["item_id"]),
            )
            dados = pluggy_pedir(conn, casa_id, "GET", "/accounts",
                                 params={"itemId": it["item_id"]})
            for c in dados.get("results", []):
                conn.execute(
                    "UPDATE pluggy_contas SET saldo = ?, nome = ? WHERE account_id = ?",
                    (c.get("balance"), c.get("name"), c.get("id")),
                )
            conn.commit()
            resumo["itens"] += 1
        except PluggyErro:
            # Um banco fora do ar não pode impedir os outros de sincronizar.
            continue

    for pc in conn.execute(
        "SELECT * FROM pluggy_contas WHERE casa_id = ? AND conta_id IS NOT NULL AND ignorada = 0",
        (casa_id,),
    ).fetchall():
        try:
            rel = pluggy_importar_conta(conn, casa_id, pc, criar=criar)
        except PluggyErro:
            continue
        resumo["contas"] += 1
        resumo["criadas"] += rel["criadas"]

        alvo = pc["saldo"]
        if alvo is None:
            continue
        atual = _saldo_calculado(conn, pc["conta_id"])
        ini = conn.execute(
            "SELECT saldo_inicial FROM contas WHERE id = ?", (pc["conta_id"],)
        ).fetchone()["saldo_inicial"]
        ajuste = alvo - atual
        if abs(ajuste) > 0.005:
            conn.execute(
                "UPDATE contas SET saldo_inicial = ? WHERE id = ?",
                (ini + ajuste, pc["conta_id"]),
            )
            nome = conn.execute(
                "SELECT nome FROM contas WHERE id = ?", (pc["conta_id"],)
            ).fetchone()["nome"]
            resumo["descompasso"][nome] = round(ajuste, 2)
        conn.commit()

    # Cartões: extrato próprio, que não mexe em saldo de conta nenhuma. O que
    # sai da conta é o pagamento da fatura, e esse vem pelo extrato bancário.
    for pc in conn.execute(
        "SELECT * FROM pluggy_contas WHERE casa_id = ? AND cartao_id IS NOT NULL AND ignorada = 0",
        (casa_id,),
    ).fetchall():
        try:
            rel = pluggy_importar_cartao(conn, casa_id, pc, criar=criar)
            pluggy_atualizar_cartao(conn, casa_id, pc)
        except PluggyErro:
            continue
        resumo["cartoes"] = resumo.get("cartoes", 0) + 1
        resumo["compras"] = resumo.get("compras", 0) + rel["criadas"]

    return resumo


def _saldo_calculado(conn, conta_id):
    ini = conn.execute("SELECT saldo_inicial FROM contas WHERE id = ?",
                       (conta_id,)).fetchone()["saldo_inicial"]
    r = conn.execute("SELECT COALESCE(SUM(valor),0) t FROM lancamentos "
                     "WHERE conta_id = ? AND tipo='renda' AND pago=1", (conta_id,)).fetchone()["t"]
    d = conn.execute("SELECT COALESCE(SUM(valor),0) t FROM lancamentos "
                     "WHERE conta_id = ? AND tipo='despesa' AND pago=1", (conta_id,)).fetchone()["t"]
    return ini + r - d


@app.route("/api/pluggy/sincronizar", methods=["POST"])
def pluggy_sincronizar_agora():
    if em_demo():
        return jsonify({"erro": msg("o modo demonstração não conecta em banco de verdade")}), 400
    conn = get_db()
    try:
        casa_id = minha_casa_id(conn)
        return jsonify(pluggy_sincronizar_tudo(conn, casa_id))
    finally:
        conn.close()


def _loop_pluggy():
    """Uma vez por dia. O banco não publica extrato de minuto em minuto, e
    consentimento do Open Finance tem limite de chamadas."""
    while True:
        time.sleep(86400)
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            conn.row_factory = sqlite3.Row
            for casa in conn.execute("SELECT DISTINCT casa_id FROM pluggy_credenciais").fetchall():
                try:
                    pluggy_sincronizar_tudo(conn, casa["casa_id"])
                except Exception as e:
                    print(f"[pluggy] casa {casa['casa_id']}: {e}", flush=True)
            conn.close()
        except Exception as e:
            print(f"[pluggy] ciclo falhou: {e}", flush=True)


def iniciar_agendador_pluggy():
    t = threading.Thread(target=_loop_pluggy, daemon=True)
    t.start()


# ---- Importação de cartão de crédito ----
#
# Cartão não é conta: o extrato dele não muda saldo de conta nenhuma. O que sai
# da conta é o pagamento da fatura, que aparece no extrato bancário e é
# importado por lá. Importar as compras do cartão como despesa de conta
# contaria a fatura duas vezes — uma na compra, outra no pagamento.
#
# Atenção ao sentido, que é invertido em relação à conta: num cartão, DEBIT é
# compra (aumenta o que você deve) e CREDIT é pagamento ou estorno (diminui).


def _mes_da_fatura(transacao):
    """Em qual fatura a compra cai (AAAA-MM).

    O cartão fecha antes do fim do mês, então uma compra do dia 12 pode entrar
    na fatura do mês seguinte. A Pluggy diz isso em `billForecastDate`; sem
    ele, o mês da própria transação é a melhor aproximação.
    """
    cc = transacao.get("creditCardMetadata") or {}
    previsto = cc.get("billForecastDate")
    if previsto and len(str(previsto)) >= 7:
        return str(previsto)[:7]
    return (transacao.get("date") or "")[:7]


def pluggy_importar_cartao(conn, casa_id, pluggy_conta, criar=True):
    """Importa o extrato de um cartão vinculado para `cartao_transacoes`."""
    cartao_id = pluggy_conta["cartao_id"]
    if not cartao_id:
        raise PluggyErro("essa conta da Pluggy não está vinculada a um cartão")

    account_id = pluggy_conta["account_id"]
    usuario_id = pluggy_conta["usuario_id"]
    garantir_categorias_do_open_finance(conn, casa_id)

    relatorio = {"vistas": 0, "ja_importadas": 0, "criadas": 0,
                 "pagamentos": 0, "parceladas": 0}

    for t in _pluggy_extrato(conn, casa_id, account_id):
        relatorio["vistas"] += 1
        transacao_id = t.get("id")
        if conn.execute("SELECT 1 FROM cartao_transacoes WHERE transacao_id = ?",
                        (transacao_id,)).fetchone():
            relatorio["ja_importadas"] += 1
            continue
        if not criar:
            continue

        cc = t.get("creditCardMetadata") or {}
        pluggy_tipo = (t.get("type") or "").upper()
        tipo = "pagamento" if pluggy_tipo == "CREDIT" else "compra"
        valor = abs(t.get("amount") or 0)
        data = (t.get("date") or "")[:10]

        # Categoria só faz sentido em compra: rotular um pagamento de fatura
        # como "Fatura de cartão" dentro do próprio cartão seria circular.
        categoria = None if tipo == "pagamento" else categoria_do_pluggy(
            t.get("category"), "despesa")

        parcela_num = cc.get("installmentNumber")
        parcela_total = cc.get("totalInstallments")
        if parcela_total:
            relatorio["parceladas"] += 1
        if tipo == "pagamento":
            relatorio["pagamentos"] += 1

        conn.execute(
            """INSERT INTO cartao_transacoes
                   (cartao_id, descricao, valor, data, categoria, usuario_id, criado_em,
                    transacao_id, origem, parcela_num, parcela_total, fatura_mes, tipo)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open_finance', ?, ?, ?, ?)""",
            (cartao_id, (t.get("description") or "Sem descrição").strip(), valor, data,
             categoria, usuario_id, datetime.now().isoformat(), transacao_id,
             parcela_num if isinstance(parcela_num, int) else None,
             parcela_total if isinstance(parcela_total, int) else None,
             _mes_da_fatura(t), tipo),
        )
        relatorio["criadas"] += 1

    conn.commit()
    return relatorio


def _resumo_pagamento_fatura(bruto):
    """Lê `payments[]` da fatura e diz se ela foi paga, quando e como.

    Quitar não é a mesma coisa que pagar o mínimo: a Pluggy distingue
    FULL_PAYMENT de INSTALLMENT_PAYMENT, e chamar as duas de "paga" faria o
    app dizer que está tudo certo enquanto a dívida rolou para o mês seguinte.

    Por isso só conta como paga quando há um FULL_PAYMENT, ou quando a soma dos
    pagamentos cobre o total — um centavo de folga porque arredondamento de
    juros costuma deixar diferença mínima.
    """
    pagamentos = bruto.get("payments") or []
    if not pagamentos:
        return {"pago": 0, "pago_em": None, "pago_valor": None,
                "pago_modo": None, "pago_tipo": None}

    total = bruto.get("totalAmount") or 0
    somado = sum(p.get("amount") or 0 for p in pagamentos)
    integral = next((p for p in pagamentos if p.get("valueType") == "FULL_PAYMENT"), None)
    quitou = bool(integral) or (total and somado >= total - 0.01)

    # O último pagamento é o que descreve o desfecho da fatura.
    ultimo = integral or max(
        pagamentos, key=lambda p: (p.get("paymentDate") or ""))
    return {
        "pago": 1 if quitou else 0,
        "pago_em": (ultimo.get("paymentDate") or "")[:10] or None,
        "pago_valor": round(somado, 2),
        "pago_modo": ultimo.get("paymentMode"),
        "pago_tipo": ultimo.get("valueType"),
    }


def _encargos_fatura(bruto):
    """Juros, multa e IOF da fatura: total e detalhe.

    Ficam guardados porque hoje esses valores somem no meio das transações e
    ninguém vê quanto custou ter atrasado.
    """
    encargos = bruto.get("financeCharges") or []
    total = sum(e.get("amount") or 0 for e in encargos)
    detalhe = [{"tipo": e.get("type"), "valor": e.get("amount"),
                "info": e.get("additionalInfo")} for e in encargos]
    return round(total, 2), (json.dumps(detalhe, ensure_ascii=False) if detalhe else None)


def pluggy_atualizar_cartao(conn, casa_id, pluggy_conta):
    """Traz limite, vencimento e faturas do cartão, com pagamento e encargos."""
    cartao_id = pluggy_conta["cartao_id"]
    account_id = pluggy_conta["account_id"]
    if not cartao_id:
        return {}

    dados = pluggy_pedir(conn, casa_id, "GET", "/accounts",
                         params={"itemId": pluggy_conta["item_id"]})
    credito = {}
    for c in dados.get("results", []):
        if c.get("id") == account_id:
            credito = c.get("creditData") or {}
            conn.execute("UPDATE cartoes SET fatura_atual = ? WHERE id = ?",
                         (c.get("balance") or 0, cartao_id))
            break

    if credito.get("creditLimit") is not None:
        conn.execute("UPDATE cartoes SET limite = ? WHERE id = ?",
                     (credito["creditLimit"], cartao_id))
    vence = credito.get("balanceDueDate")
    if vence and len(str(vence)) >= 10:
        conn.execute("UPDATE cartoes SET dia_vencimento = ? WHERE id = ?",
                     (int(str(vence)[8:10]), cartao_id))

    try:
        resposta = pluggy_pedir(conn, casa_id, "GET", "/bills", params={"accountId": account_id})
    except PluggyErro:
        resposta = {}

    faturas, pagas = 0, 0
    for b in resposta.get("results", []):
        pg = _resumo_pagamento_fatura(b)
        total_encargos, detalhe = _encargos_fatura(b)
        conn.execute(
            """INSERT INTO cartao_faturas
                   (fatura_id, cartao_id, account_id, vencimento, fechamento,
                    total, minimo, criado_em, pago, pago_em, pago_valor,
                    pago_modo, pago_tipo, encargos, encargos_detalhe, permite_parcelar)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(fatura_id) DO UPDATE SET
                   total = excluded.total, minimo = excluded.minimo,
                   vencimento = excluded.vencimento, fechamento = excluded.fechamento,
                   pago = excluded.pago, pago_em = excluded.pago_em,
                   pago_valor = excluded.pago_valor, pago_modo = excluded.pago_modo,
                   pago_tipo = excluded.pago_tipo, encargos = excluded.encargos,
                   encargos_detalhe = excluded.encargos_detalhe,
                   permite_parcelar = excluded.permite_parcelar""",
            (b.get("id"), cartao_id, account_id,
             (b.get("dueDate") or "")[:10] or None,
             (b.get("billClosingDate") or "")[:10] or None,
             b.get("totalAmount"), b.get("minimumPaymentAmount"),
             datetime.now().isoformat(), pg["pago"], pg["pago_em"], pg["pago_valor"],
             pg["pago_modo"], pg["pago_tipo"], total_encargos, detalhe,
             1 if b.get("allowsInstallments") else 0),
        )
        faturas += 1
        pagas += pg["pago"]

    # O `fatura_paga` do cartão passa a vir da fatura mais recente. É dado
    # observado no banco, não marcação manual — e é o único jeito de ele não
    # depender de alguém lembrar de marcar.
    recente = conn.execute(
        "SELECT pago FROM cartao_faturas WHERE cartao_id = ? "
        "ORDER BY vencimento DESC LIMIT 1", (cartao_id,)).fetchone()
    if recente is not None:
        conn.execute("UPDATE cartoes SET fatura_paga = ? WHERE id = ?",
                     (recente["pago"], cartao_id))

    conn.commit()
    return {"faturas": faturas, "pagas": pagas, "limite": credito.get("creditLimit")}


@app.route("/api/cartoes/<int:item_id>/faturas", methods=["GET"])
def listar_faturas_cartao(item_id):
    """Histórico de faturas do cartão, com pagamento e encargos."""
    conn = get_db()
    try:
        if not pertence_ao_usuario(conn, "cartoes", item_id):
            return jsonify({"erro": msg("cartão não encontrado")}), 404
        faturas = []
        for r in conn.execute(
            "SELECT * FROM cartao_faturas WHERE cartao_id = ? ORDER BY vencimento DESC",
            (item_id,)
        ).fetchall():
            d = dict(r)
            d["pago"] = bool(d["pago"])
            try:
                d["encargos_detalhe"] = json.loads(d["encargos_detalhe"]) if d["encargos_detalhe"] else []
            except (TypeError, ValueError):
                d["encargos_detalhe"] = []
            faturas.append(d)
        return jsonify(faturas)
    finally:
        conn.close()


# ---- Webhook da Pluggy ----
#
# O ciclo diário só descobre novidade uma vez por dia. Com o webhook, a Pluggy
# avisa quando chega transação nova ou quando a conexão quebra, e a coisa
# aparece em minutos.
#
# A URL é por instalação (`PLUGGY_WEBHOOK_URL`), e não fixa no código: o app é
# genérico e cada pessoa hospeda o seu. Sem essa variável o webhook fica
# desligado e o ciclo diário continua dando conta.
PLUGGY_WEBHOOK_URL = os.environ.get("PLUGGY_WEBHOOK_URL") or ""

# O endpoint não pode exigir sessão — quem chama é a Pluggy, que não tem login.
# Então ele se protege por um segredo na própria URL. Guardado em /data junto
# das outras chaves, para sobreviver a `docker compose up`.
PLUGGY_WEBHOOK_SEGREDO_PATH = os.path.join(os.path.dirname(DB_PATH), ".pluggy_webhook")


def pluggy_webhook_segredo():
    if os.path.exists(PLUGGY_WEBHOOK_SEGREDO_PATH):
        with open(PLUGGY_WEBHOOK_SEGREDO_PATH) as f:
            s = f.read().strip()
            if s:
                return s
    s = secrets.token_urlsafe(32)
    os.makedirs(os.path.dirname(PLUGGY_WEBHOOK_SEGREDO_PATH), exist_ok=True)
    with open(PLUGGY_WEBHOOK_SEGREDO_PATH, "w") as f:
        f.write(s)
    os.chmod(PLUGGY_WEBHOOK_SEGREDO_PATH, 0o600)
    return s


def pluggy_webhook_url_completa():
    """A URL que a Pluggy vai chamar, com o segredo. Vazia se não configurada."""
    if not PLUGGY_WEBHOOK_URL:
        return ""
    base = PLUGGY_WEBHOOK_URL.rstrip("/")
    return f"{base}/api/pluggy/webhook/{pluggy_webhook_segredo()}"


# Fila do que o webhook mandou processar. O endpoint responde na hora e o
# trabalho acontece aqui: a Pluggy exige resposta em menos de 5 segundos e
# tenta de novo 9 vezes se demorar — sincronizar durante a requisição
# garantiria timeout e avisos repetidos.
_pluggy_fila = []
_pluggy_fila_lock = threading.Lock()


@app.route("/api/pluggy/webhook/<segredo>", methods=["POST"])
def pluggy_webhook(segredo):
    # Comparação em tempo constante: com `==`, o tempo de resposta vazaria
    # quantos caracteres do segredo o atacante já acertou.
    if not secrets.compare_digest(segredo, pluggy_webhook_segredo()):
        return jsonify({"erro": msg("não autorizado")}), 401

    dados = request.get_json(silent=True) or {}
    evento = dados.get("event") or ""
    item_id = dados.get("itemId")

    # Só enfileira o que muda dado nosso. `connector/status_updated` e os
    # eventos de pagamento não mexem em nada aqui.
    interessa = evento.startswith("transactions/") or evento in (
        "item/updated", "item/created", "item/error", "item/waiting_user_input",
        "item/login_succeeded", "item/deleted",
    )
    if interessa and item_id:
        with _pluggy_fila_lock:
            if item_id not in _pluggy_fila:
                _pluggy_fila.append(item_id)

    # 2xx imediato. A Pluggy recomenda não confiar no corpo do aviso e
    # consultar o item depois — é o que o processador faz.
    return jsonify({"ok": True}), 200


def _loop_webhook():
    """Processa a fila do webhook. Confere a cada 20s em vez de reagir na
    hora: vários avisos do mesmo item chegam em rajada, e agrupar evita
    sincronizar a mesma coisa cinco vezes seguidas."""
    while True:
        time.sleep(20)
        with _pluggy_fila_lock:
            pendentes, _pluggy_fila[:] = list(_pluggy_fila), []
        if not pendentes:
            continue
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            conn.row_factory = sqlite3.Row
            casas = set()
            for item_id in pendentes:
                row = conn.execute("SELECT casa_id FROM pluggy_itens WHERE item_id = ?",
                                   (item_id,)).fetchone()
                if row:
                    casas.add(row["casa_id"])
            for casa_id in casas:
                try:
                    pluggy_sincronizar_tudo(conn, casa_id)
                except Exception as e:
                    print(f"[webhook] casa {casa_id}: {e}", flush=True)
            conn.close()
        except Exception as e:
            print(f"[webhook] falhou: {e}", flush=True)


def iniciar_processador_webhook():
    t = threading.Thread(target=_loop_webhook, daemon=True)
    t.start()


@app.route("/api/pluggy/webhook-status", methods=["GET"])
def pluggy_webhook_status():
    """O que a tela precisa para explicar o estado do webhook."""
    conn = get_db()
    try:
        casa_id = minha_casa_id(conn)
        url = pluggy_webhook_url_completa()
        registrados = []
        if url:
            try:
                for w in pluggy_pedir(conn, casa_id, "GET", "/webhooks").get("results", []):
                    registrados.append({"id": w.get("id"), "url": w.get("url"),
                                        "evento": w.get("event")})
            except PluggyErro as e:
                # Listar webhook é informativo: sem isso a tela ainda funciona,
                # só não mostra o que já está registrado do outro lado.
                print(f"[pluggy] não deu para listar os webhooks: {e}", flush=True)
        return jsonify({
            "configurado": bool(PLUGGY_WEBHOOK_URL),
            # O segredo nunca sai daqui; a tela só precisa saber se existe URL.
            "url_publica": PLUGGY_WEBHOOK_URL or None,
            "registrados": registrados,
        })
    finally:
        conn.close()


@app.route("/api/pluggy/webhook-registrar", methods=["POST"])
def pluggy_webhook_registrar():
    """Cadastra (ou recadastra) o webhook na Pluggy."""
    conn = get_db()
    try:
        if not eh_administrador(conn):
            return jsonify({"erro": msg("só o administrador da casa pode configurar o webhook")}), 403
        url = pluggy_webhook_url_completa()
        if not url:
            return jsonify({"erro": "defina PLUGGY_WEBHOOK_URL no .env com o endereço "
                                    "público do seu servidor"}), 400
        if not url.startswith("https://"):
            return jsonify({"erro": msg("a Pluggy só aceita HTTPS e recusa localhost")}), 400

        casa_id = minha_casa_id(conn)
        try:
            # Remove os antigos desta instalação: recadastrar sem limpar
            # deixaria a Pluggy mandando o mesmo aviso várias vezes.
            for w in pluggy_pedir(conn, casa_id, "GET", "/webhooks").get("results", []):
                if (w.get("url") or "").startswith(PLUGGY_WEBHOOK_URL.rstrip("/")):
                    pluggy_pedir(conn, casa_id, "DELETE", f"/webhooks/{quote(str(w['id']), safe='')}")
            criado = pluggy_pedir(conn, casa_id, "POST", "/webhooks",
                                  json={"event": "all", "url": url})
        except PluggyErro as e:
            return jsonify({"erro": str(e)}), 400
        return jsonify({"ok": True, "id": criado.get("id")})
    finally:
        conn.close()


# ---------------- Regras de categorização ----------------
#
# A categoria que a Pluggy manda é genérica ("Services", "Transfers"). A regra
# olha a DESCRIÇÃO, que é onde está o nome do estabelecimento — é assim que
# "UBER *TRIP 8829" vira Transporte. Regra primeiro, categoria da Pluggy como
# reserva.

# Operadores aceitos numa condição. Deliberadamente poucos: cobre o que
# aparece de verdade em descrição de banco, e cada um a mais é mais tela para
# a pessoa entender.
OPERADORES_REGRA = {
    "comeca_com": lambda valor, alvo: valor.startswith(alvo),
    "contem": lambda valor, alvo: alvo in valor,
    "igual": lambda valor, alvo: valor == alvo,
    "termina_com": lambda valor, alvo: valor.endswith(alvo),
    # Palavra inteira. Existe porque "contem" com uma sigla curta morde dentro
    # de outra palavra: a condicao "TIM" pegava "EMPRESTIMO" e mandava
    # emprestimo do Itau para a categoria Internet. Tentar resolver pondo
    # espaco no fim ("TIM ") nao funciona — a normalizacao tira espaco das
    # pontas, e o espaco sumia justamente onde ele importava.
    "palavra": lambda valor, alvo: re.search(
        r"\b" + re.escape(alvo) + r"\b", valor) is not None,
}


def _texto_normalizado(t):
    """Descrição de banco vem em caixa alta, com acento inconsistente e espaço
    sobrando. Comparar cru faria a mesma regra pegar num mês e falhar no outro."""
    import unicodedata

    t = unicodedata.normalize("NFKD", str(t or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.upper().split())


def regra_casa(regra, descricao, valor, tipo):
    """Se a regra se aplica. Condições da mesma regra são OU: "Uber OU 99" é
    uma regra só, não duas."""
    try:
        condicoes = json.loads(regra["condicoes"])
    except (TypeError, ValueError):
        return False
    if not condicoes:
        return False

    desc = _texto_normalizado(descricao)
    for c in condicoes:
        campo = c.get("campo", "descricao")
        if campo == "tipo":
            if c.get("valor") == tipo:
                return True
            continue
        if campo == "valor_min":
            try:
                if abs(valor) >= float(c.get("valor")):
                    return True
            except (TypeError, ValueError):
                # Regra com valor em branco ou não numérico simplesmente não
                # casa; é condição de dado, não erro, e não vale poluir o log.
                continue
            continue
        op = OPERADORES_REGRA.get(c.get("operador", "contem"))
        if op and op(desc, _texto_normalizado(c.get("valor"))):
            return True
    return False


def aplicar_regras(conn, casa_id, descricao, valor, tipo):
    """Primeira regra que casar decide. Devolve (categoria, transferencia,
    regra_id) ou (None, False, None)."""
    for r in conn.execute(
        "SELECT * FROM regras_categoria WHERE casa_id = ? AND ativa = 1 "
        "ORDER BY prioridade, id", (casa_id,)
    ).fetchall():
        if regra_casa(r, descricao, valor, tipo):
            conn.execute(
                "UPDATE regras_categoria SET vezes_aplicada = COALESCE(vezes_aplicada,0) + 1, "
                "ultima_aplicacao = ? WHERE id = ?",
                (datetime.now().isoformat(), r["id"]),
            )
            return r["categoria"], bool(r["marca_transferencia"]), r["id"]
    return None, False, None


# Regras que toda casa ganha na primeira vez. São os estabelecimentos que
# aparecem em quase todo extrato brasileiro — sem elas a pessoa começa com uma
# tela vazia e não entende para que serve.
REGRAS_INICIAIS = [
    ("Uber e 99 → Transporte", 10, "Transporte", 0, [
        {"campo": "descricao", "operador": "contem", "valor": "UBER"},
        {"campo": "descricao", "operador": "contem", "valor": "99APP"},
        {"campo": "descricao", "operador": "contem", "valor": "99 TECNOLOGIA"},
        {"campo": "descricao", "operador": "contem", "valor": "CABIFY"},
    ]),
    ("iFood e delivery → Alimentação", 10, "Alimentação", 0, [
        {"campo": "descricao", "operador": "contem", "valor": "IFOOD"},
        {"campo": "descricao", "operador": "contem", "valor": "RAPPI"},
        {"campo": "descricao", "operador": "contem", "valor": "AIQFOME"},
    ]),
    ("Streaming → Lazer", 10, "Lazer", 0, [
        {"campo": "descricao", "operador": "contem", "valor": "NETFLIX"},
        {"campo": "descricao", "operador": "contem", "valor": "SPOTIFY"},
        {"campo": "descricao", "operador": "contem", "valor": "DISNEY"},
        {"campo": "descricao", "operador": "contem", "valor": "PRIME VIDEO"},
        {"campo": "descricao", "operador": "contem", "valor": "HBO"},
        {"campo": "descricao", "operador": "contem", "valor": "STEAM"},
    ]),
    ("Mercados → Mercado", 10, "Mercado", 0, [
        {"campo": "descricao", "operador": "contem", "valor": "CARREFOUR"},
        {"campo": "descricao", "operador": "contem", "valor": "ASSAI"},
        {"campo": "descricao", "operador": "contem", "valor": "ATACADAO"},
        {"campo": "descricao", "operador": "contem", "valor": "PAO DE ACUCAR"},
        {"campo": "descricao", "operador": "contem", "valor": "SUPERMERCADO"},
    ]),
    ("Postos → Combustível", 10, "Combustível", 0, [
        {"campo": "descricao", "operador": "contem", "valor": "POSTO"},
        {"campo": "descricao", "operador": "contem", "valor": "IPIRANGA"},
        {"campo": "descricao", "operador": "palavra", "valor": "SHELL"},
        {"campo": "descricao", "operador": "contem", "valor": "PETROBRAS"},
    ]),
    ("Farmácias → Saúde", 10, "Saúde", 0, [
        {"campo": "descricao", "operador": "contem", "valor": "DROGARIA"},
        {"campo": "descricao", "operador": "contem", "valor": "DROGASIL"},
        {"campo": "descricao", "operador": "contem", "valor": "PACHECO"},
        {"campo": "descricao", "operador": "palavra", "valor": "RAIA"},
        {"campo": "descricao", "operador": "contem", "valor": "FARMACIA"},
    ]),
    ("Telefone e internet → Internet", 10, "Internet", 0, [
        {"campo": "descricao", "operador": "contem", "valor": "VIVO"},
        {"campo": "descricao", "operador": "contem", "valor": "CLARO"},
        {"campo": "descricao", "operador": "palavra", "valor": "TIM"},
        {"campo": "descricao", "operador": "contem", "valor": "OI FIBRA"},
    ]),
    ("Energia → Energia", 10, "Energia", 0, [
        {"campo": "descricao", "operador": "contem", "valor": "ENEL"},
        {"campo": "descricao", "operador": "contem", "valor": "CEMIG"},
        {"campo": "descricao", "operador": "contem", "valor": "COPEL"},
        {"campo": "descricao", "operador": "contem", "valor": "LIGHT SERV"},
        {"campo": "descricao", "operador": "contem", "valor": "CPFL"},
    ]),
    # Prioridade menor: precisa vencer as genéricas de "pagamento".
    ("Salário → Salário", 5, "Salário", 0, [
        {"campo": "descricao", "operador": "contem", "valor": "FOLHA PAGAMENTO"},
        {"campo": "descricao", "operador": "contem", "valor": "SALARIO"},
        {"campo": "descricao", "operador": "contem", "valor": "PAGAMENTO SALARIO"},
    ]),
]


def garantir_regras_iniciais(conn, casa_id):
    """Cria as regras de partida uma única vez por casa. Se a pessoa apagar
    uma delas, não volta — apagar é uma decisão, e recriar seria teimosia."""
    ja = conn.execute(
        "SELECT COUNT(*) c FROM regras_categoria WHERE casa_id = ?", (casa_id,)
    ).fetchone()["c"]
    if ja:
        return 0
    garantir_categorias_do_open_finance(conn, casa_id)
    for nome, prioridade, categoria, transf, condicoes in REGRAS_INICIAIS:
        conn.execute(
            "INSERT INTO regras_categoria (casa_id, nome, prioridade, condicoes, "
            "categoria, marca_transferencia, ativa, criado_em) VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
            (casa_id, nome, prioridade, json.dumps(condicoes, ensure_ascii=False),
             categoria, transf, datetime.now().isoformat()),
        )
    conn.commit()
    return len(REGRAS_INICIAIS)


@app.route("/api/regras", methods=["GET"])
def listar_regras():
    conn = get_db()
    try:
        casa_id = minha_casa_id(conn)
        garantir_regras_iniciais(conn, casa_id)
        regras = []
        for r in conn.execute(
            "SELECT * FROM regras_categoria WHERE casa_id = ? ORDER BY prioridade, id",
            (casa_id,)
        ).fetchall():
            d = dict(r)
            try:
                d["condicoes"] = json.loads(d["condicoes"])
            except (TypeError, ValueError):
                d["condicoes"] = []
            d["ativa"] = bool(d["ativa"])
            d["marca_transferencia"] = bool(d["marca_transferencia"])
            regras.append(d)
        return jsonify(regras)
    finally:
        conn.close()


@app.route("/api/regras", methods=["POST"])
def criar_regra():
    dados = request.get_json(silent=True) or {}
    nome = (dados.get("nome") or "").strip()
    categoria = (dados.get("categoria") or "").strip()
    condicoes = dados.get("condicoes") or []
    if not nome or not categoria or not condicoes:
        return jsonify({"erro": msg("informe nome, categoria e ao menos uma condição")}), 400
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO regras_categoria (casa_id, nome, prioridade, condicoes, "
            "categoria, marca_transferencia, ativa, criado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (minha_casa_id(conn), nome, int(dados.get("prioridade") or 10),
             json.dumps(condicoes, ensure_ascii=False), categoria,
             int(bool(dados.get("marca_transferencia"))),
             int(bool(dados.get("ativa", True))), datetime.now().isoformat()),
        )
        conn.commit()
        return jsonify({"ok": True}), 201
    finally:
        conn.close()


@app.route("/api/regras/<int:item_id>", methods=["PUT"])
def editar_regra(item_id):
    dados = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        casa_id = minha_casa_id(conn)
        dona = conn.execute("SELECT casa_id FROM regras_categoria WHERE id = ?",
                            (item_id,)).fetchone()
        if not dona or dona["casa_id"] != casa_id:
            return jsonify({"erro": msg("regra não encontrada")}), 404
        conn.execute(
            "UPDATE regras_categoria SET nome = ?, prioridade = ?, condicoes = ?, "
            "categoria = ?, marca_transferencia = ?, ativa = ? WHERE id = ?",
            ((dados.get("nome") or "").strip(), int(dados.get("prioridade") or 10),
             json.dumps(dados.get("condicoes") or [], ensure_ascii=False),
             (dados.get("categoria") or "").strip(),
             int(bool(dados.get("marca_transferencia"))),
             int(bool(dados.get("ativa", True))), item_id),
        )
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/regras/<int:item_id>", methods=["DELETE"])
def apagar_regra(item_id):
    conn = get_db()
    try:
        casa_id = minha_casa_id(conn)
        dona = conn.execute("SELECT casa_id FROM regras_categoria WHERE id = ?",
                            (item_id,)).fetchone()
        if not dona or dona["casa_id"] != casa_id:
            return jsonify({"erro": msg("regra não encontrada")}), 404
        conn.execute("DELETE FROM regras_categoria WHERE id = ?", (item_id,))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/regras/aplicar", methods=["POST"])
def aplicar_regras_no_historico():
    """Roda as regras no que já existe.

    Só toca em lançamento SEM categoria, a não ser que `sobrescrever` venha
    ligado: reclassificar o que a pessoa categorizou à mão apagaria trabalho
    dela sem pedir licença.
    """
    dados = request.get_json(silent=True) or {}
    sobrescrever = bool(dados.get("sobrescrever"))
    conn = get_db()
    try:
        casa_id = minha_casa_id(conn)
        garantir_regras_iniciais(conn, casa_id)

        filtro = "" if sobrescrever else " AND (l.categoria IS NULL OR l.categoria = '')"
        alvo = conn.execute(
            "SELECT l.id, l.descricao, l.valor, l.tipo FROM lancamentos l "
            "JOIN usuarios u ON u.id = l.usuario_id "
            "WHERE u.casa_id = ? AND l.eh_transferencia = 0" + filtro,
            (casa_id,)
        ).fetchall()

        mudados = 0
        for l in alvo:
            categoria, transf, _ = aplicar_regras(conn, casa_id, l["descricao"],
                                                  l["valor"], l["tipo"])
            if categoria:
                conn.execute(
                    "UPDATE lancamentos SET categoria = ?, eh_transferencia = ? WHERE id = ?",
                    (categoria, int(transf), l["id"]),
                )
                mudados += 1
        conn.commit()
        return jsonify({"ok": True, "avaliados": len(alvo), "classificados": mudados})
    finally:
        conn.close()


# ---------------- Não categorizados ----------------
#
# Agrupa por descrição em vez de listar um a um. Nos dados reais, 14 lançamentos
# sem categoria eram só 3 descrições repetidas — pedir para classificar 14 vezes
# a mesma coisa seria trabalho inventado.
#
# Ao classificar um grupo, a pessoa pode criar a regra junto. É isso que faz o
# sistema aprender: da próxima vez que aquele estabelecimento aparecer, já vem
# categorizado.

# Prefixos que o banco põe antes do nome do estabelecimento. Eles atrapalham a
# regra: "Compra débito CARREFOUR" e "CARREFOUR" são o mesmo lugar, e uma
# condição com o prefixo deixaria de casar quando o banco mudasse o texto.
PREFIXOS_BANCARIOS = [
    "COMPRA DEBITO", "COMPRA CARTAO", "COMPRA CREDITO", "PAGAMENTO DE",
    "PAGAMENTO", "SAIDA", "ENTRADA", "PIX ENVIADO", "PIX RECEBIDO",
    "TED ENVIADA", "TED RECEBIDA", "DEBITO AUTOMATICO", "TRANSFERENCIA",
    "COMPRA", "DEBITO", "CREDITO",
]


def sugerir_condicao_regra(descricao):
    """Um trecho da descrição que sirva de condição.

    Tira o prefixo do banco e os números do fim (data, parcela, código da
    maquininha), que mudam a cada compra e fariam a regra pegar uma vez só.
    """
    texto = _texto_normalizado(descricao)
    for p in PREFIXOS_BANCARIOS:
        if texto.startswith(p + " "):
            texto = texto[len(p) + 1:]
            break

    # Corta no primeiro pedaço que parece código: sequência com dígito, ou
    # data entre parênteses.
    palavras = []
    for palavra in texto.split():
        if any(c.isdigit() for c in palavra):
            break
        palavras.append(palavra)
        if len(palavras) >= 3:
            break
    sugestao = " ".join(palavras).strip(" -*/()")
    # Curto demais não distingue nada; aí é melhor a descrição inteira.
    return sugestao if len(sugestao) >= 4 else texto[:40].strip()


@app.route("/api/nao-categorizados", methods=["GET"])
def listar_nao_categorizados():
    """Grupos de lançamentos sem categoria, do mais frequente para o menos."""
    conn = get_db()
    try:
        casa_id = minha_casa_id(conn)
        linhas = conn.execute(
            """SELECT l.id, l.descricao, l.valor, l.tipo,
                      COALESCE(l.data_pagamento, l.vencimento, l.mes) AS quando,
                      l.conta, l.origem
               FROM lancamentos l JOIN usuarios u ON u.id = l.usuario_id
               WHERE u.casa_id = ? AND l.eh_transferencia = 0
                 AND (l.categoria IS NULL OR l.categoria = '')
               ORDER BY quando DESC""",
            (casa_id,),
        ).fetchall()

        grupos = {}
        for l in linhas:
            chave = _texto_normalizado(l["descricao"])
            g = grupos.setdefault(chave, {
                "descricao": l["descricao"],
                "chave": chave,
                "quantidade": 0,
                "total": 0.0,
                "tipo": l["tipo"],
                "primeira": l["quando"],
                "ultima": l["quando"],
                "ids": [],
                "sugestao": sugerir_condicao_regra(l["descricao"]),
                "do_banco": False,
            })
            g["quantidade"] += 1
            g["total"] += l["valor"] or 0
            g["ids"].append(l["id"])
            if l["quando"] and (not g["primeira"] or l["quando"] < g["primeira"]):
                g["primeira"] = l["quando"]
            if l["quando"] and (not g["ultima"] or l["quando"] > g["ultima"]):
                g["ultima"] = l["quando"]
            if l["origem"] == "open_finance":
                g["do_banco"] = True

        ordenados = sorted(grupos.values(),
                           key=lambda g: (-g["quantidade"], -abs(g["total"])))
        return jsonify({"total": len(linhas), "grupos": ordenados})
    finally:
        conn.close()


@app.route("/api/nao-categorizados/classificar", methods=["POST"])
def classificar_nao_categorizados():
    """Classifica um grupo inteiro e, se pedido, cria a regra para o futuro."""
    dados = request.get_json(silent=True) or {}
    ids = dados.get("ids") or []
    categoria = (dados.get("categoria") or "").strip()
    transferencia = bool(dados.get("transferencia"))
    criar_regra = bool(dados.get("criar_regra"))
    condicao = (dados.get("condicao") or "").strip()

    if not ids:
        return jsonify({"erro": msg("nada para classificar")}), 400
    if not categoria and not transferencia:
        return jsonify({"erro": msg("escolha uma categoria, ou marque como transferência")}), 400

    conn = get_db()
    try:
        casa_id = minha_casa_id(conn)
        # Só mexe no que é da casa: o id vem da tela e não pode ser confiado.
        marcadores = ",".join("?" for _ in ids)
        proprios = [r["id"] for r in conn.execute(
            f"SELECT l.id FROM lancamentos l JOIN usuarios u ON u.id = l.usuario_id "
            f"WHERE u.casa_id = ? AND l.id IN ({marcadores})",
            [casa_id] + list(ids)).fetchall()]
        if not proprios:
            return jsonify({"erro": msg("nenhum desses lançamentos é desta casa")}), 404

        marcadores2 = ",".join("?" for _ in proprios)
        conn.execute(
            f"UPDATE lancamentos SET categoria = ?, eh_transferencia = ? "
            f"WHERE id IN ({marcadores2})",
            [categoria or None, int(transferencia)] + proprios)

        regra_id = None
        if criar_regra and condicao:
            cur = conn.execute(
                "INSERT INTO regras_categoria (casa_id, nome, prioridade, condicoes, "
                "categoria, marca_transferencia, ativa, criado_em) "
                "VALUES (?, ?, 10, ?, ?, ?, 1, ?)",
                (casa_id, f"{condicao} → {categoria or 'transferência'}",
                 json.dumps([{"campo": "descricao", "operador": "contem",
                              "valor": condicao}], ensure_ascii=False),
                 categoria or "", int(transferencia), datetime.now().isoformat()),
            )
            regra_id = cur.lastrowid
        conn.commit()

        return jsonify({"ok": True, "classificados": len(proprios), "regra_id": regra_id})
    finally:
        conn.close()


# ---------------- Money Map ----------------
#
# De onde o dinheiro veio e para onde foi, num período. É um fluxo, não uma
# série temporal nem um ranking — por isso Sankey e não barra: o que interessa
# é a proporção de cada origem e cada destino dentro do mesmo total.
#
# Transferência entre contas próprias fica de fora. Ela move saldo mas não é
# ganho nem gasto, e entraria dos dois lados inflando o mapa inteiro.

PERIODOS_MONEY_MAP = {
    "30d": 30, "3m": 90, "6m": 180, "1a": 365,
}


def _intervalo_money_map(periodo):
    """(inicio, fim) em AAAA-MM-DD. `ano` é o ano corrente, não os últimos
    365 dias — é o que a pessoa espera de 'este ano'."""
    hoje = datetime.now().date()
    if periodo == "ano":
        return f"{hoje.year}-01-01", hoje.isoformat()
    dias = PERIODOS_MONEY_MAP.get(periodo, 30)
    return (hoje - timedelta(days=dias)).isoformat(), hoje.isoformat()


@app.route("/api/money-map", methods=["GET"])
def money_map():
    periodo = request.args.get("periodo", "30d")
    inicio, fim = _intervalo_money_map(periodo)

    conn = get_db()
    try:
        # Só o que foi efetivado: previsão não é dinheiro que andou, e
        # misturar os dois faria o mapa mostrar um mês que ainda não aconteceu.
        linhas = conn.execute(
            """SELECT tipo, COALESCE(NULLIF(categoria,''), 'Sem categoria') AS categoria,
                      SUM(valor) AS total, COUNT(*) AS n
               FROM lancamentos
               WHERE usuario_id = ? AND pago = 1 AND eh_transferencia = 0
                 AND COALESCE(data_pagamento, vencimento, mes || '-15') BETWEEN ? AND ?
               GROUP BY tipo, categoria""",
            (uid(), inicio, fim),
        ).fetchall()

        receitas = sorted(
            [{"nome": r["categoria"], "valor": r["total"], "n": r["n"]}
             for r in linhas if r["tipo"] == "renda"],
            key=lambda x: -x["valor"])
        despesas = sorted(
            [{"nome": r["categoria"], "valor": r["total"], "n": r["n"]}
             for r in linhas if r["tipo"] == "despesa"],
            key=lambda x: -x["valor"])

        # Acima de 8 fatias a legenda vira ilegível e as faixas somem de finas.
        # O que sobra vira "Outros" — some do detalhe, não da conta.
        def agrupar(lista, limite=6):
            if len(lista) <= limite:
                return lista
            resto = lista[limite:]
            return lista[:limite] + [{
                "nome": "Outros", "valor": sum(x["valor"] for x in resto),
                "n": sum(x["n"] for x in resto), "agrupado": len(resto),
            }]

        total_receita = sum(r["valor"] for r in receitas)
        total_despesa = sum(d["valor"] for d in despesas)

        return jsonify({
            "periodo": periodo,
            "inicio": inicio,
            "fim": fim,
            "receitas": agrupar(receitas),
            "despesas": agrupar(despesas),
            "total_receita": total_receita,
            "total_despesa": total_despesa,
            "saldo": total_receita - total_despesa,
        })
    finally:
        conn.close()


# ---------------- Metas: estado e ritmo ----------------
#
# Guardar quanto falta é fácil; o que a pessoa quer saber é se está no ritmo.
# Por isso cada meta devolve quanto precisa por mês e se está adiantada ou
# atrasada em relação ao prazo.

ESTADOS_META = ("ativa", "pausada", "concluida", "arquivada")


def _meses_ate(prazo):
    """Meses inteiros de hoje até o prazo (AAAA-MM). None se não há prazo."""
    if not prazo:
        return None
    try:
        ano, mes = int(prazo[:4]), int(prazo[5:7])
    except (ValueError, IndexError):
        return None
    hoje = datetime.now()
    return max((ano - hoje.year) * 12 + (mes - hoje.month), 0)


def _situacao_meta(m):
    """Enriquece a meta com progresso, ritmo e se está adiantada.

    'Adiantada' compara o que já foi guardado com o que deveria estar guardado
    a esta altura, distribuindo o alvo por igual entre o início e o prazo. Sem
    prazo não existe atraso possível — só progresso.
    """
    alvo = m.get("valor_alvo") or 0
    atual = m.get("valor_atual") or 0
    d = dict(m)
    d["progresso"] = min(atual / alvo, 1.0) if alvo else 0.0
    d["falta"] = max(alvo - atual, 0)

    meses = _meses_ate(m.get("prazo"))
    d["meses_restantes"] = meses
    # Quanto precisa guardar por mês daqui para frente. Com o prazo no mês
    # corrente (0 meses), o que falta é para agora.
    d["por_mes"] = (d["falta"] / meses) if meses else d["falta"]

    d["situacao"] = None
    if m.get("estado") == "concluida" or (alvo and atual >= alvo):
        d["situacao"] = "concluida"
    elif m.get("estado") in ("pausada", "arquivada"):
        d["situacao"] = m["estado"]
    elif m.get("prazo") and m.get("inicio"):
        total = _meses_entre(m["inicio"], m["prazo"])
        decorridos = _meses_entre(m["inicio"], datetime.now().strftime("%Y-%m"))
        if total and total > 0:
            esperado = alvo * min(max(decorridos / total, 0), 1)
            d["esperado"] = esperado
            d["situacao"] = "adiantada" if atual >= esperado else "atrasada"
    return d


def _meses_entre(de, ate):
    try:
        a1, m1 = int(de[:4]), int(de[5:7])
        a2, m2 = int(ate[:4]), int(ate[5:7])
    except (ValueError, IndexError, TypeError):
        return None
    return (a2 - a1) * 12 + (m2 - m1)


@app.route("/api/metas/<int:item_id>/estado", methods=["PUT"])
def mudar_estado_meta(item_id):
    dados = request.get_json(silent=True) or {}
    estado = (dados.get("estado") or "").strip()
    if estado not in ESTADOS_META:
        return jsonify({"erro": msg("estado inválido")}), 400
    conn = get_db()
    try:
        if not pertence_ao_usuario(conn, "metas", item_id):
            return jsonify({"erro": msg("meta não encontrada")}), 404
        concluida = datetime.now().isoformat() if estado == "concluida" else None
        conn.execute("UPDATE metas SET estado = ?, concluida_em = ? WHERE id = ?",
                     (estado, concluida, item_id))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/metas/<int:item_id>/depositos", methods=["POST"])
def depositar_meta(item_id):
    """Registra um depósito e recalcula o total.

    O total é a SOMA dos depósitos, não um número digitado à parte: assim ele
    nunca fica em desacordo com o histórico, e apagar um depósito errado
    corrige o total sozinho.
    """
    dados = request.get_json(silent=True) or {}
    try:
        valor = float(dados.get("valor") or 0)
    except (TypeError, ValueError):
        valor = 0
    if valor == 0:
        return jsonify({"erro": msg("informe um valor")}), 400

    conn = get_db()
    try:
        if not pertence_ao_usuario(conn, "metas", item_id):
            return jsonify({"erro": msg("meta não encontrada")}), 404
        conn.execute(
            "INSERT INTO meta_depositos (meta_id, valor, data, observacao, usuario_id, criado_em) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (item_id, valor, (dados.get("data") or datetime.now().strftime("%Y-%m-%d"))[:10],
             (dados.get("observacao") or "").strip() or None, uid(), datetime.now().isoformat()),
        )
        total = conn.execute("SELECT COALESCE(SUM(valor),0) t FROM meta_depositos WHERE meta_id = ?",
                             (item_id,)).fetchone()["t"]
        meta = conn.execute("SELECT valor_alvo, estado FROM metas WHERE id = ?", (item_id,)).fetchone()
        # Bateu o alvo? conclui sozinha — mas só se ainda estava ativa, para não
        # ressuscitar meta que a pessoa arquivou de propósito.
        estado = meta["estado"]
        concluida = None
        if meta["valor_alvo"] and total >= meta["valor_alvo"] and estado == "ativa":
            estado, concluida = "concluida", datetime.now().isoformat()
        conn.execute("UPDATE metas SET valor_atual = ?, estado = ?, concluida_em = ? WHERE id = ?",
                     (total, estado, concluida, item_id))
        conn.commit()
        return jsonify({"ok": True, "total": total, "estado": estado})
    finally:
        conn.close()


@app.route("/api/metas/<int:item_id>/depositos", methods=["GET"])
def listar_depositos_meta(item_id):
    conn = get_db()
    try:
        if not pertence_ao_usuario(conn, "metas", item_id):
            return jsonify({"erro": msg("meta não encontrada")}), 404
        return jsonify([dict(r) for r in conn.execute(
            "SELECT * FROM meta_depositos WHERE meta_id = ? ORDER BY data DESC, id DESC",
            (item_id,)).fetchall()])
    finally:
        conn.close()


@app.route("/api/metas/depositos/<int:item_id>", methods=["DELETE"])
def apagar_deposito_meta(item_id):
    conn = get_db()
    try:
        dep = conn.execute("SELECT meta_id, usuario_id FROM meta_depositos WHERE id = ?",
                           (item_id,)).fetchone()
        if not dep or dep["usuario_id"] != uid():
            return jsonify({"erro": msg("depósito não encontrado")}), 404
        meta_id = dep["meta_id"]
        conn.execute("DELETE FROM meta_depositos WHERE id = ?", (item_id,))
        total = conn.execute("SELECT COALESCE(SUM(valor),0) t FROM meta_depositos WHERE meta_id = ?",
                             (meta_id,)).fetchone()["t"]
        conn.execute("UPDATE metas SET valor_atual = ? WHERE id = ?", (total, meta_id))
        conn.commit()
        return jsonify({"ok": True, "total": total})
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    # O banco demo só é recriado do zero quando alguém liga o modo demonstração
    # (_semear_demo) — sem isso, uma migration nova feita depois da última vez
    # que isso aconteceu nunca chegaria nele, e qualquer ação ali quebraria com
    # "no such column". Migrar (não recriar) toda subida resolve sem mexer nos
    # dados fictícios que já estavam lá.
    if os.path.exists(DEMO_DB_PATH):
        init_db(DEMO_DB_PATH, criar_usuario_inicial=False)
    iniciar_agendador_backup()
    iniciar_agendador_cotacoes()
    iniciar_agendador_catalogo()
    iniciar_agendador_pluggy()
    iniciar_processador_webhook()
    app.run(host="0.0.0.0", port=5000)
