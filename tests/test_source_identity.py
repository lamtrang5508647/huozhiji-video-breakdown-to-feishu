"""Synthetic identity/mapping regressions; these never call Feishu."""
import copy
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skill/analyze-folder-videos-to-feishu/scripts'))
from source_identity import canonical_schema, source_identity, verify_source
import sync_video_base as sync
import build_video_base_plan as builder


class IdentityTests(unittest.TestCase):
    def test_bilibili_compatibility_is_exact_and_domain_bound(self):
        identity = source_identity('BVabcdefghij', {})
        self.assertEqual(identity['platform'], 'B站')
        verify_source(identity, platform='B站', urls=['https://bilibili.com/video/BVabcdefghij?share=1'])
        for url in ('https://evil.invalid/video/BVabcdefghij',
                    'https://www.bilibili.com/video/BVabcdefghij-extra',
                    'https://www.bilibili.com/video/BVabcdefghik'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                verify_source(identity, platform='B站', urls=[url])

    def test_bilibili_part_is_content_identity_and_share_parameters_are_not(self):
        identity = source_identity('BVabcdefghij', {'url': 'https://bilibili.com/video/BVabcdefghij?p=2', 'platform': 'B站'})
        verify_source(identity, platform='B站', urls=[identity['url'] + '&share_source=test'])
        for suffix in ('', '?p=1', '?p=3', '?p=2&p=1', '?p=0', '?p=unknown'):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                verify_source(identity, platform='B站', urls=['https://bilibili.com/video/BVabcdefghij' + suffix])

    def test_other_platform_needs_explicit_platform_and_exact_id_token(self):
        source = {'url': 'https://example.invalid/videos/clip-001'}
        with self.assertRaisesRegex(ValueError, 'explicit source platform'):
            source_identity('clip-001', source)
        source['platform'] = 'Example'
        identity = source_identity('clip-001', source)
        verify_source(identity, platform='Example', urls=[source['url']])
        for url in ('https://example.invalid/videos/xclip-001x',
                    'https://example.invalid/videos/other?note=xclip-001x'):
            with self.assertRaisesRegex(ValueError, 'exact video_id token'):
                source_identity('clip-001', dict(source, url=url))
        self.assertEqual(source_identity('clip-001', dict(source, url='https://example.invalid/watch?id=clip-001'))['video_id'], 'clip-001')

    def test_explicit_stable_field_can_bind_short_url_but_must_match(self):
        identity = source_identity('clip-001', {'platform': 'Example', 'url': 'https://example.invalid/share/abc'}, id_field='来源视频ID')
        verify_source(identity, platform='Example', urls=[identity['url']], stable_id='clip-001')
        for stable_id in (None, 'clip-002', 'xclip-001x'):
            with self.assertRaises(ValueError):
                verify_source(identity, platform='Example', urls=[identity['url']], stable_id=stable_id)

    def test_generic_record_cannot_change_platform_url_or_add_conflicting_url(self):
        identity = source_identity('clip-001', {'platform': 'Example', 'url': 'https://example.invalid/videos/clip-001'})
        for platform, urls in [('Other', [identity['url']]), ('Example', []),
                               ('Example', ['https://else.invalid/videos/clip-001']),
                               ('Example', [identity['url'], 'https://example.invalid/videos/clip-002'])]:
            with self.assertRaises(ValueError):
                verify_source(identity, platform=platform, urls=urls)

    def test_declarations_conflicting_or_unsafe_are_rejected(self):
        for url in ('http://example.invalid/clip-001', 'https://user:pass@example.invalid/clip-001',
                    'https://example.invalid/clip-001#other', 'https://example.invalid/clip-001\n'):
            with self.assertRaises(ValueError):
                source_identity('clip-001', {'platform': 'Example', 'url': url})
        with self.assertRaises(ValueError):
            source_identity('clip-001', {'platform': 'Example', 'url': 'https://example.invalid/clip-001'}, platform='Other')
        with self.assertRaises(ValueError):
            source_identity('BVabcdefghij', {'platform': 'Example', 'url': 'https://bilibili.com/video/BVabcdefghij'})


class WechatExportIdentityTests(unittest.TestCase):
    def setUp(self):
        self.export_id = 'synthetic+/platform-export=='
        self.key = 'wxexp_' + base64.urlsafe_b64encode(self.export_id.encode('utf-8')).decode('ascii').rstrip('=')
        self.url = 'https://channels.weixin.qq.com/finder-preview/pages/feed?' + urlencode({'eid': self.export_id, 'token': 'synthetic'})
        self.source = {'platform': '视频号', 'platform_key': 'wechat_channels', 'url': self.url,
                       'source_url': self.url, 'video_id': self.key,
                       'input_url': 'https://weixin.qq.com/sph/syntheticShare', 'platform_export_id': self.export_id,
                       'video_id_kind': 'encoded_platform_export_id', 'native_id': self.export_id,
                       'native_id_kind': 'platform_export_id', 'identity_stability_verified': False}

    def test_explicit_export_identity_is_reversible_and_reconstructs_exactly(self):
        identity = source_identity(self.key, self.source)
        self.assertEqual(identity['video_id'], self.key)
        self.assertEqual(identity['native_id'], self.export_id)
        self.assertEqual(identity['platform_export_id'], self.export_id)
        self.assertEqual(identity['input_url'], self.source['input_url'])
        self.assertEqual(identity['native_id_kind'], 'platform_export_id')
        self.assertEqual(identity['video_id_kind'], 'encoded_platform_export_id')
        self.assertFalse(identity['identity_stability_verified'])
        self.assertEqual(source_identity(self.key, identity), identity)
        verify_source(identity, platform='视频号', urls=[self.url])

    def test_export_branch_requires_explicit_matching_types_and_platform(self):
        for field in ('video_id_kind', 'native_id_kind', 'native_id', 'identity_stability_verified', 'input_url', 'platform_export_id'):
            changed = dict(self.source)
            del changed[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                source_identity(self.key, changed, id_field='来源视频ID')
        for changes in ({'platform': 'Example'}, {'platform_key': 'douyin'},
                        {'native_id_kind': 'numeric_object_id'}, {'video_id_kind': 'native'},
                        {'identity_stability_verified': True}, {'identity_stability_verified': 0},
                        {'platform_export_id': 'different'}, {'input_url': 'https://evil.invalid/share'},
                        {'native_id': self.export_id + 'other'}, {'video_id': self.key + 'other'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                source_identity(self.key, {**self.source, **changes}, id_field='来源视频ID')

    def test_export_key_must_encode_exact_raw_value_without_padding(self):
        for key in (self.key + '=', self.key + 'A', 'wxexp_fixture', 'numeric-object-id'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                source_identity(key, self.source)

    def test_export_canonical_origin_path_and_unique_eid_are_required(self):
        bad_urls = ('https://channels.weixin.qq.com.evil.invalid/finder-preview/pages/feed?eid=' + self.key,
                    'https://channels.weixin.qq.com/web/pages/feed?' + urlencode({'eid': self.export_id}),
                    'https://channels.weixin.qq.com:444/finder-preview/pages/feed?' + urlencode({'eid': self.export_id}),
                    self.url + '&eid=' + urlencode({'x': self.export_id})[2:],
                    self.url + '&token=other', self.url.replace('&token=synthetic', ''),
                    self.url.replace('token=synthetic', 'token='),
                    self.url.replace('eid=', 'other='),
                    self.url.replace('synthetic%2B%2Fplatform-export%3D%3D', 'unrelated'))
        for url in bad_urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                source_identity(self.key, {**self.source, 'url': url, 'source_url': url}, id_field='来源视频ID')
        with self.assertRaises(ValueError):
            source_identity(self.key, {**self.source, 'source_url': self.url + '&other=1'})

    def test_export_record_still_requires_exact_source_url_with_id_field(self):
        identity = source_identity(self.key, self.source, id_field='来源视频ID')
        verify_source(identity, platform='视频号', urls=[self.url], stable_id=self.key)
        for urls in ([], [self.url + '&other=1'], [self.url, self.url + '&other=1']):
            with self.subTest(urls=urls), self.assertRaises(ValueError):
                verify_source(identity, platform='视频号', urls=urls, stable_id=self.key)

    def test_regular_numeric_wechat_identity_is_unchanged(self):
        url = 'https://channels.weixin.qq.com/web/pages/feed?object_id=1234567890123456789'
        identity = source_identity('1234567890123456789', {'platform': '视频号', 'url': url})
        self.assertEqual(identity, {'platform': '视频号', 'url': url, 'video_id': '1234567890123456789', 'id_field': None})


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.raw = {'Source URL': {'id': 'fldurl', 'name': 'Source URL', 'type': 'text'}}
        self.profile = {'来源链接': {'id': 'fldurl', 'name': 'Source URL', 'type': 'text'}}
        self.schema = canonical_schema(self.raw, self.profile, 'videos')

    def test_rename_mapping_preserves_logical_name_and_validates_id_type(self):
        self.assertEqual(self.schema['来源链接']['name'], 'Source URL')
        for change in ({'id': 'fldother'}, {'type': 'number'}, {'name': 'missing'}):
            bad = copy.deepcopy(self.profile)
            bad['来源链接'].update(change)
            with self.assertRaises(ValueError):
                canonical_schema(self.raw, bad, 'videos')
        duplicate = dict(self.profile, duplicate=self.profile['来源链接'])
        with self.assertRaisesRegex(ValueError, 'Multiple logical'):
            canonical_schema(self.raw, duplicate, 'videos')

    def test_read_decode_and_write_use_verified_live_field_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = object.__new__(sync.Lark)
            api.workspace = Path(tmp)
            api.schemas = {'tblvideos': self.schema}
            calls = []
            def fake_call(command, table, args, **kwargs):
                calls.append((command, table, args, kwargs))
                if command == '+record-get':
                    return {'record': {'record_id': 'recsource', 'fields': {'Source URL': 'value'}}}, {}
                payload = json.loads((api.workspace / args[args.index('--json') + 1][1:]).read_text())
                self.assertEqual(payload, {'fldurl': 'value'})
                return {}, {}
            api._call = fake_call
            row = api.get('tblvideos', 'recsource', ['来源链接'], self.schema)
            self.assertEqual(row['fields'], {'来源链接': 'value'})
            self.assertIn('fldurl', calls[0][2])
            api.upsert('tblvideos', 'recsource', {'来源链接': 'value'}, apply=False)
            with self.assertRaises(sync.SyncError):
                api.upsert('tblvideos', 'recsource', {'unknown': 'value'}, apply=False)

    def test_builder_reads_renamed_saved_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'fields.json').write_text(json.dumps({'data': {'fields': list(self.raw.values())}}))
            project = {'tables': {'videos': {'fields_snapshot': 'fields.json', 'fields': self.profile}}}
            self.assertEqual(builder.fields_from_schema(root, project, 'videos'), self.schema)

    def test_response_identity_missing_or_different_rejected(self):
        for identity in (None, 'bot'):
            with self.assertRaises(sync.SyncError):
                sync.unwrap_cli(json.dumps({'ok': True, 'data': {}, 'identity': identity}), 0, 'user', 'fixture')
        self.assertEqual(sync.unwrap_cli(json.dumps({'ok': True, 'data': {}, 'identity': 'user'}), 0, 'user', 'fixture')[0], {})


class SyncSourceTests(unittest.TestCase):
    def fixture_plan(self):
        identity = source_identity('clip-001', {'platform': 'Example', 'url': 'https://example.invalid/videos/clip-001'})
        plan = {'base_id': 'baseFixture', 'video_id': 'clip-001', 'source_record_id': 'recsource',
                'product_record_id': 'recproduct', 'source_sha256': 'a' * 64, 'revision': 'r1',
                'source_identity': identity,
                'main_fields': {'所属产品': [{'id': 'recproduct'}], '来源平台': 'Example', '来源链接': identity['url']},
                'materials': [{'key': 'clip-001:shot1', 'fields': {'素材ID': 'clip-001:shot1',
                    '来源记录': 'recsource', '原视频关联': [{'id': 'recsource'}]}, 'attachments': []}],
                'segments': [{'key': 'clip-001:seg1', 'fields': {'脚本段键': 'clip-001:seg1',
                    '原视频': [{'id': 'recsource'}], '所属产品': [{'id': 'recproduct'}]},
                    'source_shot_keys': ['clip-001:shot1'], 'attachments': []}]}
        schemas = {}
        for name, fields in [('videos', plan['main_fields']), ('materials', plan['materials'][0]['fields']),
                             ('segments', plan['segments'][0]['fields'])]:
            schemas[name] = {key: {'id': 'fld' + str(i), 'name': 'Renamed ' + str(i),
                                'type': 'link' if isinstance(value, list) else 'text'}
                             for i, (key, value) in enumerate(fields.items())}
        return plan, schemas

    def test_plan_parser_accepts_non_bilibili_and_rejects_identity_tampering(self):
        plan, schemas = self.fixture_plan()
        project = {'base_id': 'baseFixture'}
        # Artifact-integrity/rebuild behavior has separate complete-bundle tests.
        with tempfile.TemporaryDirectory() as tmp, patch.object(sync, 'verify_plan_provenance'):
            parsed = sync.parse_plan(json.dumps(plan).encode(), Path(tmp), schemas, project)
            self.assertEqual(parsed['identity']['platform'], 'Example')
            for change in ('missing', 'video_id', 'platform', 'url', 'id_field'):
                bad = copy.deepcopy(plan)
                if change == 'missing':
                    bad.pop('source_identity')
                else:
                    bad['source_identity'][change] = 'different'
                with self.subTest(change=change), self.assertRaises(sync.SyncError):
                    sync.parse_plan(json.dumps(bad).encode(), Path(tmp), schemas, project)

    def test_plan_parser_keeps_wrong_source_product_and_script_write_guards(self):
        plan, schemas = self.fixture_plan()
        project = {'base_id': 'baseFixture'}
        with tempfile.TemporaryDirectory() as tmp, patch.object(sync, 'verify_plan_provenance'):
            for mode in ('source', 'product', 'script'):
                bad = copy.deepcopy(plan)
                if mode == 'source':
                    bad['materials'][0]['fields']['原视频关联'] = [{'id': 'recwrong'}]
                elif mode == 'product':
                    bad['main_fields']['所属产品'] = [{'id': 'recwrong'}]
                else:
                    bad['main_fields']['视频脚本'] = 'unauthorized overwrite'
                with self.subTest(mode=mode), self.assertRaises(sync.SyncError):
                    sync.parse_plan(json.dumps(bad).encode(), Path(tmp), schemas, project)

    def test_plan_without_exporter_provenance_is_rejected(self):
        plan, schemas = self.fixture_plan()
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(sync.SyncError, 'lacks export_provenance'):
            sync.parse_plan(json.dumps(plan).encode(), Path(tmp), schemas, {'base_id': 'baseFixture'})

    def test_preflight_reads_configured_source_id_and_rejects_mismatch(self):
        identity = source_identity('clip-001', {'platform': 'Example', 'url': 'https://example.invalid/share/abc'}, id_field='来源视频ID')
        project = {'tables': {'videos': {'id': 'tblvideos'}, 'products': {'id': 'tblproducts'}}}
        source_fields = {'来源平台': 'Example', '来源链接': identity['url'], '所属产品': [{'id': 'recproduct'}],
                         '视频脚本': 'unchanged source', '来源视频ID': 'clip-001'}
        schemas = {'videos': {k: {} for k in source_fields}, 'products': {}}
        plan = {'identity': identity, 'video_id': 'clip-001', 'plan': {'source_record_id': 'recsource', 'product_record_id': 'recproduct'}}
        api = Mock()
        def get(table, record, fields, schema):
            return {'record_id': record, 'fields': dict(source_fields) if table == 'tblvideos' else {}}
        api.get.side_effect = get
        row, digest = sync.verify_source_and_product(api, project, schemas, plan)
        self.assertEqual(digest, hashlib.sha256(b'unchanged source').hexdigest())
        self.assertIn('来源视频ID', api.get.call_args_list[0].args[2])
        source_fields['来源视频ID'] = 'clip-002'
        with self.assertRaisesRegex(sync.SyncError, 'stable ID differs'):
            sync.verify_source_and_product(api, project, schemas, plan)


if __name__ == '__main__':
    unittest.main()
