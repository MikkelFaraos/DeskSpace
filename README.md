# DeskSpace

Translucent fences and widgets for the Windows desktop. Sort your icons into fences, keep notes, a calendar and system stats on the desktop, launch your games from cover art, and share it all with a few small animals that wander along the tops of your fences.

Everything sits at desktop level, so it never covers your apps. Current version: **1.4.1**. Built with Python and PySide6. Developed and tested on Windows 11, with a [Linux version](#linux) for Ubuntu and Linux Mint.

## Features

### Fences

- **Icon fences:** drag desktop icons and files into a fence. Each fence is a real folder, so your files stay ordinary files.
- **Folder portals:** show any folder on your PC as a fence.
- **Tabs:** drag one fence onto another to combine them into a tab group.
- **Auto-sort:** sort the desktop into fences by file type in one go, or give a fence patterns like `*.xlsx` or `invoice*` so matching files move into it automatically.
- **Snapping:** fences snap to screen edges and to each other, with an adjustable distance.
- **Roll up:** double-click a title bar to collapse a fence.
- **Quick-hide:** double-click an empty spot on the desktop, or the tray icon, to hide all fences, and optionally the desktop icons too.
- **Appearance:** fence colours can follow your wallpaper, or you can pick your own. Title size is adjustable.
- **File previews:** hover over a file to see a thumbnail, or the first lines of a text file.

### Widgets

- **Sticky note** and **to-do list**
- **Clock and calendar:** shows upcoming events from an iCal link, such as Google Calendar or Outlook.com.
- **System monitor:** CPU, RAM, GPU, VRAM, network, disk, uptime, pending Windows updates, and the battery level of Bluetooth devices and controllers. CPU and GPU temperatures appear when LibreHardwareMonitor is running.
- **Network monitor:** live download and upload speed with a 60-second graph, ping, Wi-Fi name and signal, and your local IP address.
- **Media controls:** see what's playing and control it.
- **Package tracker:** keeps your tracking numbers until the parcels are delivered. Supports PostNord, GLS, DAO, Bring, DHL, UPS and FedEx. Live status needs a free [17TRACK](https://www.17track.net/) API key; without one, a double-click opens the carrier's tracking page.

### Games

- **Game tiles:** show a fence as cover art instead of icons.
- **Steam and GOG:** add your installed games with one click. Covers are found automatically, and GOG games start through GOG Galaxy when it's installed. GOG shortcuts you drag into a fence are recognised too.

### Tools

- **Quick search:** press `Ctrl+Shift+Space` to find apps, fence items, desktop items and files. Enter opens the result, Ctrl+Enter shows it in Explorer. The shortcut can be changed.
- **File tidy:** sorts loose downloads into type folders, files older screenshots by month, and finds duplicate downloads. You see a preview first, files are only moved, and the last tidy can be undone. It can run by itself once a day; this is off by default.
- **Disk cleanup:** opens from the System monitor and shows what the Recycle Bin, temporary files, crash reports and old downloads are using. It always asks first.

### Desktop pets

- Up to 8 pets at once: **cat, dog, bear, hamster or rabbit**, in 11 colours, each with its own name.
- They walk along fences, jump between them and nap when you're away. Drag one to pick it up, or click it to make it happy.
- With more than one, they greet each other, play chase, follow each other onto fences and nap side by side. A hamster will ride on a bear.
- Right-click a pet to give it a treat or throw a ball.
- They dress up for Halloween, Christmas, New Year and summer.
- A pet can tell you a minute before a calendar event starts. This needs a calendar link on a clock widget.

### Console mode

A full-screen home screen for a PC on a TV, made to be used from the sofa. Open it from the tray icon or Settings, or set it to start when you sign in.

- **Game library:** your Steam and GOG games as a big cover grid, most recently played first.
- **System page:** volume, game launchers, minimize, sleep, restart, shut down and exit to desktop.
- **Game launchers:** open Steam, GOG Galaxy, Epic Games, the EA app, Ubisoft Connect, Battle.net, Amazon Games, Xbox and others you have installed.
- **Pets:** they walk along the bottom of the screen at double size.
- **Controls:** an Xbox-style controller, the keyboard or the mouse. The on-screen hints follow whichever you used last.
- **Playing:** starting a game steps out of the way, and the console comes back when the game ends.
- **Stepping away:** minimizing or closing the console window leaves it running in the background. Hold **Back + Start** on the controller, or use the tray icon, to return to it at any time.

PlayStation controllers aren't read directly yet. They work if a tool such as Steam Input or DS4Windows presents them as an Xbox controller.

## Install

There is no ready-made download yet, so you build DeskSpace yourself. You need [Python 3.13](https://www.python.org/downloads/) and [Inno Setup 6](https://jrsoftware.org/isinfo.php):

```powershell
winget install JRSoftware.InnoSetup
```

Then, in the project folder:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r build\requirements-runtime.txt pillow
powershell -ExecutionPolicy Bypass -File build\build.ps1
```

The build downloads the official signed Python runtime, runs a self-test, and creates `dist\DeskSpace-Setup-<version>.exe`. Run that file to install. It needs no admin rights and installs to `%LOCALAPPDATA%\Programs\DeskSpace`.

### If Windows blocks the installer

The setup file isn't signed, so Windows' Smart App Control may refuse to run it. In that case, install from the build output instead. The app runs on the signed Python runtime, which Smart App Control allows, and this does the same job as the setup file:

```powershell
dist\stage\runtime\python.exe build\install_without_setup.py
```

Use the same commands to update to a newer version. There's no need to turn Smart App Control off, and Windows can't turn it back on without a reinstall.

### Run it from source

To run it without installing:

```powershell
.venv\Scripts\pythonw.exe deskspace.pyw
```

## Linux

DeskSpace also runs on Ubuntu, Linux Mint and other apt-based systems. In the project folder:

```bash
bash build/install-linux.sh
```

It asks for your password to install a few system packages, then installs the app for your user in `~/.local/share/deskspace` and starts it. To remove it, run the same script with `--uninstall`. Your data in `~/DeskSpace` is kept.

The Linux version has been tested automatically on Ubuntu with a basic window manager, but not yet by hand on the full Ubuntu or Mint desktops. It differs from Windows in a few ways:

- **Fences** stay behind other windows and appear on every workspace. "Show desktop" may hide them on desktops where a file manager draws the icons.
- **Not available:** double-clicking the desktop to hide fences, hiding the desktop icons, the global search shortcut, GOG games, and GPU load and VRAM. Use the tray icon to hide fences and open search.
- **File previews** cover image files only.
- **File search** needs `locate`, media controls need `playerctl`, and battery levels need `upower`. The install script adds all three.
- **Console mode** reads Xbox-style controllers through the Linux joystick driver.

## Getting started

1. Right-click the DeskSpace tray icon, choose **New**, and pick a fence or widget.
2. Drag icons from your desktop into a fence, or choose **Auto-sort desktop into fences…** from the tray menu.
3. Right-click a fence and choose **Settings…** to change it. Colour, New and Remove are also in the right-click menu.

## Your data

Fences, notes and settings are stored in `%USERPROFILE%\DeskSpace`, and installing, updating or uninstalling doesn't touch them. If you're coming from the old DeskFences name, the data folder moves over the first time the app starts.

## Uninstall

Remove DeskSpace in **Settings → Apps**, or with **Uninstall DeskSpace** in the Start menu. Your `DeskSpace` data folder is kept.
