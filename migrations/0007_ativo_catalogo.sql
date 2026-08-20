-- Catálogo local de ativos (ações/FIIs/ETFs da B3 e principais criptomoedas),
-- usado só pra busca com autocompletar ao cadastrar um investimento. Fica
-- cacheado aqui e atualizado 1x por dia em segundo plano, pra buscar
-- enquanto o usuário digita sem bater na API externa a cada letra.
CREATE TABLE IF NOT EXISTS ativo_catalogo (
    classe TEXT NOT NULL,       -- acao | fii | etf | cripto
    ticker TEXT NOT NULL,
    nome TEXT,
    PRIMARY KEY (classe, ticker)
);
