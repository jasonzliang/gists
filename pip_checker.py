#!/usr/bin/env python3
"""
Pip Package Compatibility Checker and Upgrader

This script checks for incompatible pip packages or packages with dependency versions that mismatch,
then upgrades each of them to the latest possible version while maintaining compatibility.
"""

import subprocess
import sys
import json
import pkg_resources
from packaging import version
import logging
import argparse

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

def run_command(command, capture_output=True):
    """Run a shell command and return the output."""
    try:
        if capture_output:
            result = subprocess.run(command, shell=True, capture_output=True, text=True)
            if result.returncode != 0:
                if result.stderr and result.stderr.strip():
                    logging.debug(f"Command '{command}' returned with code {result.returncode}")
                    logging.debug(f"stderr: {result.stderr}")
            return result.stdout, result.stderr, result.returncode
        else:
            result = subprocess.run(command, shell=True)
            return None, None, result.returncode
    except Exception as e:
        logging.error(f"Exception running command: {command}")
        logging.error(f"Exception: {str(e)}")
        return None, str(e), 1

def check_dependencies():
    """Check for dependency conflicts using pip check."""
    logging.info("Checking for dependency conflicts...")
    stdout, stderr, returncode = run_command("pip check")
    
    # Check if pip check is not available or if there's an error
    if returncode != 0:
        if not stdout and not stderr:
            logging.info("No output from pip check command - either no conflicts or command not available")
            return check_dependencies_alternative()
        elif stderr and 'unknown command' in stderr.lower():
            logging.info("pip check command not available, using alternative method...")
            return check_dependencies_alternative()
        elif stderr and 'no broken requirements found' in stderr.lower():
            logging.info("No dependency conflicts found (pip check).")
            return []
        else:
            # Some other error occurred, but there might be conflict information
            conflicts = []
            if stdout:
                lines = stdout.strip().split('\n')
                for line in lines:
                    if 'requires' in line.lower() or 'incompatible' in line.lower():
                        conflicts.append(line.strip())
            
            if not conflicts and stderr:
                # If no conflicts found in stdout, check stderr for actual errors
                if 'no broken requirements found' in stderr.lower():
                    logging.info("No dependency conflicts found.")
                    return []
                else:
                    logging.warning(f"pip check encountered an error: {stderr.strip()}")
                    return check_dependencies_alternative()
            
            if conflicts:
                logging.warning(f"Found {len(conflicts)} dependency conflicts.")
            return conflicts
    else:
        logging.info("No dependency conflicts found.")
        return []

def check_dependencies_alternative():
    """Alternative method to check dependencies using pkg_resources."""
    conflicts = []
    try:
        working_set = pkg_resources.working_set
        for dist in working_set:
            try:
                dist.requires()
            except pkg_resources.VersionConflict as e:
                conflicts.append(str(e))
            except pkg_resources.DistributionNotFound as e:
                conflicts.append(str(e))
    except Exception as e:
        logging.error(f"Error in alternative dependency check: {str(e)}")
    
    if conflicts:
        logging.warning(f"Found {len(conflicts)} dependency issues using alternative method.")
    else:
        logging.info("No dependency conflicts found using alternative method.")
    
    return conflicts

def get_package_info(package_name):
    """Get detailed information about a package."""
    try:
        stdout, _, _ = run_command(f"pip show {package_name}")
        info = {}
        if stdout:
            for line in stdout.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    info[key.strip()] = value.strip()
        return info
    except Exception as e:
        logging.error(f"Error getting info for package {package_name}: {str(e)}")
        return {}

def get_outdated_packages():
    """Get list of outdated packages."""
    logging.info("Checking for outdated packages...")
    stdout, stderr, returncode = run_command("pip list --outdated --format=json")
    
    if returncode == 0 and stdout:
        try:
            outdated_packages = json.loads(stdout)
            logging.info(f"Found {len(outdated_packages)} outdated packages.")
            return outdated_packages
        except json.JSONDecodeError:
            logging.error("Error parsing outdated packages JSON")
            return get_outdated_packages_alternative()
    elif 'unknown option' in str(stderr).lower():
        return get_outdated_packages_alternative()
    return []

def get_outdated_packages_alternative():
    """Alternative method to get outdated packages."""
    logging.info("Using alternative method to check for outdated packages...")
    outdated = []
    try:
        stdout, _, returncode = run_command("pip list --outdated")
        if returncode == 0 and stdout:
            lines = stdout.strip().split('\n')[2:]  # Skip header lines
            for line in lines:
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 3:
                        outdated.append({
                            'name': parts[0],
                            'version': parts[1],
                            'latest_version': parts[2]
                        })
    except Exception as e:
        logging.error(f"Error in alternative outdated check: {str(e)}")
    return outdated

def get_dependency_tree(package_name):
    """Get dependency tree for a package."""
    stdout, stderr, returncode = run_command(f"pipdeptree -p {package_name}")
    
    if returncode == 0 and stdout:
        return stdout
    elif 'command not found' in str(stderr).lower() or 'no such file' in str(stderr).lower():
        # pipdeptree not available, use pip show instead
        info = get_package_info(package_name)
        if 'Requires' in info:
            return f"{package_name}\n  Requires: {info['Requires']}"
    return ""

def safe_upgrade_package(package_name, current_version=None, max_version=None):
    """Safely upgrade a package while checking compatibility."""
    try:
        logging.info(f"Running dry run for {package_name}...")
        
        # First, dry run to see what would be upgraded with timeout
        cmd = f"pip install --upgrade --dry-run {package_name}"
        if max_version:
            cmd = f"pip install --upgrade --dry-run {package_name}<={max_version}"
        
        stdout, stderr, returncode = run_command(cmd)
        
        if returncode == 0:
            # Actually perform the upgrade
            real_cmd = cmd.replace("--dry-run", "")
            logging.info(f"Performing actual upgrade for {package_name}...")
            stdout, stderr, returncode = run_command(real_cmd)
            
            if returncode == 0:
                logging.info(f"Successfully upgraded {package_name}")
                return True
            else:
                logging.error(f"Failed to upgrade {package_name}: {stderr}")
                # Try to roll back to the original version
                if current_version:
                    rollback_cmd = f"pip install {package_name}=={current_version}"
                    run_command(rollback_cmd)
                    logging.info(f"Rolled back {package_name} to version {current_version}")
                return False
        else:
            logging.warning(f"Dry run failed for {package_name}: {stderr}")
            return False
            
    except Exception as e:
        logging.error(f"Error upgrading {package_name}: {str(e)}")
        return False

def resolve_conflicts_and_upgrade():
    """Main function to resolve conflicts and upgrade packages."""
    # Check if pipdeptree is installed
    try:
        import pipdeptree
        pipdeptree_available = True
    except ImportError:
        logging.info("pipdeptree not available, will use basic dependency checking...")
        pipdeptree_available = False
        try:
            logging.info("Attempting to install pipdeptree...")
            stdout, stderr, returncode = run_command("pip install pipdeptree")
            if returncode == 0:
                import pipdeptree
                pipdeptree_available = True
        except Exception as e:
            logging.warning(f"Could not install pipdeptree: {str(e)}")
            pipdeptree_available = False
    
    # Step 1: Check for dependency conflicts
    conflicts = check_dependencies()
    if conflicts:
        logging.info("Dependency conflicts detected:")
        for conflict in conflicts:
            logging.info(f"  - {conflict}")
    
    # Step 2: Get outdated packages
    outdated_packages = get_outdated_packages()
    
    # Step 3: Create a dependency graph
    stdout, _, returncode = run_command("pip list --format=json")
    if returncode == 0 and stdout:
        try:
            all_packages = json.loads(stdout)
        except json.JSONDecodeError:
            # Fallback to plain list
            stdout, _, _ = run_command("pip list")
            all_packages = []
            if stdout:
                lines = stdout.strip().split('\n')[2:]  # Skip header
                for line in lines:
                    if line.strip():
                        parts = line.split()
                        if len(parts) >= 2:
                            all_packages.append({'name': parts[0], 'version': parts[1]})
    else:
        all_packages = []
    
    logging.info(f"Building dependency graph for {len(all_packages)} packages...")
    dependency_graph = {}
    
    # Only process outdated packages to speed up the operation
    packages_to_process = set()
    for pkg in outdated_packages:
        packages_to_process.add(pkg['name'])
    
    # Add dependencies of outdated packages
    for i, package in enumerate(all_packages):
        package_name = package['name']
        if package_name in packages_to_process:
            if (i + 1) % 10 == 0:
                logging.info(f"Processed {i + 1}/{len(all_packages)} packages...")
            
            dependency_graph[package_name] = {
                'version': package['version'],
                'dependencies': []
            }
            
            # Get dependencies for this package
            info = get_package_info(package_name)
            if 'Requires' in info and info['Requires']:
                requires = info['Requires'].split(', ')
                for req in requires:
                    if req.strip():
                        dep_name = req.split()[0].strip()
                        dependency_graph[package_name]['dependencies'].append(dep_name)
                        packages_to_process.add(dep_name)
    
    logging.info("Dependency graph built. Starting upgrade process...")
    
    # Step 4: Sort packages based on dependency order (topological sort)
    def topological_sort(graph):
        visited = set()
        result = []
        
        def visit(node):
            if node not in visited:
                visited.add(node)
                if node in graph:
                    for dep in graph[node]['dependencies']:
                        if dep in graph:  # Only visit dependencies that are in our graph
                            visit(dep)
                result.append(node)
        
        for node in graph:
            visit(node)
        
        return result[::-1]  # Reverse to get proper order
    
    upgrade_order = topological_sort(dependency_graph)
    
    # Step 5: Upgrade packages in the correct order
    upgraded_count = 0
    failed_upgrades = []
    
    packages_to_upgrade = [pkg for pkg in upgrade_order if any(p['name'] == pkg for p in outdated_packages)]
    total_to_upgrade = len(packages_to_upgrade)
    
    logging.info(f"Starting upgrade of {total_to_upgrade} packages...")
    
    for i, package_name in enumerate(packages_to_upgrade):
        # Check if package is outdated
        outdated_info = next((pkg for pkg in outdated_packages if pkg['name'] == package_name), None)
        
        if outdated_info:
            current_version = outdated_info['version']
            latest_version = outdated_info['latest_version']
            
            logging.info(f"[{i+1}/{total_to_upgrade}] Upgrading {package_name}: {current_version} -> {latest_version}")
            
            if safe_upgrade_package(package_name, current_version):
                upgraded_count += 1
            else:
                failed_upgrades.append(package_name)
    
    # Step 6: Final check for conflicts
    final_conflicts = check_dependencies()
    
    # Report results
    logging.info("=" * 50)
    logging.info("UPGRADE SUMMARY")
    logging.info("=" * 50)
    logging.info(f"Total packages upgraded: {upgraded_count}")
    logging.info(f"Failed upgrades: {len(failed_upgrades)}")
    if failed_upgrades:
        logging.info("Packages that failed to upgrade:")
        for pkg in failed_upgrades:
            logging.info(f"  - {pkg}")
    
    if final_conflicts:
        logging.warning("Remaining dependency conflicts:")
        for conflict in final_conflicts:
            logging.warning(f"  - {conflict}")
    else:
        logging.info("No dependency conflicts after upgrade!")
    
    return upgraded_count, failed_upgrades, final_conflicts

def main():
    parser = argparse.ArgumentParser(description='Check and resolve pip package dependencies and upgrade packages.')
    parser.add_argument('--dry-run', action='store_true', help='Only check for issues without making changes')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose output')
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    if args.dry_run:
        logging.info("Running in dry-run mode - no changes will be made")
        conflicts = check_dependencies()
        outdated = get_outdated_packages()
        
        if conflicts:
            logging.info("Current dependency conflicts:")
            for conflict in conflicts:
                logging.info(f"  - {conflict}")
        else:
            logging.info("No dependency conflicts found.")
        
        if outdated:
            logging.info(f"Found {len(outdated)} outdated packages:")
            for pkg in outdated:
                logging.info(f"  - {pkg['name']}: {pkg['version']} -> {pkg['latest_version']}")
        else:
            logging.info("All packages are up to date.")
    else:
        resolve_conflicts_and_upgrade()

if __name__ == "__main__":
    main()
