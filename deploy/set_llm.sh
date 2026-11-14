#!/bin/bash
# Переключает модель на AITunnel (OpenAI-совместимый API). Ключ вводится скрыто, пишется только в .env.
set -euo pipefail
cd /root/hak_mipt
BASE=https://api.aitunnel.ru/v1
read -r -s -p "API-ключ AITunnel (ввод не отображается): " KEY; echo
[ -n "$KEY" ] || { echo "Пусто, ничего не изменено."; exit 1; }
echo "Проверяем ключ и ищем модель Kimi K3.1 Pro..."
MODELS=$(curl -sf -m 20 -H "Authorization: Bearer $KEY" $BASE/models) || { echo "AITunnel не принял ключ или недоступен. Ничего не изменено."; exit 1; }
NAME=$(echo "$MODELS" | grep -oE '"id" *: *"[^"]*kimi[^"]*k3\.?1[^"]*pro[^"]*"' | head -1 | sed -E 's/.*"([^"]+)"$/\1/')
[ -n "$NAME" ] || { echo "Модель Kimi K3.1 Pro не найдена. Доступные kimi:"; echo "$MODELS" | grep -oE '"id" *: *"[^"]*kimi[^"]*"'; exit 1; }
echo "Модель: $NAME"
cp .env .env.bak-before-aitunnel
grep -vE '^(LLM_API_KEY|LLM_API_URL|LLM_MODEL_NAME|TZ_SEARCH_BACKEND)=' .env.bak-before-aitunnel > .env.new || true
{ printf 'LLM_API_KEY=%s\n' "$KEY"; echo "LLM_API_URL=$BASE/chat/completions"; echo "LLM_MODEL_NAME=$NAME"; echo "TZ_SEARCH_BACKEND=duckduckgo"; } >> .env.new
chmod 600 .env.new && chown 1000:1000 .env.new && mv .env.new .env; unset KEY
docker compose -f compose.yaml -f compose.domain.yaml up -d --force-recreate >/dev/null 2>&1
for i in $(seq 1 30); do sleep 3; docker ps --filter name=hak_mipt-audit-1 --format '{{.Status}}' | grep -q '(healthy)' && { echo "Готово: сервис ходит в AITunnel ($NAME). Откат: cp .env.bak-before-aitunnel .env и перезапуск."; exit 0; }; done
echo "Сервис не поднялся, возвращаем прежние настройки."; cp .env.bak-before-aitunnel .env; docker compose -f compose.yaml -f compose.domain.yaml up -d --force-recreate >/dev/null 2>&1; exit 1
