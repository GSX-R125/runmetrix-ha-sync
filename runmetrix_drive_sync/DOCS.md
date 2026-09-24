# Runmetrix Drive Sync

`/share/runmetrix` をGoogle Driveの `Runmetrix_Auto` に `rclone copy` で転送します。Drive上の余分なファイルは削除しません。同期に成功すると構成画面の `interval_hours`（1～24、初期値6）後に再実行し、失敗すると5分後に再試行します。

## Google Drive接続の準備

1. [rclone公式手順](https://rclone.org/drive/#making-your-own-client-id)でGoogle Drive APIを有効にし、ご自身のDesktop app型OAuth `client_id` と `client_secret` を作成します。rcloneの共通IDは2026年中に廃止予定です。
2. Windowsでrcloneをインストールして `rclone config` を実行します。接続名は **`runmetrix_drive`**、ストレージは `drive`、権限は `drive.file` を選び、作成したIDとシークレットでブラウザー認証します。継続利用時はOAuth同意画面の公開状態にも注意してください。
3. `rclone config file` で `rclone.conf` の場所を確認します。Home Assistantのアプリ情報画面に表示されるIDに合わせ、`\\homeassistant\addon_configs\<アプリID>\rclone.conf` へコピーします。`app_configs` と表示される環境では、その共有を使います。ローカルインストール時は `local_runmetrix_drive_sync`、GitHubリポジトリからの場合は接頭辞が変わります。
4. アプリを起動し、ログに「Google Driveへのコピーが完了しました」と出ることを確認します。構成を変更したらアプリを再起動してください。

`drive.file` 権限では、このOAuthアプリが作成していない既存フォルダにはアクセスできません。初回転送で作る `Runmetrix_Auto` フォルダを使ってください。既存フォルダへの保存には別途権限と保存先の調整が必要です。

`rclone.conf` にはトークンが含まれます。GitHub、Issue、Driveの共有フォルダに置かないでください。Driveへのコピーが成功しない場合は、認証情報を伏せたログのエラー文のみ共有してください。
