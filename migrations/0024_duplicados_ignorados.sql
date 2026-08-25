-- Pares que a pessoa já olhou e disse que NÃO são duplicados.
--
-- O detector de duplicados é um palpite, não um fato: dois Pix de mesmo valor
-- para a mesma pessoa no mesmo dia são indistinguíveis de um Pix lançado duas
-- vezes.
-- Sem memória do que já foi descartado o aviso voltaria em toda visita e a
-- pessoa aprenderia a ignorá-lo — que é o pior destino possível para um alerta.
--
-- A chave é a assinatura do grupo: os ids dos lançamentos envolvidos, em ordem
-- crescente, separados por hífen. Apagar qualquer um dos lançamentos desfaz a
-- assinatura, e aí o grupo simplesmente deixa de ser detectado — não é preciso
-- limpar esta tabela junto.
CREATE TABLE IF NOT EXISTS duplicados_ignorados (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL,
    assinatura TEXT NOT NULL,
    criado_em TEXT,
    UNIQUE (usuario_id, assinatura)
);
