# Changelog

All notable changes to Lindoze are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] — 2026-06-26

### Added
- **Processes — multi-select batch actions.** Ctrl/Shift-click to select several
  processes and End or Kill them in one go. The toolbar button reflects the
  selection ("End 3 tasks"), and a batch right-click menu offers "End N tasks" and
  "Kill N tasks (SIGKILL)". Processes you don't own are skipped, and batch actions
  ask for confirmation before signalling.

### Fixed
- **Performance graphs — readable value readout under load.** The current-value
  number on each graph (e.g. CPU %) now keeps a subtle dark halo, so it stays
  legible when the area fill climbs behind it in the same accent colour at high
  utilisation instead of washing out.

## [0.5.2] — 2026-06-24

### Fixed
- Performance sidebar: rows are no longer added twice (the list item was inserted
  both via its parent constructor and `addItem`), which could desync the per-row
  value updates.
- CPU page: the per-core grid's right-click menu now appears under the cursor — it
  was mapping coordinates from the wrong widget and popping up offset.
- Sampler: a failing GPU backend (driver hiccup, subprocess error) no longer takes
  down the entire sampling tick; it degrades gracefully for that frame.
- Window: sampler timers are now stopped on close, so nothing keeps firing after
  the window is destroyed.

## [0.5.1] — 2026-06-18

### Fixed
- Process list: row height no longer flickers during horizontal scrolling in
  Compact mode. The view now pins a single row height instead of sampling
  whichever column was leftmost-visible (whose font height could differ).
  Reported by tbone26-fed.

## [0.5.0] — 2026-06-18

Power-user pass driven by forum feedback (tbone26-fed): denser process list, clipboard actions, a leaner sampler, and a window you can pin and shrink into a corner.

### Added
- **Copy path**, **Copy PID**, and **Copy command line** entries on the process right-click menu (path / command line are disabled when unreadable, e.g. kernel threads).
- **Compact / Standard row-density toggle** in the Processes toolbar — Compact drops the row font and padding to fit roughly 20 rows where Standard fits ~15, for more processes per screen. Persists across launches.
- **Always on Top** under a new **View** menu (persisted). Works on X11; under Wayland the compositor controls window stacking, so the menu is greyed with a pointer to your compositor's own "Keep Above".
- Search now also matches the executable path, alongside name, command line, and PID.

### Changed
- **Lower CPU on the Processes tab.** The sampler now caches the immutable per-process fields (user, executable path, command line) instead of re-reading them for every process every tick, and reads the path / command line only when the Path/Command-line column is shown or a search is active. The per-tick sampling cost drops ~38%.

### Fixed
- Menu **check indicators now render** under the dark theme (the column show/hide menu showed no tick before); all menus get consistent dark styling.
- **Window icon now ships inside the package**, so `pipx`/`pip` installs — which don't install a `.desktop` file or themed icon — get a real window icon instead of a generic placeholder.
- Lower minimum window size (380×280) for a small corner-of-the-screen monitor.

## [0.4.0] — 2026-06-18

Process visibility, by request: see exactly which binary and command line each process is running, and pick which columns you want.

### Added
- **Path** and **Command line** columns in the Processes tab — see a process's full executable path and its complete command line (all arguments), so you can tell apart eight instances of the same script launched with different flags. Both are sortable and hidden by default; use the **Columns ▾** button in the toolbar (or right-click the column header) to show or hide any column, and your choice is remembered between launches.
- The search box now also matches the executable path, alongside the name, command line, and PID it already searched.

### Fixed
- The Processes **Properties** dialog no longer fails entirely when a single attribute is unreadable (e.g. restricted system helpers like `(sd-pam)`); each field now degrades to `(restricted)` / `(no longer running)` independently so the rest still shows.

### Note
- On first launch after upgrading, the Processes column layout (widths/sort) resets once — the saved layout predates the new columns. It persists normally from then on.

## [0.3.0] — 2026-06-12

Graph readability pass: the sparklines now carry the numbers that give them meaning — per-core clocks, live values, and real axis scales — rendered inside Lindoze's existing dark cards.

### Added
- **Per-core clock speed** on each logical-processor cell in the CPU grid (top-right), next to its utilization trace. The data was already sampled via `psutil.cpu_freq(percpu=True)`; it's now shown per core instead of only as the min/max/avg in the stats block.
- **Inline current-value readout** painted on every graph, color-coded to the page accent — read CPU %, memory %, GPU %, or live throughput at a glance without dropping to the stats grid.
- **Axis scale labels**: autoscaling throughput graphs (Disk, Network) show their live ceiling with units (e.g. `247 MB/s`) instead of bare gridlines.

### Changed
- **Disk** and **Network** each render their two directions (Read+Write / Receive+Send) as a **single combined graph** with two color-coded traces sharing one y-axis and a legend, replacing the previous side-by-side pair of boxes — direct comparison at a glance.
- **GPU** Utilization and VRAM graphs gained the same real scale + value labels, replacing a hand-built label string.
- **CPU grid** cell labels (`CPU N` + clock) are tinted rose to complement the teal traces; the live utilization `%` stays teal.

## [0.2.6] — 2026-06-10

First release validated on a second desktop environment, plus a CPU-grid layout fix and a steady-state performance trim.

### Added
- GNOME (Fedora Workstation 44) cross-desktop validation — Processes monospace numerics, Startup icon-theme fallback, and CPU narrow-window reflow all confirmed under Cantarell/Adwaita. Screenshots added under `docs/screenshots/gnome/`.

### Changed
- README claim updated from "tested on KDE" to "Built and tested on KDE Plasma and GNOME (Fedora Workstation); should work on other Qt-capable desktops too."
- MiniGraph brushes/pens/fonts are now cached and rebuilt only on resize; ProcessModel caches fonts, alignment, and group-header colors. The Performance page early-returns when it isn't the visible tab, skipping ~40 graph pushes + repaints per tick — roughly a 2–3% steady-state CPU drop on a Ryzen 7945HX.

### Fixed
- Per-thread CPU cells are capped at 250×200 (aspect ~0.6) so low-core machines on a tall window no longer stretch each sparkline into a skyscraper; the grid now centers on both axes when smaller than the viewport. High-core (32+) layouts are unchanged.

## [0.2.5] — 2026-06-10

UX polish pass driven by an outside review. No new tabs — the existing ones read better at high core counts, at idle, and on first launch.

### Changed
- **CPU per-thread grid**: reflows columns on resize (narrow window → fewer columns, no clipping); vertical scroll when minimum cell size won't fit; last partial row centered; 100×50 readable cell floor so labels like `CPU 117` don't clip on Threadripper-class systems.
- **Processes**: numeric columns (PID, CPU %, Memory, Disk, Threads) right-aligned with a monospace font role; group header rows get a faint teal background tint so they read as section dividers.
- **Startup apps**: icons paint a fallback placeholder immediately and cache resolved themed icons (no empty slots flashing on cold theme caches); friendlier empty-state copy.
- **Sparklines (every graph)**: whole-cell vertical accent wash so idle traces have presence; trace stroke thickened to 1.8px; softened cell border alpha.
- **Window/system**: window + taskbar icon resolves via Wayland's `setDesktopFileName` + bundled SVG fallback; toolbar buttons unified through a shared `lindoze/styles.py`.

## [0.2.4] — 2026-06-09

Metadata-only release fixing the PyPI listing.

### Fixed
- README uses absolute GitHub raw URLs for screenshots so images render on pypi.org.
- `[project.urls]` expanded with `Repository` and `Bug Tracker` for PyPI's sidebar links.

## [0.2.3] — 2026-06-09

Tooling release — no user-visible app changes, but much more installable and shippable.

### Added
- `bootstrap.sh` self-installs the `.desktop` start-menu entry and bundled SVG icon into per-user XDG locations (idempotent). `packaging/lindoze.desktop` uses an `@EXEC_PATH@` substitution token.
- GitHub Actions CI: pytest matrix on Python 3.10/3.11/3.12 + ruff lint (`select=["F"]`) on every push and PR.
- Trusted Publishing workflow auto-publishes future releases to PyPI on `v*` tag push via OIDC — no long-lived tokens, with a manual-approval gate on the `pypi` environment.

## [0.2.2] — 2026-06-09

Two polish features for Processes and Performance — and Lindoze landed on PyPI.

### Added
- **Processes**: search needle bolded in accent teal in the Name/PID columns; a "N matches" counter that refreshes on each snapshot.
- **Performance**: 60s / 10min / 1hr time-scale toolbar applied globally to detail-page graphs (sidebar mini-graphs stay at 60s); selection persists across launches.
- Published to PyPI: `pipx install lindoze`.

### Changed
- Graphs render right-anchored (newest sample at the right edge) instead of starting from a misleading flat-zero baseline.

## [0.2.1] — 2026-06-09

### Added
- Window size/position, last-viewed tab, and Processes column widths + sort order now persist between launches via `QSettings` (`~/.config/Lindoze/Lindoze Process Manager.conf`).

## [0.2.0] — 2026-06-08

Polishes the experimental Intel GPU support and adds a debug flag so Intel users can file actionable reports.

### Added
- **Intel GPU `--dump-gpu` flag** — prints raw PMU file descriptors, counter values, sysfs paths, and event configs to stderr for bug reports.
- PMU parser unit tests (15 tests with synthetic counter fixtures, no real hardware needed).

### Fixed
- Intel frequency sysfs fallback — older Gen7 (Ivy Bridge HD 4000-era) places `gt_cur_freq_mhz` directly under `cardN/`, not `cardN/device/`; both paths are tried now.

### Known limitations
- Ivy Bridge (Gen7): detection + freq work, but PMU util may report 0 (awaiting a `--dump-gpu` report).
- `xe` driver: temp/freq only. Old AMD `radeon` driver: no `gpu_busy_percent` sysfs → not detected (use `amdgpu`).

## [0.1.0] — 2026-06-06

First public release — a Linux system monitor laid out like Windows 11 Task Manager.

### Added
- **Per-thread CPU grid** — every logical processor as its own mini-graph.
- **Multi-GPU support** — NVIDIA (NVML) and AMD (sysfs), one page per detected device.
- **Processes tab** — sortable tree, search, end-task / kill / suspend / renice.
- **Startup apps tab** — one-click toggle with 10-second undo.

[Unreleased]: https://github.com/awn007-eng/lindoze/compare/v0.5.2...HEAD
[0.5.2]: https://github.com/awn007-eng/lindoze/releases/tag/v0.5.2
[0.5.1]: https://github.com/awn007-eng/lindoze/releases/tag/v0.5.1
[0.5.0]: https://github.com/awn007-eng/lindoze/releases/tag/v0.5.0
[0.4.0]: https://github.com/awn007-eng/lindoze/releases/tag/v0.4.0
[0.3.0]: https://github.com/awn007-eng/lindoze/releases/tag/v0.3.0
[0.2.6]: https://github.com/awn007-eng/lindoze/releases/tag/v0.2.6
[0.2.5]: https://github.com/awn007-eng/lindoze/releases/tag/v0.2.5
[0.2.4]: https://github.com/awn007-eng/lindoze/releases/tag/v0.2.4
[0.2.3]: https://github.com/awn007-eng/lindoze/releases/tag/v0.2.3
[0.2.2]: https://github.com/awn007-eng/lindoze/releases/tag/v0.2.2
[0.2.1]: https://github.com/awn007-eng/lindoze/releases/tag/v0.2.1
[0.2.0]: https://github.com/awn007-eng/lindoze/releases/tag/v0.2.0
[0.1.0]: https://github.com/awn007-eng/lindoze/releases/tag/v0.1.0
