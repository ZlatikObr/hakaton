# Адаптер DeepSeek Harness

Основной работающий runner — `tz_audit.agent.Agent`. Этот дополнительный native plugin даёт внешнему агенту DeepSeek Harness один инструмент `run_procurement_review`. Он запускает тот же ограниченный pipeline через локальный API. Это **двухуровневая оркестрация**, а не замена Python agent loop на внутренний loop DSH.

```sh
cd integrations/deepseek_harness
npm ci --ignore-scripts
npm test
```

Проверена компиляция схемы реальным опубликованным `@deepseek-ai/dsh-tools@0.0.1-rc.1` и контракт адаптера. Полный запуск приложения DSH не проверен. Текущий репозиторий DSH на момент реализации сообщает `0.2.2-rc.2` и Node >=24.9; npm-пакет tools доступен в версии `0.0.1-rc.1`. Не смешивайте вслепую версии хоста и SDK; проверьте совместимость в конкретной сборке.

Подключение из host-кода при создании **отдельного agent scope**:

```js
import { apply } from './adapter.mjs';
// agentCtx — scoped context вашего выделенного DSH agent.
apply(agentCtx, {
  baseUrl: 'http://127.0.0.1:8000',
  token: process.env.TZ_API_TOKEN,
  jobId: approvedJobId,
  approvedDigest: locallyReviewedDigest,
});
```

`approvedJobId` и `locallyReviewedDigest` назначает локальный host после просмотра `/jobs/{id}`. Они не являются аргументами model tool. Внешняя DSH-модель видит только `complete`/код ошибки и количество замечаний. Отчёт, тексты, пути и ключи ей не возвращаются. Инструмент выполняется не более одного раза на экземпляр плагина.

Нужно подключать native tool executor и запрещать shell, filesystem, browser, MCP, telemetry и сторонние плагины в этом агенте. `restrict` и монотонный `guard` запрещают другие инструменты в scope, но не превращают произвольный DSH host в OS sandbox. Исходники и vault должны быть доступны только Python backend. Не прикладывайте Word или закрытые тексты к внешнему DSH-чату. Отмена HTTP-запроса плагина не останавливает уже начавшийся backend job; он завершится в пределах собственных лимитов.

Источники API: [tools](https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/core/tools), [agent](https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/core/agent).
