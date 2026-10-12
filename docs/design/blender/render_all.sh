#!/bin/zsh
# Renders Zero's four example animations and turns each into an mp4.
cd "$(dirname "$0")"
for mode in ${MODES:-idle happy talk}; do
  start=$(date +%s)
  want=$(/usr/bin/python3 -c "print({'idle':96,'happy':60,'talk':72,'walkup':90}['$mode'])")
  for attempt in 1 2 3 4; do   # Blender can crash mid-render; each attempt resumes from the frames on disk
    [ $(ls /tmp/zero-$mode 2>/dev/null | wc -l) -ge $want ] && break
    /Applications/Blender.app/Contents/MacOS/Blender -b -P zero_spike.py -- /tmp/zero-$mode $mode >> /tmp/zero-$mode.log 2>&1
  done
  ffmpeg -loglevel error -y -framerate 24 -i /tmp/zero-$mode/f_%04d.png -c:v libx264 -pix_fmt yuv420p -crf 16 renders/zero-$mode.mp4
  echo "$mode done in $(( $(date +%s) - start ))s"
done
