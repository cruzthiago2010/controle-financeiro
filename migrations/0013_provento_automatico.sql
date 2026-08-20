-- Marca se a operação foi digitada pelo usuário ou importada sozinha do
-- histórico de dividendos da brapi.dev — só pra mostrar na tela de onde
-- veio, não muda nenhum cálculo.
ALTER TABLE investimento_operacoes ADD COLUMN origem TEXT DEFAULT 'manual';
