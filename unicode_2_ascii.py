#!/usr/bin/env python3
"""
Convert Unicode HTML entities to ASCII equivalents.
"""

import html
import re
import unicodedata

def unicode_to_ascii(text):
    """
    Convert Unicode HTML entities and character entities to ASCII equivalents.
    
    Args:
        text (str): Input text containing Unicode entities
    
    Returns:
        str: Text with Unicode entities converted to ASCII
    """
    # First, unescape HTML entities (e.g., &#39; -> ')
    text = html.unescape(text)
    
    # Convert Unicode characters to their closest ASCII representation
    # This uses NFKD normalization to decompose characters
    normalized = unicodedata.normalize('NFKD', text)
    
    # Remove combining characters and encode to ASCII
    ascii_text = normalized.encode('ascii', 'ignore').decode('ascii')
    
    # Handle some common cases that might not be properly normalized
    replacements = {
        '"': '"',  # smart quotes
        '"': '"',  # smart quotes
        ''': "'",  # smart quotes
        ''': "'",  # smart quotes
        '–': '-',  # en dash
        '—': '-',  # em dash
        '…': '...',  # ellipsis
        '•': '*',  # bullet
        '→': '->',  # arrow
        '←': '<-',  # arrow
        '↔': '<->',  # arrow
        '©': '(c)',  # copyright
        '®': '(R)',  # registered
        '™': '(TM)',  # trademark
        '°': ' degrees',  # degree symbol
        '±': '+/-',  # plus minus
        '×': 'x',  # multiplication
        '÷': '/',  # division
    }
    
    for unicode_char, ascii_char in replacements.items():
        ascii_text = ascii_text.replace(unicode_char, ascii_char)
    
    return ascii_text

def convert_file(input_file, output_file):
    """
    Convert Unicode entities in a file to ASCII equivalents.
    
    Args:
        input_file (str): Path to input file
        output_file (str): Path to output file
    """
    try:
        with open(input_file, 'r', encoding='utf-8') as infile:
            content = infile.read()
        
        ascii_content = unicode_to_ascii(content)
        
        with open(output_file, 'w', encoding='ascii') as outfile:
            outfile.write(ascii_content)
        
        print(f"Successfully converted {input_file} to {output_file}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    import sys
    
    # Example usage
    if len(sys.argv) > 1:
        if len(sys.argv) < 3:
            print("Usage: python unicode_to_ascii.py input_file output_file")
            sys.exit(1)
        
        input_file = sys.argv[1]
        output_file = sys.argv[2]
        convert_file(input_file, output_file)
    else:
        # Test the conversion with examples
        test_strings = [
            "This text has &#39;smart quotes&#39; and &#8212; em dash",
            "Unicode café becomes cafe",
            "&#169; Copyright 2024 &#8482;",
            "Temperature: 25&#176;C &#177; 5",
            "&#8216;Single&#8217; and &#8220;double&#8221; quotes",
            "Résumé with diacritics",
            "Mathematical: 5 &#215; 3 = 15",
        ]
        
        print("Testing Unicode to ASCII conversion:")
        print("-" * 40)
        
        for test in test_strings:
            converted = unicode_to_ascii(test)
            print(f"Original: {test}")
            print(f"Converted: {converted}")
            print("-" * 40)
