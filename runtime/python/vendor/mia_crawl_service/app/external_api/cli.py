from __future__ import annotations

import argparse
from typing import Sequence

from app.account_connections.repository import create_account_connection_repository
from app.external_api.factory import create_api_control_repository
from app.job_engine.factory import create_job_engine_repository
from app.session_manager.factory import create_session_repository


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='MIA external API control commands')
    parser.add_argument('command', choices=('migrate',))
    args = parser.parse_args(argv)
    if args.command == 'migrate':
        # Apply dependencies before tables that reference them. Account
        # connections map to internal session state, while immediate admission
        # extends the job-engine schema with worker slots and account leases.
        create_session_repository().migrate()
        create_account_connection_repository().migrate()
        create_job_engine_repository().migrate()
        create_api_control_repository().migrate()
        print('Control database migration complete (versions 1-8)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())