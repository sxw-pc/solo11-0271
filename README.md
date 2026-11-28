# Pod 安全终止宽限与连接排空庭

# 项目生成提示词：Pod 安全终止宽限与连接排空庭

面向服务网关与容器生命周期团队，实现一个离线可运行的云原生业务控制台。场景：滚动下线时协调入口摘除、存量连接、终止钩子和宽限截止，说明下线是否无损。边界：本地模拟连接与钩子回执，不向真实 Pod 发送系统信号。

使用 Python 3.11+ 标准库、SQLite、unittest；提供 `python -m draincourt` 单命令 CLI。每个命令以 SQLite IMMEDIATE 事务写入业务状态与不可变原始事件；相同业务身份和相同内容幂等，冲突内容拒绝；回放要从原始事件重建所有业务表并逐表比较。



三个独立命令：`python -m draincourt termination --db state.sqlite --fixture fixtures/termination_normal.json`, `python -m draincourt connection --db state.sqlite --fixture fixtures/connection_normal.json`, `python -m draincourt shutdown --db state.sqlite --fixture fixtures/shutdown_normal.json`。
回放 `python -m draincourt replay --db state.sqlite`；测试 `python -m unittest discover -s tests -v`。

The offline clock inventory keeps per-node clock-offset bounds and local observations
of agent message exchanges. Milestones retain their original source and local time.
Run `python -m draincourt nodeclock --db incident.sqlite --fixture fixtures/clock_incident.json`.

Event-order proofs are `milestone` events with `op: prove_order` over two milestones of
one incident. Only the exchanges listed in `authorized_exchanges` constrain the proof;
all other observed exchanges are reported as `ignored_exchanges` and never enter the
extremal evidence. Every authorized exchange is reduced to difference constraints on one
shared set of node offsets (`0 <= received-sent <= max_delay` after offset conversion),
solved jointly with each node's static range. The export gives the minimum and maximum
of `global(b) - global(a)`, one jointly feasible offset assignment attaining each
attainable endpoint (with per-edge checks), and a verdict: `proven_after`,
`proven_before`, `proven_simultaneous`, or `order_undecided` whenever zero or both signs
remain possible. Contradictory calibrations return `calibration_conflict` with the
exchanges on the negative cycle instead of any ordering verdict.
Run `python -m draincourt milestone --db incident.sqlite --fixture fixtures/clock_order_proofs.json`
after the incident fixture; the static-only and relay-authorized contracts are both
reproduced unchanged by `replay`.
