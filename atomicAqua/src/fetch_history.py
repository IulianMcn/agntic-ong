#!/usr/bin/env python3
"""CLI tool to fetch paginated conversation history from AgentCore Memory."""

import argparse
from typing import Any

import boto3


def format_event(event: dict[str, Any]) -> str:
    """Format a single event for display."""
    lines: list[str] = []
    event_id = event.get("eventId", "unknown")
    timestamp = event.get("createdAt", "")
    
    for payload_item in event.get("payload", []):
        if "conversational" in payload_item:
            conv = payload_item["conversational"]
            role = conv.get("role", "UNKNOWN").upper()
            content = conv.get("content", "")
            
            # Format content based on type
            if isinstance(content, list):
                text_parts: list[str] = [
                    c.get("text", "") for c in content # type: ignore[reportUnknownMemberType]
                    if isinstance(c, dict) and "text" in c
                ]
                content = " ".join(text_parts)
            
            lines.append(f"[{role}] {content}")
    
    if lines:
        return f"Event {event_id} ({timestamp}):\n" + "\n".join(f"  {line}" for line in lines)
    return f"Event {event_id} ({timestamp}): (no conversational content)"


def create_agentcore_client(region: str) -> Any:
    """Create a boto3 client for bedrock-agentcore service.
    
    Returns BedrockAgentCoreClient (typed if boto3-stubs[bedrock-agentcore] is installed).
    """
    create_client = getattr(boto3, "client")
    return create_client("bedrock-agentcore", region_name=region)


def fetch_history(
    memory_id: str,
    region: str,
    user_id: str,
    session_id: str,
    limit: int,
    next_token: str | None,
) -> None:
    """Fetch and display paginated conversation history."""
    client = create_agentcore_client(region)
    
    # Use paginator for proper cursor-based pagination
    paginator = client.get_paginator("list_events")
    
    pagination_config: dict[str, Any] = {"PageSize": limit}
    if next_token:
        pagination_config["StartingToken"] = next_token
    
    page_iterator = paginator.paginate(
        memoryId=memory_id,
        actorId=user_id,
        sessionId=session_id,
        includePayloads=True,
        PaginationConfig=pagination_config,
    )
    
    # Get first page only (for CLI pagination)
    all_events: list[dict[str, Any]] = []
    resume_token: str | None = None
    
    for page in page_iterator:
        page_dict: dict[str, Any] = page
        all_events.extend(page_dict.get("events", []))
        resume_token = getattr(page_iterator, "resume_token", None)
        break  # Only get one page at a time for CLI
    
    if not all_events:
        print("No events found.")
        return
    
    print(f"=== Conversation History (user: {user_id}, session: {session_id}) ===\n")
    
    for event in all_events:
        formatted = format_event(event)
        print(formatted)
        print()
    
    print(f"--- Showing {len(all_events)} event(s) ---")
    
    if resume_token:
        print("\nMore events available. Use --next-token to fetch next page:")
        print(f'--next-token "{resume_token}"')
    else:
        print("\nNo more events.")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Fetch paginated conversation history from AgentCore Memory"
    )
    parser.add_argument(
        "--user-id",
        required=True,
        help="The user ID (actor_id) to fetch history for",
    )
    parser.add_argument(
        "--session-id",
        required=True,
        help="The session ID to fetch history from",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Number of events per page (default: 20)",
    )
    parser.add_argument(
        "--next-token",
        default=None,
        help="Pagination token from previous response for next page",
    )
    parser.add_argument(
        "--memory-id",
        default=None,
        help="Memory ID (defaults to BEDROCK_AGENTCORE_MEMORY_ID env var)",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="AWS region (defaults to AWS_REGION env var or eu-central-1)",
    )
    
    args = parser.parse_args()
    
    memory_id = "aqua_memory_v1-ou5CPqEFsu"
    region = "eu-central-1"
    
    fetch_history(
        memory_id=memory_id,
        region=region,
        user_id=args.user_id,
        session_id=args.session_id,
        limit=args.limit,
        next_token=args.next_token,
    )


if __name__ == "__main__":
    main()
