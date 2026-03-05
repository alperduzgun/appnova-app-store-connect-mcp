# Contributing

## Getting Started

```bash
git clone https://github.com/alperduzgun/appnova-app-store-connect-mcp.git
cd appnova-app-store-connect-mcp
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in your credentials
```

## Project Structure

| File | Purpose |
|------|---------|
| `server.py` | MCP tool definitions and HTTP middleware |
| `service.py` | App Store Connect API client, JWT auth, per-user state |
| `Dockerfile` | Container for Hugging Face Spaces deployment |

## Adding a New Tool

1. Add the API call method to `AppStoreConnectService` in `service.py`
2. Add the `@mcp.tool()` decorated function in `server.py`
3. Test locally with your credentials in `.env`

## Running Locally

```bash
# stdio mode (Claude Desktop)
python server.py

# HTTP mode (test the hosted flow)
MCP_TRANSPORT=http python server.py
```

## Pull Requests

- Keep changes focused — one feature or fix per PR
- Test with real App Store Connect credentials before submitting
- Update `README.md` if you add or change tool behavior
