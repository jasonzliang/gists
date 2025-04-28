#!/bin/sh -e

# Get absolute path to script directory, works with any way of invoking the script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -d /Applications/CrossOver.app ]; then
  echo "Please install CrossOver!"
  exit 1
fi

# Fix license
cd ~/Library/Preferences/
openssl genrsa -out key.pem 2048
openssl rsa -in key.pem -outform PEM -pubout -out public.pem
sudo mv public.pem /Applications/CrossOver.app/Contents/SharedSupport/CrossOver/share/crossover/data/tie.pub
sudo rm -f com.codeweavers.CrossOver.license com.codeweavers.CrossOver.sha256
printf "[crossmac]\ncustomer=user\nemail=user@apple.com\nexpires=2030/01/01\n[license]\nid=a4xdUZD2bWB00tQI" > com.codeweavers.CrossOver.license
openssl dgst -sha256 -sign key.pem -out com.codeweavers.CrossOver.sha256 com.codeweavers.CrossOver.license
rm key.pem

# Fix updating DB - use script directory
sudo cp "${SCRIPT_DIR}/libcxsetup-v3.py" /Applications/CrossOver.app/Contents/Resources
# Resign to avoid corruption
sudo codesign -fs - /Applications/CrossOver.app
