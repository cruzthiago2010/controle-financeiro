<p align="center">
  <b>Português</b> · <a href="SECURITY.en.md">English</a>
</p>

# Política de segurança

O FinanCerto guarda dados financeiros — extrato, holerite, comprovante, saldo de
investimento. Uma falha aqui expõe a vida financeira de quem instalou. Por isso
falha de segurança tem tratamento separado de bug comum.

## Versões com suporte

O projeto é distribuído como código-fonte, e a instalação recomendada é sempre a
última versão da branch `main`. Correção de segurança sai só para ela — não há
backport para commits antigos. Se a sua instalação está atrasada, atualizar é
parte da correção:

```bash
cd controle-financeiro
git pull
docker compose up -d --build
```

| Versão | Suporte |
|---|---|
| última da `main` | ✅ recebe correções |
| qualquer commit anterior | ❌ atualize antes de reportar |

## Como reportar uma falha

**Não abra uma issue pública.** Issue é visível para todo mundo, inclusive para
quem quiser usar a falha antes de ela ser corrigida.

Use o canal privado do próprio GitHub:

1. Vá em **[Security → Report a vulnerability](https://github.com/cruzthiago2010/controle-financeiro/security/advisories/new)**.
2. Descreva o problema. O relato fica visível só para você e para o mantenedor.

Ajuda muito incluir:

- o que dá para fazer com a falha (ler dado de outra casa? entrar sem senha?
  executar comando no servidor?);
- o passo a passo para reproduzir, com o mínimo possível de passos;
- a versão que você testou (`git rev-parse --short HEAD`) e como a instalação
  está exposta (só na rede local, atrás de um túnel, aberta na internet);
- se você já sabe, o trecho de código envolvido.

## O que esperar

- **Confirmação de recebimento em até 5 dias.** Este é um projeto mantido por
  uma pessoa só, nas horas vagas — o prazo é honesto, não é SLA de empresa.
- **Um retorno dizendo se a falha foi reproduzida** assim que houver um
  diagnóstico, com uma ideia de prazo para a correção.
- **Crédito no advisory publicado**, com o nome ou apelido que você preferir,
  a não ser que peça para não aparecer.
- **Divulgação depois da correção.** O advisory é publicado junto com o commit
  que corrige, para que quem hospeda saiba que precisa atualizar.

Não há programa de recompensa em dinheiro. É um projeto livre, sem receita.

## O que conta como falha de segurança

Entram aqui, entre outras coisas:

- acessar dado de uma **casa** diferente da sua (o isolamento entre casas é a
  garantia central do cadastro público);
- passar pela tela de login, escalar de usuário somente-leitura para editor, ou
  de usuário comum para administrador;
- SQL injection, XSS, CSRF, path traversal nos anexos e comprovantes;
- execução de código no servidor por upload (nota fiscal, holerite, foto);
- vazamento de `SECRET_KEY`, senha, token do Pluggy ou do Google em log,
  resposta de API ou backup.

**Não** contam como falha do projeto:

- instalação exposta direto na internet sem HTTPS e sem proxy reverso — o
  README recomenda o contrário;
- `.env` com senha fraca, ou `SECRET_KEY` em branco em produção;
- ataque que dependa de já ter acesso de administrador na sua própria casa;
- resultado de scanner automático sem impacto demonstrado.

## O que o projeto faz pela sua instalação

- **Nada sai do seu servidor.** Não há telemetria, e o banco é um SQLite dentro
  de `data/`.
- **`.env`, `data/` e chave de sessão nunca são versionados** — estão no
  `.gitignore`, e o repositório tem varredura de segredo ligada.
- **Dependências acompanhadas pelo Dependabot**, com alerta de vulnerabilidade
  e atualização automática de versão vulnerável.
- **Análise estática (CodeQL) a cada commit e uma vez por semana.**

Se você hospeda o FinanCerto, duas recomendações que valem mais que qualquer
código: mantenha o app atrás de HTTPS (túnel Cloudflare, Tailscale ou proxy
reverso com certificado) e não deixe a porta aberta direto para a internet.
