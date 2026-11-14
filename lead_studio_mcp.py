"""
lead_studio_mcp.py - MCP server (stdio) for Lead Studio.

Lets Claude Desktop, or any other MCP client, list and run your Lead Studio tools,
follow their logs and read the lead files - on this computer only.

Claude Desktop: Settings > Developer > Edit Config, then add (use your own path):

  { "mcpServers": { "lead-studio": {
        "command": "python",
        "args": ["C:\\\\Users\\\\admin\\\\Desktop\\\\Modulario\\\\LEAD_STUDIO\\\\lead_studio_mcp.py"] } } }

Restart Claude Desktop. Lead Studio itself does not need to be open for this to work.
"""
import sys, json

import lead_studio as ls

ls.QUIET = True          # stdout carries the protocol; everything else goes to stderr


def main():
    ls.discover_async()
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            res = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            res = [r for r in (ls.mcp_handle(m) for m in msg) if r] if isinstance(msg, list) else ls.mcp_handle(msg)
        if res:
            out.write(json.dumps(res, ensure_ascii=False) + "\n"); out.flush()


if __name__ == "__main__":
    main()
