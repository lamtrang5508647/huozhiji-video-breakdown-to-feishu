import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'skill/bilibili-editing-analysis/scripts/check_semantic_boundaries.py'
spec=importlib.util.spec_from_file_location('semantic_boundary',SCRIPT)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source.mp4';self.source.write_bytes(b'synthetic source')
        self.sha=hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.timeline=self.root/'timeline.json';self.asr=self.root/'asr.json'
        self.t={'video_id':'example','source':{'sha256':self.sha},'duration_sec':4.0,'semantic_groups':[
            {'id':'g1','start_ms':0,'end_ms':1000},{'id':'g2','start_ms':1000,'end_ms':2000},
            {'id':'g3','start_ms':2000,'end_ms':3000},{'id':'g4','start_ms':3000,'end_ms':4000}]}
        self.a={'video_id':'example','source_sha256':self.sha,'source_duration_sec':4.0,'status':'machine_unreviewed','segments':[
            {'start_sec':.8,'end_sec':1.3,'text':'first','words':[{'start_sec':.9,'end_sec':1.1,'text':'first'}]},
            {'start_sec':1.8,'end_sec':2.2,'text':'second','words':[{'start_sec':1.8,'end_sec':1.9,'text':'second'}]}]}
        self.save()

    def save(self):
        self.timeline.write_text(json.dumps(self.t),encoding='utf8');self.asr.write_text(json.dumps(self.a),encoding='utf8')

    def test_word_sentence_and_no_overlap_all_remain_unreviewed(self):
        r=mod.report(self.source,self.timeline,self.asr)
        self.assertEqual([x['risk'] for x in r['boundaries']],['machine_word_overlap','machine_segment_overlap','no_machine_overlap_detected'])
        self.assertTrue(all(x['review_status']=='requires_listening' for x in r['boundaries']))
        self.assertEqual(r['boundaries'][0]['crossing_words'][0]['text'],'first')
        self.assertIn('absence of overlap does not establish a safe cut',r['boundaries'][2]['note'])
        self.assertEqual(r['source_sha256'],self.sha)
        self.assertEqual(r['timeline_sha256'],hashlib.sha256(self.timeline.read_bytes()).hexdigest())
        self.assertEqual(r['asr_sha256'],hashlib.sha256(self.asr.read_bytes()).hexdigest())

    def test_invalid_or_missing_timing_never_yields_false_clear(self):
        self.a['segments'].append({'start_sec':0,'end_sec':1,'text':'bad','words':[{'start_sec':3,'end_sec':4,'text':'outside'}]})
        self.save();r=mod.report(self.source,self.timeline,self.asr)
        self.assertEqual(r['boundaries'][2]['risk'],'not_determinable_invalid_asr_times')
        self.assertIn('outside parent segment',r['timing_warnings'][0]['reason'])
        self.a['segments']=[];self.save();r=mod.report(self.source,self.timeline,self.asr)
        self.assertTrue(all(x['risk']=='not_determinable_invalid_asr_times' for x in r['boundaries']))
        self.a['segments']=[{'start_sec':0,'end_sec':.5,'text':'no word timestamps'}];self.save()
        r=mod.report(self.source,self.timeline,self.asr)
        self.assertTrue(all(x['risk']=='not_determinable_invalid_asr_times' for x in r['boundaries']))

    def test_mismatch_and_noncontiguous_groups_block(self):
        self.a['source_sha256']='0'*64;self.save()
        with self.assertRaisesRegex(ValueError,'Source hash mismatch'):mod.report(self.source,self.timeline,self.asr)
        self.a['source_sha256']=self.sha;self.a['video_id']='another';self.save()
        with self.assertRaisesRegex(ValueError,'Video ID mismatch'):mod.report(self.source,self.timeline,self.asr)
        self.a['video_id']='example';self.t['semantic_groups'][1]['start_ms']=1001;self.save()
        with self.assertRaisesRegex(ValueError,'contiguous'):mod.report(self.source,self.timeline,self.asr)

    def test_never_overwrite_and_report_only_no_input_mutation(self):
        before=[p.read_bytes() for p in (self.source,self.timeline,self.asr)]
        out=self.root/'out.json'
        args=['--source',str(self.source),'--timeline',str(self.timeline),'--asr',str(self.asr),'--output',str(out)]
        self.assertEqual(mod.main(args),0);saved=out.read_bytes()
        self.assertEqual(mod.main(args),1);self.assertEqual(out.read_bytes(),saved)
        self.assertEqual([p.read_bytes() for p in (self.source,self.timeline,self.asr)],before)


if __name__=='__main__':unittest.main()
