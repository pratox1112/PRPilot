"""Feeding historical PRs to the deployed system (roadmap §9.3, §9.4).

`bundle` picks PRs from a split and writes a replay bundle (open-time data only); `send` signs a
pull_request.opened webhook per PR, sends it to the deployed api with a bounded number in
flight, and records when each run finished; `measure` reports the outcomes, latency and cost of
a send, from the api, Cloud Monitoring and the public price catalog.
"""
