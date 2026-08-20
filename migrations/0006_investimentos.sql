-- Carteira de investimentos: ações, FIIs, ETFs, cripto e renda fixa.
-- Quantidade, preço médio e valor investido não são guardados aqui — são
-- somados a partir de investimento_operacoes a cada leitura, do mesmo jeito
-- que o saldo de conta já é somado a partir de `lancamentos` em vez de
-- cacheado, pra nunca ficar dessincronizado se uma operação for editada.
CREATE TABLE IF NOT EXISTS investimentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER,
    nome TEXT NOT NULL,
    classe TEXT NOT NULL,       -- acao | fii | etf | cripto | renda_fixa | outro
    ticker TEXT,                -- código de cotação (PETR4, BTC) — nulo em renda_fixa/outro
    conta_id INTEGER,           -- conta usada nos aportes/resgates/proventos
    indexador TEXT,             -- cdi | ipca | prefixado — só renda_fixa
    taxa REAL,                  -- ex: 110 (% do indexador) ou 6 (indexador + 6% a.a.)
    vencimento TEXT,            -- opcional, informativo
    criado_em TEXT
);

-- aporte/resgate/provento de um investimento. Cada um tem um lançamento
-- espelho em `lancamentos` (lancamento_id) pra afetar o saldo da conta —
-- aporte e resgate como eh_transferencia (não contam no orçamento do mês,
-- só mudam a forma do dinheiro), provento como receita normal (ganho real).
CREATE TABLE IF NOT EXISTS investimento_operacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    investimento_id INTEGER NOT NULL,
    usuario_id INTEGER,
    tipo TEXT NOT NULL,         -- aporte | resgate | provento
    quantidade REAL,            -- nulo em renda_fixa/outro e em provento
    preco_unitario REAL,        -- nulo em renda_fixa/outro e em provento
    valor REAL NOT NULL,
    data TEXT NOT NULL,
    lancamento_id INTEGER,
    observacao TEXT,
    criado_em TEXT
);

-- cache de cotação. Preço de mercado não é dado pessoal, então fica global
-- (sem usuario_id) — evita bater na API duas vezes pro mesmo ticker quando
-- duas casas diferentes têm o mesmo ativo.
CREATE TABLE IF NOT EXISTS investimento_cotacoes (
    chave TEXT PRIMARY KEY,     -- ticker (ação/FII/ETF/cripto)
    valor REAL,
    atualizado_em TEXT
);

-- série diária/mensal do CDI e do IPCA (Banco Central, API SGS, sem chave),
-- guardada como fator acumulado desde uma data-base fixa. Renda fixa calcula
-- o rendimento de cada aporte como a razão entre o fator na data do aporte e
-- o fator de hoje, sem precisar rebuscar a série inteira a cada leitura.
CREATE TABLE IF NOT EXISTS indexador_serie (
    indexador TEXT NOT NULL,        -- cdi | ipca
    data TEXT NOT NULL,
    fator_acumulado REAL NOT NULL,
    PRIMARY KEY (indexador, data)
);
