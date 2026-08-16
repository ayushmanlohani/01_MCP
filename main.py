from mcp.server import MCPServer

from tools import addition

mcp = MCPServer("learning-mcp-server")

mcp.tool()(addition.add)

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8000)
