# WinGPC UniChrom: everything learned so far

A self-contained brief for anyone (human or AI) trying to connect to, drive,
or read data out of Agilent/PSS WinGPC and the LC instrument behind it.
Compiled 2026-11-25 from the Agilent WinGPC user guide (D0029603 Rev. B,
edition 07/2024, software revision 1.1, 555 pages), PSS/Agilent product
pages, the Chemotion ELN integration guide, and the control layer built in
this repository (https://github.com/amyth-create/unichrom-control-layer).
Page numbers refer to the user guide. Items marked **unverified** have not
been checked on a real installation.

Note on scope: the WinGPC licence terms may restrict decompiling or
redistributing the software. Everything below is about observing files,
network traffic and the user interface of a legally installed copy on the
lab's own PC.

---

## 1. What the product is

* **WinGPC Software** (Agilent, formerly PSS Polymer Standards Service
  "WinGPC UniChrom") is a macromolecular chromatography data system (MCDS)
  for GPC/SEC, polymer HPLC, 2D chromatography, light scattering and
  viscometry. Agilent revision 1.1 continues the PSS 8.x line; the manual
  still refers to "WinGPC Software version 8.2 / 8.3" behaviour (p. 489).
  Chemotion's lab ran "PSS winGPC 8.33" on Windows 10.
* Modules/add-ons: **ChromPilot** (instrument control), Compliance Edition
  (21 CFR Part 11: login, user levels, e-signatures, audit trails),
  ReportDesigner, Viscometry, Light Scattering, Triple Detection, 2D,
  Copolymer, Chemical Heterogeneity, Endgroup/Heparin, 3D Spectra, MS
  (p. 7-8). The licence is the file `WinGPC.KEY` (p. 550).
* Stand-alone on one PC; a Client/Server edition and "NetConnect" networked
  data capture exist for legacy licences only (p. 9, 70).
* UI language packs exist (a Language menu, p. 6851 of the text dump;
  `Lang.DLL`, p. 550). German captions must be expected on some installs.

## 2. Data acquisition paths (how signal gets into WinGPC)

WinGPC has two independent channels to the instrument: **data acquisition**
(detector signals) and **instrument control** (ChromPilot). They are
configured separately and can come from different sources (p. 67-69).

Data acquisition sources (Login dialog, "Data Acquisition" section, p. 67):

| Source | What it is |
|---|---|
| **PSS-UDC810** | PSS "Universal Data Center", an external A/D box connected via COM (serial), LAN or USB; "the digital UDC used for digital data acquisition will be displayed with the serial no. 00FFFF and the connection type Eth" (p. 68). It has 8 output relays and injector-trigger inputs (p. 176-177, 536-540 pin assignment). |
| **COMx** | serial port for PSS WinCHROM interfaces, PSS eta-100x/201x viscometers, WGE products (p. 67). |
| **NetConnect** | legacy networked capture (NetConnect Server, "Cubes") (p. 67, 70). |
| **Digital detectors** | multi-angle light scattering detectors are read digitally (PSS SLD7x00/BI-MwA, Agilent 1260 MALS, Wyatt DAWN) (p. 68). |
| none | WinGPC starts in "Reprocessing Mode" (p. 67). |

So the chromatographic signal is usually **not** coming through the Agilent
LAN control path; it comes from the UDC810 A/D box (analog detector outputs)
or digital detector links. Interface menu: Information (type/serial of the
PSS data interface), UDC Verification, LAN Interface / NetConnect settings,
Reset (p. 179-180).

Signal types recorded per channel (p. 173, Table 12): Standard (0), UV (1),
RI (2), Concentration (24), Column temperature (64), Temperature (65),
**Pump pressure in bar (66)**, Inlet pressure Pa (71), Difference pressure Pa
(72), Light scattering (27), Rh signal (28), Other (100). Up to 6 signals per
instrument, up to 4 instruments per WinGPC instance (p. 176, 181).
`Definition > Data interval` sets per-detector sampling intervals "if oven
temperature or pump pressure is to be recorded beside the chromatographic
data" (p. 182).

## 3. Instrument control: ChromPilot

* Requires the ChromPilot option; third-party modules need vendor drivers
  (p. 541). Control is over **LAN (TCP/IP)**: "one module per system has to
  be connected to the data acquisition PC via LAN", static IP, same subnet;
  the IP is entered as "Port Address" in the ChromPilot Connection Dialog
  (p. 542). Agilent LAN cards: G1369A (DIP switch 5 "Using Stored"),
  integrated LAN on 1200-series modules (DIP 7), MIO G1846A (web-browser
  config) (p. 543-545). "WinGPC Software uses this connection to control the
  modules, but not for data acquisition" (p. 543).
* Driver technology: Agilent **RC.Net** drivers under the Instrument Control
  Framework (ICF); module panels in the Instrument Manager exist "for
  modules which are supported by special rc.NET drivers" (p. 476). ChromPilot
  needs .NET Framework 3.5 (PSS product page). Supported hardware (PSS):
  PSS SECcurity, Tosoh EcoSEC, HP/Agilent 1100/1200/1260 modules, Dionex
  systems (different UI, p. 479).
* Configuration Manager (p. 469-475, "ChromPilot Instrument Configuration
  window" at start-up, p. 69) defines modules per instrument; stored as
  `PSS.ChromPilot.DeviceConfiguration.Instrument.n.xml`.
* **Instrument Manager** (CP button in the status bar / ChromPilot button in
  the Method window): System View with each module's setpoints and actual
  values, two status diodes (module state, analysis state) with tooltip
  reasons, direct-control box (e.g. Pump on, detector Zero), end-action box,
  **Apply**, **Get System Ready**, OK; File > Load settings... / Load
  settings with comparison... / Save settings... (`*.spm`, "including end
  action definitions and timetables, but not the direct control status"),
  File > Print > Method / Device config / Logbook; View > Settings / Logbook
  (p. 476-486). Flow, oven temperature and injection volume are copied into
  the Method window automatically (p. 477).
* **Timetables (gradients)**: "If certain system parameters are to be
  changed during a sample run (e.g., a solvent gradient needed for HPLC
  runs), this needs to be defined using a time table. For information about
  the availability of timetables please refer to the according WinGPC
  Software system control documentation. Once defined, a timetable can be
  saved and managed in the instrument settings (*.spm). Timetables will be
  started with the next injection." (p. 502). The editor itself is in the
  separate **WinGPC System Control documentation** (Documentation folder of
  the installation medium / program folder) - not yet obtained.
* **Sequence Manager** (Sequence button / Raw Data window Editor > Samples
  on a controlled instrument, p. 489): icon bar Open/Save (`*.sps`), Print,
  Help, Clear table, Import (`*.sps` or WinGPC `*.txt` sequences), Export
  (`*.sps` or `*.txt`), Default Settings (p. 490); status bar with current
  step, Sequence Summary (estimated/elapsed/remaining time, solvent), start
  and stop functions with colour-coded status (p. 491); table columns Run,
  Pos., Action type, Parameter, Sample name, Conc., Inj.Vol., Inj./Vial,
  Interval, Done, Sample type, Substance, Account (p. 493).
  Action types: Injection; Load WinGPC method (`*.MET`); **Load instrument
  settings (`*.spm`)** "especially helpful if complex settings (e.g.,
  gradients in form of timetables) shall be loaded"; Sequence command
  (single module parameter, WinGPC: Pause sequence, WinGPC: Load automation
  file `*.AUT`, Change Operator); Switch UDC relais o1-o8 (p. 500-501).
  Green **Start** ("Start Sequence", top right) starts autosampler and
  acquisition and opens a new login; **Stop** stops both immediately, a
  restart continues at the first unfinished row; **Pause** delays only the
  next injection (p. 502-503). The sequence does not start until all modules
  are ready; it terminates on a module alarm (p. 483).
* Toolwindow Start/Stop (Window > Toolwindows > Start/Stop): for a
  non-controlled instrument the buttons are Record / Stop / Pause (data
  capture triggered by the injector signal on the UDC); for a controlled one
  Start Baseline / Sequence / CP (p. 175-176, 489). Toolbar Instruments: one
  icon per instrument, colour = state (grey ready, yellow pausing/not ready,
  green acquiring, red error), tooltip shows status (p. 176).
* End actions (pump off, reduce flow, UV lamp off, recycle eluent, column
  heater off, valve to standby, Disconnect system, close WinGPC) run at
  sequence end if activated in both managers (p. 480-482).
* Logbook: Instrument Audit Trail, encrypted monthly XML
  `Logbook.Instrument[n].[year].[month].xml` in `C:\wingpc_8#1`; the view
  shows all ChromPilot-to-module communication including injection times
  (p. 484-485).

## 4. Files and folders (what to look at on disk)

Cache folder **`C:\wingpc_8#n`** (n = program instance; several instances
possible for several interfaces) (p. 550-552):

| File | Meaning |
|---|---|
| `WinGPC_8.INI` | general user settings, **default directories** |
| `WinGPC_8.BTL` | item list (columns, detectors, operators) |
| `WinGPC_8.EDT` | last Data Editor contents |
| `WinGPC_8.PRO` | NetConnect settings |
| `WinGPC_8_yyyy_mm.LOG` | session audit trail, monthly, **encrypted** |
| `Instrumentn.*` | default method files for instrument n (`Instrumenti.set`, p. 177) |
| `PSS.ChromPilot.DeviceConfiguration.Instrument.n.xml` | module configuration (also a default copy in the program folder) |
| `PSS.ChromPilot.Method.Instrument.n.xml` | temporary / current instrument method (setpoints, timetables) |
| `PSS.ChromPilot.Sequence.Instrument.n.xml` | temporary / current sequence table |
| `to_configuratorn.dat`, `from_configuratorn.dat` | temporary configuration exchange files between WinGPC and the ChromPilot configurator |
| `KonfPara.txt` | module settings (SystemPilot only) |
| `System/ChromPilot.INI` | whether the connection dialog is shown on start-up |
| `Logbook.Instrumentn_yyyy_mm.log` | instrument audit trail, encrypted |
| `CH.*, K.*, V.*` | temporary curve cache, deleted on regular exit |
| `INJ.SDB`, `INJ.SDC` | sample search database |
| `S001_pss.drd` | calibration search index per drive |

Project files (one name, several extensions, p. 87, 550-551): `*.LDX`
(logins: start/stop times), `*.MDX` (raw data), `*.INX` (inject positions,
names, baseline and integration limits), `*.FSX` (processing parameters,
method settings), `*.SAX` (inject-dependent settings), optional `*.ATX`
(injection audit trail), `*.SPX` (3D spectra), `*.MSX` (MS). Up to 2048
logins per project and 256 injects per login; a login is created at every
acquisition start. WinGPC "saves the data independently as well in
pre-defined intervals" during acquisition (Raw Data > Save forces a write,
p. 203). The MDX raw-data format is binary and undocumented; "WinGPC
Software will use a modified file format to save data" compared with
older versions (p. 9).

Other files: `*.MET` method (data capture + processing defaults, may embed
`*.CAL` and `*.MAS`), `*.CAL` calibration, `*.MAS` multi-area, `*.REF` HPLC
reference table (ASCII), `*.REL` relay table (ASCII), `*.ADD` overlay,
`*.LST`/`*.LSP` ReportDesigner layout/printer, `*.nrm` MALLS normalisation,
`*.spm` ChromPilot instrument settings, `*.sps` ChromPilot sequence, `*.AUT`
automation settings, `Substanz.ACC` (user-editable ASCII substance list),
`PSS-*.TXT` ReadyCal references (p. 550-552).

ASCII formats that are documented:

* Sample list import (Raw Data window Injects/Editor > Samples import):
  blocks `define sample {No}` ... `end define sample {No}` with German keys
  (`Probenname`, `Probentyp`, `Injektvolumen (ul)`, `Konz. Komp. 1 (g/l)`,
  `Molmasse Komp. 1`, `Mark-Houwink K/A`, `Brechungsink. dn/dc`, ...)
  (p. 216). The Sequence Manager can import such `*.txt` sequences (p. 504).
* Elugram Export ASCII: columns "volume, detector signal 1, detector signal
  2, etc." (p. 230); Raw Data > ASCII save captured data: raw signals, no
  baseline/volume correction (p. 204); Raw Data > ASCII save injects:
  "1: 0.000 ml, Monday 21.02.94 16:02:49:12, BHT" per inject (p. 205).
* Mass Distribution File > Save ASCII Report: "all results, sample specific
  parameters, analysis conditions and the displayed data in all graphical
  windows ... using a simple macro language on any target computer";
  separators follow the Data Editor's Columns/Lines settings (p. 262, 288).
* Project management Import from...: Standard ASCII (first column = time in
  minutes), EZChrom ASCII/CSV, TSP-CDIF, HPLC ChemStation, ... (p. 275-276).

## 5. Automation and unattended output (what WinGPC does by itself)

* Method window `Definition > Automation properties` opens "WinGPC Software
  Batch Processing Settings" (General / Report / New Calib / HPLC tabs).
  Report tab, "Additional Results as": **Create ASCII MWD Report** "will
  automatically create an ASCII file for each sample with all method
  parameters, results and graphics (the output is identical to the Save
  ASCII Report function)"; template `instrument1.txt` yields
  `001_instrument1.txt` for injection 1, **in the project file folder**
  (p. 130). Active only after `Definition > Automation activated` (p. 131).
  Settings save/load as `*.AUT`, and a sequence row can load an `*.AUT`
  (p. 131, 500). Multi-area results can be included "for LIMS transfers,
  macro programming" (p. 220).
* Mass Distribution File > **FTP Report**: "GPC result transfer to external
  databases (LIMS etc) via FTP transfer. Please contact Agilent about proper
  configuration" (p. 262). A second unattended output channel.
* Raw Data > Print Report (ReportDesigner): output "to the printer, to
  screen, to disk or published as HTML" (p. 205); ReportDesigner formats
  include PDF and Excel (Agilent export overview). Printer emulation options
  mention "export only data" (text dump line 12512).
* Instrument Scheduler (Definition menu): "temporarily deactivated"
  (p. 181). Start/Stop conditions by time or inject number exist for
  non-controlled instruments (p. 182).
* WinGPC Session Logbook (Toolwindow Audit Trails) records all automation
  events (p. 127).

## 6. User interface facts (for UI Automation)

* Start-up: authentication dialog (Windows logon name is the default
  operator; optional common password via KeyViewer; Compliance Edition
  requires login), optional Wizard Selection dialog, Login dialog (data
  acquisition source, instruments, add-ons), UDC Device Connection dialog,
  ChromPilot Instrument Configuration window (p. 65-69). No command-line
  switches are documented.
* MDI application; the menu bar follows the active sub-window (p. 155-164):
  Method window (Method, Interface, Definition, Language, Window, Help), Raw
  Data (Raw Data, Calibration Data, Injects, Editor, Options), Elugram
  (File, Overlay, Curves, Options), Mass Distribution (File, Options), Data
  Editor (File, Columns, Lines), Calibration window (File, Calibration,
  Table, Options). Default tiling: Method top-left, Raw Data bottom-left,
  Elugram bottom-right, MWD top-right; Window > Tile, Single Raw Data
  Window, Toolbars, Toolwindow (p. 162-163).
* Each chromatogram window: title bar, graphics area (zoom by drag,
  right-click unzoom), information box (Window > Information), X axis
  (context menu: scale, properties, elution volume vs time), one or more Y
  axes with interactive scale buttons and Norm./Standard scaling (p. 84-86,
  196-197). The active detector is shown in the status bar and switchable
  by clicking its name (p. 196). The status bar Sample field lists open
  logins and instruments (p. 88).
* Raw Data window "shows the real-time view of raw data capture" (p. 195);
  sample names appear next to injection markers as "Vial 1: PS0815 -1"
  (p. 503).
* Compliance Edition `Window > Lock WinGPC Software` shows a LOCKED splash
  that blocks all interaction until the launching user's password is typed
  (p. 162).
* Help menu has a "Step by step" online help with how-to videos, and the
  Quick Reference Guides live in the Documentation folder (p. 11-12, 164).

## 7. What is absent

* No COM/OLE object, .NET API, REST/SDK, command-line switch or scripting
  language is mentioned anywhere in the manual. The word "macro" refers to
  processing the ASCII report elsewhere (p. 262).
* No live data feed: during acquisition the trace exists on screen and in
  the periodically written `*.MDX`; nothing is pushed to a file, socket or
  database in real time.
* The pump timetable editor and the `*.spm` / `*.sps` / `*.AUT` layouts are
  not described (System Control documentation).
* The only published third-party integration (Chemotion ELN) does not talk
  to WinGPC: a folder watcher forwards manually saved `.txt` exports by
  SFTP/WebDAV, and "remote control" is a VNC server on the PC.

## 8. Concrete reverse-engineering leads, in order of promise

1. **ChromPilot XML files** in `C:\wingpc_8#1`: `PSS.ChromPilot.Method.
   Instrument.n.xml` (setpoints, timetables), `...Sequence...xml`,
   `...DeviceConfiguration...xml`. If they are plain XML, a gradient can be
   written by editing the method XML or an exported `*.spm` and loading it
   with File > Load settings... (or a sequence row "Load instrument
   settings"). Compare an `*.spm` saved before and after adding a timetable
   row to learn the schema. Also diff `to_configurator*.dat` /
   `from_configurator*.dat` while opening the Configuration Manager.
2. **Agilent RC.Net / ICF driver layer.** ChromPilot talks to Agilent
   modules through Agilent's own driver DLLs (RC.Net, "Instrument Control
   Framework"). The driver assemblies will be .NET (inspectable with
   ILSpy/dnSpy for the public driver interfaces and the XML method schema
   they serialise). ICF/RC.Net communication with the modules is over LAN
   (Agilent's proprietary protocol on the module's LAN card); Agilent also
   licenses ICF to other CDS vendors (Waters Empower, Thermo Chromeleon,
   Bruker), so the same modules can be driven by another ICF host, or by
   Agilent OpenLab / Lab Advisor, independently of WinGPC. Watch the LAN
   traffic (Wireshark on the PC, module IP) during Get System Ready and a
   sequence start to see the control protocol.
3. **PSS UDC810 data path.** If detector signals come through the UDC810
   over LAN ("connection type Eth", serial 00FFFF for the digital UDC) or
   USB/COM, the raw signal stream is on that link. Capture it (Wireshark
   for LAN, a serial sniffer or USB capture for COM/USB) to read detector
   values in real time without WinGPC. Interface > Information retrieves
   the UDC type/serial and "requests an update of the method settings to
   the Data Interface" (p. 179), i.e. WinGPC pushes channel configuration
   to the box; the UDC verification plug and "UDC Training Mode" suggest a
   documented service protocol. The 8 output relays and injector inputs
   (p. 536-540) are a hardware trigger path.
4. **The `*.MDX` raw-data file**, written at intervals during acquisition:
   watch its growth and diff consecutive snapshots during a run to locate
   the sample stream (likely fixed-width records per detector). `*.INX`
   holds inject positions and names, `*.LDX` login start/stop times; these
   are small and may be simple to decode. Open a project in a hex editor
   while WinGPC runs a known synthetic signal (e.g. the System
   Verification reference data, p. 178).
5. **Automation output as a near-real-time feed**: Create ASCII MWD Report
   plus Automation activated yields one text file per sample within
   seconds of each sample's end; a folder watcher gives per-sample data
   without touching the UI. FTP Report is a push alternative.
6. **Session and instrument logbooks** (`*.LOG`, `Logbook.Instrument*.xml`)
   are "encrypted"; the ChromPilot logbook viewer decrypts them in-app, so
   the key lives in the executable. Low value: the same information is on
   screen.
7. **UI Automation tree**: dump with pywinauto (`uia` backend) or Inspect.exe;
   check whether the chart pane exposes a UIA value pattern (some .NET
   charts do) and the names/automation ids of Start/Stop/Pause, CP,
   Sequence, Record, the Save dialogs, and the Instrument Manager panels.
8. **Registry**: `HKLM\SOFTWARE\...\WinGPC`, `PSS`, `Agilent` keys for
   install paths, and `HKCR` for any registered ProgID/TypeLib (none
   expected, but `wingpc-agent probe` checks).
9. **Program folder**: Documentation sub-folder (Quick Reference Guides,
   System Control documentation, ReportDesigner guide), `Lang.DLL`, help
   files with the "Step by step" videos; look for any `*.xsd`, `*.xml`
   templates or driver manifests shipped with ChromPilot.

## 9. What the control layer already does (so the other AI does not redo it)

* MCP server with 31 tools: UI Automation (tree, find, click, type, menu,
  keys, screenshot), 20 YAML recipes keyed to the documented menus,
  real-time plot capture with pixel-to-trace extraction, ASCII export
  parsing (any separator/decimal, multi-block reports), a policy gate with
  human confirmation tokens and an audit log.
* A discovery probe that dumps the UIA tree, registry entries, install and
  cache folders, `WinGPC_8.INI` directories, and whether the ChromPilot XML
  files are plain text.
* A bridge for Gradient Bench LLM: reads the proposed gradient, applies it
  (manual / template `*.spm` / UI recipe), starts the sequence, watches
  status, trace and pump pressure, exports (recipe or the automation
  per-sample file), uploads to GBL, triggers its read.
* A headless simulated LC and a Tk stand-in window for rehearsal; 40 tests.
* Nothing has yet been run against a real WinGPC installation.

## 10. Safety notes for experiments on the instrument PC

* Stop in the Sequence Manager halts autosampler and acquisition at once
  and starts a new login on restart; Pause is harmless. Do not experiment
  with end actions (pump off, lamp off) while a column is under flow.
* The Compliance Edition audit trails record every action under the
  Windows logon name; a locked session blocks all automation.
* Keep `runs`, project folders and the cache folder backed up before
  editing any ChromPilot XML by hand; a malformed method file can leave the
  Instrument Manager unable to load settings.
