# WindowsでRDMのログイン状態を作る

WindowsのGoogle Chromeで手動ログインし、その状態をHAのRDM取得アプリへ渡す補助スクリプトです。Python 3.9以降とGoogle Chromeが必要です。Chromeの専用プロファイルとローカルのデバッグポート `127.0.0.1:9222` を使用します。

PowerShellでこのフォルダを開いて実行します。

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe chrome_probe.py
```

開いたChromeでRDMにログインし、アクティビティ一覧にCSVボタンが表示されたら、PowerShell側でEnterを押します。先頭1件のZIPとフォームCSVを検証し、`YYYYMMDDHHMM.zip` および `auth_state.json` をこのフォルダに作成します。ファイル名は画面の「出力対象」日時を使います。

`auth_state.json` を[アプリの設定手順](../../runmetrix_sync/DOCS.md)にある専用フォルダへコピーしてください。このファイルとChrome専用プロファイルはログイン情報なので公開しないでください。検証後は専用Chromeを閉じます。ZIPとCSVにも個人の運動データが含まれるため、GitHubへアップロードしないでください。

以前の試行で `rdm_probe.zip` が残っている場合は、同じChromeで対象のCSV出力画面を開いて `chrome_probe.py --finalize-existing` を実行できます。ZIP内の日時と画面が一致するか検証し、再ダウンロードせずに日時付きZIPへ変更します。
