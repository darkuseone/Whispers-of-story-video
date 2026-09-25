# Выкладка роликов на Google Drive

После сборки `build.yml` сам кладёт выпуск в папку канала
**Whisper of History / <название ролика> [<id>]** на твоём Google Drive: `final.mp4`,
субтитры, шортсы, обложки, `youtube.txt`. Файлы идут с раннера GitHub
прямо в Drive API (`pipeline/drive.py`), кусками по 32 МБ с докачкой после
обрыва. Через чат и MCP видео не проходит — токены на него не тратятся.

Пока секретов нет, шаг просто пропускается и сборке не мешает.

## Разовая настройка (15 минут)

Доступ выдаётся со scope `drive` (полный Диск). Узкий `drive.file` не
годится: папку канала создали через коннектор, а с `drive.file` скрипт
видит только созданное им самим — не нашёл бы её и завёл бы вторую.
Скрипт при этом пишет только в папку канала. Полный scope Google
считает «restricted», поэтому при входе будет предупреждение
«приложение не проверено» — для личного использования его просто
пропускаешь (Advanced → Go to …).

1. **Проект.** https://console.cloud.google.com → создать проект
   (любое имя, например `ancient-whispers`).
2. **Drive API.** APIs & Services → Library → «Google Drive API» → Enable.
3. **Экран согласия.** APIs & Services → OAuth consent screen:
   - тип **External**, имя приложения любое, почта — твоя;
   - scope добавлять не обязательно;
   - в Test users добавь свой gmail;
   - потом нажми **Publish app → In production**. Это важно: в режиме
     Testing Google выдаёт токен на 7 дней, и через неделю выкладка молча
     перестанет работать. Проверка Google для личного использования
     не нужна.
4. **Клиент.** APIs & Services → Credentials → Create credentials →
   OAuth client ID → тип **Desktop app**. Скопируй Client ID и Client
   secret.
5. **Refresh token** — один раз, на своём компьютере с Python:

   ```
   pip install requests
   set GDRIVE_CLIENT_ID=...        (Windows; на Mac/Linux — export)
   set GDRIVE_CLIENT_SECRET=...
   python pipeline/drive.py auth
   ```

   Откроется браузер, войди своим аккаунтом, разреши доступ — в консоли
   появится `GDRIVE_REFRESH_TOKEN`.

   Без Python то же самое делается через https://developers.google.com/oauthplayground
   (шестерёнка → Use your own OAuth credentials; клиент тогда нужен типа
   Web application с redirect URI `https://developers.google.com/oauthplayground`;
   scope `https://www.googleapis.com/auth/drive` → Authorize →
   Exchange authorization code for tokens → Refresh token).
6. **Секреты GitHub.** Репозиторий → Settings → Secrets and variables →
   Actions → New repository secret, три штуки:
   `GDRIVE_CLIENT_ID`, `GDRIVE_CLIENT_SECRET`, `GDRIVE_REFRESH_TOKEN`.
   И переменная (вкладка **Variables**, не Secrets)
   `GDRIVE_FOLDER_ID` = `1Vs4CMdrwWorh26EMu2kM5AaDScadtV6L` — папка
   «Whisper of History», уже созданная на Диске. Без неё скрипт ищет
   папку по имени (`GDRIVE_ROOT_NAME`, по умолчанию «Whisper of History»).

## Как пользоваться

- Новый ролик — ничего делать не нужно: в конце сборки шаг
  «Выкладываем на Google Drive» пишет ссылку на папку в лог и в Summary.
- Уже готовый выпуск — Actions → **Upload to Google Drive** → имя
  выпуска. Берёт файлы из релиза `final-<имя>`. Через неделю после
  сборки `cleanup.yml` удаляет `final.mp4` из релиза — после этого
  уедет только то, что там осталось.
- Повторный запуск ничего не дублирует: файл с тем же именем и размером
  пропускается, изменённый — заменяется новой версией по той же ссылке.

## Если сломалось

- «Google не выдал токен (400) invalid_grant» — токен отозван или
  протух (приложение осталось в Testing). Переопубликуй приложение
  (шаг 3) и получи токен заново (шаг 5).
- Место на Диске: 15 ГБ бесплатно — это ~25 роликов. Старые папки
  удаляй руками или переноси.
