#!/usr/bin/env bash

# inspired by
# https://gist.github.com/JannikArndt/feb720c1f5d210b4820b880af23f2a07
# which was inspired by
# https://github.com/fwartner/mac-cleanup/blob/master/cleanup.sh
# https://gist.github.com/jamesrampton/4503412
# https://github.com/mengfeng/clean-my-mac/blob/master/clean_my_mac.sh
# https://github.com/szymonkaliski/Dotfiles/blob/master/Scripts/clean-my-mac
# http://brettterpstra.com/2015/10/27/vacuuming-mail-dot-app-on-el-capitan/ / https://github.com/pbihq/tools/blob/master/MailDBOptimiser.sh

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

echo 'Clean .DS_Store files in ~...'
sudo -S find ~ / -name ".DS_Store" -exec rm {} &>/dev/null

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

echo 'Cleanup Homebrew Cache...'
brew cleanup --force -s &>/dev/null
brew cask cleanup &>/dev/null
rm -rfv /Library/Caches/Homebrew/* &>/dev/null
brew tap --repair &>/dev/null

echo 'Purge CoreSimulator...'
xcrun simctl delete unavailable

echo 'Cleanup any old versions of gems...'
gem cleanup &>/dev/null

echo 'Remove dangling docker containers...'
docker rm $(docker ps -q -f status=exited)

echo 'Purge inactive memory...'
sudo purge

echo 'Rebuild Spotlight...'
sudo mdutil -E /

clear && echo 'Success!'

newAvailable=$(df / | tail -1 | awk '{print $4}')
count=$((newAvailable-oldAvailable))
count=$(( $count * 512))
bytesToHuman $count
