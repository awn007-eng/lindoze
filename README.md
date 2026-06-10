# Lindoze Process Manager

A Linux system monitor with the layout of Windows 11 Task Manager.

Most Linux system monitors (gnome-system-monitor, ksysguard, even the
otherwise-excellent mission-center) collapse every CPU thread into a single
overlaid graph. On a 32-thread machine that's unreadable. Lindoze gives you
the per-thread grid you remember from Windows Task Manager, plus the rest of
the Task Manager layout — sortable processes, performance pages, startup
apps — in a single Qt app. Built and tested on KDE Plasma; should work on
GNOME and other Qt-capable desktops but the visual fit there is not yet
verified.

## Screenshots

The Performance tab defaults to the per-thread grid (right-click or toggle
button to switch to the aggregate view):

![CPU per-thread grid](https://raw.githubusercontent.com/awn007-eng/lindoze/main/docs/screenshots/cpu-grid.png?v=0.2.5)

Processes tab — sortable tree, search, end-task / kill / suspend / renice:

![Processes tab](https://raw.githubusercontent.com/awn007-eng/lindoze/main/docs/screenshots/processes.png?v=0.2.5)

Startup apps tab — toggle XDG autostart entries with a 10-second undo:

![Startup tab](https://raw.githubusercontent.com/awn007-eng/lindoze/main/docs/screenshots/startup.png?v=0.2.5)

## Features (v0.2)

- **Performance tab** with per-resource sub-navigation
  - **CPU** — aggregate + per-logical-processor grid (auto-sized; handles 2 to 128+ threads)
  - **Memory** — usage, swap, cached/buffers from /proc/meminfo
  - **GPU** — NVIDIA (via NVML), AMD (via sysfs), Intel (experimental — i915 perf PMU + hwmon); multi-GPU systems show one page per GPU
  - **Disk** — per-physical-device R/W throughput
  - **Network** — per-interface RX/TX throughput
- **Processes tab** — sortable tree with User / System grouping, search, right-click actions
  (End task, Kill, Suspend/Resume, Set priority, Open file location, Properties)
- **Startup apps tab** — `~/.config/autostart/*.desktop` entries; one-click toggle with
  10-second undo and automatic `.bak` files

## Requirements

- Python 3.10+
- A Qt 6-capable Linux desktop (KDE, GNOME, etc.)
- For GPU pages:
  - **NVIDIA**: the proprietary driver (provides NVML)
  - **AMD**: any modern `amdgpu` driver (kernel `>=5.0`); no extra packages needed
  - **Intel** (experimental): `i915` driver, kernel `>=5.13` for the perf PMU; reads engine
    utilization via `perf_event_open` and temp/freq via sysfs+hwmon. No subprocess, no root.
    The `xe` driver degrades to temp/freq only — Arc-on-xe testing pending. Older Gen7
    (Ivy Bridge) hardware is known thin: freq reads work via fallback sysfs path but
    PMU util may return 0. **If your Intel GPU is detected but util/temp/freq look wrong,
    please run `lindoze --dump-gpu` and file an issue with the output.**

## Install

### Quick install with pipx (recommended)

If you have [`pipx`](https://pipx.pypa.io/) installed, one command does it:

```bash
pipx install lindoze
lindoze
```

Update later with `pipx upgrade lindoze`; remove with `pipx uninstall lindoze`.

To track the development branch instead of the latest release, install from
git: `pipx install git+https://github.com/awn007-eng/lindoze.git`.

(`pipx` itself: `sudo apt install pipx` on Ubuntu/Debian, `sudo dnf install pipx`
on Fedora/Nobara, `sudo pacman -S python-pipx` on Arch.)

### From source (for hacking / customization)

```bash
git clone https://github.com/awn007-eng/lindoze.git
cd lindoze
./bootstrap.sh
./run.sh
```

`bootstrap.sh` creates a local `.venv/`, installs dependencies, drops a
Start-menu entry into `~/.local/share/applications/`, and installs the
bundled icon. Safe to re-run — it's idempotent. Launch via `./run.sh` or
your desktop's Start menu.

### Flatpak

Not yet shipping on Flathub — coming in a future release.

## Limitations / not yet supported

- **No per-process GPU/Network columns** — these need root or eBPF on Linux. The
  totals are accurate; per-process attribution isn't.
- **No Services tab** — systemd unit management is well-covered by KDE's System
  Settings and `systemctl`; we're not duplicating it.
- **No Users tab** — single-user desktop assumption.

## Credits

Inspired by Dave Plummer's work on the original Windows Task Manager. This is a
tribute, not an affiliation.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
