-- Lançamentos individuais de cartão de crédito. Ficam sempre isolados da
-- tabela lancamentos (nunca aparecem na lista de despesas do mês, no
-- dashboard ou nos totais) — só são exibidos dentro da aba Cartões,
-- agrupados por cartão. O total de cada cartão (cartoes.fatura_atual)
-- passa a ser a soma dessas transações.
CREATE TABLE IF NOT EXISTS cartao_transacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cartao_id INTEGER NOT NULL,
    descricao TEXT NOT NULL,
    valor REAL NOT NULL,
    data TEXT,
    categoria TEXT,
    usuario_id INTEGER,
    criado_em TEXT
);
