"""Home Assistant app: scheduled visible-UI CSV downloads from RDM."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

RDM_URL = "https://data-viewer.asics.com/activities"
AUTH = Path("/config/auth_state.json")
ROOT = Path("/share/runmetrix")
OPTIONS = Path("/data/options.json")
DATE_RE = re.compile(r"20\d{2}[/.-]\d{1,2}[/.-]\d{1,2}\s+\d{1,2}:\d{2}")
REQUIRED = {"lap_form_data.csv", "lap_form_score.csv"}
KNOWN = REQUIRED | {"lap_heart_rate_data.csv"}
CSV_NAME = re.compile(r"^(lap_form_data|lap_form_score|lap_heart_rate_data)(?:-(\d{12}))?\.csv$", re.I)


class SyncError(Exception):
    pass


def key_from_date(value: str) -> str:
    return datetime.strptime(re.sub(r"[.-]", "/", value), "%Y/%m/%d %H:%M").strftime("%Y%m%d_%H%M")


def read_manifest() -> dict:
    path = ROOT / "manifest.json"
    if not path.exists():
        return {"schema": 1, "activities": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != 1 or not isinstance(value.get("activities"), dict):
        raise SyncError("manifest.jsonの形式が不正です")
    return value


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def extract_csv(archive_path: Path, dest: Path) -> dict[str, str]:
    """Only extract recognized CSV names, checking content before writing."""
    with zipfile.ZipFile(archive_path) as archive:
        members = {}
        for item in archive.infolist():
            if item.is_dir():
                continue
            match = CSV_NAME.fullmatch(Path(item.filename).name)
            if match:
                canonical = match.group(1).lower() + ".csv"
                if canonical in members:
                    raise SyncError(f"同じ種類のCSVが複数あります: {canonical}")
                members[canonical] = item
        if not REQUIRED.issubset(members):
            raise SyncError(f"ZIP内の必須CSVが不足: {sorted(REQUIRED - set(members))}")
        values = {}
        for name in KNOWN & members.keys():
            item = members[name]
            if item.file_size > 100_000_000:
                raise SyncError(f"CSVサイズが大きすぎます: {name}")
            data = archive.read(item)
            if not data or data.lstrip().lower().startswith((b"<html", b"<!doctype")):
                raise SyncError(f"CSV内容が不正: {name}")
            for encoding in ("utf-8-sig", "cp932"):
                try:
                    decoded = data.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise SyncError(f"CSV文字コードを読めません: {name}")
            if not next(csv.reader(decoded.splitlines()), []):
                raise SyncError(f"CSVヘッダーがありません: {name}")
            values[name] = data
    dest.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, data in sorted(values.items()):
        target = dest / name
        temp = target.with_suffix(".tmp")
        temp.write_bytes(data)
        temp.replace(target)
        hashes[name] = hashlib.sha256(data).hexdigest()
    return hashes


def row_for_button(button) -> tuple[str, str]:
    """Use the closest ancestor with one CSV button and an activity timestamp."""
    found = button.evaluate(
        """el => {
          const date = /20\\d{2}[\\/.-]\\d{1,2}[\\/.-]\\d{1,2}\\s+\\d{1,2}:\\d{2}/;
          for (let node = el; node && node !== document.body; node = node.parentElement) {
            const controls = [...node.querySelectorAll('button, [role="button"]')]
              .filter(b => /^CSV$/i.test((b.innerText || b.getAttribute('aria-label') || '').trim()));
            const match = (node.innerText || '').match(date);
            if (match && controls.length === 1) return [match[0], node.innerText];
          }
          return null;
        }"""
    )
    if not found:
        raise SyncError("CSVボタンに対応する活動日時を読めません")
    return found[0], found[1]


def sync_once(max_activities: int) -> int:
    if not AUTH.is_file():
        raise SyncError("/config/auth_state.json がありません。Windowsで初回ログインを実施してください")
    from playwright.sync_api import sync_playwright

    ROOT.mkdir(parents=True, exist_ok=True)
    manifest = read_manifest()
    done = 0
    seen = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        context = browser.new_context(storage_state=str(AUTH), accept_downloads=True,
                                      locale="ja-JP", timezone_id="Asia/Tokyo")
        try:
            page = context.new_page()
            page.goto(RDM_URL, wait_until="domcontentloaded", timeout=60_000)
            if "Unauthenticated" in page.locator("body").inner_text(timeout=10_000):
                raise SyncError("RDMがUnauthenticatedを返しました。一時的な応答の可能性があるため5分後に再試行します。続く場合はログイン状態を確認してください")
            if "/activities" not in page.url:
                raise SyncError("RDMのログイン状態が失効しました。初回ログインを再実施してください")
            page.get_by_text("アクティビティ一覧", exact=True).first.wait_for(timeout=20_000)
            buttons = page.get_by_role("button", name=re.compile(r"^CSV$", re.I))
            buttons.first.wait_for(timeout=20_000)
            count = min(buttons.count(), max_activities)
            print(f"表示中の活動 {count} 件を確認", flush=True)
            for index in range(count):
                button = buttons.nth(index)
                date_text, summary = row_for_button(button)
                key = key_from_date(date_text)
                if key in seen:
                    raise SyncError(f"同じ分に複数活動: {date_text}。上書きを防ぐため停止")
                seen.add(key)
                if key in manifest["activities"]:
                    print(f"SKIP {key}", flush=True)
                    continue
                button.click()
                try:
                    page.get_by_text("CSV出力", exact=True).wait_for(timeout=10_000)
                    for filename in REQUIRED:
                        page.get_by_text(filename, exact=True).wait_for(timeout=10_000)
                    with page.expect_download(timeout=60_000) as pending:
                        page.locator('input[placeholder*="名前をつけてダウンロード"]').fill(key)
                        page.get_by_text("一括", exact=True).click()
                    path = ROOT / f".{key}.download.zip"
                    pending.value.save_as(path)
                    try:
                        target = ROOT / key
                        hashes = extract_csv(path, target)
                        path.replace(target / f"{key.replace('_', '')}.zip")
                    finally:
                        path.unlink(missing_ok=True)
                    manifest["activities"][key] = {
                        "rdm_datetime": date_text,
                        "row_summary": " ".join(summary.split())[:500],
                        "files_sha256": hashes,
                        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    atomic_json(ROOT / "manifest.json", manifest)
                    done += 1
                    print(f"OK {key}: {', '.join(sorted(hashes))}", flush=True)
                finally:
                    # Reloading closes the modal even if its X icon has no
                    # accessible name. The next run also checks this page.
                    page.reload(wait_until="domcontentloaded", timeout=60_000)
                    page.get_by_text("アクティビティ一覧", exact=True).first.wait_for(timeout=20_000)
            # Keep refreshed cookies/local storage across future runs.
            temp = AUTH.with_suffix(".tmp")
            context.storage_state(path=str(temp), indexed_db=True)
            temp.replace(AUTH)
            AUTH.chmod(0o600)
        finally:
            context.close()
            browser.close()
    return done


def main() -> None:
    options = json.loads(OPTIONS.read_text(encoding="utf-8"))
    interval = int(options["interval_hours"])
    limit = int(options["max_activities"])
    if not 1 <= interval <= 24 or not 1 <= limit <= 100:
        raise ValueError("interval_hours は1～24、max_activities は1～100")
    while True:
        try:
            print(f"開始: {datetime.now().isoformat(timespec='seconds')}", flush=True)
            print(f"新規保存: {sync_once(limit)} 件", flush=True)
            time.sleep(interval * 3600)
        except Exception as exc:
            # Don't log cookies, page HTML, or auth_state contents.
            print(f"同期失敗: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(300)


if __name__ == "__main__":
    main()
