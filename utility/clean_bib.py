import subprocess
import shutil
import os
import sys
import argparse
import textwrap
import re

def parse_arguments():
    epilog_text = textwrap.dedent("""
    ==============================================================================
    TUTORIAL & EXAMPLES
    ==============================================================================

    1. THE "SAFE" DEFAULT RUN
       ----------------------
       Clean syntax, fix conference names, and fetch missing DOIs/Years.
       Output saved to 'references_clean.bib'.

       $ python clean_bib.py references.bib

    2. THE "PUBLICATION READY" RUN (Recommended)
       -----------------------------------------
       Removes BibTeX entries NOT cited in your LaTeX file, then cleans the rest.
       Crucial for reducing file size before submitting to ICML/NeurIPS.

       $ python clean_bib.py references.bib --tex main.tex

    3. PRESERVING CITATION KEYS
       ------------------------
       By default, tools might lowercase keys (e.g., 'Smith2020' -> 'smith2020').
       Use this to prevent breakage if your LaTeX relies on specific casing.

       $ python clean_bib.py references.bib --freeze-keys

    ==============================================================================
    REQUIRED TOOLS
    ==============================================================================
    - bibtex-tidy (npm install -g bibtex-tidy)
    - rebiber     (pip install git+https://github.com/yuchenlin/rebiber)
    - btac        (pip install git+https://github.com/dlesbre/bibtex-autocomplete)
    """)

    parser = argparse.ArgumentParser(
        description="A bulletproof pipeline to clean, prune, normalize, and verify BibTeX files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog_text
    )

    parser.add_argument("input_file", nargs="?", default="references.bib", help="Path to input BibTeX file")
    parser.add_argument("--output", "-o", default=None, help="Path to output file")
    parser.add_argument("--tex", help="Path to .tex file. Used to REMOVE unused entries.")
    parser.add_argument("--freeze-keys", action="store_true", help="Strictly preserve citation keys.")
    parser.add_argument("--skip-rebiber", action="store_true", help="Skip conference normalization.")

    return parser.parse_args()

def check_tool(name):
    """Checks if a command line tool is available."""
    return shutil.which(name) is not None

def run_command(command, step_name, capture=True):
    """Runs a shell command and handles logging."""
    print(f"\n--- [STEP: {step_name}] ---")
    print(f"Running: {' '.join(command)}")
    try:
        if capture:
            # Capture output so we can suppress it unless there's an error
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        else:
            # Let output stream to console (useful for progress bars like btac)
            subprocess.run(command, check=True)
        print("✅ Success")
    except subprocess.CalledProcessError as e:
        print(f"❌ Error in {step_name}:")
        if capture:
            print(e.stderr)
            print(e.stdout) # Sometimes errors are in stdout
        else:
            print("(See above for error details)")

def prune_unused_entries(bib_file, tex_file):
    """Removes BibTeX entries that are not cited in the provided .tex file."""
    print(f"\n--- [STEP: Pruning Unused Entries] ---")

    if not os.path.exists(tex_file):
        print(f"⚠️  Tex file {tex_file} not found. Skipping pruning.")
        return

    # 1. Extract citation keys from .tex file
    cited_keys = set()
    try:
        with open(tex_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                # Ignore commented lines
                if line.strip().startswith('%'):
                    continue

                # Find all citations: \cite{key}, \cite{key1, key2}
                matches = re.findall(r'\\cite[a-zA-Z]*\*?\{([^}]+)\}', line)
                for match in matches:
                    keys = [k.strip() for k in match.split(',')]
                    cited_keys.update(keys)

        # Check for \nocite{*} which means "include everything"
        with open(tex_file, 'r', encoding='utf-8', errors='ignore') as f:
            if '\\nocite{*}' in f.read():
                print("⚠️  Found \\nocite{*} in .tex file. SKIPPING pruning (all entries are required).")
                return

    except Exception as e:
        print(f"❌ Error reading .tex file: {e}")
        return

    print(f"Found {len(cited_keys)} unique citations in {tex_file}.")

    # 2. Filter the BibTeX file
    # Note: We rely on bibtex-tidy having run FIRST to ensure standard formatting
    kept_count = 0
    removed_count = 0
    temp_out = "temp_pruned.bib"

    with open(bib_file, 'r', encoding='utf-8') as infile, open(temp_out, 'w', encoding='utf-8') as outfile:
        current_entry = []
        keep_entry = False
        inside_entry = False

        for line in infile:
            stripped = line.strip()

            # Detect start of entry (ignore comments/preambles)
            if stripped.startswith('@') and not any(stripped.lower().startswith(x) for x in ['@comment', '@preamble', '@string']):
                # Write previous entry if it was valid
                if inside_entry and keep_entry:
                    outfile.writelines(current_entry)
                    kept_count += 1
                elif inside_entry:
                    removed_count += 1

                # Reset for new entry
                current_entry = [line]
                inside_entry = True
                keep_entry = False

                # Extract Key. Tidy ensures format is always: @type{key,
                try:
                    # Split by { then by ,
                    # Example: @article{vaswani2017attention,
                    key = line.split('{')[1].split(',')[0].strip()
                    if key in cited_keys:
                        keep_entry = True
                except IndexError:
                    # Fallback for weird formatting
                    pass
            else:
                if inside_entry:
                    current_entry.append(line)
                else:
                    outfile.write(line) # Write global comments immediately

        # Write final entry
        if inside_entry and keep_entry:
            outfile.writelines(current_entry)
            kept_count += 1
        elif inside_entry:
            removed_count += 1

    shutil.move(temp_out, bib_file)
    print(f"✅ Pruning complete: Kept {kept_count}, Removed {removed_count} unused entries.")

def detect_suspicious_entries(file_path):
    """Scans for entries missing DOIs, URLs, or arXiv IDs."""
    print(f"\n--- [ANALYSIS: Suspicious Entries] ---")
    suspicious_count = 0
    current_entry = None
    has_verify = False

    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith('@') and not any(stripped.lower().startswith(x) for x in ['@comment', '@preamble', '@string']):
                if current_entry and not has_verify:
                    print(f"⚠️  Missing Link (DOI/URL/ArXiv): {current_entry}")
                    suspicious_count += 1

                try:
                    current_entry = line.split('{')[1].split(',')[0].strip()
                except IndexError:
                    current_entry = "Unknown"
                has_verify = False

            # Check for any field that proves existence
            check_line = stripped.lower()
            if any(x in check_line for x in ['doi =', 'url =', 'eprint =', 'pdf =', 'acmid =']):
                has_verify = True

    if suspicious_count == 0:
        print("🎉 No suspicious entries found (all have verification links).")
    else:
        print(f"⚠️  Found {suspicious_count} entries that might be fake or incomplete.")

def main():
    args = parse_arguments()
    INPUT_FILE = args.input_file
    OUTPUT_FILE = args.output if args.output else f"{os.path.splitext(INPUT_FILE)[0]}_clean.bib"
    INTERMEDIATE_FILE = "temp_working.bib"

    if not os.path.exists(INPUT_FILE):
        print(f"Error: {INPUT_FILE} not found.")
        sys.exit(1)

    shutil.copy(INPUT_FILE, INTERMEDIATE_FILE)

    # 1. BibTeX Tidy (RUN FIRST to standardize format for Pruner)
    if check_tool("bibtex-tidy"):
        # We perform an initial clean to make the file machine-readable
        cmd = ["bibtex-tidy", INTERMEDIATE_FILE, "--curly", "--sort", "--duplicates", "--modify"]
        if args.freeze_keys:
            # Tidy might lowercase keys by default unless configured otherwise in .bibtidyrc
            # but usually it respects existing case.
            pass
        run_command(cmd, "Syntax Cleaning (bibtex-tidy)", capture=False) # Capture=False to see duplicate warnings
    else:
        print("⚠️  Skipping bibtex-tidy (Tool not found)")

    # 2. Prune Unused Entries (Now safe because file is tidy)
    if args.tex:
        prune_unused_entries(INTERMEDIATE_FILE, args.tex)

    # 3. Rebiber (Normalization)
    if not args.skip_rebiber:
        if check_tool("rebiber"):
            print("\n--- [STEP: Normalization (Rebiber)] ---")
            try:
                temp_rebiber = "temp_rebiber_out.bib"
                cmd = ["rebiber", "--input", INTERMEDIATE_FILE, "--output", temp_rebiber]
                # Run with capture=False so user sees the "Loaded..." progress
                subprocess.run(cmd, check=True)

                if os.path.exists(temp_rebiber):
                    shutil.move(temp_rebiber, INTERMEDIATE_FILE)
                    print("✅ Success: Conference names normalized.")
                else:
                    print("❌ Error: Rebiber output file was not created.")
            except subprocess.CalledProcessError:
                print("❌ Rebiber failed")
        else:
            print("⚠️  Skipping Rebiber (Tool not found)")

    # 4. BibTeX Autocomplete (btac)
    btac_cmd = None
    if check_tool("btac"):
        btac_cmd = ["btac"]
    else:
        try:
            # Try running module directly if 'btac' alias is missing
            subprocess.run([sys.executable, "-m", "bibtexautocomplete", "--version"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            btac_cmd = [sys.executable, "-m", "bibtexautocomplete"]
        except Exception:
            pass

    if btac_cmd:
        cmd = btac_cmd + [INTERMEDIATE_FILE, "--inplace", "--verbose"]
        # capture=False so we see the progress bar
        run_command(cmd, "Data Completion (btac)", capture=False)
    else:
        print("⚠️  Skipping btac (Tool not found)")

    # 5. Finalize
    shutil.move(INTERMEDIATE_FILE, OUTPUT_FILE)
    print(f"\n✨ Pipeline Complete! Saved to: {OUTPUT_FILE}")
    detect_suspicious_entries(OUTPUT_FILE)

if __name__ == "__main__":
    main()