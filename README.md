# Runmetrix Home Assistant Sync

RunmetrixのRunning Data Manager（RDM）画面からCSVをHome Assistant OSへ保存し、必要に応じてGoogle Driveへコピーする非公式のアプリリポジトリです。ASICSおよびHome Assistantの公式製品ではありません。RDMの画面構成が変わると取得できなくなることがあります。

| アプリ | 役割 | 主な保存先 |
| --- | --- | --- |
| Runmetrix RDM Sync | ログイン済みのブラウザー状態を使い、RDM画面のCSV出力を定期取得 | `/share/runmetrix/YYYYMMDD_HHMM/` |
| Runmetrix Drive Sync | HA内の保存データをrcloneでGoogle Driveへコピー | `Runmetrix_Auto` |

RDM Syncだけでも利用できます。Drive Syncにはご自身のGoogle OAuthクライアントIDとrclone設定が必要です。どちらも起動直後に処理し、その後は構成画面の `interval_hours` ごとに実行します。失敗時はRDM側が5分後、Drive側も5分後に再試行します。対象環境はRaspberry Pi 5の64-bit Home Assistant OS（aarch64）です。ほかの環境は未検証です。

## インストール

1. Home Assistantの **設定 → アプリ → アプリストア → 右上の︙ → リポジトリ** に `https://github.com/GSX-R125/runmetrix-ha-sync` を追加します。
2. `Runmetrix RDM Sync` をインストールし、[Windowsで初回ログイン状態を作成](tools/windows-auth/README.md)してから[アプリ設定手順](runmetrix_sync/DOCS.md)に従います。
3. Google Driveへ送る場合は `Runmetrix Drive Sync` をインストールし、[Drive設定手順](runmetrix_drive_sync/DOCS.md)に従います。
4. 手動実行には[ダッシュボードボタン](dashboard/README.md)を設定します。

**すでにローカルアプリとして導入した方へ:** このGitHubリポジトリから導入するとアプリIDの接頭辞が `local_` から別の値に変わり、設定領域も別になります。既存の `/share/runmetrix` は共通ですが、`auth_state.json` と `rclone.conf` は新しいアプリの設定領域へコピーしてください。移行をしない場合は、稼働中のローカルアプリをそのまま使用できます。同じアプリを二重起動すると取得・転送が重なるため、移行時は旧アプリを停止します。

## 保存データと認証情報

RDM SyncはZIP、フォームCSV、任意の心拍CSVと `manifest.json` をHAの `/share/runmetrix` に保存します。Drive SyncはこのディレクトリをDriveへコピーします。Drive上の既存ファイルを削除する `sync` は使わず、`copy` を使います。

`auth_state.json` はRDMのログイン情報、`rclone.conf` はGoogleの認証情報を含みます。**どちらもGitHubに公開せず、各アプリ専用の `/addon_configs/<アプリID>/` にのみ置いてください。** このリポジトリには実際の認証情報やランニングデータを含めていません。

## 制約

- RDM画面の最初のページに表示される活動を最大 `max_activities` 件まで確認します。ページ送りは未実装です。
- RDMの認証状態には手動の初回ログインが必要です。RDMが一時的に認証エラーを返す場合もあるため、1回の失敗だけで認証の失効とは判断しません。
- Google Driveへの転送はRDM取得の完了を待って自動連動する方式ではありません。すぐ転送したい場合は取得完了後にDrive側を手動実行します。
- このアプリを使用する際は、RDMとGoogleの利用条件を確認してください。

## 開発者向け

`python3 -m unittest discover -s tests -v` でZIP内CSVの検証テストを実行できます。Drive側のシェル構文は `sh -n runmetrix_drive_sync/run.sh` で確認できます。実機のRDMとDriveへの接続は各自の認証が必要です。

MIT License。問題の報告にはログのエラー文のみを添え、認証ファイルやCSVは添付しないでください。
