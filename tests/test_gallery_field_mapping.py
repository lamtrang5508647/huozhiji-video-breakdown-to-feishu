"""Exercise gallery acceptance with custom live field names and IDs; no network."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skill/analyze-folder-videos-to-feishu/scripts'))
import verify_material_gallery as gallery
from sync_video_base import decode_records


class GalleryMappingTests(unittest.TestCase):
    def run_case(self, archived=False, wrong_cover=False, wrong_segment=False):
        material_names = ['素材ID', '产品编码', '所属产品', '来源平台', '素材附件', '原视频关联', '所属脚本段', '选材分区']
        video_names = ['画面素材', '脚本段', '所属产品']
        mappings = {table: {name: {'id': 'fld' + table + str(i), 'name': 'Custom ' + table + ' ' + str(i), 'type': 'text'}
                           for i, name in enumerate(names)}
                    for table, names in [('materials', material_names), ('videos', video_names)]}
        profile = {'base_id': 'baseFixture', 'identity': 'user', 'tables': {
            name: {'id': 'tbl' + name, 'fields': fields} for name, fields in mappings.items()}}
        row = {'record_id': 'recmaterial', 'fields': {'素材ID': 'clip-001:shot1', '产品编码': 'P001',
               '所属产品': 'Example product', '来源平台': 'Example', '素材附件': [{'name': 'clip.mp4'}],
               '原视频关联': [{'id': 'recsource'}], '所属脚本段': [{'id': 'recunrelated' if wrong_segment else 'recsegment'}],
               '选材分区': 'archive' if archived else 'active'}}
        row['fields'] = {mappings['materials'][name]['name']: value for name, value in row['fields'].items()}
        parent = {mappings['videos'][name]['name']: value for name, value in {
            '画面素材': [{'id': 'recmaterial'}], '脚本段': [{'id': 'recsegment'}], '所属产品': [{'id': 'recproduct'}]}.items()}
        plan = {'base_id': 'baseFixture', 'video_id': 'clip-001', 'source_record_id': 'recsource',
                'product_record_id': 'recproduct', 'source_sha256': 'a' * 64,
                'materials': [{'key': 'clip-001:shot1', 'attachments': [{'field': '素材附件', 'path': 'clip.mp4'}]}],
                'segments': [{'key': 'clip-001:seg1', 'source_shot_keys': ['clip-001:shot1']}]}
        state = {'pending': None, 'main_fields_verified': True,
                 'records': {'materials': {'clip-001:shot1': {'record_id': 'recmaterial'}},
                             'segments': {'clip-001:seg1': {'record_id': 'recsegment'}}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'config').mkdir()
            for path, obj in [('config/project.json', profile), ('plan.json', plan), ('state.json', state)]:
                (root / path).write_text(json.dumps(obj), encoding='utf-8')
            outer = self
            class Fake:
                def __init__(self, base, identity, *args):
                    outer.assertEqual(identity, 'user')
                    self.filters = {}
                def fields(self, table):
                    logical = table.removeprefix('tbl')
                    return {value['name']: value for value in mappings[logical].values()}
                def write_json_file(self, value):
                    path = root / ('filter' + str(len(self.filters)) + '.json')
                    path.write_text(json.dumps(value))
                    self.filters['@' + path.name] = value
                    return '@' + path.name, path
                def _call(self, command, table, args):
                    if command == '+view-get': return {'view': {'type': 'gallery'}}, {}
                    if command == '+view-get-card':
                        return {'card': {'cover_field': 'wrong' if wrong_cover else mappings['materials']['素材附件']['id']}}, {}
                    if command == '+view-get-filter':
                        conditions = [[mappings['materials']['产品编码']['id'], '==', 'P001']]
                        if archived: conditions.append([mappings['materials']['选材分区']['name'], 'disjoint', ['archive']])
                        return {'filter': {'logic': 'and', 'conditions': conditions}}, {}
                    if command == '+record-list':
                        for i, arg in enumerate(args):
                            if arg == '--field-id': outer.assertTrue(args[i + 1].startswith('fldmaterials'))
                        filt = self.filters[args[args.index('--filter-json') + 1]]
                        archive_query = any(c[1] == 'intersects' for c in filt['conditions'])
                        records = [] if archived and not archive_query else [copy.deepcopy(row)]
                        if archive_query:
                            outer.assertTrue(all(c[0].startswith('fld') for c in filt['conditions']))
                        return {'records': records, 'has_more': False}, {}
                    raise AssertionError(command)
                def get(self, table, record, fields, schema):
                    return decode_records({'record': {'record_id': record, 'fields': parent}}, schema)[0]
            argv = ['gallery', '--project', str(root), '--workspace', str(root), '--plan', str(root / 'plan.json'),
                    '--state', str(root / 'state.json'), '--output', str(root / 'out.json'), '--view-id', 'vewfixture',
                    '--product-code', 'P001', '--platform', 'Example']
            if archived: argv += ['--archive-zone', 'archive']
            with patch.object(gallery, 'Lark', Fake), patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
                gallery.main()
            return json.loads((root / 'out.json').read_text(encoding='utf-8'))

    def test_custom_fields_pass_with_attachments_and_parent_links(self):
        self.assertEqual(self.run_case()['materials_visible'], 1)

    def test_custom_archive_filter_uses_mapped_ids_and_verifies_hidden_clip(self):
        result = self.run_case(archived=True)
        self.assertEqual(result['materials_visible'], 0)
        self.assertEqual(result['materials_archived_verified'], 1)

    def test_custom_schema_does_not_weaken_video_cover_guard(self):
        with self.assertRaisesRegex(AssertionError, 'preview video'):
            self.run_case(wrong_cover=True)

    def test_custom_fields_reject_wrong_segment_link(self):
        with self.assertRaisesRegex(AssertionError, 'semantic segment'):
            self.run_case(wrong_segment=True)

    def test_archived_material_rejects_wrong_segment_link(self):
        with self.assertRaisesRegex(AssertionError, 'semantic segment'):
            self.run_case(archived=True, wrong_segment=True)


if __name__ == '__main__':
    unittest.main()
