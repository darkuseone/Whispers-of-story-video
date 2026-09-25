# Заливка длинного ролика на YouTube (private)

После сборки `build.yml` сам заливает `final.mp4` на канал
**ANCIENT WHISPERS of HISTORY** как **private** — с названием, описанием
с главами, тегами, категорией Education, обложкой `cover_1.jpg` и
английскими субтитрами. Сразу после выкладки на Google Drive.

- Ролик **всегда private**. Публикуешь ты сам, кнопкой в Studio, после
  просмотра. Из API публичного статуса не бывает намеренно.
- **Шортсы** этим путём не заливаются — их по-прежнему руками или через
  Buffer (файлы лежат в релизе и в папке на Drive).
- Видео идёт с раннера GitHub прямо в YouTube Data API
  (`pipeline/youtube_upload.py`), через чат/MCP ни байта — токены модели
  не тратятся.
- Пока секретов нет, шаг пишет «пропуск: нет YT_* секретов» и сборке не
  мешает. Если YouTube сорвался — Drive и релиз на месте, в Summary
  красная строка, перезалить: Actions → **Upload to YouTube**.

## Разовая настройка (15 минут, можно с телефона)

Проект Google Cloud — **тот же**, что для Google Drive
(`docs/google-drive.md`), второй заводить не нужно. Секреты — отдельные.

1. **API.** https://console.cloud.google.com → тот же проект →
   APIs & Services → Library → «**YouTube Data API v3**» → Enable.
2. **Экран согласия** уже настроен для Drive. Проверь, что он в
   **In production** (Publish app). В режиме Testing refresh token живёт
   7 дней — через неделю заливка молча начнёт падать с `invalid_grant`.
   Предупреждение «приложение не проверено» при входе пропускаешь:
   Advanced → Go to …
3. **Клиент.** Можно взять тот же OAuth-клиент, что для Drive (Client ID
   и Secret совпадут), или создать отдельный. Для входа с телефона нужен
   клиент типа **Web application** с redirect URI
   `https://developers.google.com/oauthplayground`.
4. **Refresh token с телефона** — через OAuth Playground:
   - открой https://developers.google.com/oauthplayground;
   - шестерёнка справа вверху → **Use your own OAuth credentials** →
     вставь Client ID и Client secret;
   - слева в поле «Input your own scopes» впиши
     `https://www.googleapis.com/auth/youtube.force-ssl` →
     **Authorize APIs**;
   - войди Google-аккаунтом и **выбери канал ANCIENT WHISPERS of HISTORY**
     (Brand Account), а не личный профиль — токен привязывается к
     выбранному каналу;
   - **Exchange authorization code for tokens** → скопируй
     **Refresh token**.

   С компьютером то же самое делает `python pipeline/youtube_upload.py auth`
   (нужен клиент типа Desktop app и переменные YT_CLIENT_ID /
   YT_CLIENT_SECRET).
5. **Секреты GitHub** (Settings → Secrets and variables → Actions →
   вкладка **Secrets**, не Variables — переменные видны в логах):
   `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`.

## Как выглядит выпуск

1. Пушишь `jobs/<id>.json` — идёт сборка (до 5,5 часа).
2. В конце: релиз `final-<id>` → папка на Google Drive → YouTube private.
   В Summary прогона: id ролика и ссылка «открыть в Studio».
3. С телефона: приложение **YouTube Studio** → Контент → ролик со
   статусом «Ограниченный доступ»/Private → проверь обложку, главы,
   субтитры → **Видимость → Открытый доступ** (или «Запланировать»).

Отложенная публикация без телефона: поле `youtube.publish_at` в
спецификации (ISO-8601, например `2026-10-01T18:00:00Z`) — ролик
зальётся private и сам станет публичным в это время. Своя категория —
`youtube.categoryId` (по умолчанию `27`, Education).

Повторная сборка того же выпуска второй ролик не создаёт: уже залитый
находится по названию среди последних загрузок канала, у него
обновляются описание, теги и обложка, а статус не трогается — если ты
уже опубликовал, он останется опубликованным.

## Если сломалось

| в логе | что значит | что делать |
|---|---|---|
| `invalid_grant` | refresh token протух или отозван | приложение в Production (шаг 2), токен заново (шаг 4) |
| `quotaExceeded` | дневная квота API 10 000 единиц; заливка стоит 1600, обложка 50, субтитры 400 | подождать до полуночи по Тихоокеанскому, перезалить Upload to YouTube |
| `uploadLimitExceeded` | YouTube ограничил загрузки канала на сегодня | перезалить завтра |
| `youtubeSignupRequired` | при входе выбран не тот канал | шаг 4, выбрать ANCIENT WHISPERS |
| `обложка не встала … forbidden` | свои обложки только у подтверждённого канала | youtube.com/verify (телефон), потом перезалить — обложка встанет, ролик не задвоится |
| ролик «заблокирован как private» | проект Google Cloud не прошёл аудит YouTube API: такие загрузки YouTube держит private | нам это и нужно — публикуешь вручную в Studio. Если он не даёт сменить статус, подать проект на аудит YouTube API Services |
| `нет final.mp4` в Upload to YouTube | `cleanup.yml` удалил ролик из релиза (старше недели) | взять файл с Google Drive и залить руками |
