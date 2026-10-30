"""1080p scene composition and deterministic cuts; no model calls."""
import math
from pathlib import Path
from src.shared.media_tools import binary,run,validate,duration,probe


def stamp(seconds):
    millis=round(seconds*1000);return f'{millis//3600000:02}:{millis//60000%60:02}:{millis//1000%60:02},{millis%1000:03}'


def wrap(text,font_size=44,max_width=1720):
    from PIL import ImageFont
    from src.shared._workflow_store import WorkflowError
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',font_size)
    lines=['']
    for word in text.split():
        candidate=(lines[-1]+' '+word).strip()
        if font.getlength(candidate)>max_width:
            lines.append(word)
        else: lines[-1]=candidate
    if len(lines)>2 or any(font.getlength(line)>max_width for line in lines): raise WorkflowError('SUBTITLE_LAYOUT','Subtitle does not fit two lines; shorten the speech segment')
    return '\n'.join(lines)


def srt(cues,path,font_size=44,resolution=(1920,1080)):
    max_width=resolution[0]-200
    path.write_text('\n\n'.join(f'{i}\n{stamp(c["start"])} --> {stamp(c["end"])}\n{wrap(c["text"],font_size,max_width)}' for i,c in enumerate(cues,1))+'\n',encoding='utf-8')
    header='[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,DejaVu Sans,44,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,100,100,24,1\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
    def astamp(value):
        cs=round(value*100);return f'{cs//360000}:{cs//6000%60:02}:{cs//100%60:02}.{cs%100:02}'
    header=header.replace('DejaVu Sans,44,',f'DejaVu Sans,{font_size},')
    header=header.replace('PlayResX: 1920',f'PlayResX: {resolution[0]}').replace('PlayResY: 1080',f'PlayResY: {resolution[1]}')
    events=[]
    for cue in cues:
        text=wrap(cue['text'],font_size,max_width).replace('\\','\\\\').replace('{',r'\{').replace('}',r'\}').replace('\n',r'\N')
        events.append(f'Dialogue: 0,{astamp(cue["start"])},{astamp(cue["end"])},Default,,0,0,0,,{text}\n')
    path.with_suffix('.ass').write_text(header+''.join(events),encoding='utf-8')


def concat_audio(paths,output,seconds=None):
    args=[binary('ffmpeg'),'-y','-v','error']
    for path in paths: args+=['-i',str(path)]
    inputs=''.join(f'[{i}:a]' for i in range(len(paths)))
    filters=inputs+f'concat=n={len(paths)}:v=0:a=1,aresample=48000'
    if seconds is not None: filters+=',adelay=150,apad';args+=['-t',str(seconds)]
    run(args+['-filter_complex',filters+'[a]','-map','[a]','-ac','1','-c:a','pcm_s16le',str(output)])


def scene(video,audio,captions,output,seconds):
    # Quote the filter filename (the process is invoked without a shell).
    escaped=str(captions.with_suffix('.ass')).replace('\\','\\\\').replace(':','\\:').replace("'", "'\\''")
    info=probe(video)['streams']
    stream=next(s for s in info if s['codec_type']=='video')
    width,height=(1080,1920) if stream['width']<stream['height'] else (1920,1080)
    vf=f"scale={width}:{height-160}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:0:color=0x0D1726,setsar=1,fps=30,ass=filename='"+escaped+"'"
    temp=output.with_suffix('.partial.mp4')
    try:
        run([binary('ffmpeg'),'-y','-v','error','-i',str(video),'-i',str(audio),'-vf',vf,'-map','0:v:0','-map','1:a:0','-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p','-c:a','aac','-b:a','160k','-ar','48000','-af','apad','-t',str(seconds),'-movflags','+faststart',str(temp)])
        validate(temp,audio=True,resolution=[width,height],fps=30);temp.replace(output)
    finally: temp.unlink(missing_ok=True)


def assemble(clips,output):
    args=[binary('ffmpeg'),'-y','-v','error']
    for path in clips: args+=['-i',str(path)]
    durations=[float(probe(path)['streams'][0]['duration']) for path in clips]
    stream=next(s for s in probe(clips[0])['streams'] if s['codec_type']=='video')
    resolution=[stream['width'],stream['height']]
    filters=[]
    for i,seconds in enumerate(durations):
        filters.append(f'[{i}:v]setpts=PTS-STARTPTS[v{i}]')
        filters.append(f'[{i}:a]apad,atrim=0:{seconds},asetpts=PTS-STARTPTS[a{i}]')
    inputs=''.join(f'[v{i}][a{i}]' for i in range(len(clips)))
    graph=';'.join(filters)+';'+inputs+f'concat=n={len(clips)}:v=1:a=1[v][a];[v]fps=30[vout];[a]apad[aout]'
    total=sum(durations)
    temp=output.with_suffix('.partial.mp4')
    try:
        run(args+['-filter_complex',graph,'-map','[vout]','-map','[aout]','-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p','-c:a','aac','-b:a','160k','-ar','48000','-t',str(total),'-r','30','-movflags','+faststart',str(temp)])
        validate(temp,audio=True,resolution=resolution,fps=30);temp.replace(output)
    finally: temp.unlink(missing_ok=True)
