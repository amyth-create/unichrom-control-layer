"""Bridge to Gradient Bench LLM (https://github.com/amyth-create/gradient-bench-llm).

Gradient Bench LLM (GBL) proposes an HPLC gradient method (a %B timetable and a
column temperature), waits for the analyst to run it and upload the detector's
ASCII export, has its model read the export, and records the analyst's verdict.
Its loop is: propose -> upload -> read -> record.

This package closes the "run it on the instrument" gap with WinGPC UniChrom:

    GBL pending method  --->  gbl.method   (gradient table -> WinGPC/ChromPilot method)
                        --->  gbl.bridge   (apply, start run, wait, ASCII export)
    ASCII export        --->  gbl.client   (POST /api/upload, optional /api/read)

Nothing in GBL is modified: the bridge speaks its existing HTTP API (or, when
both run in one Python environment, its campaign folder through the
gradient_llm package).
"""
