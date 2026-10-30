"""Subprocess worker used to SIGKILL publication at each durable boundary."""
import argparse
import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient
from temporalio import workflow, activity
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from biosim_server import dependencies as deps
from biosim_server.config import get_settings
from biosim_server.common.temporal import pydantic_data_converter
from biosim_server.common.storage.file_service_local import FileServiceLocal
from biosim_server.biosim_verify.report_storage import VerificationReportStore
from biosim_server.biosim_verify.database import VerificationDatabaseServiceMongo
from biosim_server.biosim_verify.activities import PersistVerificationReportActivityInput, persist_verification_report_activity
from biosim_server.biosim_verify.models import VerifyWorkflowOutput


@workflow.defn
class CrashPublicationWorkflow:
    @workflow.run
    async def run(self, payload: PersistVerificationReportActivityInput) -> VerifyWorkflowOutput:
        payload.output.workflow_run_id = payload.provenance.workflow_run_id = workflow.info().run_id
        result: VerifyWorkflowOutput = await workflow.execute_activity('persist_verification_report_activity', payload,
            result_type=VerifyWorkflowOutput, start_to_close_timeout=timedelta(seconds=3),
            retry_policy=RetryPolicy(initial_interval=timedelta(milliseconds=100), maximum_attempts=10))
        return result


async def run(args: argparse.Namespace) -> None:
    async def stop_at(stage: str) -> None:
        if args.stage == stage:
            Path(args.marker).write_text(stage)
            await asyncio.Event().wait()

    class Objects(FileServiceLocal):
        async def upload_bytes_immutable(self, contents: bytes, key: str) -> str:
            await stop_at('before_upload')
            reference = await super().upload_bytes_immutable(contents, key)
            await stop_at('after_upload')
            return reference

    class Ledger(VerificationDatabaseServiceMongo):
        async def record_report_persisted(self, workflow_id: str, report_id: str, status: Any) -> None:
            await stop_at('before_ledger')
            await super().record_report_persisted(workflow_id, report_id, status)
            await stop_at('after_ledger')

    class CollectionProxy:
        def __init__(self, collection: Any) -> None:
            self.collection = collection

        def __getattr__(self, name: str) -> Any:
            return getattr(self.collection, name)

        async def insert_one(self, document: Any) -> Any:
            await stop_at('before_manifest')
            result = await self.collection.insert_one(document)
            await stop_at('after_manifest')
            return result

    settings = get_settings()
    settings.mongodb_database = args.database
    settings.verification_report_inline_max_bytes = 1
    mongo = AsyncIOMotorClient(args.mongo)
    files = Objects(Path(args.objects))
    ledger, store = Ledger(mongo), VerificationReportStore(mongo, files)
    store._collection = CollectionProxy(store._collection)  # type: ignore[assignment]
    deps.set_verification_database_service(ledger)
    deps.set_verification_report_store(store)
    deps.set_mongo_client(mongo)

    @activity.defn(name='persist_verification_report_activity')
    async def publish(payload: PersistVerificationReportActivityInput) -> VerifyWorkflowOutput:
        output = await persist_verification_report_activity(payload)
        await stop_at('before_ack')
        return output

    client = await Client.connect(args.server, data_converter=pydantic_data_converter)
    async with Worker(client, task_queue=args.queue, workflows=[CrashPublicationWorkflow],
                      activities=[publish], workflow_runner=UnsandboxedWorkflowRunner()):
        await asyncio.Event().wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('server', 'mongo', 'database', 'objects', 'queue', 'stage', 'marker'):
        parser.add_argument('--' + name, required=True)
    asyncio.run(run(parser.parse_args()))
