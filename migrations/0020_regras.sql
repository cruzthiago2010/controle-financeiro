-- Regras de categorização automática.
--
-- O mapa de categorias da Pluggy (código) traduz o rótulo genérico que o banco
-- manda. A regra é mais específica: olha a DESCRIÇÃO da transação, que é onde
-- está o nome do estabelecimento. "UBER *TRIP" só vira Transporte por regra.
--
-- Por isso a ordem importa: regra primeiro, categoria da Pluggy como reserva.
CREATE TABLE IF NOT EXISTS regras_categoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    casa_id INTEGER NOT NULL,
    nome TEXT NOT NULL,
    -- Menor número roda primeiro. Regra específica ("Folha de pagamento")
    -- precisa vencer a genérica ("pagamento"), e prioridade é como se resolve
    -- isso sem depender da ordem de criação.
    prioridade INTEGER NOT NULL DEFAULT 10,
    -- JSON com a lista de condições. Em lista para permitir "descrição começa
    -- com UBER OU começa com 99", que é o caso real mais comum.
    condicoes TEXT NOT NULL,
    categoria TEXT NOT NULL,
    -- Marca a transação como transferência em vez de receita/despesa. Serve
    -- para PIX entre contas próprias, que move saldo mas não é ganho nem gasto.
    marca_transferencia INTEGER NOT NULL DEFAULT 0,
    ativa INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT
);

CREATE INDEX IF NOT EXISTS idx_regras_casa
    ON regras_categoria (casa_id, ativa, prioridade);

-- Quantas vezes a regra pegou e quando foi a última. Sem isso não dá para
-- saber se uma regra virou letra morta depois que o banco mudou a descrição.
ALTER TABLE regras_categoria ADD COLUMN vezes_aplicada INTEGER DEFAULT 0;
ALTER TABLE regras_categoria ADD COLUMN ultima_aplicacao TEXT;
