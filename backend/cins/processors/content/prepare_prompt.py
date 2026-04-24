#!/usr/bin/env python3
"""Prompt Preparation Tool — prepares user prompts for LLM extraction from document content."""

import sys
from pathlib import Path


def get_document_content(document_content_file):
    """
    Get the document content to use as user prompt.

    Args:
        document_content_file: Path to file containing extracted document content

    Returns:
        str: Document content formatted as user prompt
    """
    try:
        with open(document_content_file, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"Document content file not found: {document_content_file}")
    except Exception as e:
        raise Exception(f"Error reading document content: {str(e)}")

    return content


def prepare_extraction_request(document_content_file, extraction_prompt_file=None):
    """
    Prepare user prompt for extraction.

    Args:
        document_content_file: Path to file containing extracted document content
        extraction_prompt_file: Deprecated, ignored (for backward compatibility)

    Returns:
        tuple: (None, user_prompt)
    """
    user_prompt = get_document_content(document_content_file)
    return None, user_prompt


def main():
    """CLI entry point"""
    if len(sys.argv) < 2:
        print("Usage: python prepare_prompt.py <document_content_file>")
        print()
        print("  document_content_file: Path to extracted document content (required)")
        print()
        print("Output: Prints formatted user prompt for extraction")
        sys.exit(1)

    document_content_file = sys.argv[1]

    try:
        user_prompt = get_document_content(document_content_file)
        print("=== USER PROMPT ===")
        print(user_prompt)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
