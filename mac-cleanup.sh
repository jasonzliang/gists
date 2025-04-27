#!/usr/bin/env bash

# Optimized Mac Cleanup Script - faster execution, no browser cache cleaning
# inspired by various Mac cleanup scripts

bytesToHuman() {
    b=${1:-0}; d=''; s=0; S=(Bytes {K,M,G,T,E,P,Y,Z}iB)
    while ((b > 1024)); do
        d="$(printf ".%02d" $((b % 1024 * 100 / 1024)))"
        b=$((b / 1024))
        let s++
    done
    echo "more than $b$d ${S[$s]} of space was cleaned up :3"
}

# Check if Time Machine is running
if [ `tmutil status | grep -c "Running = 1"` -ne 0 ]; then
	echo "Time Machine is currently running. Let it finish first!"
	exit
fi

# Check for last Time Machine backup and exit if it's longer than 1 hour ago
lastBackupDateString=`tmutil latestbackup | grep -E -o "[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{6}"`
if [ "$lastBackupDateString" == "" ]; then
	read -n 1 -p "$(tput setaf 3)Last Time Machine backup cannot be found. Proceed anyway?$(tput sgr0) (y/n) " RESP
    echo ""
	if [ "$RESP" != "y" ]; then
		exit
	fi
else
	lastBackupDate=`date -j -f "%Y-%m-%d-%H%M%S" $lastBackupDateString "+%s"`
	if [ $((`date +%s` - $lastBackupDate)) -gt 3600 ]
	then
		printf "Time Machine has not backed up since `date -j -f %s $lastBackupDate` (more than 60 minutes)!"
		exit 1003
	else
		echo "Last Time Machine backup was on `date -j -f %s $lastBackupDate`. "
	fi
fi

# Ask for the administrator password upfront
if [ "$EUID" -ne 0  ]; then
	echo "Please run as root"
	exit
fi

oldAvailable=$(df / | tail -1 | awk '{print $4}')

echo 'Empty the Trash on all mounted volumes and the main HDD...'
sudo rm -rfv /Volumes/*/.Trashes &>/dev/null
sudo rm -rfv ~/.Trash &>/dev/null

echo 'Clean temporary files...'
sudo -S rm -rfv /tmp/*
sudo -S rm -rfv /private/var/tmp/Processing/
sudo -S rm -rfv /private/var/tmp/Xcode/
sudo -S rm -rfv /private/var/tmp/tmp*

echo 'Clear Mail Downloads...'
sudo rm -rfv ~/Library/Containers/com.apple.mail/Data/Library/Mail\ Downloads/* &>/dev/null

echo 'Clear System Log Files...'
sudo -S rm -rfv /private/var/log/* &>/dev/null
sudo -S rm -rfv /Library/Logs/* &>/dev/null
sudo -S rm -rfv ~/Library/Logs/* &>/dev/null
sudo -S rm -rfv ~/Library/Application\ Support/Adobe/Common/Media\ Cache\ Files/* &>/dev/null
sudo rm -rfv /private/var/log/asl/*.asl &>/dev/null
rm -rfv ~/Library/Containers/com.apple.mail/Data/Library/Logs/Mail/* &>/dev/null
rm -rfv ~/Library/Logs/CoreSimulator/* &>/dev/null

# echo 'Clean .DS_Store files...'
# sudo -S find ~/Desktop ~/Documents ~/Downloads -name ".DS_Store" -delete 2>/dev/null

echo 'Vacuum Mail Envelope Index...'
sqlite3 ~/Library/Mail/V4/MailData/Envelope\ Index vacuum &>/dev/null

echo 'Clean System Caches...'
sudo -S rm -rf /Library/Caches/*
sudo -S rm -rf ~/Library/Caches/*

echo 'Clean Application Caches...'
for x in $(ls ~/Library/Containers/)
do
	rm -rfv ~/Library/Containers/$x/Data/Library/Caches/*
done

echo 'Clear Adobe Cache Files...'
sudo rm -rfv ~/Library/Application\ Support/Adobe/Common/Media\ Cache\ Files/* &>/dev/null

echo 'Cleanup iOS Applications...'
rm -rfv ~/Music/iTunes/iTunes\ Media/Mobile\ Applications/* &>/dev/null

echo 'Remove iOS Device Backups...'
rm -rfv ~/Library/Application\ Support/MobileSync/Backup/* &>/dev/null

echo 'Cleanup XCode Derived Data and Archives...'
rm -rfv ~/Library/Developer/Xcode/DerivedData/* &>/dev/null
rm -rfv ~/Library/Developer/Xcode/Archives/* &>/dev/null
rm -rfv ~/Library/Developer/Xcode/iOS\ Device\ Logs/* &>/dev/null
rm -rfv ~/Library/Developer/CoreSimulator/Caches/* &>/dev/null

echo 'Cleanup Homebrew Cache...'
brew cleanup --prune=all &>/dev/null
rm -rfv /Library/Caches/Homebrew/* &>/dev/null
brew autoremove &>/dev/null

echo 'Purge CoreSimulator...'
xcrun simctl delete unavailable

echo 'Cleanup any old versions of gems...'
gem cleanup &>/dev/null

echo 'Remove dangling docker containers...'
docker rm $(docker ps -q -f status=exited) 2>/dev/null
docker rmi $(docker images -f "dangling=true" -q) 2>/dev/null
docker volume rm $(docker volume ls -qf dangling=true) 2>/dev/null

# echo 'Clean Python cache files...'
# find ~ -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
# find ~ -name "*.pyc" -delete 2>/dev/null
# find ~ -name "*.pyo" -delete 2>/dev/null
# rm -rfv ~/.cache/pip/* &>/dev/null

echo 'Clean npm cache...'
npm cache clean --force &>/dev/null
rm -rfv ~/.npm/_cacache/* &>/dev/null

echo 'Clean yarn cache...'
yarn cache clean &>/dev/null

echo 'Clean CocoaPods cache...'
pod cache clean --all &>/dev/null

echo 'Clean Slack cache...'
rm -rfv ~/Library/Application\ Support/Slack/Cache/* &>/dev/null
rm -rfv ~/Library/Application\ Support/Slack/Service\ Worker/CacheStorage/* &>/dev/null

echo 'Clean Visual Studio Code cache...'
rm -rfv ~/Library/Application\ Support/Code/Cache/* &>/dev/null
rm -rfv ~/Library/Application\ Support/Code/CachedData/* &>/dev/null
rm -rfv ~/Library/Application\ Support/Code/CachedExtensions/* &>/dev/null
rm -rfv ~/Library/Application\ Support/Code/CachedExtensionVSIXs/* &>/dev/null

echo 'Clean JetBrains IDE cache...'
rm -rfv ~/Library/Caches/JetBrains/* &>/dev/null
rm -rfv ~/Library/Caches/IntelliJIdea* &>/dev/null
rm -rfv ~/Library/Caches/PyCharm* &>/dev/null
rm -rfv ~/Library/Caches/WebStorm* &>/dev/null

# echo 'Clean Jupyter notebook checkpoints...'
# find ~ -type d -name ".ipynb_checkpoints" -exec rm -rf {} + 2>/dev/null

echo 'Clean Conda cache...'
conda clean --all --yes &>/dev/null

echo 'Clean Ruby cache...'
rm -rfv ~/.bundle/cache/* &>/dev/null

echo 'Clean Go cache...'
go clean -cache &>/dev/null

echo 'Clean Gradle cache...'
rm -rfv ~/.gradle/caches/* &>/dev/null

echo 'Clean Maven cache...'
rm -rfv ~/.m2/repository/* &>/dev/null

echo 'Clean Steam download cache...'
rm -rfv ~/Library/Application\ Support/Steam/steamapps/downloading/* &>/dev/null

echo 'Clean Spotify cache...'
rm -rfv ~/Library/Caches/com.spotify.client/Data/* &>/dev/null

echo 'Clean Quick Look cache...'
qlmanage -r cache &>/dev/null

echo 'Clean Thumbnail cache...'
rm -rfv ~/Library/Thumbnails/* &>/dev/null

echo 'Clean Font cache...'
sudo atsutil databases -remove &>/dev/null

echo 'Clean DNS cache...'
sudo dscacheutil -flushcache &>/dev/null
sudo killall -HUP mDNSResponder &>/dev/null

echo 'Clean Speech Recognition cache...'
rm -rfv ~/Library/Caches/com.apple.SpeechRecognitionCore/* &>/dev/null

echo 'Clean CUPS printing system cache...'
sudo rm -rfv /var/spool/cups/cache/* &>/dev/null

echo 'Clean old iOS backups (older than 180 days)...'
find ~/Library/Application\ Support/MobileSync/Backup/* -type d -mtime +180 -exec rm -rf {} + 2>/dev/null

echo 'Clean old Software Updates...'
sudo softwareupdate --clear-catalog &>/dev/null

echo 'Clean old iPhone Software Updates...'
rm -rfv ~/Library/iTunes/iPhone\ Software\ Updates/* &>/dev/null

echo 'Clean old iOS device logs...'
rm -rfv ~/Library/Logs/CrashReporter/MobileDevice/* &>/dev/null

echo 'Clean Telegram cache...'
rm -rfv ~/Library/Group\ Containers/*.Telegram/Telegram/Telegram\ Data/cache/* &>/dev/null

echo 'Clean Discord cache...'
rm -rfv ~/Library/Application\ Support/discord/Cache/* &>/dev/null
rm -rfv ~/Library/Application\ Support/discord/Code\ Cache/* &>/dev/null

echo 'Clean Microsoft Teams cache...'
rm -rfv ~/Library/Application\ Support/Microsoft/Teams/Cache/* &>/dev/null
rm -rfv ~/Library/Application\ Support/Microsoft/Teams/Service\ Worker/CacheStorage/* &>/dev/null

echo 'Clean Zoom cache...'
rm -rfv ~/Library/Application\ Support/zoom.us/AutoDownload/* &>/dev/null

echo 'Clean Java cache...'
rm -rfv ~/Library/Caches/Java/* &>/dev/null

echo 'Clean old kernel extensions cache...'
sudo kextcache --clear-staging &>/dev/null

echo 'Empty Safari download history...'
rm -rfv ~/Library/Safari/Downloads.plist &>/dev/null

echo 'Purge inactive memory...'
sudo purge

clear && echo 'Success!'

newAvailable=$(df / | tail -1 | awk '{print $4}')
count=$((newAvailable-oldAvailable))
count=$(( $count * 512))
bytesToHuman $count

echo 'All cleanup operations completed!'