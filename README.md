# Nexus Tab Installer

**English** | [Русский](README_RU.md)

A portable Windows program that downloads Nexus Mods files by itself, from links and from open browser tabs.
Version 1.0.0 · Windows 10 and 11, 64-bit · [Changelog](CHANGELOG.md)

The program downloads Nexus Mods files **by itself, with no clicks by a person**, for a free or a paid (Premium) account:

- **from a list of links** ("Paste links or open JSON");
- **from open browser tabs** in any state: a mod page, its Files tab or the page of one file (`?tab=files&file_id=…`, including Slow download).
  A tab that points to one file downloads that file, otherwise the latest main file.

The language can be switched at any time: RU / EN at the bottom of the side bar. The first start follows the language of Windows.

## Download

The ready program is on the **Releases** page of this repository: the file `NexusTabInstaller-<version>-portable.zip`. Nothing needs installing,
see "Getting started" below. To build such an archive yourself: `py -3 scripts/make_package.py` (needs Python 3.14).

## How it works

| Account / key | How files download |
|---|---|
| Premium + API key | directly through the Nexus API, no browser needed (**not tested**, see below) |
| Free (with or without a key) | the extension opens the file page in a separate window, presses **Slow download** (**Standard download** for files over 500 MB), waits for the countdown of the site and takes over the download; the program downloads the file, with resume and retries |
| Premium without a key | the same, with the **Start download** button (**not tested**, see below) |

**A Premium account has not been tested:** only Free accounts were checked. The program shows this warning if it sees Premium.
Start with one or two mods and check the result.

- An error in one file does not stop the queue: up to 3 tries, then the file is marked as failed and the others go on.
- Files downloaded earlier are not downloaded again.
- A file need not be an archive: `.esp`, `.dll`, `.schem` and others work the same way. With an API key the program takes the latest main archive and all main
  files that are not archives. Without a key the site itself chooses one main file. Choose exactly what you need in "Files / versions".
- If the program was closed in the middle of a download, its list comes back at the next start: press "Download selected" and finished files are skipped.
- If Nexus asks you to **log in** or pass a **check (CAPTCHA)**, the program only warns you. A person does that, and the download then goes on by itself.
  The program neither solves nor bypasses a CAPTCHA.

## Requirements of a mod

A short mark next to a mod says how things stand: "Missing: 2", "Optional: 1", "Outside Nexus: 2" or "Requirements met". A click opens the list with the note of the
author and an "Add" button. One warning at the top covers the whole list and offers "Add to list", "Download them now" and "Hide".

**This is only a hint.** The list is written by the author of the mod, and the program cannot know what you really need: downloads are never refused and never wait for
an answer. It counts as already here when it is in the list, downloaded, or found in MO2 or Vortex. What lies outside Nexus (SKSE, Visual C++) cannot be checked, so
the program only reminds you once, with a link. In the settings you can also have needed mods added by themselves, or turn all of this off.

## New versions

"Check for updates" on the "Downloads" tab, and if you want, once a day at start. What is found is only offered: nothing downloads until you press the button.

Names, sizes, requirements and new versions come from the public Nexus API in one request, with no key and nothing about you: only the numbers of the mods.

## Deleting, disk space, notifications

- **Deleting:** a file or a whole session, with a confirmation in the row. Files go to the Recycle Bin, on a flash drive they are deleted right away. Copies in MO2 and Vortex stay.
- **Disk space:** a warning before and during a download. The download goes on, you decide.
- **Notifications:** a Windows message when a download ends, your action is needed or the disk is nearly full. Can be turned off in the settings.

## Pace and the risk of a block

**There is no guarantee that an account will not be blocked.** The [Nexus terms](https://help.nexusmods.com/article/18-terms-of-service) forbid bots and automated
programs for accessing the content of the site (section 11) and downloading far beyond normal use without permission from Nexus (section 10), and an account can be
suspended for that. Downloading through the browser presses the buttons of the site automatically, so a slow pace lowers the load and the risk but does not make this
way allowed. Use it at your own risk. The way with the official API is Premium and an API key.

Nexus does not publish a limit for site downloads, so the numbers below are the choice of this program, not a proven safe threshold. In the "Settings" you can change the
pace or choose "Own" and set the pauses and the limit yourself, at your own risk.

| Pace | One file every | Long pause | Per day | 100 files (not counting the downloads) |
|---|---|---|---|---|
| Careful (default) | 60 s | 10 min after 25 files | up to 300 | about 2 h 6 min |
| Moderate | 25 s | 5 min after 40 files | up to 600 | about 50 min |
| Own | as you say | as you say | as you say | shown in the "Settings" |

The program also reacts to the site itself: it slows down after a check, waits after failures and after "too many requests", and stops at a block page until you press the button.

## Mod Organizer 2 and Vortex

Choose "To Mod Organizer 2" or "To Vortex" for a mod or in "Destination for all mods". The program finds both programs by itself. A copy of every file stays in its session folder.

| | What happens |
|---|---|
| **MO2 closed, simple mod** | the file is on the Downloads tab with its Nexus details, the mod is installed and enabled in the chosen profile |
| **MO2 open, or a mod with an installer** | the file appears on the Downloads tab by itself, you install it with a double click |
| **Vortex closed** | the program checks that the game is set up there, starts it with the right game, waits for the switch and passes the file to install |
| **Vortex open on this game** | the file goes to install right away |
| **Vortex open on another game** | the file goes to the downloads of the right game and you press Install |

After that Vortex follows its own settings: by default it enables an installed mod and deploys it to the game folder.

MO2 and Vortex take only archives, so a file that is not an archive reaches them in a small zip. Programs (`.exe` and so on) stay in the session folder, are not passed to
the managers, and the program never runs them.

## Getting started

1. Unpack the archive into any folder, even on a flash drive, and run `START_WINDOWS.bat`. Nothing gets installed: Python and everything else is inside. A 64-bit Windows is needed.
2. Install the extension into your browser, once: the program shows the steps. Then allow the connection in the program.
3. Log in to Nexus Mods in that browser.
4. In the program: "Find mods" (or paste links), tick the mods, "Download selected".

An API key is not required, but it gives a choice of versions and direct Premium downloads. Everyone needs their own key, created in the account settings of Nexus Mods.

Settings, the key, logs and downloaded archives live in the `data/` folder next to the program. **Do not give this folder to others:** the key is kept there without encryption.

Each download puts its files into its own folder with date and time: `data/sessions/2026-11-06 19-07-12/`.

## Portable

The whole program lives in its folder. To move it, close it and move the folder. Only two things stay outside, and you install both yourself:

- **The extension in the browser.** After moving the folder, load it again from the new place.
- **The NXM link handler.** An optional button in the settings and the only registry entry. After moving the folder, press it again.

To remove the program, delete its folder and remove the extension from the browser.

## Browsers

| Browser | Extension folder | What was tested |
|---|---|---|
| Edge | `extensions/chromium` | real downloads from Nexus (Free account, with and without an API key) and automated scenarios on a copy of the site |
| Chrome | `extensions/chromium` | automated scenarios on a copy of the site |
| Firefox | `extensions/firefox` | automated scenarios on a copy of the site |
| Opera GX | `extensions/chromium` | automated scenarios on a copy of the site and a real Nexus page without logging in; downloads from the real site were not tested |

- **"Always ask where to save files"** is better turned off in the browser settings.
- **Firefox:** without a signature from Mozilla, the extension has to be loaded again after each restart of the browser.
- **Opera GX:** RAM Limiter with a hard limit in GX Control may unload the download tab.

## Documentation

- [Connecting a browser](docs/BROWSER_SETUP_GUIDE_EN.html)
- [На русском](README_RU.md)

## License

[MIT](LICENSE): free to use, change and share, keeping the text of the license. It comes "as is", without warranty.
The program is not affiliated with Nexus Mods, Mod Organizer 2 or Vortex.
