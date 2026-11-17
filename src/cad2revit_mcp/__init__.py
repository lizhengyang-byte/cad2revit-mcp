"""cad2revit-mcp - LLM driven 2D CAD to Revit BIM MCP tool."""

from importlib.metadata import PackageNotFoundError, version

try:  # single source of truth: the installed distribution metadata
    __version__ = version("cad2revit-mcp")
except PackageNotFoundError:  # running from a bare checkout without install
    __version__ = "0.0.0"
