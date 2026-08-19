# FinanCerto

Controle financeiro pessoal/familiar self-hosted. Flask + SQLite, servido por
Docker num servidor Umbrel doméstico. Repositório público:
`github.com/cruzthiago2010/controle-financeiro`.

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
