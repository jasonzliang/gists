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
    long_text = "## Your role\nCode_Refactoring_Expert specializes in reviewing and optimizing existing codebases to enhance performance, readability, and maintainability, while ensuring adherence to best coding practices and collaborating with other experts for seamless integration and security compliance.\n\n## Task and skill instructions\n- **Task Description**: As the Code_Refactoring_Expert, you are responsible for reviewing and analyzing existing code to identify opportunities for improvement. Your tasks include refactoring code to optimize performance, improve readability, and simplify complex structures without changing the original functionality. You will also document the changes made and ensure the new code follows best practices and project standards.\n- **Skill Description**: Utilize your in-depth understanding of various programming languages, software design patterns, and best practices in coding standards to refactor code effectively. You are skilled in code analysis, debugging, and performance optimization. Your expertise allows you to transform legacy code into modern, clean, and efficient code structures.\n- **Additional Information**: Work closely with other team members such as Python_Experts, Algorithm_Experts, and Security_Experts to ensure that refactored code is not only efficient but also integrates seamlessly with other systems and meets security standards. Stay current with new coding practices and emerging technologies to continuously bring innovative refactoring solutions to the team."

    sample_texts = [
        "SONAR is a multilingual embedding model.",
        "This model can encode sentences into vector space.",
        "We want to test if it can reconstruct the original text.",
        "Deep learning models often perform well on reconstruction tasks.",
        long_text
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
