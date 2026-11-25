# What the WinGPC user guide settles

Source: Agilent WinGPC Software for GPC/SEC Data Acquisition and Analysis,
User Guide D0029603 Rev. B, edition 07/2024, software revision 1.1 (page
numbers below). This replaces the guesses in RESEARCH.md where they differ.

## Windows and menus (what the recipes click)

WinGPC is an MDI application and the menu bar follows the active sub-window
(p. 84-86, 155-164). Default tiling: Method window top left, Raw Data bottom
left, Elugram bottom right, Mass Distribution top right.

| Sub-window | Menus | Items the layer uses |
|---|---|---|
| Method | Method, Interface, Definition, Language, Window, Help | Method > Load / Save Method as (*.MET) p. 177; Definition > Automation properties / Automation activated / Data interval p. 181-182; Window > Toolwindows > Start/Stop, Window > Toolbar > Instruments p. 175 |
| Raw Data | Raw Data, Calibration Data, Injects, Editor, Options | Raw Data > Load / Save / Save analysis / ASCII save captured data / ASCII save injects / Print Report p. 203-205; Editor > Samples (opens the Sequence Manager on a controlled instrument) and Editor > Slice data (to the Data Editor) p. 208 |
| Elugram | File, Overlay, Curves, Options | File > Export ASCII: columns volume, detector signal 1, 2... p. 230 |
| Mass Distribution | File, Options | File > Save ASCII Report (all results, parameters, all window data), File > FTP Report (LIMS transfer) p. 261-262 |
| Data Editor | File, Columns, Lines | File > ASCII Import... / ASCII Export...; Columns and Lines menus set the separators and the decimal sign, also used by Save ASCII Report p. 286-289 |
| Project management | buttons | Data to WinGPC, Data to overlay, Export data (logins to a new project), Search sample p. 269-285 |

The Toolwindow Start/Stop in the status bar shows Record / Stop / Pause for an
instrument that is not controlled, and Start Baseline / Sequence / CP for a
ChromPilot-controlled one (p. 175-176, 489). The Toolbar Instruments shows one
icon per instrument whose colour is the run state: grey ready, yellow pausing
or not ready, green acquiring, red error (p. 176).

## ChromPilot (instrument control)

* Instrument Manager (CP button or Method window button): System View with
  every module's setpoints and actual values, two status diodes per module,
  direct-control box, end actions, Get System Ready, Apply, OK. Menus File >
  Load settings... / Load settings with comparison... / Save settings...
  (*.spm, includes timetables and end actions) and View > Logbook (p. 476-486).
* Sequence Manager (Sequence button or Editor > Samples): icon bar Open /
  Save (*.sps), Print, Clear table, Import (*.sps or WinGPC *.txt sequences),
  Export (*.sps or *.txt), Default Settings; status bar with the current
  sequence step, Sequence Summary (estimated, elapsed and remaining time,
  solvent), start / stop functions and colour-coded status (p. 487-491).
* Sequence table columns: Run, Pos., Action type, Parameter, Sample name,
  Conc., Inj.Vol., Inj./Vial, Interval, Done, Sample type, Substance, Account
  (p. 493). Action types: Injection, Load WinGPC method (*.MET), Load
  instrument settings (*.spm, "especially helpful if complex settings (e.g.,
  gradients in form of timetables) shall be loaded"), Sequence command
  (single module parameter; WinGPC: Pause sequence; WinGPC: Load automation
  file *.AUT; Change Operator), Switch UDC relais (p. 499-502).
* Timetables (gradients) are defined per module in the instrument settings
  and start with the next injection; their availability depends on the driver
  and is described in the separate WinGPC System Control documentation
  (p. 502). That document is not in the user guide.
* Start: the green Start button ("Start Sequence", top right). Stop stops the
  autosampler and the acquisition immediately; a restart continues with the
  first unfinished row and opens a new login. Pause delays the next injection
  only (p. 502-503). A sequence will not start until all modules are ready,
  and terminates on a module alarm (p. 483).
* Module and analysis status: two diodes per module; tooltips give the reason
  for not-ready and error states (p. 482-483). Logbook: Instrument Audit Trail,
  monthly encrypted files `Logbook.Instrument[n].[year].[month].xml` (p. 485).

## Automation (per-sample files without clicking)

Definition > Automation properties opens "WinGPC Software Batch Processing
Settings" with General, Report, New Calib and HPLC tabs. The Report tab option
**Create ASCII MWD Report** "will automatically create an ASCII file for each
sample with all method parameters, results and graphics (the output is identical
to the Save ASCII Report function)"; a template name such as `instrument1.txt`
yields `001_instrument1.txt` for injection 1, in the project file folder.
Automation runs only after Definition > Automation activated is ticked. All
settings can be saved and reloaded (*.AUT), and a sequence row can load an
*.AUT file (p. 127-131, 181, 500). Multi-area results and slice lists can be
included "for LIMS transfers, macro programming" (p. 220).

This is the file-based automation surface: the layer's `export_strategy:
automation_file` waits for the new `NNN_<template>` file instead of driving
the export menu.

## Signals, pressure and data

* Up to 6 detector signals per instrument; signal types include Column
  temperature (64), Temperature (65), **Pump pressure in bar (66)**, Inlet and
  Difference pressure in Pa (71, 72); Definition > Data interval allows a
  different sampling interval "if oven temperature or pump pressure is to be
  recorded beside the chromatographic data" (p. 172-173, 182). So pressure can
  be a recorded channel that appears in the Raw Data window and in the ASCII
  exports, as well as a live value in the Instrument Manager.
* A project is a sample database of at least five files with one name:
  `*.LDX` (logins, start/stop times), `*.MDX` (raw data), `*.INX` (injects,
  names, limits), `*.FSX` (processing parameters), `*.SAX` (inject settings),
  plus `*.ATX`, `*.SPX`, `*.MSX` with add-ons. Up to 2048 logins per project,
  256 injects per login; a login is created at every acquisition start
  (p. 87-89, 550-551).
* WinGPC saves data to disk "in pre-defined intervals" during acquisition and
  on Raw Data > Save (p. 203). The raw data file format is binary and
  undocumented.
* Other file types: `*.MET` method, `*.CAL` calibration, `*.MAS` multi-area,
  `*.REF` HPLC reference table (ASCII), `*.REL` relay table (ASCII), `*.LST`
  ReportDesigner layout, `*.spm` ChromPilot instrument settings, `*.sps`
  ChromPilot sequence, `*.AUT` automation settings (p. 550-552).
* Cache folder `C:\wingpc_8#n` (n = program instance): `WinGPC_8.INI`
  (default directories), `Instrumentn.*` default methods, the ChromPilot files
  `PSS.ChromPilot.Method.Instrument.n.xml`,
  `PSS.ChromPilot.Sequence.Instrument.n.xml`,
  `PSS.ChromPilot.DeviceConfiguration.Instrument.n.xml`, encrypted session and
  instrument logbooks (p. 551-552). The probe lists this folder and reports
  whether the ChromPilot files are plain XML.
* ASCII import formats: Elugram "volume, detector signal 1, detector signal 2"
  with decimal point and comma or tab separators (p. 230); Project management
  Import from... Standard ASCII interprets the first column as time in minutes
  (p. 275-276); sample lists `define sample {No} ... end define sample {No}`
  with German keys such as `Probenname`, `Injektvolumen (ul)` (p. 216).

## Start-up and remote use

* Start-up goes through an authentication dialog, the Login dialog (data
  acquisition source: PSS-UDC810, COMx, NetConnect; instruments; add-ons), the
  UDC Device Connection dialog and the ChromPilot Instrument Configuration
  window. No command-line switches are documented (p. 65-69).
* Networked data capture (NetConnect) and a Client/Server edition exist for
  legacy licences only (p. 70).
* Compliance Edition: login, user levels, electronic signatures, audit trails;
  "Lock WinGPC Software" splash screen blocks interaction (p. 162, 507-529).
  UI Automation will not get past a locked session.

## Still not in the user guide

* The pump timetable editor and which drivers offer it (System Control
  documentation).
* The content and layout of `*.spm`, `*.sps`, `*.AUT` and the exact columns of
  "ASCII save captured data" and "Save ASCII Report".
* Any COM, command-line or scripting interface: none is mentioned anywhere in
  the 555 pages. "Macro language" refers to processing the ASCII report on
  another computer (p. 261), not to scripting WinGPC.
