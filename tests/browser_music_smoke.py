"""Run with base Python/Playwright; pass the video environment's Python as argv[1]."""
import json
from pathlib import Path
import subprocess
import sys
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
output=root/'output/audio_music_validation';output.mkdir(exist_ok=True)
fixture=subprocess.Popen([sys.argv[1],'-m','tests.music_browser_fixture'],cwd=root,stdout=subprocess.PIPE,text=True)
try:
    info=json.loads(fixture.stdout.readline());url=info['origin'];calls=Path(info['calls'])
    initial_calls=calls.read_text()
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(url+'/#preview')
        page.get_by_role('heading',name='Nhạc nền',exact=True).wait_for()
        assert page.get_by_label('Giọng đọc',exact=True).input_value()=='vi-VN-NamMinhNeural'
        select=page.get_by_label('Bài nhạc',exact=True)
        assert select.locator('option').count()==6
        select.select_option('relaxation-05')
        audio=page.get_by_label('Nghe thử nhạc nền',exact=True)
        audio.evaluate('(a)=>{a.preload="auto";a.load();}')
        page.wait_for_function('() => document.querySelector("#music-settings-panel audio").readyState>=1')
        assert audio.evaluate('(a)=>a.duration')>100
        page.get_by_role('button',name='Lưu và phối nhạc',exact=True).click()
        def wait_for_mix(track_id,volume):
            page.wait_for_function('''async (expected)=>{
              const data=await (await fetch('/api/state')).json();
              const item=data.project.continuous_preview.audio;
              return item && item.music.config.track_id===expected.id && item.music.config.volume===expected.volume;
            }''',arg={'id':track_id,'volume':volume},timeout=120000)
            page.get_by_role('button',name='Lưu và phối nhạc',exact=True).wait_for(state='visible')
            page.wait_for_function('() => !document.querySelector("#music-settings-panel button").disabled')
            page.wait_for_function('''(expected)=>{
              const text=document.querySelector('#continuous-preview .muted')?.textContent || '';
              return text.includes('Có lời đọc') && (!expected || text.includes(expected));
            }''',arg={'relaxation-05':'Relaxation 05','meditation':'Meditation'}.get(track_id),timeout=30000)
        wait_for_mix('relaxation-05',10)
        page.locator('#continuous-video').evaluate('(v)=>{v.preload="auto";v.load();}')
        page.wait_for_function('() => document.querySelector("#continuous-video").videoWidth===640')
        for name in ('Tải âm thanh WAV','Tải lời đọc riêng','Tải video MP4','Tải phụ đề SRT'):
            link=page.get_by_role('link',name=name,exact=True)
            response=page.request.get(url+link.get_attribute('href'));assert response.ok
        assert calls.read_text()==initial_calls,'Music edit unexpectedly synthesized speech'
        page.locator('#music-settings-panel').scroll_into_view_if_needed()
        page.screenshot(path=str(output/'music-desktop.png'),full_page=True)
        select=page.get_by_label('Bài nhạc',exact=True);select.select_option('meditation')
        page.get_by_label('Âm lượng nhạc',exact=True).evaluate('(v)=>{v.value="20";v.dispatchEvent(new Event("input",{bubbles:true}));}')
        page.get_by_label('Tự hạ nhạc khi có lời đọc',exact=True).uncheck()
        page.get_by_role('button',name='Lưu và phối nhạc',exact=True).click();wait_for_mix('meditation',20)
        assert calls.read_text()==initial_calls
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),'Mobile layout overflows'
        page.locator('#music-settings-panel').scroll_into_view_if_needed()
        page.screenshot(path=str(output/'music-mobile.png'),full_page=True)
        page.get_by_label('Bài nhạc',exact=True).select_option('')
        page.get_by_role('button',name='Lưu và phối nhạc',exact=True).click();wait_for_mix(None,20)
        assert calls.read_text()==initial_calls
        assert not errors,errors
        browser.close()
    print('PASS: Nam Minh default; five tracks; MP3 playback; select/change/remove music; volume/ducking; WAV/MP4/SRT downloads; no repeat TTS; desktop/mobile; no JS errors')
finally:
    fixture.terminate()
    try:fixture.communicate(timeout=10)
    except subprocess.TimeoutExpired:fixture.kill();fixture.communicate()
