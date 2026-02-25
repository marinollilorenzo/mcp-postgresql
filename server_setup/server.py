from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv("../.env")

mcp = FastMCP(
    name = "Calculator",
    host = "0.0.0.0",   #only used for SSE transport (localhost)
    port = 8050         #only used for SSE transport (set this to any port)
)

#Add a simple calculator tool
@mcp.tool()
def add(a:int, b:int)-> int:
    """Sum two numbers"""
    return a + b


# Run the server
if __name__ == "__main__":
    TRANSPORT = "stdio" #this is put in .env file
    if TRANSPORT == "stdio":
        print("Running server with stdio transport")
        mcp.run(transport="stdio")
    elif TRANSPORT == "sse":
        print("Running server with SSE transport")
        mcp.run(transport="sse")
    elif TRANSPORT == "streamable-http":
        print("Running server with Streamable HTTP transport")
        mcp.run(transport="streamable-http")
    else:
        raise ValueError(f"Unknown transport: {TRANSPORT}") 
    
    
"""


"""