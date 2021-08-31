"""
TODO remote execution
TODO capabilities
TODO checking for and restarting requested but not running jobs
"""

from typing import Any, Callable, Dict, Iterable
from concurrent.futures import ProcessPoolExecutor, Future, CancelledError

from nextbeat.event_log import Event, AppendEventType
from nextbeat.jobs_common import JobRunSpec, JobPayload


class LocalJobRunner:
    """Runs jobs on the current machine using a ProcessPoolExecutor"""

    def __init__(self, append_event: AppendEventType):
        self._running: Dict[str, Future] = {}
        self._executor = ProcessPoolExecutor(max_workers=5)
        self._append_event = append_event

    async def run(
        self, job_name: str, run_request_id: str, job_run_spec: JobRunSpec
    ) -> None:
        if run_request_id in self._running:
            return

        self._append_event(job_name, JobPayload(run_request_id, "RUN_REQUESTED"))
        self._running[run_request_id] = self._executor.submit(
            job_run_spec.fn,
            *(job_run_spec.args or []),
            **(job_run_spec.kwargs or {}),
        )

    async def poll_jobs(self, last_events: Iterable[Event[JobPayload]]) -> None:
        """
        last_events is the last event we've recorded for the jobs that we are interested
        in. poll_jobs will add new events to the EventLog for these jobs if there's been
        any change in their state.
        """

        # TODO can we have more than one run_request_id going for the same job?

        for last_event in last_events:
            request_id = last_event.payload.request_id
            if request_id in self._running:
                fut = self._running[request_id]
                if fut.done():
                    try:
                        fut_result = fut.result()
                        # TODO add pid to all of these?
                        new_payload = JobPayload(
                            request_id, "SUCCEEDED", result_value=fut_result
                        )
                    except CancelledError as e:
                        new_payload = JobPayload(
                            last_event.payload.request_id,
                            "CANCELLED",
                            raised_exception=e,
                        )
                    except Exception as e:
                        new_payload = JobPayload(
                            request_id, "FAILED", raised_exception=e
                        )
                else:
                    # TODO this isn't technically correct, we could still be in
                    #  RUN_REQUESTED state
                    new_payload = JobPayload(request_id, "RUNNING")

                if last_event.payload.state != new_payload.state:
                    # if we went straight from RUN_REQUESTED to one of the "done"
                    # states, then "make up" the RUNNING state that we didn't see, but
                    # we know it must have happened
                    if (
                        last_event.payload.state == "RUN_REQUESTED"
                        and new_payload.state != "RUNNING"
                    ):
                        self._append_event(
                            last_event.topic_name,
                            JobPayload(request_id, "RUNNING", pid=new_payload.pid),
                        )
                    self._append_event(last_event.topic_name, new_payload)
            else:
                # TODO we should probably be doing something with the run_request_ids
                #  that we don't recognize
                pass
