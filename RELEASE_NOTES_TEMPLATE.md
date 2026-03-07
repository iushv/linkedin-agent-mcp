For an installation guide, refer to the [README](https://github.com/iushv/linkedin-agent-mcp/blob/main/README.md).

## 🐳 Update Docker Installation
**For users with Docker-based MCP client configurations:**
```bash
docker pull iushv/linkedin-agent-mcp:latest
```
The `latest` tag will always point to the most recent release.
To pull this specific version, run:
```bash
docker pull iushv/linkedin-agent-mcp:${VERSION}
```

## 📦 Update DXT Extension Installation
**For Claude Desktop users:**
1. Download the `.dxt` file below
2. Pre-pull the Docker image to avoid timeout issues:
   ```bash
   docker pull iushv/linkedin-agent-mcp:${VERSION}
   ```
3. Double-click the `.dxt` file to install in Claude Desktop
4. Restart Claude Desktop

> **Note:** The pre-pull step is important because Claude Desktop has a ~60 second connection timeout. Without pre-pulling, the initial image download may exceed this limit.
