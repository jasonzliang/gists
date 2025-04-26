#!/usr/bin/env python3
"""
Alternative script to update packages using a different strategy.
This version checks for updates individually and uses batch updates for related packages.
"""

import subprocess
import sys
import os
import logging
import json
import tempfile
from collections import defaultdict

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

def try_update_package(package_name, current_version, latest_version, constraints_content=None):
    """Try to update a single package."""
    logging.info(f"Testing update of {package_name} from {current_version} to {latest_version}")
    
    # Create a requirements content for this test
    requirements_content = f"{package_name}>={latest_version}\n"
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.in', delete=False) as req_file:
        req_file.write(requirements_content)
        req_file.flush()
        
        try:
            if constraints_content:
                with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as constraints_file:
                    constraints_file.write(constraints_content)
                    constraints_file.flush()
                    
                    cmd = f"pip-compile --constraint {constraints_file.name} --resolver=backtracking {req_file.name}"
                    stdout, stderr, returncode = run_command(cmd, check=False)
                    os.unlink(constraints_file.name)
            else:
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

def update_packages_strategically():
    """Update packages using a strategic approach."""
    outdated_packages, current_packages = check_package_updates()
    
    if not outdated_packages:
        return True, []
    
    # Group packages by their update potential
    easy_updates = []
    difficult_updates = []
    
    # First, try to update each package individually
    logging.info("\nTesting individual package updates...")
    
    for package in outdated_packages:
        name = package['name']
        current_version = package['version']
        latest_version = package['latest_version']
        
        success, error = try_update_package(name, current_version, latest_version)
        
        if success:
            easy_updates.append(package)
            logging.info(f"✓ {name} can be upgraded to {latest_version}")
        else:
            difficult_updates.append(package)
            logging.warning(f"✗ {name} cannot be upgraded due to conflicts")
            if error:
                logging.debug(f"  Error: {error[:200]}...")
    
    # Create a requirements content with all easy updates
    if easy_updates:
        logging.info(f"\nApplying {len(easy_updates)} easy updates...")
        requirements_content = []
        
        # Get all current packages
        for pkg in current_packages:
            if pkg['name'] in [p['name'] for p in easy_updates]:
                # For packages that can be updated, specify the new version
                package_info = next(p for p in easy_updates if p['name'] == pkg['name'])
                requirements_content.append(f"{pkg['name']}=={package_info['latest_version']}\n")
            else:
                # For other packages, keep the current version
                requirements_content.append(f"{pkg['name']}=={pkg['version']}\n")
        
        requirements_text = ''.join(requirements_content)
        
        # Write to temporary file and compile
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
                logging.error("Failed to compile requirements with easy updates")
                return False, None
    else:
        logging.error("No packages could be updated due to conflicts")
        return False, None

def main():
    """Main function."""
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
