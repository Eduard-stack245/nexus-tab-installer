# Changelog / История изменений

## 1.0.0

Первый публичный выпуск. / First public release.

### Что умеет

- **Скачивание модов Nexus Mods** по списку ссылок и из открытых вкладок браузера, в любом их состоянии: страница мода, вкладка Files,
  страница одного файла. Бесплатный аккаунт: расширение браузера нажимает кнопки сайта. Premium с API-ключом: официальный API.
- **Темп скачивания:** осторожный, умеренный или свой. Программа сама ждёт, когда сайт просит подождать, и останавливается на странице блокировки.
  CAPTCHA и вход выполняет человек, программа только предупреждает.
- **Требования модов:** что нужно моду по словам автора и чего у вас нет, с докачкой недостающего одной кнопкой. Это подсказка, скачивание она не останавливает.
- **Новые версии** скачанных модов: проверка по кнопке и раз в день, скачивание только по нажатию.
- **Mod Organizer 2 и Vortex:** передача файла с данными Nexus; простой мод сразу ставится в профиль закрытого MO2.
- **Сессии:** каждая загрузка в своей папке с датой и временем; удаление файла или сессии в Корзину.
- **Предупреждение о месте на диске** и **уведомления Windows** об окончании загрузки и о нужном действии.
- **Русский и английский интерфейс**, переключение в любой момент.
- **Портативность:** Python и всё нужное внутри папки, ничего не устанавливается.
- Браузеры: Edge, Chrome, Opera GX, Firefox.

### Проверено

- Автотесты: 631.
- Настоящий сайт Nexus в Edge с бесплатным аккаунтом: скачивание, докачка требований, обновление, удаление, уведомления.
- Настоящие Mod Organizer 2 2.5.2 и Vortex 2.0.2: передача и установка.

### Не проверено

- Premium-аккаунт.
- Скачивание с настоящего сайта в Chrome, Opera GX и Firefox: проверены на локальной имитации сайта.
- Файл без архива с настоящего сайта.

### Features

- **Downloads Nexus Mods files** from a list of links or from open browser tabs in any state: a mod page, its Files tab, the page of one file.
  Free account: the browser extension presses the buttons of the site. Premium with an API key: the official API.
- **Download pace:** careful, moderate or your own. The program waits when the site asks it to and stops at a block page.
  A person solves a CAPTCHA and logs in; the program only warns.
- **Mod requirements:** what a mod needs according to its author and what you do not have yet, with one click to download what is missing.
  Only a hint: downloads never wait for it.
- **New versions** of downloaded mods: checked on request and once a day, downloaded only when you press the button.
- **Mod Organizer 2 and Vortex:** files arrive with their Nexus details; a simple mod is installed into the profile of a closed MO2.
- **Sessions:** each download in its own folder with date and time; deleting a file or a session to the Recycle Bin.
- **Disk space warnings** and **Windows notifications** when a download ends or your action is needed.
- **Russian and English**, switchable at any time.
- **Portable:** Python and everything else inside the folder, nothing gets installed.
- Browsers: Edge, Chrome, Opera GX, Firefox.

### Tested

- 631 automated tests.
- The real Nexus site in Edge with a free account: downloads, fetching requirements, an update, deleting, notifications.
- Real Mod Organizer 2 2.5.2 and Vortex 2.0.2: hand-over and install.

### Not tested

- A Premium account.
- Downloads from the real site in Chrome, Opera GX and Firefox: tested on a local copy of the site.
- A file that is not an archive, from the real site.
