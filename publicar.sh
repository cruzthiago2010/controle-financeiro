#!/bin/sh
# Publica o código atual em produção reconstruindo a IMAGEM.
#
# Por que não `docker cp`: cp escreve só na camada do container, que morre
# quando o container é recriado — e ele é recriado sozinho no boot do
# servidor. Em 21/08/2026 um reboot devolveu a produção a uma imagem anterior
# ao módulo de Investimentos, apagando semanas de publicações. Com o build, o
# código passa a viver na imagem e sobrevive a reboot, `up -d` e recriação.
#
# O build leva ~15s: só as camadas COPY mudam, o resto vem do cache.
set -e
AQUI=$(dirname "$(readlink -f "$0")")
cd "$AQUI"

# O nome do serviço vem do docker-compose.yml; a porta é perguntada a ele em
# vez de ficar fixa aqui, senão trocar a porta no compose (o README ensina a
# fazer isso) faria a conferência abaixo falhar num deploy que deu certo.
SERVICO=${SERVICO:-controle-financeiro}
PORTA=$(docker compose port "$SERVICO" 5000 2>/dev/null | sed 's/.*://')
[ -n "$PORTA" ] || PORTA=8420

# O banco fica no volume ./data e não é tocado pelo build, mas uma cópia antes
# de mexer em produção custa 1s.
mkdir -p backups
[ -f data/orcamento.db ] && cp data/orcamento.db "backups/orcamento_pre_PUBLICACAO_$(date +%Y%m%d_%H%M%S).db"

# Retenção: sem isto cada publicação deixava mais uma cópia de ~650 KB para
# sempre. Em um dia de trabalho já eram 22 arquivos e 34 MB, e o disco é o do
# servidor de casa. Guarda as 10 mais recentes — as antigas não servem para
# nada que a mais nova não sirva melhor.
ls -1t backups/orcamento_pre_PUBLICACAO_*.db 2>/dev/null | tail -n +11 | while read -r velho; do
    rm -f "$velho"
done

docker compose up -d --build

printf 'aguardando a produção subir'
for _ in $(seq 1 30); do
    if [ "$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORTA/login")" = "200" ]; then
        echo " ok — http://localhost:$PORTA"
        # As migrations vão na imagem junto com o resto, mas conferir que o
        # banco aplicou é o que separa "subiu" de "funciona".
        docker compose exec -T "$SERVICO" python3 -c \
            "import sqlite3; print('migrations:', [r[0] for r in sqlite3.connect('/data/orcamento.db').execute('SELECT nome FROM schema_migrations ORDER BY id')][-1])"
        exit 0
    fi
    printf '.'
    sleep 2
done
echo " a produção não respondeu; veja: docker compose logs $SERVICO"
exit 1
