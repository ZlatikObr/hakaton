#!/bin/bash
# Подключает почту quantara-lab@yandex.ru для кодов входа. Пароль приложения вводится скрыто
# и записывается только в /root/hak_mipt/.env (права 600), в историю команд не попадает.
set -euo pipefail
cd /root/hak_mipt
MAIL=quantara-lab@yandex.ru
echo "Проверяем, открыт ли у хостинга исходящий порт 465..."
if ! timeout 8 bash -c "echo > /dev/tcp/smtp.yandex.ru/465" 2>/dev/null; then
  echo "Порт 465 закрыт хостингом. Попросите поддержку открыть исходящие порты 465 и 587, затем запустите скрипт снова."
  exit 1
fi
read -r -s -p "Пароль приложения Яндекса для $MAIL (ввод не отображается): " PASS; echo
[ -n "$PASS" ] || { echo "Пароль пустой, ничего не изменено."; exit 1; }
echo "Проверяем вход в почту..."
if ! docker exec -i hak_mipt-audit-1 python -c "
import smtplib,sys
p=sys.stdin.read().strip()
with smtplib.SMTP_SSL('smtp.yandex.ru',465,timeout=15) as s: s.login('$MAIL',p)
" <<< "$PASS"; then
  echo "Яндекс не принял пароль. Нужен именно пароль приложения (Яндекс ID → Безопасность → Пароли приложений → Почта). Ничего не изменено."
  exit 1
fi
cp .env .env.bak-before-mail
grep -vE '^(SMTP_HOST|SMTP_PORT|SMTP_USER|SMTP_PASSWORD|SMTP_FROM|TZ_AUTH_DEMO)=' .env.bak-before-mail > .env.new || true
{ echo "SMTP_HOST=smtp.yandex.ru"; echo "SMTP_PORT=465"; echo "SMTP_USER=$MAIL"; printf 'SMTP_PASSWORD=%s\n' "$PASS"; echo "SMTP_FROM=$MAIL"; echo "TZ_AUTH_DEMO=false"; } >> .env.new
chmod 600 .env.new && chown 1000:1000 .env.new && mv .env.new .env
unset PASS
echo "Перезапускаем сервис..."
docker compose -f compose.yaml -f compose.domain.yaml up -d --force-recreate >/dev/null 2>&1
for i in $(seq 1 30); do sleep 3; docker ps --filter name=hak_mipt-audit-1 --format '{{.Status}}' | grep -q '(healthy)' && { echo "Готово: коды входа теперь приходят на почту. Откат: cp .env.bak-before-mail .env и перезапуск."; exit 0; }; done
echo "Сервис не поднялся за 90 секунд. Возвращаем прежние настройки."; cp .env.bak-before-mail .env
docker compose -f compose.yaml -f compose.domain.yaml up -d --force-recreate >/dev/null 2>&1; exit 1
