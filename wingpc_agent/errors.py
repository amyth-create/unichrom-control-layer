class WinGPCAgentError(Exception):
    """Base error."""


class BackendUnavailable(WinGPCAgentError):
    """The backend cannot run here (e.g. pywinauto on Linux, WinGPC not running)."""


class ElementNotFound(WinGPCAgentError):
    """A UI element or window could not be located."""


class ActionDenied(WinGPCAgentError):
    """The safety policy refused the action."""


class RecipeError(WinGPCAgentError):
    """A recipe is malformed or unverified."""
