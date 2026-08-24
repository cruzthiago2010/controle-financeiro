-- Transação de cartão vinda do Open Finance.
--
-- `cartao_transacoes` nasceu para o que a pessoa digita, então não tinha como
-- saber se uma linha já foi importada nem de onde ela veio.

-- O id da Pluggy é o que impede reimportar a mesma compra quando o sync roda
-- de novo. UNIQUE num índice (e não na coluna) porque ALTER TABLE do SQLite
-- não aceita adicionar coluna com restrição de unicidade.
ALTER TABLE cartao_transacoes ADD COLUMN transacao_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_cartao_transacoes_externo
    ON cartao_transacoes (transacao_id) WHERE transacao_id IS NOT NULL;

-- Mesma marca dos lançamentos: conferir o que veio do banco é diferente de
-- conferir o que você mesmo digitou.
ALTER TABLE cartao_transacoes ADD COLUMN origem TEXT DEFAULT 'manual';

-- Parcelamento que o próprio cartão informa (creditCardMetadata). Sem isso,
-- "1/3" viraria três compras soltas sem relação entre si.
ALTER TABLE cartao_transacoes ADD COLUMN parcela_num INTEGER;
ALTER TABLE cartao_transacoes ADD COLUMN parcela_total INTEGER;

-- Em que fatura a compra cai (AAAA-MM). O cartão fecha antes do fim do mês,
-- então uma compra do dia 12 pode entrar na fatura do mês seguinte — usar a
-- data da compra para agrupar mostraria o total errado.
ALTER TABLE cartao_transacoes ADD COLUMN fatura_mes TEXT;

-- Pagamento e estorno chegam como CREDIT e abatem o que se deve; compra chega
-- como DEBIT. Guardar o sentido evita ter que reinterpretar depois.
ALTER TABLE cartao_transacoes ADD COLUMN tipo TEXT DEFAULT 'compra';

-- Faturas fechadas, para conferir o total e a data de vencimento contra o que
-- o app calcula somando as transações.
CREATE TABLE IF NOT EXISTS cartao_faturas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fatura_id TEXT NOT NULL UNIQUE,
    cartao_id INTEGER NOT NULL,
    account_id TEXT,
    vencimento TEXT,
    fechamento TEXT,
    total REAL,
    minimo REAL,
    criado_em TEXT
);

CREATE INDEX IF NOT EXISTS idx_cartao_faturas_cartao
    ON cartao_faturas (cartao_id, vencimento);
