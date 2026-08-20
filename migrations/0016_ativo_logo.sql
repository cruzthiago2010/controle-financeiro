-- Cache local da logo de cada ativo. Fica no banco (e não em static/) por dois
-- motivos: entra no backup automático junto com o resto, e o repositório é
-- público — logo de empresa é marca de terceiro e não deve ser redistribuída
-- num clone.
--
-- `tentado_em` guarda a última tentativa mesmo quando ela falha: boa parte dos
-- FIIs simplesmente não tem logo em lugar nenhum, e sem isso cada abertura da
-- aba tentaria baixar de novo o que já se sabe que não existe.
CREATE TABLE IF NOT EXISTS ativo_logo (
    chave TEXT PRIMARY KEY,       -- ticker (PETR4) ou id da CoinGecko (bitcoin)
    conteudo BLOB,                -- nulo quando a busca falhou
    tipo TEXT,                    -- content-type, pra devolver no cabeçalho
    atualizado_em TEXT,
    tentado_em TEXT NOT NULL
);

-- A CoinGecko já devolve a URL da imagem no mesmo endpoint do catálogo, então
-- dá pra guardar de graça em vez de descobrir depois.
ALTER TABLE ativo_catalogo ADD COLUMN logo_url TEXT;
