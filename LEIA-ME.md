# Controle Financeiro — Self-hosted

App simples de controle mensal de renda, despesas e consignados, feito
para rodar no seu servidor (Umbrel ou qualquer Docker).

Todos os dados ficam salvos localmente em SQLite, dentro da pasta `data/` —
nada sai do seu servidor.

## Como rodar no Umbrel (mais fácil — via terminal/SSH)

1. Acesse seu Umbrel via SSH (ou o terminal, se tiver o app "Terminal" instalado).
2. Copie a pasta `orcamento-app` inteira para o Umbrel, por exemplo via `scp`:
   ```bash
   scp -r orcamento-app umbrel@umbrel.local:~/orcamento-app
   ```
3. Entre na pasta e suba o container:
   ```bash
   cd ~/orcamento-app
   docker compose up -d --build
   ```
4. Acesse no navegador: `http://umbrel.local:8420` (ou o IP do seu Umbrel).

Para parar: `docker compose down`
Para atualizar depois de editar o código: `docker compose up -d --build`

## Login

O app agora exige login. No primeiro start:

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

## Transferências entre contas

Cadastre suas contas na aba "Contas". Use o botão flutuante (+) no canto
inferior direito, ou "🔁 Transferir" na aba Contas, para mover dinheiro entre
contas — inclusive entre contas de usuários diferentes (ex: da sua conta para
a da sua esposa). A transferência não conta como receita/despesa real nos
totais do mês, só ajusta o saldo de cada conta.

## Como rodar em qualquer máquina com Docker

```bash
cd orcamento-app
docker compose up -d --build
```
Acesse `http://localhost:8420`.

## Estrutura

- `app.py` — backend Flask (API REST + serve o site + autenticação)
- `static/index.html` — frontend do app (uma página só)
- `static/login.html` — tela de login
- `static/manifest.json`, `static/sw.js`, `static/icon*.svg` — arquivos do PWA
- `Dockerfile` / `docker-compose.yml` — empacotamento
- `data/` — onde o banco SQLite e a chave de sessão ficam salvos (fica fora
  do container, não perde dados ao atualizar)
- `umbrel-app.yml` — manifesto opcional, caso queira publicar como app
  formal numa community app store do Umbrel

## Como usar

- Escolha o mês no topo.
- Adicione lançamentos (renda, despesa ou consignado) no formulário embaixo.
- Marque "recorrente" para itens que se repetem todo mês (aluguel, salário,
  consignados fixos).
- Use "Copiar recorrentes do mês anterior" para não digitar tudo de novo
  todo mês — ele traz os itens marcados como recorrentes do mês anterior
  para o mês atual.
- O saldo do mês é calculado automaticamente.

## Trocar a porta

Se `8420` já estiver em uso no seu Umbrel, edite `docker-compose.yml` e
troque `"8420:5000"` para outra porta livre, ex: `"8421:5000"`.
