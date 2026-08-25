# A/B questions

One question per `## ` heading. The body is the prompt.

Each question carries its own facts. That is deliberate: the two variants must
see identical material so the only thing that differs is how it is presented.
A free-form question would send the two runs off gathering different evidence,
and the comparison would measure luck rather than style.

The shapes are drawn from real prompts: a comparison, a decision, a root cause,
a structure question, a correction, and a scoping call. Replace them with your
own domain if you want the A/B to reflect the work you actually do.

## comparison

Four read paths were measured against the same dataset: an in-memory cache hit
2.1 ms, a warm index scan 12.4 ms, a cold index scan 21.9 ms, a full table scan
37.2 ms. Each is n=200 at p50. Which path should the list endpoint use, and what
does the gap between the cache and the warm index actually cost?

## decision

I can keep sessions in process memory (fast, 512 MB per node, lost on restart,
and not shared between nodes) or in Redis (shared, survives a restart, one more
service to run, and about 0.4 ms per lookup on the same network). We run four
nodes and deploy twice a day. Which do I pick, and what breaks if I pick wrong?

## root cause

Requests ran at roughly half throughput: an endpoint that should serve 60 per
second served near 30. Queries through the ORM measured 2.1 MB/s of row traffic
while the same query run directly measured 39 MB/s. The connection pool was at
its default size. Explain what was happening and why the symptom looked like a
network problem.

## structure

Describe how a request gets from the load balancer to the response: the router,
the auth middleware, the handler, the service layer, the repository and the
database, plus the separate branch that emits metrics off the handler. I want to
understand the shape of the pipeline.

## correction

I previously told you the unindexed query cost 10x. The load test then measured
1.7x. The 10x came from a run before the composite index landed, and that index
changed the plan. Tell me what that invalidates.

## scoping

I want to add a bulk export feature. The job queue is designed but not written,
the storage layer exists for two backends, and there is no decision record for
the export format yet. What should happen first?
