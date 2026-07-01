"""
Minerva API + Gemini Two-Stage Parser
Stage 1: Convert PDF to Markdown using Minerva API (http://43.205.3.35:8000/file_parse)
Stage 2: Extract raw label-value pairs using Gemini (free-form extraction)
Stage 3: Map raw data to target schema using Gemini (schema mapping)

The two-stage LLM approach improves field extraction by separating
data discovery from schema mapping.
"""

import os
import sys
import json
import time
import requests
from pathlib import Path
from typing import Dict, Any
from datetime import datetime

from django.conf import settings

from django.conf import settings

try:
    from google import genai
except ImportError:
    print("Error: google-genai not installed")
    print("Install with: pip install google-genai")
    sys.exit(1)


class MineruGeminiParser:
    """
    Three-stage parser for better field extraction:
    1. Minerva API: PDF → Markdown
    2. Gemini Stage 1: Markdown → Raw Data (free-form label-value extraction)
    3. Gemini Stage 2: Raw Data → Structured JSON (schema mapping)

    The two-stage LLM approach helps with missing fields by:
    - Stage 1: Model focuses purely on data discovery (no schema constraints)
    - Stage 2: Model focuses on mapping clean data to schema
    """

    MINERVA_API_URL = os.getenv("MINERVA_API_URL", "https://rhobots.pbpartners.com/api/mineru/file_parse")

    def __init__(self, api_key: str = None, model: str = None, intermediate_dir: str = None):
        """
        Initialize parser

        Args:
            api_key: Google API key (or set GOOGLE_API_KEY env var)
            model: Gemini model to use
            intermediate_dir: Directory to save intermediate markdown/raw JSON files.
                              Defaults to 'minerva_gemini_intermediate_data' in cwd.
                              Pass a validation set path to co-locate with the set.
        """
        self.model_name = model or settings.POLICY_EXTRACT_GEMINI_MODEL
        # Public Gemini Developer API by default. Vertex AI Express mode
        # (vertexai=True + API key) is region-locked and 404s on models absent
        # from the express region (e.g. gemini-2.5-flash-lite in asia-southeast1).
        # Opt into Vertex with GOOGLE_GENAI_USE_VERTEXAI=true (project/location
        # then come from GOOGLE_CLOUD_* env vars or ADC).
        if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in ("1", "true", "yes"):
            # Vertex AI via ADC — no API key (see cins gemini.py). Set
            # GOOGLE_CLOUD_LOCATION to "global" or a regional endpoint like
            # "us-central1" (the bare "us"/"eu" multi-region values need the
            # .rep. hostname the SDK doesn't build and 404). Offline preflight:
            # fail fast on misconfig instead of deferring an opaque auth failure.
            if not os.getenv("GOOGLE_CLOUD_PROJECT"):
                raise RuntimeError(
                    "Vertex mode (GOOGLE_GENAI_USE_VERTEXAI=true) requires GOOGLE_CLOUD_PROJECT."
                )
            # Region must be set and must not be a bare "us"/"eu" multi-region
            # value (the SDK doesn't build the .rep. endpoint, so those 404).
            location = os.getenv("GOOGLE_CLOUD_LOCATION", "").strip()
            if not location:
                raise RuntimeError(
                    "Vertex mode requires GOOGLE_CLOUD_LOCATION (use 'global' or a "
                    "regional endpoint like 'us-central1')."
                )
            if location.lower() in ("us", "eu"):
                raise RuntimeError(
                    f"GOOGLE_CLOUD_LOCATION='{location}' is a multi-region value needing "
                    "the .rep. endpoint this client does not set; use 'global' or a "
                    "regional endpoint like 'us-central1'."
                )
            cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            if cred_path and not Path(cred_path).is_file():
                raise RuntimeError(
                    f"GOOGLE_APPLICATION_CREDENTIALS points to a missing file: {cred_path}"
                )
            self._client = genai.Client(vertexai=True)
        else:
            self._client = genai.Client(api_key=api_key or os.getenv("GOOGLE_API_KEY"))

        # Master-data resolver — backed by the `master_data` Django app.
        # Loaded once per parser instance (~7 small SELECTs); negligible vs
        # minutes of LLM work per PDF.
        from master_data.services.resolver import MasterDataResolver
        self._resolver = MasterDataResolver()

        # Cost tracking
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_tokens = 0
        self.total_cost_usd = 0.0

        # Gemini pricing (per 1M tokens)
        self.pricing = {
            "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
            "gemini-3.1-flash-lite": {"input": 0.075, "output": 0.30},
            "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
            "gemini-3.5-flash": {"input": 0.15, "output": 0.60},
        }

        # Intermediate data directory
        self.intermediate_dir = Path(intermediate_dir) if intermediate_dir else Path("minerva_gemini_intermediate_data")
        self.intermediate_dir.mkdir(parents=True, exist_ok=True)

        # Load RTO CSV mapping for city lookup
        self.rto_city_map, self.rto_reverse_map = self._load_rto_csv()

    def _load_rto_csv(self) -> tuple:
        """Load RTO.csv and build mappings:
        1. RTO code → city name (e.g., MH12 → PUNE)
        2. Reverse: (state, city) → RTO code for new vehicle lookup
        """
        import csv
        import re
        rto_map = {}
        rto_reverse_map = {}  # (state_upper, city_upper) → first matching RTO code
        csv_path = Path(__file__).parent.parent / "RTO.csv"
        if not csv_path.exists():
            return rto_map, rto_reverse_map
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    reg_no = row.get('RegNo', '').strip()
                    place = row.get('Place', '').strip()
                    state = row.get('State', '').strip()
                    if not reg_no or not place:
                        continue
                    # Clean city: take text before first comma or parenthesis
                    city = re.split(r'[,(]', place)[0].strip().upper()
                    # Handle combined entries like "AP16 & AP17"
                    codes = [c.strip() for c in reg_no.split('&')]
                    for code in codes:
                        code = code.strip()
                        if code:
                            rto_map[code] = city
                            # Build reverse map: (state, city) → first RTO code
                            state_upper = state.upper().strip()
                            if state_upper and city and (state_upper, city) not in rto_reverse_map:
                                rto_reverse_map[(state_upper, city)] = code
        except Exception:
            pass
        return rto_map, rto_reverse_map

    def _find_rto_code_from_location(self, rto_location: str) -> str:
        """Find RTO code from rto_location string like 'MADHYA PRADESH - Ujjain'"""
        if not rto_location or not self.rto_reverse_map:
            return ""
        # Parse "STATE - City" format
        parts = rto_location.split('-', 1)
        if len(parts) == 2:
            state = parts[0].strip().upper()
            city = parts[1].strip().upper()
        else:
            # Try as just city name
            state = ""
            city = rto_location.strip().upper()

        # Direct lookup with state and city
        if state and (state, city) in self.rto_reverse_map:
            return self.rto_reverse_map[(state, city)]

        # Fuzzy: try matching city across all states
        for (s, c), code in self.rto_reverse_map.items():
            if c == city:
                if not state or state in s or s in state:
                    return code

        # Partial match: check if city is contained in the key or vice versa
        for (s, c), code in self.rto_reverse_map.items():
            if state and state not in s and s not in state:
                continue
            if city in c or c in city:
                return code

        return ""

    def _get_rto_code(self, reg_num: str) -> str:
        """Extract RTO code from registration number (e.g., MH12AB1234 → MH12)"""
        import re
        match = re.match(r'^([A-Z]{2}\d{1,2})', reg_num.upper())
        return match.group(1) if match else ""

    def _resolve_vehicle_subclass(self, extracted_text: str, vehicle_class: str):
        """Backwards-compatible shim around :class:`MasterDataResolver`.

        Delegates to the DB-backed resolver, which uses the same 4-step ladder
        plus a Private-Car fallback. Returns ``(PBVehicleSubClassId, canonical_name)``
        or ``(None, None)``.
        """
        product = self._resolver.resolve_product_code(vehicle_class)
        if not product:
            return None, None
        resolution = self._resolver.resolve_vehicle_subclass(extracted_text, product)
        if not resolution.matched or resolution.id is None:
            return None, None
        return resolution.id, resolution.canonical

    def _strip_markdown_code_blocks(self, text: str) -> str:
        """Remove markdown code blocks from response text"""
        import re
        text = text.strip()

        # Remove markdown code blocks (```json ... ``` or ``` ... ```)
        # Pattern 1: ```json\n{...}\n```
        if text.startswith("```"):
            # Remove opening ```json or ```
            text = re.sub(r'^```(?:json)?\s*\n', '', text)
            # Remove closing ```
            text = re.sub(r'\n```\s*$', '', text)

        return text.strip()

    def _fix_json_escapes(self, text: str) -> str:
        """Fix invalid escape sequences and control characters in JSON string"""
        # Strategy: Process character by character, tracking if we're inside a string
        result = []
        i = 0
        in_string = False

        while i < len(text):
            char = text[i]

            # Track string boundaries (but not escaped quotes)
            if char == '"' and (i == 0 or text[i-1] != '\\'):
                in_string = not in_string
                result.append(char)
                i += 1
                continue

            # If we're inside a string and find a backslash
            if in_string and char == '\\':
                if i + 1 < len(text):
                    next_char = text[i + 1]
                    # Valid JSON escape characters: " \ / b f n r t u
                    if next_char in '"\\/bfnrtu':
                        # Valid escape - keep as is
                        result.append(char)
                        result.append(next_char)
                        i += 2
                    else:
                        # Invalid escape - double the backslash to escape it
                        result.append('\\\\')
                        result.append(next_char)
                        i += 2
                else:
                    # Backslash at end of string - escape it
                    result.append('\\\\')
                    i += 1
            # Handle control characters inside strings
            elif in_string and ord(char) < 32:
                if char == '\n':
                    result.append('\\n')
                elif char == '\r':
                    result.append('\\r')
                elif char == '\t':
                    result.append('\\t')
                else:
                    result.append(f'\\u{ord(char):04x}')
                i += 1
            else:
                # Regular character or outside string
                result.append(char)
                i += 1

        return ''.join(result)

    def _repair_truncated_json(self, text: str) -> str:
        """Attempt to repair truncated JSON by closing unclosed brackets/braces"""
        import re

        # Strip trailing whitespace first
        text = text.rstrip()

        # Remove incomplete trailing entries more aggressively
        # Pattern: remove last incomplete object in an array (common truncation point)
        # This handles: [..., {"key": "value", "key2": ] or [..., {"key": "val

        # Step 1: Find and remove incomplete trailing strings (unclosed quotes)
        # Count quotes to check if we have an unclosed string
        quote_count = text.count('"') - text.count('\\"')
        if quote_count % 2 == 1:
            # Unclosed string - find and remove the incomplete part
            last_quote = text.rfind('"')
            if last_quote > 0:
                # Check if this quote starts a value that was never closed
                before_quote = text[:last_quote].rstrip()
                if before_quote.endswith(':'):
                    # Pattern: "key": "incomplete - remove the whole key-value
                    text = re.sub(r',?\s*"[^"]*":\s*"[^"]*$', '', text)
                else:
                    # Just an unclosed string value - close it
                    text = text + '"'

        # Step 2: Remove incomplete key-value pairs at the end
        # Pattern: "key": or "key":  (colon with no value)
        text = re.sub(r',?\s*"[^"]*":\s*$', '', text.rstrip())

        # Step 3: Fix missing values after colons (e.g., "key": } or "key": ,)
        text = re.sub(r':\s*([,}\]])', r': ""\1', text)

        # Step 4: Remove trailing commas before closing brackets/braces
        text = re.sub(r',(\s*[}\]])', r'\1', text)

        # Step 5: Remove trailing comma and whitespace
        text = re.sub(r',\s*$', '', text.rstrip())

        # Step 6: Handle incomplete objects at the end of arrays
        # Find the last complete object/element and truncate there
        # Look for pattern: }, { incomplete... and remove the incomplete object
        last_complete = text.rfind('},')
        if last_complete > 0:
            # Check if there's an incomplete object after this
            after = text[last_complete + 2:].strip()
            if after.startswith('{'):
                # Count braces in the trailing part
                trailing_open = after.count('{')
                trailing_close = after.count('}')
                if trailing_open > trailing_close:
                    # Incomplete trailing object - remove it
                    text = text[:last_complete + 1]

        # Step 7: Remove any remaining incomplete structures at the end
        text = text.rstrip()
        while text and text[-1] in ',:{[':
            text = text[:-1].rstrip()

        # Step 8: Count and close unclosed brackets and braces
        open_braces = text.count('{') - text.count('}')
        open_brackets = text.count('[') - text.count(']')

        # Remove trailing comma before closing
        if text.endswith(','):
            text = text[:-1]

        # Close arrays first, then objects
        text += ']' * max(0, open_brackets)
        text += '}' * max(0, open_braces)

        return text

    def _get_extraction_prompt(self, markdown_content: str) -> str:
        """Stage 1 prompt: Extract ALL label-value pairs from markdown (no schema constraints)"""
        prompt_file = Path(__file__).parent / "stage1_extraction_prompt.md"
        with open(prompt_file, 'r', encoding='utf-8') as f:
            prompt_template = f.read()
        return prompt_template.replace("{MARKDOWN_CONTENT}", markdown_content)

    def _extract_raw_data(self, markdown_content: str) -> Dict[str, Any]:
        """Stage 1: Extract all label-value pairs from markdown"""
        print(f"🔍 Stage 1: Extracting all data from markdown...")
        start_time = time.time()

        prompt = self._get_extraction_prompt(markdown_content)

        MAX_RETRIES = 3
        fallback_data = None

        for attempt in range(MAX_RETRIES):
            if attempt > 0:
                print(f"   🔄 Retry {attempt}/{MAX_RETRIES - 1} (previous response was truncated)...")

            try:
                response = self._client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                )

                # Track tokens
                if hasattr(response, 'usage_metadata') and response.usage_metadata:
                    self.total_input_tokens += response.usage_metadata.prompt_token_count or 0
                    self.total_output_tokens += response.usage_metadata.candidates_token_count or 0

                response_text = response.text.strip()
                response_text = self._strip_markdown_code_blocks(response_text)

                # Strategy 1: Parse as-is (clean — return immediately, no retry needed)
                try:
                    raw_data = json.loads(response_text)
                    print(f"   ✓ Extracted raw data in {time.time() - start_time:.1f}s")
                    return raw_data
                except json.JSONDecodeError:
                    pass

                # Strategy 2: Fix escape sequences (clean — return immediately)
                try:
                    fixed_text = self._fix_json_escapes(response_text)
                    raw_data = json.loads(fixed_text)
                    print(f"   ✓ Extracted raw data in {time.time() - start_time:.1f}s")
                    return raw_data
                except json.JSONDecodeError:
                    pass

                # Strategies 3-6 require repair → store as fallback and retry
                repaired_data = None
                last_error = None

                # Strategy 3: Repair truncated JSON
                try:
                    repaired_text = self._repair_truncated_json(response_text)
                    repaired_data = json.loads(repaired_text)
                    print(f"   ⚠ JSON was truncated, repaired successfully")
                except json.JSONDecodeError as e:
                    last_error = e

                # Strategy 4: Fix escapes + repair truncated
                if repaired_data is None:
                    try:
                        fixed_text = self._fix_json_escapes(response_text)
                        repaired_text = self._repair_truncated_json(fixed_text)
                        repaired_data = json.loads(repaired_text)
                        print(f"   ⚠ JSON was truncated and had escape issues, repaired successfully")
                    except json.JSONDecodeError as e:
                        last_error = e

                # Strategy 5: Truncate at error position and repair
                if repaired_data is None and last_error is not None:
                    try:
                        error_pos = getattr(last_error, 'pos', None)
                        if error_pos and error_pos > 100:
                            truncated = response_text[:error_pos]
                            last_complete_entity = truncated.rfind('},')
                            if last_complete_entity > 0:
                                truncated = truncated[:last_complete_entity + 1]
                            repaired_text = self._repair_truncated_json(truncated)
                            repaired_data = json.loads(repaired_text)
                            print(f"   ⚠ JSON truncated at error position, repaired successfully")
                    except json.JSONDecodeError as e:
                        last_error = e

                # Strategy 6: Find last complete entity by scanning backwards
                if repaired_data is None:
                    try:
                        import re
                        entities_match = re.search(r'"entities"\s*:\s*\[', response_text)
                        if entities_match:
                            start_pos = entities_match.end()
                            complete_entities = []
                            brace_count = 0
                            entity_start = None
                            in_string = False

                            for i, char in enumerate(response_text[start_pos:], start_pos):
                                if char == '"' and (i == 0 or response_text[i-1] != '\\'):
                                    in_string = not in_string

                                if not in_string:
                                    if char == '{':
                                        if brace_count == 0:
                                            entity_start = i
                                        brace_count += 1
                                    elif char == '}':
                                        brace_count -= 1
                                        if brace_count == 0 and entity_start is not None:
                                            complete_entities.append(response_text[entity_start:i+1])
                                            entity_start = None

                            if complete_entities:
                                rebuilt = '{"entities": [' + ','.join(complete_entities) + ']}'
                                repaired_data = json.loads(rebuilt)
                                print(f"   ⚠ Rebuilt JSON with {len(complete_entities)} complete entities")
                    except Exception:
                        pass

                if repaired_data is not None:
                    fallback_data = repaired_data
                    if attempt < MAX_RETRIES - 1:
                        print(f"   🔄 Retrying for a complete response...")
                        continue
                    else:
                        print(f"   ⚠ Using repaired data after {MAX_RETRIES} attempts")
                        print(f"   ✓ Extracted raw data in {time.time() - start_time:.1f}s")
                        return fallback_data

                # All strategies failed
                if last_error is not None and attempt == MAX_RETRIES - 1:
                    print(f"   ⚠ JSON parse error in Stage 1: {last_error}")
                    print(f"   Response (after stripping): {response_text[:300]}...")

            except Exception as e:
                print(f"   ❌ Stage 1 extraction error: {e}")
                if attempt == MAX_RETRIES - 1:
                    return fallback_data or {}

        return fallback_data or {}

    def _get_output_schema(self) -> dict:
        """JSON schema defining all extraction fields"""
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "policy_number": {"description": "Policy/Certificate number"},
                "insurer_id": {"description": "Insurance company full name"},
                "plan_type_id": {"description": "MUST be Comprehensive, Third Party, or SAOD", "enum": ["Comprehensive", "Third Party", "SAOD"]},
                "policy_term": {"description": "Policy term in years (default: 1)"},
                "business_type": {"description": "Business type: NEW, RENEWAL, ROLLOVER"},
                "product_id": {"description": "Product type - MUST be exactly CV, Car, or Two Wheeler", "enum": ["CV", "Car", "Two Wheeler"]},
                "cust_name": {"description": "Customer name"},
                "mobileNo": {"description": "Mobile number"},
                "email": {"description": "Email address"},
                "cust_address": {"description": "Full address"},
                "pinCode": {"description": "PIN code (6 digits)"},
                "reg_num": {"description": "Vehicle registration NO SPACES (e.g., JH13J3431)"},
                "search_text_make": {"description": "Manufacturer (HYUNDAI, TATA)"},
                "model_name": {"description": "Vehicle model name"},
                "variantName": {"description": "Vehicle variant/trim level"},
                "fuel_type": {"description": "PETROL, DIESEL, CNG, ELECTRIC"},
                "VehicleClass": {"description": "Vehicle class", "enum": ["GCV", "PCV", "MISC D", "BUS", "Private Car", "Two Wheeler"]},
                "VehicleSubClass": {
                    "description": (
                        "Vehicle sub-class — pick the CLOSEST canonical name based on VehicleClass:\n"
                        "Two Wheeler: Bike, Scooter, E-Bike\n"
                        "Private Car: Private Car, Taxi\n"
                        "CV (GCV/PCV/BUS/MISC D): Truck, Heavy Truck/Semi Trailer, Tipper, Dumper, Tractor, "
                        "Tanker, Bulker, Trailer, Pick Up/Delivery Van, Refrigerated Van, Flat Bed Trailer, "
                        "Container, Gas Tanker, Car Carrier, Agricultural Tractor, Bulldozer, Crane, "
                        "Garbage Van, Ambulance, Excavator, Fork Lift Trucks, Drilling Rigs, Fire Vehicles, "
                        "Compressors, Road Rollers, Motor Grader, Transit Mixer, Generator Van, Backhoe Loader, "
                        "Loader, Harvester, Tractor Loader, Kisan Suvidha, Compactor, Vibrator, Towing Van, "
                        "Tar Sprayer, Jetting Machine, Road Sweeper, E Rickshaw, Auto Rickshaw, Taxi, "
                        "Passenger Bus, School Bus, Staff Bus\n"
                        "Return ONLY the canonical name from the list above — do not return raw document text."
                    )
                },
                "engineNo": {"description": "Engine number"},
                "chassisNo": {"description": "Chassis number"},
                "manufacturing_date": {"description": "Mfg date (dd/mm/yyyy)"},
                "registration_date": {"description": "Reg date (dd/mm/yyyy)"},
                "cubic_capacity_value": {"description": "CC/Engine capacity (numeric only)", "default": "0"},
                "vehicle_weight_value": {"description": "GVW weight (numeric)", "default": "0"},
                "no_of_seats": {"description": "Number of seats (numeric only)"},
                "no_of_wheels": {"description": "Number of wheels (numeric only)"},
                "vechicle_carrier_id": {"description": "Vehicle carrier type", "enum": ["PUBLIC", "PRIVATE", "COMMERCIAL"]},
                "rto_num": {"description": "RTO code (MH12)"},
                "rto_city": {"description": "RTO city"},
                "rto_state": {"description": "RTO state"},
                "premium": {"description": "Total premium (numeric only)", "default": "0"},
                "net_premium": {"description": "Net premium BEFORE GST (numeric only)", "default": "0"},
                "od_premium": {"description": "OD premium (numeric only)", "default": "0"},
                "tp_premium": {"description": "TP premium (numeric only)", "default": "0"},
                "basic_od_premium": {"description": "Basic OD (numeric)", "default": "0"},
                "basic_tp_premium": {"description": "Basic TP (numeric)", "default": "0"},
                "sum_insured": {"description": "IDV (numeric only)", "default": "0"},
                "ncb_percent": {"description": "NCB % (0,20,25,35,45,50)", "default": "0"},
                "cgst": {"description": "CGST amount (numeric only)", "default": "0"},
                "sgst": {"description": "SGST/UTGST amount (numeric only)", "default": "0"},
                "igst": {"description": "IGST amount (numeric only)", "default": "0"},
                "serviceTax": {"description": "Total GST = CGST + SGST + IGST (numeric only)", "default": "0"},
                "finalTotalDiscount": {"description": "NCB Discount amount in rupees (monetary deduction for claim-free years, NOT NCB protection addon)", "default": "0"},
                "policy_start_date": {"description": "Start date (dd/mm/yyyy)"},
                "policy_end_date": {"description": "End date (dd/mm/yyyy)"},
                "issuance_date": {"description": "Issuance date (dd/mm/yyyy)"},
                "tpStartDate": {"description": "TP start (dd/mm/yyyy)"},
                "tpEndDate": {"description": "TP end (dd/mm/yyyy)"},
                "od_startdate": {"description": "OD start (dd/mm/yyyy)"},
                "od_enddate": {"description": "OD end (dd/mm/yyyy)"},
                "pay_term": {"description": "Payment term (default: 1)"},
                "od_term": {"description": "OD term years (default: 1)"},
                "tp_term": {"description": "TP term years (default: 1)"},
                "previous_policy_number": {"description": "Previous policy no"},
                "previous_policy_end_date": {"description": "Previous policy end/expiry date (dd/mm/yyyy) — used to derive STP/NSTP"},
                "pa_cover": {"description": "PA cover (numeric)", "default": "0"},
                "pa_cover_addon": {"description": "PA addon: YES/NO", "default": "NO"},
                "paUnnamedPassengerPremium": {"description": "PA Unnamed Passenger addon premium in rupees — the SMALL premium paid, NOT the cover sum (numeric)", "default": "0"},
                "electricalAccessoriesIDV": {"description": "Elec IDV (numeric)", "default": "0"},
                "nonElectricalAccessoriesIDV": {"description": "Non-elec IDV (numeric)", "default": "0"},
                "cngLPGUnitIDV": {"description": "CNG IDV (numeric)", "default": "0"},
                "isCNGFitted": {"description": "CNG: TRUE/FALSE", "default": "FALSE"},
                "valueOfElectricalAccessories": {"description": "Elec value (numeric)", "default": "0"},
                "valueOfNonElectricalAccessories": {"description": "Non-elec value (numeric)", "default": "0"},
                "cngAmount": {"description": "CNG value (numeric)", "default": "0"},
                "bifuelKitTPPremium": {"description": "Bi-fuel (CNG/LPG) kit TP (third-party) premium in rupees. Distinct from cngAmount, which is the kit OD-side premium. Only present when the policy prints a SEPARATE TP line for the kit (numeric)", "default": "0"},
                "isZeroDepCover": {"description": "Zero dep cover", "enum": ["YES", "NO"], "default": "NO"},
                "zeroDepPremium": {"description": "Zero dep premium (numeric)", "default": "0"},
                "isEPCover": {"description": "Engine protection cover", "enum": ["YES", "NO"], "default": "NO"},
                "ePPremium": {"description": "EP premium (numeric)", "default": "0"},
                "engineProtectionPremium": {"description": "EP premium alt (numeric)", "default":"0"},
                "isConsumablesCover": {"description": "Consumables cover", "enum": ["YES", "NO"], "default": "NO"},
                "consumablesPremium": {"description": "Consumables premium (numeric)", "default": "0"},
                "isKeyReplacementCover": {"description": "Key replacement cover", "enum": ["YES", "NO"], "default": "NO"},
                "keyReplacement": {"description": "Key premium (numeric)", "default": "0"},
                "isNCBCover": {"description": "NCB Protection addon cover (NOT NCB discount)", "enum": ["YES", "NO"], "default": "NO"},
                "nCBProtection": {"description": "NCB Protection addon premium in rupees (NOT the NCB discount amount)", "default": "0"},
                "isRSACover": {"description": "Roadside assistance cover", "enum": ["YES", "NO"], "default": "NO"},
                "rSAPremium": {"description": "RSA premium (numeric)", "default": "0"},
                "isRTICover": {"description": "Return to invoice cover", "enum": ["YES", "NO"], "default": "NO"},
                "rtiPremium": {"description": "RTI premium (numeric)", "default": "0"},
                "isLPBCover": {"description": "Loss of Personal Belongings cover", "enum": ["YES", "NO"], "default": "NO"},
                "lPBPremium": {"description": "Loss of Personal Belongings premium (numeric)", "default": "0"},
                "isTyreProtectionCover": {"description": "Tyre protection cover", "enum": ["YES", "NO"], "default": "NO"},
                "tyreProtectionPremium": {"description": "Tyre premium (numeric)", "default": "0"},
                "isInsurerPaidDriver": {"description": "Paid driver cover", "enum": ["YES", "NO"], "default": "NO"},
                "paidDriverPremium": {"description": "Driver premium (numeric)"},
                "addOn": {"description": "Has add-ons: TRUE/FALSE", "default": "FALSE"},
                "addonList": {"description": "Add-ons list", "default": "[]"},
                "totalAddonPremium": {"description": "COMPUTED in post-processing (LLM must leave 0). Sum of 9 OD-side optional add-ons: zeroDep + RTI + EP + tyre + NCBProtection + consumables + LPB + RSA + keyReplacement. DELIBERATELY EXCLUDES statutory PA, CNG/bifuel kit premiums, and the paid-driver TP add-on (numeric)", "default": "0"},
                "branch": {"description": "Insurance branch"},
                "intermediary_code": {"description": "Broker/Intermediary code"},
                "intermediary_name": {"description": "Broker name"},
                "IMT23_value": {"description": "IMT23 (numeric)", "default": "0"},
                "IMT34_value": {"description": "IMT34 (numeric)", "default": "0"},
                "IMT34": {"description": "IMT34", "enum": ["YES", "NO"], "default": "NO"},
                "imtIncluded": {"description": "IMT: TRUE/FALSE", "default": "FALSE"},
                "stp_nstp": {"description": "STP/NSTP status — DERIVED in post-processing from previous_policy_end_date vs policy_start_date. LLM should leave this empty.", "enum": ["STP", "NSTP", ""]},
                "financialInstitutionName": {"description": "Financier name"},
                "nomineeName": {"description": "Nominee name"},
                "nomineeRelationWithCustomer": {"description": "Nominee relation"},
                "AgentUserName": {"description": "Agent name"}
            }
        }

    def _convert_pdf_to_markdown(self, pdf_path: str) -> str:
        """Convert PDF to Markdown using Minerva API"""
        print(f"📄 Converting PDF to Markdown using Minerva API...")
        start_time = time.time()

        # Prepare the multipart form data
        with open(pdf_path, 'rb') as pdf_file:
            files = {
                'files': (Path(pdf_path).name, pdf_file, 'application/pdf')
            }
            data = {
                'return_middle_json': 'false',
                'return_model_output': 'false',
                'return_md': 'true',
                'return_images': 'false',
                'end_page_id': '99999',
                'parse_method': 'auto',
                'start_page_id': '0',
                'lang_list': 'en',
                'output_dir': './output',
                'server_url': 'string',
                'return_content_list': 'false',
                'backend': 'hybrid-auto-engine',
                'table_enable': 'true',
                'response_format_zip': 'false',
                'formula_enable': 'true',
                'force_enable_ocr': 'true'
            }

            try:
                response = requests.post(
                    self.MINERVA_API_URL,
                    files=files,
                    data=data,
                    headers={'accept': 'application/json'},
                    timeout=300  # 5 minute timeout for large PDFs
                )
                response.raise_for_status()

                result = response.json()

                # Extract markdown content from the response
                # API returns: {"results": {filename: {"md_content": "..."}}}
                markdown_content = ""

                if isinstance(result, dict):
                    # Primary format: results -> filename -> md_content
                    results = result.get("results", {})
                    if results:
                        for filename, data in results.items():
                            if isinstance(data, dict) and "md_content" in data:
                                markdown_content = data["md_content"]
                                break

                    # Fallback to other possible response formats
                    if not markdown_content:
                        if 'md_content' in result:
                            markdown_content = result['md_content']
                        elif 'md' in result:
                            markdown_content = result['md']
                        elif 'markdown' in result:
                            markdown_content = result['markdown']
                        elif 'content' in result:
                            markdown_content = result['content']

                elif isinstance(result, str):
                    markdown_content = result

                print(f"   ✓ Converted in {time.time() - start_time:.1f}s ({len(markdown_content)} chars)")
                return markdown_content

            except requests.exceptions.RequestException as e:
                print(f"   ❌ API request error: {e}")
                raise
            except json.JSONDecodeError as e:
                # If response is not JSON, it might be raw markdown
                print(f"   ⚠ Response is not JSON, treating as raw markdown")
                markdown_content = response.text
                print(f"   ✓ Converted in {time.time() - start_time:.1f}s ({len(markdown_content)} chars)")
                return markdown_content

    def _get_mapping_prompt(self, raw_data: Dict[str, Any]) -> str:
        """Stage 2 prompt: Map extracted data to target schema"""
        schema = self._get_output_schema()
        schema_json = json.dumps(schema, indent=2)
        raw_data_json = json.dumps(raw_data, indent=2)

        prompt_file = Path(__file__).parent / "stage2_mapping_prompt.md"
        with open(prompt_file, 'r', encoding='utf-8') as f:
            prompt_template = f.read()

        return prompt_template.replace("{RAW_DATA_JSON}", raw_data_json).replace("{SCHEMA_JSON}", schema_json)

    def _map_to_schema(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Stage 2: Map extracted data to target schema"""
        print(f"🔗 Stage 2: Mapping data to target schema...")
        start_time = time.time()

        prompt = self._get_mapping_prompt(raw_data)

        MAX_RETRIES = 3
        fallback_data = None

        for attempt in range(MAX_RETRIES):
            if attempt > 0:
                print(f"   🔄 Retry {attempt}/{MAX_RETRIES - 1} (previous response was truncated)...")

            try:
                response = self._client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                )

                # Track tokens
                if hasattr(response, 'usage_metadata') and response.usage_metadata:
                    self.total_input_tokens += response.usage_metadata.prompt_token_count or 0
                    self.total_output_tokens += response.usage_metadata.candidates_token_count or 0

                response_text = response.text.strip()
                response_text = self._strip_markdown_code_blocks(response_text)

                # Strategy 1: Parse as-is (clean — return immediately)
                try:
                    structured_data = json.loads(response_text)
                    print(f"   ✓ Mapped {len(structured_data)} fields in {time.time() - start_time:.1f}s")
                    return structured_data
                except json.JSONDecodeError:
                    pass

                # Strategy 2: Fix escape sequences (clean — return immediately)
                try:
                    fixed_text = self._fix_json_escapes(response_text)
                    structured_data = json.loads(fixed_text)
                    print(f"   ✓ Mapped {len(structured_data)} fields in {time.time() - start_time:.1f}s")
                    return structured_data
                except json.JSONDecodeError:
                    pass

                # Strategies 3-4 require repair → store as fallback and retry
                repaired_data = None
                last_error = None

                # Strategy 3: Repair truncated JSON
                try:
                    repaired_text = self._repair_truncated_json(response_text)
                    repaired_data = json.loads(repaired_text)
                    print(f"   ⚠ JSON was truncated, repaired successfully")
                except json.JSONDecodeError as e:
                    last_error = e

                # Strategy 4: Fix escapes + repair truncated
                if repaired_data is None:
                    try:
                        fixed_text = self._fix_json_escapes(response_text)
                        repaired_text = self._repair_truncated_json(fixed_text)
                        repaired_data = json.loads(repaired_text)
                        print(f"   ⚠ JSON was truncated and had escape issues, repaired successfully")
                    except json.JSONDecodeError as e:
                        last_error = e

                if repaired_data is not None:
                    fallback_data = repaired_data
                    if attempt < MAX_RETRIES - 1:
                        print(f"   🔄 Retrying for a complete response...")
                        continue
                    else:
                        print(f"   ⚠ Using repaired data after {MAX_RETRIES} attempts")
                        print(f"   ✓ Mapped {len(fallback_data)} fields in {time.time() - start_time:.1f}s")
                        return fallback_data

                if last_error is not None and attempt == MAX_RETRIES - 1:
                    print(f"   ⚠ JSON parse error in Stage 2: {last_error}")
                    print(f"   Response (after stripping): {response_text[:300]}...")

            except Exception as e:
                print(f"   ❌ Stage 2 mapping error: {e}")
                if attempt == MAX_RETRIES - 1:
                    return fallback_data or {}

        return fallback_data or {}

    # Indian state code to state name mapping (from reg_num prefix)
    RTO_STATE_MAP = {
        "AP": "ANDHRA PRADESH", "AR": "ARUNACHAL PRADESH", "AS": "ASSAM",
        "BR": "BIHAR", "CG": "CHHATTISGARH", "GA": "GOA",
        "GJ": "GUJARAT", "HR": "HARYANA", "HP": "HIMACHAL PRADESH",
        "JH": "JHARKHAND", "KA": "KARNATAKA", "KL": "KERALA",
        "MP": "MADHYA PRADESH", "MH": "MAHARASHTRA", "MN": "MANIPUR",
        "ML": "MEGHALAYA", "MZ": "MIZORAM", "NL": "NAGALAND",
        "OD": "ODISHA", "OR": "ODISHA", "PB": "PUNJAB",
        "RJ": "RAJASTHAN", "SK": "SIKKIM", "TN": "TAMIL NADU",
        "TS": "TELANGANA", "TR": "TRIPURA", "UP": "UTTAR PRADESH",
        "UK": "UTTARAKHAND", "UA": "UTTARAKHAND", "WB": "WEST BENGAL",
        "AN": "ANDAMAN AND NICOBAR", "CH": "CHANDIGARH",
        "DN": "DADRA AND NAGAR HAVELI", "DD": "DAMAN AND DIU",
        "DL": "DELHI", "JK": "JAMMU AND KASHMIR", "LA": "LADAKH",
        "LD": "LAKSHADWEEP", "PY": "PUDUCHERRY",
    }

    # Amount fields that should be rounded to whole numbers
    AMOUNT_FIELDS = {
        'premium', 'net_premium', 'od_premium', 'tp_premium',
        'basic_od_premium', 'basic_tp_premium', 'sum_insured',
        'serviceTax', 'cgst', 'sgst', 'igst', 'finalTotalDiscount', 'pa_cover',
        'electricalAccessoriesIDV', 'nonElectricalAccessoriesIDV',
        'cngLPGUnitIDV', 'valueOfElectricalAccessories',
        'valueOfNonElectricalAccessories', 'cngAmount',
        'zeroDepPremium', 'ePPremium', 'engineProtectionPremium',
        'consumablesPremium', 'keyReplacement', 'nCBProtection',
        'rSAPremium', 'rtiPremium', 'lPBPremium', 'tyreProtectionPremium',
        'paidDriverPremium', 'IMT23_value', 'IMT34_value',
        'paUnnamedPassengerPremium', 'bifuelKitTPPremium',
    }

    # Sum drivers for the computed totalAddonPremium. ePPremium only —
    # engineProtectionPremium is a duplicate alias (coalesced in _post_process)
    # to avoid under/double-count. DELIBERATELY excludes PA, CNG/bifuel, and the
    # paid-driver TP add-on by product definition. Keep in sync with default_zero_fields.
    ADDON_TOTAL_FIELDS = [
        'zeroDepPremium', 'rtiPremium', 'ePPremium', 'tyreProtectionPremium',
        'nCBProtection', 'consumablesPremium', 'lPBPremium', 'rSAPremium',
        'keyReplacement',
    ]

    def _clean_whitespace(self, value: str, field_name: str) -> str:
        """
        Clean whitespace from string values based on field type

        - Identifier/code fields: Remove ALL whitespace
        - Numeric string fields: Remove ALL whitespace
        - Text fields: Normalize whitespace (trim + collapse multiple spaces)
        """
        if not isinstance(value, str) or not value:
            return value

        import re

        # Fields that should have ALL whitespace removed (identifiers, codes, numbers)
        no_space_fields = {
            # Policy identifiers
            'policy_number', 'previous_policy_number', 'proposal_number',
            # Vehicle identifiers
            'reg_num', 'engineNo', 'chassisNo',
            # Location codes
            'rto_num', 'pinCode',
            # Contact info
            'mobileNo', 'email',
            # Business codes
            'intermediary_code', 'agent_code',
            # All numeric fields (even if stored as strings)
            'premium', 'net_premium', 'od_premium', 'tp_premium',
            'basic_od_premium', 'basic_tp_premium', 'sum_insured',
            'cgst', 'sgst', 'igst', 'serviceTax', 'finalTotalDiscount',
            'pa_cover', 'ncb_percent',
            'electricalAccessoriesIDV', 'nonElectricalAccessoriesIDV',
            'cngLPGUnitIDV', 'valueOfElectricalAccessories',
            'valueOfNonElectricalAccessories', 'cngAmount',
            'cubic_capacity_value', 'vehicle_weight_value',
            'no_of_seats', 'no_of_wheels',
            'zeroDepPremium', 'ePPremium', 'engineProtectionPremium',
            'consumablesPremium', 'keyReplacement', 'nCBProtection',
            'rSAPremium', 'rtiPremium', 'lPBPremium',
            'tyreProtectionPremium', 'paidDriverPremium',
            'paUnnamedPassengerPremium', 'bifuelKitTPPremium',
            'IMT23_value', 'IMT34_value', 'policy_term', 'pay_term',
            'od_term', 'tp_term'
        }

        if field_name in no_space_fields:
            # Remove ALL whitespace characters (spaces, newlines, tabs, etc.)
            return ''.join(value.split())

        # For all other text fields (names, addresses, etc.): normalize whitespace
        # - Strip leading/trailing whitespace
        # - Replace multiple spaces/newlines/tabs with single space
        normalized = re.sub(r'\s+', ' ', value.strip())
        return normalized

    def _apply_total_addon_premium(self, structured_data: Dict[str, Any]) -> None:
        """Compute totalAddonPremium = sum of the 9 OD-side add-on premiums.

        Pure dict->dict (mutates structured_data in place); no DB/SDK needed, so it is
        unit-testable in isolation. Call AFTER the rounding + default-zero loops so every
        component is already a clean numeric string. ePPremium and engineProtectionPremium
        are duplicate aliases for the same add-on — coalesce (max) so the EP value is
        counted once whether the LLM filled the primary or the alt field. Marks the field
        is_extracted="TRUE" (matches the serviceTax/net_premium derived-field convention).
        """
        def _addon_num(field_name: str) -> float:
            data = structured_data.get(field_name)
            if not isinstance(data, dict):
                return 0.0
            raw = data.get('value', '')
            clean = ''.join(c for c in str(raw) if c.isdigit() or c in '.-')
            try:
                return abs(float(clean)) if clean else 0.0
            except (ValueError, TypeError):
                return 0.0

        addon_total = 0.0
        for field in self.ADDON_TOTAL_FIELDS:
            if field == "ePPremium":
                addon_total += max(_addon_num("ePPremium"),
                                   _addon_num("engineProtectionPremium"))
            else:
                addon_total += _addon_num(field)

        total_value = str(round(addon_total))
        if isinstance(structured_data.get("totalAddonPremium"), dict):
            structured_data["totalAddonPremium"]["value"] = total_value
            structured_data["totalAddonPremium"]["is_extracted"] = "TRUE"
            structured_data["totalAddonPremium"]["confidence_score"] = 0.95
        else:
            structured_data["totalAddonPremium"] = {
                "id": "", "value": total_value, "confidence_score": 0.95,
                "is_extracted": "TRUE", "do_verification": "FALSE"
            }

    def _post_process(self, structured_data: Dict[str, Any], raw_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Post-process extracted data - round amounts, derive fields, clean whitespace"""

        # Clean whitespace from all string values
        for field_name, field_data in structured_data.items():
            if isinstance(field_data, dict) and 'value' in field_data:
                value = field_data.get('value', '')
                if isinstance(value, str):
                    field_data['value'] = self._clean_whitespace(value, field_name)

        # Round amount fields and convert to absolute values
        for field_name, field_data in structured_data.items():
            if field_name in self.AMOUNT_FIELDS and isinstance(field_data, dict):
                value = field_data.get('value', '')
                if value:
                    try:
                        clean_value = ''.join(c for c in str(value) if c.isdigit() or c in '.-')
                        if clean_value:
                            numeric_value = float(clean_value)
                            # Convert to absolute value (remove negative sign)
                            absolute_value = abs(numeric_value)
                            rounded_value = str(round(absolute_value))
                            field_data['value'] = rounded_value
                    except (ValueError, TypeError):
                        pass

        # Default zero fields
        default_zero_fields = [
            "zeroDepPremium", "ePPremium", "consumablesPremium", "keyReplacement",
            "nCBProtection", "rSAPremium", "rtiPremium", "lPBPremium",
            "tyreProtectionPremium", "finalTotalDiscount",
            "paUnnamedPassengerPremium", "bifuelKitTPPremium",
        ]

        for field in default_zero_fields:
            if field in structured_data and isinstance(structured_data[field], dict):
                val = structured_data[field].get('value')
                if not val or (isinstance(val, str) and not val.strip()):
                    structured_data[field]['value'] = "0"
            elif field not in structured_data:
                structured_data[field] = {
                    "id": "", "value": "0", "confidence_score": 1.0,
                    "is_extracted": "FALSE", "do_verification": "FALSE"
                }

        # Compute totalAddonPremium from the 9 OD-side add-on premiums (after the
        # rounding + default-zero loops above guarantee clean numeric strings).
        self._apply_total_addon_premium(structured_data)

        # Fallback: basic_od_premium ↔ od_premium when one is missing
        basic_od = (structured_data.get('basic_od_premium', {}).get('value', '') or '').strip()
        od_total = (structured_data.get('od_premium', {}).get('value', '') or '').strip()
        if not basic_od and od_total:
            # No basic OD found but OD total exists → use OD total as basic
            structured_data['basic_od_premium']['value'] = od_total
            structured_data['basic_od_premium']['is_extracted'] = "TRUE"
        elif basic_od and not od_total:
            # No OD total found but basic OD exists → use basic as OD total
            if 'od_premium' in structured_data and isinstance(structured_data['od_premium'], dict):
                structured_data['od_premium']['value'] = basic_od
                structured_data['od_premium']['is_extracted'] = "TRUE"

        # Fallback: basic_tp_premium ↔ tp_premium when one is missing
        basic_tp = (structured_data.get('basic_tp_premium', {}).get('value', '') or '').strip()
        tp_total = (structured_data.get('tp_premium', {}).get('value', '') or '').strip()
        if not basic_tp and tp_total:
            if 'basic_tp_premium' in structured_data and isinstance(structured_data['basic_tp_premium'], dict):
                structured_data['basic_tp_premium']['value'] = tp_total
                structured_data['basic_tp_premium']['is_extracted'] = "TRUE"
        elif basic_tp and not tp_total:
            if 'tp_premium' in structured_data and isinstance(structured_data['tp_premium'], dict):
                structured_data['tp_premium']['value'] = basic_tp
                structured_data['tp_premium']['is_extracted'] = "TRUE"

        # Clean reg_num
        if 'reg_num' in structured_data and isinstance(structured_data['reg_num'], dict):
            reg_value = structured_data['reg_num'].get('value', '')
            if reg_value:
                clean_reg = ''.join(c for c in reg_value if c.isalnum()).upper()
                structured_data['reg_num']['value'] = clean_reg

        # Derive serviceTax from raw_data taxes array
        # serviceTax = only the 18% GST ("others" category) — NOT the 5% Basic TP GST
        # For split-rate policies (CV/TP-only): CGST-Others(9%) + SGST-Others(9%) = serviceTax
        # Basic TP tax at 2.5%+2.5% is excluded — it's a govt-mandated rate, not "service tax"
        if 'serviceTax' in structured_data and isinstance(structured_data['serviceTax'], dict):
            service_tax_value = structured_data['serviceTax'].get('value', '')
            if not service_tax_value or service_tax_value.strip() == "":
                others_total = 0.0
                has_split_rates = False

                if raw_data:
                    for tax_entry in raw_data.get('taxes', []):
                        gst_category = tax_entry.get('gst_category', '')
                        ctx = str(tax_entry.get('context', '')).upper()
                        val = tax_entry.get('value', 0) or 0

                        # Check if this is a split-rate policy by looking for "basic_tp" category
                        if gst_category == 'basic_tp' or 'BASIC TP' in ctx or '2.5%' in ctx or '2.50%' in ctx:
                            has_split_rates = True

                        # Sum only the "others" (18%) components
                        if gst_category == 'others' or gst_category == 'igst':
                            try:
                                others_total += float(val)
                            except (ValueError, TypeError):
                                pass
                        elif not gst_category and 'BASIC TP' not in ctx and '2.5%' not in ctx and '2.50%' not in ctx:
                            # No category tag — include if not Basic TP
                            try:
                                others_total += float(val)
                            except (ValueError, TypeError):
                                pass

                if has_split_rates and others_total > 0:
                    # Split-rate policy: use only "others" tax
                    calculated_tax = str(round(others_total))
                    structured_data['serviceTax']['value'] = calculated_tax
                    structured_data['serviceTax']['is_extracted'] = "TRUE"
                    structured_data['serviceTax']['confidence_score'] = 0.90
                else:
                    # Normal policy: fall back to cgst + sgst + igst from structured_data
                    cgst_value = structured_data.get('cgst', {}).get('value', '')
                    sgst_value = structured_data.get('sgst', {}).get('value', '')
                    igst_value = structured_data.get('igst', {}).get('value', '')
                    if cgst_value or sgst_value or igst_value:
                        try:
                            cgst_num = float(''.join(c for c in str(cgst_value) if c.isdigit() or c in '.-') or '0')
                            sgst_num = float(''.join(c for c in str(sgst_value) if c.isdigit() or c in '.-') or '0')
                            igst_num = float(''.join(c for c in str(igst_value) if c.isdigit() or c in '.-') or '0')
                            calculated_tax = str(round(cgst_num + sgst_num + igst_num))
                            structured_data['serviceTax']['value'] = calculated_tax
                            structured_data['serviceTax']['is_extracted'] = "TRUE"
                            structured_data['serviceTax']['confidence_score'] = 0.90
                        except (ValueError, TypeError):
                            pass

        # Derive net_premium from od + tp
        if 'net_premium' in structured_data and isinstance(structured_data['net_premium'], dict):
            net_value = structured_data['net_premium'].get('value', '')
            if not net_value or net_value.strip() == "":
                od_value = structured_data.get('od_premium', {}).get('value', '')
                tp_value = structured_data.get('tp_premium', {}).get('value', '')
                if od_value or tp_value:
                    try:
                        od_num = float(''.join(c for c in str(od_value) if c.isdigit() or c in '.-') or '0')
                        tp_num = float(''.join(c for c in str(tp_value) if c.isdigit() or c in '.-') or '0')
                        calculated_net = str(round(od_num + tp_num))
                        structured_data['net_premium']['value'] = calculated_net
                        structured_data['net_premium']['is_extracted'] = "TRUE"
                        structured_data['net_premium']['confidence_score'] = 0.85
                    except (ValueError, TypeError):
                        pass

        # Derive plan_type_id from od/tp premiums
        if 'plan_type_id' in structured_data and isinstance(structured_data['plan_type_id'], dict):
            od_value = structured_data.get('od_premium', {}).get('value', '')
            tp_value = structured_data.get('tp_premium', {}).get('value', '')
            try:
                od_num = float(''.join(c for c in str(od_value) if c.isdigit() or c in '.-') or '0')
                tp_num = float(''.join(c for c in str(tp_value) if c.isdigit() or c in '.-') or '0')

                if od_num > 0 and tp_num > 0:
                    derived_plan = "COMPREHENSIVE"
                elif od_num > 0 and tp_num == 0:
                    derived_plan = "SAOD"
                elif od_num == 0 and tp_num > 0:
                    derived_plan = "THIRD PARTY"
                else:
                    derived_plan = None

                if derived_plan:
                    structured_data['plan_type_id']['value'] = derived_plan
                    structured_data['plan_type_id']['is_extracted'] = "TRUE"
                    structured_data['plan_type_id']['confidence_score'] = 0.95
            except (ValueError, TypeError):
                pass

        # Derive no_of_wheels from product_id
        if 'no_of_wheels' in structured_data and isinstance(structured_data['no_of_wheels'], dict):
            wheels_value = structured_data['no_of_wheels'].get('value', '')
            if not wheels_value or wheels_value.strip() == "":
                product_id = structured_data.get('product_id', {}).get('value', '')
                if product_id:
                    if product_id.upper() == "TWO WHEELER":
                        structured_data['no_of_wheels']['value'] = "2"
                        structured_data['no_of_wheels']['is_extracted'] = "TRUE"
                    elif product_id.upper() in ["CAR", "CV"]:
                        structured_data['no_of_wheels']['value'] = "4"
                        structured_data['no_of_wheels']['is_extracted'] = "TRUE"

        # Derive vechicle_carrier_id
        if 'vechicle_carrier_id' in structured_data and isinstance(structured_data['vechicle_carrier_id'], dict):
            carrier_value = structured_data['vechicle_carrier_id'].get('value', '').upper()
            vehicle_class = structured_data.get('VehicleClass', {}).get('value', '').upper()
            product_id = structured_data.get('product_id', {}).get('value', '').upper()

            derived_carrier = None
            if carrier_value in ["PUBLIC", "PRIVATE", "COMMERCIAL"]:
                derived_carrier = carrier_value
            elif not derived_carrier:
                if vehicle_class in ["GCV"]:
                    derived_carrier = "COMMERCIAL"
                elif vehicle_class in ["PCV", "BUS"]:
                    derived_carrier = "PUBLIC"
                elif product_id == "CAR":
                    derived_carrier = "PRIVATE"
                elif product_id == "CV":
                    derived_carrier = "COMMERCIAL"

            if derived_carrier:
                structured_data['vechicle_carrier_id']['value'] = derived_carrier
                structured_data['vechicle_carrier_id']['is_extracted'] = "TRUE"

        # ────────────── Master-data ID resolution (DB-backed) ──────────────
        # Populate the `id` field on each enum-like extracted field by looking
        # up the canonical row in the master_data tables. Order matters:
        # product_id must be resolved before insurer_id (insurer rows are
        # scoped per product). When no match is found we leave `id=""` and
        # set `do_verification="TRUE"` so a human can review.
        def _apply(field_name: str, resolution) -> None:
            if field_name not in structured_data:
                return
            bucket = structured_data[field_name]
            if not isinstance(bucket, dict):
                return
            extracted = (bucket.get('value') or '').strip()
            if resolution.matched and resolution.id is not None:
                bucket['id'] = str(resolution.id)
                if resolution.canonical:
                    bucket['value'] = resolution.canonical
                bucket['is_extracted'] = "TRUE"
                bucket['do_verification'] = "FALSE"
            else:
                # No confident master match: never leave a raw/echoed value in
                # `id` (the LLM often copies the UPPERCASE value into it).
                bucket['id'] = ""
                if extracted:
                    bucket['do_verification'] = "TRUE"

        # 1. product_id — try the explicit product_id field first, then VehicleClass.
        product_text = (structured_data.get('product_id', {}).get('value') or '').strip()
        vehicle_class_text = (structured_data.get('VehicleClass', {}).get('value') or '').strip()
        prod_resolution = self._resolver.resolve_product_id(product_text or vehicle_class_text)
        _apply('product_id', prod_resolution)
        resolved_product_id = prod_resolution.id

        # 2. insurer_id — filtered by product_id; default to 188 (CV) when unresolved.
        insurer_text = (structured_data.get('insurer_id', {}).get('value') or '').strip()
        lookup_pid = resolved_product_id if resolved_product_id is not None else 188
        _apply('insurer_id', self._resolver.resolve_insurer(insurer_text, product_id=lookup_pid))
        if (
            resolved_product_id is None
            and 'insurer_id' in structured_data
            and isinstance(structured_data['insurer_id'], dict)
            and insurer_text
        ):
            structured_data['insurer_id']['do_verification'] = "TRUE"

        # 3. plan_type_id — key on (plan, isZeroDepCover == "YES").
        plan_text = (structured_data.get('plan_type_id', {}).get('value') or '').strip()
        zd_value = (structured_data.get('isZeroDepCover', {}).get('value') or '').strip().upper()
        _apply('plan_type_id', self._resolver.resolve_plan_type(plan_text, is_zero_dep=(zd_value == "YES")))

        # 4. Independent small enums.
        _apply('fuel_type', self._resolver.resolve_fuel_type(
            (structured_data.get('fuel_type', {}).get('value') or '').strip()
        ))
        _apply('vechicle_carrier_id', self._resolver.resolve_carrier(
            (structured_data.get('vechicle_carrier_id', {}).get('value') or '').strip()
        ))
        _apply('VehicleClass', self._resolver.resolve_vehicle_class(vehicle_class_text))

        # 5. VehicleSubClass — uses the DB-backed resolver via the existing shim.
        if 'VehicleSubClass' in structured_data and isinstance(structured_data['VehicleSubClass'], dict):
            extracted_sub = (structured_data['VehicleSubClass'].get('value') or '').strip()
            vehicle_class = (structured_data.get('VehicleClass', {}).get('value') or '').strip()
            if extracted_sub and vehicle_class:
                sub_id, canonical = self._resolve_vehicle_subclass(extracted_sub, vehicle_class)
                if sub_id is not None:
                    structured_data['VehicleSubClass']['id'] = str(sub_id)
                    structured_data['VehicleSubClass']['value'] = canonical
                    structured_data['VehicleSubClass']['is_extracted'] = "TRUE"
                    structured_data['VehicleSubClass']['do_verification'] = "FALSE"
                    print(f"   ✓ VehicleSubClass resolved: '{extracted_sub}' → [{sub_id}] {canonical}")
                else:
                    structured_data['VehicleSubClass']['id'] = ""
                    structured_data['VehicleSubClass']['do_verification'] = "TRUE"
                    print(f"   ⚠ VehicleSubClass no match: '{extracted_sub}' for class '{vehicle_class}'")

        # Strip hallucinated `id` values from non-master-data fields. The LLM
        # often echoes the field's own key into `id`; only the 7 master-data
        # fields above legitimately carry a resolved DB row id here.
        ID_BEARING_FIELDS = {
            "product_id", "insurer_id", "plan_type_id", "fuel_type",
            "vechicle_carrier_id", "VehicleClass", "VehicleSubClass",
        }
        for _fname, _bucket in structured_data.items():
            if _fname in ID_BEARING_FIELDS:
                continue
            if isinstance(_bucket, dict) and "id" in _bucket:
                _bucket["id"] = ""

        # Handle NEW vehicles: derive rto_num from rto_location in raw data
        reg_value = (structured_data.get('reg_num', {}).get('value', '') or '').strip().upper()
        is_new_vehicle = reg_value in ('NEW', '')

        if is_new_vehicle and raw_data:
            # Extract rto_location from raw data (vehicles array or locations array)
            rto_location = ""
            # Check vehicles array first
            vehicles = raw_data.get('vehicles', [])
            if vehicles and isinstance(vehicles, list):
                rto_location = vehicles[0].get('rto_location', '')
            # Fallback: check locations array for RTO Location context
            if not rto_location:
                for loc in raw_data.get('locations', []):
                    if isinstance(loc, dict) and 'RTO' in loc.get('context', '').upper():
                        rto_location = loc.get('value', '')
                        break

            if rto_location:
                rto_code = self._find_rto_code_from_location(rto_location)
                if rto_code:
                    # Set rto_num
                    if 'rto_num' in structured_data and isinstance(structured_data['rto_num'], dict):
                        structured_data['rto_num']['value'] = rto_code
                        structured_data['rto_num']['is_extracted'] = "TRUE"
                        structured_data['rto_num']['confidence_score'] = 0.90
                    # Set rto_city from the looked-up code
                    if 'rto_city' in structured_data and isinstance(structured_data['rto_city'], dict):
                        if rto_code in self.rto_city_map:
                            structured_data['rto_city']['value'] = self.rto_city_map[rto_code]
                            structured_data['rto_city']['is_extracted'] = "TRUE"
                    # Set rto_state from the code prefix
                    if 'rto_state' in structured_data and isinstance(structured_data['rto_state'], dict):
                        import re
                        state_match = re.match(r'^([A-Z]{2})', rto_code)
                        if state_match and state_match.group(1) in self.RTO_STATE_MAP:
                            structured_data['rto_state']['value'] = self.RTO_STATE_MAP[state_match.group(1)]
                            structured_data['rto_state']['is_extracted'] = "TRUE"
                    print(f"   ✓ New vehicle: derived RTO {rto_code} from location '{rto_location}'")
        else:
            # Derive rto_state from reg_num prefix (e.g., RJ10... → RAJASTHAN)
            if 'rto_state' in structured_data and isinstance(structured_data['rto_state'], dict):
                if reg_value and len(reg_value) >= 2:
                    state_code = reg_value[:2]
                    if state_code in self.RTO_STATE_MAP:
                        structured_data['rto_state']['value'] = self.RTO_STATE_MAP[state_code]
                        structured_data['rto_state']['is_extracted'] = "TRUE"

            # Derive rto_city from reg_num using RTO.csv (e.g., MH12... → PUNE)
            if 'rto_city' in structured_data and isinstance(structured_data['rto_city'], dict):
                if reg_value:
                    rto_code = self._get_rto_code(reg_value)
                    if rto_code and rto_code in self.rto_city_map:
                        structured_data['rto_city']['value'] = self.rto_city_map[rto_code]
                        structured_data['rto_city']['is_extracted'] = "TRUE"

        # Derive addon boolean flags: YES if premium > 0 OR explicitly detected in coverages
        ADDON_PAIRS = {
            'isZeroDepCover': ('zeroDepPremium', 'zero_dep'),
            'isEPCover': ('ePPremium', 'engine_protection'),
            'isConsumablesCover': ('consumablesPremium', 'consumables'),
            'isKeyReplacementCover': ('keyReplacement', 'key_replacement'),
            'isNCBCover': ('nCBProtection', 'ncb_protection'),
            'isRSACover': ('rSAPremium', 'roadside_assistance'),
            'isRTICover': ('rtiPremium', 'return_to_invoice'),
            'isLPBCover': ('lPBPremium', 'personal_belongings'),
            'isTyreProtectionCover': ('tyreProtectionPremium', 'tyre_protection'),
            'isInsurerPaidDriver': ('paidDriverPremium', 'paid_driver'),
            'pa_cover_addon': ('pa_cover', 'personal_accident'),
        }

        # Build a set of addon_names explicitly detected in Stage 1.
        # Two sources with different rules:
        #
        #   coverages[] — Stage 1 is instructed to only put SELECTED add-ons here.
        #     Count any entry with is_addon=true regardless of premium amount.
        #     This handles bundled add-ons with no individual line-item premium.
        #     Exception: skip if premium is explicitly "-" (Stage 1 mistake on template slot).
        #
        #   premiums[] — Contains every row from the premium table, including template slots.
        #     Only count if value > 0 (template slots show "-" → parsed as 0).
        detected_addons = set()
        if raw_data:
            for cov in raw_data.get('coverages', []):
                if cov.get('is_addon') and cov.get('addon_name'):
                    # Skip only if premium is explicitly "-" (unselected template slot)
                    raw_prem = str(cov.get('premium') or '').strip()
                    if raw_prem == '-':
                        continue
                    detected_addons.add(cov['addon_name'])
            # Only include premiums array entries with a positive value
            # (premium table rows include template slots with value="-" → 0)
            for prem in raw_data.get('premiums', []):
                if prem.get('addon_name'):
                    try:
                        prem_val = float(prem.get('value') or 0)
                    except (ValueError, TypeError):
                        prem_val = 0
                    if prem_val > 0:
                        detected_addons.add(prem['addon_name'])

        for flag_field, (_, addon_name) in ADDON_PAIRS.items():
            if flag_field in structured_data and isinstance(structured_data[flag_field], dict):
                # Use ONLY Stage 1's detected_addons for the boolean decision.
                # Do NOT use Stage 2's premium field value here — Stage 2 can incorrectly
                # pick up values from raw_tables (e.g. TP Total bleeding into an RSA row
                # in a two-column OD|TP table), causing false positives.
                # detected_addons is built from premiums[](addon_name+value>0) and
                # coverages[](is_addon=true) which are the clean Stage 1 signals.
                is_active = addon_name in detected_addons

                # Only override Stage 2's value when we have a clear signal;
                # if Stage 1 didn't detect it, preserve Stage 2's boolean answer
                if is_active:
                    structured_data[flag_field]['value'] = "YES"
                    structured_data[flag_field]['is_extracted'] = "TRUE"
                elif structured_data[flag_field].get('value', '').upper() not in ('YES', 'NO'):
                    # Stage 2 gave an unexpected value — normalise to NO
                    structured_data[flag_field]['value'] = "NO"
                    structured_data[flag_field]['is_extracted'] = "TRUE"
                # else: Stage 2 already said YES or NO — leave it untouched

        # Derive CNG/LPG fields from raw data
        # isCNGFitted = TRUE only when EXTERNAL/AFTERMARKET CNG/LPG kit is fitted (cngLPGUnitIDV > 0)
        # Factory CNG vehicles (CNG in model name) are NOT "CNG fitted" — they just have fuel_type=CNG
        if raw_data:
            # Check CNG Unit IDV from monetary_amounts or limits
            cng_unit_idv = 0.0
            for amt in raw_data.get('monetary_amounts', []):
                ctx = str(amt.get('context', '')).upper()
                if 'CNG UNIT' in ctx or 'LPG UNIT' in ctx:
                    val = amt.get('value', 0)
                    if val and float(val) > 0:
                        cng_unit_idv = float(val)

            # isCNGFitted is TRUE only when aftermarket kit IDV > 0
            if cng_unit_idv > 0:
                if 'isCNGFitted' in structured_data and isinstance(structured_data['isCNGFitted'], dict):
                    structured_data['isCNGFitted']['value'] = "TRUE"
                    structured_data['isCNGFitted']['is_extracted'] = "TRUE"
                if 'cngLPGUnitIDV' in structured_data and isinstance(structured_data['cngLPGUnitIDV'], dict):
                    structured_data['cngLPGUnitIDV']['value'] = str(round(cng_unit_idv))
                    structured_data['cngLPGUnitIDV']['is_extracted'] = "TRUE"

            # Derive fuel_type from vehicle make_model if not already set
            vehicles = raw_data.get('vehicles', [])
            if vehicles and isinstance(vehicles, list):
                make_model = str(vehicles[0].get('make_model', '')).upper()
                if 'fuel_type' in structured_data and isinstance(structured_data['fuel_type'], dict):
                    existing_fuel = (structured_data['fuel_type'].get('value', '') or '').strip().upper()
                    if not existing_fuel or existing_fuel in ('', 'PETROL', 'DIESEL'):
                        if 'CNG' in make_model:
                            structured_data['fuel_type']['value'] = "CNG"
                            structured_data['fuel_type']['is_extracted'] = "TRUE"
                        elif 'LPG' in make_model:
                            structured_data['fuel_type']['value'] = "LPG"
                            structured_data['fuel_type']['is_extracted'] = "TRUE"

        # Convert date fields from dd/mm/yyyy to mm/dd/yyyy
        DATE_FIELDS = [
            'manufacturing_date', 'registration_date', 'policy_start_date',
            'policy_end_date', 'issuance_date', 'tpStartDate', 'tpEndDate',
            'od_startdate', 'od_enddate', 'previous_policy_end_date'
        ]
        for field in DATE_FIELDS:
            if field in structured_data and isinstance(structured_data[field], dict):
                date_val = str(structured_data[field].get('value', '')).strip()
                if date_val and '/' in date_val:
                    parts = date_val.split('/')
                    if len(parts) == 3:
                        try:
                            # dd/mm/yyyy → mm/dd/yyyy (strip leading zeros)
                            day, month, year = int(parts[0]), int(parts[1]), parts[2]
                            if day <= 31 and month <= 12:
                                # structured_data[field]['value'] = f"{month}/{day}/{year}"
                                structured_data[field]['value'] = f"{day:02d}/{month:02d}/{year}"
                        except (ValueError, IndexError):
                            pass

        # Derive term fields from date differences
        TERM_PAIRS = {
            'policy_term': ('policy_start_date', 'policy_end_date'),
            'od_term': ('od_startdate', 'od_enddate'),
            'tp_term': ('tpStartDate', 'tpEndDate'),
        }
        for term_field, (start_field, end_field) in TERM_PAIRS.items():
            if term_field in structured_data and start_field in structured_data and end_field in structured_data:
                start_val = str(structured_data[start_field].get('value', '')).strip()
                end_val = str(structured_data[end_field].get('value', '')).strip()
                if start_val and end_val and '/' in start_val and '/' in end_val:
                    try:
                        # Dates are already in mm/dd/yyyy after conversion above
                        start_date = datetime.strptime(start_val, "%m/%d/%Y")
                        end_date = datetime.strptime(end_val, "%m/%d/%Y")
                        diff_days = (end_date - start_date).days
                        term_years = round(diff_days / 365)
                        if term_years > 0:
                            structured_data[term_field]['value'] = str(term_years)
                            structured_data[term_field]['is_extracted'] = "TRUE"
                    except (ValueError, KeyError):
                        pass

        # Derive stp_nstp from previous_policy_end_date vs policy_start_date.
        # STP if gap is <= 1 day (clean rollover); NSTP if gap > 1 day (lapse).
        # Empty (FALSE) when previous_policy_end_date is absent (new business).
        if 'stp_nstp' in structured_data and isinstance(structured_data['stp_nstp'], dict):
            prev_end_str = ''
            start_str = ''
            if 'previous_policy_end_date' in structured_data and isinstance(structured_data['previous_policy_end_date'], dict):
                prev_end_str = str(structured_data['previous_policy_end_date'].get('value', '')).strip()
            if 'policy_start_date' in structured_data and isinstance(structured_data['policy_start_date'], dict):
                start_str = str(structured_data['policy_start_date'].get('value', '')).strip()

            if prev_end_str and start_str and '/' in prev_end_str and '/' in start_str:
                try:
                    # Dates are dd/mm/yyyy after the DATE_FIELDS conversion above
                    prev_end_date = datetime.strptime(prev_end_str, "%d/%m/%Y")
                    start_date = datetime.strptime(start_str, "%d/%m/%Y")
                    diff_days = (start_date - prev_end_date).days
                    stp_value = "STP" if diff_days <= 1 else "NSTP"
                    structured_data['stp_nstp']['value'] = stp_value
                    structured_data['stp_nstp']['is_extracted'] = "TRUE"
                    structured_data['stp_nstp']['confidence_score'] = 1.0
                    structured_data['stp_nstp']['do_verification'] = "FALSE"
                except (ValueError, KeyError):
                    pass
            else:
                structured_data['stp_nstp']['value'] = ""
                structured_data['stp_nstp']['is_extracted'] = "FALSE"
                structured_data['stp_nstp']['confidence_score'] = 0.0

        # Ensure no null values
        for field, data in structured_data.items():
            if isinstance(data, dict):
                if data.get('value') is None:
                    data['value'] = ""

        return structured_data

    def _calculate_cost(self):
        """Calculate approximate cost"""
        self.total_tokens = self.total_input_tokens + self.total_output_tokens

        model_key = self.model_name
        if model_key not in self.pricing:
            model_key = "gemini-3.1-flash-lite"

        input_cost = (self.total_input_tokens / 1_000_000) * self.pricing[model_key]["input"]
        output_cost = (self.total_output_tokens / 1_000_000) * self.pricing[model_key]["output"]
        self.total_cost_usd = input_cost + output_cost

    def parse_pdf(self, pdf_path: str, save_intermediate: bool = True) -> Dict[str, Any]:
        """
        Parse PDF using Minerva API + Gemini two-stage pipeline

        Pipeline:
        1. Minerva API: PDF → Markdown
        2. Gemini Stage 1: Markdown → Raw Data (label-value extraction)
        3. Gemini Stage 2: Raw Data → Structured JSON (schema mapping)

        Args:
            pdf_path: Path to PDF file
            save_intermediate: Save intermediate markdown and raw data

        Returns:
            Dictionary with extracted fields and metadata
        """
        print(f"\n{'='*60}")
        print(f"🚀 MINERVA API + GEMINI TWO-STAGE PARSING: {Path(pdf_path).name}")
        print(f"{'='*60}")

        start_time = time.time()
        pdf_name = Path(pdf_path).stem

        # Reset token counters
        self.total_input_tokens = 0
        self.total_output_tokens = 0

        # Stage 1: Convert PDF to Markdown (Minerva API)
        # Check if markdown file already exists
        md_file = self.intermediate_dir / f"{pdf_name}.md"
        if md_file.exists():
            # print(f"\n📊 STAGE 1: Converting PDF to Markdown (Minerva API)...")
            # markdown_content = self._convert_pdf_to_markdown(pdf_path)
            print(f"\n📊 STAGE 1: Loading existing Markdown...")
            with open(md_file, 'r', encoding='utf-8') as f:
                markdown_content = f.read()
            print(f"📂 Loaded from: {md_file}")
        else:
            print(f"\n📊 STAGE 1: Converting PDF to Markdown (Minerva API)...")
            markdown_content = self._convert_pdf_to_markdown(pdf_path)

            # Save intermediate markdown
            if save_intermediate:
                with open(md_file, 'w', encoding='utf-8') as f:
                    f.write(markdown_content)
                print(f"💾 Markdown saved: {md_file}")

        # Stage 2: Extract raw data from markdown (Gemini)
        print(f"\n📊 STAGE 2: Extracting raw data (Gemini)...")
        raw_data = self._extract_raw_data(markdown_content)

        # Save intermediate raw data
        if save_intermediate and raw_data:
            raw_file = self.intermediate_dir / f"{pdf_name}_raw.json"
            with open(raw_file, 'w', encoding='utf-8') as f:
                json.dump(raw_data, f, indent=2, ensure_ascii=False)
            print(f"💾 Raw data saved: {raw_file}")

        # Stage 3: Map raw data to target schema (Gemini)
        print(f"\n📊 STAGE 3: Mapping to target schema (Gemini)...")
        if raw_data:
            structured_data = self._map_to_schema(raw_data)
            # Post-process the structured data (pass raw_data for premium validation)
            structured_data = self._post_process(structured_data, raw_data)
        else:
            print("   ⚠ No raw data to map - skipping Stage 3")
            structured_data = {}

        # Calculate cost
        self._calculate_cost()

        elapsed_time = time.time() - start_time

        result = {
            "pdf_path": str(pdf_path),
            "model_used": self.model_name,
            "extraction_timestamp": datetime.now().isoformat(),
            "processing_time_seconds": round(elapsed_time, 2),
            "extracted_fields": structured_data,
            "intermediate_data_saved": save_intermediate,
            "pipeline": "minerva-api-two-stage",
            "cost_summary": {
                "total_input_tokens": int(self.total_input_tokens),
                "total_output_tokens": int(self.total_output_tokens),
                "total_tokens": int(self.total_tokens),
                "total_cost_usd": round(self.total_cost_usd, 6),
                "model": self.model_name
            }
        }

        print(f"\n{'='*60}")
        print(f"✅ TWO-STAGE PARSING COMPLETE in {elapsed_time:.1f}s")
        print(f"💰 Cost: ${self.total_cost_usd:.6f} ({self.total_tokens} tokens)")
        print(f"{'='*60}\n")

        return result


if __name__ == "__main__":
    # Check for API key
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("❌ Error: GOOGLE_API_KEY environment variable not set")
        sys.exit(1)

    # Initialize parser
    parser = MineruGeminiParser(api_key=api_key)

    # Parse a PDF
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
    else:
        pdf_path = "policies/1001461110_N5467793.pdf"

    if not os.path.exists(pdf_path):
        print(f"❌ PDF not found: {pdf_path}")
        print(f"Usage: python {sys.argv[0]} <path-to-pdf>")
        sys.exit(1)

    # Parse
    result = parser.parse_pdf(pdf_path, save_intermediate=True)

    # Save output
    output_file = f"output_minerva_gemini_{Path(pdf_path).stem}.json"
    with open(output_file, 'w') as f:
        json.dump(result, f, indent=2)

    print(f"💾 Output saved to: {output_file}")

    # Show sample fields
    print(f"\n📋 Sample Extracted Fields:")
    for field in ["policy_number", "cust_name", "reg_num", "premium", "insurer_id"]:
        if field in result["extracted_fields"]:
            value = result["extracted_fields"][field].get("value", "")
            conf = result["extracted_fields"][field].get("confidence_score", 0)
            print(f"   {field:20} = {value[:50]} (conf: {conf:.2f})")
