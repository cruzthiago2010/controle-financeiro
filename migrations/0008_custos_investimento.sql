-- Custo extra (corretagem, taxas etc.) de uma compra/venda de ativo com
-- ticker. Entra somado ao valor da compra e descontado do valor da venda,
-- então o preço médio e o que de fato mexeu na conta ficam mais fiéis.
ALTER TABLE investimento_operacoes ADD COLUMN custos_extras REAL DEFAULT 0;
