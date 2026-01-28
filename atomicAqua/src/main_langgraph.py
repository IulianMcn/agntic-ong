"""LangGraph-based agent implementation for AtomicAqua."""

import os
from collections.abc import AsyncGenerator
from typing import Annotated, Any, Literal

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langchain_aws import ChatBedrock
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

app = BedrockAgentCoreApp()
log = app.logger

# Configuration from environment variables
MEMORY_ID: str | None = os.getenv("MEMORY_ID")
REGION: str = os.getenv("AWS_REGION") or "eu-central-1"
MODEL_ID: str = "eu.amazon.nova-lite-v1:0"

# System prompt
SYSTEM_PROMPT = """You are a helpful assistant with code execution capabilities. Use tools when appropriate."""


# --- Tools ---
@tool
def add_numbers(a: int, b: int) -> int:
    """Return the sum of two numbers.

    Args:
        a: First number to add
        b: Second number to add
    """
    return a + b


# --- State Definition ---
class AgentState(TypedDict):
    """State container for the agent graph."""

    messages: Annotated[list[BaseMessage], add_messages]
    session_id: str
    user_id: str


def create_agent_graph(model: ChatBedrock, tools: list[Any]) -> StateGraph[AgentState]:
    """Create the LangGraph agent with tools."""

    # Bind tools to model
    model_with_tools = model.bind_tools(tools)

    async def call_model(state: AgentState) -> dict[str, list[BaseMessage]]:
        """Invoke the LLM with current messages."""
        messages = state["messages"]

        # Prepend system message if not already present
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT), *messages]

        response = await model_with_tools.ainvoke(messages)
        return {"messages": [response]}

    def should_continue(state: AgentState) -> Literal["tools", "__end__"]:
        """Determine if we should call tools or end."""
        messages = state["messages"]
        last_message = messages[-1]

        # If the LLM made a tool call, route to tool node
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            return "tools"
        return "__end__"

    # Build the graph
    workflow: StateGraph[AgentState] = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("agent", call_model)
    workflow.add_node("tools", ToolNode(tools))

    # Add edges
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", should_continue, ["tools", "__end__"])
    workflow.add_edge("tools", "agent")

    return workflow


def get_model() -> ChatBedrock:
    """Create Bedrock chat model."""
    return ChatBedrock(
        model_id=MODEL_ID,
        region_name=REGION,
        streaming=True,
    )


@app.entrypoint  # type: ignore[reportUnknownMemberType]
async def invoke(payload: dict[str, Any], context: Any) -> AsyncGenerator[str, None]:
    """Main entrypoint for the LangGraph agent."""
    session_id = (
        getattr(context, "session_id", None) or payload.get("session_id") or "default"
    )
    user_id = payload.get("user_id", "default-user")
    prompt = payload.get("prompt", "")

    # Initialize model and tools
    model = get_model()
    tools = [add_numbers]

    # Create graph
    workflow = create_agent_graph(model, tools)

    # Compile with memory checkpoint (for conversation continuity within session)
    memory = MemorySaver()
    graph = workflow.compile(checkpointer=memory)

    # Config for this thread/session
    config = {"configurable": {"thread_id": session_id}}

    # Initial state with user message
    initial_state: AgentState = {
        "messages": [HumanMessage(content=prompt)],
        "session_id": session_id,
        "user_id": user_id,
    }

    # Stream the response
    async for event in graph.astream_events(initial_state, config=config, version="v2"):
        kind = event.get("event", "")

        # Log for debugging
        log.debug(f"LangGraph event: {kind}")

        match kind:
            case "on_chat_model_stream":
                # Stream token-by-token from the LLM
                chunk = event.get("data", {}).get("chunk")
                if (
                    chunk
                    and hasattr(chunk, "content")
                    and isinstance(chunk.content, str)
                ):
                    yield chunk.content

            case "on_tool_start":
                tool_name = event.get("name", "unknown")
                yield f"\n🔧 Using tool: {tool_name}...\n"

            case "on_tool_end":
                tool_output = event.get("data", {}).get("output", "")
                yield f"📤 Tool result: {tool_output}\n\n"


def format_response(result: Any) -> str:
    """Format final response if needed."""
    if isinstance(result, dict) and "messages" in result:
        messages = result["messages"]
        if messages:
            last_msg = messages[-1]
            if hasattr(last_msg, "content"):
                return str(last_msg.content)
    return str(result)


if __name__ == "__main__":
    app.run()  # type: ignore[reportUnknownMemberType]
