#!/bin/sh
set -u

config=/config/rclone.conf
source_dir=/share/runmetrix
destination=runmetrix_drive:Runmetrix_Auto
options=/data/options.json

if [ ! -r "$options" ]; then
  echo "設定エラー: /data/options.json を読めません。"
  exit 1
fi

interval_hours=$(sed -n 's/.*"interval_hours"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$options")
case "$interval_hours" in
  ''|*[!0-9]*)
    echo "設定エラー: interval_hours は1～24の整数にしてください。"
    exit 1
    ;;
esac
if [ "$interval_hours" -lt 1 ] || [ "$interval_hours" -gt 24 ]; then
  echo "設定エラー: interval_hours は1～24の整数にしてください。"
  exit 1
fi
interval_seconds=$((interval_hours * 3600))

echo "Google Drive同期間隔: ${interval_hours}時間"

while :; do
  if [ ! -f "$config" ]; then
    echo "Google Drive設定待ち: app_config/rclone.conf を配置してください。"
    sleep 60
    continue
  fi

  if [ ! -d "$source_dir" ]; then
    echo "取得データ待ち: /share/runmetrix がありません。"
    sleep 60
    continue
  fi

  if ! rclone listremotes --config "$config" | grep -qx 'runmetrix_drive:'; then
    echo "設定エラー: rclone.conf に runmetrix_drive という接続名がありません。"
    sleep 60
    continue
  fi

  echo "RunmetrixをGoogle Driveへコピーします。"
  if rclone copy "$source_dir" "$destination" \
    --config "$config" \
    --checkers 4 --transfers 2 --retries 3 \
    --stats 30s --stats-one-line --log-level INFO; then
    echo "Google Driveへのコピーが完了しました。次回は${interval_hours}時間後です。"
    sleep "$interval_seconds"
  else
    echo "コピーに失敗しました。5分後に再試行します。"
    sleep 300
  fi
done
