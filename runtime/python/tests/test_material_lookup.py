import json
import logging
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mia_material_lookup import codes_from_xml, enrich_materials, _run, _tasks


class MaterialLookupTests(unittest.TestCase):
    def test_namespace_exact_name_and_last_match(self):
        xml = '<Invoice xmlns="urn:test"><HHDVu><MHHDVu>001</MHHDVu><THHDVu>A</THHDVu></HHDVu><HHDVu><MHHDVu>002</MHHDVu><THHDVu>A</THHDVu></HHDVu><HHDVu><THHDVu>B</THHDVu></HHDVu></Invoice>'
        self.assertEqual(codes_from_xml(xml), {'A': '002', 'B': ''})

    def test_cached_xml_persists_and_reloads_without_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / 'invoices.sqlite3'
            xml = root / 'invoice.xml'
            xml.write_text('<Invoice><HHDVu><MHHDVu>001</MHHDVu><THHDVu>A</THHDVu></HHDVu></Invoice>', encoding='utf-8')
            with closing(sqlite3.connect(database)) as connection:
                connection.execute('CREATE TABLE invoice_detail_items (id INTEGER, company_tax_code TEXT, direction TEXT, query_type TEXT, nbmst TEXT, khhdon TEXT, shdon TEXT, khmshdon TEXT, mvt_xml TEXT)')
                connection.execute("INSERT INTO invoice_detail_items VALUES (1, 'tax', 'purchase', 'query', 'seller', 'AA', '1', '1', NULL)")
                connection.commit()
            target = dict(direction='purchase', query_type='query', nbmst='seller', khhdon='AA', shdon='1', khmshdon='1', artifact_key='key')
            backend = SimpleNamespace(artifact_targets_for_export=Mock(return_value=[target]), ensure_invoice_packages=Mock(), logger=logging.getLogger('test'))
            _tasks['test'] = dict(status='running', processed=0, total=0, failed=0)
            query = dict(connection_id='test', direction='purchase', query_type='query', date_from='2026-01-01', date_to='2026-01-31')
            with patch('mia_artifact_pipeline._package_row', return_value={'xml_path': str(xml)}):
                _run(backend, query, database, 'tax')
            backend.ensure_invoice_packages.assert_not_called()
            self.assertEqual(_tasks.pop('test')['processed'], 1)
            _tasks['test'] = dict(status='running', processed=0, total=0, failed=0)
            def download(request, ready_callback):
                self.assertEqual(request['_artifact_keys'], {'key'})
                ready_callback(target, 'completed', 'downloaded')
            backend.ensure_invoice_packages.side_effect = download
            with patch('mia_artifact_pipeline._package_row', side_effect=[None, {'xml_path': str(xml)}]):
                _run(backend, query, database, 'tax')
            backend.ensure_invoice_packages.assert_called_once()
            self.assertEqual(_tasks.pop('test')['status'], 'completed')
            items = [{**target, 'ten': 'A'}, {**target, 'ten': 'a', 'm_VT': 'existing'}]
            job = SimpleNamespace(company_tax_code='tax', parameters=dict(directions=['purchase'], query_types=['query']))
            enrich_materials(database, items, job)
            self.assertEqual([item['m_VT'] for item in items], ['001', 'existing'])
            self.assertTrue(all('mvt_xml' not in item for item in items))
            from mia_source_results import _ExcelSafeDetailRowBuilder
            builder = _ExcelSafeDetailRowBuilder()
            builder.material_database = database
            builder._delegate = Mock()
            builder._delegate.build_rows.return_value = [{'ten': 'A', 'm_VT': ''}]
            with patch('mia_invoice_lookup.resolve_invoice_lookup', return_value=SimpleNamespace(url='', code='')):
                exported = builder.build_rows({}, {**target, 'company_tax_code': 'tax'})
            self.assertEqual(exported[0]['m_VT'], '001')
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(json.loads(connection.execute('SELECT mvt_xml FROM invoice_detail_items').fetchone()[0]), {'A': '001'})
