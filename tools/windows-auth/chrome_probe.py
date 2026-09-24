"""Test RDM's visible CSV export using a manually signed-in Google Chrome.

This is a diagnostic, not a scheduled downloader. It uses only the RDM UI.
"""

from __future__ import annotations

import os
import asyncio
import hashlib
import re
import sys
import subprocess
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parent
PROFILE = Path(os.environ["LOCALAPPDATA"]) / "RunmetrixChromeProbe"
ENDPOINT = "http://127.0.0.1:9222"
RDM = "https://data-viewer.asics.com/"
REQUIRED = {"lap_form_data.csv", "lap_form_score.csv"}
CSV_NAME = re.compile(r"^(lap_form_data|lap_form_score)(?:-(\d{12}))?\.csv$", re.I)
SCREEN_DATE = re.compile(r"出力対象\s*[:：]\s*(20\d{2}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2})")


def timestamp_from_screen(text: str) -> str:
    match = SCREEN_DATE.search(text)
    if not match:
        raise RuntimeError("CSV出力画面の『出力対象』の日時を読めません")
    return datetime.strptime(match.group(1), "%Y/%m/%d %H:%M").strftime("%Y%m%d%H%M")


def verify_archive(archive: Path) -> Optional[str]:
    if not archive.is_file():
        raise RuntimeError(f"ZIPが見つかりません: {archive}")
    found = {}
    dates = set()
    with zipfile.ZipFile(archive) as zf:
        damaged = zf.testzip()
        if damaged:
            raise RuntimeError(f"ZIPが破損しています: {damaged}")
        members = [item for item in zf.infolist() if not item.is_dir()]
        print(f"ZIP内のファイル名: {[item.filename for item in members]}")
        for item in members:
            match = CSV_NAME.fullmatch(Path(item.filename).name)
            if match:
                canonical = match.group(1).lower() + ".csv"
                if canonical in found:
                    raise RuntimeError(f"同じ種類のCSVが複数あります: {canonical}")
                if item.file_size == 0:
                    raise RuntimeError(f"CSVが空です: {item.filename}")
                found[canonical] = item.filename
                if match.group(2):
                    dates.add(match.group(2))
    if not REQUIRED <= found.keys():
        raise RuntimeError(
            f"フォームCSVが不足しています: {sorted(REQUIRED - found.keys())}。"
            f" ZIPは削除せず残しました: {archive}"
        )
    print(f"確認済み: {found['lap_form_data.csv']}, {found['lap_form_score.csv']}")
    if len(dates) > 1:
        raise RuntimeError("ZIP内のCSVの日時が一致しません。ZIPは削除せず残しました")
    return dates.pop() if dates else None


def name_archive(archive: Path, timestamp: str) -> Path:
    named = archive.with_name(f"{timestamp}.zip")
    if named == archive:
        return named
    if named.exists():
        def digest(path: Path) -> str:
            result = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    result.update(chunk)
            return result.hexdigest()

        if digest(named) != digest(archive):
            raise RuntimeError(f"同名で内容の異なるZIPが既にあります: {named}")
        archive.unlink()
    else:
        archive.rename(named)
    print(f"日時付きZIP: {named}")
    return named


def chrome_path() -> Path:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ["LOCALAPPDATA"]) / "Google/Chrome/Application/chrome.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("Google Chromeが見つかりません。chrome.exeの場所を確認してください")


def endpoint_ready() -> bool:
    try:
        with urllib.request.urlopen(ENDPOINT + "/json/version", timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def start_chrome() -> None:
    if endpoint_ready():
        print("起動済みのChromeに再接続します。")
        return
    PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([
        str(chrome_path()),
        f"--user-data-dir={PROFILE}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=9222",
        "--no-first-run",
        RDM,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        if endpoint_ready():
            return
        time.sleep(0.25)
    raise RuntimeError("Chromeの接続口が起動しません。Chromeを閉じて再実行してください")


async def main() -> None:
    finalize_existing = sys.argv[1:] == ["--finalize-existing"]
    if sys.argv[1:] and not finalize_existing:
        raise RuntimeError("指定できる引数は --finalize-existing のみです")
    archive = ROOT / "rdm_probe.zip"
    if finalize_existing:
        if not archive.exists():
            candidates = sorted(ROOT.glob("????????????.zip"))
            if len(candidates) != 1:
                raise RuntimeError("対象ZIPを特定できません。rdm_probe.zipをこのフォルダに置いてください")
            archive = candidates[0]
        timestamp = verify_archive(archive)
    start_chrome()
    if not finalize_existing:
        print("開いた通常のChromeで、RDMへ手動ログインしてください。")
        print("活動一覧を表示し、各活動のCSVボタンが見えたら、この画面でEnterを押してください。")
        input()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(ENDPOINT, timeout=15_000)
        context = browser.contexts[0]
        pages = [page for page in context.pages if page.url.startswith(RDM)]
        if not pages:
            raise RuntimeError("RDMのタブがありません。Chromeで活動一覧を表示してください")
        page = pages[-1]
        if "/activities" not in page.url or "Unauthenticated" in await page.locator("body").inner_text():
            raise RuntimeError("RDMの活動一覧を確認できません。認証済み画面で再実行してください")

        screen_timestamp = None
        modal = page.get_by_text("CSV出力", exact=True)
        if finalize_existing and await modal.is_visible():
            screen_timestamp = timestamp_from_screen(await page.locator("body").inner_text())
            if timestamp and screen_timestamp != timestamp:
                raise RuntimeError(f"画面の出力対象 {screen_timestamp} とZIP内の日時 {timestamp} が一致しません")

        if not finalize_existing:
            buttons = page.get_by_role("button", name=re.compile(r"^CSV$", re.I))
            count = await buttons.count()
            if not count:
                raise RuntimeError("CSVボタンがありません。活動一覧を表示し直してください")
            if not await modal.is_visible():
                print(f"活動一覧にCSVボタンを{count}個確認。先頭の1件を開きます。")
                await buttons.first.click(timeout=15_000)
            else:
                print("開いているCSV出力画面を利用します。")
            await modal.wait_for(timeout=15_000)
            screen_timestamp = timestamp_from_screen(await page.locator("body").inner_text())
            for name in sorted(REQUIRED):
                await page.get_by_text(name, exact=True).wait_for(timeout=15_000)

            print("一括ダウンロードを実行します。")
            await page.locator('input[placeholder*="名前をつけてダウンロード"]').fill(screen_timestamp)
            async with page.expect_download(timeout=90_000) as pending:
                await page.get_by_text("一括", exact=True).click(timeout=15_000)
            download = await pending.value
            print(f"RDMが返したファイル名: {download.suggested_filename}")
            await download.save_as(archive)
            print(f"取得ファイルを保存しました: {archive}")
            timestamp = verify_archive(archive)
            if timestamp and timestamp != screen_timestamp:
                raise RuntimeError(f"画面の出力対象 {screen_timestamp} とZIP内の日時 {timestamp} が一致しません。ZIPは残しました")

        if not (screen_timestamp or timestamp):
            raise RuntimeError("ZIP名の日時を特定できません。CSV出力画面を開いて再実行してください")
        archive = name_archive(archive, screen_timestamp or timestamp)

        # Save only after a real download succeeds. Do not send this file to us.
        state = ROOT / "auth_state.json"
        await context.storage_state(path=str(state), indexed_db=True)
        print(f"成功: {archive.name} にフォームCSVを確認しました。")
        print(f"ログイン状態も {state.name} に保存しました（外部へ送らないでください）。")
        print("Chromeは手動で閉じてください。実行結果の成功／エラー文のみ共有してください。")
        await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"失敗: {type(exc).__name__}: {exc}")
        raise SystemExit(1) from None
