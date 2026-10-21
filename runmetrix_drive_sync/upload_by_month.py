"""Append future HA activities to the existing manually organized Drive tree."""
import json
import fcntl
from functools import wraps
import hashlib
import shutil
import re
import subprocess
import time
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

CONFIG=Path('/config/rclone.conf')
SOURCE=Path('/share/runmetrix')
STATE=Path('/config/yearmonth_upload_state.json')
OPTIONS=Path('/data/options.json')
ROOT_ID=''
REMOTE='runmetrix_drive:'
KEY=re.compile(r'^20\d{6}_\d{4}$')


def atomic_json(path,value):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temp.replace(path)


def activity_path(key):
    if not KEY.fullmatch(key): raise ValueError('活動フォルダー名が不正です')
    datetime.strptime(key,'%Y%m%d_%H%M')
    return f'{key[:4]}/{key[4:6]}/{key}'


def rclone(*args):
    # Capture output: never emit tokens, config contents, or raw authentication URLs.
    result=subprocess.run(['rclone',*args,'--config',str(CONFIG),'--drive-root-folder-id',ROOT_ID,
                           '--retries','3','--log-level','ERROR'],capture_output=True,text=True,timeout=900)
    if result.returncode:
        raise RuntimeError(f'Drive操作 {args[0]} に失敗（終了コード {result.returncode}）。接続・権限・容量、保存先を確認してください')
    return result.stdout


def list_folders(path):
    entries=json.loads(rclone('lsjson',REMOTE+path,'--dirs-only'))
    names=[e['Name'] for e in entries if e.get('IsDir')]
    if len(names)!=len(set(names)):
        raise RuntimeError('同じ名前のフォルダーが複数あります。重複作成を避けて停止します')
    return set(names)


def configure_destination(options):
    global ROOT_ID
    folder_id=options.get('root_folder_id','')
    if not isinstance(folder_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',folder_id.strip()):
        raise RuntimeError('root_folder_idにRunmetrix_AutoフォルダーのIDを設定してください')
    ROOT_ID=folder_id.strip()


def source_activities(state=None, clean_run=False):
    obj=({'schema':1,'activities':{}} if clean_run else
         json.loads((SOURCE/'manifest.json').read_text(encoding='utf-8')))
    if obj.get('schema')!=1 or not isinstance(obj.get('activities'),dict):
        raise RuntimeError('HAのmanifest.jsonの形式が不正です')
    result={}
    for key,entry in obj['activities'].items():
        activity_path(key)
        if not isinstance(entry,dict): raise RuntimeError('活動情報の形式が不正です')
        directory=SOURCE/key
        if not directory.is_dir():
            recorded=(state or {}).get('activities',{}).get(key,{})
            if recorded.get('local_pruned') or recorded.get('local_prune_started'):
                continue
            if clean_run:
                print('ローカルなし: '+key+' クリーン転送の対象外')
                continue
            raise RuntimeError('活動フォルダーが不足しています: '+key)
        result[key]=entry
    # Count and process physical activity folders, including ones outside manifest.
    for directory in SOURCE.iterdir():
        if not KEY.fullmatch(directory.name): continue
        activity_path(directory.name)
        if directory.is_symlink(): raise RuntimeError('活動フォルダーがリンクです: '+directory.name)
        if directory.is_dir() and directory.name not in result:
            result[directory.name]={}
            print('実フォルダー検出: '+directory.name+' 履歴外も転送・保持判定に含めます')
    return result


def load_state():
    if not STATE.exists(): return None
    obj=json.loads(STATE.read_text(encoding='utf-8'))
    if obj.get('schema')!=1 or obj.get('root_folder_id')!=ROOT_ID or not isinstance(obj.get('activities'),dict):
        raise RuntimeError('年月転送記録の形式が不正です。自動で初期化せず停止します')
    return obj



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
def once(local_keep=10, clean_run=False):
    state=load_state()
    current=source_activities(state,clean_run)
    if state is None:
        state={'schema':1,'root_folder_id':ROOT_ID,'initialized_at_utc':datetime.now(timezone.utc).isoformat(),'activities':{}}
        print('転送履歴を新規作成し、ローカル活動を年月別に転送します')
    if clean_run: print('クリーン処理: 転送履歴を無視して全ローカル活動を再転送・同名ファイルを置換します')
    for key,entry in sorted(current.items()):
        if key in state['activities'] and not clean_run: continue
        if clean_run: local_snapshot(SOURCE/key)
        relative=activity_path(key)
        year,month,_=relative.split('/')
        years=list_folders('')
        if year not in years: rclone('mkdir',REMOTE+year)
        months=list_folders(year)
        if month not in months: rclone('mkdir',REMOTE+year+'/'+month)
        # copy is non-destructive; no remote delete or move operation is used.
        rclone('copy',str(SOURCE/key),REMOTE+relative,'--checkers','4','--transfers','2',*(['--ignore-times'] if clean_run else []))
        state['activities'][key]={'status':'uploaded','relative_path':relative,
                'uploaded_at_utc':datetime.now(timezone.utc).isoformat(),'source_metadata':entry}
        atomic_json(STATE,state)
        print('保存完了: Runmetrix_Auto/'+relative)
    # A separate future-upload index preserves the old root manifest and manual history.
    uploaded={key:{**entry['source_metadata'],'drive_relative_path':entry['relative_path']}
              for key,entry in state['activities'].items() if entry['status']=='uploaded'}
    index=STATE.with_name('yearmonth_upload_index.json')
    atomic_json(index,{'schema':1,'layout':'YYYY/MM/YYYYMMDD_HHMM','activities':uploaded})
    rclone('copyto',str(index),REMOTE+'yearmonth_upload_index.json')
    print('年月別コピーが完了しました。Driveの既存ファイルは削除しません。')
    return prune_local(state,current,local_keep)


def local_snapshot(directory):
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError('活動フォルダーが通常のディレクトリではありません')
    files={}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink(): raise RuntimeError('活動フォルダー内にリンクがあります')
        if path.is_file():
            files[str(path.relative_to(directory))]=hashlib.sha256(path.read_bytes()).hexdigest()
    if not any(re.fullmatch(r'lap_form_(data|score)(?:-\d{12})?\.csv', name, re.I) for name in files):
        raise RuntimeError('ローカルのフォームCSVがありません')
    return files


def equivalent_names(name, files, key):
    """Only allow CSV timestamp aliases for this activity, never another activity."""
    stamp=key.replace('_','')
    match=re.fullmatch(r'lap_form_(data|score)(?:-(\d{12}))?\.csv',name,re.I)
    if match and (match[2] is None or match[2]==stamp):
        allowed={f'lap_form_{match[1].lower()}.csv',f'lap_form_{match[1].lower()}-{stamp}.csv'}
        return [n for n in files if n.lower() in allowed]
    if '/' not in name and name.lower().endswith('.zip'):
        return [n for n in files if '/' not in n and n.lower().endswith('.zip')]
    return [name] if name in files else []


def verify_backup(directory, relative, key, before):
    # Read actual remote bytes; no size-only comparison or filename-only success.
    with tempfile.TemporaryDirectory(prefix='runmetrix_verify_') as temp:
        backup=Path(temp)
        rclone('copy',REMOTE+relative,str(backup),'--checkers','4','--transfers','2')
        remote=local_snapshot(backup)
        missing=[]
        for name,digest in before.items():
            names=equivalent_names(name,remote,key)
            if name in remote and remote[name]!=digest:
                raise RuntimeError('Driveと内容不一致: '+name+'。上書き・削除しません')
            if names:
                if not any(remote[n]==digest for n in names):
                    raise RuntimeError('Driveと内容不一致: '+name+'。上書き・削除しません')
            else:
                missing.append(name)
        # All existing equivalents must match before filling missing backups.
        for name in missing:
            rclone('copyto',str(directory/name),REMOTE+relative+'/'+name,'--ignore-existing')
        if missing:
            with tempfile.TemporaryDirectory(prefix='runmetrix_recheck_') as second:
                rclone('copy',REMOTE+relative,second,'--checkers','4','--transfers','2')
                remote=local_snapshot(Path(second))
        for name,digest in before.items():
            names=equivalent_names(name,remote,key)
            if (name in remote and remote[name]!=digest) or not any(remote[n]==digest for n in names):
                raise RuntimeError('Driveの最終照合不一致: '+name+'。HA側を保持します')


def prune_local(state,current,keep):
    if not isinstance(keep,int) or isinstance(keep,bool) or keep<1:
        raise RuntimeError('local_keepは1以上の整数にしてください')
    candidates=sorted(current,reverse=True)[keep:]
    removed=0
    for key in candidates:
        entry=state['activities'].get(key,{})
        if entry.get('status') not in ('uploaded','baseline_existing'):
            print('保持: '+key+' 転送記録なし')
            continue
        directory=SOURCE/key
        relative=activity_path(key)
        try:
            if entry.get('relative_path')!=relative:
                raise RuntimeError('転送先記録が一致しません')
            before=local_snapshot(directory)
            # Detect ambiguity before any writes. A missing remote activity is not recreated here.
            if key not in list_folders(relative.rsplit('/',1)[0]):
                raise RuntimeError('Driveにバックアップ活動フォルダーがありません')
            verify_backup(directory, relative, key, before)
            if before!=local_snapshot(directory):
                raise RuntimeError('照合中にローカルファイルが変わりました')
            entry['local_prune_started']=True
            entry['backup_verified_at_utc']=datetime.now(timezone.utc).isoformat()
            atomic_json(STATE,state)
            shutil.rmtree(directory)
            entry['local_pruned']=True
            entry['local_pruned_at_utc']=datetime.now(timezone.utc).isoformat()
            atomic_json(STATE,state)
            removed+=1
            print('HA側を削除: '+key+' Driveの全ファイル照合済み')
        except Exception as exc:
            reason=str(exc) if isinstance(exc,(RuntimeError,ValueError)) else type(exc).__name__
            print('保持: '+key+' バックアップ照合・削除未完了: '+reason)
    remaining=sum((SOURCE/key).is_dir() for key in current)
    print(f'HA保持: {remaining}活動 / 今回削除: {removed}活動 / 通常保持: 最新{keep}活動。未確認データは追加保持します。')
    return max(0,remaining-keep)


def main():
    options=json.loads(OPTIONS.read_text())
    configure_destination(options)
    interval=options.get('interval_hours')
    if not isinstance(interval,int) or isinstance(interval,bool) or not 1<=interval<=24:
        raise RuntimeError('interval_hours は1～24の整数にしてください')
    keep=options.get('local_keep',10)
    if not isinstance(keep,int) or isinstance(keep,bool) or keep<1:
        raise RuntimeError('local_keepは1以上の整数にしてください')
    clean=options.get('clean_run',False)
    if not isinstance(clean,bool): raise ValueError('clean_runは真偽値にしてください')
    print(f'Google Drive年月別同期間隔: {interval}時間 / HA通常保持: 最新{keep}活動')
    while True:
        delay=run_cycle(keep,interval,clean)
        time.sleep(delay)


def jst_now():
    return datetime.now(timezone(timedelta(hours=9)))


def run_cycle(keep,interval,clean_run=False):
    print(jst_now().strftime('%Y/%m/%d %H:%M')+' 同期処理実行開始',flush=True)
    delay=interval*3600
    status='正常終了'
    try:
        if not CONFIG.is_file(): raise RuntimeError('rclone.confがありません')
        pending=once(keep,clean_run)
        if pending: status=f'照合保留 {pending}活動'
    except Exception as exc:
        status='エラー終了'
        print('転送停止: '+(str(exc) if isinstance(exc,(RuntimeError,ValueError)) else type(exc).__name__))
        delay=300
    finally:
        ended=jst_now()
        print(ended.strftime('%Y/%m/%d %H:%M')+' 同期処理終了（'+status+'）',flush=True)
        wait=f'{interval}時間後' if delay==interval*3600 else '5分後（再試行）'
        planned=(ended+timedelta(seconds=delay)).strftime('%Y/%m/%d %H:%M')
        print('次回実行予定 '+wait+' / '+planned+'（日本時間）',flush=True)
    return delay

if __name__=='__main__': main()
