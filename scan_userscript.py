#!/usr/bin/env python3
"""
TampermonkeyScriptScanner - A security scanner for Tampermonkey/Greasemonkey userscripts
"""

import os
import re
import sys
import json
import argparse
from urllib.request import urlopen
from urllib.parse import urlparse
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TampermonkeyScanner:
    def __init__(self, risk_threshold=5):
        self.risk_threshold = risk_threshold
        # Define suspicious patterns with risk scores
        self.suspicious_patterns = [
            # High risk patterns
            {"pattern": r"eval\s*\(", "name": "eval() usage", "risk": 8, 
             "description": "Evaluates JavaScript code dynamically, often used to hide malicious code"},
            {"pattern": r"(?<!\.)(document\.cookie)", "name": "Cookie access", "risk": 6, 
             "description": "Accesses browser cookies, could be used to steal session data"},
            {"pattern": r"(localStorage|sessionStorage)\.(get|set)Item", "name": "Storage manipulation", "risk": 5, 
             "description": "Manipulates browser storage, could be used for tracking or data theft"},
            {"pattern": r"new\s+Function\s*\(", "name": "Dynamic function creation", "risk": 8, 
             "description": "Creates functions dynamically, often used to obfuscate malicious code"},
            {"pattern": r"(btoa|atob)\s*\(", "name": "Base64 encoding/decoding", "risk": 5, 
             "description": "Base64 encoding/decoding, sometimes used to hide malicious code or data exfiltration"},
            {"pattern": r"document\.write\s*\(", "name": "document.write()", "risk": 6, 
             "description": "Modifies page content directly, could inject malicious elements"},
            {"pattern": r"\.createTextNode\s*\(", "name": "createTextNode()", "risk": 4, 
             "description": "Creates text nodes, can be used to modify page content"},
            {"pattern": r"\.innerHTML\s*=", "name": "innerHTML assignment", "risk": 7, 
             "description": "Modifies HTML content directly, could inject malicious content"},
            {"pattern": r"window\.open\s*\(", "name": "window.open()", "risk": 6, 
             "description": "Opens new browser windows, could be used for popup ads or phishing"},
            {"pattern": r"\.executeScript\s*\(", "name": "executeScript()", "risk": 9, 
             "description": "Executes arbitrary script, high risk of malicious code execution"},
            {"pattern": r"XMLHttpRequest|fetch\s*\(|\.ajax\s*\(", "name": "Network requests", "risk": 5, 
             "description": "Makes network requests, could exfiltrate data or communicate with malicious servers"},
            {"pattern": r"\.src\s*=\s*['\"]http", "name": "Remote resource loading", "risk": 6, 
             "description": "Loads external resources, could load malicious content"},
            {"pattern": r"document\.location|window\.location|location\.href", "name": "Page redirection", "risk": 7, 
             "description": "Can redirect to different websites, potential phishing risk"},
            {"pattern": r"\.replace\s*\([^)]*,(.*?function|\{)", "name": "String manipulation with function", "risk": 7, 
             "description": "Complex string manipulation with functions, often used for obfuscation"},
            
            # Obfuscation techniques
            {"pattern": r"\\u00[0-9a-f]{2}", "name": "Unicode escapes", "risk": 5, 
             "description": "Unicode escape sequences, often used to obfuscate strings"},
            {"pattern": r"\\x[0-9a-f]{2}", "name": "Hex escapes", "risk": 5, 
             "description": "Hexadecimal escape sequences, often used to obfuscate strings"},
            {"pattern": r"fromCharCode", "name": "fromCharCode usage", "risk": 6, 
             "description": "Character code conversion, often used to hide strings"},
            {"pattern": r"String\.prototype", "name": "String prototype manipulation", "risk": 7, 
             "description": "Modifies JavaScript's String behavior, can be used to hide malicious activity"},
            
            # Tampermonkey specific concerns
            {"pattern": r"@grant\s+GM_xmlhttpRequest", "name": "GM_xmlhttpRequest permission", "risk": 5, 
             "description": "Requests cross-domain XHR permissions, could enable data exfiltration"},
            {"pattern": r"@grant\s+GM_setValue|@grant\s+GM_getValue", "name": "GM storage access", "risk": 4, 
             "description": "Accesses persistent storage, could store malicious data"},
            {"pattern": r"@grant\s+GM_openInTab", "name": "GM tab opening", "risk": 5, 
             "description": "Can open new tabs, potential for unwanted redirects"},
            {"pattern": r"@grant\s+unsafeWindow", "name": "Unsafe window access", "risk": 8, 
             "description": "Requests access to page JavaScript context, high risk for compromising page security"}
        ]
        
        # External URL patterns
        self.external_url_pattern = re.compile(r'(https?://[^\s\'"]+)')
        
        # Known good CDNs
        self.good_cdns = [
            'cdn.jsdelivr.net',
            'cdnjs.cloudflare.com',
            'unpkg.com',
            'code.jquery.com',
            'ajax.googleapis.com',
            'maxcdn.bootstrapcdn.com',
            'stackpath.bootstrapcdn.com'
        ]

    def load_script_from_file(self, file_path):
        """Load a userscript from a file"""
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                return file.read()
        except Exception as e:
            logger.error(f"Error loading file {file_path}: {e}")
            return None

    def extract_metadata(self, script_content):
        """Extract userscript metadata"""
        metadata = {}
        metadata_pattern = re.compile(r'// ==UserScript==(.*?)// ==/UserScript==', re.DOTALL)
        match = metadata_pattern.search(script_content)
        
        if match:
            metadata_block = match.group(1)
            for line in metadata_block.split('\n'):
                line = line.strip()
                if line.startswith('// @'):
                    parts = line[4:].split(' ', 1)
                    if len(parts) == 2:
                        key, value = parts
                        if key in metadata:
                            if isinstance(metadata[key], list):
                                metadata[key].append(value)
                            else:
                                metadata[key] = [metadata[key], value]
                        else:
                            metadata[key] = value
        
        return metadata

    def scan_script(self, script_content):
        """Scan a script for suspicious patterns"""
        if not script_content:
            return {"error": "Empty script content"}
        
        # Extract metadata
        metadata = self.extract_metadata(script_content)
        
        # Find all suspicious patterns
        findings = []
        total_risk_score = 0
        
        for pattern in self.suspicious_patterns:
            matches = re.finditer(pattern["pattern"], script_content)
            for match in matches:
                line_number = script_content[:match.start()].count('\n') + 1
                context_start = max(0, match.start() - 40)
                context_end = min(len(script_content), match.end() + 40)
                context = script_content[context_start:context_end].strip()
                
                findings.append({
                    "pattern_name": pattern["name"],
                    "risk_score": pattern["risk"],
                    "description": pattern["description"],
                    "line": line_number,
                    "match": match.group(0),
                    "context": context
                })
                total_risk_score += pattern["risk"]
        
        # Extract and check external URLs
        external_urls = self.external_url_pattern.findall(script_content)
        suspicious_urls = []
        
        for url in external_urls:
            parsed_url = urlparse(url)
            if parsed_url.netloc and parsed_url.netloc not in self.good_cdns:
                suspicious_urls.append(url)
        
        # Check for script size and complexity
        script_size = len(script_content)
        complexity_score = 0
        if script_size > 10000:  # More than 10KB
            complexity_score += 2
        if script_size > 50000:  # More than 50KB
            complexity_score += 3
        
        # Calculate overall risk
        malicious_likelihood = "Low"
        if total_risk_score > self.risk_threshold * 3:
            malicious_likelihood = "High"
        elif total_risk_score > self.risk_threshold:
            malicious_likelihood = "Medium"
        
        return {
            "metadata": metadata,
            "script_size_bytes": script_size,
            "complexity_score": complexity_score,
            "findings": findings,
            "suspicious_urls": suspicious_urls,
            "total_risk_score": total_risk_score,
            "malicious_likelihood": malicious_likelihood
        }

    def format_report(self, scan_results, detailed=False):
        """Format scan results into a readable report"""
        report = []
        report.append("===============================================")
        report.append("TAMPERMONKEY SCRIPT SECURITY SCAN REPORT")
        report.append("===============================================")
        report.append("")
        
        # Metadata section
        if "metadata" in scan_results and scan_results["metadata"]:
            report.append("SCRIPT METADATA:")
            report.append("--------------")
            for key, value in scan_results["metadata"].items():
                report.append(f"  @{key}: {value}")
            report.append("")
        
        # Summary section
        report.append("SCAN SUMMARY:")
        report.append("------------")
        report.append(f"  Script size: {scan_results.get('script_size_bytes', 0):,} bytes")
        report.append(f"  Total risk score: {scan_results.get('total_risk_score', 0)}")
        report.append(f"  Malicious likelihood: {scan_results.get('malicious_likelihood', 'Unknown')}")
        report.append(f"  Suspicious patterns found: {len(scan_results.get('findings', []))}")
        report.append(f"  Suspicious external URLs: {len(scan_results.get('suspicious_urls', []))}")
        report.append("")
        
        # Suspicious URLs
        if scan_results.get("suspicious_urls"):
            report.append("SUSPICIOUS EXTERNAL URLS:")
            report.append("------------------------")
            for url in scan_results["suspicious_urls"]:
                report.append(f"  - {url}")
            report.append("")
        
        # Detailed findings
        if scan_results.get("findings"):
            report.append("SUSPICIOUS PATTERNS DETECTED:")
            report.append("---------------------------")
            
            # Group findings by pattern name for a cleaner report
            findings_by_pattern = {}
            for finding in scan_results["findings"]:
                pattern_name = finding["pattern_name"]
                if pattern_name not in findings_by_pattern:
                    findings_by_pattern[pattern_name] = []
                findings_by_pattern[pattern_name].append(finding)
            
            for pattern_name, pattern_findings in findings_by_pattern.items():
                # Get description and risk from the first finding of this pattern
                description = pattern_findings[0]["description"]
                risk_score = pattern_findings[0]["risk_score"]
                
                report.append(f"  {pattern_name} (Risk: {risk_score}/10)")
                report.append(f"  Description: {description}")
                report.append(f"  Occurrences: {len(pattern_findings)}")
                
                if detailed:
                    report.append("  Details:")
                    for finding in pattern_findings:
                        report.append(f"    Line {finding['line']}: {finding['match']}")
                        report.append(f"    Context: {finding['context']}")
                        report.append("")
                else:
                    # Just show the first occurrence as an example with more context
                    example = pattern_findings[0]
                    line_num = example['line']
                    match_text = example['match']
                    context = example['context']

                    # Create a more informative example line
                    report.append(f"  Example (Line {line_num}):")
                    report.append(f"    Match: {match_text}")

                    # Create a context with the suspicious pattern in bold using ANSI escape codes
                    if match_text in context:
                        # Split the context at the match
                        before_match = context[:context.find(match_text)]
                        after_match = context[context.find(match_text) + len(match_text):]

                        # Use ANSI escape codes for bold in terminal
                        # \033[1m enables bold, \033[0m resets formatting
                        highlighted_context = f"{before_match}\033[1m{match_text}\033[0m{after_match}"
                        report.append(f"    Context: {highlighted_context}")
                    else:
                        # Fallback if the match isn't found in the context
                        report.append(f"    Context: {context}")

                    report.append("")

                report.append("")

        # Recommendations section
        report.append("RECOMMENDATIONS:")
        report.append("----------------")

        if scan_results.get('malicious_likelihood') == "High":
            report.append("  HIGH RISK DETECTED! This script has multiple high-risk patterns and should NOT be installed")
            report.append("  without a thorough code review by a security professional.")
        elif scan_results.get('malicious_likelihood') == "Medium":
            report.append("  MEDIUM RISK DETECTED! Review the suspicious patterns carefully before installing this script.")
            report.append("  Consider modifying the script to remove or limit risky behaviors.")
        else:
            report.append("  LOW RISK DETECTED. This script appears to be relatively safe, but still review")
            report.append("  any suspicious patterns before installing.")

        report.append("")
        report.append("Remember: No automated scanner can guarantee script safety. Always review code carefully.")
        report.append("===============================================")

        return "\n".join(report)

def main():
    parser = argparse.ArgumentParser(description='Scan Tampermonkey/Greasemonkey userscripts for malicious patterns')
    parser.add_argument('input', help='Path to the userscript file or URL')
    parser.add_argument('--detailed', '-d', action='store_true', help='Show detailed findings')
    parser.add_argument('--json', '-j', action='store_true', help='Output in JSON format')
    parser.add_argument('--threshold', '-t', type=int, default=5, help='Risk threshold (default: 5)')
    parser.add_argument('--output', '-o', help='Output file for the report')

    args = parser.parse_args()

    scanner = TampermonkeyScanner(risk_threshold=args.threshold)

    # Load script content
    script_content = None
    if args.input.startswith(('http://', 'https://')):
        try:
            with urlopen(args.input) as response:
                script_content = response.read().decode('utf-8')
        except Exception as e:
            logger.error(f"Error fetching script from URL: {e}")
            sys.exit(1)
    else:
        script_content = scanner.load_script_from_file(args.input)
        if script_content is None:
            sys.exit(1)

    # Scan the script
    scan_results = scanner.scan_script(script_content)

    # Generate report
    if args.json:
        report = json.dumps(scan_results, indent=2)
    else:
        report = scanner.format_report(scan_results, detailed=args.detailed)

    # Output report
    if args.output:
        try:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(report)
            logger.info(f"Report saved to {args.output}")
        except Exception as e:
            logger.error(f"Error writing to output file: {e}")
            print(report)
    else:
        print(report)

if __name__ == "__main__":
    main()