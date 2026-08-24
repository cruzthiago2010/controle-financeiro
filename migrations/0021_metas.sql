-- Metas com estado e ritmo.
--
-- A tabela só guardava nome, alvo, valor atual e prazo. Faltava o que responde
-- as perguntas que importam: essa meta ainda está de pé? estou no ritmo certo?

-- ativa | pausada | concluida | arquivada. Pausar é diferente de arquivar:
-- pausada continua na lista e volta quando você quiser; arquivada sai da vista
-- sem apagar o histórico.
ALTER TABLE metas ADD COLUMN estado TEXT NOT NULL DEFAULT 'ativa';

-- Quando foi concluída, para o histórico não virar só um "100%" sem data.
ALTER TABLE metas ADD COLUMN concluida_em TEXT;

-- Ícone e cor, para a lista não ser um paredão de cartões iguais.
ALTER TABLE metas ADD COLUMN icone TEXT;
ALTER TABLE metas ADD COLUMN cor TEXT;

-- Data em que a meta começou a valer. Sem ela não dá para dizer se o ritmo
-- está bom: guardar R$ 500 em um mês é diferente de guardar em seis.
ALTER TABLE metas ADD COLUMN inicio TEXT;

CREATE INDEX IF NOT EXISTS idx_metas_usuario_estado ON metas (usuario_id, estado);

-- Cada depósito feito na meta. Antes só existia o total, então não dava para
-- ver quando o dinheiro entrou nem desfazer um lançamento errado.
CREATE TABLE IF NOT EXISTS meta_depositos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meta_id INTEGER NOT NULL,
    valor REAL NOT NULL,
    data TEXT NOT NULL,
    observacao TEXT,
    usuario_id INTEGER NOT NULL,
    criado_em TEXT
);

CREATE INDEX IF NOT EXISTS idx_meta_depositos_meta ON meta_depositos (meta_id, data);
