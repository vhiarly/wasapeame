#!/bin/bash
set -e

APP="wasapeame"
RG="wasapeame-rg"
URL="https://wasapeame.co/ping"
ZIP="/tmp/wasapeame_deploy.zip"
ESPERADO="¡El servidor está vivo!"

# ── Colores ──────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓ $1${NC}"; }
fail() { echo -e "${RED}✗ $1${NC}"; exit 1; }
info() { echo -e "${YELLOW}→ $1${NC}"; }

# ── 1. Verificar login Azure ─────────────────────────────────────────────
info "Verificando sesión Azure..."
az account show --query name -o tsv > /dev/null 2>&1 || fail "No estás logueado en Azure. Corre: az login"
ok "Azure autenticado"

# ── 2. Mostrar commit que se va a deployar ───────────────────────────────
COMMIT=$(git log --oneline -1)
info "Deployando: $COMMIT"

# ── 3. Crear zip ─────────────────────────────────────────────────────────
info "Creando zip..."
rm -f "$ZIP"
zip -r "$ZIP" . \
  -x "*.git*" \
  -x ".DS_Store" \
  -x ".env" \
  -x "__pycache__/*" \
  -x "*.pyc" \
  -x "*.sh" \
  -x "deploy.sh" \
  -x "tunnel.py" \
  -x "crear-test.js" \
  -x "limpiar_estado.py" \
  -x "fix_se2_numero.py" \
  -x "update_se1_lugares.py" \
  -x "create_se2_test.py" \
  -x "test_confirmacion_se1.py" \
  > /dev/null
ok "Zip creado ($(du -sh $ZIP | cut -f1))"

# ── 4. Deploy ────────────────────────────────────────────────────────────
info "Subiendo a Azure App Service..."
az webapp deploy \
  --name "$APP" \
  --resource-group "$RG" \
  --src-path "$ZIP" \
  --type zip \
  --timeout 300 \
  --output none
ok "Deploy completado"

# ── 5. Verificar que el app levantó ─────────────────────────────────────
info "Esperando que el app reinicie..."
for i in {1..12}; do
  sleep 5
  RESP=$(curl -sk --max-time 5 "$URL" 2>/dev/null || true)
  if [ "$RESP" = "$ESPERADO" ]; then
    ok "App respondiendo en $URL"
    break
  fi
  echo "   intento $i/12..."
  if [ $i -eq 12 ]; then
    fail "El app no responde después de 60s. Revisa Azure Portal."
  fi
done

# ── 6. Limpieza ──────────────────────────────────────────────────────────
rm -f "$ZIP"
echo ""
echo -e "${GREEN}Deploy exitoso ✓${NC}"
echo -e "Commit: ${COMMIT}"
echo -e "URL:    https://wasapeame.co"
