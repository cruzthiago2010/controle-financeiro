-- Baseline: consolida em um schema só tudo que antes era montado aos poucos
-- por CREATE TABLE + add_col_if_missing() espalhados pelo código. Uma
-- instalação nova recebe o schema completo direto daqui; uma instalação já
-- existente não sofre nada (todas as colunas já existem, então cada
-- comando abaixo não faz efeito nenhum — é só o registro formal do que já
-- estava em vigor).
--
-- Todo arquivo de migration precisa ser idempotente (IF NOT EXISTS / não dar
-- erro se rodar de novo), porque o sqlite3 do Python não faz rollback
-- parcial de executescript().

CREATE TABLE IF NOT EXISTS lancamentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mes TEXT NOT NULL,                    -- formato YYYY-MM
    tipo TEXT NOT NULL,                   -- renda | despesa
    descricao TEXT NOT NULL,
    valor REAL NOT NULL,
    vencimento TEXT,                      -- opcional, formato AAAA-MM-DD
    categoria TEXT,
    conta TEXT,
    conta_id INTEGER,
    recorrente INTEGER DEFAULT 0,         -- 1 = repete todo mês
    grupo_recorrencia TEXT,
    recorrencia_ate TEXT,                 -- último mês da recorrência (NULL = sempre)
    grupo_parcela TEXT,
    parcela_num INTEGER,
    parcela_total INTEGER,
    pago INTEGER DEFAULT 0,
    data_pagamento TEXT,
    comprovante TEXT,
    observacao TEXT,
    eh_transferencia INTEGER DEFAULT 0,
    grupo_transferencia TEXT,
    usuario_id INTEGER,
    criado_em TEXT
);

CREATE TABLE IF NOT EXISTS cartoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    limite REAL DEFAULT 0,
    fatura_atual REAL DEFAULT 0,
    fatura_paga INTEGER DEFAULT 0,
    dia_vencimento INTEGER,
    conta_id INTEGER,
    usuario_id INTEGER
);

CREATE TABLE IF NOT EXISTS categorias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    tipo TEXT NOT NULL,                   -- receita | despesa
    cor TEXT,
    UNIQUE(nome, tipo)
);

CREATE TABLE IF NOT EXISTS contas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    saldo_inicial REAL DEFAULT 0,
    criado_em TEXT,
    usuario_id INTEGER
);

CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    username TEXT NOT NULL UNIQUE,
    senha_hash TEXT NOT NULL,
    foto TEXT,
    somente_leitura INTEGER DEFAULT 0,
    criado_em TEXT
);

CREATE TABLE IF NOT EXISTS recorrencias_puladas (
    grupo_recorrencia TEXT NOT NULL,
    mes TEXT NOT NULL,
    PRIMARY KEY (grupo_recorrencia, mes)
);

CREATE TABLE IF NOT EXISTS orcamentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    categoria TEXT NOT NULL,
    limite REAL NOT NULL,
    usuario_id INTEGER,
    UNIQUE(categoria, usuario_id)
);

CREATE TABLE IF NOT EXISTS metas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    valor_alvo REAL NOT NULL,
    valor_atual REAL DEFAULT 0,
    prazo TEXT,
    criado_em TEXT,
    usuario_id INTEGER
);

CREATE TABLE IF NOT EXISTS holerites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER,
    referencia TEXT,
    recebido_em TEXT,
    total_proventos REAL,
    total_descontos REAL,
    total_liquido REAL,
    adiantamento REAL,
    itens_json TEXT,
    arquivo TEXT NOT NULL,
    lancamento_id INTEGER,
    lancamento_adiantamento_id INTEGER,
    criado_em TEXT
);

CREATE TABLE IF NOT EXISTS consignados (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER,
    nome TEXT NOT NULL,
    valor_parcela REAL NOT NULL,
    parcela_atual INTEGER,
    parcela_total INTEGER,
    ativo INTEGER DEFAULT 1,
    observacao TEXT,
    criado_em TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_contas_nome_usuario ON contas (nome, usuario_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lancamentos_recorrencia_mes
    ON lancamentos (grupo_recorrencia, mes, usuario_id) WHERE grupo_recorrencia IS NOT NULL;
