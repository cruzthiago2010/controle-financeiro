#!/usr/bin/env python3
"""Confere os dois caminhos do app que abrem arquivo vindo de fora.

São os mais frágeis a uma atualização de dependência, porque dependem de
bibliotecas que leem formato binário de terceiro: o pypdf lê o PDF do holerite,
e o Pillow (junto com o PyMuPDF e o tesseract) processa a foto do cupom fiscal.
Uma versão nova costuma passar no `import` e falhar só no arquivo de verdade —
por isso o teste monta um holerite e um cupom na hora e confere os valores
lidos, em vez de só checar se a rota respondeu.

Roda de dentro do container, onde as bibliotecas e o tesseract já existem:

    docker exec <container> python /app/testes/teste_anexos.py

O endereço padrão é o do próprio container; use BASE_URL para apontar noutro
lugar, e USUARIO/SENHA para as credenciais do login.
"""

import io
import json
import os
import random
import sys

import fitz
import pypdf
import requests
import PIL
from PIL import Image, ImageFilter, ImageOps

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:5000")
USUARIO = os.environ.get("USUARIO", "admin")
SENHA = os.environ.get("SENHA", "teste-anexos-1234")

falhas = []


def checa(nome, condicao, detalhe=""):
    print(f"  {'OK   ' if condicao else 'FALHA'} {nome}{(' — ' + detalhe) if detalhe else ''}")
    if not condicao:
        falhas.append(nome)


# ---------------- Arquivos de mentira, montados na hora ----------------

def pdf_holerite():
    """Um contracheque com os campos que `extrair_dados_holerite` procura."""
    doc = fitz.open()
    pagina = doc.new_page()
    y = 60
    for linha in [
        "EMPRESA EXEMPLO LTDA",
        "Folha: Mensal",
        "Referencia: ago/2026",
        "Recebido em: 05/09/2026",
        "Total de Proventos: 5.432,10",
        "Total de Descontos: 1.234,56",
        "Total Liquido a Receber: 4.197,54",
        "Adiantamento Quinzenal: 1.500,00",
    ]:
        pagina.insert_text((50, y), linha, fontsize=13)
        y += 26
    return doc.tobytes()


def pdf_cupom():
    """Um cupom fiscal no tamanho de bobina térmica, com loja, CNPJ e total."""
    doc = fitz.open()
    pagina = doc.new_page(width=300, height=420)
    y = 40
    for linha in [
        "MERCADO SAO JOAO LTDA",
        "12.345.678/0001-90",
        "24/08/2026",
        "ARROZ 5KG",
        "1 UN X 28,90    28,90",
        "VALOR TOTAL R$ 28,90",
    ]:
        pagina.insert_text((20, y), linha, fontsize=13)
        y += 30
    return doc.tobytes()


def foto_grande():
    """Ruído colorido do tamanho de uma foto de celular, só para pesar alguns MB.

    O PNG comprime demais uma imagem lisa; o ruído em blocos de 4px garante um
    arquivo de verdade sem levar minutos para gerar.
    """
    img = Image.new("RGB", (2400, 3200))
    px = img.load()
    for x in range(0, 2400, 4):
        for y in range(0, 3200, 4):
            cor = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
            for dx in range(4):
                for dy in range(4):
                    px[x + dx, y + dy] = cor
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------- As bibliotecas, isoladas ----------------

def teste_pypdf():
    print("\n[1] pypdf — extract_text")
    leitor = pypdf.PdfReader(io.BytesIO(pdf_holerite()))
    texto = "\n".join((pagina.extract_text() or "") for pagina in leitor.pages)
    checa("extract_text devolve o conteúdo do PDF", "Total Liquido a Receber: 4.197,54" in texto)


def teste_pillow():
    print("\n[2] Pillow — frombytes / open / grayscale / autocontrast / point / SHARPEN")
    pix = fitz.open(stream=pdf_cupom(), filetype="pdf")[0].get_pixmap(
        dpi=300, colorspace=fitz.csRGB, alpha=False
    )
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    checa("Image.frombytes a partir do pixmap do PyMuPDF", img.size == (pix.width, pix.height), str(img.size))

    # Mesma preparação que o app faz antes de mandar para o tesseract.
    preparada = ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=1)
    preparada = preparada.point(lambda p: 255 if p > 150 else 0).filter(ImageFilter.SHARPEN)
    checa("grayscale + autocontrast + point + SHARPEN", preparada.mode == "L")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    checa("Image.open num PNG", Image.open(io.BytesIO(buf.getvalue())).convert("RGB").size == img.size)
    return buf.getvalue()


# ---------------- As rotas ----------------

def teste_rotas(png_cupom):
    print("\n[3] Rotas do app")
    sessao = requests.Session()
    r = sessao.post(f"{BASE}/api/login", json={"username": USUARIO, "senha": SENHA}, timeout=30)
    checa("login", r.status_code == 200, f"HTTP {r.status_code}")
    if r.status_code != 200:
        return sessao

    r = sessao.post(
        f"{BASE}/api/holerites",
        files={"arquivo": ("holerite.pdf", pdf_holerite(), "application/pdf")},
        data={"lancar_receita": "false"},
        timeout=60,
    )
    checa("POST /api/holerites", r.status_code in (200, 201), f"HTTP {r.status_code}")
    if r.status_code in (200, 201):
        lido = json.dumps(r.json(), ensure_ascii=False)
        checa("  holerite: proventos 5.432,10", "5432.1" in lido)
        checa("  holerite: descontos 1.234,56", "1234.56" in lido)
        checa("  holerite: líquido 4.197,54", "4197.54" in lido)
        checa("  holerite: adiantamento 1.500,00", "1500.0" in lido)
        checa("  holerite: referência ago/2026", "2026-08" in lido)
        checa("  holerite: recebido em 05/09/2026", "2026-09-05" in lido)

    # Os dois ramos do código de leitura: o PDF passa pelo PyMuPDF antes do
    # Pillow, a foto vai direto para o Image.open.
    for nome, conteudo, tipo in [
        ("cupom.pdf", pdf_cupom(), "application/pdf"),
        ("cupom.png", png_cupom, "image/png"),
    ]:
        r = sessao.post(
            f"{BASE}/api/notas-fiscais/analisar",
            files={"arquivo": (nome, conteudo, tipo)},
            timeout=120,
        )
        checa(f"POST /api/notas-fiscais/analisar ({nome})", r.status_code == 200, f"HTTP {r.status_code}")
        if r.status_code == 200:
            d = r.json()
            print(f"         loja={d.get('loja')!r} valor={d.get('valor_total')!r} cnpj={d.get('cnpj')!r}")
            checa(f"  OCR leu algum texto ({nome})", bool((d.get("texto_bruto") or "").strip()))
            checa(f"  OCR achou o valor 28,90 ({nome})", d.get("valor_total") == 28.9, str(d.get("valor_total")))
            checa(f"  OCR achou o CNPJ ({nome})", d.get("cnpj") == "12.345.678/0001-90", str(d.get("cnpj")))
    return sessao


def teste_anexo_grande(sessao):
    # O Flask 3.1 passou a limitar o formulário em memória (MAX_FORM_MEMORY_SIZE,
    # 500 KB). O limite vale só para campo de texto, não para anexo — mas foto de
    # celular tem alguns MB, e é assim que a nota fiscal chega, então confirma-se.
    print("\n[4] Anexo grande")
    conteudo = foto_grande()
    r = sessao.post(
        f"{BASE}/api/notas-fiscais/analisar",
        files={"arquivo": ("foto.png", conteudo, "image/png")},
        timeout=180,
    )
    checa(f"POST anexo de {len(conteudo) / 1_000_000:.1f} MB", r.status_code == 200, f"HTTP {r.status_code}")


def main():
    ver_fitz = getattr(fitz, "__version__", "?")
    print(f"pypdf {pypdf.__version__} · Pillow {PIL.__version__} · PyMuPDF {ver_fitz}")
    print(f"alvo: {BASE}")

    teste_pypdf()
    png_cupom = teste_pillow()
    sessao = teste_rotas(png_cupom)
    teste_anexo_grande(sessao)

    print()
    if falhas:
        print(f"FALHOU ({len(falhas)}): " + ", ".join(falhas))
        return 1
    print("Todos os testes passaram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
