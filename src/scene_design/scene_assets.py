"""Wikimedia-only image acquisition, with per-file license evidence and limits."""
from datetime import datetime, timezone
import hashlib
import http.client
import io
import json
from pathlib import Path
import urllib.parse
import urllib.request

from src.shared import _workflow_store as store
from src.shared._workflow_store import WorkflowError

API='https://commons.wikimedia.org/w/api.php'
HEADERS={'User-Agent':'AI-Create-Video/Checkpoint5 (educational scene generation)'}


def _get(url, limit):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443) or parsed.hostname not in ('commons.wikimedia.org','upload.wikimedia.org'):
        raise WorkflowError('ASSET_URL_REJECTED','Only Wikimedia HTTPS URLs are allowed')
    from src.shared.openrouter_client import NoRedirect
    try:
        with urllib.request.build_opener(NoRedirect).open(urllib.request.Request(url,headers=HEADERS),timeout=15) as response:
            data=response.read(limit+1)
        if len(data)>limit: raise WorkflowError('ASSET_TOO_LARGE','Asset download exceeded limit')
        return data
    except (OSError,ValueError,http.client.HTTPException) as exc:
        raise WorkflowError('ASSET_FETCH_FAILED',str(exc)) from None


def search(query):
    params=dict(action='query',format='json',generator='search',gsrsearch=query,gsrnamespace=6,gsrlimit=10,
                prop='imageinfo|templates',tllimit=50,iiprop='url|extmetadata|mime|size|sha1',iiextmetadatalanguage='en')
    result=json.loads(_get(API+'?'+urllib.parse.urlencode(params),2_000_000))
    return list(result.get('query',{}).get('pages',{}).values())[:10]


def license_evidence(page):
    info=(page.get('imageinfo') or [{}])[0]
    metadata=info.get('extmetadata',{})
    def val(name): return metadata.get(name,{}).get('value','')
    short=val('LicenseShortName').strip().lower()
    url=val('LicenseUrl').strip()
    cc0=short in ('cc0','cc0 1.0','cc0 1.0 universal') and url.rstrip('/') in (
        'https://creativecommons.org/publicdomain/zero/1.0','http://creativecommons.org/publicdomain/zero/1.0')
    # Require the file copyright-status metadata, not the Commons metadata license.
    release_templates={t.get('title','').lower() for t in page.get('templates',[])}
    public=(short=='public domain' and val('Copyrighted')=='False' and bool(val('UsageTerms'))
            and bool(release_templates & {'template:pd-self','template:pd-author','template:pd-user'}))
    if not (cc0 or public) or val('Restrictions') or not val('Artist') or info.get('mime') not in ('image/png','image/jpeg'):
        raise WorkflowError('ASSET_LICENSE_REJECTED','File lacks supported content-license evidence or has additional restrictions')
    return dict(license='CC0-1.0' if cc0 else 'Public domain',license_url=url,
                author=val('Artist'),usage_terms=val('UsageTerms'),metadata=metadata,
                source_page=info.get('descriptionurl'),download_url=info.get('url'),page_id=page.get('pageid'))


def collect(root, directory, queries):
    """Each search and download outcome is persisted; restart does not re-query."""
    from PIL import Image
    assets=[]; failures=[]
    for index,query in enumerate(queries[:2]):
        cache=directory/f'asset_search_{index}.json'
        if cache.exists(): result=store.read_json(cache)
        else:
            try: result=dict(pages=search(query),query=query)
            except (WorkflowError,ValueError) as exc: result=dict(pages=[],query=query,error=str(exc))
            store.atomic_write(cache,result)
        for candidate_index,page in enumerate(result['pages'][:10]):
            if len(assets)>=2: break
            attempt=directory/f'asset_{index}_{candidate_index}.json'
            if attempt.exists(): record=store.read_json(attempt)
            else:
                try:
                    evidence=license_evidence(page)
                    raw=_get(evidence['download_url'],10_000_000)
                    with Image.open(io.BytesIO(raw)) as image:
                        if image.format not in ('JPEG','PNG') or image.width*image.height>20_000_000:
                            raise WorkflowError('ASSET_INVALID','Unsupported or oversized image')
                        image.verify()
                    checksum=hashlib.sha256(raw).hexdigest()
                    suffix='.png' if raw.startswith(b'\x89PNG') else '.jpg'
                    path='assets/'+checksum+suffix
                    store.materialize(root,path,content=raw)
                    evidence_path='assets/'+checksum+'.license.json'
                    evidence=dict(evidence,retrieved_at=datetime.now(timezone.utc).isoformat(),sha256=checksum,raw_file_metadata=page)
                    # Multiple uses of the same image retain the first evidence snapshot.
                    if not (root/evidence_path).exists(): store.materialize(root,evidence_path,content=(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n').encode())
                    record=dict(asset_id='image_'+checksum[:16],path=path,sha256=checksum,evidence_path=evidence_path,
                                evidence_sha256=store.digest(root/evidence_path),status='success',license=evidence['license'],author=evidence['author'],source_page=evidence['source_page'])
                except (WorkflowError,OSError,ValueError,Image.DecompressionBombError) as exc:
                    record=dict(status='failed',error=str(exc))
                store.atomic_write(attempt,record)
            if record['status']=='success':
                if store.digest(root/record['path'])!=record['sha256'] or store.digest(root/record['evidence_path'])!=record['evidence_sha256']:
                    raise WorkflowError('ASSET_CHANGED','Saved image or license evidence changed')
                if record['asset_id'] not in {a['asset_id'] for a in assets}: assets.append(record)
            else: failures.append(record)
    return dict(assets=assets,failures=failures,fallback_required=not assets)
