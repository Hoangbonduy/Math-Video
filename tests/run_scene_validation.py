"""Run scene regressions with fixed offline provider settings, independent of user config."""
import os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'artifacts/scene-validation'
OUTPUT.mkdir(parents=True,exist_ok=True)
files=sorted({f for pattern in ['test_scene*.py','test_manim*.py','test_render*.py','test_single_scene_routing.py','test_failed_scene_feedback.py']
              for f in (ROOT/'tests').glob(pattern)})
env=dict(os.environ,AI_BACKEND='openrouter',OPENROUTER_MODEL='anthropic/claude-haiku-5.5',
         SCENE_REVIEW_MODEL='qwen/qwen3-vl-235b-a22b-instruct',RUN_LIVE_SCENE_TESTS='0')
command=[sys.executable,'-m','pytest','-q','--junitxml='+str(OUTPUT/'pytest.xml'),*map(str,files)]
with (OUTPUT/'pytest.log').open('w') as stream:
    result=subprocess.run(command,cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT)
print((OUTPUT/'pytest.log').read_text()[-6000:])
raise SystemExit(result.returncode)
