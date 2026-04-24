#!/bin/bash
#
# End-to-end commission document processing script
#
# Usage: ./process_document.sh PROVIDER FILENAME OUTPUT_NAME
#
# Example:
#   ./process_document.sh HDFC "Motor Grid- June'25.eml" hdfc_motor_june25
#

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check arguments
if [ "$#" -lt 3 ]; then
    echo "Usage: $0 PROVIDER FILENAME OUTPUT_NAME"
    echo ""
    echo "Arguments:"
    echo "  PROVIDER     : Provider name (e.g., HDFC, ICICI, United)"
    echo "  FILENAME     : Document filename in inputs/finance/July/PROVIDER/"
    echo "  OUTPUT_NAME  : Output filename (without .json extension)"
    echo ""
    echo "Example:"
    echo "  $0 HDFC \"Motor Grid- June'25.eml\" hdfc_motor_june25"
    exit 1
fi

PROVIDER="$1"
FILENAME="$2"
OUTPUT_NAME="$3"

# Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DATA_DIR="$PROJECT_ROOT/inputs/finance/July/$PROVIDER"
INPUT_FILE="$DATA_DIR/$FILENAME"
OUTPUT_DIR="$PROJECT_ROOT/outputs/$PROVIDER/$OUTPUT_NAME"
OUTPUT_FILE="$OUTPUT_DIR/${OUTPUT_NAME}.json"

# Temp files
TEMP_CONTENT="/tmp/cins_content_$$.txt"
TEMP_PROMPT="/tmp/cins_prompt_$$.txt"

# Cleanup function
cleanup() {
    rm -f "$TEMP_CONTENT" "$TEMP_PROMPT"
}
trap cleanup EXIT

echo -e "${BLUE}================================================${NC}"
echo -e "${BLUE}Processing: $PROVIDER / $FILENAME${NC}"
echo -e "${BLUE}================================================${NC}"
echo ""

# Step 1: Verify input file exists
if [ ! -f "$INPUT_FILE" ]; then
    echo -e "${RED}Error: Input file not found: $INPUT_FILE${NC}"
    exit 1
fi

# Step 2: Create output directory if needed
echo -e "${YELLOW}[1/6] Creating output directory...${NC}"
mkdir -p "$OUTPUT_DIR"
echo "  → $OUTPUT_DIR"
echo ""

# Step 3: Detect file type and extract content
FILE_EXT="${FILENAME##*.}"
echo -e "${YELLOW}[2/6] Extracting content from .$FILE_EXT file...${NC}"

if [ "$FILE_EXT" = "eml" ]; then
    python3 "$SCRIPT_DIR/extract_eml.py" "$INPUT_FILE" > "$TEMP_CONTENT"
    echo "  → Used email extractor"
elif [ "$FILE_EXT" = "pdf" ]; then
    python3 "$SCRIPT_DIR/extract_pdf.py" "$INPUT_FILE" > "$TEMP_CONTENT" 2>/dev/null
    echo "  → Used PDF extractor"
elif [ "$FILE_EXT" = "xlsx" ] || [ "$FILE_EXT" = "xls" ] || [ "$FILE_EXT" = "xlsm" ]; then
    # Check for Gemini API key (recommended for Excel extraction)
    if [ -z "$GEMINI_API_KEY" ] && [ -z "$GOOGLE_API_KEY" ]; then
        echo -e "${YELLOW}  Warning: GEMINI_API_KEY not set - Excel extraction may be limited${NC}"
    fi

    # Check mode (default to sheet_by_sheet)
    EXCEL_MODE="${EXCEL_PROCESS_MODE:-sheet_by_sheet}"
    export EXCEL_PROCESS_MODE="$EXCEL_MODE"

    if [ "$EXCEL_MODE" = "sheet_by_sheet" ]; then
        echo "  → Using sheet-by-sheet mode"
        # Call batch processing script
        python3 "$SCRIPT_DIR/extract_excel_batch.py" "$INPUT_FILE" "$OUTPUT_DIR" "$OUTPUT_NAME" "$LLM_PROVIDER" 2>&1

        # Check if batch processing succeeded
        BATCH_EXIT_CODE=$?
        if [ $BATCH_EXIT_CODE -ne 0 ]; then
            echo -e "${RED}Error: Sheet-by-sheet processing failed${NC}"
            exit 1
        fi

        # Skip remaining steps (batch script handles everything)
        echo ""
        echo -e "${GREEN}✓ Sheet-by-sheet processing complete!${NC}"
        echo -e "${GREEN}✓ Output files in: $OUTPUT_DIR/${NC}"
        echo ""
        echo -e "${BLUE}================================================${NC}"
        exit 0
    else
        echo "  → Using all-at-once mode"
        python3 "$SCRIPT_DIR/extract_excel.py" "$INPUT_FILE" > "$TEMP_CONTENT" 2>/dev/null
        echo "  → Used Excel extractor"
    fi
else
    echo -e "${RED}Error: Unsupported file type: .$FILE_EXT${NC}"
    echo "  Supported: .eml, .pdf, .xlsx, .xls, .xlsm"
    exit 1
fi

# Check if extraction succeeded
if [ ! -s "$TEMP_CONTENT" ]; then
    echo -e "${RED}Error: Content extraction failed (empty output)${NC}"
    exit 1
fi

CONTENT_SIZE=$(wc -c < "$TEMP_CONTENT")
echo "  → Extracted $CONTENT_SIZE bytes"
echo ""

# Step 4: Prepare extraction prompt
echo -e "${YELLOW}[3/6] Preparing extraction prompt...${NC}"
python3 "$SCRIPT_DIR/prepare_prompt.py" "$TEMP_CONTENT" > "$TEMP_PROMPT"

PROMPT_SIZE=$(wc -c < "$TEMP_PROMPT")
echo "  → Generated prompt: $PROMPT_SIZE bytes"
echo ""

# Step 5: Extraction
# Determine provider (default to gemini)
LLM_PROVIDER=${LLM_PROVIDER:-gemini}

echo -e "${YELLOW}[4/6] Extracting...${NC}"
echo "  → Provider: $LLM_PROVIDER"

# Check for appropriate API key based on provider
if [ "$LLM_PROVIDER" = "gemini" ]; then
    # Support both GEMINI_API_KEY and GEMINI_API_KEY
    if [ -z "$GEMINI_API_KEY" ] && [ -z "$GEMINI_API_KEY" ]; then
        echo -e "${RED}Error: Gemini API key not set${NC}"
        echo "  Please set your Gemini API key:"
        echo "    export GEMINI_API_KEY='your-api-key-here'"
        echo "  Or:"
        echo "    export GEMINI_API_KEY='your-api-key-here'"
        exit 1
    fi
elif [ "$LLM_PROVIDER" = "openai" ]; then
    if [ -z "$OPENAI_API_KEY" ]; then
        echo -e "${RED}Error: OPENAI_API_KEY environment variable not set${NC}"
        echo "  Please set your OpenAI API key:"
        echo "    export OPENAI_API_KEY='your-api-key-here'"
        exit 1
    fi
else
    echo -e "${RED}Error: Invalid LLM_PROVIDER '$LLM_PROVIDER'${NC}"
    echo "  Must be 'gemini' or 'openai'"
    exit 1
fi

python3 "$SCRIPT_DIR/llm_extract.py" "$TEMP_PROMPT" "$OUTPUT_FILE" 2>&1 | grep "^#" || true
echo ""

# Check if extraction succeeded
if [ ! -f "$OUTPUT_FILE" ]; then
    echo -e "${RED}Error: LLM extraction failed (no output file)${NC}"
    exit 1
fi

# Step 6: Validate extracted JSON
echo -e "${YELLOW}[5/6] Validating extracted JSON...${NC}"
cd "$PROJECT_ROOT"
python3 validators/validate.py "$OUTPUT_FILE" --verbose

VALIDATION_EXIT_CODE=$?
echo ""

# Step 7: Summary
echo -e "${YELLOW}[6/6] Processing complete!${NC}"
echo ""
echo -e "${GREEN}✓ Input:       $INPUT_FILE${NC}"
echo -e "${GREEN}✓ Output:      $OUTPUT_FILE${NC}"
echo ""

if [ $VALIDATION_EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✓ Validation:  PASSED (no errors)${NC}"
elif [ $VALIDATION_EXIT_CODE -eq 1 ]; then
    echo -e "${YELLOW}⚠ Validation:  WARNINGS (review recommended)${NC}"
else
    echo -e "${RED}✗ Validation:  FAILED (errors found)${NC}"
    echo -e "${RED}  Please review the validation output above${NC}"
fi

echo ""
echo -e "${BLUE}================================================${NC}"

exit $VALIDATION_EXIT_CODE
