-- Redefinição de senha por link enviado ao e-mail.
--
-- Até aqui, quem esquecia a senha dependia do administrador da casa
-- (/api/usuarios/<id>/senha-admin). E o administrador é o usuário de menor id
-- da casa — ou seja, a casa cujo PRIMEIRO usuário esquecia a senha ficava sem
-- saída nenhuma. É esse buraco que isto fecha.

-- O token viaja em claro no e-mail e só o HASH fica aqui. Com o valor em claro
-- no banco, um backup vazado (e o app faz backup automático) viraria
-- chave-mestra de todas as contas.
--
-- SHA-256 e não scrypt de propósito: a busca é por igualdade e precisa ser
-- determinística, e o token tem 32 bytes aleatórios — não há dicionário que
-- ataque isso. Hash lento aqui só tornaria a rota lenta sem ganhar nada.
CREATE TABLE IF NOT EXISTS senha_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    criado_em TEXT NOT NULL,
    expira_em TEXT NOT NULL,
    -- Uso único. Pedir um link novo marca os anteriores como usados: sem isso,
    -- o link de uma semana atrás, esquecido numa caixa de entrada, continuaria
    -- valendo.
    usado_em TEXT,
    -- Só para investigar abuso depois. Não decide nada — cabeçalho é
    -- falsificável, e tratar isto como identidade seria confiar no atacante.
    ip TEXT
);

CREATE INDEX IF NOT EXISTS idx_senha_tokens_usuario
    ON senha_tokens (usuario_id, usado_em);

-- Quem entrou pelo Google nunca escolheu senha: o `senha_hash` dele é o hash de
-- um token aleatório que foi descartado na hora. Sem esta marca, a tela oferece
-- "trocar senha" e pede a senha atual — que não existe e nunca vai existir —,
-- e a pessoa fica sem caminho para poder entrar também por usuário e senha.
--
-- 1 como padrão porque todo usuário que já existe escolheu a própria senha; a
-- única exceção é criada daqui pra frente, pelo próprio fluxo do Google.
ALTER TABLE usuarios ADD COLUMN senha_definida INTEGER NOT NULL DEFAULT 1;

-- Redefinir a senha precisa derrubar as sessões abertas com a senha antiga —
-- senão quem roubou a senha continua dentro por 31 dias, que é a validade do
-- cookie. O cookie do Flask não tem revogação: a versão entra na sessão no
-- login e é conferida a cada requisição, então subir o número aqui invalida
-- todos os cookies daquele usuário de uma vez.
ALTER TABLE usuarios ADD COLUMN sessao_versao INTEGER NOT NULL DEFAULT 1;
