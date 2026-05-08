# Objective

Clean email content by removing noise and preserving only commission-relevant data.

Return **strict JSON only**. No explanation.

---

## Remove These Elements

### 1. Email Headers

- To:, Cc:, Bcc:
- Sent:, Received:
- Subject: (unless it contains commission info)
- Message-ID, References, In-Reply-To

**Preserve `From:` lines and the sender's email address** — the carrier's domain (e.g. `futuregenerali.in`, `bajajallianz.com`, `icicilombard.com`, `hdfcergo.com`) is often the only place the carrier identity appears textually. Removing `From:` lines strips the provider signal Pass 1 needs.

### 2. Email Thread History

- Quoted reply boilerplate markers (lines starting with >, |, etc.) — strip the markers, keep the content
- Repetitive forwarded message headers (e.g. "Sent: ... To: ... Cc: ... Subject: ...") — remove these lines but keep the email body. **Always preserve `From:` lines** in every message of the chain; the sender's email address (especially the domain) is load-bearing for carrier identification and must not be stripped.

### 3. Signatures and Contact Info

- Email signatures (Name, Title, Company, Phone, Email)
- Company logos/images (preserve table structure only)
- "Sent from my iPhone/Android"
- Social media links

### 4. Legal/Confidentiality Notices

- "This email is confidential"
- "If you are not the intended recipient"
- Legal disclaimers
- Privacy notices
- "Please consider the environment before printing"

### 5. Greetings and Pleasantries

- "Dear [name]"
- "Hi [name]"
- "Hope you're doing well"
- "Thank you"
- "Best regards", "Regards", "Sincerely"
- "Looking forward to hearing from you"

---

## Preserve These Elements

### 1. Commission Data Tables

- All tables containing:
  - Commission percentages/rates
  - Premium ranges/slabs
  - Product names
  - Vehicle types
  - Coverage types
  - Any numeric data

### 2. Commission Rules Text

- Sentences describing commission terms
- Lists of products and rates
- Conditions (NCB, states, vehicle types, etc.)

### 3. Master Data References

- RTO mappings
- Declined/excluded lists
- State codes
- Vehicle classifications

### 4. Attachment References

- "Please find attached"
- "See attached file: [filename]"
- List of attachment filenames

### 5. Important Dates

- Commission period
- Effective dates
- Validity periods

### 6. Provider Information

- Insurance provider name
- Policy/product names

---

## Processing Rules

### Table Handling

- Preserve ALL tables
- Keep column headers
- Keep all rows
- Maintain table structure (markdown format preferred)

### Text Formatting

- Remove excessive blank lines (max 2 consecutive)
- Remove HTML tags (preserve content)
- Convert HTML tables to markdown
- Remove image references (keep alt text if relevant)

### Thread Detection

- Identify quoted text by:
  - Lines starting with >, |, etc.
  - "On [date]" patterns
  - Indentation changes
  - "From: ... Sent: ..." patterns
- **Preserve the full email chain** — do NOT summarize, collapse, or skip any message
- The outermost (latest) email at the top of the raw input is the most recent reply — it MUST be included even if its body is very short (e.g. "ok", "noted", "confirmed")
- Each email in the thread may contain different commission rates, products, or effective dates
- Strip boilerplate from each message in the chain (signatures, legal notices, forwarding headers like "From: ... Sent: ... To: ... Subject: ...")
- Keep the cleaned content of every message in the **same order as the raw input** — do NOT reorder by date
- Email chains are typically newest-first (outermost reply at top, oldest at bottom) — preserve that structure

### Signature Detection

- Identify signatures by:
  - "Best regards", "Sincerely", etc.
  - Email addresses
  - Phone numbers
  - Company names after main content
  - Horizontal lines (---, ___)
- Remove from first signature indicator onward

---

## Examples

### Input Email

```email
From: john@provider.com
To: agent@company.com
Subject: Commission Rates for January 2024

Hi Sarah,

Hope you're doing well. Please find attached the commission rates for January 2024.

Product | New | Renewal
4W Comp | 12% | 10%
2W TP | 8% | 7%

Best regards,
John Smith
Manager, Provider Insurance
Phone: +91-9876543210
Email: john@provider.com

CONFIDENTIALITY NOTICE: This email is confidential...
```

### Output

```json
{
  "cleaned_content": "From: john@provider.com\nDate: January 2024\n\nCommission rates for January 2024.\n\nProduct | New | Renewal\n4W Comp | 12% | 10%\n2W TP | 8% | 7%",
  "removed_sections": ["to/cc headers", "signature", "legal disclaimer"],
  "tables_found": 1,
  "attachments_mentioned": [],
  "cleaning_notes": "Standard email format with single commission table"
}
```

---

## Strict Rules

- Do NOT skip any email in the chain — even one-word replies ("ok", "noted", "confirmed") must be preserved as they signal acceptance of terms
- Do NOT reorder emails — preserve the original sequence from the raw input
- Do NOT extract or interpret commission data
- Do NOT normalize rates or terms
- Do NOT infer missing information
- Do NOT summarize content
- Preserve original wording in cleaned content
- If no cleaning needed → return original with empty removed_sections
- Output must be valid JSON
- No commentary outside JSON
