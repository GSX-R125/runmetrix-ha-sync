# Runmetrix RDM Sync

RDMのアクティビティ一覧から、表示中の未保存の活動を取得します。画面の「一括」CSV出力で得たZIPを `YYYYMMDDHHMM.zip` として保存し、フォームCSVを `lap_form_data.csv`、`lap_form_score.csv` に統一して展開します。RDMに心拍CSVがあればそれも展開します。

## 初回設定

1. [Windows用ログイン補助](../tools/windows-auth/README.md)を使い、`auth_state.json` を作成します。
2. インストール後、アプリの情報画面に表示されるIDを確認します。ファイルを `\\homeassistant\addon_configs\<アプリID>\auth_state.json` にコピーします。SMB共有が `app_configs` と表示される環境では、その共有を使います。ローカルインストール時のIDは `local_runmetrix_sync`、GitHubリポジトリからのインストール時は異なる接頭辞になります。
3. 構成画面で `interval_hours`（1～24、初期値6）と `max_activities`（1～100、初期値10）を設定し、アプリを起動します。構成を変更したらアプリを再起動してください。
4. ログに `OK YYYYMMDD_HHMM` または既存分の `SKIP YYYYMMDD_HHMM` が出ることを確認します。保存先は `\\homeassistant\share\runmetrix\` です。

最初のページに表示された件数を上限として処理するため、`max_activities` を増やしてもRDMの次ページは取得しません。未保存の活動が最初のページから消える前に同期してください。

## 認証・通信の失敗

`Unauthenticated` や通信エラーは5分後に再試行します。繰り返し失敗する場合はWindowsでRDMのログインを確認し、必要なら `auth_state.json` を作り直して同じ場所へコピーし、アプリを再起動します。保存済みデータは削除されません。ログやIssueに `auth_state.json` の中身を載せないでください。
