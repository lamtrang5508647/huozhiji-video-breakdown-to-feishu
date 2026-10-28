"""Real headless-browser regression using locally generated media; no network.

If Chrome/Chromium, websocket-client or FFmpeg is missing, this test is skipped.
It does not watch the video or listen to the sound.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / 'skill/bilibili-editing-analysis/assets/local-review.html'


def chrome_path():
    choices = [os.environ.get('CHROME_PATH'), shutil.which('google-chrome'),
               shutil.which('chromium'), shutil.which('chromium-browser'),
               r'C:\Program Files\Google\Chrome\Application\chrome.exe',
               r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']
    return next((str(Path(p)) for p in choices if p and Path(p).is_file()), None)


@unittest.skipUnless(shutil.which('ffmpeg') and chrome_path() and importlib.util.find_spec('websocket'),
                     'Chrome/Chromium, FFmpeg or websocket-client not available')
class ReviewBrowserTest(unittest.TestCase):
    def test_offline_review_roundtrip_and_mismatched_source_rejected(self):
        import websocket
        with tempfile.TemporaryDirectory(prefix='bilibili-review-test-') as tmp:
            root = Path(tmp); profile = root/'profile'; downloads = root/'downloads'; downloads.mkdir()
            sample = root/'sample.mp4'; manifest = root/'manifest.json'; bad = root/'bad.json'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=10:duration=3',
                            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3', '-shortest', '-c:v', 'libx264',
                            '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(sample)], check=True, capture_output=True)
            sha = hashlib.sha256(sample.read_bytes()).hexdigest()
            plan = {'schema':'bilibili-review-events/v1', 'source_sha256':sha,
                    'events':[{'start_sec':0,'end_sec':1,'title':'<img src=x onerror=alert(1)>',
                               'audio':{'coverage_status':'partial','sample_count':44}},
                              {'start_sec':1,'end_sec':2,'title':'合成片段B'}]}
            manifest.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf8')
            bad.write_text(json.dumps({**plan, 'source_sha256':'0'*64}), encoding='utf8')
            cmd = [chrome_path(), '--headless=new', '--disable-gpu', '--no-first-run', '--remote-debugging-port=0',
                   '--remote-allow-origins=*', f'--user-data-dir={profile}', HTML.as_uri()]
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            ws = None
            try:
                def until(fn, seconds=10):
                    deadline = time.monotonic()+seconds
                    while time.monotonic()<deadline:
                        if fn(): return
                        time.sleep(.1)
                    self.fail('Browser condition timed out')
                active = profile/'DevToolsActivePort'; until(active.exists)
                port = int(active.read_text().splitlines()[0]); base = f'http://127.0.0.1:{port}'
                def pages():
                    return [p for p in json.load(urllib.request.urlopen(base+'/json/list',timeout=3))
                            if p.get('type')=='page' and p.get('url','').startswith('file:')]
                until(lambda: bool(pages())); ws = websocket.create_connection(pages()[0]['webSocketDebuggerUrl'],
                                                                               timeout=10, suppress_origin=True)
                serial = 0
                def call(method, params=None):
                    nonlocal serial
                    serial += 1; token = serial
                    ws.send(json.dumps({'id':token, 'method':method, 'params':params or {}}))
                    while True:
                        reply = json.loads(ws.recv())
                        if reply.get('id')==token:
                            if 'error' in reply: raise RuntimeError(reply['error'])
                            return reply.get('result', {})
                def js(expression):
                    result = call('Runtime.evaluate', {'expression':expression,'returnByValue':True,'awaitPromise':True})
                    if 'exceptionDetails' in result: raise RuntimeError(result['exceptionDetails'])
                    return result.get('result',{}).get('value')
                call('Browser.setDownloadBehavior', {'behavior':'allow','downloadPath':str(downloads)})
                doc = call('DOM.getDocument')['root']['nodeId']
                def pick(selector, path):
                    node = call('DOM.querySelector', {'nodeId':doc,'selector':selector})['nodeId']
                    call('DOM.setFileInputFiles', {'nodeId':node,'files':[str(path)]})
                pick('#videoFile',sample);pick('#manifestFile',manifest)
                until(lambda: js('!document.querySelector("#download").disabled'))
                self.assertEqual(js('document.querySelectorAll("tbody tr").length'),2)
                self.assertEqual(js('document.querySelectorAll("tbody img").length'),0)
                self.assertEqual(js('document.querySelector("tbody tr td:nth-child(2)").textContent'),plan['events'][0]['title'])
                self.assertTrue(js('fetch("https://example.com").then(()=>false).catch(()=>true)'))
                js('document.querySelectorAll(".seek")[1].click()');until(lambda: js('document.querySelector("video").currentTime') >= .95)
                js('document.querySelector("tbody select").value="continuous_playback";document.querySelector("tbody select").dispatchEvent(new Event("change"));document.querySelector("tbody textarea").value="合成观察";document.querySelector("tbody textarea").dispatchEvent(new Event("input"));document.querySelector("#download").click()')
                result = downloads/'manual-review.json'; until(result.exists)
                data = json.loads(result.read_text(encoding='utf8'))
                self.assertEqual(data['events'][0]['review'], {'visual':'continuous_playback','audio':'not_reviewed'})
                self.assertEqual(data['events'][0]['source_record']['audio']['coverage_status'],'partial')
                self.assertEqual(data['events'][0]['note'],'合成观察')
                pick('#manifestFile',result);until(lambda: js('!document.querySelector("#download").disabled'))
                self.assertEqual(js('document.querySelector("tbody select").value'),'continuous_playback')
                self.assertEqual(js('document.querySelector("tbody textarea").value'),'合成观察')
                pick('#manifestFile',bad);until(lambda: '不一致' in js('document.querySelector("#status").textContent'))
                self.assertTrue(js('document.querySelector("#download").disabled'))
                self.assertTrue(js('!document.querySelector("video").getAttribute("src")'))
            finally:
                if ws:ws.close()
                proc.terminate()
                try:proc.wait(timeout=10)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()


if __name__ == '__main__': unittest.main()
