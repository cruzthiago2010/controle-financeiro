#!/usr/bin/env bash
#
# Instalador do FinanCerto — https://github.com/financerto/controle-financeiro
#
#   curl -fsSL https://raw.githubusercontent.com/financerto/controle-financeiro/main/install.sh | bash
#
# O que ele faz: confere se o Docker está instalado (e instala no Linux se não
# estiver), clona o repositório, cria um .env com senha e chave de sessão
# sorteadas na hora, sobe o container e espera o app responder.
#
# Variáveis que você pode passar antes do comando:
#   PORTA=8421      porta do host (padrão 8420)
#   PASTA=~/apps    onde clonar (padrão: a pasta atual)
#   BRANCH=main     branch a clonar

set -euo pipefail

REPO_URL="https://github.com/financerto/controle-financeiro.git"
PORTA="${PORTA:-8420}"
BRANCH="${BRANCH:-main}"
PASTA="${PASTA:-$PWD}"
DESTINO="$PASTA/controle-financeiro"

# ── Cores ────────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  AZUL='\033[0;34m'; VERDE='\033[0;32m'; AMARELO='\033[1;33m'; VERMELHO='\033[0;31m'; FIM='\033[0m'
else
  AZUL=''; VERDE=''; AMARELO=''; VERMELHO=''; FIM=''
fi
info()  { echo -e "${AZUL}[INFO]${FIM} $*"; }
ok()    { echo -e "${VERDE}[OK]${FIM} $*"; }
aviso() { echo -e "${AMARELO}[AVISO]${FIM} $*"; }
erro()  { echo -e "${VERMELHO}[ERRO]${FIM} $*" >&2; exit 1; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 && SUDO="sudo"
fi

# ── Sistema ──────────────────────────────────────────────────────────────────
detectar_so() {
  case "$(uname -s)" in
    Linux*)  SO="linux" ;;
    Darwin*) SO="macos" ;;
    *) erro "Sistema não suportado por este script: $(uname -s). No Windows, instale o Docker Desktop e siga o README." ;;
  esac
}

# ── Docker ───────────────────────────────────────────────────────────────────
instalar_docker_linux() {
  aviso "Docker não encontrado. Vou instalar pelo script oficial (get.docker.com)."
  command -v curl >/dev/null 2>&1 || erro "curl não está instalado; instale-o e rode de novo."
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  $SUDO sh /tmp/get-docker.sh
  rm -f /tmp/get-docker.sh
  $SUDO systemctl enable --now docker 2>/dev/null || true
  if [ -n "$SUDO" ]; then
    $SUDO usermod -aG docker "$USER" 2>/dev/null || true
    aviso "Você foi adicionado ao grupo 'docker'. Saia e entre de novo na sessão para usar o docker sem sudo."
  fi
  ok "Docker instalado."
}

resolver_compose() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
  else
    erro "Docker Compose não encontrado. Instale o plugin 'docker-compose-plugin' e rode de novo."
  fi
}

garantir_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    [ "$SO" = "linux" ] || erro "Docker não encontrado. No macOS, instale o Docker Desktop: https://www.docker.com/products/docker-desktop/"
    instalar_docker_linux
  fi
  if ! docker info >/dev/null 2>&1; then
    if [ -n "$SUDO" ] && $SUDO docker info >/dev/null 2>&1; then
      DOCKER_SUDO="$SUDO"
      aviso "Seu usuário ainda não está no grupo 'docker'; usando sudo nesta instalação."
    else
      erro "O Docker está instalado mas não responde. Confira se o serviço (ou o Docker Desktop) está rodando."
    fi
  fi
  resolver_compose
  [ -n "${DOCKER_SUDO:-}" ] && COMPOSE="$DOCKER_SUDO $COMPOSE"
  ok "Docker pronto ($(${DOCKER_SUDO:-} docker --version 2>/dev/null || echo docker))."
}

# ── Repositório ──────────────────────────────────────────────────────────────
baixar_repo() {
  # Se o script foi chamado de dentro do próprio repositório, usa a pasta atual.
  if [ -f "$PWD/app.py" ] && [ -f "$PWD/docker-compose.yml" ]; then
    DESTINO="$PWD"
    info "Já estou dentro do repositório; usando $DESTINO."
    return
  fi
  command -v git >/dev/null 2>&1 || erro "git não está instalado; instale-o e rode de novo."
  if [ -d "$DESTINO/.git" ]; then
    info "Repositório já existe em $DESTINO; atualizando."
    git -C "$DESTINO" pull --ff-only origin "$BRANCH" || aviso "Não consegui atualizar; seguindo com o que já está no disco."
  else
    mkdir -p "$PASTA"
    info "Clonando em $DESTINO."
    git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$DESTINO"
  fi
}

# ── .env ─────────────────────────────────────────────────────────────────────
sortear() {
  n="${1:-32}"
  # Ler /dev/urandom direto no tr e cortar com `head -c` fecha o pipe no meio:
  # o tr morre com SIGPIPE, e o `set -o pipefail` lá em cima aborta a instalação
  # inteira ("tr: write error: Broken pipe"). Lendo um bloco de tamanho fixo
  # antes, todo mundo termina sozinho e ninguém leva sinal. 16 bytes por
  # caractere pedido sobra: cerca de um quarto do sorteio cai em [A-Za-z0-9].
  head -c "$((n * 16))" /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9' | cut -c1-"$n"
}

gerar_env() {
  if [ -f "$DESTINO/.env" ]; then
    info ".env já existe; mantendo o seu (nada foi sobrescrito)."
    SENHA_GERADA=""
    return
  fi
  SENHA_GERADA="$(sortear 20)"
  CHAVE="$(sortear 48)"
  cp "$DESTINO/.env.example" "$DESTINO/.env"
  # Preenche só as duas variáveis obrigatórias; o resto fica como está no exemplo.
  sed -i.bak \
    -e "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$SENHA_GERADA|" \
    -e "s|^SECRET_KEY=.*|SECRET_KEY=$CHAVE|" \
    "$DESTINO/.env"
  rm -f "$DESTINO/.env.bak"
  chmod 600 "$DESTINO/.env"
  ok ".env criado com senha e chave de sessão sorteadas."
}

ajustar_porta() {
  [ "$PORTA" = "8420" ] && return
  sed -i.bak "s|\"8420:5000\"|\"$PORTA:5000\"|" "$DESTINO/docker-compose.yml"
  rm -f "$DESTINO/docker-compose.yml.bak"
  info "Porta trocada para $PORTA."
}

# ── Subir ────────────────────────────────────────────────────────────────────
subir() {
  info "Construindo a imagem e subindo o container (pode demorar alguns minutos na primeira vez)."
  ( cd "$DESTINO" && $COMPOSE up -d --build )
}

esperar() {
  info "Esperando o app responder em http://localhost:$PORTA ..."
  for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null "http://localhost:$PORTA/" 2>/dev/null; then
      ok "O FinanCerto está no ar."
      return 0
    fi
    sleep 2
  done
  aviso "O app ainda não respondeu depois de 2 minutos. Veja o log com:"
  echo "    cd $DESTINO && $COMPOSE logs -f"
  return 0
}

# ── Fim ──────────────────────────────────────────────────────────────────────
resumo() {
  IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo
  echo "────────────────────────────────────────────────────────────"
  ok "Instalação concluída."
  echo
  echo "  Endereço:  http://localhost:$PORTA"
  [ -n "${IP:-}" ] && echo "             http://$IP:$PORTA  (de outro aparelho da sua rede)"
  echo "  Usuário:   admin"
  if [ -n "$SENHA_GERADA" ]; then
    echo "  Senha:     $SENHA_GERADA"
    echo
    echo "  Guarde essa senha agora — ela também está no arquivo $DESTINO/.env,"
    echo "  e você pode trocá-la depois dentro do app."
  else
    echo "  Senha:     a que já estava no seu .env"
  fi
  echo
  echo "  Seus dados ficam em $DESTINO/data/ — fora do container, então"
  echo "  atualizar o app não apaga nada."
  echo "────────────────────────────────────────────────────────────"
}

main() {
  echo
  echo "  FinanCerto — instalação"
  echo
  detectar_so
  garantir_docker
  baixar_repo
  gerar_env
  ajustar_porta
  subir
  esperar
  resumo
}

main "$@"
