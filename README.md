# Betting — футбольные прогнозы

Статический сайт (HTML/CSS/JS) для GitHub Pages. GitHub Actions раз в 3 часа получает данные BSD и SStats.net и обновляет `data/forecasts.json`. Токены не попадают в клиентский код.

## Подключение

1. Добавьте в **Settings → Secrets and variables → Actions → New repository secret** два секрета: `BSD_TOKEN` и `SSTATS_TOKEN`.
2. В **Settings → Actions → General → Workflow permissions** разрешите **Read and write permissions**.
3. Откройте **Settings → Pages**, выберите **Deploy from a branch**, ветку `main`, папку `/(root)`, сохраните.
4. Откройте **Actions → Update football forecasts → Run workflow** и запустите обновление вручную.

Сайт: https://he-sytikova.github.io/betting/ (после включения Pages и завершения первого workflow).

## Важно про рынки

Наличие каждого конкретного рынка зависит от ответа API. SStats Glicko — это модельная оценка, а не гарантированно готовые рынки П1/П2/1Х/2Х или угловых. Интерфейс показывает только значения, которые удаётся найти в данных; тире означает, что значение не получено. Формат ответов SStats может отличаться по аккаунту/версии API — проверьте первый запуск Actions и при необходимости скорректируйте `scripts/update_data.py` по фактическому JSON.

## API

- BSD Football API: `https://sports.bzzoiro.com/api/v2/events/`, `https://sports.bzzoiro.com/api/v2/predictions/`; авторизация `Authorization: Token ...`.
- SStats: `https://api.sstats.net/Games/list`, `https://api.sstats.net/Games/glicko/{id}`; ключ `apikey`.
