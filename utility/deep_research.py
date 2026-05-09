import time
import json
import argparse
from google import genai
from openai import OpenAI

# =========================================================
# 2026 PRICING CONFIGURATION (Per 1 Million Tokens)
# =========================================================

# --- Gemini 3.1 Pro (Tiered Pricing) ---
G_TIER1_IN = 2.00     # Context <= 200k
G_TIER1_OUT = 12.00
G_TIER2_IN = 4.00     # Context > 200k
G_TIER2_OUT = 18.00
G_SEARCH_QUERY = 0.014 # After 5,000 free searches/month

# --- OpenAI Deep Research Models ---
# o3-deep-research
OAI_O3_IN = 10.00
OAI_O3_OUT = 40.00
# o4-mini-deep-research
OAI_O4_IN = 2.00
OAI_O4_OUT = 8.00
OAI_SEARCH_QUERY = 0.010 # Flat $10 per 1k calls

def dump_payload(data, provider):
    filename = f"{provider}_payload.json"
    try:
        with open(filename, "w", encoding="utf-8") as f:
            if hasattr(data, 'model_dump_json'):
                f.write(data.model_dump_json(indent=2))
            else:
                json.dump(data, f, default=lambda o: o.__dict__, indent=2)
        print(f"\n[+] Raw payload saved to {filename}")
    except Exception as e:
        print(f"\n[-] Error saving payload: {e}")

# =========================================================
# GOOGLE GEMINI LOGIC
# =========================================================
def run_gemini(query):
    client = genai.Client(http_options={"timeout": 3600_000})
    print(f"Starting Gemini Deep Research...")

    interaction = client.interactions.create(
        input=query,
        agent='deep-research-pro-preview-12-2025',
        background=True,
        agent_config={
            "type": "deep-research",
            "thinking_summaries": "auto"
        }
    )

    while True:
        interaction = client.interactions.get(interaction.id)
        if interaction.status == "completed":
            print("\n=== Gemini Research Complete ===")
            print(interaction.outputs[-1].text)
            dump_payload(interaction, "gemini")

            usage = interaction.usage
            input_vol = getattr(usage, 'total_input_tokens', 0) + getattr(usage, 'total_tool_use_tokens', 0)
            output_vol = getattr(usage, 'total_output_tokens', 0) + getattr(usage, 'total_thought_tokens', 0)
            total_tokens = input_vol + output_vol

            # Determine Tier
            is_high_tier = total_tokens > 200000
            p_in = G_TIER2_IN if is_high_tier else G_TIER1_IN
            p_out = G_TIER2_OUT if is_high_tier else G_TIER1_OUT

            # Estimate search queries from unique grounding URLs
            text_output = next((o for o in interaction.outputs if hasattr(o, 'annotations')), None)
            annotations = getattr(text_output, 'annotations', []) if text_output else []
            search_queries = len(set(getattr(a, 'url', '') for a in annotations))

            token_cost = (input_vol / 1_000_000 * p_in) + (output_vol / 1_000_000 * p_out)
            search_cost = search_queries * G_SEARCH_QUERY
            cost = token_cost + search_cost

            print(f"\n{'='*40}\nGEMINI COST ANALYSIS ({'TIER 2' if is_high_tier else 'TIER 1'})\n{'='*40}")
            print(f"Input (Prompt + Web Reading): {input_vol:,}")
            print(f"Output (Thought + Report):    {output_vol:,}")
            print(f"Total Workflow Tokens:        {total_tokens:,}")
            print(f"Search Queries (est.):        ~{search_queries}")
            print(f"  Token Cost:                 ${token_cost:.4f}")
            print(f"  Search Cost (est.):         ${search_cost:.4f}")
            print(f"ESTIMATED MONETARY COST:      ${cost:.4f}\n{'='*40}")
            break
        elif interaction.status == "failed":
            print(f"Gemini Error: {interaction.error}")
            break
        time.sleep(10)

# =========================================================
# OPENAI LOGIC
# =========================================================
def run_openai(query, model):
    client = OpenAI(timeout=3600)
    print(f"Starting OpenAI Deep Research ({model})...")

    response = client.responses.create(
        model=model,
        input=query,
        background=True,
        reasoning={"summary": "auto"},
        tools=[{"type": "web_search"}]
    )

    while True:
        response = client.responses.retrieve(response.id)
        if response.status == "completed":
            print(f"\n=== OpenAI {model} Complete ===")
            print(response.output_text)
            dump_payload(response, "openai")

            usage = response.usage
            p_in = OAI_O3_IN if "o3" in model else OAI_O4_IN
            p_out = OAI_O3_OUT if "o3" in model else OAI_O4_OUT

            # OpenAI completion_tokens includes hidden reasoning/thought
            in_tokens = usage.input_tokens
            out_tokens = usage.output_tokens

            # Count web search calls from response output
            search_queries = sum(1 for item in response.output if item.type == 'web_search_call')

            token_cost = (in_tokens / 1_000_000 * p_in) + (out_tokens / 1_000_000 * p_out)
            search_cost = search_queries * OAI_SEARCH_QUERY
            cost = token_cost + search_cost

            print(f"\n{'='*40}\nOPENAI COST ANALYSIS\n{'='*40}")
            print(f"Input (Prompt + Search Data): {in_tokens:,}")
            print(f"Output (Reasoning + Report):  {out_tokens:,}")
            print(f"Search Queries:               {search_queries}")
            print(f"  Token Cost:                 ${token_cost:.4f}")
            print(f"  Search Cost:                ${search_cost:.4f}")
            print(f"ESTIMATED MONETARY COST:      ${cost:.4f}\n{'='*40}")
            break
        elif response.status == "failed":
            print(f"OpenAI Error: {response.error}")
            break
        time.sleep(10)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-Provider Deep Research CLI")
    parser.add_argument("-p", "--provider", type=str, choices=["gemini", "o3", "o4-mini"],
        default="gemini")
    parser.add_argument("-q", "--query", type=str, required=True)
    args = parser.parse_args()

    if args.provider == "gemini":
        run_gemini(args.query)
    elif args.provider == "o3":
        run_openai(args.query, "o3-deep-research")
    else:
        run_openai(args.query, "o4-mini-deep-research")