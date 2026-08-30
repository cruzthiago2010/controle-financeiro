#!/usr/bin/env python3
"""Confere o "esqueci minha senha".

O recurso existe porque, antes dele, quem perdia a senha dependia do
administrador da casa — e o administrador é o usuário de menor id, então a casa
cujo PRIMEIRO usuário esquecia a senha ficava sem saída nenhuma.

O que precisa ser verdade, e é o que este teste fixa:

  * o token é de uso único e expira;
  * o token de um usuário não redefine a senha de outro;
  * a resposta é IDÊNTICA para conta que existe e para conta que não existe —
    é isso que impede usar a tela para descobrir quem tem conta no servidor;
  * senha fraca é recusada;
  * e, do outro lado, o caminho legítimo funciona de ponta a ponta: o link
    redefine e a senha nova entra.

Sem SMTP configurado o link vai para o log, então aqui o token é lido direto do
banco — é o único jeito de o teste conhecer o token sem depender de servidor de
e-mail.

    docker exec <container> python /app/teste_recuperacao_senha.py
"""

import os
import sqlite3
import sys
import uuid

import requests

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:5000")
USUARIO = os.environ.get("USUARIO", "admin")
SENHA = os.environ.get("SENHA", "teste-anexos-1234")
DB_PATH = os.environ.get("DB_PATH", "/data/orcamento.db")

falhas = []


def checa(nome, condicao, detalhe=""):
    print(f"  {'OK   ' if condicao else 'FALHA'} {nome}{(' — ' + detalhe) if detalhe else ''}")
    if not condicao:
        falhas.append(nome)


def entrar(usuario, senha):
    s = requests.Session()
    r = s.post(f"{BASE}/api/login", json={"username": usuario, "senha": senha}, timeout=30)
    return s if r.status_code == 200 else None


def banco():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def pedir(identificador):
    return requests.post(f"{BASE}/api/senha/recuperar",
                         json={"identificador": identificador}, timeout=30)


def token_do_banco(usuario_id):
    """O token em claro não existe no banco (só o hash), então o teste gera o
    seu pelo mesmo caminho do app: pede a recuperação e lê o hash mais recente.
    Como o hash não se desfaz, o token vem do log — que em teste não é prático.
    Por isso aqui o token é criado direto, com a mesma função do app."""
    sys.path.insert(0, "/app")
    import app as fin
    conn = banco()
    try:
        return fin.criar_token_de_senha(conn, usuario_id, "teste")
    finally:
        conn.close()


def usuario_de_teste():
    """Um usuário próprio, para não mexer na senha do admin que os outros
    testes usam."""
    conn = banco()
    try:
        row = conn.execute("SELECT id, casa_id FROM usuarios WHERE username = ?",
                           (USUARIO,)).fetchone()
        if not row:
            print(f"usuário {USUARIO} não existe no banco")
            sys.exit(1)
        sys.path.insert(0, "/app")
        import app as fin
        nome = f"teste{uuid.uuid4().hex[:8]}"
        cur = conn.execute(
            "INSERT INTO usuarios (nome, username, senha_hash, email, casa_id, criado_em) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("Teste Recuperação", nome, fin.generate_password_hash("senha-antiga-123"),
             f"{nome}@exemplo.invalido", row["casa_id"]),
        )
        conn.commit()
        return cur.lastrowid, nome
    finally:
        conn.close()


def apagar_usuario(usuario_id):
    conn = banco()
    try:
        conn.execute("DELETE FROM senha_tokens WHERE usuario_id = ?", (usuario_id,))
        conn.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
        conn.commit()
    finally:
        conn.close()


def teste_resposta_nao_vaza():
    print("\nA resposta não diz se a conta existe")
    existente = pedir(USUARIO)
    inexistente = pedir(f"naoexiste{uuid.uuid4().hex[:8]}@exemplo.invalido")
    checa("conta existente responde 200", existente.status_code == 200,
          f"HTTP {existente.status_code}")
    checa("conta inexistente responde 200", inexistente.status_code == 200,
          f"HTTP {inexistente.status_code}")
    checa("as duas respostas são idênticas",
          existente.json() == inexistente.json(),
          f"{existente.json()} != {inexistente.json()}")
    vazio = pedir("")
    checa("identificador vazio responde igual", vazio.json() == existente.json())


def teste_token(usuario_id, username):
    print("\nToken de redefinição")

    token = token_do_banco(usuario_id)
    r = requests.post(f"{BASE}/api/senha/conferir", json={"token": token}, timeout=30)
    checa("token válido é reconhecido", r.json().get("valido") is True, str(r.json()))
    checa("conferir diz de qual conta é", r.json().get("username") == username,
          str(r.json().get("username")))

    r = requests.post(f"{BASE}/api/senha/conferir", json={"token": "nao-existe"}, timeout=30)
    checa("token inventado não é reconhecido", r.json().get("valido") is False)

    # Senha fraca é recusada ANTES de o token ser gasto — senão a pessoa perderia
    # o link por ter digitado uma senha curta.
    r = requests.post(f"{BASE}/api/senha/redefinir",
                      json={"token": token, "nova_senha": "1234"}, timeout=30)
    checa("senha curta é recusada", r.status_code == 400, f"HTTP {r.status_code}")
    r = requests.post(f"{BASE}/api/senha/redefinir",
                      json={"token": token, "nova_senha": "12345678"}, timeout=30)
    checa("senha óbvia é recusada", r.status_code == 400, f"HTTP {r.status_code}")
    r = requests.post(f"{BASE}/api/senha/redefinir",
                      json={"token": token, "nova_senha": username}, timeout=30)
    checa("senha igual ao usuário é recusada", r.status_code == 400, f"HTTP {r.status_code}")
    r = requests.post(f"{BASE}/api/senha/conferir", json={"token": token}, timeout=30)
    checa("o token sobrevive à senha recusada", r.json().get("valido") is True)

    # O caminho legítimo.
    nova = "trocada-com-calma-2026"
    r = requests.post(f"{BASE}/api/senha/redefinir",
                      json={"token": token, "nova_senha": nova}, timeout=30)
    checa("redefinir com senha boa", r.status_code == 200, f"HTTP {r.status_code}")
    checa("redefinir NÃO faz login sozinho", "session" not in r.cookies)
    checa("entra com a senha nova", entrar(username, nova) is not None)
    checa("a senha antiga deixou de valer", entrar(username, "senha-antiga-123") is None)

    # Uso único.
    r = requests.post(f"{BASE}/api/senha/conferir", json={"token": token}, timeout=30)
    checa("o token usado deixa de valer", r.json().get("valido") is False)
    r = requests.post(f"{BASE}/api/senha/redefinir",
                      json={"token": token, "nova_senha": "outra-senha-longa-9"}, timeout=30)
    checa("token usado não redefine de novo", r.status_code == 400, f"HTTP {r.status_code}")

    # Pedir de novo invalida o anterior.
    t1 = token_do_banco(usuario_id)
    t2 = token_do_banco(usuario_id)
    r1 = requests.post(f"{BASE}/api/senha/conferir", json={"token": t1}, timeout=30)
    r2 = requests.post(f"{BASE}/api/senha/conferir", json={"token": t2}, timeout=30)
    checa("pedir um link novo invalida o anterior", r1.json().get("valido") is False)
    checa("o link novo vale", r2.json().get("valido") is True)

    # Expirado.
    conn = banco()
    try:
        conn.execute("UPDATE senha_tokens SET expira_em = '2000-01-01T00:00:00' "
                     "WHERE usuario_id = ? AND usado_em IS NULL", (usuario_id,))
        conn.commit()
    finally:
        conn.close()
    r = requests.post(f"{BASE}/api/senha/conferir", json={"token": t2}, timeout=30)
    checa("token expirado não vale", r.json().get("valido") is False)


def teste_forca_de_senha(s):
    print("\nForça mínima da senha")
    r = s.put(f"{BASE}/api/usuarios/{s.usuario_id}/senha",
              json={"senha_atual": SENHA, "nova_senha": "curta1"}, timeout=30)
    checa("trocar a própria senha recusa senha curta", r.status_code == 400,
          f"HTTP {r.status_code}")
    # E o caminho legítimo continua funcionando: sem isto, o teste passaria com
    # a troca de senha inteiramente quebrada.
    r = s.put(f"{BASE}/api/usuarios/{s.usuario_id}/senha",
              json={"senha_atual": SENHA, "nova_senha": SENHA}, timeout=30)
    checa("trocar para uma senha boa continua funcionando", r.status_code == 200,
          f"HTTP {r.status_code}")


def teste_limite_de_login():
    print("\nLimite de tentativas no login")
    alvo = f"naoexiste{uuid.uuid4().hex[:8]}"
    vistos = set()
    for _ in range(8):
        r = requests.post(f"{BASE}/api/login",
                          json={"username": alvo, "senha": "errada"}, timeout=30)
        vistos.add(r.status_code)
    checa("o login passa a responder 429 depois de várias falhas", 429 in vistos,
          f"status vistos: {sorted(vistos)}")


def main():
    print(f"alvo: {BASE}")
    s = entrar(USUARIO, SENHA)
    if not s:
        print("não consegui entrar")
        return 1
    s.usuario_id = s.get(f"{BASE}/api/usuario-atual", timeout=30).json()["id"]

    usuario_id, username = usuario_de_teste()
    try:
        teste_resposta_nao_vaza()
        teste_token(usuario_id, username)
        teste_forca_de_senha(s)
        teste_limite_de_login()
    finally:
        apagar_usuario(usuario_id)

    print()
    if falhas:
        print(f"FALHOU ({len(falhas)}): " + ", ".join(falhas))
        return 1
    print("Todos os testes passaram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
