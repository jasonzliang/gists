import torch
from sonar.inference_pipelines.text import TextToEmbeddingModelPipeline, TextToTextModelPipeline

def text_autoencoder_demo():
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
    print("\nEncoding texts to embeddings...")
    embeddings = t2vec_model.predict(sample_texts, source_lang="eng_Latn")
    print(f"Generated embeddings shape: {embeddings.shape}")

    # Since there's no direct API to feed embeddings to the decoder,
    # we'll use the text-to-text pipeline with the same source and target language
    # to simulate an autoencoding process
    print("\nReconstructing texts (using same language translation as proxy)...")
    reconstructed_texts = t2t_model.predict(
        sample_texts,
        source_lang="eng_Latn",
        target_lang="eng_Latn"
    )

    print("\nReconstructed texts:")
    for i, text in enumerate(reconstructed_texts):
        print(f"{i+1}: {text}")

    # Compare original with reconstructed
    print("\nComparison (original vs reconstructed):")
    for i, (original, reconstructed) in enumerate(zip(sample_texts, reconstructed_texts)):
        print(f"{i+1}: {'✓' if original == reconstructed else '✗'} | {original} | {reconstructed}")

    # Optional: Try to implement a custom decoder function
    # This would require more knowledge of SONAR internals
    print("\nNote: For true autoencoding where embeddings are directly decoded,")
    print("you would need to modify the SONAR codebase to access the decoder directly.")
    print("The approach above uses same-language translation as a proxy for autoencoding.")

if __name__ == "__main__":
    text_autoencoder_demo()
