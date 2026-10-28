"""Fresh-project and identity checks with synthetic data and mocked CLI output."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skill/analyze-folder-videos-to-feishu/scripts'))
import check_project as check


class ProjectCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'config').mkdir()
        (self.root / 'state').mkdir()
        self.cfg = {'schema_version': 1, 'mode': 'feishu', 'base_id': 'baseFixture',
                    'base_url': 'https://example.invalid/base/baseFixture', 'app_id': 'cli_fixture',
                    'identity': 'user', 'registry_path': 'state/platform-document-registry.json',
                    'network': {'ignore_proxy_env': False},
                    'tables': {name: {'id': 'tbl' + name} for name in ('videos', 'materials', 'products', 'platforms', 'segments')}}
        self.registry = {'base_id': 'baseFixture', 'groups': {}}
        self.save()

    def save(self):
        (self.root / 'config/project.json').write_text(json.dumps(self.cfg), encoding='utf-8')
        (self.root / 'state/platform-document-registry.json').write_text(json.dumps(self.registry), encoding='utf-8')

    def validate(self):
        self.save()
        return check.validate(self.root, self.root / 'receipt.json')

    def test_fresh_feishu_registry_and_empty_group_are_allowed(self):
        _, _, details = self.validate()
        self.assertEqual(details['docs'], {})
        self.assertEqual(details['owner_checks_unconfigured'], ['base'])
        self.registry['groups']['baseFixture::product-1::platform-1'] = {'videos': {}}
        self.assertEqual(self.validate()[2]['video_count'], 0)

    def test_existing_video_still_requires_registered_document_and_headings(self):
        self.registry['groups']['baseFixture::product-1::platform-1'] = {'videos': {'clip-001': {}}}
        with self.assertRaisesRegex(check.CheckError, 'doc token'):
            self.validate()
        self.registry['groups']['baseFixture::product-1::platform-1'].update(doc_token='docFixture', doc_title='Fixture')
        with self.assertRaisesRegex(check.CheckError, 'H1'):
            self.validate()

    def test_local_config_passes_without_cli_or_media_executables(self):
        self.cfg.update(mode='local', base_id=None, base_url=None, app_id=None, tables={})
        self.registry['base_id'] = None
        self.save()
        with patch.object(sys, 'argv', ['check_project', '--project', str(self.root), '--output', 'receipt.json']), \
             patch.object(check.shutil, 'which', return_value=None), \
             patch.object(check.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check.main(), 0)
            run.assert_not_called()
        receipt = json.loads((self.root / 'receipt.json').read_text(encoding='utf-8'))
        self.assertFalse(receipt['integration_configured'])
        self.assertFalse(receipt['dependencies']['lark_cli'])
        self.assertEqual(receipt['identity'], 'user')
        self.assertTrue(any('No live' in item for item in receipt['unverified']))

    def test_partial_remote_config_and_hidden_target_fail(self):
        self.cfg['tables'] = {}
        with self.assertRaisesRegex(check.CheckError, 'table map incomplete'):
            self.validate()
        self.cfg['mode'] = 'local'
        with self.assertRaisesRegex(check.CheckError, 'conceal'):
            self.validate()

    def test_user_cli_arguments_and_response_identity_are_bound(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({'ok': True, 'identity': 'user',
                    'data': {'tables': [{'id': 'tblfixture'}], 'has_more': False}}), '')
        with patch.object(check.subprocess, 'run', return_value=result) as run:
            self.assertEqual(check.read_tables('fake-cli', 'baseFixture', self.root, {}, 'user'), ({'tblfixture'}, 1))
            argv = run.call_args.args[0]
            self.assertEqual(argv[argv.index('--as') + 1], 'user')
        for identity in ('bot', None):
            result.stdout = json.dumps({'ok': True, 'identity': identity, 'data': {}})
            with patch.object(check.subprocess, 'run', return_value=result), self.assertRaisesRegex(check.CheckError, 'unexpected API identity'):
                check.call('fake-cli', [], self.root, {}, 'fixture', 'user')

    def test_owner_absent_is_reported_unverified_and_configured_mismatch_fails(self):
        cfg, _, details = self.validate()
        meta = {'baseFixture': {'doc_type': 'bitable', 'owner_id': 'ou_actual'}}
        with patch.object(check, 'verify_app', return_value=True), \
             patch.object(check, 'read_tables', return_value=(set(details['table_ids'].values()), 1)), \
             patch.object(check, 'read_metas', return_value=meta):
            result = check.live('fake-cli', self.root, cfg, details)
            self.assertFalse(result['base_owner_verified'])
            self.assertEqual(result['owner_checks_unconfigured'], ['base'])
            details['base_owner'] = 'ou_expected'
            with self.assertRaisesRegex(check.CheckError, 'Base owner mismatch'):
                check.live('fake-cli', self.root, cfg, details)
            details['base_owner'] = 'ou_actual'
            self.assertTrue(check.live('fake-cli', self.root, cfg, details)['base_owner_verified'])

    def test_state_output_guard_remains(self):
        self.assertTrue(check.protected_output(self.root, self.root / 'state/receipt.json', self.cfg))
        self.assertTrue(check.protected_output(self.root, self.root / 'config/project.json', self.cfg))
        self.cfg['video_index_path'] = 'metadata/video-index.json'
        self.assertTrue(check.protected_output(self.root, self.root / 'metadata/video-index.json', self.cfg))

    def project_alias(self):
        alias = self.root / 'project-alias'
        try:
            alias.symlink_to(self.root.resolve(), target_is_directory=True)
        except OSError:
            self.skipTest('Host does not permit directory symlink creation')
        return alias

    def test_symlink_project_root_resolves_registry_to_same_file(self):
        alias = self.project_alias()
        expected = (self.root / self.cfg['registry_path']).resolve()
        self.assertEqual(check.project_path(alias, self.cfg['registry_path']), expected)
        self.assertEqual(check.validate(alias, alias / 'receipt.json')[2]['docs'], {})

    def test_symlink_project_root_keeps_state_output_protected(self):
        alias = self.project_alias()
        self.assertTrue(check.protected_output(alias, alias / 'state/receipt.json', self.cfg))
        self.assertTrue(check.protected_output(alias, self.root / 'state/receipt.json', self.cfg))
        self.assertFalse(check.protected_output(alias, alias / 'receipt.json', self.cfg))

    def test_symlink_project_root_still_rejects_escape(self):
        alias = self.project_alias()
        with tempfile.TemporaryDirectory() as outside:
            link = self.root / 'outside'
            link.symlink_to(Path(outside).resolve(), target_is_directory=True)
            for value in ('../escape.json', str(Path(outside) / 'registry.json'), 'outside/registry.json'):
                with self.subTest(value=value), self.assertRaisesRegex(check.CheckError, 'escapes'):
                    check.project_path(alias, value)


if __name__ == '__main__':
    unittest.main()
