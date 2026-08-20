-- Detalhes de renda fixa: emissor (ex: "Banco Inter", "Tesouro Nacional"),
-- tipo de investimento (CDB, LCI, Tesouro Selic etc — só rótulo, o cálculo
-- de rendimento continua vindo de indexador+taxa) e se tem liquidez diária.
ALTER TABLE investimentos ADD COLUMN emissor TEXT;
ALTER TABLE investimentos ADD COLUMN tipo_investimento TEXT;
ALTER TABLE investimentos ADD COLUMN liquidez_diaria INTEGER DEFAULT 0;
