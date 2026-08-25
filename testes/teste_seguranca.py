#!/usr/bin/env python3
"""Confere que os valores que o cliente escolhe não escapam para onde não devem.

São dois riscos que o app carrega pela própria natureza: o ticker de um
investimento vira parte da URL de uma API externa, e o nome de um anexo vira
parte de um caminho no disco. Nos dois casos o valor é escolhido por quem usa,
e nos dois um caractere a mais muda o destino — outro endereço na internet, ou
outro arquivo no servidor.

Cada caso aqui é uma tentativa de fuga que precisa ser recusada, e junto vai o
caso legítimo correspondente, para o teste não passar só porque o app parou de
funcionar.

    docker exec <container> python /app/testes/teste_seguranca.py
"""

import io
import os
import sys

import requests

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:5000")
USUARIO = os.environ.get("USUARIO", "admin")
SENHA = os.environ.get("SENHA", "teste-anexos-1234")

falhas = []


def checa(nome, condicao, detalhe=""):
    print(f"  {'OK   ' if condicao else 'FALHA'} {nome}{(' — ' + detalhe) if detalhe else ''}")
    if not condicao:
        falhas.append(nome)


def entrar():
    s = requests.Session()
    r = s.post(f"{BASE}/api/login", json={"username": USUARIO, "senha": SENHA}, timeout=30)
    if r.status_code != 200:
        print(f"não consegui entrar: HTTP {r.status_code}")
        sys.exit(1)
    return s


# ---------------- Ticker que vira URL ----------------

TICKERS_RECUSADOS = [
    "../../etc/passwd",       # sobe de diretório no caminho da API
    "PETR4/../../v1/admin",   # mesma ideia, disfarçada de ticker válido
    "PETR4?token=roubado",    # inventa parâmetro na query da fonte
    "PETR4#frag",             # corta o resto do caminho
    "PETR4 e mais",           # espaço quebra a linha da requisição
    "PETR4\r\nX-Coisa: 1",    # injeção de cabeçalho
    "http://169.254.169.254", # endereço de metadados de nuvem
    "//evil.example.com",     # troca de host por caminho relativo de protocolo
]


def conta_de_teste(s):
    """O cadastro de investimento exige uma conta. Sem criar uma antes, todo
    POST volta 400 por causa dela, e o teste passaria sem chegar perto do
    ticker — que é justamente o que ele quer conferir."""
    r = s.post(f"{BASE}/api/contas", json={"nome": "Conta de teste", "saldo_inicial": 0}, timeout=30)
    if r.status_code in (200, 201):
        return r.json().get("id")
    r = s.get(f"{BASE}/api/contas", timeout=30)
    contas = r.json() if r.status_code == 200 else []
    return (contas[0].get("id") if contas else None)


def teste_ticker_na_url(s, conta_id):
    print("\n[1] Ticker que entra na URL de uma API externa")
    for ticker in TICKERS_RECUSADOS:
        r = s.post(f"{BASE}/api/investimentos", json={
            "nome": "Tentativa", "classe": "acao", "ticker": ticker,
            "quantidade": 1, "conta_id": conta_id,
        }, timeout=30)
        checa(f"cadastro recusa {ticker!r}", r.status_code == 400, f"HTTP {r.status_code}")

        r = s.get(f"{BASE}/api/investimentos/cotacao",
                  params={"classe": "acao", "ticker": ticker}, timeout=30)
        # A rota busca a cotação ao vivo: o certo é nem tentar, e devolver vazio.
        ok = r.status_code == 200 and r.json().get("preco") is None
        checa(f"  cotação não busca {ticker!r}", ok, f"HTTP {r.status_code} {r.text[:60]}")

    # E o caso legítimo continua aceito, senão o teste passaria com o app quebrado.
    r = s.post(f"{BASE}/api/investimentos", json={
        "nome": "Petrobras", "classe": "acao", "ticker": "PETR4",
        "quantidade": 10, "conta_id": conta_id,
    }, timeout=30)
    checa("ticker normal (PETR4) é aceito", r.status_code in (200, 201), f"HTTP {r.status_code}")
    r = s.post(f"{BASE}/api/investimentos", json={
        "nome": "Bitcoin", "classe": "cripto", "ticker": "bitcoin",
        "quantidade": 1, "conta_id": conta_id,
    }, timeout=30)
    checa("id de cripto (bitcoin) é aceito", r.status_code in (200, 201), f"HTTP {r.status_code}")
    r = s.post(f"{BASE}/api/investimentos", json={
        "nome": "Berkshire", "classe": "stock", "ticker": "BRK.B",
        "quantidade": 1, "conta_id": conta_id,
    }, timeout=30)
    checa("ticker com ponto (BRK.B) é aceito", r.status_code in (200, 201), f"HTTP {r.status_code}")


# ---------------- Nome de arquivo que vira caminho ----------------

def teste_caminho_de_arquivo(s):
    print("\n[2] Nome de arquivo que vira caminho no disco")

    # Um PNG mínimo de verdade, para o upload não ser recusado pelo formato.
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
        "00000049454e44ae426082"
    )

    r = s.post(f"{BASE}/api/lancamentos", json={
        "tipo": "despesa", "descricao": "Teste", "valor": 10, "data": "2026-08-24",
    }, timeout=30)
    if r.status_code not in (200, 201):
        checa("criar lançamento para o anexo", False, f"HTTP {r.status_code}")
        return
    lanc_id = r.json().get("id")

    for nome in ["../../../evil.png", "..\\..\\evil.png", "/etc/evil.png"]:
        r = s.post(f"{BASE}/api/lancamentos/{lanc_id}/comprovante",
                   files={"arquivo": (nome, io.BytesIO(png), "image/png")}, timeout=30)
        gravado = r.json().get("comprovante", "") if r.status_code in (200, 201) else ""
        # Aceitar é aceitável; o que não pode é o caminho gravado sair da pasta.
        ok = r.status_code >= 400 or (".." not in gravado and "/" not in gravado)
        checa(f"comprovante {nome!r} não escapa da pasta", ok, f"HTTP {r.status_code} → {gravado!r}")

    # Download de backup pedindo um arquivo de fora da pasta de backups.
    for pedido in ["../app.py", "..%2f..%2fapp.py", "....//app.py", "../../data/orcamento.db"]:
        r = s.get(f"{BASE}/api/backups/{pedido}", timeout=30)
        checa(f"backup {pedido!r} não é entregue", r.status_code == 404, f"HTTP {r.status_code}")


def main():
    print(f"alvo: {BASE}")
    s = entrar()
    conta_id = conta_de_teste(s)
    if not conta_id:
        print("não consegui criar a conta de teste")
        return 1
    teste_ticker_na_url(s, conta_id)
    teste_caminho_de_arquivo(s)
    print()
    if falhas:
        print(f"FALHOU ({len(falhas)}): " + ", ".join(falhas))
        return 1
    print("Todos os testes passaram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
