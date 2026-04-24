# CICS Validators

Python validators for Commission Intelligence Canonical Schema (CICS) documents.

## Overview

The validators provide comprehensive quality assurance for CICS documents through:

1. **Schema Validation** - Structure, types, formats, and business rules
2. **Rule Overlap Detection** - Identifies ambiguous or conflicting commission rules
3. **CLI Tool** - Easy-to-use command-line interface with colored output

## Components

### 1. SchemaValidator (`schema_validator.py`)

Validates CICS documents against schema requirements and business rules.

**Validation Tiers:**
- **Tier 1**: JSON structure and required fields
- **Tier 2**: Field types and enum values
- **Tier 3**: Format validation (dates, IDs, emails)
- **Tier 4**: Business rules (date consistency, rate sanity)
- **Tier 5**: Completeness and traceability
- **Tier 6**: Metadata quality

**Usage:**
```python
from schema_validator import SchemaValidator

validator = SchemaValidator()
result = validator.validate('document.json')

if result.is_valid:
    print("Valid!")
else:
    for error in result.errors:
        print(f"{error.severity}: {error.message}")
```

### 2. RuleOverlapChecker (`rule_overlap_checker.py`)

Detects overlapping commission rules that could cause ambiguous calculations.

**Overlap Types:**
- **Complete Overlap**: Identical conditions (CRITICAL)
- **Partial Overlap**: Intersecting conditions (HIGH if same priority)
- **Priority-Resolved**: Overlapping but different priorities (INFO)
- **Coverage Gaps**: Scenarios not covered by any rule

**Usage:**
```python
from rule_overlap_checker import RuleOverlapChecker

checker = RuleOverlapChecker()
result = checker.check('document.json')

for overlap in result.overlaps:
    print(f"{overlap.severity}: {overlap.description}")
```

### 3. Validation CLI (`validate.py`)

Command-line tool combining both validators with user-friendly output.

**Usage:**
```bash
# Basic validation
python validate.py document.json

# Verbose output
python validate.py document.json --verbose

# Generate JSON report
python validate.py document.json --output report.json

# Use custom schema
python validate.py document.json --schema custom_schema.json

# Disable colors
python validate.py document.json --no-color
```

**Exit Codes:**
- `0` - Valid (no errors)
- `1` - Warnings (medium severity issues)
- `2` - Errors (high or critical issues)

## Installation

No external dependencies required! Uses Python standard library only.

```bash
# Optional: Install development dependencies
pip install -r ../requirements.txt
```

**Requirements:**
- Python 3.7+
- No external packages needed

## Quick Start

### Validate a Document

```bash
cd validators
python validate.py ../examples/final_output.json
```

**Expected Output:**
```
╔══════════════════════════════════════════════════════════════════╗
║     CICS DOCUMENT VALIDATOR                                      ║
║     Commission Intelligence Canonical Schema v1.0                ║
╚══════════════════════════════════════════════════════════════════╝

Document: ../examples/final_output.json

⟳ Running schema validation...
⟳ Running rule overlap check...

════════════════════════════════════════════════════════════════════
  VALIDATION SUMMARY
════════════════════════════════════════════════════════════════════

✓ Overall Status: PASSED

Schema Validation:
  ✗ Critical: 0
  ✗ High: 0
  ⚠ Medium: 0
  ℹ Low: 0
  ℹ Info: 0

Rule Overlap Check:
  Total Rules: 12
  ✗ Critical: 0
  ✗ High: 0
  ⚠ Medium: 0
  ℹ Priority-Resolved: 3

════════════════════════════════════════════════════════════════════
  RECOMMENDATION
════════════════════════════════════════════════════════════════════
✓ APPROVE
  Document is valid and ready for production use.
```

### Programmatic Usage

```python
from schema_validator import SchemaValidator
from rule_overlap_checker import RuleOverlapChecker

# Validate schema
validator = SchemaValidator()
schema_result = validator.validate('document.json')

# Check overlaps
checker = RuleOverlapChecker()
overlap_result = checker.check('document.json')

# Get summary
print(f"Critical Issues: {schema_result.get_summary()['critical']}")
print(f"Rule Overlaps: {overlap_result.get_summary()['critical']}")

# Check specific issues
if schema_result.has_critical:
    print("CRITICAL ERRORS FOUND:")
    for error in schema_result.errors:
        if error.severity.value == "CRITICAL":
            print(f"  - {error.message}")
```

## Validation Examples

### Example 1: Valid Document

```bash
$ python validate.py ../examples/final_output.json
✓ Overall Status: PASSED
✓ APPROVE - Document is valid and ready for production use.
```

### Example 2: Missing Required Field

```bash
$ python validate.py invalid_doc.json
✗ Overall Status: FAILED
✗ [CRITICAL] Required Fields
   Field: provider.provider_id
   Missing required field: provider_id
✗ REJECT - Address all errors before using this document.
```

### Example 3: Overlapping Rules

```bash
$ python validate.py overlap_doc.json
⚠ Overall Status: PASSED WITH WARNINGS
⚠ [HIGH] COMPLETE_OVERLAP
   Product: MOTOR_4W_COMPREHENSIVE
   Rules: RULE_001 ↔ RULE_002
   Rules have identical conditions
   Suggestion: Remove duplicate rule or ensure different priorities
```

### Example 4: Business Rule Violation

```bash
$ python validate.py bad_rates.json
✗ [HIGH] Business Logic
   Field: products[0].commission_rules[2].outcome.percentage_value
   Commission percentage exceeds 100%: 150%
```

## Validation Details

### Severity Levels

| Severity | Description | Action Required |
|----------|-------------|-----------------|
| **CRITICAL** | Breaks schema, unusable data | MUST fix |
| **HIGH** | Violates business rules significantly | SHOULD fix |
| **MEDIUM** | Minor issues, suboptimal quality | Consider fixing |
| **LOW** | Best practice violations | Optional |
| **INFO** | Informational, no action needed | - |

### Common Validation Issues

#### Missing Required Fields
```
✗ [CRITICAL] Missing required field: commission_period.effective_from
```
**Fix**: Add the missing field

#### Invalid Date Format
```
✗ [CRITICAL] effective_from has invalid date format: 01/01/2024
```
**Fix**: Use YYYY-MM-DD format: `2024-01-01`

#### Duplicate Priorities
```
✗ [HIGH] Duplicate priority 10: rules RULE_001 and RULE_002
```
**Fix**: Assign unique priorities to each rule

#### Commission Rate > 100%
```
✗ [HIGH] Commission percentage exceeds 100%: 150%
```
**Fix**: Verify rate with source, should be 0-100

#### Complete Rule Overlap
```
✗ [CRITICAL] Rules have identical conditions
```
**Fix**: Remove duplicate or change conditions

## Advanced Usage

### Custom Schema Path

```bash
python validate.py document.json --schema /path/to/custom_schema.json
```

### JSON Output for CI/CD

```bash
python validate.py document.json --output report.json
cat report.json | jq '.overall_status'
```

### Integration with Scripts

```bash
#!/bin/bash
python validate.py document.json
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "Validation passed!"
    # Deploy to production
elif [ $EXIT_CODE -eq 1 ]; then
    echo "Warnings detected, review required"
    # Send notification
else
    echo "Validation failed!"
    # Block deployment
    exit 1
fi
```

### Batch Validation

```bash
# Validate all JSON files in a directory
for file in *.json; do
    echo "Validating $file..."
    python validate.py "$file" --no-color >> validation_log.txt
done
```

## Testing the Validators

### Run Against Example Documents

```bash
# Valid document
python validate.py ../examples/final_output.json

# Intermediate extraction (may have warnings)
python validate.py ../examples/pdf_extracted.json --verbose
```

### Create Test Cases

```python
# test_validators.py
from schema_validator import SchemaValidator

def test_valid_document():
    validator = SchemaValidator()
    result = validator.validate('../examples/final_output.json')
    assert result.is_valid
    assert result.get_summary()['critical'] == 0

def test_missing_required_field():
    validator = SchemaValidator()
    # Create test document with missing field
    # ...
    result = validator.validate('test_invalid.json')
    assert not result.is_valid
    assert any('provider_id' in e.message for e in result.errors)
```

## Extending the Validators

### Add Custom Validation Rules

```python
# In schema_validator.py
def _validate_custom_business_rules(self):
    """Add your custom validation logic"""
    if "products" in self.current_doc:
        for product in self.current_doc["products"]:
            # Custom rule: Motor insurance should have vehicle_type conditions
            if product.get("product_category") == "motor":
                for rule in product.get("commission_rules", []):
                    conditions = rule.get("conditions", {})
                    if "vehicle_type" not in conditions:
                        self.issues.append(ValidationIssue(
                            severity=Severity.MEDIUM,
                            message="Motor insurance rule missing vehicle_type condition",
                            field_path=f"products[].commission_rules[].rule_id={rule.get('rule_id')}",
                            category="Custom Business Rules"
                        ))
```

## Troubleshooting

### Import Errors

```
ImportError: Cannot import validators
```
**Solution**: Ensure `schema_validator.py` and `rule_overlap_checker.py` are in the same directory as `validate.py`

### JSON Decode Error

```
✗ [CRITICAL] Invalid JSON: Expecting ',' delimiter: line 45 column 5
```
**Solution**: Fix JSON syntax in the document (missing comma, bracket, etc.)

### File Not Found

```
Error: Document not found: document.json
```
**Solution**: Check file path, use absolute path if needed

## Performance

- **Small documents** (<10 products): <100ms
- **Medium documents** (10-50 products): <500ms
- **Large documents** (>50 products): <2s

Validators are optimized for typical commission documents and can handle 100+ rules efficiently.

## Contributing

To add new validators or improve existing ones:

1. Follow the existing code structure
2. Use `ValidationIssue` / `RuleOverlap` dataclasses
3. Assign appropriate severity levels
4. Add clear error messages and suggestions
5. Update this README with examples

## License

Part of the CICS (Commission Intelligence Canonical Schema) system.

## Support

For issues or questions:
- Check the main CICS documentation: `../docs/schema_reference.md`
- Review example documents: `../examples/`
- See validation prompts: `../prompts/validation_prompt.md`
