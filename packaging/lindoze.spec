Name:           lindoze
Version:        0.7.0
Release:        1%{?dist}
Summary:        Linux system monitor with the layout of Windows 11 Task Manager

License:        GPL-3.0-or-later
URL:            https://github.com/awn007-eng/lindoze
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  desktop-file-utils

# PySide6 is huge and not auto-detected as a system package by some macro
# versions; pin it explicitly so the auto-generated python3dist() requires are
# satisfied by the distro Qt6 bindings rather than a pip build.
Requires:       python3-pyside6

%global _description %{expand:
Lindoze is a Linux system monitor laid out like the Windows 11 Task Manager.
Most Linux monitors collapse every CPU thread into one overlaid graph; on a
many-thread machine that is unreadable. Lindoze gives you the per-thread CPU
grid you remember from Windows Task Manager, plus sortable processes,
performance pages (CPU, memory, multi-GPU, disk, network), and startup-app
toggles in a single Qt app. Built and tested on KDE Plasma and GNOME.}

%description %_description

%prep
%autosetup -n %{name}-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files lindoze

# Desktop entry: the in-repo file uses an @EXEC_PATH@ token for the from-source
# install; for the RPM the gui-script lands at %{_bindir}/lindoze.
sed 's|@EXEC_PATH@|%{_bindir}/lindoze|' packaging/lindoze.desktop > %{name}.desktop
desktop-file-install --dir=%{buildroot}%{_datadir}/applications %{name}.desktop

install -Dm0644 assets/lindoze.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/%{name}.svg

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/%{name}.desktop

%files -f %{pyproject_files}
%license LICENSE
%doc README.md
%{_bindir}/lindoze
%{_datadir}/applications/%{name}.desktop
%{_datadir}/icons/hicolor/scalable/apps/%{name}.svg

%changelog
* Fri Jun 26 2026 Aaron <awn007@gmail.com> - 0.7.0-1
- Processes: new View menu — Grouped / Flat (all) / User only / System only. Flat
  is one global list, so sorting by CPU surfaces the hottest process from any user
  at the top instead of only within its group.
- Processes: multi-select gains Suspend/Resume, Set priority, and Copy PIDs / paths
  / command lines (one per line) in addition to batch End/Kill.
- Processes: Compact density now tightens row padding only, keeping the font full
  size; search-highlighted cells are vertically aligned with their neighbours.

* Fri Jun 26 2026 Aaron <awn007@gmail.com> - 0.6.0-1
- Processes: multi-select (Ctrl/Shift-click) with batch End/Kill actions; the End
  button and a new right-click menu act on the whole selection.
- Performance: the current-value readout stays legible at high utilisation (a dark
  halo keeps it from washing into the area fill).

* Wed Jun 24 2026 Aaron <awn007@gmail.com> - 0.5.2-1
- Audit fixes: duplicate Performance sidebar row, offset CPU-grid context menu,
  GPU-backend failure isolation, and sampler timer cleanup on window close.

* Thu Jun 18 2026 Aaron <awn007@gmail.com> - 0.5.1-1
- Processes: fix row-height flicker during horizontal scroll in Compact mode.

* Thu Jun 18 2026 Aaron <awn007@gmail.com> - 0.5.0-1
- Processes: Copy path/PID/command line context-menu entries, a Compact/Standard
  row-density toggle, and lower CPU (the sampler caches immutable per-process
  fields and reads exe/cmdline lazily).
- New View menu with an Always on Top toggle (X11; greyed under Wayland).
- Window icon bundled in the package so pipx/pip installs get a real icon.

* Thu Jun 18 2026 Aaron <awn007@gmail.com> - 0.4.0-1
- Processes tab: new Path and Command line columns (full executable path and
  complete command line with all arguments), a Columns button to show/hide
  columns, and search now matches the path too.
- Properties dialog degrades gracefully when individual fields are restricted.

* Fri Jun 12 2026 Aaron <awn007@gmail.com> - 0.3.0-1
- Graph readability: per-core CPU clock speeds on the grid, inline value
  readouts and axis scale labels on every graph, combined dual-trace Disk and
  Network graphs, and rose-tinted CPU grid labels.

* Wed Jun 10 2026 Aaron <awn007@gmail.com> - 0.2.6-1
- Initial Copr packaging: CPU grid aspect cap + GNOME validation release.
