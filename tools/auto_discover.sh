#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/ytscan/yt-autoscanner"
VENV_PY="$PROJECT_ROOT/.venv/bin/python"
ENV_FILE="/home/ytscan/.env"

if [ ! -x "$VENV_PY" ]; then
  echo "[FATAL] Python venv not found at: $VENV_PY"
  exit 1
fi

if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
fi

cd "$PROJECT_ROOT/worker"

export YT_RANDOM_PICK="1"
export YT_RANDOM_REGION_POOL="US,GB,CA,AU,IN,JP,VN,KR,FR,DE,BR,MX,ID,TH,ES,IT,NL,SG,MY,PH,TW,HK,AR,CL,TR,PL,SA,AE,EG,NG,KE,RU,SE,NO,FI,DK,IE,PT,GR,IL,ZA"

# safer multi-line assignment
YT_RANDOM_QUERY_POOL=$(cat <<'EOF'
live:6, breaking news:5, world news:5, update:3, politics:3, president speech:2, economy:3, stock market:4, crypto:4, bitcoin:4, ethereum:3,
finance:3, investing:3, business:3, startup:3, ai:6, artificial intelligence:4, chatgpt:5, openai:4, gen ai:3, machine learning:3, tech review:5,
iphone:5, samsung:4, smartphone:4, camera:4, unboxing:5, gadgets:4, drone:3, gopro:3, pc build:3, linux:2, coding:4, programming:4, python:4,
javascript:3, sql:2, how to:5, tutorial:5, diy:4, life hacks:3, tips:3, tricks:3, education:4, science:4, math:3, physics:3, chemistry:2, nasa:3,
rocket launch:3, space:4, astronomy:3, documentary:3, history:3, geography:2, nature:3, animals:3, pets:3, cat:4, dog:4, wildlife:3, zoo:2,
gaming:6, esports:5, fortnite:5, minecraft:5, roblox:4, valorant:5, league of legends:5, genshin impact:4, pubg:3, mobile legends:3, fifa:4, nba:4,
soccer:5, football:5, cricket:4, boxing:3, ufc:3, mma:3, motorsport:3, formula 1:4, f1:4, racing:3, car review:3, gta 6:5, apex legends:3, cs2:3,
counter strike:3, dota 2:3, rust:2, ark:2, among us:3, speedrun:3, walkthrough:4, lets play:4, top 10:4, shorts:6, meme:4, memes:4, funny moments:3,
fail:3, prank:3, challenge:3, try not to laugh:2, reaction:4, compilation:4, music:5, mv:4, music video:4, cover:5, remix:4, lyrics:4, karaoke:3,
kpop:5, jpop:4, hip hop:4, rap:5, edm:4, lo-fi:3, lofi:3, classical music:2, jazz:2, podcast:4, interview:4, talk show:3, debate:2, stand up comedy:3,
daily vlog:3, travel vlog:4, travel:4, street food:4, cooking:5, recipe:5, mukbang:4, food review:4, restaurant:3, cafe:3, coffee:3, beauty:3, makeup:4,
skincare:4, hairstyle:3, fashion:4, ootd:3, haul:3, fitness:4, workout:4, gym:3, yoga:3, meditation:2, health:3, doctor:2, baby:3, parenting:2, kids:3,
cartoons:3, nursery rhymes:3, toys:3, movie trailer:5, official trailer:5, teaser:4, netflix:3, marvel:3, dc:2, anime:5, vtuber:5, cosplay:3, manga:3,
fan animation:3, study with me:3, pomodoro:2, productivity:3, motivation:3, self improvement:3, education tips:3, career advice:2
EOF
)
export YT_RANDOM_QUERY_POOL

DURATIONS=("short" "medium" "long" "any")
export YT_MAX_PAGES="5"

while true; do
  export YT_DURATION_MODE="${DURATIONS[$RANDOM % ${#DURATIONS[@]}]}"
  echo "[AutoDiscover] $(date) starting discover_once.py"
  echo "[AutoDiscover] YT_DURATION_MODE=$YT_DURATION_MODE"

  if ! "$VENV_PY" discover_once.py; then
    echo "[AutoDiscover] discover_once.py exited with non-zero code"
  fi

  echo "[AutoDiscover] sleeping 30s"
  sleep 30
done
