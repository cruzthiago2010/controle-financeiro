-- Login com Google: cada usuário pode (opcionalmente) estar vinculado a uma
-- conta Google, guardada pelo "sub" (ID estável do Google, nunca muda mesmo
-- se o e-mail mudar). Usuários que só usam usuário/senha continuam com
-- google_id NULL normalmente.

ALTER TABLE usuarios ADD COLUMN google_id TEXT;
ALTER TABLE usuarios ADD COLUMN email TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_usuarios_google_id
    ON usuarios (google_id) WHERE google_id IS NOT NULL;
