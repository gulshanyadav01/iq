#!/usr/bin/env python3
"""
Sheet Classification Module

Classifies Excel sheets by purpose using heuristics and AI.

Categories:
- COMMISSION_DATA: Main commission calculation tables with rates and business logic
- MASTER_DATA: Supporting reference data, metadata, glossaries, summaries

Usage:
    from cins.processors.sheet_classifier import SheetClassifier, SheetType

    classifier = SheetClassifier()
    classification = classifier.classify_sheet(
        sheet_name="Commission Rates Q1 2024",
        csv_preview=csv_content,
        metadata=sheet_metadata
    )

    if classification.sheet_type == SheetType.COMMISSION_DATA:
        print(f"Commission data sheet: {classification.reasoning}")
"""

import functools
import os
import re
from enum import Enum
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


@functools.lru_cache(maxsize=1)
def _load_classification_prompt() -> Optional[str]:
    """Cached read of the sheet-classification prompt template.

    Returns None if the file is missing so the caller can fall back gracefully.
    """
    prompt_path = (
        Path(__file__).parent.parent.parent / "prompts" / "sheet_classification_prompt.md"
    )
    if not prompt_path.exists():
        return None
    return prompt_path.read_text(encoding="utf-8")

# Import centralized Gemini service
try:
    from cins.processors.llm_services.gemini import GeminiService, GeminiConfig, GeminiServiceError, GeminiAPIKeyMissingError
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    GeminiService = None


class SheetType(Enum):
    """Sheet classification categories."""
    COMMISSION_DATA = "commission_data"
    MASTER_DATA = "master_data"


class SheetClassificationSchema(BaseModel):
    """Pydantic schema for LLM structured output."""
    sheet_type: str = Field(..., description="One of: commission_data, master_data")
    confidence: float = Field(..., description="Confidence score between 0.0 and 1.0")
    reasoning: str = Field(..., description="2-3 sentences explaining the classification")
    indicators: List[str] = Field(..., description="2-4 specific signals used for classification")


@dataclass
class SheetClassification:
    """Classification result for an Excel sheet."""
    sheet_name: str
    sheet_type: SheetType
    confidence: float  # 0.0-1.0
    reasoning: str
    indicators: List[str]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "sheet_name": self.sheet_name,
            "sheet_type": self.sheet_type.value,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "indicators": self.indicators
        }


class SheetClassifier:
    """
    Classifies Excel sheets using AI (LLM-only classification).

    Strategy:
    - Uses Gemini Flash for cost-efficient classification
    - Defaults to COMMISSION_DATA when uncertain
    - Heuristics disabled - relies purely on LLM content analysis
    """

    # Heuristic patterns for sheet names
    COMMISSION_PATTERNS = [
        r'\bcommission\b',
        r'\brate\s*card',
        r'\bpremium\s*slab',
        r'\brate\s*table',
        r'\bcommission\s*rate',
        r'\bpremium\s*range'
    ]

    MASTER_DATA_PATTERNS = [
        r'\bmaster\b',
        r'\breference\b',
        r'\brto\s*code',
        r'\bstate\s*code',
        r'\bzone\s*map',
        r'\bvehicle\s*class',
        r'\bchannel\s*code',
        r'\bmapping\b',
        r'\bcodes\b',
        r'\bmetadata\b',
        r'\binfo\b',
        r'\bdetails\b',
        r'\babout\b',
        r'\bdocument\s*info',
        r'\beffective\s*(period|date)',
        r'\bprovider\s*detail',
        r'\bdefinition',
        r'\bglossary\b',
        r'\bterms\b',
        r'\blegend\b',
        r'\babbreviation',
        r'\bsummary\b',
        r'\btotal\b',
        r'\bdashboard\b',
        r'\breport\b',
        r'\baggregate'
    ]

    def __init__(self, api_key: Optional[str] = None, model_name: str = "gemini-3.1-flash-lite"):
        """
        Initialize classifier.

        Args:
            api_key: Gemini API key (default: from GEMINI_API_KEY env var)
            model_name: Gemini model to use (default: gemini-3.1-flash-lite for cost efficiency)
        """
        self.api_key = api_key or os.environ.get('GEMINI_API_KEY')
        self.model_name = model_name

        # Initialize Gemini service if available
        if GENAI_AVAILABLE:
            try:
                self.service = GeminiService(api_key=self.api_key, model=model_name)

                # Create schema dict for Gemini API
                self.response_schema = {
                    "type": "object",
                    "properties": {
                        "sheet_type": {
                            "type": "string",
                            "description": "One of: commission_data, master_data"
                        },
                        "confidence": {
                            "type": "number",
                            "description": "Confidence score between 0.0 and 1.0"
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "2-3 sentences explaining the classification"
                        },
                        "indicators": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "2-4 specific signals used for classification"
                        }
                    },
                    "required": ["sheet_type", "confidence", "reasoning", "indicators"]
                }
            except Exception as e:
                print(f"Warning: Could not initialize GeminiService: {e}")
                self.service = None
                self.response_schema = None
        else:
            self.service = None
            self.response_schema = None
            print("Warning: GeminiService not available. Install with: pip install google-genai")

    def _match_patterns(self, text: str, patterns: List[str]) -> bool:
        """Check if text matches any of the regex patterns (case-insensitive)."""
        text_lower = text.lower()
        return any(re.search(pattern, text_lower) for pattern in patterns)

    def _heuristic_classify(
        self,
        sheet_name: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[SheetClassification]:
        """
        Heuristic classification disabled - always returns None to force LLM classification.

        Returns:
            None (always falls through to LLM)
        """
        return None

    def _llm_classify(
        self,
        sheet_name: str,
        csv_preview: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> SheetClassification:
        """
        LLM-based classification when heuristics insufficient.

        Args:
            sheet_name: Name of the sheet
            csv_preview: First ~20 rows of CSV content
            metadata: Sheet metadata (dimensions, merged cells, etc.)

        Returns:
            SheetClassification
        """
        if not self.service:
            # Fallback if no service available - default to commission_data
            return SheetClassification(
                sheet_name=sheet_name,
                sheet_type=SheetType.COMMISSION_DATA,
                confidence=0.3,
                reasoning="GeminiService not available for classification, defaulting to commission_data",
                indicators=["GeminiService not initialized"]
            )

        # Load prompt template (cached at module level)
        prompt_template = _load_classification_prompt()
        if prompt_template is None:
            # Fallback classification - default to commission_data
            return SheetClassification(
                sheet_name=sheet_name,
                sheet_type=SheetType.COMMISSION_DATA,
                confidence=0.3,
                reasoning="Classification prompt template not found, defaulting to commission_data",
                indicators=["Missing prompt template"]
            )

        # Extract metadata info
        dims = metadata.get('dimensions', {}) if metadata else {}
        rows = dims.get('rows', 0)
        cols = dims.get('cols', 0)
        header_rows = metadata.get('formatting', {}).get('detected_header_rows', []) if metadata else []
        merged_count = len(metadata.get('merged_ranges', [])) if metadata else 0

        # Format prompt
        prompt = prompt_template.format(
            sheet_name=sheet_name,
            rows=rows,
            cols=cols,
            header_rows=", ".join(map(str, header_rows)) if header_rows else "None",
            merged_count=merged_count,
            csv_preview=csv_preview
        )

        try:
            # Build config with response schema
            config = GeminiConfig(
                temperature=0.1,
                max_output_tokens=4096,
                response_mime_type="application/json",
                response_json_schema=self.response_schema
            )

            # Call GeminiService
            result = self.service.generate_content(prompt, config=config, model='gemini-3.1-flash-lite')

            # Parse JSON response
            import json

            # Clean up result if needed (remove markdown code blocks, etc.)
            result_clean = result.strip()
            if result_clean.startswith('```json'):
                result_clean = result_clean[7:]
            if result_clean.startswith('```'):
                result_clean = result_clean[3:]
            if result_clean.endswith('```'):
                result_clean = result_clean[:-3]
            result_clean = result_clean.strip()

            parsed = json.loads(result_clean)

            # Map string to enum
            sheet_type_str = parsed.get('sheet_type', 'commission_data')
            try:
                sheet_type = SheetType(sheet_type_str)
            except ValueError:
                # Default to commission_data if invalid value
                sheet_type = SheetType.COMMISSION_DATA

            return SheetClassification(
                sheet_name=sheet_name,
                sheet_type=sheet_type,
                confidence=parsed.get('confidence', 0.5),
                reasoning=parsed.get('reasoning', 'LLM classification'),
                indicators=parsed.get('indicators', [])
            )

        except json.JSONDecodeError as e:
            # JSON parsing failed - log details for debugging, default to commission_data
            error_preview = result[:200] if 'result' in locals() else "No response"
            return SheetClassification(
                sheet_name=sheet_name,
                sheet_type=SheetType.COMMISSION_DATA,
                confidence=0.2,
                reasoning=f"LLM returned invalid JSON: {str(e)[:100]}, defaulting to commission_data",
                indicators=["JSON parse error", f"Response preview: {error_preview}"]
            )
        except Exception as e:
            # Other error - fallback to commission_data
            return SheetClassification(
                sheet_name=sheet_name,
                sheet_type=SheetType.COMMISSION_DATA,
                confidence=0.2,
                reasoning=f"LLM classification failed: {str(e)[:100]}, defaulting to commission_data",
                indicators=["LLM error"]
            )

    def classify_sheet(
        self,
        sheet_name: str,
        csv_preview: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> SheetClassification:
        """
        Classify sheet using LLM only (heuristics disabled).

        Args:
            sheet_name: Name of the sheet
            csv_preview: First ~20 rows of CSV content
            metadata: Sheet metadata from SheetMetadata.to_dict()

        Returns:
            SheetClassification with type, confidence, reasoning
        """
        # Use LLM for all classifications
        return self._llm_classify(sheet_name, csv_preview, metadata)


def main():
    """CLI for testing sheet classification."""
    import sys
    import json

    if len(sys.argv) < 3:
        print("Usage: python sheet_classifier.py <sheet_name> <csv_file>")
        print("\nExample:")
        print("  python sheet_classifier.py 'Commission Rates' rates.csv")
        sys.exit(1)

    sheet_name = sys.argv[1]
    csv_path = Path(sys.argv[2])

    if not csv_path.exists():
        print(f"Error: CSV file not found: {csv_path}")
        sys.exit(1)

    # Read CSV preview (first 20 lines)
    with open(csv_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()[:20]
        csv_preview = ''.join(lines)

    # Classify
    classifier = SheetClassifier()
    result = classifier.classify_sheet(sheet_name, csv_preview)

    # Display results
    print(f"\n{'='*80}")
    print(f"Sheet Classification Results")
    print(f"{'='*80}\n")
    print(f"Sheet Name: {result.sheet_name}")
    print(f"Type: {result.sheet_type.value}")
    print(f"Confidence: {result.confidence:.2f}")
    print(f"\nReasoning:")
    print(f"  {result.reasoning}")
    print(f"\nIndicators:")
    for indicator in result.indicators:
        print(f"  - {indicator}")
    print()


if __name__ == '__main__':
    main()
