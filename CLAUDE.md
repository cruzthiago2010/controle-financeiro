# FinanCerto

Controle financeiro pessoal/familiar self-hosted. Flask + SQLite, servido por
Docker num servidor Umbrel doméstico. Repositório público:
`github.com/cruzthiago2010/controle-financeiro`.

## Estado atual (19/08/2026)

Versão no ar: **FinanCerto 3.1** (web em produção, APK publicado na release v3.1).

Feito nesta rodada, em ordem:

- **Identidade FinanCerto** — logo em SVG (escala e serve aos dois temas), 11
  ícones de PWA gerados dele, telas de entrada e de criar casa refeitas.
- **Painel redesenhado** — paleta escura neutra, indicadores com variação sobre o
  mês anterior, gastos por categoria em lista ranqueada (a rosca ficava ilegível
  com ~20 categorias), gráficos com linha-guia no hover.
- **Barra superior** — busca em todos os meses (`/api/busca`), sino de contas
  vencidas com marcar-como-lidas, menu do usuário.
- **Lançar mais rápido** — sugestão de categoria pelo histórico (`/api/sugestoes`)
  e teclado numérico próprio com as quatro operações.
- **Aba Notificações** — o que avisar, botão de teste, e comportamento distinto
  dentro do app Android (notificação nativa via `PonteApp`).
- **App Android sem servidor fixo** — endereço perguntado na instalação, testado
  antes de salvar; código foi para `android/`.
- **Repositório limpo** — domínio pessoal e analytics saíram do código versionado.
- **Conciliação** — extratos de Itaú, C6 e Nubank importados até 18/08; as três
  contas batem com o extrato real.

### Decisões que valem lembrar

- **Sem framework no frontend.** Tudo em `static/index.html`. Cresceu bastante,
  mas trocar isso agora é reescrita — só considerar se virar impeditivo.
- **Notificação no navegador só com o app aberto.** Push em segundo plano exigiria
  um serviço externo, o que contraria a ideia de self-hosted. Quem quer aviso com
  o celular guardado usa o app Android, e isso está dito na própria aba.
- **Logo dos bancos vem do ícone oficial do app de cada um**, guardado localmente
  em `static/logos/`. Favicon e Wikimedia foram testados antes e não serviram
  (resolução baixa ou logotipo por extenso, que não cabe em ícone redondo).
- **"Marcar como lida" no sino não marca como paga.** São coisas diferentes e o
  toast diz isso — mexer nisso é mexer em dado financeiro.

### Próximos passos

1. **Auditoria de segurança pedida, ainda incompleta.** Já verificado: nenhuma
   rota com IDOR e nenhuma chave em código. Faltam três frentes — isolamento
   query a query, tratamento de entrada num ponto específico e se as restrições
   de somente-leitura/administrador valem no servidor ou só na tela. As pistas em
   aberto estão em `NOTAS-SEGURANCA.md`, que **não é versionado** de propósito:
   descrever suspeita não confirmada num repositório público é entregar o caminho.
2. **Foto de perfil dá 404** (`u1_20260810050841.jpeg`) — o arquivo sumiu do
   servidor. Ou subir de novo, ou limpar a referência no banco.
3. **APK nunca foi testado em aparelho.** Não há emulador no servidor; a validação
   foi estática (telas registradas, textos presentes, sem domínio embutido).

### Onde as coisas ficam

| O quê | Onde |
|---|---|
| Produção | container `controle-financeiro`, porta 8420 |
| Staging | `../controle-financeiro-staging/`, porta 8421 |
| Navegador para prints | `/home/umbrel/navegador/ver.sh` |
| Chave de assinatura do APK | `/home/umbrel/chaves-financerto/` (fora do git) |
| Backups do banco | `backups/` |
| Extratos e prints enviados | `../Photos/` |

## Como o código está organizado

Duas peças grandes, sem framework de frontend:

- `app.py` (~3.100 linhas) — API REST, autenticação, serve os arquivos estáticos.
- `static/index.html` (~5.200 linhas) — o app inteiro numa página: HTML, CSS e JS
  no mesmo arquivo. `login.html` e `registro.html` são separados.

`migrations/*.sql` roda em ordem no boot e fica registrado em `schema_migrations`.
Toda mudança de schema entra como migração nova — nunca editar uma já aplicada.

`android/` tem o app (WebView + digital + notificação + widget). O endereço do
servidor não é fixo: é perguntado na primeira abertura e guardado no aparelho
(`Servidor.kt`, `SetupActivity.kt`). `PonteApp.kt` deixa a página saber que está
dentro do app e usar a notificação nativa.

## Rodar e publicar

Produção é o container `controle-financeiro` (porta 8420), acessível de fora por
túnel Cloudflare. O deploy é copiar o arquivo pra dentro e reiniciar:

```sh
docker cp static/index.html controle-financeiro:/app/static/index.html
docker cp app.py controle-financeiro:/app/app.py
docker restart controle-financeiro
```

`docker compose up --build` também funciona, mas é bem mais lento e desnecessário
pra mudança de HTML/CSS/JS.

**Antes de mexer no banco**, sempre fazer backup:

```sh
docker exec controle-financeiro cp /data/orcamento.db /data/_b.db
docker cp controle-financeiro:/data/_b.db backups/orcamento_pre_MUDANCA_$(date +%Y%m%d_%H%M%S).db
docker exec controle-financeiro rm /data/_b.db
```

Scripts de manutenção precisam ser copiados pra `/app/` dentro do container antes
de rodar — em `/tmp/` o `import app` falha, porque o `sys.path[0]` é a pasta do
próprio script.

## Staging

`/home/umbrel/umbrel/home/controle-financeiro-staging/` é uma cópia que sobe na
porta 8421 com um clone do banco de produção. Mudança visual grande vai pra lá
primeiro. A senha do usuário 1 é trocada pra uma conhecida só nessa cópia; a
produção nunca é tocada. Derrubar com `docker compose down` quando terminar.

## Ver o resultado

Existe um navegador de verdade em container em `/home/umbrel/navegador`
(`./ver.sh <url>`), que tira print e lista erros de JS e requisições quebradas.
Ele é a forma de conferir mudança visual — vale mais que supor pelo código, e já
pegou vários defeitos que passariam batido (halo no hover, texto cortado, seta
que não girava).

Pra telas que exigem login, escrever um script Playwright próprio e rodar com
`--entrypoint node navegador-local`. O login do app é por formulário com cookie
de sessão, não por token em localStorage.

## Isolamento de dados

SQLite não tem row-level security: a separação depende inteiramente das queries.

- **Casa** (`casas`) agrupa quem compartilha categorias e contas — é a fronteira
  entre famílias diferentes.
- **Usuário** (`usuario_id`) é o dono do lançamento, conta, cartão, meta.

Toda query nova precisa filtrar por `uid()` ou `minha_casa_id(conn)`. Rota que
recebe ID na URL precisa de `pertence_ao_usuario` ou `pertence_a_minha_casa`
antes de tocar no registro. Já existem outras famílias reais no banco (casas 3 e
4), então vazamento aqui é vazamento de verdade.

## Nada de dados pessoais no repositório

O repositório é público. Não pode entrar:

- Domínio pessoal — `GOOGLE_REDIRECT_URI` não tem padrão embutido; cada
  instalação define o seu.
- `static/analytics.js` (gitignored) — se ficasse versionado, quem clonasse
  mandaria as visitas pro servidor de outra pessoa.
- A chave de assinatura do Android, que fica em `/home/umbrel/chaves-financerto/`
  (fora do repositório, com LEIA-ME). Perder essa chave impede atualizações que
  instalem por cima do app existente.

**Prints do README** são feitos com o **modo demonstração ligado** — dados
fictícios, com o aviso aparecendo na própria imagem. Nunca publicar captura com
os lançamentos reais.

## Marcar como pago

Só marcar `pago=1` depois de confirmação explícita de que o pagamento aconteceu.
"Vou pagar" é intenção; marcar aí desalinha o saldo do app do saldo real do banco.

## Conciliar extrato

O fluxo que funciona: ler o extrato, comparar com o que já existe, importar só o
que falta e conferir se o saldo calculado bate com o do extrato antes de dar por
encerrado. Transferência entre contas próprias vira um par com o mesmo
`grupo_transferencia` e `eh_transferencia=1`, pra não contar duas vezes nos
totais do mês.

Quando o extrato é grande demais pra itemizar, dá pra recalibrar o
`saldo_inicial` da conta: `saldo_inicial = saldo_real − Σ(rendas pagas) +
Σ(despesas pagas)`.

## Estilo

Comentário explica **por que**, não o que o código já diz. Vale comentar
armadilha (o `display:flex` que vence o atributo `hidden`, o `transform:none` do
hover que cancelava a rotação) e decisão não óbvia. Não vale narrar o passo a passo.

Interface e mensagens em português do Brasil, com acentuação correta.
