# Runmetrix Home Assistant Sync

RunmetrixのRunning Data Manager（RDM）からフォームCSVをHome Assistantへ保存し、Google Driveへ年月別にバックアップする非公式アプリです。ASICS・Home Assistantの公式製品ではありません。

| アプリ | 最新版 | 役割 |
| --- | --- | --- |
| Runmetrix RDM Sync | 0.3.6 | OneASICSログイン、元ZIP・日時付きフォームCSVの定期取得 |
| Runmetrix Drive Sync | 0.2.5 | 年月別転送、バックアップ照合、HA側の最新10活動保持 |

対象はRaspberry Pi 5 / 64-bit Home Assistant OS（aarch64）です。[変更履歴](CHANGELOG.md)と[旧版からの移行手順](MIGRATION.md)を用意しています。**旧Drive版からの更新では `root_folder_id` の設定が必要です。HA側の古いデータを自動整理する機能も追加されています。**

## 保存構成

例の活動日時は説明用です。

```text
Home Assistant: /share/runmetrix/
  manifest.json
  YYYYMMDD_HHMM/
    YYYYMMDDHHMM.zip
    lap_form_data-YYYYMMDDHHMM.csv
    lap_form_score-YYYYMMDDHHMM.csv

Google Drive: Runmetrix_Auto/
  YYYY/
    MM/
      YYYYMMDD_HHMM/
        YYYYMMDDHHMM.zip
        lap_form_data-YYYYMMDDHHMM.csv
        lap_form_score-YYYYMMDDHHMM.csv
```

CSV名はZIP内の元の名前を保持します。年・月は実行日ではなく活動日時から決め、必要なフォルダーを自動作成します。新しい年月別転送は活動フォルダー内のファイルが対象です。旧版のDrive直下データは自動移動しません。

## インストール

1. HAの **設定 → アプリ → アプリストア → リポジトリ** に次を追加します。
   `https://github.com/GSX-R125/runmetrix-ha-sync`
2. RDM Syncをインストールし、[RDM設定手順](runmetrix_sync/DOCS.md)に従ってOneASICS認証情報またはログイン済み状態を配置します。
3. Driveを使う場合はDrive Syncをインストールし、[Drive設定手順](runmetrix_drive_sync/DOCS.md)に従ってrclone認証と保存先フォルダーIDを設定します。
4. 必要に応じて[手動同期ボタン](dashboard/README.md)を配置します。

両アプリは起動直後に実行し、処理後に指定時間待機します。構成を変更したら保存・再起動してください。

## 通常処理とクリーン処理

| `clean_run` | RDM Sync | Drive Sync |
| --- | --- | --- |
| `false`（初期値） | 取得履歴にある活動をSKIP | 転送履歴にある活動をSKIP |
| `true` | 履歴とローカル保存の有無を問わず、対象活動をサイトから再取得して置換 | ローカルの全活動フォルダーを再転送し、同名のDriveファイルを上書き |

**ONの間は定期実行・再起動でも繰り返します。検証後は両方ともOFFへ戻して保存・再起動してください。** RDMの対象は最初のページと `max_activities` の範囲です。全期間の一括取得ではありません。Driveにしか残っていないデータをサイトから復元する機能はありません。

## 最新10活動の保持

Drive Syncは通常 `local_keep: 10` とし、実際のローカル活動フォルダーを日時順に数えます。古い活動は、Driveからダウンロードした全対象ファイルの内容とローカルの一致を確認後にHA側だけ削除します。内容不一致・通信失敗・バックアップ未確認の場合は追加保持し、理由をログに出します。

旧CSV名と同じ活動の日時付きCSV名は、内容一致を確認して対応付けます。名前やサイズが同じだけで削除しません。履歴外の実フォルダーも集計に含めます。両アプリは共有ロックで取得・転送・削除の競合を防ぎます。

## 履歴と認証情報

| ファイル | 置き場所 | 用途 |
| --- | --- | --- |
| `manifest.json` | `/share/runmetrix/` | RDMの取得履歴。古いCSV整理後も保持 |
| `yearmonth_upload_state.json` | Drive Syncの `/config/` | 転送履歴・HA側整理の記録 |
| `yearmonth_upload_index.json` | Drive Syncの `/config/` とDrive保存先直下 | 転送済み活動の補助一覧 |
| `auth_state.json` | RDM Syncの `/config/` | ログイン状態 |
| `rdm_credentials.json` | RDM Syncの `/config/` | OneASICSのメールアドレス・パスワード（任意） |
| `rclone.conf` | Drive Syncの `/config/` | Googleの接続設定・トークン |

画面のログを読み返してスキップ判定するわけではありません。通常処理では履歴を使用するため、手動削除したDriveデータは自動復元されません。HA側に元データがあれば、Driveの `clean_run` で再転送できます。

認証ファイル・活動データ・個人用保存先IDはこのリポジトリに含めません。Issueに認証情報やCSVを添付しないでください。

## 制約と確認範囲

- RDM画面の最初のページのみ。次ページ・全期間取得は未対応です。
- RDMのフォームdata・score CSVが両方必要です。欠落ZIPはエラーとして旧データを維持します。Windows全期間取得ツールの部分取得仕様とは異なります。
- 追加認証・画面変更・アクセス拒否は自動解決できません。RDM失敗時は設定間隔、Driveの転送エラー時は5分後に再試行します。
- Driveの既存の別名ファイル・別活動は削除しません。`rclone sync` は使いません。
- 各アプリは独立した周期で動きます。共有ロックは処理の重複を防ぎますが、RDM終了からDrive実行を自動で開始する仕組みではありません。
- ローカル版RDM 0.3.6 / Drive 0.2.4では、実機で10活動の再取得、12活動の転送、古い2活動の照合後削除、通常モードへの復帰を確認済みです。公開用Drive 0.2.5の保存先設定・初回動作の一般化はローカルテストで確認しており、公開版を新規導入した環境での実接続は未検証です。

## 開発・検証

```bash
python3 -m unittest discover -s tests -v
sh -n runmetrix_drive_sync/run.sh
```

テストはサイトやDriveの実アカウントを使いません。Linuxのファイルロックを使うため、テストもLinux環境で実行してください。

MIT License。
