from strands.models import BedrockModel

# Amazon Nova Lite (smaller, may have higher quota)
MODEL_ID = "eu.amazon.nova-lite-v1:0"

def load_model() -> BedrockModel:
    """
    Get Bedrock model client.
    Uses IAM authentication via the execution role.
    """
    return BedrockModel(model_id=MODEL_ID)