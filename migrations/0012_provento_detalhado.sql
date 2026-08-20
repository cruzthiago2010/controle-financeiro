-- Detalhe de cada provento: tipo de pagamento (dividendo/jscp/rendimento —
-- só JSCP tem 15% de IR retido na fonte), Data Com (opcional, data-base do
-- direito ao provento) e o valor bruto antes do imposto (o `valor` da
-- tabela já é o líquido, que é o que de fato caiu na conta).
ALTER TABLE investimento_operacoes ADD COLUMN tipo_pagamento TEXT;
ALTER TABLE investimento_operacoes ADD COLUMN data_com TEXT;
ALTER TABLE investimento_operacoes ADD COLUMN valor_bruto REAL;
