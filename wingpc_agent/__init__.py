"""wingpc-agent: a control + observation layer that lets an LLM drive WinGPC UniChrom.

Layers (see docs/ARCHITECTURE.md):
  discovery   -> find out what the installed WinGPC actually exposes
  backends    -> UIA (pywinauto), screen capture, file watching, COM probe
  chromatogram-> turn pixels / ASCII exports into numeric traces + peaks
  stream      -> real-time monitor with a ring buffer and change events
  mcp_server  -> tools an LLM can call (MCP, stdio or streamable-http)
  safety      -> allow / confirm / deny policy and an audit log
"""

__version__ = "0.1.0"
