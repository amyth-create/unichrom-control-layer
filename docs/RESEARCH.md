# What is publicly known about automating WinGPC UniChrom

**Update 2026-11-25:** the Agilent WinGPC user guide (D0029603 Rev. B) has since been read in full; `MANUAL_NOTES.md` holds the settled facts with page numbers and overrides this file where they differ.

Research date: 2026-11-24. Vendor PDFs (agilent.com, pss-polymer.com) are behind
bot protection and could not be fetched from the build container; the facts
below come from search-engine snippets of those documents plus third-party
pages. Everything marked **unverified** must be checked on the real
installation with `wingpc-agent probe` (see RUNBOOK.md).

## The product

* **WinGPC UniChrom** is PSS Polymer Standards Service's (now Agilent) modular
  *macromolecular chromatography data system* for GPC/SEC, polymer HPLC,
  LACCC and 2D chromatography. Agilent now ships it as "WinGPC Software"
  (user guide G7890 / D0029603 Rev. B, 07/2024).
* Modules: **ChromPilot** (instrument control: Configuration Manager,
  **Instrument Manager**, **Sequence Manager**), **Unity** (data processing,
  automatic (re)calibration), **Compliance** (21 CFR Part 11), copolymer and
  light-scattering modules, **ChromeleonData** (reads sequences from a
  Thermo Chromeleon 7 Data Vault into the WinGPC project structure).
* Instrument control is built on Agilent **RC.Net** driver technology
  (ICF, the Instrument Control Framework). Supported: PSS SECcurity, Tosoh
  EcoSEC, HP/Agilent 1100/1200/1260 LC modules, UDC-810 A/D boxes
  (COM/USB/network). Requires .NET Framework 3.5 for ChromPilot.
* Windows: main **WinGPC** window with **Raw Data window**, **Elugram**,
  **MWD** window, a **Data Editor**, project management window; ChromPilot
  windows for sequences and instrument status.

## Automation surface (what exists, what does not)

| Channel | Status | Notes |
|---|---|---|
| Public API / SDK / COM object model | **Not found** (confirmed by the 555-page manual) | No COM, .NET or REST API is mentioned. The probe still scans the registry for PSS/WinGPC ProgIDs and TypeLibs. |
| Command line / macro / script | **Not found** (manual p. 65-69) | Start-up is a chain of dialogs; no switches. "Macro language" in the manual means post-processing the ASCII report elsewhere. |
| LIMS link | Exists | PSS "standard LIMS interface" transfers results, conditions and graphics; implemented via the **ASCII-MWD report** text file, i.e. file based, not an API. |
| ASCII export (manual) | Documented | Raw Data window -> **Data Editor**; Data Editor **File > ASCII export...** -> `.TXT`; separator and decimal sign set in an "export filter". MWD window **File > Save ASCII Report** writes raw + elugram chromatograms, molar mass distribution and method in one text file. **Column layout not published** -> our parser auto-detects dialect and blocks. |
| Automatic export at end of sample/sequence | **Exists** (manual p. 130) | Definition > Automation properties > Report tab > Create ASCII MWD Report writes `NNN_<template>.txt` per sample into the project folder once Automation activated is ticked. |
| Project export | Documented | Project management window -> select sequence -> **Export data** creates a new project file (binary WinGPC format, undocumented). |
| Report designer | Documented | **ReportDesigner** custom layouts, PDF and Excel output. Not scriptable as far as known. |
| Live data during a run | Only on screen | Raw Data window shows the acquisition in real time; the integration article notes that e-flow evaluation "requires real-time data acquisition", confirming the live stream is internal. No file/DB/network live feed documented. |
| UI Automation | Works for Win32/.NET apps generally | WinGPC is a Win32/.NET desktop app; pywinauto's UIA backend should expose menus, buttons, grids. Owner-drawn charts expose no data -> **pixel extraction**. |

## Consequences for the design

1. **Control = UI Automation** (menus, dialogs, Sequence Manager buttons),
   expressed as *recipes* in YAML because the exact menu captions are
   version- and language-dependent and must be confirmed on site.
2. **Real-time observation = screen capture of the Raw Data plot** +
   trace extraction (colour mask, column median, axis calibration). This
   gives trends, retention times and peak counts at a few Hz; it is not a
   substitute for the exported numbers.
3. **Ground truth = ASCII exports** triggered by the agent (recipe) or by the
   operator, parsed robustly (any separator/decimal, several blocks).
4. **Optional upgrade path = COM**: if the registry probe finds a WinGPC
   automation server, `wingpc-agent com-inspect <progid>` lists its members
   and a `backends/com.py` driver can replace the UI recipes.
5. **Alternative upgrade path = Chromeleon**: labs that acquire with Thermo
   Chromeleon and only *process* in WinGPC can skip WinGPC for live data
   entirely and read the Chromeleon SDK instead (not covered here).

## Sources

* Agilent, *WinGPC Software for GPC/SEC Data Acquisition and Analysis User Guide*
  (G7890-WinGPC-UseMa-en-D0029603) - https://www.agilent.com/cs/library/usermanuals/public/G7890-WinGPC-UseMa-en-D0029603.pdf
* Agilent, *WinGPC Software Export Options* (5994-5943EN) - https://www.agilent.com/cs/library/technicaloverviews/public/to-gpc-sec-software-export-5994-5943en-agilent.pdf
* Agilent WinGPC product page - https://www.agilent.com/en/product/liquid-chromatography/hplc-ce-software/application-specific-software/wingpc-software
* PSS, WinGPC UniChrom MCDS - https://www.pss-polymer.com/products/chromatography-software/wingpc-unichrom-mcds
* PSS, ChromPilot - https://pss-polymer.com/products/chromatography-software/wingpc-unichrom-mcds/chrompilot
* PSS, Software modules - https://www.pss-polymer.com/products/macromolecular-chromatography-data-systems/wingpc-unichrom-mcds/software-modules/
* PSS, ChromeleonData - https://www.pss-polymer.com/products/chromatography-software/wingpc-unichrom-mcds/chromeleondata
* PSS brochure (2020) - https://marketing-art.de/wp-content/uploads/2021/04/20200320_WinGPC_Unichrom_Broschuere_PSS_EN.pdf
* The Analytical Scientist, *Reliable GPC/SEC results by integrating Chromeleon software and WinGPC UniChrom* - https://theanalyticalscientist.com/product-profiles/2018/reliable-gpcsec-results-by-integrating-chromeleon-software-and-wingpc-unichrom
* Agilent Community thread *WinGPC data extraction* - https://community.agilent.com/technical/gpc_sec-solutions/f/forum/8257/wingpc-data-extraction
* PSS WinGPC Unity brochure (German) - https://silo.tips/download/die-vielfalt-makromolekularer-informationen-mit-wingpc-unity
* Agilent Instrument Control Framework - https://www.agilent.com/en/p/chromatography/lc/software/instrument-control-framework
* KIT Chemotion ELN, Agilent/PSS WinGPC device page - https://chemotion.ibcs.kit.edu/docs/eln/devices/hplc_lc-ms/agilent_pss_wingpc
* pywinauto docs - https://pywinauto.readthedocs.io/en/latest/getting_started.html
