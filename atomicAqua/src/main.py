import os
from collections.abc import AsyncGenerator
from typing import Any

from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from model.load import load_model
from strands import Agent, tool  # type: ignore[reportUnknownMemberType]

app = BedrockAgentCoreApp()
log = app.logger

# Configuration from environment variables (set by CloudFormation)
MEMORY_ID: str | None = os.getenv("MEMORY_ID")
REGION: str = os.getenv("AWS_REGION") or "eu-central-1"

# Import AgentCore Gateway as Streamable HTTP MCP Client
# Note: MCP client disabled due to network timeout in container
# mcp_client = get_streamable_http_mcp_client()
mcp_client = None

# Define a simple function tool
@tool
def add_numbers(a: int, b: int) -> int:
    """Return the sum of two numbers"""
    return a+b

@app.entrypoint  # type: ignore[reportUnknownMemberType]
async def invoke(payload: dict[str, Any], context: Any) -> AsyncGenerator[str, None]:
    session_id = getattr(context, 'session_id', None) or payload.get("session_id") or "default"
    user_id = payload.get("user_id", "default-user")
    
    # Configure memory
    session_manager = None
    if MEMORY_ID:
        session_manager = AgentCoreMemorySessionManager(
            AgentCoreMemoryConfig(
                memory_id=MEMORY_ID,
                session_id=session_id,
                actor_id=user_id,
                retrieval_config={
                    f"/users/{user_id}/facts": RetrievalConfig(top_k=3, relevance_score=0.5),
                    f"/users/{user_id}/preferences": RetrievalConfig(top_k=3, relevance_score=0.5)
                }
            ),
            REGION
        )
    else:
        log.warning("MEMORY_ID is not set. Skipping memory session manager initialization.")

    
    # Create code interpreter
    # code_interpreter = AgentCoreCodeInterpreter(
    #     region=REGION,
    #     session_name=session_id,
    #     auto_create=True,
    #     persist_sessions=True
    # )

    # Get MCP tools if available
    mcp_tools: list[Any] = []
    if mcp_client is not None:
        with mcp_client as client:
            mcp_tools = client.list_tools_sync()

    # Create agent
    agent = Agent(
        model=load_model(),
        session_manager=session_manager,
        system_prompt="""
            You are a helpful assistant with code execution capabilities. Use tools when appropriate.
        """,
        tools=[add_numbers] + mcp_tools
    )

    # Execute and format response
    stream = agent.stream_async(payload.get("prompt"))

    async for event in stream:
        # Debug: log the event structure
        import json
        print(f"Stream event: {type(event).__name__}\n{json.dumps(event, indent=2, default=str)}")
        print("--------------------------------")
        
        # Handle Text parts of the response
        if "data" in event and isinstance(event["data"], str):
            yield event["data"]

        # Implement additional handling for other events
        # if "toolUse" in event:
        #   # Process toolUse

        # Handle end of stream
        # if "result" in event:
        #    yield(format_response(event["result"]))

def format_response(result: Any) -> str:
    """Extract code from metrics and format with LLM response."""
    parts: list[str] = []

    # Extract executed code from metrics
    try:
        tool_metrics = result.metrics.tool_metrics.get('code_interpreter')
        if tool_metrics and hasattr(tool_metrics, 'tool'):
            action = tool_metrics.tool['input']['code_interpreter_input']['action']
            if 'code' in action:
                parts.append(f"## Executed Code:\n```{action.get('language', 'python')}\n{action['code']}\n```\n---\n")
    except (AttributeError, KeyError):
        pass  # No code to extract

    # Add LLM response
    parts.append(f"## 📊 Result:\n{str(result)}")
    return "\n".join(parts)

if __name__ == "__main__":
    app.run()  # type: ignore[reportUnknownMemberType]