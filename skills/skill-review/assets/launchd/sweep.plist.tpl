<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>__LABEL__</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>__DREAMING_REPO_ROOT__/skills/skill-review/scripts/daemon-run.sh</string>
    <string>--prompt</string>
    <string>__DREAMING_REPO_ROOT__/skills/skill-review/references/sweep-prompt.txt</string>
    <string>--name</string>
    <string>skills-sweep</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>9</integer>
    <key>Minute</key><integer>15</integer>
  </dict>
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>__HOME__/.copilot/skill-state/daemon-logs/launchd-sweep.out</string>
  <key>StandardErrorPath</key>
  <string>__HOME__/.copilot/skill-state/daemon-logs/launchd-sweep.err</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>__HOME__</string>
    <key>COPILOT_HOME</key><string>__COPILOT_HOME__</string>
    <key>PATH</key><string>__HOME__/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>DREAMING_REPO_ROOT</key><string>__DREAMING_REPO_ROOT__</string>
    <key>DREAMING_SHARED_SKILLS_ROOT</key><string>__DREAMING_SHARED_SKILLS_ROOT__</string>
__SKILLS_REPO_ROOT_ENV__    <key>SKILLS_STATE_DIR</key><string>__SKILLS_STATE_DIR__</string>
    <key>SKILLS_LOCAL_ROOT</key><string>__SKILLS_LOCAL_ROOT__</string>
  </dict>
  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
