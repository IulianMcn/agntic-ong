#!/usr/bin/env bash
#
# Deploy script for atomicAqua agent to Bedrock AgentCore via CloudFormation
#
# Usage:
#   ./deploy.sh [create|update|delete|build|push]
#
# Environment variables (optional):
#   STACK_NAME      - CloudFormation stack name (default: atomicAqua-stack)
#   AWS_REGION      - AWS region (default: eu-central-1)
#   AGENT_NAME      - Agent runtime name (default: atomicAqua_agent)
#   MEMORY_NAME     - Name for the AgentCore Memory (default: atomicAqua_memory)
#   ENVIRONMENT     - Environment type (default: production)
#   IMAGE_TAG       - Docker image tag (default: latest)
#

set -euo pipefail

# Configuration with defaults
STACK_NAME="${STACK_NAME:-aqua-agent-stack}"
AWS_REGION="${AWS_REGION:-eu-central-1}"
AGENT_NAME="${AGENT_NAME:-aqua_agent_v1}"
MEMORY_NAME="${MEMORY_NAME:-aqua_memory_v1}"
ENVIRONMENT="${ENVIRONMENT:-production}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

# Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
AGENT_DIR="${PROJECT_ROOT}/atomicAqua"
TEMPLATE_FILE="${SCRIPT_DIR}/template.yaml"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    if ! command -v aws &> /dev/null; then
        log_error "AWS CLI is not installed. Please install it first."
        exit 1
    fi
    
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed. Please install it first."
        exit 1
    fi
    
    # Check AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        log_error "AWS credentials not configured or expired."
        exit 1
    fi
    
    log_success "Prerequisites check passed."
}

# Get AWS account ID
get_account_id() {
    aws sts get-caller-identity --query Account --output text
}

# Get ECR repository name (lowercase for Docker compatibility)
get_ecr_repo_name() {
    echo "$AGENT_NAME" | tr '[:upper:]' '[:lower:]'
}

# Get ECR repository URI
get_ecr_uri() {
    local account_id
    account_id=$(get_account_id)
    local repo_name
    repo_name=$(get_ecr_repo_name)
    echo "${account_id}.dkr.ecr.${AWS_REGION}.amazonaws.com/${repo_name}"
}

# Get full container image URI with tag
get_container_image_uri() {
    echo "$(get_ecr_uri):${IMAGE_TAG}"
}

# Build Docker image
build_image() {
    log_info "Building Docker image..."
    
    if [[ ! -f "${AGENT_DIR}/Dockerfile" ]]; then
        log_error "Dockerfile not found at ${AGENT_DIR}/Dockerfile"
        exit 1
    fi
    
    local image_uri
    image_uri=$(get_container_image_uri)
    local repo_name
    repo_name=$(get_ecr_repo_name)
    local local_tag="${repo_name}:${IMAGE_TAG}"
    
    docker build -t "$image_uri" -t "$local_tag" "$AGENT_DIR"
    
    log_success "Docker image built: ${image_uri}"
}

# Login to ECR
ecr_login() {
    log_info "Logging in to ECR..."
    
    local account_id
    account_id=$(get_account_id)
    
    aws ecr get-login-password --region "$AWS_REGION" | \
        docker login --username AWS --password-stdin "${account_id}.dkr.ecr.${AWS_REGION}.amazonaws.com"
    
    log_success "ECR login successful."
}

# Create ECR repository if it doesn't exist
ensure_ecr_repository() {
    local repo_name
    repo_name=$(get_ecr_repo_name)
    
    log_info "Checking ECR repository..."
    
    if aws ecr describe-repositories --repository-names "$repo_name" --region "$AWS_REGION" &> /dev/null; then
        log_info "ECR repository exists: ${repo_name}"
    else
        log_info "Creating ECR repository: ${repo_name}"
        aws ecr create-repository \
            --repository-name "$repo_name" \
            --image-scanning-configuration scanOnPush=true \
            --encryption-configuration encryptionType=AES256 \
            --region "$AWS_REGION"
        log_success "ECR repository created."
    fi
}

# Push image to ECR
push_image() {
    log_info "Pushing image to ECR..."
    
    local image_uri
    image_uri=$(get_container_image_uri)
    
    docker push "$image_uri"
    
    log_success "Image pushed: ${image_uri}"
}

# Validate CloudFormation template
validate_template() {
    log_info "Validating CloudFormation template..."
    
    aws cloudformation validate-template \
        --template-body "file://${TEMPLATE_FILE}" \
        --region "$AWS_REGION" > /dev/null
    
    log_success "Template validation passed."
}

# Check if stack exists
stack_exists() {
    aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$AWS_REGION" &> /dev/null
}

# Create the CloudFormation stack
create_stack() {
    local image_uri
    image_uri=$(get_container_image_uri)
    
    log_info "Creating CloudFormation stack: ${STACK_NAME}..."
    log_info "Using container image: ${image_uri}"
    
    local repo_name
    repo_name=$(get_ecr_repo_name)
    
    aws cloudformation create-stack \
        --stack-name "$STACK_NAME" \
        --template-body "file://${TEMPLATE_FILE}" \
        --parameters \
            ParameterKey=AgentRuntimeName,ParameterValue="$AGENT_NAME" \
            ParameterKey=MemoryName,ParameterValue="$MEMORY_NAME" \
            ParameterKey=ECRRepositoryName,ParameterValue="$repo_name" \
            ParameterKey=EnvironmentType,ParameterValue="$ENVIRONMENT" \
            ParameterKey=ContainerImageUri,ParameterValue="$image_uri" \
        --capabilities CAPABILITY_NAMED_IAM \
        --region "$AWS_REGION" \
        --tags Key=Application,Value="$AGENT_NAME" Key=Environment,Value="$ENVIRONMENT"
    
    log_info "Waiting for stack creation to complete (this may take a few minutes)..."
    aws cloudformation wait stack-create-complete \
        --stack-name "$STACK_NAME" \
        --region "$AWS_REGION"
    
    log_success "Stack created successfully!"
}

# Update the CloudFormation stack
update_stack() {
    local image_uri
    image_uri=$(get_container_image_uri)
    
    log_info "Updating CloudFormation stack: ${STACK_NAME}..."
    log_info "Using container image: ${image_uri}"
    
    local repo_name
    repo_name=$(get_ecr_repo_name)
    
    set +e
    output=$(aws cloudformation update-stack \
        --stack-name "$STACK_NAME" \
        --template-body "file://${TEMPLATE_FILE}" \
        --parameters \
            ParameterKey=AgentRuntimeName,ParameterValue="$AGENT_NAME" \
            ParameterKey=MemoryName,ParameterValue="$MEMORY_NAME" \
            ParameterKey=ECRRepositoryName,ParameterValue="$repo_name" \
            ParameterKey=EnvironmentType,ParameterValue="$ENVIRONMENT" \
            ParameterKey=ContainerImageUri,ParameterValue="$image_uri" \
        --capabilities CAPABILITY_NAMED_IAM \
        --region "$AWS_REGION" 2>&1)
    exit_code=$?
    set -e
    
    if [[ $exit_code -ne 0 ]]; then
        if [[ "$output" == *"No updates are to be performed"* ]]; then
            log_warn "No template changes detected."
            log_info "To update the runtime with new code, use a new IMAGE_TAG:"
            log_info "  IMAGE_TAG=v2 ./deploy.sh update"
            return 0
        else
            log_error "Stack update failed: $output"
            exit 1
        fi
    fi
    
    log_info "Waiting for stack update to complete..."
    aws cloudformation wait stack-update-complete \
        --stack-name "$STACK_NAME" \
        --region "$AWS_REGION"
    
    log_success "Stack updated successfully!"
}

# Delete the CloudFormation stack
delete_stack() {
    log_info "Deleting CloudFormation stack: ${STACK_NAME}..."
    
    aws cloudformation delete-stack \
        --stack-name "$STACK_NAME" \
        --region "$AWS_REGION"
    
    log_info "Waiting for stack deletion to complete..."
    aws cloudformation wait stack-delete-complete \
        --stack-name "$STACK_NAME" \
        --region "$AWS_REGION"
    
    log_success "Stack deleted successfully!"
    
    local repo_name
    repo_name=$(get_ecr_repo_name)
    log_warn "Note: ECR repository was not deleted. To clean up images:"
    log_warn "  aws ecr delete-repository --repository-name $repo_name --force --region $AWS_REGION"
}

# Show stack outputs
show_outputs() {
    log_info "Stack outputs:"
    echo ""
    aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$AWS_REGION" \
        --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' \
        --output table
}

# Full deployment workflow
deploy() {
    check_prerequisites
    validate_template
    
    # Build and push the container image
    build_image
    ensure_ecr_repository
    ecr_login
    push_image
    
    # Deploy or update stack
    if stack_exists; then
        log_info "Stack exists. Performing update..."
        update_stack
    else
        log_info "Stack doesn't exist. Creating new stack..."
        create_stack
    fi
    
    show_outputs
}

# Main entry point
main() {
    local command="${1:-deploy}"
    
    echo ""
    echo "╔════════════════════════════════════════════════════════════════╗"
    echo "║       atomicAqua AgentCore CloudFormation Deployment           ║"
    echo "╚════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "  Stack Name:   ${STACK_NAME}"
    echo "  Region:       ${AWS_REGION}"
    echo "  Agent Name:   ${AGENT_NAME}"
    echo "  Environment:  ${ENVIRONMENT}"
    echo "  Image Tag:    ${IMAGE_TAG}"
    echo ""
    
    case "$command" in
        create)
            check_prerequisites
            validate_template
            build_image
            ensure_ecr_repository
            ecr_login
            push_image
            create_stack
            show_outputs
            ;;
        update)
            check_prerequisites
            validate_template
            build_image
            ecr_login
            push_image
            update_stack
            show_outputs
            ;;
        delete)
            check_prerequisites
            delete_stack
            ;;
        deploy)
            deploy
            ;;
        outputs)
            show_outputs
            ;;
        build)
            check_prerequisites
            build_image
            ;;
        push)
            check_prerequisites
            build_image
            ensure_ecr_repository
            ecr_login
            push_image
            ;;
        *)
            echo "Usage: $0 [create|update|delete|deploy|outputs|build|push]"
            echo ""
            echo "Commands:"
            echo "  deploy   - Full deployment (build, push, create/update stack)"
            echo "  create   - Build, push, and create new stack"
            echo "  update   - Build, push, and update existing stack"
            echo "  delete   - Delete stack (ECR images preserved)"
            echo "  outputs  - Show stack outputs"
            echo "  build    - Build Docker image only"
            echo "  push     - Build and push to ECR only"
            echo ""
            echo "Environment variables:"
            echo "  IMAGE_TAG  - Docker image tag (default: latest)"
            echo "               Use different tags for versioning:"
            echo "               IMAGE_TAG=v2 ./deploy.sh update"
            exit 1
            ;;
    esac
}

main "$@"
