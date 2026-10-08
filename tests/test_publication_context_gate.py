"""Publication must reject incomplete or unresolved reviews before remote work."""
import hashlib,json,pathlib,sys,tempfile,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'skill/analyze-folder-videos-to-feishu/scripts'))
from publication_review import validate_publication_review
from test_context_review import make_analysis,make_receipt,analysis_bytes_sha256

class PublicationContextGateTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.path=pathlib.Path(self.temp.name)/'context-review.json'
  self.analysis=make_analysis();self.record=make_receipt(self.analysis)
  self.asha=analysis_bytes_sha256(self.analysis)
  self.review=dict(status='text_function_review_completed',reviewer_role='supervisor',analysis_sha256=self.asha,manifest_sha256='manifest',unit_ids=[u['id'] for u in self.analysis['units']],scope='Contextual text decisions only; source claims not verified')
  self.bind()
 def bind(self):
  self.path.write_text(json.dumps(self.record,ensure_ascii=False),encoding='utf8')
  self.review['context_review_sha256']=hashlib.sha256(self.path.read_bytes()).hexdigest()
 def check(self):return validate_publication_review(self.review,self.analysis,self.asha,'manifest',self.path)
 def test_complete_record_reports_integrity_only(self):
  self.assertEqual(self.check()['semantic_truth'],'not_assessed')
 def test_old_global_receipt_is_insufficient(self):
  self.path.unlink()
  with self.assertRaisesRegex(ValueError,'context-review.json is required'):self.check()
 def test_changed_record_needs_fresh_supervising_approval(self):
  self.record['unit_reviews'][0]['reading_rationale']='新增语境说明改变了审阅记录，需要重新监督确认'
  self.path.write_text(json.dumps(self.record,ensure_ascii=False),encoding='utf8')
  with self.assertRaisesRegex(ValueError,'exact context-review.json'):self.check()
 def test_valid_but_unresolved_record_cannot_publish(self):
  self.record['unit_reviews'][0]['review_decision']='uncertain';self.bind()
  with self.assertRaisesRegex(ValueError,'unresolved decisions'):self.check()
 def test_executor_cannot_approve_own_record(self):
  self.review['reviewer_role']='executor'
  with self.assertRaisesRegex(ValueError,'supervising text review'):self.check()

if __name__=='__main__':unittest.main()
