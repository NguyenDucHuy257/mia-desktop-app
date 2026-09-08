"""Persist XML material codes beside detail records using the package pipeline."""
import json
import sqlite3
import threading
import uuid
from contextlib import closing
from pathlib import Path
from xml.etree import ElementTree as ET

_lock = threading.Lock()
_tasks = {}


def codes_from_xml(content):
    root = ET.fromstring(content)
    codes = {}
    for element in root.iter():
        if element.tag.split('}')[-1] != 'HHDVu':
            continue
        fields = {child.tag.split('}')[-1]: child.text or '' for child in element}
        name, code = fields.get('THHDVu', ''), fields.get('MHHDVu', '')
        if name:
            # Match the legacy exact-name rule, including last matching line.
            codes[name] = code
    return codes


def has_material_column(database):
    if not Path(database).is_file():
        return False
    with closing(sqlite3.connect(database, timeout=5)) as connection:
        return any(row[1] == 'mvt_xml' for row in connection.execute('PRAGMA table_info(invoice_detail_items)'))


def enrich_materials(database, items, job):
    if not has_material_column(database):
        return
    directions = job.parameters.get('directions') or ['purchase', 'sold']
    types = job.parameters.get('query_types') or ['query', 'sco-query']
    with closing(sqlite3.connect(database, timeout=5)) as connection:
        cache = {}
        for item in items:
            identity = tuple(str(item.get(key) or '') for key in ('nbmst', 'khhdon', 'shdon', 'khmshdon'))
            if identity not in cache:
                row = connection.execute(
                    'SELECT mvt_xml FROM invoice_detail_items WHERE company_tax_code=? '
                    f'AND direction IN ({",".join("?" for _ in directions)}) '
                    f'AND query_type IN ({",".join("?" for _ in types)}) '
                    'AND nbmst=? AND khhdon=? AND shdon=? AND khmshdon=? '
                    'AND mvt_xml IS NOT NULL ORDER BY id DESC LIMIT 1',
                    (job.company_tax_code, *directions, *types, *identity),
                ).fetchone()
                cache[identity] = json.loads(row[0]) if row else {}
            code = cache[identity].get(str(item.get('ten') or ''), '')
            if code:
                item['m_VT'] = code


def status(connection_id):
    with _lock:
        return dict(_tasks.get(connection_id, {'status': 'idle', 'processed': 0, 'total': 0, 'failed': 0}))


def start(backend, query):
    connection_id = query['connection_id']
    tax_code = backend.connection_tax_code(connection_id)
    database = backend.data_root / tax_code / 'db' / 'invoices.sqlite3'
    if not database.is_file():
        raise ValueError('result_job_not_found')
    with _lock:
        if _tasks.get(connection_id, {}).get('status') == 'running':
            return dict(_tasks[connection_id])
        with closing(sqlite3.connect(database, timeout=5)) as connection:
            if not any(row[1] == 'mvt_xml' for row in connection.execute('PRAGMA table_info(invoice_detail_items)')):
                connection.execute('ALTER TABLE invoice_detail_items ADD COLUMN mvt_xml TEXT')
            line_columns = list(connection.execute('PRAGMA table_info(invoice_detail_lines)'))
            if line_columns and not any(row[1] == 'mvt_xml' for row in line_columns):
                connection.execute('ALTER TABLE invoice_detail_lines ADD COLUMN mvt_xml TEXT')
            connection.commit()
        _tasks[connection_id] = {'task_id': uuid.uuid4().hex, 'status': 'running', 'processed': 0, 'total': 0, 'failed': 0, 'missing_xml': 0}
        initial = dict(_tasks[connection_id])
    threading.Thread(target=_run, args=(backend, dict(query), database, tax_code), daemon=True, name='mia-material-lookup').start()
    return initial


def _run(backend, query, database, tax_code):
    from mia_artifact_pipeline import _package_row
    connection_id = query['connection_id']

    def update(**values):
        with _lock:
            _tasks[connection_id].update(values)

    def ready(target, state, outcome):
        if state == 'missing_original' or outcome == 'missing_original':
            with _lock:
                _tasks[connection_id]['processed'] += 1
                _tasks[connection_id]['missing_xml'] = _tasks[connection_id].get('missing_xml', 0) + 1
            return
        failed = state != 'completed'
        try:
            if not failed:
                package = _package_row(database, tax_code, target)
                if not package or not package.get('xml_path'):
                    raise ValueError('xml_cache_missing')
                codes = codes_from_xml(Path(package['xml_path']).read_bytes())
                with closing(sqlite3.connect(database, timeout=10)) as connection:
                    connection.execute(
                        'UPDATE invoice_detail_items SET mvt_xml=? WHERE company_tax_code=? '
                        'AND direction=? AND query_type=? AND nbmst=? AND khhdon=? AND shdon=? AND khmshdon=?',
                        (json.dumps(codes, ensure_ascii=False), tax_code, *(target[key] for key in ('direction', 'query_type', 'nbmst', 'khhdon', 'shdon', 'khmshdon'))),
                    )
                    if any(row[1] == 'mvt_xml' for row in connection.execute('PRAGMA table_info(invoice_detail_lines)')):
                        detail_ids = [row[0] for row in connection.execute(
                            'SELECT id FROM invoice_detail_items WHERE company_tax_code=? AND direction=? AND query_type=? '
                            'AND nbmst=? AND khhdon=? AND shdon=? AND khmshdon=?',
                            (tax_code, *(target[key] for key in ('direction', 'query_type', 'nbmst', 'khhdon', 'shdon', 'khmshdon'))),
                        )]
                        for detail_id in detail_ids:
                            connection.execute('UPDATE invoice_detail_lines SET mvt_xml=NULL WHERE detail_item_id=?', (detail_id,))
                            connection.executemany('UPDATE invoice_detail_lines SET mvt_xml=? WHERE detail_item_id=? AND ten=?',
                                                   [(code, detail_id, name) for name, code in codes.items()])
                    connection.commit()
        except Exception:
            backend.logger.exception('material_lookup_invoice_failed')
            failed = True
        with _lock:
            _tasks[connection_id]['processed'] += 1
            _tasks[connection_id]['failed'] += int(failed)

    try:
        batches = []
        for direction in ([query['direction']] if query.get('direction') else ['purchase', 'sold']):
            for query_type in ([query['query_type']] if query.get('query_type') else query.get('query_types') or ['query', 'sco-query']):
                request = {'connection_ids': [connection_id], 'direction': direction, 'query_type': query_type,
                           'date_from': query['date_from'], 'date_to': query['date_to'], 'search': '', 'kinds': ['xml']}
                targets = backend.artifact_targets_for_export(request)
                with closing(sqlite3.connect(database, timeout=5)) as connection:
                    targets = [target for target in targets if connection.execute(
                        'SELECT 1 FROM invoice_detail_items WHERE company_tax_code=? AND direction=? AND query_type=? '
                        'AND nbmst=? AND khhdon=? AND shdon=? AND khmshdon=? LIMIT 1',
                        (tax_code, *(target[key] for key in ('direction', 'query_type', 'nbmst', 'khhdon', 'shdon', 'khmshdon'))),
                    ).fetchone()]
                request['_artifact_keys'] = {target['artifact_key'] for target in targets}
                batches.append((request, targets))
        update(total=sum(len(targets) for _, targets in batches))
        for request, targets in batches:
            missing = set()
            for target in targets:
                package = _package_row(database, tax_code, target)
                if package and package.get('xml_path') and Path(package['xml_path']).is_file():
                    ready(target, 'completed', 'cached')
                else:
                    missing.add(target['artifact_key'])
            if missing:
                backend.ensure_invoice_packages({**request, '_artifact_keys': missing}, ready_callback=ready)
        update(status='completed')
    except Exception:
        backend.logger.exception('material_lookup_failed')
        update(status='failed', error='Tra cứu XML bị gián đoạn. Các mã đã lấy được vẫn được lưu.')
