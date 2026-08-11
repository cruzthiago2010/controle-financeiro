-- Introduz o conceito de "casa": cada família/grupo de usuários fica isolado
-- dos demais (categorias próprias, contas visíveis só entre quem é da mesma
-- casa). Instalações que já tinham usuários ganham uma casa própria aqui —
-- nenhum dado muda de dono, só ganha essa organização por trás.
-- Instalações totalmente novas (sem nenhum usuário ainda) recebem a casa na
-- hora em que o primeiro usuário é criado (ver bootstrap_usuario_inicial).

CREATE TABLE IF NOT EXISTS casas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    consignados_habilitado INTEGER DEFAULT 0,
    criado_em TEXT
);

ALTER TABLE usuarios ADD COLUMN casa_id INTEGER;

INSERT INTO casas (nome, criado_em)
SELECT 'Minha Casa', datetime('now')
WHERE EXISTS (SELECT 1 FROM usuarios WHERE casa_id IS NULL)
  AND NOT EXISTS (SELECT 1 FROM casas);

UPDATE usuarios SET casa_id = (SELECT id FROM casas ORDER BY id LIMIT 1)
WHERE casa_id IS NULL;
