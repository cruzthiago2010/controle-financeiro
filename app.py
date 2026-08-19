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
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
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
    if request.path == "/":
        if not session.get("usuario_id"):
            return redirect("/login")
        return None
    if request.path.startswith("/api/"):
        if not session.get("usuario_id"):
            return jsonify({"erro": "não autenticado"}), 401
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and not em_demo()
            and session.get("somente_leitura")
            and not escrita_liberada_para_leitura(request.path)
        ):
            return jsonify({"erro": "acesso somente leitura — essa conta não pode fazer alterações"}), 403
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
    conn = sqlite3.connect(caminho_banco_atual())
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
        return jsonify({"erro": "usuário ou senha inválidos"}), 401
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
        return jsonify({"erro": "nome da casa, seu nome, usuário e senha (mín. 4 caracteres) são obrigatórios"}), 400

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
        return jsonify({"erro": "esse usuário já existe"}), 400

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
        return jsonify({"erro": "login com Google não está configurado nesse servidor"}), 501
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
        return jsonify({"erro": "não autenticado"}), 401
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
        return jsonify({"erro": "nome, usuário e senha (mín. 4 caracteres) são obrigatórios"}), 400
    conn = get_db()
    # Quem entra na casa decide quem mais entra: sem isso, qualquer conta com
    # escrita podia criar outra pessoa com acesso aos dados da família.
    if not eh_administrador(conn):
        conn.close()
        return jsonify({"erro": "só o administrador pode adicionar usuários"}), 403
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
        return jsonify({"erro": "esse usuário já existe"}), 400
    conn.close()
    return jsonify({"ok": True}), 201


@app.route("/api/usuarios/<int:item_id>/somente-leitura", methods=["PUT"])
def alternar_somente_leitura(item_id):
    data = request.get_json(force=True)
    somente_leitura = bool(data.get("somente_leitura"))
    conn = get_db()
    if not eh_administrador(conn):
        conn.close()
        return jsonify({"erro": "só o administrador pode alterar essa configuração"}), 403
    alvo = conn.execute("SELECT casa_id FROM usuarios WHERE id = ?", (item_id,)).fetchone()
    if not alvo or alvo["casa_id"] != minha_casa_id(conn):
        conn.close()
        return jsonify({"erro": "usuário não encontrado"}), 404
    # O administrador não pode se limitar: esta rota é a única forma de desfazer e
    # ela mesma é escrita, então ele ficaria trancado fora da própria casa.
    if item_id == uid():
        conn.close()
        return jsonify({"erro": "o administrador não pode limitar a própria conta"}), 403
    conn.execute("UPDATE usuarios SET somente_leitura = ? WHERE id = ?", (int(somente_leitura), item_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


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
    cor = data.get("cor") or None
    if not nome or tipo not in ("receita", "despesa"):
        return jsonify({"erro": "nome e tipo (receita/despesa) são obrigatórios"}), 400
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
    cor = data.get("cor") or None
    if not nome:
        return jsonify({"erro": "nome é obrigatório"}), 400
    conn = get_db()
    if not pertence_a_minha_casa(conn, "categorias", item_id):
        conn.close()
        return jsonify({"erro": "categoria não encontrada"}), 404
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
    if not pertence_a_minha_casa(conn, "categorias", item_id):
        conn.close()
        return jsonify({"erro": "categoria não encontrada"}), 404
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
        return jsonify({"erro": "categoria e limite são obrigatórios"}), 400
    try:
        limite = float(limite)
    except (TypeError, ValueError):
        return jsonify({"erro": "limite inválido"}), 400
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
        return jsonify({"erro": "cartão não encontrado"}), 404
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
        return jsonify({"erro": "cartão não encontrado"}), 404
    data = request.get_json(force=True)
    descricao = (data.get("descricao") or "").strip()
    if not descricao or data.get("valor") in (None, ""):
        conn.close()
        return jsonify({"erro": "descrição e valor são obrigatórios"}), 400
    try:
        valor = float(data.get("valor"))
    except (TypeError, ValueError):
        conn.close()
        return jsonify({"erro": "valor inválido"}), 400
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
        return jsonify({"erro": "lançamento não encontrado"}), 404
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
    rows = conn.execute(
        "SELECT * FROM metas WHERE usuario_id = ? ORDER BY criado_em", (uid(),)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/metas", methods=["POST"])
def criar_meta():
    data = request.get_json(force=True)
    nome = (data.get("nome") or "").strip()
    valor_alvo = data.get("valor_alvo")
    prazo = data.get("prazo") or None
    if not nome or not valor_alvo:
        return jsonify({"erro": "nome e valor alvo são obrigatórios"}), 400
    try:
        valor_alvo = float(valor_alvo)
    except (TypeError, ValueError):
        return jsonify({"erro": "valor alvo inválido"}), 400
    conn = get_db()
    conn.execute(
        "INSERT INTO metas (nome, valor_alvo, valor_atual, prazo, criado_em, usuario_id) "
        "VALUES (?, ?, 0, ?, ?, ?)",
        (nome, valor_alvo, prazo, datetime.now().isoformat(), uid()),
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
        return jsonify({"erro": "meta não encontrada"}), 404
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
        return jsonify({"erro": "meta não encontrada"}), 404
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
        return jsonify({"erro": "só o administrador pode alterar essa configuração"}), 403
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
        return jsonify({"erro": "nome e valor da parcela são obrigatórios"}), 400
    try:
        valor_parcela = float(valor_parcela)
    except (TypeError, ValueError):
        return jsonify({"erro": "valor da parcela inválido"}), 400
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
        return jsonify({"erro": "consignado não encontrado"}), 404
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
        return jsonify({"erro": "consignado não encontrado"}), 404
    conn.execute("DELETE FROM consignados WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


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
        return jsonify({"erro": "nenhum arquivo enviado"}), 400
    arquivo = request.files["arquivo"]
    if arquivo.filename == "":
        return jsonify({"erro": "arquivo sem nome"}), 400
    if not arquivo.filename.lower().endswith(".pdf"):
        return jsonify({"erro": "envie um arquivo PDF"}), 400

    nome_seguro = secure_filename(arquivo.filename)
    nome_final = f"{uid()}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{nome_seguro}"
    os.makedirs(HOLERITES_DIR, exist_ok=True)
    caminho = os.path.join(HOLERITES_DIR, nome_final)
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
        return jsonify({"erro": "holerite não encontrado"}), 404
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
        return jsonify({"erro": "holerite não encontrado"}), 404
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
        return jsonify({"erro": "nenhum arquivo enviado"}), 400
    arquivo = request.files["arquivo"]
    if arquivo.filename == "":
        return jsonify({"erro": "arquivo sem nome"}), 400

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
        return jsonify({"erro": "não foi possível ler o arquivo — envie um PDF ou uma foto (JPG/PNG)"}), 400

    chave_acesso_qr = None
    try:
        for resultado in zbar_decode(img):
            m = re.search(r"p=(\d{44})\|", resultado.data.decode("utf-8", "ignore"))
            if m:
                chave_acesso_qr = m.group(1)
                break
    except Exception:
        pass

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
        return jsonify({"erro": "holerite não encontrado"}), 404
    return send_from_directory(HOLERITES_DIR, row["arquivo"])


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

def _semear_demo():
    """Preenche o banco de demonstração com uma vida financeira fictícia:
    salário, dois aluguéis recebidos, contas de casa, cartões e parcelamentos."""
    if os.path.exists(DEMO_DB_PATH):
        os.remove(DEMO_DB_PATH)
    init_db(DEMO_DB_PATH, criar_usuario_inicial=False)

    conn = sqlite3.connect(DEMO_DB_PATH)
    conn.row_factory = sqlite3.Row
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
    if backup_bloqueado_multi_casa():
        return jsonify({"erro": MENSAGEM_BACKUP_BLOQUEADO}), 403
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
    if backup_bloqueado_multi_casa():
        return jsonify({"erro": MENSAGEM_BACKUP_BLOQUEADO}), 403
    caminho = caminho_backup_valido(nome_arquivo)
    if not caminho:
        return jsonify({"erro": "backup não encontrado"}), 404
    return send_from_directory(BACKUPS_DIR, os.path.basename(caminho), as_attachment=True)


@app.route("/api/backups/<path:nome_arquivo>", methods=["DELETE"])
def excluir_backup_salvo(nome_arquivo):
    if backup_bloqueado_multi_casa():
        return jsonify({"erro": MENSAGEM_BACKUP_BLOQUEADO}), 403
    caminho = caminho_backup_valido(nome_arquivo)
    if not caminho:
        return jsonify({"erro": "backup não encontrado"}), 404
    os.remove(caminho)
    return jsonify({"ok": True})


@app.route("/api/backups/<path:nome_arquivo>/restaurar", methods=["POST"])
def restaurar_backup_salvo(nome_arquivo):
    if em_demo():
        return jsonify({"erro": "desligue o modo demonstração antes de restaurar"}), 400
    if backup_bloqueado_multi_casa():
        return jsonify({"erro": MENSAGEM_BACKUP_BLOQUEADO}), 403
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
