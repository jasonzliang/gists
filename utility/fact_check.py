import os
import argparse
import sys
from openfactcheck import OpenFactCheck, OpenFactCheckConfig

def main():
    # 1. ARGUMENT PARSER
    parser = argparse.ArgumentParser(description="Run OpenFactCheck with GPT-5-mini")
    parser.add_argument("input_file", help="Path to the .txt file to evaluate")

    # Allow passing keys via flags (optional), otherwise defaults to env vars
    parser.add_argument("--openai_key", default=os.getenv("OPENAI_API_KEY"), help="OpenAI API Key")
    parser.add_argument("--serper_key", default=os.getenv("SERPER_API_KEY"), help="Serper.dev API Key (Search)")
    parser.add_argument("--scraper_key", default=os.getenv("SCRAPER_API_KEY"), help="ScraperAPI.com Key (Scraping)")

    args = parser.parse_args()

    # 2. VALIDATE KEYS
    missing = []
    if not args.openai_key: missing.append("OPENAI_API_KEY")
    if not args.serper_key: missing.append("SERPER_API_KEY")
    if not args.scraper_key: missing.append("SCRAPER_API_KEY")

    if missing:
        print("Error: Missing required API keys.")
        print(f"Please export the following: {', '.join(missing)}")
        print("\nExample:")
        print("  export SCRAPER_API_KEY='...'")
        sys.exit(1)

    # 3. SET ENVIRONMENT VARIABLES
    # The library looks for these specific names internally
    os.environ["OPENAI_API_KEY"] = args.openai_key
    os.environ["SERPER_API_KEY"] = args.serper_key
    os.environ["SCRAPER_API_KEY"] = args.scraper_key

    print("Initializing OpenFactCheck (Standard Mode)...")

    # 4. CONFIGURE
    # Initialize empty to avoid TypeErrors with your specific version
    config = OpenFactCheckConfig()

    # Manually force the model to GPT-5-mini
    # We set both 'verifier_model' and 'llm_model' to cover different version conventions
    config.verifier_model = "gpt-5-mini"
    config.llm_model = "gpt-5-mini"

    # Ensure retriever is set to standard Google
    config.retriever_type = "google"

    # 5. INITIALIZE & VERIFY SETTINGS
    try:
        ofc = OpenFactCheck(config)

        # Double-check: Force the internal verifier to use the correct model
        # (This overrides defaults if the config object was ignored)
        if hasattr(ofc, 'ResponseEvaluator') and hasattr(ofc.ResponseEvaluator, 'verifier'):
             ofc.ResponseEvaluator.verifier.model_name = "gpt-5-mini"

    except Exception as e:
        print(f"Initialization Failed: {e}")
        sys.exit(1)

    # 6. RUN EVALUATION
    print(f"Evaluating '{args.input_file}' using GPT-5-mini + Google Search...")
    try:
        with open(args.input_file, "r") as f:
            content = f.read()

        report = ofc.ResponseEvaluator.evaluate(content)

        # 7. PRINT RESULTS
        print("\n" + "="*40)
        print("   FACT CHECK REPORT")
        print("="*40)

        if isinstance(report, dict):
            print(f"Overall Score: {report.get('score', 'N/A')}")
            print("-" * 40)

            for claim in report.get('claims', []):
                verdict = claim.get('label', claim.get('verdict', 'Unknown'))
                text = claim.get('text', 'Unknown Text')

                # Visual Checkmark/X
                icon = "✅" if str(verdict).lower() in ['supported', 'true'] else "❌"
                print(f"{icon} {text}")

                # Source Evidence
                if 'evidence' in claim and claim['evidence']:
                    src = claim['evidence'][0]
                    url = src.get('url') if isinstance(src, dict) else "Unknown"
                    print(f"   Source: {url}")
        else:
            print(report)

    except Exception as e:
        print(f"\nExecution Error: {e}")

if __name__ == "__main__":
    main()
