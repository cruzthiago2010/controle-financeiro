-- Símbolo curto da cripto (BTC) ao lado do id da CoinGecko (bitcoin): o
-- histórico longo de preço vem do Yahoo, que pede o par "BTC-USD", enquanto
-- a CoinGecko gratuita só devolve os últimos 365 dias.
ALTER TABLE ativo_catalogo ADD COLUMN simbolo TEXT;
