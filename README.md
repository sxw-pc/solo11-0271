# Pod 安全终止宽限与连接排空庭

# 项目生成提示词：Pod 安全终止宽限与连接排空庭

面向服务网关与容器生命周期团队，实现一个离线可运行的云原生业务控制台。场景：滚动下线时协调入口摘除、存量连接、终止钩子和宽限截止，说明下线是否无损。边界：本地模拟连接与钩子回执，不向真实 Pod 发送系统信号。

使用 Python 3.11+ 标准库、SQLite、unittest；提供 `python -m draincourt` 单命令 CLI。每个命令以 SQLite IMMEDIATE 事务写入业务状态与不可变原始事件；相同业务身份和相同内容幂等，冲突内容拒绝；回放要从原始事件重建所有业务表并逐表比较。



三个独立命令：`python -m draincourt termination --db state.sqlite --fixture fixtures/termination_normal.json`, `python -m draincourt connection --db state.sqlite --fixture fixtures/connection_normal.json`, `python -m draincourt shutdown --db state.sqlite --fixture fixtures/shutdown_normal.json`。
回放 `python -m draincourt replay --db state.sqlite`；测试 `python -m unittest discover -s tests -v`。

Local diagnostic frames are built from literals and slices of synthetic labelled
values. The source recipe stays available independently of the rendered text.
Run `python -m draincourt transcript --db trace.sqlite --fixture fixtures/transcript_fragments.json`.
