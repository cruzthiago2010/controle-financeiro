-- Etiquetas livres para os lançamentos.
--
-- Categoria responde "que tipo de gasto é este" e existe uma por lançamento;
-- tag responde "a que isso pertence" e pode existir várias. São perguntas
-- diferentes, e por isso a tag não é uma segunda categoria: a viagem de julho
-- tem lançamento de Combustível, de Alimentação e de Lazer, e o que a pessoa
-- quer somar é a viagem.
--
-- Escopo de CASA, como as categorias, e não de usuário como os lançamentos:
-- quem divide a casa divide o vocabulário. Com escopo de usuário, duas pessoas
-- da mesma casa acabam criando "Viagem" duas vezes e nenhum filtro fecha.

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    cor TEXT,
    casa_id INTEGER,
    criado_em TEXT
);

-- NOCASE porque "Viagem" e "viagem" são a mesma etiqueta para quem digita, e o
-- objetivo declarado é reaproveitar em vez de acumular quase-duplicatas.
CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_casa_nome
    ON tags (casa_id, nome COLLATE NOCASE);

-- A ligação carrega `usuario_id` próprio, como meta_depositos: é o dono do
-- lançamento, e é o que permite conferir posse sem JOIN em toda consulta.
CREATE TABLE IF NOT EXISTS lancamento_tags (
    lancamento_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    usuario_id INTEGER,
    PRIMARY KEY (lancamento_id, tag_id)
);

-- O índice é pelo lado da TAG porque a consulta que ele serve é "quais
-- lançamentos têm esta etiqueta" (o filtro da tela). O outro sentido já é
-- coberto pela chave primária.
CREATE INDEX IF NOT EXISTS idx_lancamento_tags_tag
    ON lancamento_tags (tag_id, lancamento_id);
