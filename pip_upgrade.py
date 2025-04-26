#!/usr/bin/env python3
"""
Script to update all packages to versions that minimize dependency conflicts using pip-tools
"""

import subprocess
import sys
import os
import logging
import json
from pathlib import Path

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

def generate_requirements_in():
    """Generate requirements.in from current environment without version constraints."""
    logging.info("Generating requirements.in from current environment...")
    
    packages = get_current_packages()
    
    # Create requirements.in with just package names (no versions)
    # This allows pip-compile to find the best compatible versions
    with open('requirements.in', 'w') as f:
        for package in packages:
            name = package['name']
            # Skip packages that shouldn't be in requirements
            if name not in ['pip', 'setuptools', 'wheel', 'pip-tools']:
                f.write(f"{name}\n")
    
    logging.info(f"Created requirements.in with {len(packages)} packages")

def resolve_with_minimal_conflicts():
    """Resolve dependencies to minimize conflicts while updating packages."""
    logging.info("="*60)
    logging.info("Starting dependency resolution to minimize conflicts")
    logging.info("="*60)
    
    # First attempt: Try to upgrade everything to latest compatible versions
    logging.info("\nPhase 1: Attempting full upgrade of all packages...")
    logging.info("Running: pip-compile --upgrade --resolver=backtracking requirements.in")
    stdout, stderr, returncode = run_command(
        "pip-compile --upgrade --resolver=backtracking requirements.in", 
        check=False
    )
    
    if returncode == 0:
        logging.info("✓ Successfully resolved all dependencies with latest versions!")
        return True
    
    # If that fails, try a more conservative approach
    logging.info("\nPhase 2: Full upgrade failed, starting incremental approach...")
    logging.info("This will attempt to upgrade packages one by one...")
    
    # Get current packages with versions
    current_packages = get_current_packages()
    
    # Create a constraints file with current versions
    with open('constraints.txt', 'w') as f:
        for package in current_packages:
            f.write(f"{package['name']}=={package['version']}\n")
    
    # Try to upgrade packages one by one
    problematic_packages = []
    updated_packages = []
    
    # Filter out packages we don't want to upgrade
    packages_to_process = [pkg for pkg in current_packages 
                          if pkg['name'] not in ['pip', 'setuptools', 'wheel', 'pip-tools']]
    
    total_packages = len(packages_to_process)
    
    for i, package in enumerate(packages_to_process, 1):
        name = package['name']
        progress = f"[{i}/{total_packages}]"
        logging.info(f"{progress} Attempting to upgrade {name}...")
        
        # Remove the constraint for this package
        temp_constraints = []
        with open('constraints.txt', 'r') as f:
            for line in f:
                if not line.startswith(f"{name}=="):
                    temp_constraints.append(line)
        
        with open('constraints.txt', 'w') as f:
            f.writelines(temp_constraints)
        
        # Try to compile with this package unconstrained
        _, _, returncode = run_command(
            f"pip-compile --constraint constraints.txt --upgrade-package {name} requirements.in",
            check=False
        )
        
        if returncode == 0:
            updated_packages.append(name)
            logging.info(f"{progress} ✓ Successfully found compatible upgrade for {name}")
        else:
            problematic_packages.append(name)
            logging.warning(f"{progress} ✗ Could not find compatible upgrade for {name}")
            # Restore the constraint
            with open('constraints.txt', 'a') as f:
                f.write(f"{name}=={package['version']}\n")
        
        # Show running totals
        successful = len(updated_packages)
        failed = len(problematic_packages)
        logging.info(f"{progress} Running total: {successful} upgraded, {failed} failed")
    
    # Final compilation with all successful upgrades
    logging.info("\nPhase 3: Performing final compilation with compatible upgrades...")
    stdout, stderr, returncode = run_command(
        "pip-compile --constraint constraints.txt requirements.in",
        check=False
    )
    
    if returncode == 0:
        logging.info("\n" + "="*60)
        logging.info("SUMMARY")
        logging.info("="*60)
        logging.info(f"✓ Successfully updated {len(updated_packages)} packages")
        
        if updated_packages:
            logging.info("\nPackages upgraded:")
            for pkg in updated_packages:
                logging.info(f"  • {pkg}")
        
        if problematic_packages:
            logging.info(f"\n✗ Could not update {len(problematic_packages)} packages due to conflicts:")
            for pkg in problematic_packages:
                logging.info(f"  • {pkg}")
        
        logging.info("="*60)
        return True
    else:
        logging.error("\nFailed to find a compatible set of package versions")
        return False

def sync_environment():
    """Sync environment with resolved dependencies."""
    logging.info("\n" + "="*60)
    logging.info("Syncing environment with resolved dependencies...")
    logging.info("="*60)
    logging.info("Running: pip-sync requirements.txt")
    logging.info("This may take a few minutes...\n")
    
    stdout, stderr, returncode = run_command("pip-sync requirements.txt")
    
    if returncode == 0:
        logging.info("✓ Successfully synced environment")
        return True
    else:
        logging.error("✗ Failed to sync environment")
        return False

def check_final_state():
    """Verify the final state has no conflicts."""
    logging.info("Checking final state for conflicts...")
    
    # Check with pip check
    stdout, stderr, returncode = run_command("pip check", check=False)
    
    if returncode == 0:
        logging.info("No dependency conflicts found!")
    else:
        logging.warning("Some dependency issues remain:")
        if stdout:
            logging.warning(stdout)
    
    # Show what was upgraded
    if os.path.exists('requirements.txt'):
        logging.info("Comparing old and new versions...")
        old_packages = get_current_packages()
        new_packages = {}
        
        with open('requirements.txt', 'r') as f:
            for line in f:
                if '==' in line and not line.startswith('#'):
                    name, version = line.strip().split('==')
                    new_packages[name] = version
        
        upgraded = []
        for old_pkg in old_packages:
            name = old_pkg['name']
            if name in new_packages and old_pkg['version'] != new_packages[name]:
                upgraded.append(f"{name}: {old_pkg['version']} -> {new_packages[name]}")
        
        if upgraded:
            logging.info("Upgraded packages:")
            for pkg in upgraded:
                logging.info(f"  {pkg}")

def main():
    """Main function to update packages while minimizing conflicts."""
    ensure_pip_tools()
    
    # Generate requirements.in without version constraints
    generate_requirements_in()
    
    # Resolve dependencies to minimize conflicts
    if resolve_with_minimal_conflicts():
        # Sync the environment
        sync_environment()
        
        # Check the final state
        check_final_state()
        
        logging.info("Update complete! Dependencies have been updated to minimize conflicts.")
    else:
        logging.error("Failed to resolve dependencies while minimizing conflicts.")
        sys.exit(1)

if __name__ == "__main__":
    main()
