from dotenv import load_dotenv
from mcp.server import MCPServer

from tools import addition, fetch_content, notes, web_search

load_dotenv()

mcp = MCPServer("learning-mcp-server")

mcp.tool()(addition.add)
mcp.tool()(web_search.web_search)
mcp.tool()(fetch_content.fetch_content)
mcp.tool()(notes.create_note)
mcp.tool()(notes.update_note)
mcp.tool()(notes.get_note)
mcp.tool()(notes.list_notes)
mcp.tool()(notes.delete_note)

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8000)
