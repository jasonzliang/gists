import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
import logging
import sys

# Set up simple logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_model(model_name):
    """
    Downloads and loads a pretrained model (GPT-1 or GPT-2) using Auto classes.
    """
    logger.info(f"Loading {model_name} model and tokenizer...")

    try:
        # AutoTokenizer determines if it needs OpenAIGPTTokenizer or GPT2Tokenizer
        tokenizer = AutoTokenizer.from_pretrained(model_name)

        # AutoModelForCausalLM determines the correct architecture
        model = AutoModelForCausalLM.from_pretrained(model_name)

        # CRITICAL FIX for GPT-2:
        # GPT-2 does not have a default pad token, which causes errors/warnings in generation.
        # GPT-1 usually handles this differently, but setting it to EOS is safe for both here.
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
            model.config.pad_token_id = model.config.eos_token_id

    except Exception as e:
        logger.error(f"Failed to load model {model_name}: {e}")
        sys.exit(1)

    # Move to GPU/MPS/CPU
    device = torch.device("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
    model.to(device)
    model.eval()

    logger.info(f"Model loaded on {device}")
    return model, tokenizer, device

def generate_text(model, tokenizer, device, prompt_text, max_length=1024):
    inputs = tokenizer(prompt_text, return_tensors="pt")
    input_ids = inputs["input_ids"].to(device)

    # Attention mask is important for GPT-2 when padding is involved,
    # though usually handled automatically by generate() if pad_token is set.
    attention_mask = inputs.get("attention_mask", None)
    if attention_mask is not None:
        attention_mask = attention_mask.to(device)

    with torch.no_grad():
        output_ids = model.generate(
            input_ids,
            attention_mask=attention_mask,
            min_length=max_length//2,
            max_length=max_length,
            do_sample=True,
            temperature=0.7,
            top_k=50,
            top_p=0.95,
            repetition_penalty=1.2,
            # Explicitly set pad_token_id to ensure GPT-2 behaves
            pad_token_id=tokenizer.pad_token_id
        )

    generated_text = tokenizer.decode(output_ids[0], skip_special_tokens=True)
    return generated_text

def main():
    try:
        print("\n--- LLM Selector ---")
        print("1. GPT-1 (openai-gpt)")
        print("2. GPT-2 Small (gpt2)")
        print("3. GPT-2 Medium (gpt2-medium)")
        print("4. GPT-2 Large (gpt2-large)")
        choice = input("Select model (1-4): ").strip()

        model_map = {
            "1": "openai-gpt",
            "2": "gpt2",
            "3": "gpt2-medium",
            "4": "gpt2-large"
        }

        selected_model = model_map.get(choice, "gpt2") # Default to gpt2 if invalid

        model, tokenizer, device = load_model(selected_model)

        print(f"\n--- Interactive Mode: {selected_model} ---")
        print("Type 'quit' or 'exit' to stop.\n")

        while True:
            prompt = input("Enter prompt: ")
            if prompt.lower() in ["quit", "exit"]:
                break

            if not prompt.strip():
                continue

            print(f"\nGenerating... (Device: {device})")
            result = generate_text(model, tokenizer, device, prompt)

            print("-" * 40)
            print(result)
            print("-" * 40 + "\n")

    except KeyboardInterrupt:
        print("\nExiting...")
    except Exception as e:
        logger.error(f"An error occurred: {e}")

if __name__ == "__main__":
    main()