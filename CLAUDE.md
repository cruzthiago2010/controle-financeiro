# FinanCerto

Controle financeiro pessoal/familiar self-hosted. Flask + SQLite, servido por
Docker num servidor Umbrel doméstico. Repositório público:
`github.com/cruzthiago2010/controle-financeiro`.

## Estado atual (19/08/2026)

Versão no ar: **FinanCerto 3.1** — web em produção, APK na release `v3.1`.
Repositório publicado e limpo de dados pessoais.

### O que existe hoje

**Dashboard** — indicadores em cards com variação sobre o mês anterior, destaque
do saldo real com número animado e mini-gráfico, fluxo de caixa do mês e diário,
gastos por categoria em lista ranqueada, tendência de 6 meses, contas vencidas e
vencendo, parcelas futuras e resumo por conta.

**Lançamentos** — receitas e despesas com categoria, conta, parcelamento e
recorrência; anexo de comprovante; leitura de nota fiscal por OCR. Ao digitar a
descrição, sugere lançamentos parecidos já feitos e preenche categoria, valor e
conta a partir do histórico (`/api/sugestoes`). Campos de valor abrem um teclado
próprio do app, com as quatro operações.

**Contas e cartões** — saldo por conta com o logo oficial do banco,
transferências entre contas pareadas (`grupo_transferencia`), cartões com limite
e fatura, e lançamentos de cartão isolados das despesas do mês
(`cartao_transacoes`).

**Outras abas** — categorias com cor, calendário, metas, holerite (importa PDF e
extrai proventos/descontos), empréstimos consignados, notificações e backup
automático com retenção.

**Barra superior** — busca que varre todos os meses (`/api/busca`), sino de
contas vencidas com marcar-como-lidas, menu do usuário e alternância de tema.

**Multiusuário** — cada casa é isolada; dentro dela dá para ter mais de um
usuário, com modo somente-leitura. O administrador da casa é o usuário de menor
`id` dentro dela (`eh_administrador`), e só ele adiciona usuário, alterna o
somente-leitura de alguém e mexe na configuração da casa. Login por senha ou
Google. Modo demonstração troca tudo por dados fictícios.

**App Android** — WebView com bloqueio por digital/PIN, notificação de contas em
segundo plano, widget de saldo e envio de arquivos. O endereço do servidor é
perguntado na instalação.

### Decisões que valem lembrar

- **Sem framework no frontend.** Tudo em `static/index.html`. Cresceu bastante,
  mas trocar isso agora é reescrita — só considerar se virar impeditivo.
- **Notificação no navegador só com o app aberto.** Push em segundo plano exigiria
  um serviço externo, o que contraria a ideia de self-hosted. Quem quer aviso com
  o celular guardado usa o app Android, e isso está dito na própria aba.
- **Dentro do app Android quem notifica é o app**, não o navegador — o WebView não
  tem a API de notificação, e a página detecta isso pela ponte `FinanCertoApp`.
- **Logo dos bancos vem do ícone oficial do app de cada um**, guardado localmente
  em `static/logos/`. Favicon e Wikimedia foram testados antes e não serviram
  (resolução baixa ou logotipo por extenso, que não cabe em ícone redondo).
- **"Marcar como lida" no sino não marca como paga.** São coisas diferentes e o
  toast diz isso — mexer nisso é mexer em dado financeiro.
- **Gastos por categoria é lista, não rosca.** Com ~20 categorias a legenda da
  rosca ficava ilegível.
- **Permissão se decide no servidor, não na tela.** Esconder botão é conforto;
  quem barra é o `before_request` (somente-leitura) mais o `eh_administrador`
  dentro da rota. Toda rota nova que cria usuário ou muda permissão precisa da
  checagem explícita — foi assim que duas brechas apareceram em agosto/2026.
- **A isenção de escrita do somente-leitura vale por sufixo, não por prefixo.**
  `/api/usuarios/` inteiro liberado deixava a conta somente-leitura chamar
  `/somente-leitura` em si mesma e se promover. Só `/senha` e `/foto` passam.
- **O administrador não pode se marcar como somente-leitura.** A rota que desfaz
  é escrita e exige administrador, então ele se trancaria fora da própria casa.
- **Despesa não paga aparece também no mês seguinte**, marcada como atrasada.
  Para série recorrente, só a ocorrência não paga mais antiga — senão cada mês
  sem pagar empilharia mais uma cópia.

### Armadilhas já encontradas (não repetir)

- `hidden` no HTML **perde** para `display:flex` no CSS. Aconteceu com o selo do
  sino e com o rodapé da barra lateral; precisa de `[hidden]{display:none}`.
- `transform:none` no `:hover` cancela também a rotação de estado. Por isso o giro
  da seta de recolher fica no ícone, não no botão.
- Zerar só o `transform` no hover de botão transparente deixa o brilho verde
  global virar um halo retangular. Precisa zerar `box-shadow` junto.
- Trocar `innerHTML` recria o elemento e mata qualquer transição em curso.
- Chart.js não renderiza gradiente na caixinha da legenda — usar `usePointStyle`.
- Mudou ícone ou marca? Trocar o `CACHE_NAME` no `sw.js`, senão o service worker
  segue servindo o arquivo antigo.

### Próximos passos

1. **Foto de perfil dá 404 no modo demonstração.** O arquivo está no servidor;
   quem erra é `baixar_foto_perfil` (`app.py`), que valida o nome contra o banco
   atual — e no modo demo esse banco tem `usuarios.foto` nulo. Como os prints do
   README são feitos em demo, é ali que o 404 aparece. **Não limpar a referência
   no banco**: apagaria um dado válido por causa de um bug de outro lugar.
2. **APK nunca foi testado em aparelho.** Não há emulador no servidor; a validação
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
| Contexto financeiro pessoal | `NOTAS-PESSOAIS.md` (não versionado) |

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
primeiro. As senhas ali são trocadas pra uma conhecida só nessa cópia — vale
trocar também a de um usuário comum quando o teste precisa de dois perfis; a
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

`/home/umbrel/navegador/testar-admin.js` serve de molde pra testar permissão:
entra no staging com dois perfis (administrador e usuário comum), diz se cada
botão está visível, e ainda chama a API pelo `fetch` da própria página pra
conferir que o servidor recusa sozinho quem a tela apenas esconderia. Mudança de
permissão só está verificada quando as duas metades passam.

## Isolamento de dados

SQLite não tem row-level security: a separação depende inteiramente das queries.

- **Casa** (`casas`) agrupa quem compartilha categorias e contas — é a fronteira
  entre famílias diferentes.
- **Usuário** (`usuario_id`) é o dono do lançamento, conta, cartão, meta.

Toda query nova precisa filtrar por `uid()` ou `minha_casa_id(conn)`. Rota que
recebe ID na URL precisa de `pertence_ao_usuario` ou `pertence_a_minha_casa`
antes de tocar no registro. Estar na mesma casa não basta pra rota que muda
permissão ou cria usuário: essas exigem `eh_administrador(conn)` também. Já
existem outras famílias reais no banco (casas 3 e 4), então vazamento aqui é
vazamento de verdade.

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
