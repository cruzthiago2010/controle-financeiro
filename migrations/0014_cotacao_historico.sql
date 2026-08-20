-- Preço de fechamento mês a mês de cada ticker, pra reconstruir a evolução do
-- patrimônio de antes de o app existir. Sem isso o gráfico "Evolução do
-- Patrimônio" só teria o mês em que o módulo entrou no ar (uma barra só) e a
-- rentabilidade comparada com o CDI ficaria sem série nenhuma.
CREATE TABLE IF NOT EXISTS investimento_cotacao_historico (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chave TEXT NOT NULL,          -- mesmo identificador de investimento_cotacoes (ticker ou USD_BRL)
    mes TEXT NOT NULL,            -- AAAA-MM
    valor REAL NOT NULL,          -- fechamento do mês, na moeda de origem do ativo
    atualizado_em TEXT NOT NULL,
    UNIQUE(chave, mes)
);

CREATE INDEX IF NOT EXISTS idx_cotacao_historico_chave ON investimento_cotacao_historico(chave, mes);
