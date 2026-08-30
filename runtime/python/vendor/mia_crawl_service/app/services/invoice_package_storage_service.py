from __future__ import annotations

import io
import logging
import os
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from app.repositories.invoice_package_repository import InvoicePackageRepository

logger = logging.getLogger(__name__)

REQUIRED_HTML_ASSETS = ('sign-check.jpg', 'viewinvoice-bg.jpg')
OPTIONAL_HTML_ASSET = 'details.js'


class InvoicePackageStorageService:
    """Persist raw packages, selected invoice files, assets, and DB state."""

    def __init__(
        self,
        base_data_dir: Path,
        package_repository: InvoicePackageRepository,
        resources_dir: Path | None = None,
        progress_callback=None,
    ) -> None:
        self.base_data_dir = Path(base_data_dir)
        self.package_repository = package_repository
        self.progress_callback = progress_callback
        self.resources_dir = (
            Path(resources_dir)
            if resources_dir is not None
            else Path('resources') / 'invoice_assets'
        )

    def save_invoice_package(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        invoice_category: str,
        invoice_item: dict[str, Any],
        zip_bytes: bytes,
        from_date: str,
        to_date: str,
        export_xml: bool,
        export_html: bool,
    ) -> dict[str, Any]:
        if not export_xml and not export_html:
            raise ValueError('At least one of export_xml/export_html must be True')
        if not zipfile.is_zipfile(io.BytesIO(zip_bytes)):
            raise RuntimeError('Cannot store invoice package because content is not a valid ZIP')
        self._progress('zip_validated')

        paths = self.artifact_paths(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            from_date=from_date,
            to_date=to_date,
            invoice_item=invoice_item,
        )
        invoice_key = str(paths['invoice_key'])
        package_dir = Path(paths['package_dir'])
        zip_dir = Path(paths['raw_zip_path']).parent
        xml_dir = Path(paths['xml_path']).parent
        html_dir = Path(paths['html_path']).parent
        zip_dir.mkdir(parents=True, exist_ok=True)
        raw_zip_path = Path(paths['raw_zip_path'])
        self._write_bytes_atomically(raw_zip_path, zip_bytes)

        xml_path: Path | None = None
        html_path: Path | None = None
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            file_names = [info.filename for info in archive.infolist() if not info.is_dir()]
            xml_member = self._find_member(file_names, 'invoice.xml')
            html_member = self._find_member(file_names, 'invoice.html')

            if export_xml:
                if xml_member is None:
                    raise RuntimeError(
                        f'Invoice package does not contain invoice.xml key={invoice_key}'
                    )
                xml_dir.mkdir(parents=True, exist_ok=True)
                xml_path = Path(paths['xml_path'])
                xml_bytes = archive.read(xml_member)
                self._progress('xml_extracted')
                self._write_bytes_atomically(xml_path, xml_bytes)
                self._progress('xml_file_written')

            if export_html:
                if html_member is None:
                    raise RuntimeError(
                        f'Invoice package does not contain invoice.html key={invoice_key}'
                    )
                html_dir.mkdir(parents=True, exist_ok=True)
                html_bytes = archive.read(html_member)
                html_path = Path(paths['html_path'])
                self._write_bytes_atomically(html_path, html_bytes)
                self._extract_zip_assets(
                    archive=archive,
                    file_names=file_names,
                    html_dir=html_dir,
                    html_member=html_member,
                    excluded_members={xml_member, html_member},
                )
                self._install_shared_assets(
                    archive=archive,
                    file_names=file_names,
                    html_dir=html_dir,
                    html_bytes=html_bytes,
                )

        fetched_at = datetime.now(timezone.utc).isoformat()
        self.package_repository.upsert_package_success(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            invoice_category=invoice_category,
            nbmst=str(invoice_item['nbmst']),
            khhdon=str(invoice_item['khhdon']),
            shdon=invoice_item['shdon'],
            khmshdon=invoice_item['khmshdon'],
            nlap=invoice_item.get('nlap'),
            nlap_date=invoice_item.get('nlap_date'),
            package_dir=package_dir,
            raw_zip_path=raw_zip_path,
            xml_path=xml_path,
            html_path=html_path,
            xml_fetched=xml_path is not None,
            html_fetched=html_path is not None,
            fetched_at=fetched_at,
        )
        self._progress('package_database_upserted')
        result = {
            'invoice_key': invoice_key,
            'package_dir': str(package_dir),
            'raw_zip_path': str(raw_zip_path),
            'xml_path': str(xml_path) if xml_path is not None else None,
            'html_path': str(html_path) if html_path is not None else None,
            'xml_fetched': xml_path is not None,
            'html_fetched': html_path is not None,
            'fetched_at': fetched_at,
        }
        logger.info(
            'Stored invoice package key=%s xml=%s html=%s',
            invoice_key, result['xml_fetched'], result['html_fetched'],
        )
        return result

    def _progress(self, event: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(event)

    def artifact_paths(
        self,
        *,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        invoice_item: dict[str, Any],
    ) -> dict[str, Path | str]:
        invoice_key = self._invoice_key(invoice_item)
        package_dir = (
            self.base_data_dir / company_tax_code / 'exports'
            / 'invoice_packages' / direction / query_type
            / f'{from_date}_{to_date}'
        ).resolve()
        return {
            'invoice_key': invoice_key,
            'package_dir': package_dir,
            'raw_zip_path': package_dir / 'zip' / f'{invoice_key}.zip',
            'xml_path': package_dir / 'xml' / f'{invoice_key}.xml',
            'html_path': package_dir / 'html' / f'{invoice_key}.html',
        }

    @staticmethod
    def _invoice_key(invoice_item: dict[str, Any]) -> str:
        fields = ('khmshdon', 'khhdon', 'shdon', 'nbmst')
        values: list[str] = []
        for field in fields:
            value = str(invoice_item[field]).strip()
            if not value:
                raise ValueError(f'Invoice key field {field} must not be empty')
            values.append(re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value))
        return '_'.join(values)

    @staticmethod
    def _find_member(file_names: list[str], basename: str) -> str | None:
        wanted = basename.casefold()
        return next(
            (
                name
                for name in file_names
                if PurePosixPath(name.replace('\\', '/')).name.casefold() == wanted
            ),
            None,
        )

    def _extract_zip_assets(
        self,
        *,
        archive: zipfile.ZipFile,
        file_names: list[str],
        html_dir: Path,
        html_member: str,
        excluded_members: set[str | None],
    ) -> None:
        """Extract auxiliary files while rejecting absolute and traversal paths."""
        resolved_html_dir = html_dir.resolve()
        html_source_parent = PurePosixPath(
            html_member.replace('\\', '/')
        ).parent
        for member in file_names:
            if member in excluded_members:
                continue
            relative = PurePosixPath(member.replace('\\', '/'))
            if relative.is_absolute() or '..' in relative.parts:
                logger.warning('Skipped unsafe ZIP member name=%s', member)
                continue
            # invoice.html is renamed into the batch HTML root. When a ZIP
            # wraps all files in one directory, remove that common prefix so
            # its original relative asset references continue to work.
            if html_source_parent != PurePosixPath('.'):
                try:
                    relative = relative.relative_to(html_source_parent)
                except ValueError:
                    pass
            target = html_dir.joinpath(*relative.parts)
            try:
                target.resolve().relative_to(resolved_html_dir)
            except ValueError:
                logger.warning('Skipped ZIP member outside HTML directory name=%s', member)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            self._write_bytes_atomically(target, archive.read(member))

    def _install_shared_assets(
        self,
        *,
        archive: zipfile.ZipFile,
        file_names: list[str],
        html_dir: Path,
        html_bytes: bytes,
    ) -> None:
        for asset_name in REQUIRED_HTML_ASSETS:
            resource_path = self.resources_dir / asset_name
            target_path = html_dir / asset_name
            if resource_path.is_file():
                self._write_bytes_atomically(target_path, resource_path.read_bytes())
                continue
            zip_member = self._find_member(file_names, asset_name)
            if zip_member is not None:
                self._write_bytes_atomically(target_path, archive.read(zip_member))
            else:
                logger.warning(
                    'HTML asset %s is missing from both resources and ZIP', asset_name
                )

        # Copy a fixed details.js only when the invoice actually references it.
        if b'details.js' in html_bytes.lower():
            resource_path = self.resources_dir / OPTIONAL_HTML_ASSET
            target_path = html_dir / OPTIONAL_HTML_ASSET
            if resource_path.is_file():
                self._write_bytes_atomically(target_path, resource_path.read_bytes())
            elif not target_path.is_file():
                zip_member = self._find_member(file_names, OPTIONAL_HTML_ASSET)
                if zip_member is not None:
                    self._write_bytes_atomically(target_path, archive.read(zip_member))
                else:
                    logger.warning(
                        'HTML references details.js but it is missing from resources and ZIP'
                    )

    @staticmethod
    def _write_bytes_atomically(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='wb',
                dir=path.parent,
                prefix=f'.{path.name}.',
                suffix='.tmp',
                delete=False,
            ) as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
                temporary_path = Path(temporary_file.name)
            temporary_path.replace(path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
