#!/usr/bin/env python3
"""
Enhanced package update script with better dependency resolution strategy.
This version implements incremental updates and batch resolution for related packages.
"""

import subprocess
import sys
import os
import logging
import json
import tempfile
from collections import defaultdict
from packaging.version import parse
import re

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def run_command(command, check=True):
    """Run a shell command and return the output."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            check=check
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.CalledProcessError as e:
        if check:
            logging.error(f"Command failed: {command}")
            logging.error(f"Error: {e.stderr}")
        return e.stdout, e.stderr, e.returncode

def ensure_pip_tools():
    """Ensure pip-tools is installed."""
    try:
        import piptools
    except ImportError:
        logging.info("Installing pip-tools...")
        run_command("pip install pip-tools")

def get_current_packages():
    """Get current installed packages and their versions."""
    stdout, _, _ = run_command("pip list --format=json")
    return json.loads(stdout)

def get_outdated_packages():
    """Get list of outdated packages."""
    stdout, _, returncode = run_command("pip list --outdated --format=json", check=False)
    if returncode == 0 and stdout:
        return json.loads(stdout)
    return []

def get_package_dependencies(package_name):
    """Get dependencies of a package."""
    stdout, _, returncode = run_command(f"pip show {package_name}", check=False)
    if returncode == 0:
        for line in stdout.split('\n'):
            if line.startswith('Requires:'):
                deps = line.split(': ')[1].strip()
                return [dep.strip() for dep in deps.split(',') if dep.strip()]
    return []

def get_available_versions(package_name):
    """Get all available versions for a package."""
    stdout, _, returncode = run_command(f"pip index versions {package_name}", check=False)
    if returncode == 0:
        versions = []
        # Extract versions from the output
        lines = stdout.split('\n')
        for line in lines:
            if 'Available versions:' in line:
                continue
            match = re.findall(r'\b\d+\.\d+(?:\.\d+)*\b', line)
            versions.extend(match)
        return sorted(set(versions), key=parse, reverse=True)
    return []

def try_update_package(package_name, target_version, current_packages, constraints_content=None):
    """Try to update a package to a specific version."""
    logging.debug(f"Testing update of {package_name} to {target_version}")

    # Create requirements content maintaining current versions for other packages
    requirements_content = []
    for pkg in current_packages:
        if pkg['name'] == package_name:
            requirements_content.append(f"{package_name}=={target_version}\n")
        else:
            requirements_content.append(f"{pkg['name']}=={pkg['version']}\n")

    requirements_text = ''.join(requirements_content)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.in', delete=False) as req_file:
        req_file.write(requirements_text)
        req_file.flush()

        try:
            cmd = f"pip-compile --resolver=backtracking {req_file.name}"
            stdout, stderr, returncode = run_command(cmd, check=False)

            # Clean up
            output_file = req_file.name.replace('.in', '.txt')
            if os.path.exists(output_file):
                os.unlink(output_file)
            os.unlink(req_file.name)

            return returncode == 0, stderr
        except Exception as e:
            if os.path.exists(req_file.name):
                os.unlink(req_file.name)
            logging.error(f"Error testing update for {package_name}: {e}")
            return False, str(e)

def find_compatible_version(package_name, current_version, latest_version, current_packages):
    """Find the highest compatible version between current and latest."""
    available_versions = get_available_versions(package_name)

    if not available_versions:
        return current_version, False

    # Filter versions between current and latest
    valid_versions = [v for v in available_versions
                     if parse(current_version) < parse(v) <= parse(latest_version)]

    # Try versions from newest to oldest
    for version in valid_versions:
        success, _ = try_update_package(package_name, version, current_packages)
        if success:
            return version, True

    return current_version, False

def update_related_packages(primary_package, current_packages, outdated_packages):
    """Try to update a package along with its dependencies."""
    logging.info(f"Trying to update {primary_package['name']} with related packages...")

    deps = get_package_dependencies(primary_package['name'])
    related_outdated = [pkg for pkg in outdated_packages if pkg['name'] in deps]

    if not related_outdated:
        # No related packages to update, try solo update
        return try_update_package(
            primary_package['name'],
            primary_package['latest_version'],
            current_packages
        )

    # Try updating all related packages together
    requirements_content = []
    for pkg in current_packages:
        if pkg['name'] == primary_package['name']:
            requirements_content.append(f"{pkg['name']}=={primary_package['latest_version']}\n")
        elif pkg['name'] in [p['name'] for p in related_outdated]:
            related_pkg = next(p for p in related_outdated if p['name'] == pkg['name'])
            requirements_content.append(f"{pkg['name']}=={related_pkg['latest_version']}\n")
        else:
            requirements_content.append(f"{pkg['name']}=={pkg['version']}\n")

    requirements_text = ''.join(requirements_content)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.in', delete=False) as req_file:
        req_file.write(requirements_text)
        req_file.flush()

        try:
            cmd = f"pip-compile --resolver=backtracking {req_file.name}"
            stdout, stderr, returncode = run_command(cmd, check=False)

            # Clean up
            output_file = req_file.name.replace('.in', '.txt')
            if os.path.exists(output_file):
                os.unlink(output_file)
            os.unlink(req_file.name)

            return returncode == 0, (primary_package['name'], related_outdated)
        except Exception as e:
            if os.path.exists(req_file.name):
                os.unlink(req_file.name)
            logging.error(f"Error testing update for {primary_package['name']} with deps: {e}")
            return False, (primary_package['name'], [])

def update_packages_strategically():
    """Update packages using an enhanced strategic approach."""
    outdated_packages, current_packages = check_package_updates()

    if not outdated_packages:
        return True, []

    # Categorize packages
    easy_updates = []
    partial_updates = []
    group_updates = []

    # Phase 1: Try direct updates to latest version
    logging.info("\nPhase 1: Testing direct updates to latest versions...")
    for package in outdated_packages:
        success, error = try_update_package(
            package['name'],
            package['latest_version'],
            current_packages
        )

        if success:
            easy_updates.append(package)
            logging.info(f"✓ {package['name']} -> {package['latest_version']}")
        else:
            logging.debug(f"✗ {package['name']} cannot be directly upgraded")

    # Phase 2: Try incremental updates for failed packages
    remaining_packages = [p for p in outdated_packages if p not in easy_updates]
    if remaining_packages:
        logging.info("\nPhase 2: Trying incremental updates...")
        for package in remaining_packages:
            compatible_version, found = find_compatible_version(
                package['name'],
                package['version'],
                package['latest_version'],
                current_packages
            )

            if found and compatible_version != package['version']:
                partial_updates.append({
                    'name': package['name'],
                    'version': package['version'],
                    'latest_version': compatible_version,
                    'target_version': package['latest_version']
                })
                logging.info(f"◐ {package['name']} -> {compatible_version} (partial update)")

    # Phase 3: Try group updates for remaining packages
    remaining_packages = [p for p in outdated_packages
                         if p not in easy_updates and
                         not any(u['name'] == p['name'] for u in partial_updates)]

    if remaining_packages:
        logging.info("\nPhase 3: Trying group updates with dependencies...")
        processed = set()

        for package in remaining_packages:
            if package['name'] in processed:
                continue

            success, result = update_related_packages(package, current_packages, outdated_packages)
            if success:
                primary, related = result
                group_updates.append(result)
                processed.add(primary)
                for r in related:
                    processed.add(r['name'])
                logging.info(f"⚡ {primary} -> latest (with {len(related)} dependencies)")

    # Create final requirements
    logging.info("\nGenerating final requirements...")
    final_requirements = []
    updated_package_names = set()

    # Add all updates
    for pkg in current_packages:
        name = pkg['name']
        version = pkg['version']

        # Check if package should be updated
        if name in [p['name'] for p in easy_updates]:
            package_info = next(p for p in easy_updates if p['name'] == name)
            version = package_info['latest_version']
            updated_package_names.add(name)
        elif name in [p['name'] for p in partial_updates]:
            package_info = next(p for p in partial_updates if p['name'] == name)
            version = package_info['latest_version']
            updated_package_names.add(name)
        elif any(name in [r['name'] for r in g[1]] or name == g[0] for g in group_updates):
            # Part of a group update
            for primary, related in group_updates:
                if name == primary or name in [r['name'] for r in related]:
                    related_pkg = next(p for p in outdated_packages if p['name'] == name)
                    version = related_pkg['latest_version']
                    updated_package_names.add(name)
                    break

        final_requirements.append(f"{name}=={version}\n")

    if not updated_package_names:
        logging.error("No packages could be updated")
        return False, None

    requirements_text = ''.join(final_requirements)

    # Compile final requirements
    with tempfile.NamedTemporaryFile(mode='w', suffix='.in', delete=False) as req_file:
        req_file.write(requirements_text)
        req_file.flush()

        cmd = f"pip-compile --resolver=backtracking {req_file.name}"
        stdout, stderr, returncode = run_command(cmd, check=False)

        output_file = req_file.name.replace('.in', '.txt')
        if os.path.exists(output_file) and returncode == 0:
            with open(output_file, 'r') as f:
                output_content = f.read()

            # Clean up
            os.unlink(output_file)
            os.unlink(req_file.name)

            return True, output_content
        else:
            # Clean up
            if os.path.exists(output_file):
                os.unlink(output_file)
            os.unlink(req_file.name)
            logging.error("Failed to compile final requirements")
            return False, None

def check_package_updates():
    """Check which packages have available updates."""
    logging.info("Checking for package updates...")

    outdated = get_outdated_packages()
    if not outdated:
        logging.info("All packages are up to date!")
        return [], []

    logging.info(f"Found {len(outdated)} packages with available updates:")
    for pkg in outdated:
        logging.info(f"  • {pkg['name']}: {pkg['version']} -> {pkg['latest_version']}")

    return outdated, get_current_packages()

def main():
    """Main function."""
    # Install packaging module if needed
    try:
        from packaging.version import parse
    except ImportError:
        logging.info("Installing packaging module...")
        run_command("pip install packaging")
        from packaging.version import parse

    # Enable DEBUG logging if needed
    if '--debug' in sys.argv:
        logging.getLogger().setLevel(logging.DEBUG)

    ensure_pip_tools()

    success, output_content = update_packages_strategically()

    if success and output_content:
        # Apply the updates using pip-sync
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as temp_req:
            temp_req.write(output_content)
            temp_req.flush()

            try:
                logging.info("\nApplying updates...")
                stdout, stderr, returncode = run_command(f"pip-sync {temp_req.name}")

                if returncode == 0:
                    logging.info("✓ Successfully updated packages")

                    # Check the final state
                    logging.info("\nChecking final state...")
                    stdout, stderr, returncode = run_command("pip check", check=False)

                    if returncode == 0:
                        logging.info("No dependency conflicts found!")
                    else:
                        logging.warning("Some dependency issues remain:")
                        if stdout:
                            logging.warning(stdout)
                else:
                    logging.error("Failed to apply updates")
            finally:
                os.unlink(temp_req.name)
    else:
        logging.error("Failed to find compatible updates")
        sys.exit(1)

if __name__ == "__main__":
    main()