#!/usr/bin/env bash

FILE_DIRS=(
	"/Applications/Topaz Video AI.app"
	"/Library/OFX/Plugins/Topaz Video AI.ofx.bundle"
	"/Library/OFX/Plugins/Topaz Video AIframeinterpolation.ofx.bundle"
)

LICENSE_DIR="/Applications/Topaz Video AI.app/Contents/Resources/models"

hex() {
	echo "$1" | perl -0777pe 's|([0-9a-zA-Z]{2}+(?![^\(]*\)))|\\x${1}|gs'
}

replace() {
	local dom=$(hex "$2")
	local sub=$(hex "$3")
	local pattern_found_count=$(perl -0777 -ne 'while(/'"$dom"'/gs){$count++} END {print $count}' "$1")
	if [[ $pattern_found_count -eq 1 ]]; then
		sudo perl -0777pi -e 's|'"$dom"'|'"$sub"'|gs' "$1"
		echo "Patched: $4"
		((patched += 1))
	else
		local patched_found_count=$(perl -0777 -ne 'while(/'"$sub"'/gs){$count++} END {print $count}' "$1")
		if [[ $patched_found_count -eq 1 ]]; then
			echo "Already patched $4."
		else
			echo "Failed to patch $4."
		fi
	fi
}

prep() {
	sudo xattr -d com.apple.quarantine "$1"
	if sudo codesign --force --deep --sign - "$1" 2>/dev/null; then
		echo "$(basename "$1") codesigned successfully."
	else
		echo "$(basename "$1") failed."
	fi
}

patch() {
	echo "Patching: $APP_PATH"
	replace "$1" '020080D24EF0FF97E00B00B908000071' '020080D200008052E00B00B908000071' "ARM"
	replace "$1" '488B75E88B4DCCE8B4C1FFFF8945C883' '488B75E88B4DCCB8000000008945C883' "Intel"
}

create_license() {
	license_content=$(
		cat <<-EOF
			LICENSE topazlabs tvai_floating 99999999 permanent uncounted hostid=ANY customer=User_
		EOF
	)
	sudo tee "$LICENSE_DIR/license.lic" >/dev/null <<<"$license_content"
	echo "License created."
}

for dir in /Applications/Adobe\ After\ Effects\ */Plug-ins; do
	if [[ -d "$dir" ]]; then
		FILE_DIRS+=("$dir/Topaz Video AI.plugin")
		FILE_DIRS+=("$dir/Topaz Video AI Frame Interpolation.plugin")
	fi
done

for directory in "${FILE_DIRS[@]}"; do
	APP_PATH="${1:-$directory}"
	RLM_FILE="$APP_PATH/Contents/Frameworks/libtopaz_rlm.dylib"
	if [[ -f "$RLM_FILE" ]]; then
		patched=0
		patch "$RLM_FILE"
		if [[ $patched -eq 2 ]]; then
			prep "$APP_PATH"
		fi
	else
		echo "Skipping, not found: $directory"
	fi
done

if [[ ! -f "$LICENSE_DIR/license.lic" ]]; then
	create_license
fi
