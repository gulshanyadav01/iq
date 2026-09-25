You are mapping extracted insurance policy data to a standardized schema.

EXTRACTED DATA (structured by category):
{RAW_DATA_JSON}

TARGET SCHEMA (map to ALL these fields):
{SCHEMA_JSON}

FIELD OUTPUT FORMAT:
Each field must be an object with: {"id": "", "value": "...", "confidence_score": 0.0-1.0, "is_extracted": "TRUE/FALSE", "do_verification": "TRUE/FALSE"}

DATA LOCATION GUIDE (where to find values in extracted data):

- identifiers: policy_number, engine no, chassis no, proposal no, receipt no
- entities: insurer name, company name, branch
- participants: customer name, nominee, intermediary, agent
- vehicles: registration no, make, model, variant, fuel type, CC, GVW, seats, wheels
- dates: policy start/end, OD start/end, TP start/end, issuance date, mfg date, reg date,
  previous policy end date
  • manufacturing_date: If only year was in the document, Stage 1 normalizes to 01/01/YYYY
  If only month+year was in the document, Stage 1 normalizes to 01/MM/YYYY. Output as-is in dd/mm/yyyy format.
  • previous_policy_end_date: Look for context like "previous policy expiry/end/to".
  Used downstream to compute STP vs NSTP — keep dd/mm/yyyy.
- premiums: OD premium, TP premium, basic OD Premium, basic TP Premium, net premium, total premium
- taxes: CGST, SGST, IGST, GST, total GST
  • serviceTax: This is the TOTAL of ALL tax components = CGST + SGST + IGST + GST
  • Look in "taxes" array for individual tax components
  • Calculate: serviceTax = cgst + sgst + igst (sum all tax components found)
- limits: IDV/sum insured, PA cover amounts, deductibles
- coverages: add-ons (zero dep, engine protect, RSA, key replacement, NCB protect, etc.)
- endorsements: IMT23, IMT34, other IMTs
- locations: address, pincode, RTO code, city, state
- monetary_amounts: any other amounts with context
- raw_tables: premium breakdowns, coverage details

MAPPING RULES:

1. Search ALL categories for each target field
2. Use "context" field to understand what each value represents
3. Use "value" field for the actual data
4. ALL text values must be UPPERCASE
5. If data not found: value="", is_extracted="FALSE"
6. Numeric fields: No currency symbols (₹/Rs), no commas
7. Dates: Keep as dd/mm/yyyy format (do NOT convert)
8. Boolean: "TRUE" or "FALSE"
9. reg_num: Remove ALL spaces
10. Return ONLY valid JSON - no markdown code blocks

────────────────────────────────────────
PREMIUM FIELD MAPPING (ANCHOR-BASED)
────────────────────────────────────────
Stage 1 "premiums" array contains every line in document order with:
• "label": exact text, "value": numeric, "line_order": position integer
• "is_tax_line": true for CGST/SGST/IGST/GST lines
• "is_discount": true for negative/discount lines
• "addon_name": set for add-on lines

STEP 1 — FIND THE TAX ANCHORS:
Identify all entries with is_tax_line = true
tax_start = minimum line_order among those entries
tax_end = maximum line_order among those entries

STEP 2 — SPLIT INTO ZONES:
pre_tax_lines = entries where is_tax_line = false AND line_order < tax_start
post_tax_lines = entries where is_tax_line = false AND line_order > tax_end
(If no tax lines found, treat all lines as pre_tax_lines)

STEP 3 — MAP EACH FIELD:

net_premium:
★ PREFERRED: Look for an EXPLICIT net_premium label in pre_tax_lines first:
"Net Premium", "Total (A+B)", "Package Premium", "Taxable Value",
"TOTAL CONSIDERATION", "TOTAL PACKAGE PREMIUM", "Net OD Premium" (SAOD),
"Net Liability Premium" (TP-only)
→ If found, use it. Done.

★ FALLBACK A — Section-Total Structure (A+B+C+D+E):
Some documents (e.g. Chola Mandalam CV) use section labels TOTAL(A), TOTAL PREMIUM(B),
TOTAL PREMIUM(C), TOTAL ADD-ON-COVERS PREMIUM(D), TOTAL OTHER CHARGES(E) but have
NO single "net premium" line. The last "non-discount" line is a zero-value structural line.
Detection: if pre_tax_lines contains entries with labels matching "TOTAL(A)", "TOTAL(B)" etc.
AND the last non-discount pre_tax line has value = 0 (or a structural "(NON PREMIUM)" label):
→ net_premium = SUM of all TOTAL(X) section labels (e.g. TOTAL(A)+TOTAL(B)+TOTAL(C)+TOTAL(D))
→ WORKED EXAMPLE: TOTAL(A)=2870 + TOTAL PREMIUM(B)=16149 + TOTAL PREMIUM(C)=550 = 19569
→ DO NOT use "Own Damage Premium" (an intermediate OD subtotal) as net_premium

★ FALLBACK B — Generic (no section structure, no explicit label):
Take the last non-discount, non-zero line in pre_tax_lines (highest line_order before tax)

premium (final):
★ ALWAYS the last entry in post_tax_lines (highest line_order after tax)
★ Labels: "Grand Total", "Amount Payable", "Total Premium Payable", "Final Premium",
"Amount Collected", "Total Including Tax", "Total with GST"
★ If no post_tax_lines, use the last line of the entire premiums array
★ Also check "monetary_amounts" array if not found in premiums

od_premium (OD Section Total — AFTER all discounts):
★ In pre_tax_lines, find entry matching OD section total labels
★ Priority labels (use first match in this order): 1. "TOTAL(A)", "TOTAL (A)", "Total A", "Gross OD(A)" — explicit section markers, most reliable 2. "Net Own Damage Premium", "Net Own Damage Premium (a)", "Net OD Premium (a)", "Net OD Premium"
"Total OD Premium", "Total OD Premium ( )", "OD TOTAL", "Total Own Damage", "Section I Total"
★ CRITICAL DISAMBIGUATION — multiple OD-related totals can exist: - Some documents show an intermediate OD subtotal (before discount) AND TOTAL(A) (after discount) - Example: "Own Damage Premium = 14351" → "Experience Based Discount = -11480" → "TOTAL(A) = 2870" - ALWAYS take TOTAL(A) = 2870, NOT "Own Damage Premium" = 14351 - RULE: When multiple candidates exist, take the one with HIGHEST line_order in the OD section
(the last OD total = after all discounts applied) - "Own Damage Premium" WITHOUT a "TOTAL", "Net", or "(A)" marker is an INTERMEDIATE subtotal — DO NOT use it
★ FALLBACK: If no OD section total label found, use basic_od_premium value

basic_od_premium (First OD line — before any add-ons or discounts):
★ In pre_tax_lines, find the FIRST line in the OD section (lowest line_order OD entry)
★ Labels (despite containing "Total" in name, these are the PRE-DISCOUNT base rate lines):
"Total Basic Own Damage Premium", "Total Basic OD Premium", "Total Basic OD",
"Basic OD Premium", "Basic Own Damage", "Basic OD", "OD Cover",
"Loss or Damage to Vehicle", "Own Damage Basic", "Section I - Basic",
"Total Own Damage Premium" (ONLY when it is line_order=1 and precedes discount lines)
★ "Own Damage Premium" (ONLY if it is the first and only OD line with no TOTAL(A) present)
★ KEY RULE: basic_od_premium is ALWAYS the FIRST non-discount OD line (lowest line_order).
Even if labeled "Total Basic..." — if it appears before any discount line, it IS basic_od_premium.
★ WORKED EXAMPLE:
[1] Total Basic Own Damage Premium = 21903 ← basic_od_premium (FIRST, before discounts)
[2] NCB Discount = -5475 ← discount
[3] Others = -14127 ← discount
[4] Own Damage Premium = 2299 ← post-discount subtotal (NOT basic_od_premium)
→ basic_od_premium = 21903, NOT 2299
★ FALLBACK: If no separate basic OD line and only one OD line exists, use od_premium value

tp_premium (TP Section Total — AFTER all add-ons):
★ In pre_tax_lines, find entry matching TP section total labels
★ Priority labels (use first match in this order): 1. "TOTAL PREMIUM(B)", "TOTAL (B)", "Total B", "Gross TP(B)" — explicit section markers 2. "Total Act Premium", "Total Act", "Act Premium" — used by some insurers (e.g. 10-Digit) 3. "Net Liability Premium (b)", "Net Liability Premium", "NET LIABILITY PREMIUM",
"TP TOTAL", "Total TP Premium", "Total Third Party", "Section II Total",
"Total Liability Premium"
★ CRITICAL: "Basic Third-Party Liability", "Basic TP", "Third Party Basic Premium",
"Third Party Liability Premium" are the FIRST TP line (basic_tp_premium), NOT the section total.
NEVER use them for tp_premium when separate add-on lines (PA cover, paid driver) exist.
tp_premium = section total AFTER all TP add-ons (PA cover, paid driver, etc.) are added.
★ TP-ONLY POLICY FALLBACK: When no explicit tp_total label exists AND there is no OD section
(od_premium = 0 or empty), tp_premium = net_premium value (they are the same for TP-only)
Example: [Third Party Liability Premium=11912] + [PA=450] + [Paid Driver=50] → [Net Premium=12412]
→ tp_premium = 12412 (= net_premium, since this is a TP-only policy)
★ DISAMBIGUATION: When multiple TP candidates exist, take the one with HIGHEST line_order
★ GENERIC FALLBACK: If no TP section total label found, use basic_tp_premium value

basic_tp_premium (First TP line — before any add-ons):
★ In pre_tax_lines, find the FIRST line in the TP section (lowest line_order TP entry)
★ Labels: "Basic TP Premium", "Basic Third Party", "Basic TP", "BASIC TP COVER",
"Basic Third-Party Liability", "Third Party Basic Premium", "TP Cover",
"Third Party Liability Premium", "Third-Party Liability Premium",
"Section II - Basic", "B. Basic - TP"
★ FALLBACK: If no separate basic TP line and only one TP line exists, use tp_premium value

sum_insured (IDV / Insured Declared Value):
★ Look in "limits" array — NOT in premiums
★ This is the maximum claim amount, NOT a premium
★ Labels: "IDV", "Insured Declared Value", "Sum Insured", "Total IDV", "Declared Value",
"Vehicle IDV", "Market Value", "CSI"
★ USE THE GRAND TOTAL: when the IDV table lists component lines (Vehicle IDV,
CNG/LPG Kit IDV, Electrical/Non-Electrical Accessories IDV, Trailer/Body IDV) AND
a "Total IDV" line, sum_insured = the "Total IDV" (the grand total). Do NOT map the
component "Vehicle IDV" — that drops the kit/accessory value.
Example: Vehicle IDV 141322 + CNG/LPG Kit IDV 50000 → Total IDV 191322 → sum_insured = 191322.
Only fall back to "Vehicle IDV" when no total is printed.
★ IDV is much larger than premium: Two Wheeler 20K-2L, Car 2L-50L, CV 5L-1Cr
★ DO NOT confuse with premium amounts

────────────────────────────────────────
TAX FIELDS IDENTIFICATION
────────────────────────────────────────
Look in "taxes" array from Stage 1 raw JSON:

cgst: entries with context/label "CGST", "Central GST" — extract numeric value only
sgst: entries with context/label "SGST", "UTGST", "State GST" — extract numeric value only
igst: entries with context/label "IGST", "Integrated GST" — extract numeric value only
NOTE: IGST replaces CGST+SGST for inter-state transactions

serviceTax (Total GST = sum of all components):
• CALCULATE: serviceTax = cgst + sgst + igst
• Or look for label "Total GST", "GST Amount", "Total Tax", "Taxes Applicable"

────────────────────────────────────────
ADD-ON PREMIUM FIELDS IDENTIFICATION
────────────────────────────────────────
Stage 1 already tagged add-on lines with "addon_name" in the premiums array.
Map each Stage 2 field by looking for the matching addon_name:

zeroDepPremium ← addon_name = "zero_dep"
ePPremium ← addon_name = "engine_protection"
engineProtectionPremium ← addon_name = "engine_protection" (same as ePPremium)
consumablesPremium ← addon_name = "consumables"
keyReplacement ← addon_name = "key_replacement"
nCBProtection ← addon_name = "ncb_protection"
rSAPremium ← addon_name = "roadside_assistance"
rtiPremium ← addon_name = "return_to_invoice"
lPBPremium ← addon_name = "personal_belongings"
tyreProtectionPremium ← addon_name = "tyre_protection"
paidDriverPremium ← addon_name = "paid_driver" (TP add-on)
bifuelKitTPPremium ← addon_name = "bifuel_kit_tp" (TP add-on, like paidDriverPremium)
★ This is the THIRD-PARTY premium for the CNG/LPG kit, part of tp_premium.
★ cngAmount remains the OD-side kit premium — the two NEVER share a rupee figure.
★ Only present when the policy prints a SEPARATE kit TP line; else default "0".
pa_cover ← addon_name = "personal_accident" (owner-driver / DEFAULT PA — a bare "Personal Accident"/"PA" line maps here)
★ pa_cover = the PREMIUM PAID by the customer (small amount: typically 100–1000 Rs)
★ Source: premiums array entry with addon_name="personal_accident" → use its "value" field
OR coverages array entry with addon_name="personal_accident" → use its "premium" field
★ NEVER use the PA cover sum/limit (1,500,000 or 15 Lakhs) — that is the claim amount, NOT premium
★ The cover amount (1,500,000) stays in the "limits" array — DO NOT map it to pa_cover
★ EXAMPLE: "Add: Compulsory PA Rs. 15 Lacs (330.00)" → pa_cover = 330 (NOT 1500000)
paUnnamedPassengerPremium ← addon_name = "pa_unnamed_passenger" (PASSENGER PA only)
★ Use the SMALL premium paid, NOT the cover sum — same rule as pa_cover above.
★ Distinct from pa_cover (owner-driver). Default "0" if no passenger-PA line.

totalAddonPremium: DO NOT extract — output value "0". It is COMPUTED in
post-processing as the sum of the 9 OD-side add-on premiums.

Also check "coverages" array for add-ons where premium may be listed there instead.
Extract ONLY numeric amount (no ₹/Rs/commas). Default to "0" if not found.

★ CRITICAL SOURCE RESTRICTION for add-on premiums:
Use ONLY these two sources — nothing else:

1. premiums[] entry where addon_name matches (e.g. addon_name="roadside_assistance" → rSAPremium)
2. coverages[] entry where addon_name matches (e.g. addon_name="roadside_assistance")
   DO NOT use raw_tables for individual add-on premium amounts.
   raw_tables rows can have column-alignment errors in two-column OD|TP layouts where
   a TP total value bleeds into the same row as an OD add-on label — always prefer
   premiums[] with addon_name over anything seen in raw_tables.

NOTE on lPBPremium: "Loss of Personal Belongings" is NOT Loan Protection / EMI Protection.
NOTE on paidDriverPremium: This is a TP add-on — included in tp_premium, NOT od_premium.

pa_cover_addon (PA cover present: YES/NO):
★ Set "YES" if pa_cover > 0 OR personal_accident found in coverages[] with is_addon=true
★ Set "NO" if no PA cover detected anywhere
★ DISTINCT from pa_cover (premium amount) — this is just the YES/NO presence flag

────────────────────────────────────────
NCB PERCENTAGE IDENTIFICATION (CRITICAL)
────────────────────────────────────────
ncb_percent (No Claim Bonus Percentage):
★ DISTINCT from NCB Protection addon and NCB Discount amount
★ Common values: 0, 20, 25, 35, 45, 50
★ Look in "ncb_details" array for items with ncb_type = "percentage"
★ Also check "premiums" for labels like "NCB: 20%", "NCB Applicable: 35%"
★ Extract ONLY the numeric value (e.g., "20", not "20%")
★ Default to "0" if not found

finalTotalDiscount (NCB Discount Amount in rupees):
★ DISTINCT from ncb_percent — this is the rupee amount deducted
★ Look in "ncb_details" array for items with ncb_type = "discount_amount"
★ Also look in "premiums" for entries with is_discount=true AND label containing "NCB"
★ Extract absolute value (remove negative sign)

────────────────────────────────────────
VEHICLE MAKE/MODEL/VARIANT IDENTIFICATION
────────────────────────────────────────
Stage 1 now provides separate "make", "model", "variant" fields in the vehicles array.
Use those directly if present. If only "make_model" combined text is available:

- First 1-2 words = Make (e.g., "MARUTI SUZUKI", "TATA", "HYUNDAI")
- Last word(s) = Variant (e.g., "VXI", "ZXI", "SX PLUS", "XZ+")
- Middle words = Model (e.g., "SWIFT DZIRE", "CRETA", "NEXON")
  ALL values UPPERCASE. Remove special characters and extra spaces.

────────────────────────────────────────
FIELD MAPPING
────────────────────────────────────────

- policy_number: Look in "identifiers" for policy/certificate number
- previous_policy_number: Look in "identifiers" for context "previous_policy_number"
  (labels "Previous/Expiring Policy No"). This is the PRIOR policy's number — never copy
  the current policy_number here. Leave value="" and is_extracted="FALSE" when not printed.
- business_type: Look in "identifiers" for context "business_type". The value MUST be
  exactly NEW, RENEWAL, or ROLLOVER (uppercase). Do NOT use the permit "Policy Type"
  (Public/Private Carrier). Leave value="" if the document does not state it — it is
  filled in post-processing.
- branch: Look in "entities" for context "branch" (the insurer's issuing/servicing
  branch or office). Leave value="" and is_extracted="FALSE" when not printed.
- insurer_id: Look in "entities" for insurance company name
- cust_name: Look in "participants" for insured/customer name
- mobileNo: Look in "identifiers" or "participants" for a mobile/phone number (context "mobile")
- email: Look in "identifiers" or "participants" for an email address (context "email")
- reg_num: Look in "vehicles" or "identifiers" for registration number
- engineNo, chassisNo: Look in "identifiers" or "vehicles"
- sum_insured: Look in "limits" for IDV/sum insured
- cgst, sgst, igst: Look in "taxes" array for individual tax components
- serviceTax: Calculate as cgst + sgst + igst (sum of all tax components)
- policy_start_date, policy_end_date: Look in "dates"
- od_startdate, od_enddate: Look in "dates" for the OWN DAMAGE (OD) cover period —
  context like "od start/end", "own damage from/to". Output as dd/mm/yyyy.
  Leave value="" and is_extracted="FALSE" for a liability-only / Third-Party policy.
- tpStartDate, tpEndDate: Look in "dates" for the THIRD PARTY (TP) / liability cover
  period — context like "tp start/end", "third party from/to", "liability from/to".
  Output as dd/mm/yyyy. Leave value="" and is_extracted="FALSE" for a standalone-OD
  (SAOD) policy with no separate TP period.
- previous_policy_end_date: Look in "dates" for context like "previous policy expiry",
  "previous policy to", "expiry of previous policy". Output as dd/mm/yyyy.
  Leave value="" and is_extracted="FALSE" when not found (new business).
- stp_nstp: DO NOT set this field. Output {"id":"", "value":"", "confidence_score":0.0,
  "is_extracted":"FALSE", "do_verification":"FALSE"}. Computed in post-processing.
- search_text_make: Look in "vehicles" - extract MANUFACTURER only (see rules above)
- model_name: Look in "vehicles" - extract MODEL only (see rules above)
- variantName: Look in "vehicles" - extract VARIANT/TRIM only (see rules above)
- VehicleSubClass: Look in "vehicles" for body type, vehicle type, or sub-class description.
  Pick the CLOSEST canonical name from the schema enum (see field description above).
  Return ONLY the canonical name — do not return raw document text.
  Leave value="" if nothing is found or if uncertain.
- fuel_type: Look in "vehicles" for fuel type (PETROL, DIESEL, CNG, ELECTRIC, HYBRID)
  • Also check make_model for "CNG" or "LPG" keywords → fuel_type = "CNG" or "LPG"
- cubic_capacity_value, vehicle_weight_value, no_of_seats, no_of_wheels: Look in "vehicles"
- VehicleClass / product_id: a printed vehicle_body_type of PCV / GCV / BUS / MISC-D / "3W" /
  "3 Wheeler" / goods- or passenger-carrying means a COMMERCIAL VEHICLE (product CV, class
  PCV/GCV/BUS/MISC-D as applicable) — regardless of the make/model. A commercial 3-wheeler is
  NOT a Two Wheeler even if the make also sells scooters.
- isCNGFitted: Set "TRUE" ONLY when an EXTERNAL/AFTERMARKET CNG/LPG kit is fitted
  • The indicator is: CNG Unit IDV > 0 in "monetary_amounts" or "limits"
  • Factory CNG vehicles (CNG in model name like "ERTIGA VXI CNG") are NOT "CNG fitted"
  • Factory CNG = fuel_type is "CNG", but isCNGFitted stays "FALSE"
- cngLPGUnitIDV: Look in "monetary_amounts" or "limits" for "CNG Unit" or "LPG Unit" IDV value
  • This is the insured value of the aftermarket CNG/LPG kit (0 for factory CNG)
- cngAmount: The premium for insuring the aftermarket CNG/LPG kit
  • Only applicable when cngLPGUnitIDV > 0
  • "Built in CNG - OD" for a factory CNG vehicle is NOT cngAmount (it's part of basic OD)
- Add-on boolean covers (isZeroDepCover, isEPCover, isConsumablesCover, isRSACover, isRTICover,
  isNCBCover, isKeyReplacementCover, isLPBCover, isTyreProtectionCover, isInsurerPaidDriver):

  ★ Set "YES" ONLY when at least ONE of these two conditions is true:

  1. coverages[] has an entry with is_addon=true AND addon_name matching this add-on
  2. premiums[] has an entry with addon_name matching this add-on AND value > 0

  ★ Set "NO" in ALL other cases — including:
  • The add-on name appears in premiums[] WITHOUT an addon_name field set
  • The add-on name appears in raw_tables (template layouts list all slots, selected or not)
  • The premium value is 0, "-", blank, or missing
  • coverages[] is empty and no premiums[] entry has a matching addon_name

  This is strict: an add-on NAME appearing anywhere is not enough. It must have
  been explicitly tagged by Stage 1 with addon_name (proving it was selected).

- Add-on premiums: Look in "premiums" (only entries with addon_name set) or "coverages"
- ncb_percent: Look in "ncb_details" array for items with ncb_type="percentage" (see NCB section above)
- finalTotalDiscount: Look in "ncb_details" for ncb_type="discount_amount" OR in "premiums" for negative NCB discount
- IMT values: Look in "endorsements"
- cust_address, pinCode, rto_num, rto_city, rto_state: Look in "locations"
- nomineeName, nomineeRelationWithCustomer: Look in "participants" for an entry whose
  context/role is "nominee" ONLY. If no participant is tagged nominee (the Nominee
  Details table is blank), leave BOTH empty. NEVER use the insured / policyholder /
  cust_name / owner as the nominee.
- financialInstitutionName: Look in "participants" or "entities" for an entry whose
  context is "financier" / "hypothecation" (the "Financier Details" value, e.g.
  "AXIS BANK LTD", "BAJAJ AUTO CREDIT LTD"). Leave empty if none.
- intermediary_name, intermediary_code: Look in "participants", "identifiers" or
  "entities". intermediary_code is the broker/agent/IMD CODE (context "intermediary_code",
  labels "Intermediary Code", "Broker Code", "Agent Code", "IMD Code/No"); intermediary_name
  is the broker/agent NAME (context "intermediary_name"). Keep the code's exact
  alphanumeric value; leave value="" and is_extracted="FALSE" when not printed.

OUTPUT:
{
"policy_number": {"id": "", "value": "POL123456", "confidence_score": 0.95, "is_extracted": "TRUE", "do_verification": "FALSE"},
"insurer_id": {"id": "", "value": "BAJAJ ALLIANZ GENERAL INSURANCE", "confidence_score": 0.95, "is_extracted": "TRUE", "do_verification": "FALSE"},
... (ALL fields from schema)
}
