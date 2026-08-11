# Controle Financeiro — Self-hosted

App de controle financeiro mensal (renda, despesas, cartões),
feito para rodar no seu próprio servidor (home lab,ou qualquer
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
- **Consignados** _(opcional)_ — acompanhamento de empréstimos consignados
  descontados em folha, com parcela atual/total e progresso. Fica oculto
  por padrão; só o administrador (primeiro usuário da instalação) pode
  habilitar essa aba para todo mundo, na aba Backup.
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
| **Dashboard** | ![Dashboard](docs/screenshots/dashboard.png) |
| **Receitas & Despesas** | ![Receitas & Despesas](docs/screenshots/lancamentos.png) |
| **Calendário** | ![Calendário](docs/screenshots/calendario.png) |
| **Cartões de crédito** | ![Cartões](docs/screenshots/cartoes.png) |
| **Backup automático** | ![Backup](docs/screenshots/backup.png) |

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

## Instalar como app (PWA)

No celular (Android/Chrome), acesse o app pelo navegador e use "Adicionar à tela
inicial" — ele abre em tela cheia, como um app instalado.

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
- `umbrel-app.yml` — manifesto opcional, caso queira publicar como app
  formal numa community app store do Umbrel

## Trocar a porta

Se `8420` já estiver em uso, edite `docker-compose.yml` e troque
`"8420:5000"` para outra porta livre, ex: `"8421:5000"`.
