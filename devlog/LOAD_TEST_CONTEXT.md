# Load-test configuration context

The original README is preserved unchanged in [README.original.md](README.original.md), including its historical latency and failure-rate figures. Those figures did not record a rate-limit configuration alongside the results.

A local audit of the original code and `.env.example` used ten virtual users for twenty seconds with the default twenty POSTs per minute per client and endpoint. The synchronous script accepted 20 of 400 requests; the asynchronous script accepted 20 of 390 requests. Both k6 runs failed their request-failure threshold because the limiter rejected excess requests.

These are rate-limit observations, not throughput measurements. Future measurements should record the configured limit, workload, accepted-request counts, and response classes. The operational instructions are in [the demo guide](../docs/DEMO.md).
