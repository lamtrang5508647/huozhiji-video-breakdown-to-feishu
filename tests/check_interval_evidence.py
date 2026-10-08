"""Real decoder regression for rounded millisecond boundaries and stale evidence."""
import pathlib,sys,tempfile,json,hashlib
import av
from PIL import Image
project=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(project/'skill/analyze-folder-videos-to-feishu/scripts'))
from extract_interval_evidence import extract
from validate_interval_evidence import validate
def save(p,d):p.write_text(json.dumps(d),encoding='utf-8')
with tempfile.TemporaryDirectory() as td:
 root=pathlib.Path(td);source=root/'four-frames.mp4'
 with av.open(str(source),'w') as output:
  stream=output.add_stream('libx264',rate=30);stream.width=64;stream.height=64;stream.pix_fmt='yuv420p'
  for color in ['red','green','blue','white']:
   frame=av.VideoFrame.from_image(Image.new('RGB',(64,64),color))
   for packet in stream.encode(frame):output.mux(packet)
  for packet in stream.encode():output.mux(packet)
 timeline=root/'timeline.json';data={'video_id':'quantized-pts-test','source':{'path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()},'segments':[{'id':'s1','start_ms':0,'end_ms':67},{'id':'s2','start_ms':67,'end_ms':134}]};save(timeline,data)
 extract(timeline,root/'evidence');index=root/'evidence/interval-evidence.json';e=json.loads(index.read_text());frame=e['shots']['s2']['start'];assert abs(frame['actual_pts_ms']-66.6666667)<.001,frame
 assert e['shots']['s2']['cut_before']['actual_pts_ms']<66
 assert validate(timeline,index)['status']=='PASS'
 altered=json.loads(index.read_text());altered['shots']['s2']['middle']['actual_pts_ms']=20;save(index,altered);assert validate(timeline,index)['status']=='FAIL'
 save(index,e);data['segments'][1]['start_ms']=68;save(timeline,data);assert validate(timeline,index)['status']=='FAIL'
print(json.dumps({'status':'PASS','tests':['rounded cut selects native 66.667 ms frame, not 100 ms','before frame truly precedes cut frame','valid image hashes and intervals pass','outside-interval frame rejected','changed timeline rejected'],'scope':'Local real decoder fixture, not source visual content judgment'}))
