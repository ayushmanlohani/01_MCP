from dotenv import load_dotenv
from mcp.server import MCPServer

from tools import addition, web_search

load_dotenv()

mcp = MCPServer("learning-mcp-server")

mcp.tool()(addition.add)
mcp.tool()(web_search.web_search)

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8000)
