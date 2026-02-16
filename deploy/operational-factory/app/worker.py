import os
import time
from redis import Redis
from rq import Queue, Worker
import httpx

from app.settings import REDIS_URL, RUNTIME_URL


def enqueue_demo_tasks(queue: Queue):
    queue.enqueue(call_runtime, "/flow/email/intake", {"limit": 10})
    queue.enqueue(call_runtime, "/flow/repo/propose", {})
    queue.enqueue(call_runtime, "/flow/briefing/daily", {})


def call_runtime(path: str, payload: dict):
    url = f"{RUNTIME_URL}{path}"
    resp = httpx.post(url, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    redis = Redis.from_url(REDIS_URL)
    queue = Queue("navig_factory", connection=redis)

    if os.getenv("SEED_DEMO_TASKS", "1") == "1":
        try:
            enqueue_demo_tasks(queue)
        except Exception:
            pass

    worker = Worker([queue], connection=redis)
    worker.work(with_scheduler=True)
