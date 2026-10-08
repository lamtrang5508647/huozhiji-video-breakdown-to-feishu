import copy,pathlib,sys,unittest,xml.etree.ElementTree as ET
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'skill/analyze-folder-videos-to-feishu/scripts'))
from phrase_annotation import validate_supervisor_review,validate_annotations
from repair_platform_chapter import verify

class PublicationReviewTests(unittest.TestCase):
 def setUp(self):
  self.r=dict(status='text_function_review_completed',reviewer_role='supervisor',analysis_sha256='a',manifest_sha256='b',unit_ids=['u1','u2'],scope='text semantic review; audio unverified')
 def test_current_supervisor_receipt(self):validate_supervisor_review(self.r,'a','b',['u1','u2'])
 def test_worker_pass_is_not_review(self):
  self.r['reviewer_role']='executor'
  with self.assertRaises(ValueError):validate_supervisor_review(self.r,'a','b',['u1','u2'])
 def test_changed_analysis_invalidates_review(self):
  with self.assertRaises(ValueError):validate_supervisor_review(self.r,'changed','b',['u1','u2'])
 def test_changed_manifest_invalidates_review(self):
  with self.assertRaises(ValueError):validate_supervisor_review(self.r,'a','changed',['u1','u2'])
 def test_missing_unit_invalidates_review(self):
  with self.assertRaises(ValueError):validate_supervisor_review(self.r,'a','b',['u1'])
 def test_same_colors_on_wrong_characters_rejected(self):
  wanted=ET.fromstring('<p><span text-color="red">pain</span><span text-color="green">brand</span></p>')
  actual=ET.fromstring('<p><span text-color="green">pain</span><span text-color="red">brand</span></p>')
  with self.assertRaises(AssertionError):verify([actual],[wanted])
 def test_server_merging_equal_adjacent_styles_allowed(self):
  wanted=ET.fromstring('<p><span text-color="red">pa</span><span text-color="red">in</span></p>')
  actual=ET.fromstring('<p id="server"><span text-color="rgb(216,57,49)">pain</span></p>')
  verify([actual],[wanted])
 def test_stale_condition_unit_is_rejected(self):
  from test_phrase_annotation import fixture
  a,_,_=fixture();a['units'][0]['phrase_runs'][0]['context_unit_ids']=['removed-condition']
  with self.assertRaises(ValueError):validate_annotations(a)
 def test_formula_condition_references_are_checked(self):
  from test_phrase_annotation import fixture
  a,_,_=fixture();a['source_formula'][0]['phrase_runs'][0]['context_unit_ids']=['unit-01'];validate_annotations(a)
  a['source_formula'][0]['phrase_runs'][0]['context_unit_ids']=['unknown-condition']
  with self.assertRaises(ValueError):validate_annotations(a)

if __name__=='__main__':unittest.main()
