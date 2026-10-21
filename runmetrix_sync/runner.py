"""Home Assistant app: scheduled visible-UI CSV downloads from RDM."""

from __future__ import annotations

import csv
import fcntl
import shutil
import tempfile
from functools import wraps
import hashlib
import json
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

RDM_URL = "https://data-viewer.asics.com/activities"
AUTH = Path("/config/auth_state.json")
CREDENTIALS = Path("/config/rdm_credentials.json")
ROOT = Path("/share/runmetrix")
OPTIONS = Path("/data/options.json")
DATE_RE = re.compile(r"20\d{2}[/.-]\d{1,2}[/.-]\d{1,2}\s+\d{1,2}:\d{2}")
REQUIRED = {"lap_form_data.csv", "lap_form_score.csv"}
KNOWN = REQUIRED
CSV_NAME = re.compile(r"^(lap_form_data|lap_form_score)(?:-(\d{12}))?\.csv$", re.I)


class SyncError(Exception):
    pass


class AuthRequired(SyncError):
    pass


def read_credentials() -> tuple[str, str] | None:
    """The optional private file is never included in logs or the repository."""
    if not CREDENTIALS.is_file():
        return None
    try:
        value = json.loads(CREDENTIALS.read_text(encoding="utf-8"))
        email, password = value["email"], value["password"]
        if not isinstance(email, str) or not email.strip() or not isinstance(password, str) or not password:
            raise ValueError("empty credentials")
    except (OSError, ValueError, KeyError, TypeError):
        raise SyncError("rdm_credentials.json の形式が不正です。email と password を確認してください") from None
    return email.strip(), password


def save_auth_state(context) -> None:
    temp = AUTH.with_suffix(".tmp")
    try:
        context.storage_state(path=str(temp), indexed_db=True)
        temp.chmod(0o600)
        temp.replace(AUTH)
    finally:
        temp.unlink(missing_ok=True)


def activity_page(page) -> None:
    """Distinguish an expired login from an unrelated UI/network failure."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    response = page.goto(RDM_URL, wait_until="domcontentloaded", timeout=60_000)
    try:
        page.get_by_text("アクティビティ一覧", exact=True).first.wait_for(timeout=20_000)
    except PlaywrightTimeout:
        body = page.locator("body").inner_text(timeout=10_000)
        if ("Unauthenticated" in body or response and response.status in (401, 403, 500)
                or "/activities" not in page.url
                or "ログイン" in body and "アクティビティ一覧" not in body):
            raise AuthRequired("RDMのログイン状態を確認できません") from None
        raise SyncError("RDMの活動一覧が表示されません。画面構成または通信状態を確認してください") from None


def login_location(page) -> str:
    """Report only a known host and coarse page type, never auth query strings."""
    try:
        url = urlsplit(page.url)
        if url.hostname == "id.asics.com":
            place = "OneASICS"
        elif url.hostname == "data-viewer.asics.com":
            place = "RDM"
        else:
            return "別の画面"
        path = url.path
        if path == "/":
            return f"{place}トップ"
        if path in ("/login", "/home", "/activities"):
            return f"{place}{path}"
        return f"{place}の別画面"
    except Exception:
        return "画面を特定できません"


def login_diagnostics(page, failures: list[str], documents: list[str]) -> str:
    """Keep diagnostics coarse and never include input values or URL queries."""
    items = [f"停止位置: {login_location(page)}"]
    try:
        state = page.evaluate("document.readyState")
        if state in ("loading", "interactive", "complete"):
            items.append(f"読込: {state}")
        body = page.locator("body").inner_text(timeout=2_000).lower()
        for marker, label in (("access denied", "アクセス拒否"), ("forbidden", "アクセス拒否"),
                              ("verify you are human", "本人確認画面"),
                              ("checking your browser", "ブラウザー確認画面"),
                              ("unauthenticated", "未認証エラー"),
                              ("エラー", "エラー表示")):
            if marker in body:
                items.append(label)
                break
    except Exception:
        pass
    if documents:
        items.append("文書応答: " + ", ".join(dict.fromkeys(documents[:8])))
    if failures:
        items.append("通信失敗: " + ", ".join(dict.fromkeys(failures[:8])))
    return "、".join(items)


def sign_in(page, credentials: tuple[str, str]) -> None:
    """Use the visible OneASICS form; never call undocumented login endpoints."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    email, password = credentials
    stage = "OneASICSの画面表示"
    failures: list[str] = []
    documents: list[str] = []

    def record_failure(request) -> None:
        if len(failures) >= 8 or request.resource_type not in ("document", "script", "xhr", "fetch"):
            return
        host = urlsplit(request.url).hostname
        error = re.search(r"ERR_[A-Z_]+", request.failure or "")
        if host and error and error.group() != "ERR_ABORTED":
            failures.append(f"{host} {request.resource_type} {error.group()}")

    def record_http_response(response) -> None:
        resource_type = response.request.resource_type
        if resource_type not in ("document", "script", "xhr", "fetch"):
            return
        host = urlsplit(response.url).hostname
        if host and resource_type == "document" and len(documents) < 8:
            documents.append(f"{host} HTTP {response.status}")
        if host and response.status >= 400 and len(failures) < 8:
            failures.append(f"{host} {resource_type} HTTP {response.status}")

    page.on("requestfailed", record_failure)
    page.on("response", record_http_response)
    try:
        page.goto("https://data-viewer.asics.com/", wait_until="domcontentloaded", timeout=60_000)
        page.get_by_role("button", name="ログイン", exact=True).first.click(timeout=20_000)
        stage = "メールアドレス入力"
        user_field = page.locator('input[type="email"], input[autocomplete="username"], input[name="email"]').first
        user_field.wait_for(state="visible", timeout=45_000)
        # OneASICS currently shows a cookie preference dialog on first visit.
        cookie_ok = page.get_by_role("button", name="OK", exact=True)
        if cookie_ok.is_visible():
            cookie_ok.click(timeout=10_000)
        user_field.fill(email)
        stage = "パスワード入力"
        pass_field = page.locator('input[type="password"]').first
        if not pass_field.is_visible():
            page.get_by_role("button", name=re.compile(r"^(次へ|続行|Continue|Next)$", re.I)).first.click(timeout=15_000)
            pass_field.wait_for(timeout=30_000)
        pass_field.fill(password)
        stage = "ログイン送信"
        submit = page.locator('button[type="submit"], input[type="submit"]').first
        if submit.count():
            submit.click(timeout=15_000)
        else:
            page.get_by_role("button", name=re.compile(r"^(ログイン|サインイン|Sign in|Log in)$", re.I)).last.click(timeout=15_000)
        # Verify at the destination before persisting any new state.
        stage = "RDMへの戻り"
        page.wait_for_url(re.compile(r"^https://data-viewer\.asics\.com/(?:home|activities)(?:[/?#]|$)"), timeout=60_000)
        stage = "活動一覧の確認"
        activity_page(page)
    except (PlaywrightTimeout, SyncError):
        raise SyncError(f"自動ログイン失敗（{stage}、{login_diagnostics(page, failures, documents)}）") from None
    except Exception:
        # Never print a Playwright trace that might contain a credential or auth URL.
        raise SyncError(f"自動ログイン失敗（{stage}、{login_diagnostics(page, failures, documents)}）") from None
    finally:
        page.remove_listener("requestfailed", record_failure)
        page.remove_listener("response", record_http_response)


def open_authenticated_activity(page, context, credentials: tuple[str, str] | None, has_state: bool) -> None:
    if has_state:
        try:
            activity_page(page)
            return
        except AuthRequired:
            pass
    if credentials is None:
        raise SyncError("RDMの認証が失効しました。自動ログインには rdm_credentials.json が必要です")
    print("OneASICSへログインします", flush=True)
    sign_in(page, credentials)
    save_auth_state(context)
    print("再ログイン成功: 活動一覧を確認し、認証状態を保存しました", flush=True)


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
                if match.group(2):
                    try:
                        stamp = datetime.strptime(match.group(2), "%Y%m%d%H%M")
                    except ValueError:
                        raise SyncError("CSVの日時が不正です") from None
                    if re.fullmatch(r"20\d{6}_\d{4}", dest.name) and stamp.strftime("%Y%m%d_%H%M") != dest.name:
                        raise SyncError("CSV日時と活動フォルダーが一致しません")
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
            # Flatten archive subdirectories, preserving the original basename.
            values[Path(item.filename).name] = data
    if dest.exists():
        extra = [p.name for p in dest.iterdir()
                 if p.suffix.lower() == ".csv" and p.name not in values]
        if extra:
            raise SyncError("保存先に別名のCSVがあります。重複保存を避けて停止: " + ", ".join(extra))
    dest.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, data in sorted(values.items()):
        target = dest / name
        temp = target.with_suffix(".tmp")
        temp.write_bytes(data)
        temp.replace(target)
        hashes[name] = hashlib.sha256(data).hexdigest()
    return hashes



def save_download(archive_path, target, clean_run=False):
    if not clean_run:
        hashes=extract_csv(archive_path,target)
        archive_path.replace(target/f"{target.name.replace('_','')}.zip")
        return hashes
    if target.is_symlink(): raise SyncError('活動フォルダーがリンクです')
    # Validate in isolation before touching the previous export.
    with tempfile.TemporaryDirectory(prefix='.rdm_refresh_',dir=ROOT) as temporary:
        stage=Path(temporary)/target.name
        hashes=extract_csv(archive_path,stage)
        shutil.copy2(archive_path,stage/f"{target.name.replace('_','')}.zip")
        previous=Path(temporary)/'previous'
        existed=target.exists()
        if existed: target.rename(previous)
        try:
            stage.rename(target)
        except BaseException:
            if existed: previous.rename(target)
            raise
        archive_path.unlink(missing_ok=True)
    return hashes


def should_skip(key,manifest,clean_run):
    return not clean_run and key in manifest['activities']

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




def exclusive_sync(function):
    @wraps(function)
    def locked(*args, **kwargs):
        directory=ROOT if 'ROOT' in globals() else SOURCE
        directory.mkdir(parents=True,exist_ok=True)
        with (directory/'.sync.lock').open('a') as handle:
            print('共有データの処理ロックを取得します',flush=True)
            fcntl.flock(handle,fcntl.LOCK_EX)
            return function(*args,**kwargs)
    return locked


@exclusive_sync
def sync_once(max_activities: int, clean_run: bool = False) -> int:
    credentials = read_credentials()
    if not AUTH.is_file() and credentials is None:
        raise SyncError("auth_state.json か rdm_credentials.json が必要です")
    from playwright.sync_api import sync_playwright

    ROOT.mkdir(parents=True, exist_ok=True)
    manifest = read_manifest()
    if clean_run: print('クリーン処理: 取得履歴を無視してサイトから再取得・活動フォルダーを置換します',flush=True)
    done = 0
    seen = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        context = browser.new_context(storage_state=str(AUTH) if AUTH.is_file() else None, accept_downloads=True,
                                      locale="ja-JP", timezone_id="Asia/Tokyo")
        try:
            page = context.new_page()
            open_authenticated_activity(page, context, credentials, AUTH.is_file())
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
                if should_skip(key,manifest,clean_run):
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
                        hashes = save_download(path,target,clean_run)
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
            save_auth_state(context)
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
    clean=options.get('clean_run',False)
    if not isinstance(clean,bool): raise ValueError('clean_runは真偽値にしてください')
    while True:
        try:
            print(f"開始: {datetime.now().isoformat(timespec='seconds')}", flush=True)
            count=sync_once(limit,clean)
            label="再取得保存" if clean else "新規保存"
            print(f"{label}: {count} 件", flush=True)
            time.sleep(interval * 3600)
        except Exception as exc:
            # Don't log cookies, page HTML, or auth_state contents.
            print(f"同期失敗: {type(exc).__name__}: {exc}", flush=True)
            # A bad password or interactive challenge must not cause a login storm.
            time.sleep(interval * 3600)


if __name__ == "__main__":
    main()
