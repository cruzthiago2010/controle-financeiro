-- Pagamento e encargos da fatura, que a Pluggy já entrega e o app ignorava.
--
-- O `fatura_paga` do cartão era marcado à mão. Com `payments[]` do /bills dá
-- para saber pelo banco se a fatura foi paga, quando e por qual meio — sem
-- depender de alguém lembrar de marcar.

-- Pago segundo o BANCO. Fica separado de qualquer marcação manual: é dado
-- observado, não intenção.
ALTER TABLE cartao_faturas ADD COLUMN pago INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cartao_faturas ADD COLUMN pago_em TEXT;
ALTER TABLE cartao_faturas ADD COLUMN pago_valor REAL;

-- DEBIT_ACCOUNT | BANK_SLIP | PAYROLL_DEDUCTION | PIX. Serve para explicar de
-- onde saiu o dinheiro quando o valor não bate com o extrato da conta.
ALTER TABLE cartao_faturas ADD COLUMN pago_modo TEXT;

-- FULL_PAYMENT | INSTALLMENT_PAYMENT | OTHER_PAYMENT. Pagar o mínimo e
-- parcelar o resto é diferente de quitar, e o app precisa saber a diferença
-- antes de dizer "fatura paga".
ALTER TABLE cartao_faturas ADD COLUMN pago_tipo TEXT;

-- Juros, multa e IOF cobrados na fatura. Somados aqui e detalhados no JSON:
-- hoje esses valores somem no meio das transações e ninguém vê quanto custou
-- ter atrasado.
ALTER TABLE cartao_faturas ADD COLUMN encargos REAL NOT NULL DEFAULT 0;
ALTER TABLE cartao_faturas ADD COLUMN encargos_detalhe TEXT;

-- Mínimo já existia como coluna; falta saber se a fatura permite parcelar.
ALTER TABLE cartao_faturas ADD COLUMN permite_parcelar INTEGER;
