## O que muda

<!-- Descreva o efeito para quem usa o app, não só o que mudou no arquivo. -->

## Por quê

<!-- Issue relacionada, ou o problema que isso resolve. Ex: Resolve #12 -->

## Como testei

<!-- Ex: subi do zero com data/ vazia, criei uma despesa parcelada em 3x e
     conferi as parcelas no calendário. -->

## Antes / depois

<!-- Se mexeu na tela, anexe captura. Teste também no celular: o app é usado
     sobretudo no telefone. Tarje qualquer valor real antes de anexar. -->

## Checklist

- [ ] Sobe do zero com `docker compose up --build` numa pasta `data/` vazia
- [ ] Mudança de schema entrou como migration nova em `migrations/` (não editei uma já publicada)
- [ ] Nada no código depende do meu servidor: sem domínio, IP ou token fixos
- [ ] Variável nova está no `.env.example` **e** declarada no `docker-compose.yml`
- [ ] Texto novo de interface está em português e inglês
- [ ] Não há dado pessoal meu no diff
