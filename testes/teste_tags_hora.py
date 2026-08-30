#!/usr/bin/env python3
"""Confere as etiquetas e o horário dos lançamentos.

As duas coisas foram feitas para NÃO mexer no que já existia: a etiqueta vive
numa tabela de ligação e o horário numa coluna nova. O que este teste protege é
justamente isso — que lançamento antigo continue igual, que a etiqueta não
atravesse a fronteira da casa, e que um horário inventado seja recusado em vez
de gravado em silêncio.

Como no teste de segurança, cada recusa vem acompanhada do caso legítimo: um
teste que só confere recusa passaria com o app inteiro quebrado.

    docker exec <container> python /app/teste_tags_hora.py
"""

import os
import sys
import uuid

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


def criar_lancamento(s, **campos):
    corpo = {"tipo": "despesa", "descricao": "Teste", "valor": 10.0}
    corpo.update(campos)
    r = s.post(f"{BASE}/api/lancamentos", json=corpo, timeout=30)
    return r


# ---------------- Etiquetas ----------------

def teste_tags(s):
    print("\nEtiquetas")
    nome = f"Viagem {uuid.uuid4().hex[:6]}"

    r = s.post(f"{BASE}/api/tags", json={"nome": nome, "cor": "#00a76f"}, timeout=30)
    checa("criar etiqueta", r.status_code == 201, f"HTTP {r.status_code}")
    tag_id = r.json().get("id")

    # Reaproveitar em vez de recusar é o comportamento pedido: quem digita o
    # mesmo nome quer a mesma etiqueta, não um erro.
    r = s.post(f"{BASE}/api/tags", json={"nome": nome.upper()}, timeout=30)
    checa("nome repetido reaproveita a etiqueta (sem duplicar)",
          r.status_code == 200 and r.json().get("id") == tag_id,
          f"HTTP {r.status_code} id={r.json().get('id')}")

    r = s.get(f"{BASE}/api/tags", timeout=30)
    quantas = [t for t in r.json() if t["id"] == tag_id]
    checa("a etiqueta aparece uma vez só na lista", len(quantas) == 1, f"{len(quantas)} vez(es)")

    r = s.put(f"{BASE}/api/tags/{tag_id}", json={"nome": nome + " editada", "cor": "#ff5630"},
              timeout=30)
    checa("editar etiqueta", r.status_code == 200, f"HTTP {r.status_code}")

    # Cor não é uma cor: precisa ser descartada, não gravada — ela vai parar
    # dentro de um atributo style na tela.
    r = s.put(f"{BASE}/api/tags/{tag_id}", json={"nome": nome + " editada",
                                                 "cor": '#fff" onload="x'}, timeout=30)
    checa("a edição com cor inválida ainda é aceita", r.status_code == 200,
          f"HTTP {r.status_code}")
    cor = next((t["cor"] for t in s.get(f"{BASE}/api/tags", timeout=30).json()
                if t["id"] == tag_id), "?")
    checa("cor inválida não é gravada", cor is None, f"cor={cor!r}")

    # Etiqueta que não existe (ou é de outra casa) não pode ser marcada.
    r = criar_lancamento(s, descricao="Com etiqueta", tags=[tag_id, 999999])
    checa("criar lançamento com etiqueta", r.status_code == 201, f"HTTP {r.status_code}")
    lanc_id = r.json().get("id")

    mapa = s.get(f"{BASE}/api/lancamentos/tags", timeout=30).json()
    marcadas = [t["id"] for t in mapa.get(str(lanc_id), [])]
    checa("a etiqueta ficou marcada no lançamento", marcadas == [tag_id], f"{marcadas}")
    checa("etiqueta inexistente é ignorada, não gravada", 999999 not in marcadas)

    # Várias etiquetas no mesmo lançamento.
    r = s.post(f"{BASE}/api/tags", json={"nome": f"Outra {uuid.uuid4().hex[:6]}"}, timeout=30)
    tag2 = r.json().get("id")
    r = s.put(f"{BASE}/api/lancamentos/{lanc_id}/tags", json={"tags": [tag_id, tag2]}, timeout=30)
    checa("marcar duas etiquetas", r.status_code == 200, f"HTTP {r.status_code}")
    mapa = s.get(f"{BASE}/api/lancamentos/tags", timeout=30).json()
    checa("as duas aparecem no mapa", len(mapa.get(str(lanc_id), [])) == 2,
          f"{len(mapa.get(str(lanc_id), []))}")

    # Filtro pela API.
    mes = s.get(f"{BASE}/api/lancamentos", timeout=30).json()
    r = s.get(f"{BASE}/api/lancamentos", params={"tag_id": tag2}, timeout=30)
    filtrados = r.json()
    checa("filtrar por etiqueta devolve menos que a lista inteira",
          len(filtrados) < len(mes) or len(mes) == len(filtrados) == 1,
          f"{len(filtrados)} de {len(mes)}")
    checa("o lançamento etiquetado está no filtro",
          any(l["id"] == lanc_id for l in filtrados))

    # Apagar a etiqueta tira a ligação junto.
    r = s.delete(f"{BASE}/api/tags/{tag2}", timeout=30)
    checa("excluir etiqueta", r.status_code == 200, f"HTTP {r.status_code}")
    mapa = s.get(f"{BASE}/api/lancamentos/tags", timeout=30).json()
    restantes = [t["id"] for t in mapa.get(str(lanc_id), [])]
    checa("a ligação da etiqueta apagada sai junto", restantes == [tag_id], f"{restantes}")

    # Apagar o lançamento tira a ligação também.
    s.delete(f"{BASE}/api/lancamentos/{lanc_id}", timeout=30)
    mapa = s.get(f"{BASE}/api/lancamentos/tags", timeout=30).json()
    checa("apagar o lançamento leva as ligações", str(lanc_id) not in mapa)

    s.delete(f"{BASE}/api/tags/{tag_id}", timeout=30)


# ---------------- Horário ----------------

HORAS_RECUSADAS = ["25:00", "12:60", "abc", "12", "1:2", "12:00:00:00", "-1:00"]


def teste_hora(s):
    print("\nHorário")

    r = criar_lancamento(s, descricao="Com hora", hora="14:35")
    checa("criar lançamento com horário", r.status_code == 201, f"HTTP {r.status_code}")
    com_hora = r.json().get("id")

    r = criar_lancamento(s, descricao="Sem hora")
    sem_hora = r.json().get("id")

    lista = {l["id"]: l for l in s.get(f"{BASE}/api/lancamentos", timeout=30).json()}
    checa("o horário volta na listagem", lista.get(com_hora, {}).get("hora") == "14:35",
          f"{lista.get(com_hora, {}).get('hora')!r}")
    # Isto é o que protege o histórico: sem horário é NULL, e não "00:00".
    checa("lançamento sem horário fica nulo, não 00:00",
          lista.get(sem_hora, {}).get("hora") is None,
          f"{lista.get(sem_hora, {}).get('hora')!r}")

    for hora in HORAS_RECUSADAS:
        r = criar_lancamento(s, descricao="Hora ruim", hora=hora)
        checa(f"horário {hora!r} é recusado", r.status_code == 400, f"HTTP {r.status_code}")

    # O caso legítimo do formato longo: <input type="time"> com `step` manda
    # HH:MM:SS, e recusá-lo quebraria o próprio formulário.
    r = criar_lancamento(s, descricao="Hora com segundos", hora="09:30:00")
    checa("horário 'HH:MM:SS' é aceito e guardado como HH:MM", r.status_code == 201,
          f"HTTP {r.status_code}")
    com_segundos = r.json().get("id")
    lista = {l["id"]: l for l in s.get(f"{BASE}/api/lancamentos", timeout=30).json()}
    checa("os segundos são descartados", lista.get(com_segundos, {}).get("hora") == "09:30",
          f"{lista.get(com_segundos, {}).get('hora')!r}")
    s.delete(f"{BASE}/api/lancamentos/{com_segundos}", timeout=30)

    # Editar: define, mantém e apaga.
    base = {"descricao": "Com hora", "valor": 10.0, "categoria": "", "vencimento": "",
            "conta_id": None, "observacao": "", "recorrente": False}
    r = s.put(f"{BASE}/api/lancamentos/{com_hora}", json={**base, "hora": "07:05"}, timeout=30)
    checa("editar o horário", r.status_code == 200, f"HTTP {r.status_code}")
    lista = {l["id"]: l for l in s.get(f"{BASE}/api/lancamentos", timeout=30).json()}
    checa("o horário editado foi gravado", lista.get(com_hora, {}).get("hora") == "07:05",
          f"{lista.get(com_hora, {}).get('hora')!r}")

    # Cliente que não conhece o campo (o app Android) não pode apagar o horário.
    r = s.put(f"{BASE}/api/lancamentos/{com_hora}", json=base, timeout=30)
    checa("salvar sem mandar 'hora' é aceito", r.status_code == 200,
          f"HTTP {r.status_code}")
    lista = {l["id"]: l for l in s.get(f"{BASE}/api/lancamentos", timeout=30).json()}
    checa("salvar sem mandar 'hora' preserva o horário",
          lista.get(com_hora, {}).get("hora") == "07:05",
          f"{lista.get(com_hora, {}).get('hora')!r}")

    # Mas mandar vazio apaga de propósito.
    s.put(f"{BASE}/api/lancamentos/{com_hora}", json={**base, "hora": ""}, timeout=30)
    lista = {l["id"]: l for l in s.get(f"{BASE}/api/lancamentos", timeout=30).json()}
    checa("mandar horário vazio apaga", lista.get(com_hora, {}).get("hora") is None,
          f"{lista.get(com_hora, {}).get('hora')!r}")

    for i in (com_hora, sem_hora):
        s.delete(f"{BASE}/api/lancamentos/{i}", timeout=30)


def main():
    print(f"alvo: {BASE}")
    s = entrar()
    teste_tags(s)
    teste_hora(s)
    print()
    if falhas:
        print(f"FALHOU ({len(falhas)}): " + ", ".join(falhas))
        return 1
    print("Todos os testes passaram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
