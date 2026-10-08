# PR #1675 自定义记录：覆盖与执行记录

日期：2026-10-08。评审定义见 `reviews/pr1675-router-custom-records.md`；本报告区分映射、独立 B 判断与实际执行，不以单个维度替代另一个。

## 固定输入

- 产品 base/merge-base：`ae6f7d3440c23025385537c1519d2af607f3b6a0`；PR head：`d54e9b4707fbee755ec9ef7cf77ca30abc3817c3`。
- 本地运行 FNN：`fnn Fiber v0.10.0-rc1 (2ba4b25 2026-10-04)`，SHA-256 `9e3f60755bf26624b52f8ef2115791c826d3a1eacfd55cfb86579732789e87d9`。`git -C fiber merge-base --is-ancestor d54e9b4 2ba4b25` 退出码 0。
- Python 测试文件（现位置）：`test_cases/fiber/devnet/send_payment_with_router/test_router_custom_records.py`，测试内容 SHA-256 `fc6ebff3fbb7ed8ea9c5bda2bc10bfacc8b16d2e51773be288aa04b31aa1ea1c`。按目录语义移至 `send_payment_with_router`；Makefile/CI 显式收集此文件，不扩大运行该目录中的其他测试；文件、类、方法名均不带 `pr`。
- A→B→C 由同一个 `SharedFiberTest` 类创建一次；每个成功 keysend 自带新哈希。使用隔离临时目录和 8720～8727 端口，测试结束后这些端口无监听。未改框架或产品源码。

## 分项判断

| Case | TEST-MAP | B 覆盖复核 | 执行 | 证据／限制 |
| --- | --- | --- | --- | --- |
| `PR1675-01` | 无 | partial | 未运行本仓库用例 | 产品 PR 的 Rust 测试用内部 `get_payment_custom_records` 核对 C；本仓库公开 RPC 无 C 端记录观察点，不能用 A 回显代替。 |
| `PR1675-02` | 无 | missing | 未运行 | 非 MPP 发票到 C 的记录尚无绑定测试或末跳观察点。 |
| `PR1675-03` | 有 | covered | passed | 值长 2012＋固定 Molecule 开销 36＝2048；付款 Success，A 查询保留记录。编码开销按产品 schema 推导，未在 Python 重新序列化。 |
| `PR1675-04` | 有 | partial | passed | 值长 2013／编码 2049，正常与 dry-run 均拒绝且付款、两通道快照不变；运行日志给出 `encoded size 2049 exceeds limit 2048`。断言只锁定 `InvalidParameter/custom_records`，未将诊断文案作为产品契约。对外口径待确认。 |
| `PR1675-05` | 有 | covered | passed | 同一路由合法键 dry-run 对照通过；`0x10000` 拒绝，付款/两通道快照不变。 |
| `PR1675-06` | 有 | covered | passed | 省略字段后付款 Success，发送响应及查询均为 null。 |
| `PR1675-07` | 有 | covered（当前行为） | passed | 显式空对象后付款 Success，响应和查询均为 `{}`；是否对外保证与省略不同仍待确认。 |
| `PR1675-08` | 有 | covered | passed | dry-run 回显记录及哈希、`get_payment` 报会话不存在，付款/两通道快照不变。 |
| `PR1675-09` | 有 | covered | passed | 多跳两条记录，A 响应及付款 Success 后查询均与输入一致；不证明 C 末跳交付。 |

B 使用独立无继承对话的只读复核，检查代码和产品 Git 对象而非只看映射；其测试文件 SHA 与本次执行快照一致。B 把 `PR1675-04` 保留为 partial，并标明 `01/02` 的接收端缺口。设计复核和覆盖复核均不把待确认的产品语义自动批准。

## 实际命令与原始结果

1. 基线记录：修改前 `test ! -e test_cases/fiber/devnet/send_payment/params/test_pr1675_router_custom_records.py` → `yes`，退出码 0；最终文件名按用户指示改为不含 `pr` 的 `test_router_custom_records.py`。基线没有本批 Python 测试，未把收集成功当作旧版行为验证。
2. 首轮聚焦（旧文件名、用于探测 04/07）：`venv/bin/python -m pytest test_cases/fiber/devnet/send_payment/params/test_pr1675_router_custom_records.py::TestRouterCustomRecords::test_encoded_size_2049_is_rejected_before_dispatch test_cases/fiber/devnet/send_payment/params/test_pr1675_router_custom_records.py::TestRouterCustomRecords::test_empty_custom_records_remain_empty -v -s --log-cli-level=ERROR` → `2 passed in 61.81s`，退出码 0；原始日志 `.ai-test-agent/current/pr1675-custom-records/pytest-04-07.log`。该运行不代表后续改名、强化断言后的快照。
3. 迁移前完整执行：`venv/bin/python -m pytest test_cases/fiber/devnet/send_payment/params/test_router_custom_records.py -v -s --log-cli-level=ERROR` → `collected 7 items`、`7 passed in 67.20s (0:01:07)`，退出码 0；原始日志 `.ai-test-agent/current/pr1675-custom-records/pytest-all.log`。关键原始输出：

   ```text
   empty-record result: {} {}
   encoded-size rejection with dry_run=False: Error: InvalidParameter: Failed to validate payment request: "custom_records encoded size 2049 exceeds limit 2048 bytes"
   encoded-size rejection with dry_run=True: Error: InvalidParameter: Failed to validate payment request: "custom_records encoded size 2049 exceeds limit 2048 bytes"
   ========================= 7 passed in 67.20s (0:01:07) =========================
   ```

4. `python3 scripts/check_test_map.py` → `review cases: 232`、`automated cases: 98`、`automation coverage: 98/232`、`duplicate review IDs: none`、`orphan mappings: PMP-01, PMP-02, PMP-03, PMP-04, PMP-05`，退出码 1。孤儿映射位于本次范围外，未跨范围修正；本批 7/9 有映射，2/9 无映射。
5. `python3 /Users/guopenglin/.codex/skills/ai-test-agent-skill/scripts/check_test_design.py --root . --review reviews/pr1675-router-custom-records.md --json` → `DESIGN_CASES 9`、`DESIGN_ERRORS 1`，退出码 1；唯一错误类型为既有 P2P 文档的 `missing_automation_markers`，本文件的 Spec／树／Case 关系无局部错误。
6. 迁移前 `venv/bin/python -m py_compile test_cases/fiber/devnet/send_payment/params/test_router_custom_records.py` → 无输出，退出码 0。

## 目录调整验证（2026-10-08）

- 原位置 `send_payment/params` 已清出本文件；内容哈希与迁移前一致，B 对断言内容的复核仍适用。Makefile 的 `fiber_test_cases` 与 CI 的 `fiber_test_send_payment_params_path` 仅显式添加新文件路径；没有把 `send_payment_with_router/` 的另外两份旧测试一并纳入。本次不改评审用例行、TEST-MAP ID 或断言。
- 基线：迁移脚本的 `assert old.is_file() and not new.exists()` 已通过（编辑命令退出码 0）；迁移前备份的测试文件 SHA-256 为 `fc6ebff3fbb7ed8ea9c5bda2bc10bfacc8b16d2e51773be288aa04b31aa1ea1c`。
- 新位置：`venv/bin/python -m pytest test_cases/fiber/devnet/send_payment_with_router/test_router_custom_records.py --collect-only -q` → `7 tests collected in 0.21s`，退出码 0；`venv/bin/python -m py_compile test_cases/fiber/devnet/send_payment_with_router/test_router_custom_records.py` → 无输出，退出码 0。
- 实际执行：`venv/bin/python -m pytest test_cases/fiber/devnet/send_payment_with_router/test_router_custom_records.py -v -s --log-cli-level=ERROR` → `collected 7 items`、`7 passed in 65.18s (0:01:05)`、`pytest_exit=0`；原始日志 `.ai-test-agent/current/pr1675-custom-records/pytest-relocated.log`。关键原始输出为 `empty-record result: {} {}`，以及正常与 dry-run 的 `custom_records encoded size 2049 exceeds limit 2048 bytes`。运行前 `download/fiber/current/fnn` SHA-256 仍为 `9e3f60755bf26624b52f8ef2115791c826d3a1eacfd55cfb86579732789e87d9`；结束后 8720～8727 端口无监听。
- `python3 scripts/check_test_map.py` → `review cases: 232`、`automated cases: 98`、`automation coverage: 98/232`、`orphan mappings: PMP-01, PMP-02, PMP-03, PMP-04, PMP-05`、`duplicate review IDs: none`，退出码 1；与迁移前相同，本批仍 7/9 映射且无新增孤儿 ID。
- `make -pn` 包含 `test_cases/fiber/devnet/send_payment_with_router/test_router_custom_records.py`；`yaml.safe_load(.github/workflows/fiber.yml)` 返回 `yaml_jobs 21`，且目标 job 的命令包含此文件。这里只验证 CI 配置收集路径，未运行远端 CI 或整个 `make fiber_test`。

## PR #110 基线分支复核

提交分支从 `nervosnetwork/fiber-py-integration-test:v0.10.0` 的 `79c3503f09d967d7edff734b623eef9bb6003b90` 建立，仅挑选本批六个文件；上文完整 devnet 执行属于原工作区的同一测试内容 SHA-256 `fc6ebff3fbb7ed8ea9c5bda2bc10bfacc8b16d2e51773be288aa04b31aa1ea1c`，不冒充为该隔离分支的节点执行。当前分支使用原工作区 venv 的 Python 执行 `--collect-only -q` → `7 tests collected in 0.17s`、退出码 0；`py_compile` 退出码 0；Makefile/CI 选择器各出现一次，YAML 可解析（21 jobs），退出码 0；`git diff --cached --check` 退出码 0。

该分支 `python3 scripts/check_test_map.py` → `review cases: 52`、`automated cases: 48`、`automation coverage: 48/52`、`orphan mappings: PMP-01, PMP-02, PMP-03, PMP-04, PMP-05, SETTLE-01`、`duplicate review IDs: none`，退出码 1。与上文数字不同，是因为原工作区含其他未提交的评审文档；本批 `PR1675-03`～`09` 的七个映射与复选框一致，`01/02` 仍未映射。`SETTLE-01` 和 `PMP-*` 属于分支既有范围，未随本 PR 扩展。

## 后续门禁

本轮是局部回归而非 PR 全面验收：`PR1675-01` 的本仓库末跳观察与 `PR1675-02` 的非 MPP 发票末跳证明仍缺；`PR1675-04` 需要决定对外大小口径，`PR1675-07` 需要决定空对象与省略是否构成稳定差异。7 条测试通过只支持各自已断言的行为。
