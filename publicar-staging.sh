#!/bin/sh
# Copia o código atual para dentro do container de staging e reinicia.
#
# Existe porque `docker compose up -d` no staging recria o container a partir
# da imagem construída antes, desfazendo qualquer `docker cp` anterior — e
# esquecer de copiar as migrations junto já derrubou a aba de investimentos
# com "no such column". Aqui os três vão sempre juntos.
set -e
AQUI=$(dirname "$(readlink -f "$0")")
C=controle-financeiro-staging

docker cp "$AQUI/app.py" "$C:/app/app.py"
docker cp "$AQUI/static/." "$C:/app/static/"
docker cp "$AQUI/migrations/." "$C:/app/migrations/"
docker restart "$C" >/dev/null

printf 'aguardando o staging subir'
for _ in $(seq 1 30); do
    if [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8421/login)" = "200" ]; then
        echo " ok — http://localhost:8421"
        exit 0
    fi
    printf '.'
    sleep 2
done
echo " o staging não respondeu; veja: docker logs $C"
exit 1
