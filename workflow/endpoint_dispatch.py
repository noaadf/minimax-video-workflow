"""Bounded client-side dispatch to independently owned video API endpoints.

This does not change Queue device leases. Callers must already own the entire
endpoint group. A slot permits a request to wait behind another request in a
service; it does not permit concurrent model forwards inside that service.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock


class EndpointBatchError(RuntimeError):
    def __init__(self, failures, results, unstarted):
        super().__init__(f'{len(failures)} endpoint request(s) failed; no retries submitted')
        self.failures = failures
        self.results = results
        self.unstarted = unstarted


def dispatch_batch(endpoints, jobs, invoke, *, inflight_per_endpoint=2):
    """Execute each finite job once, dynamically filling at most two API slots.

``invoke(endpoint, job)`` owns transport, timeout and result persistence. On
failure, stop taking new work and drain requests already in flight. The error
carries partial results and unstarted job indices for explicit reconciliation.
Results preserve input order. There is deliberately no automatic retry.
"""
    endpoints = tuple(endpoints)
    if not endpoints or len(set(endpoints)) != len(endpoints):
        raise ValueError('Specify unique independently owned endpoints')
    if type(inflight_per_endpoint) is not int or not 1 <= inflight_per_endpoint <= 2:
        raise ValueError('Endpoint request concurrency must be 1 or 2')
    jobs = list(jobs)
    results = [None] * len(jobs)
    failures = {}
    next_index = 0
    guard = Lock()

    def worker(endpoint):
        nonlocal next_index
        while True:
            with guard:
                if failures or next_index == len(jobs):
                    return
                index = next_index
                next_index += 1
            try:
                result = invoke(endpoint, jobs[index])
            except Exception as exc:
                with guard:
                    failures[index] = exc
                return
            with guard:
                results[index] = result

    with ThreadPoolExecutor(max_workers=len(endpoints) * inflight_per_endpoint) as pool:
        futures = [pool.submit(worker, endpoint) for endpoint in endpoints
                   for _ in range(inflight_per_endpoint)]
        for future in futures:
            future.result()
    if failures:
        raise EndpointBatchError(failures, results, list(range(next_index, len(jobs))))
    return results
