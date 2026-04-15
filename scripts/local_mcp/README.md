Local MCP stub

This folder contains the local MCP stub server for development.

Run:

```bash
python3 scripts/local_mcp/local_mcp_server.py --port 8080
```

Endpoints:
- GET /mcp : health check
- POST /mcp : echoes JSON body and headers
