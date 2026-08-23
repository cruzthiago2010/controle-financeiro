-- Integração com o Open Finance via Pluggy.
--
-- A credencial é POR CASA, e não uma do app inteiro, por causa da licença: o
-- plano gratuito ("Meu Pluggy") vale só para uso pessoal e para contas no
-- próprio nome de quem cadastrou. Uma credencial central servindo todas as
-- casas seria uso comercial, que exige o plano pago.
CREATE TABLE IF NOT EXISTS pluggy_credenciais (
    casa_id INTEGER PRIMARY KEY,
    client_id TEXT NOT NULL,
    -- Cifrado com a chave em /data/.pluggy_key. Guardar em claro deixaria o
    -- acesso ao Open Finance de quem cadastrou dentro de um backup comum.
    client_secret_cifrado TEXT NOT NULL,
    criado_em TEXT,
    atualizado_em TEXT
);

-- Um "Item" na Pluggy é uma conexão com um banco. Um banco pode expor várias
-- contas, então Item e conta são coisas separadas.
CREATE TABLE IF NOT EXISTS pluggy_itens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL UNIQUE,
    casa_id INTEGER NOT NULL,
    usuario_id INTEGER NOT NULL,
    conector_id INTEGER,
    conector_nome TEXT,
    conector_logo TEXT,
    -- UPDATED, LOGIN_ERROR, WAITING_USER_INPUT... O consentimento do Open
    -- Finance expira, então o item precisa saber dizer que pede reconexão.
    status TEXT,
    status_detalhe TEXT,
    ultimo_sync TEXT,
    criado_em TEXT
);

-- Conta ou cartão que veio de um Item. O vínculo com o FinanCerto é opcional
-- e feito à mão: enquanto conta_id/cartao_id forem nulos, a conta existe mas
-- não alimenta nada — evita importar sozinho para o lugar errado.
CREATE TABLE IF NOT EXISTS pluggy_contas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL UNIQUE,
    item_id TEXT NOT NULL,
    casa_id INTEGER NOT NULL,
    usuario_id INTEGER NOT NULL,
    -- BANK ou CREDIT. Poupança vem como BANK com o subtipo dizendo SAVINGS,
    -- então o subtipo precisa ser guardado junto: só o tipo mostraria uma
    -- poupança como conta corrente.
    tipo TEXT,
    subtipo TEXT,
    nome TEXT,
    numero TEXT,
    saldo REAL,
    moeda TEXT,
    conta_id INTEGER,
    cartao_id INTEGER,
    ignorada INTEGER DEFAULT 0,
    criado_em TEXT
);

-- Área de conferência: transação que chegou do banco mas ainda NÃO virou
-- lançamento. Nada entra no financeiro sem alguém aprovar — o extrato
-- completo caindo por cima de meses de lançamento manual duplicaria tudo.
CREATE TABLE IF NOT EXISTS pluggy_transacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- O id da Pluggy é único e é ele que impede reimportar a mesma transação
    -- quando o sync roda de novo ou o webhook repete.
    transacao_id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    casa_id INTEGER NOT NULL,
    usuario_id INTEGER NOT NULL,
    descricao TEXT,
    valor REAL NOT NULL,
    tipo TEXT,
    data TEXT,
    categoria_pluggy TEXT,
    situacao TEXT,
    -- Parcelamento que o próprio cartão informa, para virar grupo_parcela.
    parcela_num INTEGER,
    parcela_total INTEGER,
    compra_em TEXT,
    fatura_id TEXT,
    -- pendente | aprovada | ignorada | conciliada
    estado TEXT NOT NULL DEFAULT 'pendente',
    lancamento_id INTEGER,
    criado_em TEXT
);

CREATE INDEX IF NOT EXISTS idx_pluggy_transacoes_conta
    ON pluggy_transacoes (account_id, estado);
CREATE INDEX IF NOT EXISTS idx_pluggy_transacoes_casa
    ON pluggy_transacoes (casa_id, estado);

-- Aprendido conforme a pessoa concilia: da segunda vez em diante a categoria
-- da casa já vem sugerida, em vez de perguntar sempre a mesma coisa.
CREATE TABLE IF NOT EXISTS pluggy_categoria_mapa (
    casa_id INTEGER NOT NULL,
    categoria_pluggy TEXT NOT NULL,
    categoria TEXT NOT NULL,
    PRIMARY KEY (casa_id, categoria_pluggy)
);
