# Controle Financeiro — Self-hosted

App de controle financeiro mensal (renda, despesas, cartões),
feito para rodar no seu próprio servidor (home lab,ou qualquer
Docker). Todos os dados ficam salvos localmente em SQLite, dentro da pasta
`data/` — nada sai do seu servidor.

## Funções

- **Dashboard** — saldo do mês, dinheiro disponível, receitas e despesas,
  gráfico de fluxo de caixa, contas vencendo nos próximos 7 dias, parcelas
  futuras e resumo por conta. Os cartões do dashboard são clicáveis e
  mostram como cada valor foi calculado.
- **Receitas & Despesas** — lançamentos avulsos ou recorrentes (aluguel,
  salário, consignados fixos), com parcelamento automático (informe o valor
  total ou o valor da parcela) e anexo de comprovante/foto por lançamento.
  Botão para copiar os lançamentos recorrentes do mês anterior sem digitar
  tudo de novo.
- **Contas** — várias contas por usuário, com saldo calculado
  automaticamente, e transferências entre contas (inclusive entre contas de
  usuários diferentes, ex: da sua conta para a da sua esposa/marido) sem
  contar como receita/despesa real.
- **Cartões de crédito** — limite, fatura atual, dia de vencimento e conta
  vinculada para débito automático da fatura.
- **Categorias** — categorias próprias de receita/despesa, com cor.
- **Calendário** — visão mensal dos vencimentos, com indicação de pago,
  pendente e recebido.
- **Múltiplos usuários** — cada pessoa da casa com seu próprio login;
  dá pra ver e mover dinheiro entre as contas de todo mundo.
- **Backup** — baixe um `.zip` com lançamentos, comprovantes e fotos a
  qualquer momento, guarde backups agendados no servidor, ou restaure a
  partir de um arquivo.
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
- Para adicionar outro usuário (ex: esposa/marido), clique em "Adicionar usuário"
  no rodapé do menu lateral.

## Instalar como app (PWA)

No celular (Android/Chrome), acesse o app pelo navegador e use "Adicionar à tela
inicial" — ele abre em tela cheia, como um app instalado.

## Estrutura

- `app.py` — backend Flask (API REST + serve o site + autenticação)
- `static/index.html` — frontend do app (uma página só)
- `static/login.html` — tela de login
- `static/manifest.json`, `static/sw.js`, `static/icon*.svg` — arquivos do PWA
- `Dockerfile` / `docker-compose.yml` — empacotamento
- `data/` — onde o banco SQLite, a chave de sessão, os comprovantes e as
  fotos ficam salvos (fica fora do container, não perde dados ao atualizar)
- `umbrel-app.yml` — manifesto opcional, caso queira publicar como app
  formal numa community app store do Umbrel

## Trocar a porta

Se `8420` já estiver em uso, edite `docker-compose.yml` e troque
`"8420:5000"` para outra porta livre, ex: `"8421:5000"`.
