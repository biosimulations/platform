"""Export a verification bundle, or verify/restore it into an explicitly selected destination."""
import argparse
import asyncio
import json
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient
from biosim_server.config import get_settings
from biosim_server.dependencies import _make_file_service
from biosim_server.biosim_verify.database import VerificationDatabaseServiceMongo
from biosim_server.biosim_verify.report_storage import VerificationReportStore
from biosim_server.biosim_verify.report_backup import export_bundle, restore_bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['export', 'restore'])
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--database', required=True, help='explicit source/destination database')
    parser.add_argument('--apply', action='store_true', help='restore into destination (otherwise verify only)')
    args = parser.parse_args()

    async def run() -> None:
        settings = get_settings()
        settings.mongodb_database = args.database
        client = AsyncIOMotorClient(settings.mongodb_uri)
        files = _make_file_service(settings.storage_backend)
        store, ledger = VerificationReportStore(client, files), VerificationDatabaseServiceMongo(client)
        try:
            if args.operation == 'export':
                manifest = await export_bundle(args.directory, store, ledger)
                print(json.dumps({'reports': len(manifest['reports']), 'ledger': len(manifest['ledger']),
                                  'objects': len(manifest['objects']), 'complete': True}))
            else:
                print(json.dumps(await restore_bundle(args.directory, store, ledger, apply=args.apply)))
        finally:
            await files.close()
            client.close()
    asyncio.run(run())


if __name__ == '__main__':
    main()
