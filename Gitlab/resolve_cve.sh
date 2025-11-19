#!/bin/bash

# Check if pipeline ID argument is provided
if [ -z "$1" ]; then
    echo "Usage: $0 <pipeline_id>"
    echo "Example: $0 12345"
    exit 1
fi

EXCLUDE_PIPELINE_ID="$1"

# Log colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

# Load environment variables from .env file
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../.env"

if [ -f "$ENV_FILE" ]; then
    echo -e "${GREEN}✓ Loading environment from $ENV_FILE${NC}"
    set -a
    source <(sed '1s/^\xEF\xBB\xBF//' "$ENV_FILE" | grep -v '^#' | grep -v '^$')
    set +a
else
    echo -e "${RED}Error: .env file not found at $ENV_FILE${NC}"
    exit 1
fi

# Configuration (use variables directly from .env)
PROJECT_ID="$GITLAB_PROJECT_ID"
PRIVATE_TOKEN="$GITLAB_PRIVATE_TOKEN"
PER_PAGE=100
PROGRESS_FILE="cve_progress_${PROJECT_ID}.txt"

# Validate configuration
if [ -z "$GITLAB_URL" ] || [ -z "$PRIVATE_TOKEN" ] || [ -z "$PROJECT_ID" ]; then
    echo -e "${RED}Error: Missing required environment variables!${NC}"
    exit 1
fi

echo -e "${YELLOW}=== CVE Resolution Script ===${NC}"
echo "GitLab URL: $GITLAB_URL"
echo "Project ID: $PROJECT_ID"
echo "Excluded pipeline: $EXCLUDE_PIPELINE_ID"
echo ""

# Load last processed page if exists
current_page=1
if [ -f "$PROGRESS_FILE" ]; then
    current_page=$(grep "last_page=" "$PROGRESS_FILE" | cut -d'=' -f2)
    [ -z "$current_page" ] && current_page=1
    
    echo -e "${GREEN}✓ Progress found: page $current_page${NC}"
    read -p "Resume at page $current_page or restart? (r/1): " -n 1 -r
    echo ""
    [[ $REPLY =~ ^[1]$ ]] && current_page=1
    echo ""
fi

# Confirmation
read -p "Start processing? (y/n): " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${RED}Cancelled${NC}"
    exit 1
fi

# Statistics
total_resolved=0
total_pages_processed=0

# Main loop
while true; do
    echo -e "${BLUE}======================================== Page $current_page ========================================${NC}"
    
    temp_file=$(mktemp)
    headers_file=$(mktemp)
    
    # Save progress
    cat > "$PROGRESS_FILE" << EOF
last_page=$current_page
timestamp=$(date '+%Y-%m-%d %H:%M:%S')
total_resolved=$total_resolved
EOF
    
    echo -e "${YELLOW}Fetching page $current_page...${NC}"
    
    # Build API URL
    api_url="${GITLAB_URL}/api/v4/projects/${PROJECT_ID}/vulnerabilities?per_page=${PER_PAGE}&page=${current_page}&severity=medium&severity=low"
    
    http_code=$(curl -s -w "%{http_code}" --max-time 30 \
        --header "PRIVATE-TOKEN: ${PRIVATE_TOKEN}" \
        -D "$headers_file" \
        -o "$temp_file" \
        "${api_url}")
    
    if [ "$http_code" -ne 200 ]; then
        echo -e "${RED}HTTP Error: $http_code${NC}"
        rm -f "$temp_file" "$headers_file"
        break
    fi
    
    response=$(cat "$temp_file")
    raw_count=$(echo "$response" | jq '. | length' 2>/dev/null || echo "0")
    
    # Display total pages
    total_pages=$(grep -i "X-Total-Pages:" "$headers_file" | tr -d '\r' | awk '{print $2}')
    [ -n "$total_pages" ] && echo -e "${BLUE}(Page $current_page of $total_pages)${NC}"
    
    # Exit if no more CVEs
    if [ "$raw_count" -eq 0 ]; then
        echo -e "${GREEN}✓ No more CVEs${NC}"
        rm -f "$temp_file" "$headers_file"
        break
    fi
    
    # Filter CVEs
    filtered=$(echo "$response" | jq --arg pid "$EXCLUDE_PIPELINE_ID" \
        '[.[] | select(.resolved_at == null and (.finding.latest_pipeline_id | tostring) != $pid)]')
    
    filtered_count=$(echo "$filtered" | jq '. | length' 2>/dev/null || echo "0")
    echo -e "${GREEN}→ $raw_count total, $filtered_count to resolve${NC}"
    
    # Resolve CVEs in parallel
    if [ "$filtered_count" -gt 0 ]; then
        echo -e "${YELLOW}Resolving $filtered_count CVEs...${NC}"
        
        resolved=0
        failed=0
        
        echo "$filtered" | jq -r '.[] | .id' | while read vuln_id; do
            (
                http_code=$(curl -s -w "%{http_code}" -o /dev/null --request POST \
                    --header "PRIVATE-TOKEN: ${PRIVATE_TOKEN}" \
                    "${GITLAB_URL}/api/v4/vulnerabilities/$vuln_id/resolve")
                
                if [ "$http_code" -eq 200 ] || [ "$http_code" -eq 201 ]; then
                    echo "SUCCESS"
                else
                    echo "FAILED"
                fi
            ) &
        done | while read result; do
            if [ "$result" = "SUCCESS" ]; then
                ((resolved++))
            else
                ((failed++))
            fi
        done
        
        wait
        
        echo -e "${GREEN}✓ Resolved: $resolved | Failed: $failed${NC}"
        total_resolved=$((total_resolved + resolved))
    fi
    
    total_pages_processed=$((total_pages_processed + 1))
    
    # Check for next page
    next_page=$(grep -i "X-Next-Page:" "$headers_file" | tr -d '\r' | awk '{print $2}')
    rm -f "$temp_file" "$headers_file"
    
    [ -z "$next_page" ] && break
    current_page=$next_page
done

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}=== Summary ===${NC}"
echo -e "${GREEN}Pages processed: $total_pages_processed${NC}"
echo -e "${GREEN}Total CVEs resolved: $total_resolved${NC}"
echo -e "${GREEN}========================================${NC}"

# Cleanup
[ -f "$PROGRESS_FILE" ] && rm "$PROGRESS_FILE" && echo -e "${BLUE}✓ Progress file deleted${NC}"

echo -e "${GREEN}✓ Done!${NC}"