import json
import os
import sys
import time
from typing import List, Optional, Union

from sonar.inference_pipelines.text import TextToEmbeddingModelPipeline, TextToTextModelPipeline
import torch
from transformers import AutoModel, AutoTokenizer
import vec2text

class Vec2TextAutoencoder:
    """
    A text autoencoder that uses vec2text for embedding inversion.

    This class allows you to encode text into embeddings and decode embeddings back to text,
    functioning as a complete autoencoder for text data.
    """

    def __init__(self,
                 embedding_model: str = "text-embedding-ada-002",
                 device: Optional[str] = None):
        """
        Initialize the text autoencoder with specified embedding model.

        Args:
            embedding_model: The name of the embedding model to use
                             Can be "text-embedding-ada-002" or "gtr-base"
            device: The device to use for computation. If None, will auto-detect.
        """
        self.embedding_model = embedding_model

        # Auto-detect device if not specified
        if device is None:
            if torch.cuda.is_available():
                self.device = "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                self.device = "mps"
            else:
                self.device = "cpu"
        else:
            self.device = device

        print(f"Using device: {self.device}")

        # Configure vec2text to use MPS if available
        # Set environment variables that vec2text might check for device selection
        if self.device == "mps":
            os.environ["VEC2TEXT_DEVICE"] = "mps"

        # Load the appropriate corrector based on the embedding model
        # We'll explicitly move it to the device after loading
        self.corrector = vec2text.load_pretrained_corrector(embedding_model)

        # Explicitly move all corrector models to the desired device
        if hasattr(self.corrector, "model"):
            self.corrector.model = self.corrector.model.to(self.device)

        # Set up the appropriate encoder based on the embedding model
        if embedding_model == "gtr-base":
            self.encoder = AutoModel.from_pretrained("sentence-transformers/gtr-t5-base").encoder.to(self.device)
            self.tokenizer = AutoTokenizer.from_pretrained("sentence-transformers/gtr-t5-base")
        else:  # Handle OpenAI models separately
            self.encoder = None
            self.tokenizer = None

        print(f"Initialized TextAutoencoder with {embedding_model} model on {self.device}")

    def encode(self, texts: List[str]) -> torch.Tensor:
        """
        Encode a list of text strings into embeddings.

        Args:
            texts: List of text strings to encode

        Returns:
            torch.Tensor: Embeddings of the input texts
        """
        if self.embedding_model == "gtr-base":
            return self._get_gtr_embeddings(texts)
        else:
            # For OpenAI models, we'd need to use the OpenAI API
            # This is just a placeholder - you'll need to implement this
            # with your own OpenAI API key
            raise NotImplementedError(
                "For OpenAI models, please use the OpenAI API directly to get embeddings "
                "and then use the decode method with those embeddings."
            )

    def _get_gtr_embeddings(self, texts: List[str]) -> torch.Tensor:
        """
        Get embeddings from the GTR model.

        Args:
            texts: List of text strings to encode

        Returns:
            torch.Tensor: Embeddings of the input texts
        """
        try:
            # Tokenize inputs
            inputs = self.tokenizer(
                texts,
                return_tensors="pt",
                max_length=512,
                truncation=True,
                padding="max_length",
            )

            # Move inputs to the appropriate device
            device_inputs = {k: v.to(self.device) for k, v in inputs.items()}

            # Ensure encoder is on the correct device
            self.encoder = self.encoder.to(self.device)

            with torch.no_grad():
                model_output = self.encoder(
                    input_ids=device_inputs['input_ids'],
                    attention_mask=device_inputs['attention_mask']
                )
                hidden_state = model_output.last_hidden_state
                embeddings = vec2text.models.model_utils.mean_pool(
                    hidden_state,
                    device_inputs['attention_mask']
                )

            # Ensure embeddings are on the correct device
            embeddings = embeddings.to(self.device)
            return embeddings

        except RuntimeError as e:
            print(f"Error in encoding: {e}")
            print(f"Falling back to CPU for encoding...")

            # Move everything to CPU as a fallback
            self.encoder = self.encoder.cpu()
            inputs = self.tokenizer(
                texts,
                return_tensors="pt",
                max_length=128,
                truncation=True,
                padding="max_length",
            )

            with torch.no_grad():
                model_output = self.encoder(
                    input_ids=inputs['input_ids'],
                    attention_mask=inputs['attention_mask']
                )
                hidden_state = model_output.last_hidden_state
                embeddings = vec2text.models.model_utils.mean_pool(
                    hidden_state,
                    inputs['attention_mask']
                )

            # Move everything back to the original device if possible
            try:
                self.encoder = self.encoder.to(self.device)
                embeddings = embeddings.to(self.device)
            except RuntimeError:
                print("Could not move back to original device")

            return embeddings

    def decode(self,
               embeddings: torch.Tensor,
               num_steps: int = 20,
               sequence_beam_width: int = 4) -> List[str]:
        """
        Decode embeddings back to text using vec2text.

        Args:
            embeddings: Tensor of embeddings to decode
            num_steps: Number of correction steps to perform
            sequence_beam_width: Beam width for sequence search

        Returns:
            List[str]: Reconstructed text from the embeddings
        """
        try:
            # Make sure embeddings are on the same device as the corrector
            embeddings_device = embeddings.to(self.device)

            # If we're using MPS, ensure the corrector model is also on MPS
            if self.device == "mps" and hasattr(self.corrector, "model"):
                # Force the corrector's model to MPS
                self.corrector.model = self.corrector.model.to(self.device)

                # vec2text sometimes has issues with direct MPS tensor input,
                # so we'll explicitly control tensor device placement
                reconstructed_texts = vec2text.invert_embeddings(
                    embeddings=embeddings_device,
                    corrector=self.corrector,
                    num_steps=num_steps,
                    sequence_beam_width=sequence_beam_width,
                )
            else:
                # For CUDA or CPU, we can directly use the device
                reconstructed_texts = vec2text.invert_embeddings(
                    embeddings=embeddings_device,
                    corrector=self.corrector,
                    num_steps=num_steps,
                    sequence_beam_width=sequence_beam_width,
                )

            return reconstructed_texts

        except RuntimeError as e:
            print(f"Device error: {e}")
            print("Trying with CPU fallback...")

            # As a last resort, try on CPU
            if hasattr(self.corrector, "model"):
                self.corrector.model = self.corrector.model.cpu()

            embeddings_cpu = embeddings.detach().cpu()
            reconstructed_texts = vec2text.invert_embeddings(
                embeddings=embeddings_cpu,
                corrector=self.corrector,
                num_steps=num_steps,
                sequence_beam_width=sequence_beam_width,
            )

            # Try to move corrector back to original device
            try:
                if hasattr(self.corrector, "model"):
                    self.corrector.model = self.corrector.model.to(self.device)
            except RuntimeError:
                print("Could not move corrector back to original device")

            return reconstructed_texts

    def reconstruct(self,
                   texts: List[str],
                   num_steps: int = 20,
                   sequence_beam_width: int = 4) -> List[str]:
        """
        Encode texts to embeddings and then decode back to texts.

        Args:
            texts: List of text strings to reconstruct
            num_steps: Number of correction steps to perform in decoding
            sequence_beam_width: Beam width for sequence search in decoding

        Returns:
            List[str]: Reconstructed texts
        """
        embeddings = self.encode(texts)
        return self.decode(embeddings, num_steps, sequence_beam_width)

def sonar_autoencoder_demo(sample_texts=None):
    print("Loading SONAR models...")

    # Initialize the text-to-embedding pipeline
    t2vec_model = TextToEmbeddingModelPipeline(
        encoder="text_sonar_basic_encoder",
        tokenizer="text_sonar_basic_encoder"
    )

    # Initialize the text-to-text pipeline
    t2t_model = TextToTextModelPipeline(
        encoder="text_sonar_basic_encoder",
        decoder="text_sonar_basic_decoder",
        tokenizer="text_sonar_basic_encoder"
    )

    # Sample input texts
    if not sample_texts:
        sample_texts = [
            "SONAR is a multilingual embedding model.",
            "This model can encode sentences into vector space.",
            "We want to test if it can reconstruct the original text.",
            "Deep learning models often perform well on reconstruction tasks."
        ]

    print("\nOriginal texts:")
    for i, text in enumerate(sample_texts):
        print(f"{i+1}: {text}")

    # Encode text to embeddings
    start = time.time()
    print("\nEncoding texts to embeddings...")
    embeddings = t2vec_model.predict(sample_texts, source_lang="eng_Latn")
    print(f"Generated embeddings shape: {embeddings.shape}")

    # Since there's no direct API to feed embeddings to the decoder,
    # we'll use the text-to-text pipeline with the same source and target language
    # to simulate an autoencoding process
    print("Reconstructing texts (using same language translation as proxy)...")
    reconstructed_texts = t2t_model.predict(
        sample_texts,
        source_lang="eng_Latn",
        target_lang="eng_Latn")
    print("Encoding/decoding time: %.2f sec" % (time.time() - start))

    print("\nReconstructed texts:")
    for i, text in enumerate(reconstructed_texts):
        print(f"{i+1}: {text}")

    # Compare original with reconstructed
    print("\n\nComparison (original vs reconstructed):")
    for i, (original, reconstructed) in enumerate(zip(sample_texts, reconstructed_texts)):
        match = '✓' if original == reconstructed else '✗'
        print(f"{i+1}: {match} | [Original]\n{original}"
            f"\n[Reconstructed]\n{reconstructed}\n")

    # Optional: Try to implement a custom decoder function
    # This would require more knowledge of SONAR internals
    print("\nNote: For true autoencoding where embeddings are directly decoded,")
    print("you would need to modify the SONAR codebase to access the decoder directly.")
    print("The approach above uses same-language translation as a proxy for autoencoding.")

def vec2text_autoencoder_demo(sample_texts=None):
    # Example using GTR-base model
    autoencoder = Vec2TextAutoencoder(embedding_model="gtr-base")

    # Example texts to reconstruct
    if not sample_texts:
        sample_texts = [
            "This is a test of the text autoencoder system",
            "Deep learning techniques can be used for natural language processing tasks",
            "The quick brown fox jumps over the lazy dog"
        ]

    print("Original texts:")
    for i, text in enumerate(sample_texts):
        print(f"{i+1}: {text}")

    # Encode to embeddings
    start = time.time()
    print("\nEncoding texts to embeddings...")
    embeddings = autoencoder.encode(sample_texts)
    print(f"Embeddings shape: {embeddings.shape}")

    # Decode back to text
    print("\nDecoding embeddings back to text...")
    reconstructed_texts = autoencoder.decode(embeddings)
    print("Encoding/decoding time: %.2f sec" % (time.time() - start))

    print("\nReconstructed texts:")
    for i, text in enumerate(reconstructed_texts):
        print(f"{i+1}: {text}")

    # Evaluate reconstruction quality
    print("\nEvaluating reconstruction similarity:")
    for i, (original, reconstructed) in enumerate(zip(sample_texts, reconstructed_texts)):
        # Simple character-level similarity
        similarity = len(set(original) & set(reconstructed)) / len(set(original) | set(reconstructed))
        print(f"Sample {i+1} similarity: {similarity:.2f}")

def load_sample_texts_from_json(
    fp="~/Desktop/MetaGPT/experiments/config/8_3_best_multirole.json"):
    fp = os.path.realpath(os.path.expanduser(fp))
    with open(fp) as f:
        role_dict = json.load(f)
    return [x["system_message"] for x in role_dict["agent_configs"]]

if __name__ == "__main__":
    sample_texts = load_sample_texts_from_json()
    vec2text_autoencoder_demo(sample_texts)
    sonar_autoencoder_demo(sample_texts)
