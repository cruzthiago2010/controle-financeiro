-- Um retrato por usuário por mês do patrimônio investido, pra dar pra
-- desenhar "Evolução do Patrimônio" sem inventar histórico. Recalculado a
-- cada ciclo do loop de cotação (1x/hora) — o registro do mês atual vai se
-- atualizando o dia inteiro, os meses passados ficam congelados assim que o
-- mês vira.
CREATE TABLE IF NOT EXISTS investimento_snapshot_mensal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL,
    mes TEXT NOT NULL,
    valor_investido REAL NOT NULL,
    valor_atual REAL NOT NULL,
    atualizado_em TEXT NOT NULL,
    UNIQUE(usuario_id, mes)
);
