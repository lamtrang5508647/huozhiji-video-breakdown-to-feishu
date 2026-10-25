"""Behavioral tests for persisted batch orchestration; fixtures are synthetic."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skill/analyze-folder-videos-to-feishu/scripts'
sys.path.insert(0, str(SCRIPTS))


class BatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.find_spec('workflow_batch')
        if spec:
            cls.batch = __import__('workflow_batch')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / 'work'
        self.source = self.root / 'a.mp4'
        self.source.write_bytes(b'synthetic-not-decodable-video')

    def require_module(self):
        self.assertTrue(hasattr(self, 'batch'), 'resumable workflow_batch is not implemented')

    def test_cross_batch_identical_sources_share_one_job_and_keep_aliases(self):
        self.require_module()
        other = self.root / 'b.mp4'
        other.write_bytes(self.source.read_bytes())
        self.batch.add_sources(self.workspace, [self.source], 'first')
        result = self.batch.add_sources(self.workspace, [other], 'second')
        self.assertEqual(result['jobs_total'], 1)
        state = self.batch.load_state(self.workspace)
        job = next(iter(state['jobs'].values()))
        self.assertEqual(set(job['batches']), {'first', 'second'})
        self.assertEqual(len(job['source_paths']), 2)
        self.assertEqual(self.source.read_bytes(), b'synthetic-not-decodable-video')

    def test_invalid_media_fails_and_is_only_retried_when_requested(self):
        self.require_module()
        self.batch.add_sources(self.workspace, [self.source], 'first')
        self.batch.run_batch(self.workspace, ffmpeg='missing-ffmpeg-for-test')
        job = next(iter(self.batch.load_state(self.workspace)['jobs'].values()))
        self.assertEqual(job['stages']['prepare']['status'], 'failed')
        self.assertEqual(job['stages']['prepare']['attempts'], 1)
        self.batch.run_batch(self.workspace, ffmpeg='missing-ffmpeg-for-test')
        job = next(iter(self.batch.load_state(self.workspace)['jobs'].values()))
        self.assertEqual(job['stages']['prepare']['attempts'], 1)
        self.batch.run_batch(self.workspace, ffmpeg='missing-ffmpeg-for-test', retry_failed=True)
        job = next(iter(self.batch.load_state(self.workspace)['jobs'].values()))
        self.assertEqual(job['stages']['prepare']['attempts'], 2)
        self.assertEqual(len(job['stages']['prepare']['history']), 2)

    def test_changed_source_is_not_run_under_old_identity(self):
        self.require_module()
        self.batch.add_sources(self.workspace, [self.source], 'first')
        self.source.write_bytes(b'different-synthetic-video')
        result = self.batch.run_batch(self.workspace, ffmpeg='missing-ffmpeg-for-test')
        job = result['jobs'][0]
        self.assertEqual(job['state'], 'source_unavailable')
        self.assertIn('source', job['next_action'].lower())
        self.assertEqual(next(iter(self.batch.load_state(self.workspace)['jobs'].values()))['stages']['prepare']['attempts'], 0)

    def test_attach_rejects_cross_video_and_does_not_mark_review_complete(self):
        self.require_module()
        result = self.batch.add_sources(self.workspace, [self.source], 'first')
        key = result['jobs'][0]['source_id']
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps({'source': {'path': str(self.source), 'sha256': 'f'*64}}))
        with self.assertRaises(ValueError):
            self.batch.attach(self.workspace, key, manifest)
        state = self.batch.load_state(self.workspace)
        self.assertNotEqual(state['jobs'][key]['stages']['review']['status'], 'complete')

    def test_stop_file_prevents_new_jobs(self):
        self.require_module()
        self.batch.add_sources(self.workspace, [self.source], 'first')
        stop = self.root / 'STOP'
        stop.touch()
        result = self.batch.run_batch(self.workspace, stop_file=stop)
        self.assertTrue(result['stopped'])
        job = next(iter(self.batch.load_state(self.workspace)['jobs'].values()))
        self.assertEqual(job['stages']['prepare']['attempts'], 0)

    def test_lock_is_exclusive_and_released(self):
        self.require_module()
        with self.batch.workspace_lock(self.workspace):
            with self.assertRaises(ValueError):
                with self.batch.workspace_lock(self.workspace):
                    self.fail('concurrent mutation acquired lock')
        with self.batch.workspace_lock(self.workspace):
            pass

    def test_actual_worker_timeout_and_interrupted_receipt_recovery(self):
        attempt = self.root / 'timeout'
        attempt.mkdir()
        result = self.batch.execute([sys.executable, '-c', 'import time; time.sleep(30)'], attempt, 0.15)
        self.assertFalse(result['ok'])
        self.assertIn('timed out', result['reason'])
        self.assertTrue((attempt / 'stderr.log').is_file())
        self.batch.add_sources(self.workspace, [self.source])
        state = self.batch.load_state(self.workspace)
        job = next(iter(state['jobs'].values()))
        job['stages']['prepare'].update(status='running', attempts=1)
        self.batch.save(self.workspace, state)
        recovered = self.batch.status(self.workspace)
        self.assertEqual(recovered['jobs'][0]['state'], 'failed_prepare')
        self.assertIn('Previous owner stopped', recovered['jobs'][0]['next_action'])

    def test_missing_analysis_and_changed_review_cannot_use_old_export(self):
        self.require_module()
        self.batch.add_sources(self.workspace, [self.source], 'first')
        state = self.batch.load_state(self.workspace)
        job = next(iter(state['jobs'].values()))
        receipt = self.root / 'review.json'
        receipt.write_text('{}')
        job['attachments']['review'] = {'path': str(receipt), 'sha256': self.batch.sha(receipt)}
        job['stages']['review']['status'] = 'complete'
        job['stages']['export']['status'] = 'complete'
        self.batch.save(self.workspace, state)
        receipt.write_text('{"changed": true}')
        result = self.batch.status(self.workspace)
        self.assertEqual(result['jobs'][0]['state'], 'waiting_reattach')
        job = next(iter(self.batch.load_state(self.workspace)['jobs'].values()))
        self.assertEqual(job['stages']['export']['status'], 'stale')

    def test_stage_inputs_change_invalidates_old_outputs(self):
        self.require_module()
        self.assertTrue(hasattr(self.batch, 'stage_inputs'), 'stage dependency fingerprints are missing')
        self.batch.add_sources(self.workspace, [self.source], 'first')
        job = next(iter(self.batch.load_state(self.workspace)['jobs'].values()))
        model = self.root / 'model.bin'
        model.write_bytes(b'first-model')
        first = self.batch.stage_inputs(job, 'asr', 'ffmpeg', 'ffprobe', str(model))
        model.write_bytes(b'second-model')
        second = self.batch.stage_inputs(job, 'asr', 'ffmpeg', 'ffprobe', str(model))
        self.assertNotEqual(first, second)

    def test_cached_model_alias_tracks_actual_bytes_and_waiting_reason(self):
        self.batch.add_sources(self.workspace, [self.source])
        job = next(iter(self.batch.load_state(self.workspace)['jobs'].values()))
        cache = self.root / 'cache/whisper'
        cache.mkdir(parents=True)
        model = cache / 'small.pt'
        model.write_bytes(b'first')
        with patch.dict('os.environ', {'XDG_CACHE_HOME': str(cache.parent)}):
            first = self.batch.stage_inputs(job, 'asr', 'ffmpeg', 'ffprobe', 'small')
            model.write_bytes(b'second')
            second = self.batch.stage_inputs(job, 'asr', 'ffmpeg', 'ffprobe', 'small')
        self.assertNotEqual(first, second)
        job['stages']['prepare']['status'] = 'complete'
        job['stages']['asr'].update(status='waiting', reason='Supply local model')
        self.assertEqual(self.batch.summary({'jobs': {'test': job}})['jobs'][0]['state'], 'waiting_asr')

    def test_remote_complete_never_hides_missing_source(self):
        self.batch.add_sources(self.workspace, [self.source])
        state = self.batch.load_state(self.workspace)
        job = next(iter(state['jobs'].values()))
        job['stages']['publish']['status'] = 'complete'
        self.batch.save(self.workspace, state)
        self.source.unlink()
        self.assertEqual(self.batch.status(self.workspace)['jobs'][0]['state'], 'source_unavailable')

    def test_zero_exit_without_required_stage_artifacts_fails(self):
        self.batch.add_sources(self.workspace, [self.source])
        with patch.object(self.batch, 'execute', return_value={'ok': True, 'seconds': 0}):
            result = self.batch.run_batch(self.workspace)
        self.assertEqual(result['jobs'][0]['state'], 'failed_prepare')

    def test_stopped_remote_command_does_not_mark_publication_complete(self):
        self.batch.add_sources(self.workspace, [self.source])
        state = self.batch.load_state(self.workspace)
        job = next(iter(state['jobs'].values()))
        manifest = self.root / 'manifest.json'
        manifest.write_text('{"video_id":"synthetic"}')
        job['attachments']['manifest'] = {'path': str(manifest), 'sha256': self.batch.sha(manifest)}
        job['stages']['export']['status'] = 'complete'
        self.batch.save(self.workspace, state)
        plan = self.root / 'plan.json'
        plan.write_text('{"video_id":"synthetic"}')
        def stopped(command, attempt, timeout):
            (attempt / 'stdout.log').write_text('{"status":"StoppedSafely"}')
            return {'ok': True, 'seconds': 0}
        with patch.object(self.batch, 'execute', side_effect=stopped):
            result = self.batch.publish(self.workspace, job['source_id'], plan, self.root, self.batch.sha(plan), True)
        self.assertFalse(result['command_succeeded'])
        self.assertEqual(result['jobs'][0]['stages']['publish']['status'], 'uncertain')

    def test_publication_accepts_explicit_common_provenance_workspace(self):
        self.batch.add_sources(self.workspace, [self.source])
        state = self.batch.load_state(self.workspace)
        job = next(iter(state['jobs'].values()))
        manifest = self.root / 'manifest.json'
        manifest.write_text('{"video_id":"synthetic"}')
        job['attachments']['manifest'] = {'path': str(manifest), 'sha256': self.batch.sha(manifest)}
        job['stages']['export']['status'] = 'complete'
        self.batch.save(self.workspace, state)
        plan = self.root / 'plan.json'
        plan.write_text('{"video_id":"synthetic"}')
        with patch.object(self.batch, 'execute', return_value={'ok': True, 'seconds': 0}) as execute:
            self.batch.publish(self.workspace, job['source_id'], plan, self.root, provenance_workspace=self.root)
        command = execute.call_args.args[0]
        self.assertEqual(command[command.index('--workspace') + 1], str(self.root.resolve()))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Media tools required')
    def test_actual_review_export_contains_semantic_and_numbered_shot_clips(self):
        demo_path = SCRIPTS.parents[2] / 'examples/synthetic/build.py'
        spec = importlib.util.spec_from_file_location('batch_synthetic_demo', demo_path)
        demo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(demo)
        output = self.root / 'fixture'
        demo.build(output, SCRIPTS)
        self.batch.add_sources(self.workspace, [output / 'source.mp4'])
        key = next(iter(self.batch.load_state(self.workspace)['jobs']))
        self.batch.attach(self.workspace, key, output / 'script_manifest.json', output / 'demo-review.json')
        result = self.batch.run_batch(self.workspace, through='export')
        self.assertEqual(result['jobs'][0]['state'], 'local_export_complete', result)
        job = self.batch.load_state(self.workspace)['jobs'][key]
        artifact = Path(job['stages']['export']['output_dir'])
        self.assertEqual(len(list((artifact / 'semantic/clips').glob('*.mp4'))), 2)
        self.assertEqual(len(list((artifact / 'shots/clips').glob('*.mp4'))), 2)
        self.batch.run_batch(self.workspace, through='export')
        self.assertEqual(self.batch.load_state(self.workspace)['jobs'][key]['stages']['export']['attempts'], 1)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required for actual synthetic preparation')
    def test_real_prepare_resume_and_corrupt_output_rebuild(self):
        self.require_module()
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
                        'color=c=red:s=96x96:r=10:d=1','-c:v','libx264','-y',str(self.source)],check=True)
        self.batch.add_sources(self.workspace, [self.source], 'actual')
        result = self.batch.run_batch(self.workspace, through='export')
        job = next(iter(self.batch.load_state(self.workspace)['jobs'].values()))
        self.assertEqual(job['stages']['prepare']['status'], 'complete')
        self.assertEqual(job['stages']['analysis']['status'], 'waiting')
        self.assertEqual(job['stages']['review']['status'], 'pending')
        self.assertEqual(result['jobs'][0]['state'], 'waiting_analysis')
        self.batch.run_batch(self.workspace)
        job = next(iter(self.batch.load_state(self.workspace)['jobs'].values()))
        self.assertEqual(job['stages']['prepare']['attempts'], 1)
        evidence_file = next(Path(p) for p in job['stages']['prepare']['outputs'] if p.endswith('.png') or p.endswith('.jpg'))
        evidence_file.write_bytes(b'corrupted-output')
        self.batch.run_batch(self.workspace)
        job = next(iter(self.batch.load_state(self.workspace)['jobs'].values()))
        self.assertEqual(job['stages']['prepare']['status'], 'complete')
        self.assertEqual(job['stages']['prepare']['attempts'], 2)
        self.assertEqual(evidence_file.read_bytes(), b'corrupted-output', 'old artifacts should be preserved')


if __name__ == '__main__':
    unittest.main()
