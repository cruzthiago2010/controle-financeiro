<p align="center">
  <img src="static/logo-financerto.svg" width="96" alt="FinanCerto">
</p>

# FinanCerto — Self-hosted

App de controle financeiro mensal (renda, despesas, cartões),
feito para rodar no seu próprio servidor (home lab, ou qualquer
Docker). Todos os dados ficam salvos localmente em SQLite, dentro da pasta
`data/` — nada sai do seu servidor.

## Funções

- **Dashboard** — saldo do mês, dinheiro disponível, receitas e despesas,
  gráfico de fluxo de caixa (resumo do mês e evolução dia a dia), gráfico de
  tendência dos últimos 6 meses, contas vencendo nos próximos 7 dias,
  parcelas futuras e resumo por conta. Os cartões do dashboard são
  clicáveis e mostram como cada valor foi calculado.
- **Receitas & Despesas** — lançamentos avulsos ou recorrentes (aluguel,
  salário), com parcelamento automático (informe o valor total ou o valor
  da parcela) e anexo de comprovante/foto por lançamento. Botão para copiar
  os lançamentos recorrentes do mês anterior sem digitar tudo de novo.
- **Escanear nota fiscal** — no botão flutuante, tira foto do cupom fiscal
  pela câmera do celular e o app lê a loja, o valor e os itens comprados
  automaticamente (OCR), sempre com uma tela de revisão antes de lançar a
  despesa. Tem também um botão só de "Importar comprovante", que já abre o
  formulário de despesa com o arquivo anexado.
- **Investimentos** — carteira completa: ações, FIIs, ETFs, BDRs,
  criptomoedas, ativos americanos (stocks, REITs e ETFs), renda fixa
  (CDB/LCI/LCA/debênture/Tesouro, com emissor e liquidez) e fundos. Você
  busca o ativo pelo ticker, o preço atual já vem preenchido, e a **cotação
  se atualiza sozinha** de hora em hora. Os **proventos são importados
  automaticamente**: o app sabe quantas cotas você tinha na data-base de cada
  dividendo, JCP ou rendimento e lança o valor certo na conta, já descontando
  o imposto do JCP. Em cinco abas — Resumo, Posições, Proventos, Patrimônio e
  Rentabilidade — dá pra ver o patrimônio crescendo mês a mês desde a
  primeira compra, quanto rendeu contra o CDI, tudo que já caiu de provento e
  o que ainda vai cair. Definindo a distribuição ideal da sua carteira (ex:
  40% em ações, 25% em FIIs), cada ativo ganha uma coluna **Comprar?**
  dizendo se está abaixo ou acima da meta.
- **Orçamento por categoria** — defina um limite mensal para cada categoria
  de despesa e acompanhe numa barra de progresso o quanto já foi gasto.
- **Metas de economia** — crie metas com valor alvo e prazo opcional, e vá
  registrando quanto já conseguiu guardar.
- **Holerite** — importe o PDF do contracheque e o app lê sozinho os
  proventos, descontos, o líquido recebido no fim do mês e o adiantamento
  quinzenal, lançando as receitas correspondentes automaticamente. Mostra
  um gráfico de renda/descontos por mês — clique numa barra de proventos ou
  descontos pra ver exatamente quais itens do contracheque compuseram
  aquele valor. Avisa quando falta importar o holerite de algum mês
  (inclusive já deixa um espaço pronto pro próximo mês) e permite
  conferir/editar os lançamentos gerados por cada holerite.
- **Empréstimos** _(opcional)_ — acompanhamento de empréstimos (consignados
  em folha ou com débito em conta), com parcela atual/total e progresso.
  Fica oculto por padrão; só o administrador (primeiro usuário da
  instalação) pode habilitar essa aba para todo mundo, na aba Backup.
- **Contas** — várias contas por usuário, com saldo calculado
  automaticamente, e transferências entre contas (inclusive entre contas de
  usuários diferentes, ex: da sua conta para a da sua esposa/marido) sem
  contar como receita/despesa real.
- **Cartões de crédito** — limite, fatura atual, dia de vencimento, conta
  vinculada para débito automático da fatura, e aviso quando a fatura está
  perto (ou passou) do limite.
- **Categorias** — categorias próprias de receita/despesa, com cor.
- **Calendário** — visão mensal dos vencimentos, com indicação de pago,
  pendente e recebido.
- **Múltiplos usuários por casa** — cada pessoa da sua casa (família) tem o
  próprio login; dá pra ver e mover dinheiro entre as contas de todo mundo
  dentro da mesma casa. Cada usuário pode ser marcado como **somente
  leitura** (vê tudo, não edita nada).
- **Cadastro público (`/registro`)** — qualquer pessoa pode criar a própria
  casa, totalmente isolada da sua — sem ver nem uma categoria, conta ou
  lançamento seus, e vice-versa. Quem cria uma casa nova já entra como
  administrador dela. Backup completo do banco fica desativado
  automaticamente se o servidor tiver mais de uma casa (evita vazar dado de
  uma casa pra outra).
- **Backup** — baixe um `.zip` com lançamentos, comprovantes, holerites e
  fotos a qualquer momento, guarde backups agendados no servidor, ou
  restaure a partir de um arquivo. É também onde fica o botão de exportar
  os lançamentos do mês selecionado em CSV.
- **Modo demonstração** — ativa um banco de dados fictício separado pra
  mostrar o app pra alguém sem expor seus dados reais.
- **Instalável como app (PWA)** — abre em tela cheia no celular, como um
  app nativo.
- **Tema claro/escuro** e opção de **ocultar valores** na tela (útil pra
  usar em público).

## Screenshots

_Capturas feitas com o **modo demonstração** ligado — dados fictícios._

| | |
|---|---|
| **Entrar** | ![Tela de entrada](docs/screenshots/login.png) |
| **Criar casa** | ![Criar casa](docs/screenshots/registro.png) |
| **Dashboard** | ![Dashboard](docs/screenshots/dashboard.png) |
| **Receitas & Despesas** | ![Receitas & Despesas](docs/screenshots/lancamentos.png) |
| **Investimentos** | ![Investimentos](docs/screenshots/investimentos.png) |
| **Proventos recebidos e a receber** | ![Proventos](docs/screenshots/investimentos-proventos.png) |
| **Rentabilidade contra o CDI** | ![Rentabilidade](docs/screenshots/investimentos-rentabilidade.png) |
| **Calendário** | ![Calendário](docs/screenshots/calendario.png) |
| **Cartões de crédito** | ![Cartões](docs/screenshots/cartoes.png) |
| **Backup automático** | ![Backup](docs/screenshots/backup.png) |

No celular, quem abre pelo Android recebe o convite para baixar o app; no
iPhone, o passo a passo para instalar na tela de início.

<p align="center">
  <img src="docs/screenshots/login-android.png" width="300" alt="Tela de entrada no Android, com o convite para baixar o app">
</p>

## Como rodar (mais fácil — via terminal/SSH)

1. Acesse via SSH (ou o terminal instalado).
2. Clone o repositório e suba o container:
   ```bash
   git clone https://github.com/cruzthiago2010/controle-financeiro.git
   cd controle-financeiro
   docker compose up -d --build
   ```
3. Acesse no navegador: `http://localhost:8420` (IP da sua máquina).

A pasta `data/` é criada sozinha no primeiro start e guarda o banco, os
comprovantes e as fotos. Ela fica fora do container, então atualizar o código
não apaga nada.

## Login

O app exige login. No primeiro start:

- Se você definiu `ADMIN_USERNAME`/`ADMIN_PASSWORD` no `docker-compose.yml`, use essas credenciais.
- Se não definiu, uma senha aleatória é gerada automaticamente. Para ver qual foi,
  rode `docker compose logs` logo após o primeiro `docker compose up -d --build` e
  procure o bloco `USUÁRIO INICIAL CRIADO`.
- Depois de entrar, troque a senha clicando no seu nome no rodapé do menu lateral.
- Para adicionar outro usuário **da sua casa** (ex: esposa/marido), clique em
  "Adicionar usuário" no rodapé do menu lateral.
- Pra dar acesso a alguém de **fora da sua casa** (um amigo, outra família),
  mande o link `/registro` — a pessoa cria a própria casa, separada da sua.

### Login com Google (opcional)

Além de usuário/senha, dá pra habilitar um botão "Entrar com Google" — quem
usa pela primeira vez também cria a própria casa automaticamente, do mesmo
jeito que pelo `/registro`. Pra ativar:

1. Crie um "ID do cliente OAuth" (tipo **Aplicativo da Web**) em
   [console.cloud.google.com](https://console.cloud.google.com) → APIs e
   Serviços → Credenciais.
2. Em "URIs de redirecionamento autorizados", cadastre
   `https://SEU-DOMINIO/api/auth/google/callback`.
3. Copie `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET` pro seu `.env`.
4. Enquanto a tela de consentimento OAuth do seu app estiver em modo
   **"Teste"**, só os e-mails que você cadastrar manualmente lá conseguem
   entrar. Pra qualquer pessoa poder usar, publique o app (status
   "Em produção") na tela de consentimento.

Sem essas variáveis definidas, o botão simplesmente não aparece — o login
por usuário/senha continua funcionando normalmente.

## Instalar no celular

### App Android (recomendado)

Baixe o APK na [página de releases](../../releases/latest) e instale. Como não
vem da Play Store, o Android pede para autorizar a instalação de "fontes
desconhecidas" na primeira vez.

Na primeira abertura o app pergunta o endereço do **seu** servidor — não vem
apontado para lugar nenhum. Vale tanto um domínio quanto o endereço na sua rede:

```
financeiro.seudominio.com.br
192.168.1.10:8420
```

Ele testa a conexão antes de salvar, então erro de digitação aparece na hora em
vez de virar tela branca. Para trocar depois, use "Trocar servidor" na tela de
erro de conexão.

O que o app faz além de abrir o site:

- **Bloqueio por digital ou PIN** ao abrir, usando o desbloqueio do próprio
  aparelho. Se o celular não tiver nenhum bloqueio configurado, abre direto —
  não faz sentido proteger o app se a tela do celular está aberta.
- **Aviso de contas vencendo**, checado em segundo plano.
- **Widget de saldo** para a tela inicial.
- Envio de arquivos (comprovantes e notas) direto da galeria ou da câmera.

### Ou como PWA

Sem instalar nada: acesse pelo Chrome e use "Adicionar à tela inicial". Abre em
tela cheia, mas sem digital, widget nem notificações.

## Estrutura

- `app.py` — backend Flask (API REST + serve o site + autenticação)
- `static/index.html` — frontend do app (uma página só)
- `static/login.html` — tela de login
- `static/registro.html` — tela de cadastro público (cria uma casa nova)
- `static/manifest.json`, `static/sw.js`, `static/icon*.svg` — arquivos do PWA
- `Dockerfile` / `docker-compose.yml` — empacotamento (inclui `tesseract-ocr`
  para a leitura de nota fiscal)
- `.env.example` — modelo das variáveis de ambiente aceitas; copie para
  `.env` e preencha (o `.env` de verdade nunca é versionado)
- `migrations/` — mudanças de schema do banco, aplicadas em ordem e
  registradas numa tabela de controle (`schema_migrations`) toda vez que o
  app sobe
- `data/` — onde o banco SQLite, a chave de sessão, os comprovantes, os
  holerites e as fotos ficam salvos (fica fora do container, não perde
  dados ao atualizar)
- `android/` — código do app Android (WebView com digital, notificação de
  contas e widget de saldo). Para compilar o seu:
  `docker build -t android-build-env -f android/Dockerfile.build android/` e
  depois `gradle assembleRelease` dentro dele. O APK sai sem assinatura;
  assine com a sua própria chave.
- `umbrel-app.yml` — manifesto opcional, caso queira publicar como app
  formal numa community app store do Umbrel

## Trocar a porta

Se `8420` já estiver em uso, edite `docker-compose.yml` e troque
`"8420:5000"` para outra porta livre, ex: `"8421:5000"`.
