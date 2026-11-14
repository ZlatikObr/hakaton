#!/bin/bash
# Подключает отправку кодов входа по почте. Секретный ключ вводится скрыто и записывается
# только в /root/hak_mipt/.env (права 600), в историю команд не попадает.
#   Яндекс напрямую (если хостинг откроет порт 465):  ./enable_mail.sh
#   Через сервис рассылки на порту 2525, например Brevo:
#     ./enable_mail.sh smtp-relay.brevo.com 2525 <логин SMTP из Brevo> noreply@planwithai.online
set -euo pipefail
cd /root/hak_mipt
HOST=${1:-smtp.yandex.ru}; PORT=${2:-465}; USER_NAME=${3:-quantara-lab@yandex.ru}; FROM=${4:-$USER_NAME}
echo "Проверяем соединение с $HOST:$PORT..."
if ! timeout 8 bash -c "echo > /dev/tcp/$HOST/$PORT" 2>/dev/null; then
  echo "Порт $PORT до $HOST закрыт. Для Яндекса нужна поддержка хостинга; сервисы рассылки работают через порт 2525."
  exit 1
fi
read -r -s -p "Пароль/ключ SMTP для $USER_NAME (ввод не отображается): " PASS; echo
[ -n "$PASS" ] || { echo "Пусто, ничего не изменено."; exit 1; }
echo "Проверяем вход в почтовый сервер..."
if ! docker exec -i -e H="$HOST" -e P="$PORT" -e U="$USER_NAME" hak_mipt-audit-1 python -c "
import os,smtplib,sys
pw=sys.stdin.read().strip(); h,p,u=os.environ['H'],int(os.environ['P']),os.environ['U']
if p==465:
    s=smtplib.SMTP_SSL(h,p,timeout=15)
else:
    s=smtplib.SMTP(h,p,timeout=15); s.starttls()
s.login(u,pw); s.quit()
" <<< "$PASS"; then
  echo "Сервер не принял логин или пароль. Ничего не изменено."
  exit 1
fi
cp .env .env.bak-before-mail
grep -vE '^(SMTP_HOST|SMTP_PORT|SMTP_USER|SMTP_PASSWORD|SMTP_FROM|TZ_AUTH_DEMO)=' .env.bak-before-mail > .env.new || true
{ echo "SMTP_HOST=$HOST"; echo "SMTP_PORT=$PORT"; echo "SMTP_USER=$USER_NAME"; printf 'SMTP_PASSWORD=%s\n' "$PASS"; echo "SMTP_FROM=$FROM"; echo "TZ_AUTH_DEMO=false"; } >> .env.new
chmod 600 .env.new && chown 1000:1000 .env.new && mv .env.new .env
unset PASS
echo "Перезапускаем сервис..."
docker compose -f compose.yaml -f compose.domain.yaml up -d --force-recreate >/dev/null 2>&1
for i in $(seq 1 30); do sleep 3; docker ps --filter name=hak_mipt-audit-1 --format '{{.Status}}' | grep -q '(healthy)' && { echo "Готово: коды входа отправляются с $FROM. Откат: cp .env.bak-before-mail .env и перезапуск."; exit 0; }; done
echo "Сервис не поднялся за 90 секунд. Возвращаем прежние настройки."; cp .env.bak-before-mail .env
docker compose -f compose.yaml -f compose.domain.yaml up -d --force-recreate >/dev/null 2>&1; exit 1
