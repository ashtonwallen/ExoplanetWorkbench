"""Absolute-path entry point for MCP hosts, independent of their working directory."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent/'backend'))
from exodiscovery.mcp_server import main

if __name__=='__main__':
    main()
