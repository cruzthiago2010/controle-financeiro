-- Quanto (%) cada classe de investimento deveria pesar na carteira, pra
-- comparar com o % atual e sugerir "comprar" ou não. Só um alvo, não uma
-- trava — não precisa somar 100%.
CREATE TABLE IF NOT EXISTS investimento_alocacao_ideal (
    usuario_id INTEGER NOT NULL,
    classe TEXT NOT NULL,
    percentual REAL NOT NULL,
    PRIMARY KEY (usuario_id, classe)
);
