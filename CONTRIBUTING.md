<p align="center">
  <b>Português</b> · <a href="CONTRIBUTING.en.md">English</a>
</p>

# Como contribuir

O FinanCerto é um app self-hosted de controle financeiro, feito para qualquer
pessoa rodar no próprio servidor. Toda contribuição é bem-vinda — inclusive
"instalei e não entendi essa tela", que costuma valer mais que código.

## Antes de escrever código

Abra uma [issue](https://github.com/financerto/controle-financeiro/issues)
descrevendo o que pretende fazer. Serve para evitar dois trabalhos iguais e para
alinhar se aquilo cabe no projeto. Para correção pequena e óbvia (typo, link
quebrado, erro de conta), pode mandar o pull request direto.

**Falha de segurança não vai em issue.** Veja [SECURITY.md](SECURITY.md).

## Rodando o projeto

```bash
git clone https://github.com/financerto/controle-financeiro.git
cd controle-financeiro
cp .env.example .env      # preencha ADMIN_PASSWORD e SECRET_KEY
docker compose up -d --build
```

O app sobe em `http://localhost:8420`. A pasta `data/` guarda o banco SQLite, os
anexos e a chave de sessão — nada dali é versionado.

Para mexer no Python sem rebuildar a imagem a cada troca:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python app.py            # precisa de tesseract-ocr e libzbar0 no sistema
```

## Como o código é organizado

| Caminho | O que é |
|---|---|
| `app.py` | backend Flask inteiro: API REST, autenticação e serviço dos arquivos |
| `static/index.html` | o app em si — uma página só, sem framework nem build |
| `static/login.html`, `static/registro.html` | telas de entrada e de cadastro público |
| `migrations/*.sql` | mudanças de schema, aplicadas em ordem no start |
| `android/` | app Android (WebView com digital, notificação e widget) |
| `testes/` | teste de fumaça dos anexos e das tentativas de fuga de ticker e caminho |
| `docs/screenshots/` | capturas usadas no README |

Não há etapa de build no frontend: é HTML, CSS e JavaScript servidos direto.
Manter assim é proposital — quem clona precisa conseguir subir com um
`docker compose up` e nada mais.

## Convenções

- **O código é em português.** Nome de variável, de função, de rota e comentário
  seguem em português, como o resto do arquivo. Só a **interface** e a
  **documentação pública** são traduzidas (pt-BR e inglês).
- **Mensagem de commit em português, no imperativo**, dizendo o efeito para quem
  usa: `Fatura de cartão: pagamento e encargos vindos do banco`. Não use prefixo
  de convenção (`feat:`, `fix:`).
- **Toda mudança de schema entra como migration nova** em `migrations/`, com
  número em sequência. Nunca edite uma migration já publicada — alguém já rodou
  aquilo no banco de verdade.
- **Nada pode depender do servidor de ninguém.** Sem domínio fixo no código, sem
  IP, sem token embutido: qualquer pessoa tem que conseguir hospedar. O que
  variar por instalação vai para o `.env`, com valor padrão sensato e uma linha
  explicando no `.env.example`.
- **O que aparece na tela tem que ser verdade.** Se o dado que existe é "data do
  último backup", o rótulo diz isso — não invente um indicador indireto que
  pareça mais útil.

## Pull request

1. Faça um branch a partir de `main`.
2. Confira que o container sobe do zero: `docker compose up --build` numa pasta
   `data/` vazia, para pegar erro de migration.
3. Se mexeu na tela, teste também no celular — o app é usado sobretudo no
   telefone e instalado como PWA.
4. Descreva **o que muda para quem usa**, não só o que mudou no arquivo. Se for
   visual, anexe uma captura antes/depois.
5. O CI roda `ruff`, compila o Python, monta a imagem Docker e roda o teste
   dos anexos. Precisa passar.

Para rodar o teste dos anexos na sua máquina, com o container já de pé:

```bash
docker compose cp testes/teste_anexos.py controle-financeiro:/app/teste_anexos.py
docker compose exec -e SENHA=<a sua senha> controle-financeiro python /app/teste_anexos.py
```

Ele monta um holerite e um cupom fiscal na hora e confere os valores lidos —
é o teste que pega uma atualização de Pillow ou pypdf que passa no `import` e
só quebra no arquivo de verdade.

Pull request grande demora a ser revisado. Se a mudança é grande, quebre em
partes que façam sentido sozinhas.

## Uso de IA

Parte deste código foi escrita com ajuda de IA, e contribuição feita com IA é
bem-vinda. O critério é o mesmo de sempre: quem assina o pull request responde
pela qualidade dele. Mande o que você entende e consegue defender — não o que
saiu pronto e você não leu.

## Código de conduta

Vale o [Código de Conduta](CODE_OF_CONDUCT.md) em todos os espaços do projeto.

## Licença

Ao contribuir, você concorda que a sua contribuição seja distribuída sob a
[AGPL-3.0](LICENSE), a mesma licença do projeto.
