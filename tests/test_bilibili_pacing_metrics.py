import importlib.util
from pathlib import Path
import unittest
p=Path(__file__).resolve().parents[1]/"skill/bilibili-editing-analysis/scripts/pacing_metrics.py"
spec=importlib.util.spec_from_file_location("pacing_metrics",p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Metrics(unittest.TestCase):
    def sample(self): return {"review_status":"reviewed","source_sha256":"a"*64,"coverage_start_sec":0,"coverage_end_sec":10,"shots":[{"start_sec":0,"end_sec":2},{"start_sec":2,"end_sec":5},{"start_sec":5,"end_sec":10}]}
    def test_metrics(self):
        v=m.summarize(self.sample());self.assertEqual(v["median_shot_sec"],3);self.assertEqual(v["cut_count_per_minute"],12);self.assertEqual(v["cut_count"],2)
    def test_reject_unreviewed(self):
        v=self.sample();v["review_status"]="candidate";self.assertRaises(ValueError,m.summarize,v)
    def test_reject_gap_and_overlap(self):
        for n in (1.9,2.1):
            v=self.sample();v["shots"][1]["start_sec"]=n;self.assertRaises(ValueError,m.summarize,v)
    def test_reject_incomplete_and_nan(self):
        for n in (9,float("nan")):
            v=self.sample();v["coverage_end_sec"]=n;self.assertRaises(ValueError,m.summarize,v)
    def test_one_shot_no_cut(self):
        v=self.sample();v["shots"]=[{"start_sec":0,"end_sec":10}];self.assertEqual(m.summarize(v)["cut_count"],0)
