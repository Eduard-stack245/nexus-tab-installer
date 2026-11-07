# Changelog

**English** | <img src="docs/flags/ua.svg" alt="UA" width="18" height="12"> [Українська](#українська)

## 1.0.0

First public release. / Перший публічний випуск.

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

---

## Українська

### Що вміє

- **Завантаження модів Nexus Mods** за списком посилань і з відкритих вкладок браузера, у будь-якому їх стані: сторінка мода, вкладка Files,
  сторінка окремого файлу. Безкоштовний акаунт: розширення браузера натискає кнопки сайту. Premium з API-ключем: офіційний API.
- **Темп завантаження:** обережний, помірний або власний. Програма сама чекає, коли сайт просить зачекати, і зупиняється на сторінці блокування.
  CAPTCHA та вхід виконує людина, програма лише попереджає.
- **Вимоги модів:** що потрібно моду за словами автора й чого у вас немає, з докачуванням відсутнього однією кнопкою. Це підказка, завантаження вона не зупиняє.
- **Нові версії** завантажених модів: перевірка за кнопкою й раз на день, завантаження лише після натискання.
- **Mod Organizer 2 і Vortex:** передавання файлу з даними Nexus; простий мод одразу встановлюється в профіль закритого MO2.
- **Сесії:** кожне завантаження у своїй папці з датою й часом; видалення файлу або сесії до Кошика.
- **Попередження про місце на диску** та **сповіщення Windows** про закінчення завантаження й потрібну дію.
- **Інтерфейс англійською та російською**, перемикання будь-коли.
- **Портативність:** Python і все потрібне всередині папки, нічого не встановлюється.
- Браузери: Edge, Chrome, Opera GX, Firefox.

### Перевірено

- Автотести: 631.
- Справжній сайт Nexus в Edge з безкоштовним акаунтом: завантаження, докачування вимог, оновлення, видалення, сповіщення.
- Справжні Mod Organizer 2 2.5.2 і Vortex 2.0.2: передавання й встановлення.

### Не перевірено

- Premium-акаунт.
- Завантаження зі справжнього сайту в Chrome, Opera GX і Firefox: перевірено на локальній імітації сайту.
- Файл без архіву зі справжнього сайту.
